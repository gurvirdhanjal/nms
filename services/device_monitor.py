import asyncio
import logging
import statistics
from datetime import datetime, timedelta

from extensions import db
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from services.network_scanner import NetworkScanner
from services.settings_service import get_monitoring_interval

logger = logging.getLogger(__name__)


def _non_agent_scan_filter(model):
    return or_(
        model.scan_type.is_(None),
        model.scan_type != "agent_push",
    )


def _normalize_scan_status(status, ping_time_ms=None):
    normalized = str(status or "").strip().lower()
    if normalized == "online":
        return "online"
    if normalized in {"no_response", "timeout"}:
        return "no_response"
    if normalized == "offline" and ping_time_ms is None:
        return "no_response"
    if normalized == "offline":
        return "offline"
    return normalized or "unknown"


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _window_timestamp(entries, key="timestamp"):
    if not entries:
        return None
    return entries[len(entries) // 2].get(key)


def _window_packet_loss(entries):
    losses = [
        float(entry["packet_loss"])
        for entry in entries
        if entry.get("packet_loss") is not None
    ]
    return statistics.mean(losses) if losses else None


def _window_details(entries):
    latencies = [float(entry["latency_ms"]) for entry in entries if entry.get("latency_ms") is not None]
    if not latencies:
        return None
    peak_latency = max(latencies)
    avg_latency = statistics.mean(latencies)
    stability = statistics.pstdev(latencies) if len(latencies) > 1 else 0.0
    packet_loss_avg = _window_packet_loss(entries)
    peak_entry = max(
        (entry for entry in entries if entry.get("latency_ms") is not None),
        key=lambda entry: float(entry["latency_ms"]),
    )
    return {
        "timestamp": peak_entry.get("timestamp"),
        "start": entries[0].get("timestamp"),
        "end": entries[-1].get("timestamp"),
        "peak_latency_ms": peak_latency,
        "baseline_latency_ms": avg_latency,
        "stability_score_ms": stability,
        "packet_loss_avg_pct": packet_loss_avg,
        "sample_count": len(entries),
    }


def _build_ping_summary(scans, hours):
    interval_seconds = max(int(get_monitoring_interval() or 300), 1)
    expected_scans = max(int(round((hours * 3600) / interval_seconds)), 1)

    normalized_scans = []
    online_samples = []
    jitter_values = []
    previous_latency = None

    for scan in scans:
        latency = float(scan.ping_time_ms) if scan.ping_time_ms is not None else None
        packet_loss = float(scan.packet_loss) if scan.packet_loss is not None else None
        jitter = float(scan.jitter) if scan.jitter is not None else None
        normalized_status = _normalize_scan_status(scan.status, scan.ping_time_ms)
        entry = {
            "timestamp": scan.scan_timestamp.isoformat(),
            "status": normalized_status,
            "status_detail": getattr(scan, "status_detail", None),
            "latency_ms": latency,
            "packet_loss": packet_loss,
            "jitter_ms": jitter,
        }
        normalized_scans.append(entry)
        if latency is not None and normalized_status == "online":
            online_samples.append(entry)
            if jitter is not None:
                jitter_values.append(jitter)
            elif previous_latency is not None:
                jitter_values.append(abs(latency - previous_latency))
            previous_latency = latency

    latencies = [entry["latency_ms"] for entry in online_samples if entry.get("latency_ms") is not None]
    packet_losses = [entry["packet_loss"] for entry in normalized_scans if entry.get("packet_loss") is not None]
    timeout_count = sum(1 for entry in normalized_scans if entry.get("status") == "no_response")
    coverage_pct = round((len(normalized_scans) / expected_scans) * 100.0, 1) if expected_scans else None

    window_size = min(5, len(online_samples))
    windows = []
    if window_size >= 2:
        for idx in range(0, len(online_samples) - window_size + 1):
            details = _window_details(online_samples[idx: idx + window_size])
            if details:
                windows.append(details)

    worst_window = None
    stable_window = None
    if windows:
        worst_window = max(
            windows,
            key=lambda window: (
                float(window["peak_latency_ms"]),
                float(window["baseline_latency_ms"]),
                float(window["packet_loss_avg_pct"] or 0.0),
            ),
        )
        stable_window = min(
            windows,
            key=lambda window: (
                float(window["stability_score_ms"]),
                float(window["baseline_latency_ms"]),
                float(window["peak_latency_ms"]),
            ),
        )
    elif online_samples:
        peak_entry = max(online_samples, key=lambda entry: float(entry["latency_ms"] or 0.0))
        base_entry = min(online_samples, key=lambda entry: float(entry["latency_ms"] or 0.0))
        worst_window = {
            "timestamp": peak_entry.get("timestamp"),
            "start": peak_entry.get("timestamp"),
            "end": peak_entry.get("timestamp"),
            "peak_latency_ms": peak_entry.get("latency_ms"),
            "baseline_latency_ms": peak_entry.get("latency_ms"),
            "stability_score_ms": peak_entry.get("jitter_ms"),
            "packet_loss_avg_pct": peak_entry.get("packet_loss"),
            "sample_count": 1,
        }
        stable_window = {
            "timestamp": base_entry.get("timestamp"),
            "start": base_entry.get("timestamp"),
            "end": base_entry.get("timestamp"),
            "peak_latency_ms": base_entry.get("latency_ms"),
            "baseline_latency_ms": base_entry.get("latency_ms"),
            "stability_score_ms": base_entry.get("jitter_ms"),
            "packet_loss_avg_pct": base_entry.get("packet_loss"),
            "sample_count": 1,
        }

    if worst_window:
        worst_window["diagnosis"] = (
            "Severe latency spike window"
            if (worst_window.get("peak_latency_ms") or 0) >= 250
            else "Highest latency window in the selected period"
        )
    if stable_window:
        baseline = stable_window.get("baseline_latency_ms") or 0
        stability = stable_window.get("stability_score_ms") or 0
        stable_window["diagnosis"] = (
            "Very stable low-latency window"
            if baseline <= 10 and stability <= 2
            else "Most stable sustained latency window"
        )

    latest_anomaly = None
    for entry in reversed(normalized_scans):
        latency = entry.get("latency_ms")
        packet_loss = entry.get("packet_loss") or 0
        status = entry.get("status")
        is_anomaly = (
            status in {"offline", "no_response"}
            or (latency is not None and latency >= 100)
            or packet_loss >= 5
        )
        if not is_anomaly:
            continue
        latest_anomaly = {
            "timestamp": entry.get("timestamp"),
            "status": status,
            "latency_ms": latency,
            "packet_loss_pct": packet_loss if entry.get("packet_loss") is not None else None,
            "status_detail": entry.get("status_detail"),
            "diagnosis": (
                "Most recent no-response interval"
                if status == "no_response"
                else "Most recent offline event"
                if status == "offline"
                else "Most recent latency spike"
            ),
        }
        break

    return {
        "avg_latency_ms": statistics.mean(latencies) if latencies else None,
        "min_latency_ms": min(latencies) if latencies else None,
        "max_latency_ms": max(latencies) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95) if latencies else None,
        "jitter_ms": statistics.mean(jitter_values) if jitter_values else None,
        "packet_loss_avg_pct": statistics.mean(packet_losses) if packet_losses else None,
        "timeout_count": timeout_count,
        "coverage_pct": coverage_pct,
        "expected_scans": expected_scans,
        "actual_scans": len(normalized_scans),
        "worst_window": worst_window,
        "stable_window": stable_window,
        "latest_anomaly": latest_anomaly,
    }


