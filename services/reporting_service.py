"""
Read-only report generators with RBAC-aware scoping.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, desc, func, or_

from extensions import db
from middleware.rbac import build_scope_context, scoped_query
from models.audit_log import AuditLog
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.department import Department
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.interfaces import DeviceInterface, InterfaceTrafficHistory
from models.maintenance_window import MaintenanceWindow
from models.printer import PrintJobAudit, PrinterMetrics
from models.restricted_site_policy import RestrictedSiteEvent
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthDailyRollup, ServerHealthHourlyRollup
from models.server_metric_threshold_state import ServerMetricThresholdState
from models.site import Site
from models.subnet import Subnet
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingHistoryIntegrityAudit,
    TrackingSample,
)
from services.tracking_freshness import build_productivity_freshness_summary
from services.tracking_workstation import scoped_tracked_device_query

APP_CATEGORIES = {
    "Microsoft Word": "Productivity",
    "Microsoft Excel": "Productivity",
    "Microsoft PowerPoint": "Productivity",
    "Google Docs": "Productivity",
    "LibreOffice": "Productivity",
    "Notepad++": "Productivity",
    "Microsoft Outlook": "Productivity",
    "Thunderbird": "Productivity",
    "Microsoft Teams": "Communication",
    "Slack": "Communication",
    "Zoom": "Communication",
    "Discord": "Communication",
    "Skype": "Communication",
    "Visual Studio Code": "Development",
    "PyCharm": "Development",
    "IntelliJ": "Development",
    "Eclipse": "Development",
    "Terminal": "Development",
    "cmd": "Development",
    "powershell": "Development",
    "Git": "Development",
    "Google Chrome": "Browser",
    "Mozilla Firefox": "Browser",
    "Microsoft Edge": "Browser",
    "Opera": "Browser",
    "Safari": "Browser",
    "Spotify": "Entertainment",
    "VLC": "Entertainment",
    "Netflix": "Entertainment",
    "YouTube": "Entertainment",
}


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _classify_app(app_name):
    if not app_name:
        return "Other"
    app_name = str(app_name).lower()
    for known, category in APP_CATEGORIES.items():
        if known.lower() in app_name:
            return category
    return "Other"


def _safe_round(value, digits=2):
    return round(value, digits) if value is not None else None


class ReportingService:
    def __init__(self):
        self.scope = build_scope_context()

    def _inventory_devices_query(self, device_ids=None):
        query = scoped_query(Device)
        if device_ids:
            query = query.filter(Device.device_id.in_(device_ids))
        return query

    def _inventory_device_ids_subquery(self, device_ids=None):
        return self._inventory_devices_query(device_ids).with_entities(Device.device_id.label("device_id")).subquery()

    def _inventory_device_ips_subquery(self, device_ids=None):
        return self._inventory_devices_query(device_ids).with_entities(Device.device_ip.label("device_ip")).subquery()

    def _tracked_devices_query(self, device_ids=None):
        query = scoped_tracked_device_query()
        if device_ids:
            query = query.filter(TrackedDevice.id.in_(device_ids))
        return query

    def _tracked_device_ids_subquery(self, device_ids=None):
        return self._tracked_devices_query(device_ids).with_entities(TrackedDevice.id.label("device_id")).subquery()

    def _scoped_dashboard_event_query(self, device_ids=None):
        query = DashboardEvent.query
        if self.scope.get("scope_type") != "global":
            inventory_ids = self._inventory_device_ids_subquery(device_ids)
            query = query.filter(DashboardEvent.device_id.in_(db.session.query(inventory_ids.c.device_id)))
        elif device_ids:
            query = query.filter(DashboardEvent.device_id.in_(device_ids))
        return query

    def _scoped_audit_log_query(self):
        if self.scope.get("scope_type") == "global":
            return AuditLog.query

        inventory_ids = self._inventory_device_ids_subquery()
        tracked_ids = self._tracked_device_ids_subquery()
        filters = [
            and_(AuditLog.entity_type == "device", AuditLog.entity_id.in_(db.session.query(inventory_ids.c.device_id))),
            and_(AuditLog.entity_type == "tracked_device", AuditLog.entity_id.in_(db.session.query(tracked_ids.c.device_id))),
        ]
        if self.scope.get("scope_type") == "site" and self.scope.get("site_id") is not None:
            dept_ids = [
                row[0]
                for row in db.session.query(Department.id).filter(Department.site_id == self.scope["site_id"]).all()
            ]
            filters.append(and_(AuditLog.entity_type == "site", AuditLog.entity_id == self.scope["site_id"]))
            if dept_ids:
                filters.append(and_(AuditLog.entity_type == "department", AuditLog.entity_id.in_(dept_ids)))
        elif self.scope.get("scope_type") == "department" and self.scope.get("department_id") is not None:
            filters.append(and_(AuditLog.entity_type == "department", AuditLog.entity_id == self.scope["department_id"]))
            if self.scope.get("site_id") is not None:
                filters.append(and_(AuditLog.entity_type == "site", AuditLog.entity_id == self.scope["site_id"]))

        if not filters:
            return AuditLog.query.filter(False)
        return AuditLog.query.filter(or_(*filters))

    @staticmethod
    def _heatmap_day_index(ts):
        return (ts.weekday() + 1) % 7

    def _raw_scan_uptime_rows(self, device_ids=None, start_date=None, end_date=None):
        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        return (
            db.session.query(
                Device.device_id,
                Device.device_name,
                Device.device_ip,
                Device.device_type,
                func.count(DeviceScanHistory.scan_id).label("total_scans"),
                func.sum(case((func.lower(DeviceScanHistory.status) == "online", 1), else_=0)).label("online_scans"),
                func.avg(DeviceScanHistory.ping_time_ms).label("avg_latency"),
                func.avg(DeviceScanHistory.packet_loss).label("avg_packet_loss"),
            )
            .join(DeviceScanHistory, DeviceScanHistory.device_ip == Device.device_ip)
            .filter(
                Device.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DeviceScanHistory.scan_timestamp >= start_date,
                DeviceScanHistory.scan_timestamp <= end_date,
            )
            .group_by(Device.device_id, Device.device_name, Device.device_ip, Device.device_type)
            .all()
        )

    @staticmethod
    def _availability_pct(online_scans, total_scans):
        total = int(total_scans or 0)
        if total <= 0:
            return None
        return round((int(online_scans or 0) / total) * 100.0, 2)

    @staticmethod
    def _is_health_payload_empty(time_series, summary):
        return not bool(time_series) and not bool(summary)

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
            uptime_score = round((total_online_scans / total_scans) * 100.0, 2) if total_scans else 0.0
            avg_latency = round(raw_ping_stats.avg_latency, 2) if raw_ping_stats and raw_ping_stats.avg_latency is not None else 0.0

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
        mtta_seconds = round(sla_stats.avg_ack_seconds) if sla_stats and sla_stats.avg_ack_seconds else 0

        problematic_devices = (
            db.session.query(
                Device.device_name,
                Device.device_ip,
                Device.device_type,
                func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"),
            )
            .join(DailyDeviceStats, DailyDeviceStats.device_id == Device.device_id)
            .filter(
                Device.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .group_by(Device.device_id, Device.device_name, Device.device_ip, Device.device_type)
            .order_by(func.avg(DailyDeviceStats.uptime_percent).asc())
            .limit(10)
            .all()
        )
        if not problematic_devices:
            availability_basis = "device_scan_history"
            problematic_devices = sorted(
                raw_uptime_rows,
                key=lambda row: (
                    101.0 if self._availability_pct(row.online_scans, row.total_scans) is None else self._availability_pct(row.online_scans, row.total_scans),
                    -int(row.total_scans or 0),
                ),
            )[:10]

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "uptime_score": uptime_score,
            "avg_latency": avg_latency,
            "availability_basis": availability_basis,
            "health_distribution": health_counts,
            "sla_metrics": {
                "mtta_seconds": mtta_seconds,
                "mtta_human": str(timedelta(seconds=mtta_seconds)),
            },
            "top_problematic": [
                {
                    "name": row.device_name,
                    "ip": row.device_ip,
                    "type": row.device_type,
                    "uptime": (
                        round(row.avg_uptime, 2)
                        if hasattr(row, "avg_uptime") and row.avg_uptime is not None
                        else self._availability_pct(row.online_scans, row.total_scans)
                    ),
                }
                for row in problematic_devices
            ],
            "total_devices": int(self._inventory_devices_query().count()),
        }

    def get_operational_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        span = end_date - start_date

        if span <= timedelta(hours=24):
            heatmap = self._operational_heatmap_from_raw(start_date, end_date)
            granularity = "raw"
        elif span <= timedelta(days=30):
            heatmap = self._operational_heatmap_from_hourly(start_date, end_date)
            granularity = "hourly"
            if not heatmap:
                heatmap = self._operational_heatmap_from_raw(start_date, end_date)
                granularity = "raw"
        else:
            heatmap = self._operational_heatmap_from_daily(start_date, end_date)
            granularity = "daily"
            if not heatmap:
                heatmap = self._operational_heatmap_from_hourly(start_date, end_date)
                granularity = "hourly"
            if not heatmap:
                heatmap = self._operational_heatmap_from_raw(start_date, end_date)
                granularity = "raw"

        audit_logs = (
            self._scoped_dashboard_event_query()
            .filter(
                DashboardEvent.event_type.in_(["SYSTEM", "CONFIG", "SECURITY"]),
                DashboardEvent.timestamp >= start_date,
                DashboardEvent.timestamp <= end_date,
            )
            .order_by(DashboardEvent.timestamp.desc())
            .limit(50)
            .all()
        )
        new_devices = (
            self._inventory_devices_query()
            .filter(Device.created_at >= start_date, Device.created_at <= end_date)
            .order_by(Device.created_at.desc())
            .limit(20)
            .all()
        )
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "heatmap": heatmap,
            "heatmap_granularity": granularity,
            "audit_log": [row.to_dict() for row in audit_logs],
            "new_devices": [row.to_dict() for row in new_devices],
        }

    def _operational_heatmap_from_raw(self, start_date, end_date):
        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(ServerHealthLog.timestamp)
            .filter(
                ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthLog.timestamp >= start_date,
                ServerHealthLog.timestamp <= end_date,
            )
            .all()
        )
        buckets = {}
        for row in rows:
            if row.timestamp is None:
                continue
            key = (self._heatmap_day_index(row.timestamp), row.timestamp.hour)
            buckets[key] = buckets.get(key, 0) + 1
        return [[day, hour, count] for (day, hour), count in sorted(buckets.items())]

    def _operational_heatmap_from_hourly(self, start_date, end_date):
        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(
                ServerHealthHourlyRollup.bucket_hour.label("bucket_hour"),
                func.sum(ServerHealthHourlyRollup.sample_count).label("activity_count"),
            )
            .filter(
                ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthHourlyRollup.bucket_hour >= start_date,
                ServerHealthHourlyRollup.bucket_hour <= end_date,
            )
            .group_by(ServerHealthHourlyRollup.bucket_hour)
            .order_by(ServerHealthHourlyRollup.bucket_hour.asc())
            .all()
        )
        return [
            [self._heatmap_day_index(row.bucket_hour), row.bucket_hour.hour, int(row.activity_count or 0)]
            for row in rows
            if row.bucket_hour is not None
        ]

    def _operational_heatmap_from_daily(self, start_date, end_date):
        inventory_ids = self._inventory_device_ids_subquery()
        rows = (
            db.session.query(
                ServerHealthDailyRollup.bucket_day.label("bucket_day"),
                func.sum(ServerHealthDailyRollup.sample_count).label("activity_count"),
            )
            .filter(
                ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                ServerHealthDailyRollup.bucket_day >= start_date.date(),
                ServerHealthDailyRollup.bucket_day <= end_date.date(),
            )
            .group_by(ServerHealthDailyRollup.bucket_day)
            .order_by(ServerHealthDailyRollup.bucket_day.asc())
            .all()
        )
        return [
            [self._heatmap_day_index(datetime.combine(row.bucket_day, datetime.min.time())), 12, int(row.activity_count or 0)]
            for row in rows
            if row.bucket_day is not None
        ]

    def get_device_health_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        span = end_date - start_date
        if span <= timedelta(hours=24):
            time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
            granularity = "raw"
        elif span <= timedelta(days=30):
            time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
            granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"
        else:
            time_series, summary = self._health_from_daily(device_ids, start_date, end_date)
            granularity = "daily"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_hourly(device_ids, start_date, end_date)
                granularity = "hourly"
            if self._is_health_payload_empty(time_series, summary):
                time_series, summary = self._health_from_raw(device_ids, start_date, end_date)
                granularity = "raw"
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "granularity": granularity,
            "time_series": time_series,
            "summary": summary,
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

    @staticmethod
    def _build_health_summary(rows):
        return [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "avg_cpu": _safe_round(row.avg_cpu),
                "max_cpu": _safe_round(row.max_cpu),
                "avg_mem": _safe_round(row.avg_mem),
                "max_mem": _safe_round(row.max_mem),
                "avg_disk": _safe_round(row.avg_disk),
                "samples": row.samples,
            }
            for row in rows
        ]

    @staticmethod
    def _build_time_series(rows):
        by_device = {}
        for row in rows:
            by_device.setdefault(row.device_id, {"device_name": row.device_name, "points": []})
            by_device[row.device_id]["points"].append(
                {
                    "ts": row.timestamp.isoformat() if hasattr(row.timestamp, "isoformat") else str(row.timestamp),
                    "cpu": _safe_round(row.cpu_usage),
                    "mem": _safe_round(row.memory_usage),
                    "disk": _safe_round(row.disk_usage),
                    "net_in": _safe_round(row.network_in_bps),
                    "net_out": _safe_round(row.network_out_bps),
                }
            )
        return by_device

    def get_productivity_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        tracked_ids = self._tracked_device_ids_subquery(device_ids)

        app_rows = (
            db.session.query(
                DeviceApplicationLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceApplicationLog.application_name,
                func.sum(DeviceApplicationLog.duration).label("total_seconds"),
                func.count(DeviceApplicationLog.id).label("session_count"),
            )
            .join(TrackedDevice, TrackedDevice.id == DeviceApplicationLog.device_id)
            .filter(
                DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                DeviceApplicationLog.timestamp >= start_date,
                DeviceApplicationLog.timestamp <= end_date,
            )
            .group_by(
                DeviceApplicationLog.device_id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                DeviceApplicationLog.application_name,
            )
            .all()
        )

        app_breakdown = {}
        category_totals = {}
        for row in app_rows:
            category = _classify_app(row.application_name)
            category_totals[category] = category_totals.get(category, 0) + int(row.total_seconds or 0)
            app_breakdown.setdefault(
                row.device_id,
                {"device_name": row.device_name, "employee_name": row.employee_name, "apps": []},
            )
            app_breakdown[row.device_id]["apps"].append(
                {
                    "name": row.application_name,
                    "category": category,
                    "total_seconds": int(row.total_seconds or 0),
                    "sessions": int(row.session_count or 0),
                }
            )

        activity_rows = (
            db.session.query(
                DeviceActivityLog.device_id,
                DeviceActivityLog.activity_type,
                func.sum(DeviceActivityLog.event_count).label("total_events"),
            )
            .filter(
                DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                DeviceActivityLog.timestamp >= start_date,
                DeviceActivityLog.timestamp <= end_date,
            )
            .group_by(DeviceActivityLog.device_id, DeviceActivityLog.activity_type)
            .all()
        )
        activity_summary = {}
        for row in activity_rows:
            activity_summary.setdefault(row.device_id, {"active": 0, "idle": 0, "keyboard": 0, "mouse": 0})
            activity_type = str(row.activity_type or "").lower()
            if activity_type in activity_summary[row.device_id]:
                activity_summary[row.device_id][activity_type] = int(row.total_events or 0)
            elif activity_type == "scroll":
                activity_summary[row.device_id]["active"] += int(row.total_events or 0)

        freshness_device_ids = [
            int(row[0])
            for row in (
                db.session.query(TrackingSample.device_id)
                .filter(
                    TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingSample.received_at >= start_date,
                    TrackingSample.received_at <= end_date,
                )
                .distinct()
                .all()
            )
            if row and row[0]
        ]
        freshness_device_ids = sorted(set(freshness_device_ids) | set(app_breakdown.keys()) | set(activity_summary.keys()))
        freshness_summary = build_productivity_freshness_summary(freshness_device_ids, start_date, end_date)

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "app_breakdown": app_breakdown,
            "category_totals": category_totals,
            "activity_summary": activity_summary,
            "freshness_summary": freshness_summary,
        }

    def get_network_performance_report(self, device_ids=None, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(hours=24))
        inventory_ids = self._inventory_device_ids_subquery(device_ids)

        bandwidth_rows = (
            db.session.query(
                DeviceInterface.device_id,
                Device.device_name,
                DeviceInterface.name.label("interface_name"),
                InterfaceTrafficHistory.timestamp,
                InterfaceTrafficHistory.rx_bps,
                InterfaceTrafficHistory.tx_bps,
                InterfaceTrafficHistory.rx_utilization_pct,
                InterfaceTrafficHistory.tx_utilization_pct,
            )
            .join(DeviceInterface, DeviceInterface.interface_id == InterfaceTrafficHistory.interface_id)
            .join(Device, Device.device_id == DeviceInterface.device_id)
            .filter(
                DeviceInterface.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                InterfaceTrafficHistory.timestamp >= start_date,
                InterfaceTrafficHistory.timestamp <= end_date,
            )
            .order_by(InterfaceTrafficHistory.timestamp.asc())
            .all()
        )
        bandwidth = {}
        for row in bandwidth_rows:
            key = f"{row.device_id}_{row.interface_name}"
            bandwidth.setdefault(
                key,
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "interface": row.interface_name,
                    "points": [],
                },
            )
            bandwidth[key]["points"].append(
                {
                    "ts": row.timestamp.isoformat(),
                    "rx_bps": _safe_round(row.rx_bps),
                    "tx_bps": _safe_round(row.tx_bps),
                    "rx_util": _safe_round(row.rx_utilization_pct),
                    "tx_util": _safe_round(row.tx_utilization_pct),
                }
            )

        uptime_summary = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "avg_uptime": _safe_round(row.avg_uptime),
                "avg_latency_ms": _safe_round(row.avg_latency),
                "avg_packet_loss": _safe_round(row.avg_packet_loss),
            }
            for row in (
                db.session.query(
                    DailyDeviceStats.device_id,
                    Device.device_name,
                    func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"),
                    func.avg(DailyDeviceStats.avg_latency_ms).label("avg_latency"),
                    func.avg(DailyDeviceStats.avg_packet_loss_pct).label("avg_packet_loss"),
                )
                .join(Device, Device.device_id == DailyDeviceStats.device_id)
                .filter(
                    DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    DailyDeviceStats.date >= start_date.date(),
                    DailyDeviceStats.date <= end_date.date(),
                )
                .group_by(DailyDeviceStats.device_id, Device.device_name)
                .all()
            )
        ]
        uptime_basis = "daily_device_stats"
        if not uptime_summary:
            uptime_basis = "device_scan_history"
            uptime_summary = [
                {
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "avg_uptime": self._availability_pct(row.online_scans, row.total_scans),
                    "avg_latency_ms": _safe_round(row.avg_latency),
                    "avg_packet_loss": _safe_round(row.avg_packet_loss),
                }
                for row in self._raw_scan_uptime_rows(
                    device_ids=device_ids,
                    start_date=start_date,
                    end_date=end_date,
                )
            ]

        mttr = (
            self._scoped_dashboard_event_query(device_ids)
            .with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.resolved_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_resolve_seconds"),
                func.count(DashboardEvent.event_id).label("total_incidents"),
            )
            .filter(
                DashboardEvent.resolved.is_(True),
                DashboardEvent.resolved_at.isnot(None),
                DashboardEvent.timestamp >= start_date,
                DashboardEvent.timestamp <= end_date,
            )
            .first()
        )
        mttr_seconds = round(mttr.avg_resolve_seconds) if mttr and mttr.avg_resolve_seconds else 0

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "uptime_basis": uptime_basis,
            "bandwidth": bandwidth,
            "uptime_summary": uptime_summary,
            "mttr": {
                "seconds": mttr_seconds,
                "human": str(timedelta(seconds=mttr_seconds)),
                "total_incidents": int(mttr.total_incidents or 0) if mttr else 0,
            },
        }

    def get_alert_history_report(self, start_date=None, end_date=None, severity=None, device_ids=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=7))
        base_query = self._scoped_dashboard_event_query(device_ids).filter(
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        )
        if severity:
            base_query = base_query.filter(DashboardEvent.severity == str(severity).upper())

        alerts = base_query.order_by(DashboardEvent.timestamp.desc()).limit(200).all()
        device_name_map = {
            row.device_id: row.device_name
            for row in self._inventory_devices_query().filter(
                Device.device_id.in_([alert.device_id for alert in alerts if alert.device_id is not None])
            )
        }
        alert_list = []
        for alert in alerts:
            row = alert.to_dict()
            row["device_name"] = device_name_map.get(alert.device_id) or alert.device_ip
            row["resolved_at"] = alert.resolved_at.isoformat() if alert.resolved_at else None
            alert_list.append(row)

        daily_rows = (
            base_query.with_entities(
                func.date(DashboardEvent.timestamp).label("day"),
                DashboardEvent.severity,
                func.count(DashboardEvent.event_id).label("count"),
            )
            .group_by(func.date(DashboardEvent.timestamp), DashboardEvent.severity)
            .order_by(func.date(DashboardEvent.timestamp))
            .all()
        )
        daily_trend = {}
        for row in daily_rows:
            daily_trend.setdefault(str(row.day), {})
            daily_trend[str(row.day)][row.severity] = int(row.count or 0)

        tta = (
            base_query.with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.acknowledged_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_tta")
            )
            .filter(DashboardEvent.is_acknowledged.is_(True))
            .first()
        )
        ttr = (
            base_query.with_entities(
                func.avg(
                    func.extract("epoch", DashboardEvent.resolved_at)
                    - func.extract("epoch", DashboardEvent.timestamp)
                ).label("avg_ttr")
            )
            .filter(DashboardEvent.resolved.is_(True), DashboardEvent.resolved_at.isnot(None))
            .first()
        )
        tta_seconds = round(tta.avg_tta) if tta and tta.avg_tta else 0
        ttr_seconds = round(ttr.avg_ttr) if ttr and ttr.avg_ttr else 0

        top_devices = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name or "Unknown",
                "device_ip": row.device_ip or "",
                "alert_count": int(row.alert_count or 0),
            }
            for row in (
                base_query.with_entities(
                    DashboardEvent.device_id,
                    Device.device_name,
                    Device.device_ip,
                    func.count(DashboardEvent.event_id).label("alert_count"),
                )
                .outerjoin(Device, Device.device_id == DashboardEvent.device_id)
                .group_by(DashboardEvent.device_id, Device.device_name, Device.device_ip)
                .order_by(desc("alert_count"))
                .limit(10)
                .all()
            )
        ]
        severity_breakdown = {
            row.severity: int(row.count or 0)
            for row in (
                base_query.with_entities(
                    DashboardEvent.severity,
                    func.count(DashboardEvent.event_id).label("count"),
                )
                .group_by(DashboardEvent.severity)
                .all()
            )
        }

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "alerts": alert_list,
            "daily_trend": daily_trend,
            "tta": {"seconds": tta_seconds, "human": str(timedelta(seconds=tta_seconds))},
            "ttr": {"seconds": ttr_seconds, "human": str(timedelta(seconds=ttr_seconds))},
            "top_alerted_devices": top_devices,
            "severity_breakdown": severity_breakdown,
        }

    def get_maintenance_availability_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        inventory_ips = self._inventory_device_ips_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        scheduled_windows = [
            row.to_dict()
            for row in (
                MaintenanceWindow.query.join(Device, Device.device_id == MaintenanceWindow.device_id)
                .filter(
                    MaintenanceWindow.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    MaintenanceWindow.start_time <= end_date,
                    MaintenanceWindow.end_time >= start_date,
                )
                .order_by(MaintenanceWindow.start_time.desc())
                .limit(50)
                .all()
            )
        ]
        maintenance_devices = [
            row.to_dict()
            for row in self._inventory_devices_query().filter(Device.maintenance_mode.is_(True)).limit(50).all()
        ]

        downtime_rows = (
            db.session.query(
                DailyDeviceStats.device_id,
                Device.device_name,
                Device.device_ip,
                func.avg(DailyDeviceStats.uptime_percent).label("avg_uptime"),
            )
            .join(Device, Device.device_id == DailyDeviceStats.device_id)
            .filter(
                DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DailyDeviceStats.date >= start_date.date(),
                DailyDeviceStats.date <= end_date.date(),
            )
            .group_by(DailyDeviceStats.device_id, Device.device_name, Device.device_ip)
            .order_by(func.avg(DailyDeviceStats.uptime_percent).asc())
            .limit(20)
            .all()
        )
        downtime_leaders = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "device_ip": row.device_ip,
                "availability_pct": _safe_round(row.avg_uptime),
            }
            for row in downtime_rows
        ]
        if not downtime_leaders:
            device_map = {
                row.device_ip: (row.device_id, row.device_name)
                for row in self._inventory_devices_query().all()
            }
            raw_rows = (
                db.session.query(
                    DeviceScanHistory.device_ip,
                    func.count(DeviceScanHistory.scan_id).label("total_scans"),
                    func.sum(case((func.lower(DeviceScanHistory.status) == "online", 1), else_=0)).label("online_scans"),
                )
                .filter(
                    DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip)),
                    DeviceScanHistory.scan_timestamp >= start_date,
                    DeviceScanHistory.scan_timestamp <= end_date,
                )
                .group_by(DeviceScanHistory.device_ip)
                .order_by(desc("total_scans"))
                .limit(20)
                .all()
            )
            for row in raw_rows:
                total_scans = int(row.total_scans or 0)
                online_scans = int(row.online_scans or 0)
                availability_pct = round((online_scans / total_scans) * 100.0, 2) if total_scans else 0.0
                device_id, device_name = device_map.get(row.device_ip, (None, row.device_ip))
                downtime_leaders.append(
                    {
                        "device_id": device_id,
                        "device_name": device_name,
                        "device_ip": row.device_ip,
                        "availability_pct": availability_pct,
                    }
                )

        tracked_instability = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "offline_events": int(row.offline_events or 0),
                "degraded_events": int(row.degraded_events or 0),
                "event_count": int(row.event_count or 0),
            }
            for row in (
                db.session.query(
                    TrackedDevice.id.label("device_id"),
                    TrackedDevice.device_name,
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "offline", 1), else_=0)).label("offline_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "degraded", 1), else_=0)).label("degraded_events"),
                    func.count(TrackedDeviceAvailabilityEvent.id).label("event_count"),
                )
                .join(TrackedDeviceAvailabilityEvent, TrackedDeviceAvailabilityEvent.device_id == TrackedDevice.id)
                .filter(
                    TrackedDevice.id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                )
                .group_by(TrackedDevice.id, TrackedDevice.device_name)
                .order_by(desc("offline_events"), desc("degraded_events"))
                .limit(20)
                .all()
            )
        ]

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "scheduled_windows": scheduled_windows,
            "maintenance_devices": maintenance_devices,
            "downtime_leaders": downtime_leaders,
            "tracked_instability": tracked_instability,
            "summary": {
                "scheduled_windows": len(scheduled_windows),
                "maintenance_devices": len(maintenance_devices),
                "downtime_leaders": len(downtime_leaders),
                "tracked_instability": len(tracked_instability),
            },
        }

    def get_security_compliance_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        alerts_query = self._scoped_dashboard_event_query().filter(
            DashboardEvent.timestamp >= start_date,
            DashboardEvent.timestamp <= end_date,
        )
        recent_alerts = alerts_query.order_by(DashboardEvent.timestamp.desc()).limit(50).all()
        device_name_map = {row.device_id: row.device_name for row in self._inventory_devices_query().all()}
        recent_audit_log = (
            self._scoped_audit_log_query()
            .filter(AuditLog.timestamp >= start_date, AuditLog.timestamp <= end_date)
            .order_by(AuditLog.timestamp.desc())
            .limit(50)
            .all()
        )
        restricted_site_violations = [
            {
                "device_name": row.device_name,
                "domain": row.domain,
                "count": int(row.hit_count or 0),
                "observed_at_utc": row.latest_seen.isoformat() if row.latest_seen else None,
            }
            for row in (
                db.session.query(
                    TrackedDevice.device_name,
                    RestrictedSiteEvent.domain,
                    func.count(RestrictedSiteEvent.id).label("hit_count"),
                    func.max(RestrictedSiteEvent.observed_at_utc).label("latest_seen"),
                )
                .join(TrackedDevice, TrackedDevice.id == RestrictedSiteEvent.device_id)
                .filter(
                    RestrictedSiteEvent.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    RestrictedSiteEvent.observed_at_utc >= start_date,
                    RestrictedSiteEvent.observed_at_utc <= end_date,
                )
                .group_by(TrackedDevice.device_name, RestrictedSiteEvent.domain)
                .order_by(desc("hit_count"))
                .limit(20)
                .all()
            )
        ]
        integrity_breakdown = {
            row.severity: int(row.count or 0)
            for row in (
                TrackingHistoryIntegrityAudit.query.with_entities(
                    TrackingHistoryIntegrityAudit.severity,
                    func.count(TrackingHistoryIntegrityAudit.id).label("count"),
                )
                .filter(
                    TrackingHistoryIntegrityAudit.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingHistoryIntegrityAudit.created_at >= start_date,
                    TrackingHistoryIntegrityAudit.created_at <= end_date,
                )
                .group_by(TrackingHistoryIntegrityAudit.severity)
                .all()
            )
        }
        threshold_breaches = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "metric_key": row.metric_key,
                "breach_streak": int(row.breach_streak or 0),
                "last_state": row.last_state,
            }
            for row in (
                db.session.query(
                    ServerMetricThresholdState.device_id,
                    Device.device_name,
                    ServerMetricThresholdState.metric_key,
                    ServerMetricThresholdState.breach_streak,
                    ServerMetricThresholdState.last_state,
                )
                .join(Device, Device.device_id == ServerMetricThresholdState.device_id)
                .filter(
                    ServerMetricThresholdState.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    ServerMetricThresholdState.breach_streak > 0,
                )
                .order_by(ServerMetricThresholdState.breach_streak.desc())
                .limit(20)
                .all()
            )
        ]
        summary = {
            "total_alerts": int(alerts_query.count()),
            "critical_alerts": int(alerts_query.filter(DashboardEvent.severity == "CRITICAL").count()),
            "acknowledged_alerts": int(alerts_query.filter(DashboardEvent.is_acknowledged.is_(True)).count()),
            "unresolved_alerts": int(alerts_query.filter(DashboardEvent.resolved.is_(False)).count()),
            "audit_rows": len(recent_audit_log),
            "restricted_site_violations": len(restricted_site_violations),
            "integrity_findings": int(sum(integrity_breakdown.values())),
            "threshold_breaches": len(threshold_breaches),
        }

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "recent_alerts": [
                {**row.to_dict(), "device_name": device_name_map.get(row.device_id) or row.device_ip}
                for row in recent_alerts
            ],
            "recent_audit_log": [row.to_dict() for row in recent_audit_log],
            "restricted_site_violations": restricted_site_violations,
            "integrity_breakdown": integrity_breakdown,
            "threshold_breaches": threshold_breaches,
        }

    def get_inventory_assets_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        tracked_ids = self._tracked_device_ids_subquery()

        inventory_devices = [row.to_dict() for row in self._inventory_devices_query().order_by(Device.created_at.desc()).limit(50).all()]
        tracked_devices = [row.to_dict() for row in self._tracked_devices_query().order_by(TrackedDevice.created_at.desc()).limit(50).all()]

        active_links = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "tracked_device_id": row.tracked_device_id,
                "tracked_device_name": row.tracked_device_name,
                "confidence": int(row.confidence or 0),
                "link_source": row.link_source,
            }
            for row in (
                db.session.query(
                    DeviceIdentityLink.device_id,
                    Device.device_name,
                    DeviceIdentityLink.tracked_device_id,
                    TrackedDevice.device_name.label("tracked_device_name"),
                    DeviceIdentityLink.confidence,
                    DeviceIdentityLink.link_source,
                )
                .join(Device, Device.device_id == DeviceIdentityLink.device_id)
                .join(TrackedDevice, TrackedDevice.id == DeviceIdentityLink.tracked_device_id)
                .filter(
                    DeviceIdentityLink.is_active.is_(True),
                    or_(
                        DeviceIdentityLink.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                        DeviceIdentityLink.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    ),
                )
                .order_by(DeviceIdentityLink.updated_at.desc())
                .limit(50)
                .all()
            )
        ]
        pending_candidates = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "tracked_device_id": row.tracked_device_id,
                "tracked_device_name": row.tracked_device_name,
                "candidate_score": int(row.candidate_score or 0),
                "status": row.status,
            }
            for row in (
                db.session.query(
                    DeviceIdentityLinkCandidate.device_id,
                    Device.device_name,
                    DeviceIdentityLinkCandidate.tracked_device_id,
                    TrackedDevice.device_name.label("tracked_device_name"),
                    DeviceIdentityLinkCandidate.candidate_score,
                    DeviceIdentityLinkCandidate.status,
                )
                .join(Device, Device.device_id == DeviceIdentityLinkCandidate.device_id)
                .join(TrackedDevice, TrackedDevice.id == DeviceIdentityLinkCandidate.tracked_device_id)
                .filter(
                    DeviceIdentityLinkCandidate.status == "pending",
                    or_(
                        DeviceIdentityLinkCandidate.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                        DeviceIdentityLinkCandidate.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    ),
                )
                .order_by(DeviceIdentityLinkCandidate.detected_at.desc())
                .limit(50)
                .all()
            )
        ]

        linked_inventory_count = int(
            db.session.query(func.count(func.distinct(DeviceIdentityLink.device_id)))
            .filter(
                DeviceIdentityLink.is_active.is_(True),
                DeviceIdentityLink.device_id.in_(db.session.query(inventory_ids.c.device_id)),
            )
            .scalar()
            or 0
        )
        linked_tracked_count = int(
            db.session.query(func.count(func.distinct(DeviceIdentityLink.tracked_device_id)))
            .filter(
                DeviceIdentityLink.is_active.is_(True),
                DeviceIdentityLink.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
            )
            .scalar()
            or 0
        )
        summary = {
            "inventory_devices": int(self._inventory_devices_query().count()),
            "tracked_devices": int(self._tracked_devices_query().count()),
            "sites": int(scoped_query(Site).count()),
            "departments": int(scoped_query(Department).count()),
            "subnets": int(scoped_query(Subnet).count()),
            "active_links": len(active_links),
            "pending_candidates": len(pending_candidates),
            "unlinked_inventory_devices": max(int(self._inventory_devices_query().count()) - linked_inventory_count, 0),
            "unlinked_tracked_devices": max(int(self._tracked_devices_query().count()) - linked_tracked_count, 0),
        }

        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "inventory_devices": inventory_devices,
            "tracked_devices": tracked_devices,
            "active_links": active_links,
            "pending_candidates": pending_candidates,
        }

    def get_tracking_operations_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=7))
        tracked_ids = self._tracked_device_ids_subquery()
        scoped_tracked_devices = self._tracked_devices_query().all()
        freshness_device_ids = [row.id for row in scoped_tracked_devices]
        freshness_summary = build_productivity_freshness_summary(freshness_device_ids, start_date, end_date)
        freshness_devices = freshness_summary.get("devices") or {}
        device_name_map = {row.id: row.device_name for row in scoped_tracked_devices}

        top_applications = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "application_name": row.application_name,
                "category": _classify_app(row.application_name),
                "total_seconds": int(row.total_seconds or 0),
            }
            for row in (
                db.session.query(
                    DeviceApplicationLog.device_id,
                    TrackedDevice.device_name,
                    DeviceApplicationLog.application_name,
                    func.sum(DeviceApplicationLog.duration).label("total_seconds"),
                )
                .join(TrackedDevice, TrackedDevice.id == DeviceApplicationLog.device_id)
                .filter(
                    DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceApplicationLog.timestamp >= start_date,
                    DeviceApplicationLog.timestamp <= end_date,
                )
                .group_by(DeviceApplicationLog.device_id, TrackedDevice.device_name, DeviceApplicationLog.application_name)
                .order_by(desc("total_seconds"))
                .limit(20)
                .all()
            )
        ]
        activity_totals = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "activity_type": row.activity_type,
                "total_events": int(row.total_events or 0),
            }
            for row in (
                db.session.query(
                    DeviceActivityLog.device_id,
                    TrackedDevice.device_name,
                    DeviceActivityLog.activity_type,
                    func.sum(DeviceActivityLog.event_count).label("total_events"),
                )
                .join(TrackedDevice, TrackedDevice.id == DeviceActivityLog.device_id)
                .filter(
                    DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceActivityLog.timestamp >= start_date,
                    DeviceActivityLog.timestamp <= end_date,
                )
                .group_by(DeviceActivityLog.device_id, TrackedDevice.device_name, DeviceActivityLog.activity_type)
                .order_by(desc("total_events"))
                .limit(30)
                .all()
            )
        ]
        availability_breakdown = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "offline_events": int(row.offline_events or 0),
                "degraded_events": int(row.degraded_events or 0),
                "online_events": int(row.online_events or 0),
            }
            for row in (
                db.session.query(
                    TrackedDevice.id.label("device_id"),
                    TrackedDevice.device_name,
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "offline", 1), else_=0)).label("offline_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "degraded", 1), else_=0)).label("degraded_events"),
                    func.sum(case((TrackedDeviceAvailabilityEvent.status == "online", 1), else_=0)).label("online_events"),
                )
                .join(TrackedDeviceAvailabilityEvent, TrackedDeviceAvailabilityEvent.device_id == TrackedDevice.id)
                .filter(
                    TrackedDevice.id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                )
                .group_by(TrackedDevice.id, TrackedDevice.device_name)
                .order_by(desc("offline_events"), desc("degraded_events"))
                .limit(20)
                .all()
            )
        ]
        integrity_breakdown = {
            row.severity: int(row.count or 0)
            for row in (
                TrackingHistoryIntegrityAudit.query.with_entities(
                    TrackingHistoryIntegrityAudit.severity,
                    func.count(TrackingHistoryIntegrityAudit.id).label("count"),
                )
                .filter(
                    TrackingHistoryIntegrityAudit.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingHistoryIntegrityAudit.created_at >= start_date,
                    TrackingHistoryIntegrityAudit.created_at <= end_date,
                )
                .group_by(TrackingHistoryIntegrityAudit.severity)
                .all()
            )
        }
        if not integrity_breakdown:
            integrity_breakdown = {
                row.integrity_status: int(row.count or 0)
                for row in (
                    TrackingSample.query.with_entities(
                        TrackingSample.integrity_status,
                        func.count(TrackingSample.id).label("count"),
                    )
                    .filter(
                        TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                        TrackingSample.received_at >= start_date,
                        TrackingSample.received_at <= end_date,
                    )
                    .group_by(TrackingSample.integrity_status)
                    .all()
                )
            }
        device_freshness = []
        for device_id, device_name in device_name_map.items():
            freshness = freshness_devices.get(str(device_id)) or freshness_devices.get(device_id) or {}
            device_freshness.append(
                {
                    "device_id": device_id,
                    "device_name": device_name,
                    "freshness_state": freshness.get("freshness_state", "empty"),
                    "coverage_pct": freshness.get("coverage_pct", 0.0),
                    "sample_count": freshness.get("sample_count", 0),
                    "report_eligible": bool(freshness.get("report_eligible")),
                }
            )
        device_freshness.sort(key=lambda item: (item["freshness_state"], -(item["coverage_pct"] or 0)))
        summary = {
            "tracked_devices": len(scoped_tracked_devices),
            "sample_count": int(
                TrackingSample.query.filter(
                    TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackingSample.received_at >= start_date,
                    TrackingSample.received_at <= end_date,
                ).count()
            ),
            "application_logs": int(
                DeviceApplicationLog.query.filter(
                    DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceApplicationLog.timestamp >= start_date,
                    DeviceApplicationLog.timestamp <= end_date,
                ).count()
            ),
            "activity_logs": int(
                DeviceActivityLog.query.filter(
                    DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    DeviceActivityLog.timestamp >= start_date,
                    DeviceActivityLog.timestamp <= end_date,
                ).count()
            ),
            "availability_events": int(
                TrackedDeviceAvailabilityEvent.query.filter(
                    TrackedDeviceAvailabilityEvent.device_id.in_(db.session.query(tracked_ids.c.device_id)),
                    TrackedDeviceAvailabilityEvent.observed_at >= start_date,
                    TrackedDeviceAvailabilityEvent.observed_at <= end_date,
                ).count()
            ),
        }
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": summary,
            "freshness_summary": freshness_summary,
            "device_freshness": device_freshness[:20],
            "top_applications": top_applications,
            "activity_totals": activity_totals,
            "availability_breakdown": availability_breakdown,
            "integrity_breakdown": integrity_breakdown,
        }

    def get_printer_operations_report(self, start_date=None, end_date=None):
        end_date = end_date or _utcnow_naive()
        start_date = start_date or (end_date - timedelta(days=30))
        inventory_ids = self._inventory_device_ids_subquery()
        printer_filter = func.lower(Device.device_type).like("%printer%")
        printer_device_count = int(self._inventory_devices_query().filter(printer_filter).count())

        printer_status = [
            {
                "device_id": row.device_id,
                "device_name": row.device_name,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "status": row.status,
                "page_count_total": row.page_count_total,
            }
            for row in (
                db.session.query(
                    PrinterMetrics.device_id,
                    Device.device_name,
                    PrinterMetrics.timestamp,
                    PrinterMetrics.status,
                    PrinterMetrics.page_count_total,
                )
                .join(Device, Device.device_id == PrinterMetrics.device_id)
                .filter(
                    PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    PrinterMetrics.timestamp >= start_date,
                    PrinterMetrics.timestamp <= end_date,
                )
                .order_by(PrinterMetrics.timestamp.desc())
                .limit(20)
                .all()
            )
        ]
        print_volume = [
            {
                "printer_name": row.printer_name,
                "user_account": row.user_account,
                "job_count": int(row.job_count or 0),
                "total_pages": int(row.total_pages or 0),
            }
            for row in (
                db.session.query(
                    PrintJobAudit.printer_name,
                    PrintJobAudit.user_account,
                    func.count(PrintJobAudit.id).label("job_count"),
                    func.sum(PrintJobAudit.page_count).label("total_pages"),
                )
                .filter(
                    PrintJobAudit.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    PrintJobAudit.submission_time >= start_date,
                    PrintJobAudit.submission_time <= end_date,
                )
                .group_by(PrintJobAudit.printer_name, PrintJobAudit.user_account)
                .order_by(desc("job_count"))
                .limit(20)
                .all()
            )
        ]
        trigger_start = end_date - timedelta(days=30)
        printer_metrics_count_30d = int(
            PrinterMetrics.query.filter(
                PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                PrinterMetrics.timestamp >= trigger_start,
                PrinterMetrics.timestamp <= end_date,
            ).count()
        )
        print_job_count_30d = int(
            PrintJobAudit.query.filter(
                PrintJobAudit.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                PrintJobAudit.submission_time >= trigger_start,
                PrintJobAudit.submission_time <= end_date,
            ).count()
        )
        consecutive_metric_devices = int(
            db.session.query(func.count())
            .select_from(
                db.session.query(
                    PrinterMetrics.device_id.label("device_id"),
                    func.count(func.distinct(func.date(PrinterMetrics.timestamp))).label("days_with_data"),
                )
                .join(Device, Device.device_id == PrinterMetrics.device_id)
                .filter(
                    PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                    printer_filter,
                    PrinterMetrics.timestamp >= end_date - timedelta(days=14),
                    PrinterMetrics.timestamp <= end_date,
                )
                .group_by(PrinterMetrics.device_id)
                .having(func.count(func.distinct(func.date(PrinterMetrics.timestamp))) >= 14)
                .subquery()
            )
            .scalar()
            or 0
        )
        promotion_ready = (
            printer_metrics_count_30d >= 10000
            or print_job_count_30d >= 5000
            or consecutive_metric_devices >= 10
        )
        return {
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": {
                "printer_devices": printer_device_count,
                "printer_status_rows": len(printer_status),
                "print_volume_rows": len(print_volume),
                "promotion_ready": promotion_ready,
            },
            "promotion_triggers": {
                "printer_metrics_30d": printer_metrics_count_30d,
                "print_job_audit_30d": print_job_count_30d,
                "managed_printers_with_14d_ingestion": consecutive_metric_devices,
                "promotion_ready": promotion_ready,
            },
            "printer_status": printer_status,
            "print_volume": print_volume,
        }
