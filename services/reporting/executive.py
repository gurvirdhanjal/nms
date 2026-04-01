"""Executive fleet health report mixin."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, cast, func

from extensions import db
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.device import Device
from models.scan_history import DeviceScanHistory
from .base import _utcnow_naive


class ExecutiveReportMixin:
    def get_executive_fleet_health(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        inventory_ips = self._inventory_device_ips_subquery()

        uptime_stats = (
            db.session.query(
                func.sum(DailyDeviceStats.online_scans).label("total_online"),
                func.sum(DailyDeviceStats.total_scans).label("total_scans"),
                func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                    DailyDeviceStats.uptime_percent.isnot(None),
                    DailyDeviceStats.uptime_percent >= 0,
                    DailyDeviceStats.uptime_percent <= 200,
                ).label("avg_uptime"),
                func.avg(cast(DailyDeviceStats.avg_latency_ms, db.Float)).filter(
                    DailyDeviceStats.avg_latency_ms.isnot(None),
                    DailyDeviceStats.avg_latency_ms >= 0,
                    DailyDeviceStats.avg_latency_ms < 1e15,
                ).label("avg_latency"),
                func.avg(cast(DailyDeviceStats.avg_packet_loss_pct, db.Float)).filter(
                    DailyDeviceStats.avg_packet_loss_pct.isnot(None),
                    DailyDeviceStats.avg_packet_loss_pct >= 0,
                    DailyDeviceStats.avg_packet_loss_pct <= 100,
                ).label("avg_packet_loss"),
            )
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .first()
        )
        availability_basis = "daily_device_stats"
        if uptime_stats and uptime_stats.total_scans:
            # Correct weighted formula: count-based, not average-of-percentages
            uptime_score = round((uptime_stats.total_online / uptime_stats.total_scans) * 100.0, 2)
        elif uptime_stats and uptime_stats.avg_uptime is not None:
            # Legacy rows: scan counts not populated — fall back to stored percentage
            uptime_score = round(uptime_stats.avg_uptime, 2)
        else:
            uptime_score = None
        avg_latency = round(uptime_stats.avg_latency, 2) if uptime_stats and uptime_stats.avg_latency is not None else None

        raw_uptime_rows = self._raw_scan_uptime_rows(start_date=start_date, end_date=end_date)
        if uptime_score is None or avg_latency is None:
            availability_basis = "device_scan_history"
            total_scans = sum(int(row.total_scans or 0) for row in raw_uptime_rows)
            total_online_scans = sum(int(row.online_scans or 0) for row in raw_uptime_rows)
            raw_ping_stats = (
                db.session.query(
                    func.avg(cast(DeviceScanHistory.ping_time_ms, db.Float)).filter(
                        DeviceScanHistory.ping_time_ms.isnot(None),
                        DeviceScanHistory.ping_time_ms >= 0,
                        DeviceScanHistory.ping_time_ms < 60000,
                    ).label("avg_latency")
                )
                .filter(
                    DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip)),
                    DeviceScanHistory.scan_timestamp >= start_date,
                    DeviceScanHistory.scan_timestamp <= end_date,
                )
                .first()
            )
            uptime_score = round((total_online_scans / total_scans) * 100.0, 2) if total_scans else None
            avg_latency = round(raw_ping_stats.avg_latency, 2) if raw_ping_stats and raw_ping_stats.avg_latency is not None else None

        # --- Prev-period uptime for trend badge ---
        # prev_end = start_date - 1 day to avoid double-counting boundary
        period_delta = end_date - start_date
        prev_start = start_date - period_delta
        prev_end = start_date - timedelta(days=1)

        prev_stats = (
            db.session.query(
                func.sum(DailyDeviceStats.online_scans).label("total_online"),
                func.sum(DailyDeviceStats.total_scans).label("total_scans"),
                func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                    DailyDeviceStats.uptime_percent.isnot(None),
                    DailyDeviceStats.uptime_percent >= 0,
                    DailyDeviceStats.uptime_percent <= 200,
                ).label("avg_uptime"),
            )
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= prev_start.date(),
                DailyDeviceStats.date <= prev_end.date(),
            )
            .first()
        )
        if prev_stats and prev_stats.total_scans:
            prev_uptime_score = round((prev_stats.total_online / prev_stats.total_scans) * 100.0, 2)
        elif prev_stats and prev_stats.avg_uptime is not None:
            prev_uptime_score = round(prev_stats.avg_uptime, 2)
        else:
            prev_uptime_score = None

        if prev_uptime_score is None:
            prev_raw_rows = self._raw_scan_uptime_rows(start_date=prev_start, end_date=prev_end)
            prev_total = sum(int(r.total_scans or 0) for r in prev_raw_rows)
            prev_online = sum(int(r.online_scans or 0) for r in prev_raw_rows)
            prev_uptime_score = round((prev_online / prev_total) * 100.0, 2) if prev_total else None

        # --- Data health fields ---
        oldest_scan = (
            db.session.query(func.min(DeviceScanHistory.scan_timestamp))
            .filter(DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip)))
            .scalar()
        )
        if oldest_scan:
            delta = end_date.replace(tzinfo=None) - oldest_scan.replace(tzinfo=None)
            scan_history_days = max(0, int(delta.total_seconds() / 86400))
        else:
            scan_history_days = 0

        daily_stats_coverage = (
            db.session.query(func.count(func.distinct(DailyDeviceStats.date)))
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .scalar()
        ) or 0

        trend_window_days = max(1, int((end_date - start_date).total_seconds() / 86400))

        latest_scans_subq = (
            db.session.query(
                DeviceScanHistory.device_ip,
                func.max(DeviceScanHistory.scan_id).label("max_id"),
            )
            .filter(DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip)))
            .group_by(DeviceScanHistory.device_ip)
            .subquery()
        )
        latest_scans = (
            db.session.query(DeviceScanHistory.status)
            .join(
                latest_scans_subq,
                and_(
                    DeviceScanHistory.device_ip == latest_scans_subq.c.device_ip,
                    DeviceScanHistory.scan_id == latest_scans_subq.c.max_id,
                ),
            )
            .all()
        )
        health_counts = {"Healthy": 0, "Critical": 0, "Warning": 0}
        for scan in latest_scans:
            if str(scan.status or "").lower() == "online":
                health_counts["Healthy"] += 1
            else:
                health_counts["Critical"] += 1

        sla_stats = (
            self._scoped_dashboard_event_query()
            .with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.acknowledged_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_ack_seconds")
            )
            .filter(
                DashboardEvent.severity == "CRITICAL",
                DashboardEvent.is_acknowledged.is_(True),
                DashboardEvent.timestamp >= start_date,
                DashboardEvent.timestamp <= end_date,
            )
            .first()
        )
        mtta_seconds = round(sla_stats.avg_ack_seconds) if sla_stats and sla_stats.avg_ack_seconds else None

        # ── Top problematic: two-tier split (degraded vs chronically offline) ──
        _all_candidates = (
            db.session.query(
                Device.device_id,
                Device.device_name,
                Device.device_ip,
                Device.device_type,
                Device.classification_confidence,
                func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                    DailyDeviceStats.uptime_percent.isnot(None),
                    DailyDeviceStats.uptime_percent >= 0,
                    DailyDeviceStats.uptime_percent <= 200,
                ).label("avg_uptime"),
                func.avg(cast(DailyDeviceStats.avg_latency_ms, db.Float)).filter(
                    DailyDeviceStats.avg_latency_ms.isnot(None),
                    DailyDeviceStats.avg_latency_ms >= 0,
                    DailyDeviceStats.avg_latency_ms < 1e15,
                ).label("avg_latency_ms"),
                func.avg(cast(DailyDeviceStats.avg_packet_loss_pct, db.Float)).filter(
                    DailyDeviceStats.avg_packet_loss_pct.isnot(None),
                    DailyDeviceStats.avg_packet_loss_pct >= 0,
                    DailyDeviceStats.avg_packet_loss_pct <= 100,
                ).label("avg_packet_loss_pct"),
            )
            .join(DailyDeviceStats, DailyDeviceStats.device_id == Device.device_id)
            .filter(
                Device.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .group_by(Device.device_id, Device.device_name, Device.device_ip,
                       Device.device_type, Device.classification_confidence)
            .order_by(func.avg(cast(DailyDeviceStats.uptime_percent, db.Float)).filter(
                DailyDeviceStats.uptime_percent.isnot(None),
                DailyDeviceStats.uptime_percent >= 0,
                DailyDeviceStats.uptime_percent <= 200,
            ).asc().nullslast())
            .limit(50)
            .all()
        )
        # PR 17: Confidence gate — deprioritize LOW-confidence devices
        if _all_candidates:
            _filtered = [r for r in _all_candidates
                         if (getattr(r, 'classification_confidence', '') or '').strip().lower() != 'low']
            if _filtered:
                _all_candidates = _filtered

        # Split: degraded (online but struggling) vs chronically offline (0% uptime)
        _degraded = []
        _offline = []
        for r in _all_candidates:
            if r.avg_uptime is not None and float(r.avg_uptime) > 0:
                _degraded.append(r)
            else:
                _offline.append(r)

        # Rank degraded by composite score (higher = worse), take top 10
        _degraded.sort(
            key=lambda r: self._degradation_score(
                r.avg_uptime,
                r.avg_latency_ms,
                r.avg_packet_loss_pct,
            ) or 0,
            reverse=True,
        )
        problematic_devices = _degraded[:10]

        # Fallback: if no degraded devices from DailyDeviceStats, try raw scan history
        if not problematic_devices:
            availability_basis = "device_scan_history"
            _raw_degraded = []
            _raw_offline = []
            for row in raw_uptime_rows:
                pct = self._availability_pct(row.online_scans, row.total_scans)
                if pct is not None and pct > 0:
                    _raw_degraded.append(row)
                else:
                    _raw_offline.append(row)
            _raw_degraded.sort(
                key=lambda row: self._degradation_score(
                    self._availability_pct(row.online_scans, row.total_scans),
                    getattr(row, 'avg_latency', None),
                    getattr(row, 'avg_packet_loss', None),
                ) or 0,
                reverse=True,
            )
            problematic_devices = _raw_degraded[:10]
            # Merge offline from raw into the offline list
            _offline_from_raw = [
                type('_row', (), {
                    'device_name': r.device_name, 'device_ip': r.device_ip,
                })
                for r in _raw_offline
            ]
            _offline = list(_offline) + _offline_from_raw

        # Build chronically offline summary
        chronically_offline = {
            "count": len(_offline),
            "devices": [
                {"name": getattr(r, 'device_name', None) or "—", "ip": getattr(r, 'device_ip', None) or "—"}
                for r in _offline[:5]
            ],
            "note": "Devices with 0% uptime for the entire period — consider decommission review or physical inspection." if _offline else None,
        }

        # ── Data confidence metadata ───────────────────────────────────────
        _confidence = {
            "uptime_score": {
                "level": "HIGH" if availability_basis == "daily_device_stats" else ("MEDIUM" if uptime_score is not None else "NO_DATA"),
                "source": availability_basis if uptime_score is not None else None,
            },
            "avg_latency": {
                "level": "HIGH" if availability_basis == "daily_device_stats" and avg_latency is not None else ("MEDIUM" if avg_latency is not None else "NO_DATA"),
                "source": availability_basis if avg_latency is not None else None,
            },
            "prev_uptime_score": {
                "level": "HIGH" if prev_uptime_score is not None else "NO_DATA",
                "source": "daily_device_stats" if prev_uptime_score is not None else None,
            },
            "mtta_seconds": {
                "level": "HIGH" if mtta_seconds is not None else "NO_DATA",
                "source": "dashboard_events" if mtta_seconds is not None else None,
            },
        }

        # ── Fleet-wide packet loss ─────────────────────────────────────────
        fleet_avg_packet_loss = None
        if uptime_stats and hasattr(uptime_stats, 'avg_packet_loss') and uptime_stats.avg_packet_loss is not None:
            fleet_avg_packet_loss = round(uptime_stats.avg_packet_loss, 2)
        elif raw_uptime_rows:
            _pls = [float(r.avg_packet_loss) for r in raw_uptime_rows if getattr(r, 'avg_packet_loss', None) is not None]
            fleet_avg_packet_loss = round(sum(_pls) / len(_pls), 2) if _pls else None

        # ── Estimated downtime hours ──────────────────────────────────────
        downtime_hours = None
        if uptime_score is not None:
            downtime_hours = round((1.0 - uptime_score / 100.0) * trend_window_days * 24, 2)

        # ── Fleet-wide p95 latency (from raw scan history) ────────────────
        avg_p95_latency_ms = None
        if db.engine.dialect.name != "sqlite":
            try:
                from sqlalchemy import text as _text
                _p95_sql = _text("""
                    SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY ping_time_ms)
                           FILTER (WHERE ping_time_ms IS NOT NULL AND ping_time_ms < 1e15)
                           AS p95_latency_ms
                    FROM device_scan_history
                    WHERE device_ip IN (
                        SELECT d.device_ip FROM device d
                        WHERE d.is_monitored = true AND d.device_ip IS NOT NULL
                    )
                    AND scan_timestamp BETWEEN :start_date AND :end_date
                """)
                _p95_row = db.session.execute(_p95_sql, {
                    "start_date": start_date, "end_date": end_date
                }).fetchone()
                if _p95_row and _p95_row.p95_latency_ms is not None:
                    avg_p95_latency_ms = round(float(_p95_row.p95_latency_ms), 2)
            except Exception:
                pass

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "uptime_score": uptime_score,
            "avg_latency": avg_latency,
            "avg_packet_loss": fleet_avg_packet_loss,
            "avg_p95_latency_ms": avg_p95_latency_ms,
            "downtime_hours": downtime_hours,
            "availability_basis": availability_basis,
            "prev_uptime_score": prev_uptime_score,
            "data_health": {
                "scan_history_days": scan_history_days,
                "daily_stats_coverage": daily_stats_coverage,
                "trend_window_days": trend_window_days,
            },
            "health_distribution": health_counts,
            "sla_metrics": {
                "mtta_seconds": mtta_seconds,
                "mtta_human": str(timedelta(seconds=mtta_seconds)) if mtta_seconds is not None else None,
            },
            "top_problematic": [
                {
                    "device_id": row.device_id if hasattr(row, "device_id") else None,
                    "name": row.device_name,
                    "ip": row.device_ip,
                    "type": row.device_type,
                    "classification_confidence": getattr(row, "classification_confidence", None),
                    "uptime": (
                        round(row.avg_uptime, 2)
                        if hasattr(row, "avg_uptime") and row.avg_uptime is not None
                        else self._availability_pct(row.online_scans, row.total_scans)
                    ),
                    "avg_latency_ms": (
                        round(row.avg_latency_ms, 2)
                        if hasattr(row, "avg_latency_ms") and row.avg_latency_ms is not None
                        else (round(row.avg_latency, 2) if hasattr(row, "avg_latency") and row.avg_latency is not None else None)
                    ),
                    "avg_packet_loss_pct": (
                        round(row.avg_packet_loss_pct, 2)
                        if hasattr(row, "avg_packet_loss_pct") and row.avg_packet_loss_pct is not None
                        else (round(row.avg_packet_loss, 2) if hasattr(row, "avg_packet_loss") and row.avg_packet_loss is not None else None)
                    ),
                    "degradation_score": self._degradation_score(
                        row.avg_uptime if hasattr(row, "avg_uptime") else self._availability_pct(getattr(row, 'online_scans', None), getattr(row, 'total_scans', None)),
                        row.avg_latency_ms if hasattr(row, "avg_latency_ms") else getattr(row, 'avg_latency', None),
                        row.avg_packet_loss_pct if hasattr(row, "avg_packet_loss_pct") else getattr(row, 'avg_packet_loss', None),
                    ),
                }
                for row in problematic_devices
            ],
            "chronically_offline": chronically_offline,
            "total_devices": int(self._inventory_devices_query().count()),
            "_confidence": _confidence,
        }
