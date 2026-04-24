"""Device health report mixin."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, text

from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthDailyRollup, ServerHealthHourlyRollup
from services.timescaledb_service import TimescaleDBService
from .base import _non_agent_scan_filter, _utcnow_naive, _safe_round, _row_value


class HealthReportMixin:
    def get_device_health_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        span = end_date - start_date
        # ── TimescaleDB query routing ─────────────────────────────────────────
        # ≤ 24h  → raw server_health_logs      (fine-grained, recent data)
        # ≤ 30d  → server_health_hourly_cagg   (pre-aggregated, fast)
        # > 30d  → server_health_daily_cagg    (pre-aggregated, essential for long ranges)
        # Each tier falls back to the next if it returns empty results.
        if span <= timedelta(hours=24):
            time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
            granularity = "raw"
        elif span <= timedelta(days=30):
            # Fallback order for <=30d is intentional:
            # 1. hourly rollups (preferred — pre-aggregated, fast)
            # 2. raw scan history (fallback when rollups missing)
            # 3. daily rollups (final fallback post-startup-backfill)
            time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
            granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
                granularity = "daily"
        else:
            time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
            granularity = "daily"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
                granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"

        total_samples = sum(len(v) for v in time_series.values()) if time_series else 0
        # Window-aware sparse threshold: < 20% of expected hourly sample count = sparse
        expected_samples = max(1, int((end_date - start_date).total_seconds() / 3600))
        data_note = None
        if total_samples == 0:
            data_note = "no_data"
        elif total_samples < expected_samples * 0.2:
            data_note = "sparse"

        # PR 19: Peaks, breaches, and capacity runway
        peaks_and_breaches = _extract_peaks_and_breaches(time_series, granularity)

        # Enrich summary with incidents, scan stats, SLA breaches, correlation
        fleet_correlation = self._enrich_health_devices(
            summary, time_series, granularity, start_date, end_date
        )

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "granularity": granularity,
            "time_series": time_series,
            "summary": summary,
            "total_samples": total_samples,
            "data_note": data_note,
            "peaks_and_breaches": peaks_and_breaches,
            "fleet_correlation": fleet_correlation,
        }

    def _health_from_raw(self, device_ids, start_dt, end_dt):
        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthLog.device_id,
                Device.device_name,
                ServerHealthLog.timestamp,
                ServerHealthLog.cpu_usage,
                ServerHealthLog.memory_usage,
                ServerHealthLog.disk_usage,
                ServerHealthLog.network_in_bps,
                ServerHealthLog.network_out_bps,
            )
            .join(Device, Device.device_id == ServerHealthLog.device_id)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_dt,
                ServerHealthLog.timestamp <= end_dt,
            )
            .order_by(ServerHealthLog.timestamp.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthLog.device_id,
                Device.device_name,
                func.avg(ServerHealthLog.cpu_usage).label("avg_cpu"),
                func.max(ServerHealthLog.cpu_usage).label("max_cpu"),
                func.avg(ServerHealthLog.memory_usage).label("avg_mem"),
                func.max(ServerHealthLog.memory_usage).label("max_mem"),
                func.avg(ServerHealthLog.disk_usage).label("avg_disk"),
                func.count(ServerHealthLog.id).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthLog.device_id)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_dt,
                ServerHealthLog.timestamp <= end_dt,
            )
            .group_by(ServerHealthLog.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    def _health_from_hourly(self, device_ids, start_dt, end_dt):
        if TimescaleDBService.is_timescaledb_enabled():
            scoped_ids = self._inventory_device_id_list(device_ids)
            rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    c.bucket_hour AS timestamp,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS cpu_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS memory_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS disk_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_in_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_in_bps,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_out_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_out_bps
                FROM server_health_hourly_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_hour >= :start_dt
                  AND c.bucket_hour <= :end_dt
                GROUP BY c.device_id, d.device_name, c.bucket_hour
                ORDER BY timestamp ASC, d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            summary_rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_cpu,
                    MAX(c.max_cpu_usage) AS max_cpu,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_mem,
                    MAX(c.max_memory_usage) AS max_mem,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_disk,
                    SUM(COALESCE(c.sample_count, 0)) AS samples
                FROM server_health_hourly_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_hour >= :start_dt
                  AND c.bucket_hour <= :end_dt
                GROUP BY c.device_id, d.device_name
                ORDER BY d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return self._build_time_series(rows), self._build_health_summary(summary_rows)

        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthHourlyRollup.device_id,
                Device.device_name,
                ServerHealthHourlyRollup.bucket_hour.label("timestamp"),
                ServerHealthHourlyRollup.avg_cpu_usage.label("cpu_usage"),
                ServerHealthHourlyRollup.avg_memory_usage.label("memory_usage"),
                ServerHealthHourlyRollup.avg_disk_usage.label("disk_usage"),
                ServerHealthHourlyRollup.avg_network_in_bps.label("network_in_bps"),
                ServerHealthHourlyRollup.avg_network_out_bps.label("network_out_bps"),
            )
            .join(Device, Device.device_id == ServerHealthHourlyRollup.device_id)
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_dt,
                ServerHealthHourlyRollup.bucket_hour <= end_dt,
            )
            .order_by(ServerHealthHourlyRollup.bucket_hour.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthHourlyRollup.device_id,
                Device.device_name,
                func.avg(ServerHealthHourlyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthHourlyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthHourlyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthHourlyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthHourlyRollup.avg_disk_usage).label("avg_disk"),
                func.sum(ServerHealthHourlyRollup.sample_count).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthHourlyRollup.device_id)
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_dt,
                ServerHealthHourlyRollup.bucket_hour <= end_dt,
            )
            .group_by(ServerHealthHourlyRollup.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    def _health_from_daily(self, device_ids, start_dt, end_dt):
        if TimescaleDBService.is_timescaledb_enabled():
            scoped_ids = self._inventory_device_id_list(device_ids)
            rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    c.bucket_day AS timestamp,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS cpu_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS memory_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS disk_usage,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_in_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_in_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_in_bps,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_network_out_bps * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_network_out_bps IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS network_out_bps
                FROM server_health_daily_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_day >= :start_dt
                  AND c.bucket_day <= :end_dt
                GROUP BY c.device_id, d.device_name, c.bucket_day
                ORDER BY timestamp ASC, d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            summary_rows = self._timescaledb_rows(
                """
                SELECT
                    c.device_id,
                    d.device_name,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_cpu_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_cpu_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_cpu,
                    MAX(c.max_cpu_usage) AS max_cpu,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_memory_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_memory_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_mem,
                    MAX(c.max_memory_usage) AS max_mem,
                    CASE
                        WHEN SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END) > 0
                        THEN SUM(c.avg_disk_usage * COALESCE(c.sample_count, 0))
                             / SUM(CASE WHEN c.avg_disk_usage IS NOT NULL THEN COALESCE(c.sample_count, 0) ELSE 0 END)
                        ELSE NULL
                    END AS avg_disk,
                    SUM(COALESCE(c.sample_count, 0)) AS samples
                FROM server_health_daily_cagg c
                JOIN device d ON d.device_id = c.device_id
                WHERE c.device_id IN :device_ids
                  AND c.bucket_day >= :start_dt
                  AND c.bucket_day <= :end_dt
                GROUP BY c.device_id, d.device_name
                ORDER BY d.device_name ASC
                """,
                scoped_ids,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            return self._build_time_series(rows), self._build_health_summary(summary_rows)

        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        rows = (
            db.session.query(
                ServerHealthDailyRollup.device_id,
                Device.device_name,
                ServerHealthDailyRollup.bucket_day.label("timestamp"),
                ServerHealthDailyRollup.avg_cpu_usage.label("cpu_usage"),
                ServerHealthDailyRollup.avg_memory_usage.label("memory_usage"),
                ServerHealthDailyRollup.avg_disk_usage.label("disk_usage"),
                ServerHealthDailyRollup.avg_network_in_bps.label("network_in_bps"),
                ServerHealthDailyRollup.avg_network_out_bps.label("network_out_bps"),
            )
            .join(Device, Device.device_id == ServerHealthDailyRollup.device_id)
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_dt.date(),
                ServerHealthDailyRollup.bucket_day <= end_dt.date(),
            )
            .order_by(ServerHealthDailyRollup.bucket_day.asc())
            .all()
        )
        summary_rows = (
            db.session.query(
                ServerHealthDailyRollup.device_id,
                Device.device_name,
                func.avg(ServerHealthDailyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthDailyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthDailyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthDailyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthDailyRollup.avg_disk_usage).label("avg_disk"),
                func.sum(ServerHealthDailyRollup.sample_count).label("samples"),
            )
            .join(Device, Device.device_id == ServerHealthDailyRollup.device_id)
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_dt.date(),
                ServerHealthDailyRollup.bucket_day <= end_dt.date(),
            )
            .group_by(ServerHealthDailyRollup.device_id, Device.device_name)
            .all()
        )
        return self._build_time_series(rows), self._build_health_summary(summary_rows)

    # ── Phase 6-7: Health enrichment ────────────────────────────────────────

    def _enrich_health_devices(self, summary, time_series, granularity, start_dt, end_dt):
        """Enrich summary dicts in-place with incidents, scan stats, SLA breaches,
        and correlation. Returns fleet_correlation dict. Silently degrades on error.
        On SQLite (test env) skips all DB enrichment to avoid session side-effects."""
        if not summary or db.engine.dialect.name == "sqlite":
            return {"findings": []}

        is_sqlite = False  # guarded above
        device_ids = [d["device_id"] for d in summary]
        period_min = (end_dt - start_dt).total_seconds() / 60
        gran_hours = _GRANULARITY_HOURS.get(granularity, 1.0)

        try:
            incidents_by_device = self._fetch_incidents_batch(device_ids, start_dt, end_dt)
        except Exception:
            incidents_by_device = {}

        try:
            scan_stats_by_device = self._fetch_scan_stats_batch(
                device_ids, start_dt, end_dt
            )
        except Exception:
            scan_stats_by_device = {}

        fleet_cpu_spikes = fleet_mem_spikes = fleet_pkt_loss = fleet_total = 0

        for device in summary:
            device_id = device["device_id"]
            incidents = incidents_by_device.get(device_id, [])
            scan_stats = scan_stats_by_device.get(device_id, {})

            sla_threshold = 99.0

            device["incidents"] = incidents
            device["timeout_analysis"] = scan_stats.get("timeout_analysis")
            device["p95_latency_ms"] = scan_stats.get("p95_latency_ms")
            device["jitter_avg_ms"] = scan_stats.get("jitter_avg_ms")
            device["sla_breaches"] = _compute_sla_breaches(incidents, period_min, sla_threshold)

            agent_points = []
            dev_ts = time_series.get(device_id)
            if dev_ts:
                agent_points = dev_ts.get("points", [])
            corr = _compute_correlation(incidents, agent_points, gran_hours)
            device["correlation"] = corr

            fleet_total += len(incidents)
            fleet_cpu_spikes += corr.get("cpu_spike_count", 0)
            fleet_mem_spikes += corr.get("mem_spike_count", 0)
            fleet_pkt_loss += sum(1 for inc in incidents if inc.get("cause") == "packet_loss")

        findings = []
        if fleet_total > 0:
            for count, metric, label in [
                (fleet_cpu_spikes, "cpu", "outages coincided with CPU > 80%"),
                (fleet_mem_spikes, "memory", "outages coincided with memory > 85%"),
                (fleet_pkt_loss, "packet_loss", "outages preceded by packet loss spike"),
            ]:
                if count > 0:
                    pct = round(count / fleet_total * 100)
                    findings.append({
                        "text": f"{count}/{fleet_total} {label}",
                        "metric": metric,
                        "pct": pct,
                    })

        return {"findings": findings}

    def _fetch_incidents_batch(self, device_ids, start_dt, end_dt):
        """Fetch CRITICAL DashboardEvents for multiple devices as incident records."""
        if not device_ids:
            return {}

        rows = (
            db.session.query(
                DashboardEvent.device_id,
                DashboardEvent.timestamp,
                DashboardEvent.resolved_at,
                DashboardEvent.metric_name,
            )
            .filter(
                DashboardEvent.device_id.in_(device_ids),
                DashboardEvent.timestamp >= start_dt,
                DashboardEvent.timestamp <= end_dt,
                DashboardEvent.severity == "CRITICAL",
            )
            .order_by(DashboardEvent.device_id, DashboardEvent.timestamp)
            .all()
        )

        result = {}
        for row in rows:
            did = row.device_id
            start_ts = row.timestamp
            end_ts = row.resolved_at
            duration_min = None
            if end_ts and start_ts:
                duration_min = round((end_ts - start_ts).total_seconds() / 60, 1)

            metric = (row.metric_name or "").lower()
            if "latency" in metric:
                cause = "latency"
            elif "loss" in metric or "packet" in metric:
                cause = "packet_loss"
            else:
                cause = "connectivity_loss"

            result.setdefault(did, []).append({
                "start_ts": start_ts.isoformat() if start_ts else None,
                "end_ts": end_ts.isoformat() if end_ts else None,
                "duration_min": duration_min,
                "cause": cause,
                "sla_impact": False,
            })

        return result

    def _fetch_scan_stats_batch(self, device_ids, start_dt, end_dt):
        """Fetch timeout rate, p95 latency, and jitter for a batch of devices.

        Uses raw SQL for percentile_cont. PostgreSQL only — returns {} on SQLite.
        DeviceScanHistory uses device_ip, so maps device_id→device_ip first.
        """
        if not device_ids:
            return {}

        ip_rows = (
            db.session.query(Device.device_id, Device.device_ip)
            .filter(Device.device_id.in_(device_ids), Device.device_ip.isnot(None))
            .all()
        )
        ip_by_id = {r.device_id: r.device_ip for r in ip_rows}
        id_by_ip = {v: k for k, v in ip_by_id.items()}
        ips = [ip for ip in ip_by_id.values() if ip]
        if not ips:
            return {}

        params = {"ips": tuple(ips), "start_dt": start_dt, "end_dt": end_dt}

        main_sql = text("""
            SELECT
                device_ip,
                COUNT(*) AS total_scans,
                COUNT(*) FILTER (WHERE ping_time_ms IS NULL) AS timeout_count,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY ping_time_ms)
                    FILTER (WHERE ping_time_ms IS NOT NULL AND ping_time_ms < 1e15) AS p95_latency_ms,
                AVG(jitter) FILTER (WHERE jitter IS NOT NULL AND jitter < 1e15) AS jitter_avg_ms
            FROM device_scan_history
            WHERE device_ip IN :ips
              AND (scan_type IS NULL OR scan_type <> 'agent_push')
              AND scan_timestamp BETWEEN :start_dt AND :end_dt
            GROUP BY device_ip
        """)

        peak_sql = text("""
            SELECT device_ip, hour_bucket
            FROM (
                SELECT
                    device_ip,
                    date_trunc('hour', scan_timestamp AT TIME ZONE 'Asia/Kolkata') AS hour_bucket,
                    COUNT(*) FILTER (WHERE ping_time_ms IS NULL) AS hour_timeouts,
                    ROW_NUMBER() OVER (
                        PARTITION BY device_ip
                        ORDER BY COUNT(*) FILTER (WHERE ping_time_ms IS NULL) DESC
                    ) AS rn
                FROM device_scan_history
                WHERE device_ip IN :ips
                  AND (scan_type IS NULL OR scan_type <> 'agent_push')
                  AND scan_timestamp BETWEEN :start_dt AND :end_dt
                GROUP BY device_ip,
                         date_trunc('hour', scan_timestamp AT TIME ZONE 'Asia/Kolkata')
            ) sub
            WHERE rn = 1
        """)

        try:
            main_rows = db.session.execute(main_sql, params).fetchall()
            peak_rows = db.session.execute(peak_sql, params).fetchall()
        except Exception:
            return {}

        peak_map = {r.device_ip: r.hour_bucket for r in peak_rows}

        result = {}
        for row in main_rows:
            device_id = id_by_ip.get(row.device_ip)
            if device_id is None:
                continue

            total = int(row.total_scans or 0)
            timeouts = int(row.timeout_count or 0)
            timeout_rate = round(timeouts / total * 100, 1) if total > 0 else 0.0

            peak_hour_ist = None
            ph = peak_map.get(row.device_ip)
            if ph is not None:
                try:
                    peak_hour_ist = f"{ph.hour:02d}:00\u2013{ph.hour:02d}:59 IST"
                except Exception:
                    pass

            result[device_id] = {
                "timeout_analysis": {
                    "total_scans": total,
                    "timeout_count": timeouts,
                    "timeout_rate_pct": timeout_rate,
                    "peak_hour_ist": peak_hour_ist,
                },
                "p95_latency_ms": _safe_round(row.p95_latency_ms),
                "jitter_avg_ms": _safe_round(row.jitter_avg_ms),
            }

        return result

    @staticmethod
    def _build_health_summary(rows):
        return [
            {
                "device_id": _row_value(row, "device_id"),
                "device_name": _row_value(row, "device_name"),
                "avg_cpu": _safe_round(_row_value(row, "avg_cpu")),
                "max_cpu": _safe_round(_row_value(row, "max_cpu")),
                "avg_mem": _safe_round(_row_value(row, "avg_mem")),
                "max_mem": _safe_round(_row_value(row, "max_mem")),
                "avg_disk": _safe_round(_row_value(row, "avg_disk")),
                "samples": _row_value(row, "samples"),
            }
            for row in rows
        ]

    @staticmethod
    def _build_time_series(rows):
        by_device = {}
        for row in rows:
            device_id = _row_value(row, "device_id")
            device_name = _row_value(row, "device_name")
            timestamp = _row_value(row, "timestamp")
            by_device.setdefault(device_id, {"device_name": device_name, "points": []})
            by_device[device_id]["points"].append(
                {
                    "ts": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                    "cpu": _safe_round(_row_value(row, "cpu_usage")),
                    "mem": _safe_round(_row_value(row, "memory_usage")),
                    "disk": _safe_round(_row_value(row, "disk_usage")),
                    "net_in": _safe_round(_row_value(row, "network_in_bps")),
                    "net_out": _safe_round(_row_value(row, "network_out_bps")),
                }
            )
        return by_device


# ── PR 19: Peaks, breaches, and capacity runway ─────────────────────────────

_GRANULARITY_HOURS = {"raw": 5 / 60, "hourly": 1.0, "daily": 24.0}

_METRIC_MAP = [
    ("cpu", "CPU", "cpu_usage_pct"),
    ("mem", "Memory", "memory_usage_pct"),
    ("disk", "Disk", "disk_usage_pct"),
]


def _extract_peaks_and_breaches(time_series: dict, granularity: str = "hourly") -> dict:
    """For each device, extract peak values, threshold breaches, trend descriptions,
    and capacity runway estimates from the time-series data.

    Uses get_report_thresholds() as canonical threshold source.
    No DB queries — operates on already-fetched time_series dict."""
    if not time_series:
        return {}

    try:
        from services.report_insight_engine import get_report_thresholds
        thresholds = get_report_thresholds()
    except Exception:
        thresholds = {
            "cpu_usage_pct": {"warning": 80, "critical": 90},
            "memory_usage_pct": {"warning": 75, "critical": 95},
            "disk_usage_pct": {"warning": 90, "critical": 95},
        }

    gran_hours = _GRANULARITY_HOURS.get(granularity, 1.0)
    result = {}

    for device_id, device_data in time_series.items():
        points = device_data.get("points", [])
        if not points:
            continue

        device_name = device_data.get("device_name", f"Device {device_id}")
        peaks = []
        breaches = []
        trend_parts = []
        capacity_runway = []

        for ts_key, label, threshold_key in _METRIC_MAP:
            values = []
            timestamps = []
            for p in points:
                v = p.get(ts_key)
                if v is not None:
                    try:
                        values.append(float(v))
                        timestamps.append(p.get("ts", ""))
                    except (TypeError, ValueError):
                        pass

            if not values:
                continue

            avg_val = sum(values) / len(values)
            max_val = max(values)
            max_idx = values.index(max_val)
            min_val = min(values)

            # Peak
            peaks.append({
                "metric": label,
                "peak_value": round(max_val, 1),
                "peak_at": timestamps[max_idx] if max_idx < len(timestamps) else None,
                "avg_value": round(avg_val, 1),
            })

            # Breach detection
            t = thresholds.get(threshold_key, {})
            for level in ("critical", "warning"):
                thresh = t.get(level)
                if thresh is None:
                    continue
                breach_points = [i for i, v in enumerate(values) if v > thresh]
                if not breach_points:
                    continue

                # Sustained breach: longest consecutive run
                max_consecutive = 0
                current_run = 1
                for i in range(1, len(breach_points)):
                    if breach_points[i] == breach_points[i - 1] + 1:
                        current_run += 1
                        max_consecutive = max(max_consecutive, current_run)
                    else:
                        current_run = 1
                if len(breach_points) == 1:
                    max_consecutive = 1
                elif max_consecutive == 0:
                    max_consecutive = 1

                breaches.append({
                    "metric": label,
                    "threshold": thresh,
                    "level": level,
                    "breach_count": len(breach_points),
                    "max_consecutive": max_consecutive,
                    "sustained_hours": round(max_consecutive * gran_hours, 1) if max_consecutive >= 2 else 0,
                    "first_breach_at": timestamps[breach_points[0]] if breach_points else None,
                    "last_breach_at": timestamps[breach_points[-1]] if breach_points else None,
                })
                break  # Only report the worst level per metric

            # Trend description
            spread = max_val - min_val
            if spread < 10:
                trend_parts.append(f"{label}: stable around {avg_val:.0f}%")
            elif max_val > avg_val * 2:
                peak_ts = timestamps[max_idx] if max_idx < len(timestamps) else "unknown"
                trend_parts.append(f"{label}: stable {min_val:.0f}-{avg_val:.0f}% with spike to {max_val:.0f}% at {peak_ts}")
            else:
                trend_parts.append(f"{label}: ranging {min_val:.0f}-{max_val:.0f}% (avg {avg_val:.0f}%)")

            # Capacity runway (linear regression)
            if len(values) >= 7:
                runway = _compute_capacity_runway(values, label, threshold_key, thresholds)
                if runway:
                    capacity_runway.append(runway)

        result[device_id] = {
            "device_name": device_name,
            "peaks": peaks,
            "breaches": breaches,
            "trend_description": ". ".join(trend_parts) + "." if trend_parts else "",
            "capacity_runway": capacity_runway,
        }

    return result


def _compute_capacity_runway(values, label, threshold_key, thresholds):
    """Simple linear regression for capacity runway estimate.
    Only shown when growth rate > 0.1%/day, R² > 0.3, and ≥7 data points."""
    n = len(values)
    if n < 7:
        return None

    # Simple linear regression: y = mx + b
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return None

    slope = numerator / denominator  # units per data point interval

    # R² calculation
    ss_res = sum((v - (slope * i + (y_mean - slope * x_mean))) ** 2 for i, v in enumerate(values))
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

    if r_squared < 0.3:
        return None  # Too noisy
    if slope <= 0.1:
        return None  # Not growing meaningfully

    # Estimate days to breach
    t = thresholds.get(threshold_key, {})
    warn_thresh = t.get("warning")
    if warn_thresh is None:
        return None

    current_avg = values[-1]  # Use latest value
    if current_avg >= warn_thresh:
        return None  # Already breached

    days_to_breach = (warn_thresh - current_avg) / slope

    return {
        "metric": label,
        "current_avg": round(current_avg, 1),
        "daily_growth_rate": round(slope, 2),
        "threshold": warn_thresh,
        "estimated_days_to_breach": round(days_to_breach),
        "r_squared": round(r_squared, 2),
        "confidence": "high" if r_squared > 0.7 else "medium",
    }


# ── Phase 6-7: SLA breach + correlation helpers ──────────────────────────────

def _compute_sla_breaches(incidents, period_min, sla_threshold_pct=99.0):
    """Derive SLA breach list from incident records.

    Marks each incident's sla_impact=True once cumulative downtime exceeds
    (100 - sla_threshold_pct)% of period_min. Returns list of breach dicts.
    """
    if not incidents or period_min <= 0:
        return []

    max_allowed_down = period_min * (1.0 - sla_threshold_pct / 100.0)
    cumulative = 0.0
    breaches = []

    for inc in incidents:
        dur = inc.get("duration_min") or 0.0
        cumulative += dur
        if cumulative > max_allowed_down:
            inc["sla_impact"] = True
            breaches.append({
                "breach_start": inc.get("start_ts"),
                "breach_end": inc.get("end_ts"),
                "duration_min": round(dur, 1),
                "cumulative_downtime_min": round(cumulative, 1),
                "sla_threshold_pct": sla_threshold_pct,
            })

    return breaches


def _compute_correlation(incidents, agent_points, gran_hours=1.0):
    """Check whether incidents correlate with CPU/memory spikes in agent time-series.

    For each incident, looks at agent_points within ±1 bucket (gran_hours) window.
    Returns correlation summary dict. All timestamps assumed naive UTC.
    """
    total = len(incidents)
    empty = {"cpu_spike_count": 0, "mem_spike_count": 0,
             "total_incidents": total, "correlated_pct": 0.0, "insight": ""}

    if not incidents or not agent_points:
        return empty

    window_seconds = gran_hours * 3600

    cpu_spikes = 0
    mem_spikes = 0

    for inc in incidents:
        start_str = inc.get("start_ts")
        if not start_str:
            continue
        try:
            inc_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if inc_start.tzinfo:
                inc_start = inc_start.replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue

        found_cpu = found_mem = False
        for pt in agent_points:
            ts_str = pt.get("ts", "")
            try:
                pt_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if pt_ts.tzinfo:
                    pt_ts = pt_ts.replace(tzinfo=None)
                if abs((pt_ts - inc_start).total_seconds()) <= window_seconds:
                    if not found_cpu and (pt.get("cpu") or 0) > 80:
                        found_cpu = True
                    if not found_mem and (pt.get("mem") or 0) > 85:
                        found_mem = True
            except (ValueError, AttributeError):
                continue
            if found_cpu and found_mem:
                break

        if found_cpu:
            cpu_spikes += 1
        if found_mem:
            mem_spikes += 1

    correlated = max(cpu_spikes, mem_spikes)
    correlated_pct = round(correlated / total * 100, 1) if total > 0 else 0.0

    parts = []
    if cpu_spikes > 0:
        parts.append(f"{cpu_spikes}/{total} outages coincided with CPU > 80%")
    if mem_spikes > 0:
        parts.append(f"{mem_spikes}/{total} outages coincided with memory > 85%")

    return {
        "cpu_spike_count": cpu_spikes,
        "mem_spike_count": mem_spikes,
        "total_incidents": total,
        "correlated_pct": correlated_pct,
        "insight": "; ".join(parts),
    }