def _is_deadlock_error(error: Exception) -> bool:
    message = str(error).lower()
    if "deadlock detected" in message:
        return True
    orig = getattr(error, "orig", None)
    return getattr(orig, "pgcode", None) == "40P01"


def _is_lock_timeout_error(error: Exception) -> bool:
    message = str(error).lower()
    if "lock timeout" in message or "lock not available" in message:
        return True
    orig = getattr(error, "orig", None)
    return getattr(orig, "pgcode", None) in {"55P03", "57014"}


def _is_missing_device_error(error: Exception) -> bool:
    message = str(error).lower()
    if "dashboard_events_device_id_fkey" in message:
        return True
    orig = getattr(error, "orig", None)
    return getattr(orig, "pgcode", None) == "23503"


def _build_latency_spike_payload(
    device_id: int,
    device_ip: str,
    device_name: str,
    latency_ms: float,
    icmp_thresholds: dict,
) -> dict:
    severity = (
        "critical"
        if latency_ms >= icmp_thresholds["latency_critical_ms"]
        else "warning"
    )
    threshold_ms = (
        icmp_thresholds["latency_critical_ms"]
        if severity == "critical"
        else icmp_thresholds["latency_warning_ms"]
    )
    return {
        "device_id": device_id,
        "ip": device_ip,
        "name": device_name,
        "latency_ms": round(latency_ms, 2),
        "threshold_ms": threshold_ms,
        "severity": severity,
    }


