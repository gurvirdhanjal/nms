"""Executive fleet health report mixin."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, func

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
                func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"),
                func.avg(DailyDeviceStats.avg_latency_ms).label("avg_latency"),
            )
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .first()
        )
        availability_basis = "daily_device_stats"
        uptime_score = round(uptime_stats.avg_uptime, 2) if uptime_stats and uptime_stats.avg_uptime is not None else None
        avg_latency = round(uptime_stats.avg_latency, 2) if uptime_stats and uptime_stats.avg_latency is not None else None

        raw_uptime_rows = self._raw_scan_uptime_rows(start_date=start_date, end_date=end_date)
        if uptime_score is None or avg_latency is None:
            availability_basis = "device_scan_history"
            total_scans = sum(int(row.total_scans or 0) for row in raw_uptime_rows)
            total_online_scans = sum(int(row.online_scans or 0) for row in raw_uptime_rows)
            raw_ping_stats = (
                db.session.query(func.avg(DeviceScanHistory.ping_time_ms).label("avg_latency"))
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
            db.session.query(func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"))
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= prev_start.date(),
                DailyDeviceStats.date <= prev_end.date(),
            )
            .first()
        )
        prev_uptime_score = round(prev_stats.avg_uptime, 2) if prev_stats and prev_stats.avg_uptime is not None else None

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

        problematic_devices = (
            db.session.query(
                Device.device_id,
                Device.device_name,
                Device.device_ip,
                Device.device_type,
                Device.classification_confidence,
                func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"),
            )
            .join(DailyDeviceStats, DailyDeviceStats.device_id == Device.device_id)
            .filter(
                Device.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .group_by(Device.device_id, Device.device_name, Device.device_ip,
                       Device.device_type, Device.classification_confidence)
            .order_by(func.avg(DailyDeviceStats.uptime_percent).asc())
            .limit(10)
            .all()
        )
        # PR 17: Confidence gate — deprioritize LOW-confidence devices
        if problematic_devices:
            filtered = [r for r in problematic_devices
                        if (getattr(r, 'classification_confidence', '') or '').strip().lower() != 'low']
            if filtered:
                problematic_devices = filtered
        if not problematic_devices:
            availability_basis = "device_scan_history"
            problematic_devices = sorted(
                raw_uptime_rows,
                key=lambda row: (
                    101.0 if self._availability_pct(row.online_scans, row.total_scans) is None else self._availability_pct(row.online_scans, row.total_scans),
                    -int(row.total_scans or 0),
                ),
            )[:10]

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

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "uptime_score": uptime_score,
            "avg_latency": avg_latency,
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
                }
                for row in problematic_devices
            ],
            "total_devices": int(self._inventory_devices_query().count()),
            "_confidence": _confidence,
        }
