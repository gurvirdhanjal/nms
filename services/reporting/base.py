"""Base class and shared helpers for the reporting service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import g as _flask_g, has_request_context
from sqlalchemy import and_, bindparam, case, cast, desc, func, literal_column, or_, text

from extensions import db
from middleware.rbac import build_scope_context, scoped_query
from models.audit_log import AuditLog
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.department import Department
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.tracked_device import TrackedDevice, TrackedDeviceAvailabilityEvent
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


def _non_agent_scan_filter(model):
    """Exclude agent HTTP upload rows from ICMP/latency report calculations."""
    return or_(
        model.scan_type.is_(None),
        model.scan_type != "agent_push",
    )


def _row_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(key, default)
    return getattr(row, key, default)


class ReportingServiceBase:
    def __init__(self):
        self.scope = build_scope_context()

    def _inventory_devices_query(self, device_ids=None):
        query = scoped_query(Device)
        if device_ids:
            query = query.filter(Device.device_id.in_(device_ids))
        return query

    def _inventory_device_ids_subquery(self, device_ids=None):
        return self._inventory_devices_query(device_ids).with_entities(Device.device_id.label("device_id")).subquery()

    def _inventory_device_id_list(self, device_ids=None):
        # Memoize per request — the health report fallback chain calls this up to 3×
        # (hourly → raw → daily) for the same device_ids. One DB round-trip is enough.
        cache_attr = f"_inv_id_list_{tuple(sorted(device_ids)) if device_ids else 'all'}"
        if has_request_context():
            cached = getattr(_flask_g, cache_attr, None)
            if cached is not None:
                return cached

        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        result = [
            int(row.device_id)
            for row in db.session.query(inventory_ids.c.device_id).all()
            if row.device_id is not None
        ]

        if has_request_context():
            setattr(_flask_g, cache_attr, result)
        return result

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

    @staticmethod
    def _timescaledb_rows(statement: str, device_ids, **params):
        if not device_ids:
            return []
        query = text(statement).bindparams(bindparam("device_ids", expanding=True))
        result = db.session.execute(query, {"device_ids": device_ids, **params})
        return [dict(row._mapping) for row in result]

    def _raw_scan_uptime_rows(self, device_ids=None, start_date=None, end_date=None):
        """Match scan history by IP, with hostname fallback for IP-changed devices.

        Uses UNION of two indexed queries instead of an OR-join, which lets
        PostgreSQL use idx_device_scan_history_ip_time for the primary path
        and avoids the cross-join behaviour of OR in JOIN ON.

        Hostname branch only picks up scans whose device_ip differs from the
        current device IP (i.e. scans recorded before the device changed IP).
        UNION deduplicates any scan_id that appears in both branches.
        """
        inventory_ids = self._inventory_device_ids_subquery(device_ids)
        inv_filter = Device.device_id.in_(db.session.query(inventory_ids.c.device_id))
        time_filter = and_(
            DeviceScanHistory.scan_timestamp >= start_date,
            DeviceScanHistory.scan_timestamp <= end_date,
            _non_agent_scan_filter(DeviceScanHistory),
        )

        columns = (
            Device.device_id.label("device_id"),
            Device.device_name.label("device_name"),
            Device.device_ip.label("device_ip"),
            Device.device_type.label("device_type"),
            DeviceScanHistory.scan_id.label("scan_id"),
            DeviceScanHistory.status.label("status"),
            DeviceScanHistory.status_detail.label("status_detail"),
            DeviceScanHistory.ping_time_ms.label("ping_time_ms"),
            DeviceScanHistory.packet_loss.label("packet_loss"),
        )

        # Branch 1: match by IP (indexed via idx_device_scan_history_ip_time)
        ip_match = (
            db.session.query(*columns)
            .join(DeviceScanHistory, DeviceScanHistory.device_ip == Device.device_ip)
            .filter(inv_filter, time_filter)
        )

        # Branch 2: match by hostname where IP differs (captures IP-changed devices)
        name_match = (
            db.session.query(*columns)
            .join(DeviceScanHistory, DeviceScanHistory.device_name == Device.device_name)
            .filter(
                inv_filter,
                time_filter,
                Device.device_name.isnot(None),
                Device.device_name != "",
                DeviceScanHistory.device_ip != Device.device_ip,
            )
        )

        combined = ip_match.union(name_match).subquery()

        return (
            db.session.query(
                combined.c.device_id,
                combined.c.device_name,
                combined.c.device_ip,
                combined.c.device_type,
                func.count(combined.c.scan_id).label("total_scans"),
                # COUNT non-null returns bigint — avoids SUM(integer) type-inference overflow.
                # CASE with literal_column avoids bound-param type ambiguity.
                func.count(
                    case((func.lower(combined.c.status) == "online", literal_column("1")))
                ).label("online_scans"),
                # Guard against overflow: CASE WHEN returns NULL for extreme/sentinel values;
                # AVG() ignores NULLs, so only sane values enter the aggregate.
                # Function.filter() aggregate FILTER clause is silently dropped by SQLAlchemy's
                # legacy Query API on UNION subquery columns, so WHERE-based filtering is used
                # here via CASE WHEN ... ELSE NULL END inside the aggregate.
                func.avg(
                    case(
                        (combined.c.ping_time_ms.between(0, 60000), combined.c.ping_time_ms),
                        else_=literal_column("NULL"),
                    )
                ).label("avg_latency"),
                func.min(
                    case(
                        (combined.c.ping_time_ms.between(0, 60000), combined.c.ping_time_ms),
                        else_=literal_column("NULL"),
                    )
                ).label("min_latency_ms"),
                func.avg(
                    case(
                        (combined.c.packet_loss.between(0, 100), combined.c.packet_loss),
                        else_=literal_column("NULL"),
                    )
                ).label("avg_packet_loss"),
            )
            .group_by(combined.c.device_id, combined.c.device_name, combined.c.device_ip, combined.c.device_type)
            .all()
        )

    @staticmethod
    def _availability_pct(online_scans, total_scans):
        total = int(total_scans or 0)
        if total <= 0:
            return None
        return round((int(online_scans or 0) / total) * 100.0, 2)

    @staticmethod
    def _degradation_score(uptime_pct, avg_latency_ms, avg_packet_loss_pct):
        """Composite degradation score (0-100, higher = worse).

        Weights: 50% uptime deficit, 25% latency penalty, 25% packet loss.
        Surfaces online-but-degraded devices above always-offline ones.
        Returns None when all inputs are None.
        """
        if uptime_pct is None and avg_latency_ms is None and avg_packet_loss_pct is None:
            return None
        score = 0.0
        if uptime_pct is not None:
            score += (100.0 - max(0.0, min(100.0, float(uptime_pct)))) * 0.5
        else:
            score += 50.0  # unknown uptime treated as worst
        if avg_latency_ms is not None:
            score += min(float(avg_latency_ms) / 500.0, 1.0) * 25.0
        if avg_packet_loss_pct is not None:
            score += min(float(avg_packet_loss_pct) / 20.0, 1.0) * 25.0
        return round(score, 2)

    @staticmethod
    def _is_health_payload_empty(time_series, summary):
        return not bool(time_series) and not bool(summary)