class DeviceMonitor:
    def __init__(self):
        self.scanner = NetworkScanner()

        from metrics.collector import MetricCollector
        from extensions import event_manager
        from thresholds.evaluator import ThresholdEvaluator
        from thresholds.rules import ThresholdOperator, ThresholdRule

        self.collector = MetricCollector()

        latency_rule = ThresholdRule(
            metric_name="network_latency_ms",
            operator=ThresholdOperator.GT,
            warning_threshold=100.0,
            critical_threshold=200.0,
            samples_required=1,
        )
        availability_rule = ThresholdRule(
            metric_name="device_availability",
            operator=ThresholdOperator.LT,
            warning_threshold=None,
            critical_threshold=0.5,
            samples_required=1,
        )

        self.evaluator = ThresholdEvaluator(rules=[latency_rule, availability_rule])
        self.event_manager = event_manager

    def hydrate_collector(self, app):
        """
        Public method to hydrate collector with DB history.
        Must be called with app context.
        """
        logger.info("Hydrating MetricCollector from database...")
        with app.app_context():
            try:
                from models.device import Device
                from models.scan_history import DeviceScanHistory
                from metrics.normalizer import MetricNormalizer
                from sqlalchemy import text as _text

                device_ips = [d.device_ip for d in Device.query.with_entities(Device.device_ip).all() if d.device_ip]
                total_loaded = 0

                if device_ips:
                    # Single LATERAL query — last 50 scans per IP in one round-trip
                    # instead of N separate queries (one per device).
                    stmt = _text("""
                        SELECT l.device_ip, l.status, l.ping_time_ms, l.scan_timestamp
                        FROM (SELECT unnest(:ips) AS device_ip) AS t
                        CROSS JOIN LATERAL (
                            SELECT device_ip, status, ping_time_ms, scan_timestamp
                            FROM device_scan_history dsh
                            WHERE dsh.device_ip = t.device_ip
                            ORDER BY dsh.scan_timestamp DESC
                            LIMIT 50
                        ) AS l
                        ORDER BY l.device_ip, l.scan_timestamp ASC
                    """)
                    rows = db.session.execute(stmt, {"ips": device_ips}).fetchall()
                    for row in rows:
                        metrics = MetricNormalizer.normalize_ping(
                            row.device_ip,
                            row.status,
                            row.ping_time_ms,
                            row.scan_timestamp,
                        )
                        self.collector.add_metrics(metrics)
                        total_loaded += 1

                logger.info("Hydration complete. Loaded %d metrics.", total_loaded)

            except Exception as e:
                logger.exception("Error hydrating collector: %s", e)

    async def monitor_stored_devices(self):
        """Monitor all stored devices and save results concurrently."""
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from metrics.normalizer import MetricNormalizer
        from services.alert_manager import AlertManager

        cycle_start = datetime.utcnow()

        try:
            devices_query = db.session.query(
                Device.device_id,
                Device.device_ip,
                Device.device_name,
                Device.maintenance_mode,
                Device.delete_pending,
            ).all()
            active_devices = [
                (d.device_id, d.device_ip, d.device_name, d.maintenance_mode)
                for d in devices_query
                if not getattr(d, "maintenance_mode", False)
                and not getattr(d, "delete_pending", False)
                and d.device_ip
            ]
        finally:
            db.session.remove()
        logger.debug("Monitoring %d stored devices...", len(active_devices))

        async def fetch_status(device_info):
            device_id, device_ip, device_name, _ = device_info

            status, latency, packet_loss, jitter, _ttl, status_detail, min_rtt, max_rtt = \
                await self.scanner.ping_device(device_ip)

            if status == "Offline":
                try:
                    agent_info = await self.scanner.check_tactical_agent(device_ip)
                    if agent_info:
                        status = "Online"
                        status_detail = "Reply via tactical agent"
                        if latency is None:
                            latency = 1.0
                except Exception:
                    pass

            return {
                "id": device_id,
                "ip": device_ip,
                "name": device_name,
                "status": status,
                "status_detail": status_detail,
                "latency": latency,
                "min_rtt": min_rtt,
                "max_rtt": max_rtt,
                "packet_loss": packet_loss,
                "jitter": jitter,
            }

        tasks = [fetch_status(device_info) for device_info in active_devices]

        try:
            results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error("[DeviceMonitor] Failed during concurrent ping gather: %s", e)
            results = []

        scan_results = []
        sse_update_batch = []
        alert_failures = 0

        for res in results:
            device_id = res["id"]
            device_ip = res["ip"]
            device_name = res["name"]
            status = res["status"]
            status_detail = res.get("status_detail")
            latency = res["latency"]
            packet_loss = res["packet_loss"]
            jitter = res["jitter"]

            scan_results.append(
                {
                    "device_name": device_name,
                    "device_ip": device_ip,
                    "status": status,
                    "status_detail": status_detail,
                    "latency": latency,
                    "min_rtt": res.get("min_rtt"),
                    "max_rtt": res.get("max_rtt"),
                    "packet_loss": packet_loss,
                    "jitter": jitter,
                    "timestamp": datetime.utcnow(),
                }
            )

            metrics = MetricNormalizer.normalize_ping(
                device_ip,
                status,
                latency,
                packet_loss=packet_loss,
                jitter=jitter,
            )
            self.collector.add_metrics(metrics)

            is_online = status == "Online"
            for attempt in range(2):
                try:
                    live_device = db.session.get(Device, device_id)
                    if not live_device:
                        db.session.rollback()
                        break

                    # Skip devices that are being deleted — avoids racing the
                    # bulk-delete worker on the same row lock.
                    if getattr(live_device, "delete_pending", False):
                        db.session.rollback()
                        break

                    # Snapshot strike values before AlertManager mutates them.
                    _strikes_before = (
                        live_device.offline_strikes,
                        getattr(live_device, "latency_strikes", 0),
                        getattr(live_device, "packet_loss_strikes", 0),
                    )

                    AlertManager.process_scan_result(
                        live_device,
                        is_online,
                        latency,
                        packet_loss,
                        commit=False,
                    )

                    # Only commit if something actually changed — fewer writes
                    # means shorter lock windows and less contention.
                    _strikes_after = (
                        live_device.offline_strikes,
                        getattr(live_device, "latency_strikes", 0),
                        getattr(live_device, "packet_loss_strikes", 0),
                    )
                    if db.session.is_modified(live_device) or _strikes_before != _strikes_after:
                        db.session.commit()
                    else:
                        db.session.rollback()

                    if is_online and latency is not None:
                        try:
                            icmp = AlertManager.get_icmp_thresholds(live_device)
                            if latency >= icmp["latency_warning_ms"]:
                                from services.sse_broadcaster import broadcast_event

                                broadcast_event(
                                    "latency_spike",
                                    _build_latency_spike_payload(
                                        device_id,
                                        device_ip,
                                        device_name,
                                        latency,
                                        icmp,
                                    ),
                                )
                        except Exception as sse_err:
                            logger.warning(
                                "[DeviceMonitor] latency_spike broadcast error for %s: %s",
                                device_ip,
                                sse_err,
                            )
                    break
                except (StaleDataError, ObjectDeletedError) as e:
                    logger.warning(
                        "[DeviceMonitor] Device became stale during alert processing for %s: %s",
                        device_ip,
                        e,
                    )
                    db.session.rollback()
                    break
                except OperationalError as e:
                    db.session.rollback()
                    if _is_deadlock_error(e) and attempt == 0:
                        logger.warning(
                            "[DeviceMonitor] Deadlock during alert update for %s; retrying once",
                            device_ip,
                        )
                        await asyncio.sleep(0.05)
                        continue
                    if _is_lock_timeout_error(e) or _is_missing_device_error(e):
                        logger.warning(
                            "[DeviceMonitor] Alert update skipped for %s during delete/contention window: %s",
                            device_ip,
                            e,
                        )
                        break
                    alert_failures += 1
                    logger.error("[DeviceMonitor] Alert update failed for %s: %s", device_ip, e)
                    break
                except Exception as e:
                    alert_failures += 1
                    logger.error("[DeviceMonitor] Alert update failed for %s: %s", device_ip, e)
                    db.session.rollback()
                    break

            try:
                sse_update_batch.append(
                    {
                        "device_id": device_id,
                        "ip": device_ip,
                        "status": status,
                        "status_detail": status_detail,
                        "latency": latency,
                        "packet_loss": packet_loss,
                        "jitter": jitter,
                    }
                )
            except Exception as e:
                logger.warning("[DeviceMonitor] Batch accumulation error: %s", e)

        try:
            if scan_results:
                db.session.bulk_insert_mappings(
                    DeviceScanHistory,
                    [
                        {
                            "device_ip": sr["device_ip"],
                            "device_name": sr["device_name"],
                            "ping_time_ms": sr["latency"],
                            "min_rtt": sr.get("min_rtt"),
                            "max_rtt": sr.get("max_rtt"),
                            "status": sr["status"],
                            "status_detail": sr.get("status_detail"),
                            "scan_type": "scheduled",
                            "packet_loss": sr["packet_loss"],
                            "jitter": sr.get("jitter"),
                            "scan_timestamp": sr["timestamp"],
                        }
                        for sr in scan_results
                    ],
                )
                db.session.commit()
        except Exception as batch_err:
            logger.error(
                "[DeviceMonitor] Batch scan-history insert failed (%d results); retrying per record: %s",
                len(scan_results),
                batch_err,
            )
            db.session.rollback()
            for sr in scan_results:
                try:
                    fallback_record = DeviceScanHistory(
                        device_ip=sr["device_ip"],
                        device_name=sr["device_name"],
                        ping_time_ms=sr["latency"],
                        status=sr["status"],
                        status_detail=sr.get("status_detail"),
                        scan_type="scheduled",
                        packet_loss=sr["packet_loss"],
                        jitter=sr.get("jitter"),
                        scan_timestamp=sr["timestamp"],
                    )
                    db.session.add(fallback_record)
                    db.session.commit()
                except Exception as fallback_err:
                    logger.error(
                        "[DeviceMonitor] Fallback commit failed for %s: %s",
                        sr["device_ip"],
                        fallback_err,
                    )
                    db.session.rollback()

        if sse_update_batch:
            try:
                from services.sse_broadcaster import broadcast_event

                broadcast_event("device_update_batch", {"devices": sse_update_batch})
            except Exception as e:
                logger.error("[DeviceMonitor] Bulk SSE broadcast error: %s", e)

        # Invalidate Redis caches so the next dashboard/summary request
        # reflects the freshly-written scan results immediately, rather than
        # waiting for the TTL to expire.
        try:
            from extensions import redis_client, is_redis_available
            if is_redis_available():
                # server:health:summary has its own flat key (no namespace prefix)
                redis_client.delete("server:health:summary")
                # inspector device search list
                redis_client.delete("reports:device_search_list")
                # bust full snapshot so next request recomputes
                for snapshot_key in redis_client.scan_iter(match="full_snapshot_*"):
                    redis_client.delete(snapshot_key)
            from services.dashboard_cache_service import invalidate_dashboard_namespace
            invalidate_dashboard_namespace(
                namespace="dashboard",
                prefixes=["summary", "top-problems", "availability-details", "trends"],
            )
        except Exception as cache_err:
            logger.warning("[DeviceMonitor] Post-scan cache invalidation failed: %s", cache_err)

        # Invalidate short-TTL in-memory report cache entries (24h range reports)
        # so the reports tab reflects fresh scan data without waiting up to 120s.
        try:
            from routes.reports import invalidate_short_ttl_report_cache
            invalidate_short_ttl_report_cache()
        except Exception as report_cache_err:
            logger.warning("[DeviceMonitor] Report cache invalidation failed: %s", report_cache_err)

        cycle_elapsed = (datetime.utcnow() - cycle_start).total_seconds()
        logger.info(
            "[DeviceMonitor] Scan cycle completed in %.2fs for %d devices (alert_failures=%d)",
            cycle_elapsed,
            len(active_devices),
            alert_failures,
        )
        return scan_results

    def get_device_statistics(self, device_ip, hours=24, start_time=None, end_time=None):
        """Get statistics for a device over specified hours OR time range.

        Uses SQL aggregation for scalar stats (avoids loading hundreds of thousands
        of rows for long periods). Window/anomaly analysis uses only the most recent
        scans (capped at 2000) for the ping_summary.
        """
        from models.scan_history import DeviceScanHistory
        from sqlalchemy import func, case

        if start_time and end_time:
            cutoff_time = start_time
            end_time_filter = end_time
        else:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            end_time_filter = None

        base_filter = [
            DeviceScanHistory.device_ip == device_ip,
            DeviceScanHistory.scan_timestamp >= cutoff_time,
            _non_agent_scan_filter(DeviceScanHistory),
        ]
        if end_time_filter:
            base_filter.append(DeviceScanHistory.scan_timestamp <= end_time_filter)

        # SQL aggregation — one query instead of loading all rows into Python
        agg = db.session.query(
            func.count(DeviceScanHistory.scan_id).label("total"),
            func.sum(
                case(
                    (func.lower(func.coalesce(DeviceScanHistory.status, '')) == 'online', 1),
                    else_=0,
                )
            ).label("online"),
            func.sum(
                case(
                    (func.lower(func.coalesce(DeviceScanHistory.status, '')) != 'online', 1),
                    else_=0,
                )
            ).label("offline"),
            func.sum(
                case(
                    (
                        (func.lower(func.coalesce(DeviceScanHistory.status, '')) != 'online') &
                        DeviceScanHistory.ping_time_ms.is_(None),
                        1,
                    ),
                    else_=0,
                )
            ).label("no_response"),
            func.avg(
                case(
                    (func.lower(func.coalesce(DeviceScanHistory.status, '')) == 'online',
                     DeviceScanHistory.ping_time_ms),
                    else_=None,
                )
            ).label("avg_latency"),
            func.min(
                case(
                    (func.lower(func.coalesce(DeviceScanHistory.status, '')) == 'online',
                     DeviceScanHistory.ping_time_ms),
                    else_=None,
                )
            ).label("min_latency"),
            func.max(
                case(
                    (func.lower(func.coalesce(DeviceScanHistory.status, '')) == 'online',
                     DeviceScanHistory.ping_time_ms),
                    else_=None,
                )
            ).label("max_latency"),
            func.avg(DeviceScanHistory.packet_loss).label("avg_packet_loss"),
            func.max(DeviceScanHistory.packet_loss).label("max_packet_loss"),
        ).filter(*base_filter).first()

        if not agg or not agg.total:
            return None

        total = int(agg.total)
        online_count = int(agg.online or 0)
        offline_count = int(agg.offline or 0)
        no_response_count = int(agg.no_response or 0)

        stats = {
            "total_scans": total,
            "online_count": online_count,
            "offline_count": offline_count,
            "no_response_count": no_response_count,
            "uptime_percentage": (online_count / total) * 100 if total else 0,
            "downtime_percentage": (offline_count / total) * 100 if total else 0,
        }

        if agg.avg_latency is not None:
            latency_std_dev = 0.0
            stats.update({
                "avg_latency": float(agg.avg_latency),
                "min_latency": float(agg.min_latency or agg.avg_latency),
                "max_latency": float(agg.max_latency or agg.avg_latency),
                "latency_std_dev": latency_std_dev,
            })

        if agg.avg_packet_loss is not None:
            stats.update({
                "avg_packet_loss": float(agg.avg_packet_loss),
                "max_packet_loss": float(agg.max_packet_loss or agg.avg_packet_loss),
            })

        # Fetch a bounded recent sample for ping_summary window/anomaly analysis.
        # 2000 rows covers ~8h at 15s intervals — enough for window and anomaly work.
        recent_scans = (
            DeviceScanHistory.query
            .filter(*base_filter)
            .order_by(DeviceScanHistory.scan_timestamp.desc())
            .limit(2000)
            .all()
        )
        recent_scans = list(reversed(recent_scans))
        stats["ping_summary"] = _build_ping_summary(recent_scans, hours)
        # Override counts with the accurate SQL-aggregated totals (the capped sample
        # would otherwise report coverage_pct / actual_scans for only 2000 rows).
        expected = stats["ping_summary"].get("expected_scans") or 1
        stats["ping_summary"]["actual_scans"] = total
        stats["ping_summary"]["coverage_pct"] = round(
            min(100.0, (total / expected) * 100.0), 1
        )

        return stats

    def get_daily_report(self, date=None):
        """Generate daily report for all monitored devices"""
        from models.device import Device

        if date is None:
            date = datetime.utcnow().date()

        start_time = datetime.combine(date, datetime.min.time())
        end_time = datetime.combine(date, datetime.max.time())

        devices = Device.query.all()
        report = {
            "date": date.isoformat(),
            "total_monitored_devices": len(devices),
            "devices": [],
        }

        for device in devices:
            stats = self.get_device_statistics(
                device.device_ip,
                start_time=start_time,
                end_time=end_time,
            )

            if stats:
                report["devices"].append(
                    {
                        "device_name": device.device_name,
                        "device_ip": device.device_ip,
                        "stats": stats,
                    }
                )

        return report
