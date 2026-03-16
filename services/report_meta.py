from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from flask import has_request_context
from sqlalchemy import and_, bindparam, func, or_, text

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
from models.server_health_rollups import (
    ServerHealthDailyRollup,
    ServerHealthHourlyRollup,
)
from models.server_metric_threshold_state import ServerMetricThresholdState
from models.site import Site
from models.subnet import Subnet
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingDailyRollup,
    TrackingHistoryIntegrityAudit,
    TrackingHourlyRollup,
    TrackingSample,
)
from services.timescaledb_service import TimescaleDBService
from services.tracking_workstation import scoped_tracked_device_query

REPORT_DEFINITIONS = {
    "executive": {
        "source_tables": ["device", "daily_device_stats", "device_scan_history", "dashboard_events"],
        "freshness_sources": ["daily_device_stats", "device_scan_history", "dashboard_events"],
        "exportable_formats": ["pdf"],
    },
    "operational": {
        "source_tables": ["server_health_logs", "server_health_hourly_rollups", "server_health_daily_rollups", "dashboard_events", "device"],
        "freshness_sources": ["server_health_logs", "server_health_hourly_rollups", "server_health_daily_rollups", "dashboard_events"],
        "exportable_formats": ["pdf"],
    },
    "device-health": {
        "source_tables": ["server_health_logs", "server_health_hourly_rollups", "server_health_daily_rollups", "device"],
        "freshness_sources": ["server_health_logs", "server_health_hourly_rollups", "server_health_daily_rollups"],
        "exportable_formats": ["pdf"],
    },
    "productivity": {
        "source_tables": ["tracking_samples", "device_application_logs", "device_activity_logs", "tracking_hourly_rollups", "tracking_daily_rollups"],
        "freshness_sources": ["tracking_samples", "device_application_logs", "device_activity_logs", "tracking_hourly_rollups", "tracking_daily_rollups"],
        "exportable_formats": ["pdf"],
    },
    "network": {
        "source_tables": ["daily_device_stats", "device_interfaces", "interface_traffic_history", "dashboard_events", "device"],
        "freshness_sources": ["daily_device_stats", "interface_traffic_history", "dashboard_events"],
        "exportable_formats": ["pdf"],
    },
    "alerts": {
        "source_tables": ["dashboard_events", "device"],
        "freshness_sources": ["dashboard_events"],
        "exportable_formats": ["pdf"],
    },
    "maintenance-availability": {
        "source_tables": ["maintenance_window", "daily_device_stats", "device_scan_history", "tracked_device_availability_events", "device"],
        "freshness_sources": ["daily_device_stats", "device_scan_history", "tracked_device_availability_events", "maintenance_window"],
        "exportable_formats": ["pdf"],
    },
    "security-compliance": {
        "source_tables": ["dashboard_events", "audit_logs", "restricted_site_events", "tracking_history_integrity_audit", "server_metric_threshold_state"],
        "freshness_sources": ["dashboard_events", "audit_logs", "restricted_site_events", "tracking_history_integrity_audit", "server_metric_threshold_state"],
        "exportable_formats": ["pdf"],
    },
    "inventory-assets": {
        "source_tables": ["device", "tracked_devices", "device_identity_links", "device_identity_link_candidates", "sites", "departments", "subnets"],
        "freshness_sources": ["device", "tracked_devices", "device_identity_links", "device_identity_link_candidates", "sites", "departments", "subnets"],
        "exportable_formats": ["pdf"],
    },
    "tracking-operations": {
        "source_tables": ["tracked_devices", "tracking_samples", "device_activity_logs", "device_application_logs", "tracked_device_availability_events", "tracking_hourly_rollups", "tracking_daily_rollups", "tracking_history_integrity_audit"],
        "freshness_sources": ["tracking_samples", "device_activity_logs", "device_application_logs", "tracked_device_availability_events", "tracking_hourly_rollups", "tracking_daily_rollups", "tracking_history_integrity_audit"],
        "exportable_formats": ["pdf"],
    },
    "printer-operations": {
        "source_tables": ["printer_metrics", "print_job_audit", "device"],
        "freshness_sources": ["printer_metrics", "print_job_audit"],
        "exportable_formats": ["pdf"],
    },
}


def get_report_definition(report_type: str, granularity: str | None = None) -> dict:
    definition = dict(
        REPORT_DEFINITIONS.get(
            report_type,
            {
                "source_tables": [],
                "freshness_sources": [],
                "exportable_formats": ["pdf"],
            },
        )
    )
    normalized_granularity = str(granularity or "").strip().lower() or None
    if TimescaleDBService.is_timescaledb_enabled() and report_type == "operational":
        source_name = "server_health_logs"
        if normalized_granularity == "hourly":
            source_name = "server_health_hourly_cagg"
        elif normalized_granularity == "daily":
            source_name = "server_health_daily_cagg"
        definition["source_tables"] = [source_name, "dashboard_events", "device"]
        definition["freshness_sources"] = [source_name, "dashboard_events"]
        return definition
    if TimescaleDBService.is_timescaledb_enabled() and report_type == "device-health":
        source_name = "server_health_logs"
        if normalized_granularity == "hourly":
            source_name = "server_health_hourly_cagg"
        elif normalized_granularity == "daily":
            source_name = "server_health_daily_cagg"
        definition["source_tables"] = [source_name, "device"]
        definition["freshness_sources"] = [source_name]
        return definition
    if TimescaleDBService.is_timescaledb_enabled() and report_type == "productivity":
        definition["source_tables"] = [
            "tracking_samples",
            "device_application_logs",
            "device_activity_logs",
        ]
        definition["freshness_sources"] = [
            "tracking_samples",
            "device_application_logs",
            "device_activity_logs",
        ]
        return definition
    if TimescaleDBService.is_timescaledb_enabled() and report_type == "tracking-operations":
        definition["source_tables"] = [
            "tracked_devices",
            "tracking_samples",
            "device_activity_logs",
            "device_application_logs",
            "tracked_device_availability_events",
            "tracking_history_integrity_audit",
        ]
        definition["freshness_sources"] = [
            "tracking_samples",
            "device_activity_logs",
            "device_application_logs",
            "tracked_device_availability_events",
            "tracking_history_integrity_audit",
        ]
        return definition
    if TimescaleDBService.is_timescaledb_enabled():
        replacements = {
            "server_health_hourly_rollups": "server_health_hourly_cagg",
            "server_health_daily_rollups": "server_health_daily_cagg",
        }
        definition["source_tables"] = [
            replacements.get(source_name, source_name)
            for source_name in definition.get("source_tables", [])
        ]
        definition["freshness_sources"] = [
            replacements.get(source_name, source_name)
            for source_name in definition.get("freshness_sources", [])
        ]
    return definition


def _timescaledb_view_stats(source_name: str, start_date: datetime, end_date: datetime) -> dict:
    if source_name == "server_health_hourly_cagg":
        view_name = "server_health_hourly_cagg"
        time_column = "bucket_hour"
    elif source_name == "server_health_daily_cagg":
        view_name = "server_health_daily_cagg"
        time_column = "bucket_day"
    else:
        return {"count": 0, "latest": None, "range_count": 0, "distinct_buckets": 0}

    inventory_ids = [int(row.device_id) for row in db.session.query(_inventory_device_ids_subquery().c.device_id).all()]
    if not inventory_ids:
        return {"count": 0, "latest": None, "range_count": 0, "distinct_buckets": 0}

    query = text(f"""
        SELECT
            COUNT(*) AS count,
            MAX({time_column}) AS latest,
            COUNT(*) FILTER (
                WHERE {time_column} >= :start_date
                  AND {time_column} <= :end_date
            ) AS range_count,
            COUNT(DISTINCT CASE
                WHEN {time_column} >= :start_date
                 AND {time_column} <= :end_date
                THEN {time_column}
            END) AS distinct_buckets
        FROM {view_name}
        WHERE device_id IN :device_ids
    """).bindparams(bindparam("device_ids", expanding=True))
    row = db.session.execute(
        query,
        {
            "device_ids": inventory_ids,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().one()
    return {
        "count": int(row["count"] or 0),
        "latest": _coerce_datetime(row["latest"]),
        "range_count": int(row["range_count"] or 0),
        "distinct_buckets": int(row["distinct_buckets"] or 0),
    }


def _coerce_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _scope() -> dict:
    if not has_request_context():
        return {
            "scope_type": "global",
            "scope_key": "global",
            "site_id": None,
            "department_id": None,
        }
    return build_scope_context()


def _inventory_devices_query():
    if not has_request_context():
        return Device.query
    return scoped_query(Device)


def _inventory_device_ids_subquery():
    return _inventory_devices_query().with_entities(Device.device_id.label("device_id")).subquery()


def _inventory_device_ips_subquery():
    return _inventory_devices_query().with_entities(Device.device_ip.label("device_ip")).subquery()


def _tracked_devices_query():
    if not has_request_context():
        return TrackedDevice.query
    return scoped_tracked_device_query()


def _tracked_device_ids_subquery():
    return _tracked_devices_query().with_entities(TrackedDevice.id.label("device_id")).subquery()


def _scoped_dashboard_event_query():
    scope = _scope()
    query = DashboardEvent.query
    if scope.get("scope_type") == "global":
        return query
    device_ids = _inventory_device_ids_subquery()
    return query.filter(DashboardEvent.device_id.in_(db.session.query(device_ids.c.device_id)))


def _scoped_audit_log_query():
    scope = _scope()
    if scope.get("scope_type") == "global":
        return AuditLog.query

    device_ids = _inventory_device_ids_subquery()
    tracked_ids = _tracked_device_ids_subquery()
    filters = [
        and_(AuditLog.entity_type == "device", AuditLog.entity_id.in_(db.session.query(device_ids.c.device_id))),
        and_(AuditLog.entity_type == "tracked_device", AuditLog.entity_id.in_(db.session.query(tracked_ids.c.device_id))),
    ]
    if scope.get("scope_type") == "site" and scope.get("site_id") is not None:
        dept_ids = [
            row[0]
            for row in db.session.query(Department.id).filter(Department.site_id == scope["site_id"]).all()
        ]
        filters.append(and_(AuditLog.entity_type == "site", AuditLog.entity_id == scope["site_id"]))
        if dept_ids:
            filters.append(and_(AuditLog.entity_type == "department", AuditLog.entity_id.in_(dept_ids)))
    elif scope.get("scope_type") == "department" and scope.get("department_id") is not None:
        filters.append(and_(AuditLog.entity_type == "department", AuditLog.entity_id == scope["department_id"]))
        if scope.get("site_id") is not None:
            filters.append(and_(AuditLog.entity_type == "site", AuditLog.entity_id == scope["site_id"]))

    if not filters:
        return AuditLog.query.filter(False)
    return AuditLog.query.filter(or_(*filters))


def _source_query(source_name: str):
    inventory_ids = _inventory_device_ids_subquery()
    inventory_ips = _inventory_device_ips_subquery()
    tracked_ids = _tracked_device_ids_subquery()

    if source_name == "device":
        return _inventory_devices_query(), Device.updated_at
    if source_name == "daily_device_stats":
        return DailyDeviceStats.query.filter(DailyDeviceStats.device_id.in_(db.session.query(inventory_ids.c.device_id))), DailyDeviceStats.date
    if source_name == "device_scan_history":
        return DeviceScanHistory.query.filter(DeviceScanHistory.device_ip.in_(db.session.query(inventory_ips.c.device_ip))), DeviceScanHistory.scan_timestamp
    if source_name == "dashboard_events":
        return _scoped_dashboard_event_query(), DashboardEvent.timestamp
    if source_name == "server_health_logs":
        return ServerHealthLog.query.filter(ServerHealthLog.device_id.in_(db.session.query(inventory_ids.c.device_id))), ServerHealthLog.timestamp
    if source_name == "server_health_hourly_rollups":
        return ServerHealthHourlyRollup.query.filter(ServerHealthHourlyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id))), ServerHealthHourlyRollup.bucket_hour
    if source_name == "server_health_daily_rollups":
        return ServerHealthDailyRollup.query.filter(ServerHealthDailyRollup.device_id.in_(db.session.query(inventory_ids.c.device_id))), ServerHealthDailyRollup.bucket_day
    if source_name == "tracking_samples":
        return TrackingSample.query.filter(TrackingSample.device_id.in_(db.session.query(tracked_ids.c.device_id))), TrackingSample.received_at
    if source_name == "device_application_logs":
        return DeviceApplicationLog.query.filter(DeviceApplicationLog.device_id.in_(db.session.query(tracked_ids.c.device_id))), DeviceApplicationLog.timestamp
    if source_name == "device_activity_logs":
        return DeviceActivityLog.query.filter(DeviceActivityLog.device_id.in_(db.session.query(tracked_ids.c.device_id))), DeviceActivityLog.timestamp
    if source_name == "tracked_device_availability_events":
        return TrackedDeviceAvailabilityEvent.query.filter(TrackedDeviceAvailabilityEvent.device_id.in_(db.session.query(tracked_ids.c.device_id))), TrackedDeviceAvailabilityEvent.observed_at
    if source_name == "tracking_hourly_rollups":
        return TrackingHourlyRollup.query.filter(TrackingHourlyRollup.device_id.in_(db.session.query(tracked_ids.c.device_id))), TrackingHourlyRollup.bucket_hour
    if source_name == "tracking_daily_rollups":
        return TrackingDailyRollup.query.filter(TrackingDailyRollup.device_id.in_(db.session.query(tracked_ids.c.device_id))), TrackingDailyRollup.bucket_day
    if source_name == "device_interfaces":
        return DeviceInterface.query.filter(DeviceInterface.device_id.in_(db.session.query(inventory_ids.c.device_id))), DeviceInterface.updated_at
    if source_name == "interface_traffic_history":
        return (
            InterfaceTrafficHistory.query.join(
                DeviceInterface,
                DeviceInterface.interface_id == InterfaceTrafficHistory.interface_id,
            ).filter(DeviceInterface.device_id.in_(db.session.query(inventory_ids.c.device_id))),
            InterfaceTrafficHistory.timestamp,
        )
    if source_name == "printer_metrics":
        return PrinterMetrics.query.filter(PrinterMetrics.device_id.in_(db.session.query(inventory_ids.c.device_id))), PrinterMetrics.timestamp
    if source_name == "print_job_audit":
        return PrintJobAudit.query.filter(PrintJobAudit.device_id.in_(db.session.query(inventory_ids.c.device_id))), PrintJobAudit.submission_time
    if source_name == "maintenance_window":
        return MaintenanceWindow.query.filter(MaintenanceWindow.device_id.in_(db.session.query(inventory_ids.c.device_id))), MaintenanceWindow.end_time
    if source_name == "audit_logs":
        return _scoped_audit_log_query(), AuditLog.timestamp
    if source_name == "restricted_site_events":
        return RestrictedSiteEvent.query.filter(RestrictedSiteEvent.device_id.in_(db.session.query(tracked_ids.c.device_id))), RestrictedSiteEvent.received_at_utc
    if source_name == "tracking_history_integrity_audit":
        return TrackingHistoryIntegrityAudit.query.filter(TrackingHistoryIntegrityAudit.device_id.in_(db.session.query(tracked_ids.c.device_id))), TrackingHistoryIntegrityAudit.created_at
    if source_name == "server_metric_threshold_state":
        return ServerMetricThresholdState.query.filter(ServerMetricThresholdState.device_id.in_(db.session.query(inventory_ids.c.device_id))), ServerMetricThresholdState.updated_at
    if source_name == "tracked_devices":
        return _tracked_devices_query(), TrackedDevice.updated_at
    if source_name == "device_identity_links":
        return DeviceIdentityLink.query.filter(
            or_(
                DeviceIdentityLink.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DeviceIdentityLink.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
            )
        ), DeviceIdentityLink.updated_at
    if source_name == "device_identity_link_candidates":
        return DeviceIdentityLinkCandidate.query.filter(
            or_(
                DeviceIdentityLinkCandidate.device_id.in_(db.session.query(inventory_ids.c.device_id)),
                DeviceIdentityLinkCandidate.tracked_device_id.in_(db.session.query(tracked_ids.c.device_id)),
            )
        ), DeviceIdentityLinkCandidate.detected_at
    if source_name == "sites":
        return scoped_query(Site), Site.updated_at
    if source_name == "departments":
        return scoped_query(Department), Department.updated_at
    if source_name == "subnets":
        return scoped_query(Subnet), Subnet.updated_at
    return None, None


def _range_bounds_for_source(source_name: str, start_date: datetime, end_date: datetime):
    if source_name in {"daily_device_stats", "server_health_daily_rollups", "tracking_daily_rollups"}:
        return start_date.date(), end_date.date()
    return start_date, end_date


def _collect_source_stats(report_type: str, start_date: datetime, end_date: datetime, granularity: str | None = None) -> dict:
    stats = {}
    for source_name in get_report_definition(report_type, granularity).get("source_tables", []):
        if source_name in {"server_health_hourly_cagg", "server_health_daily_cagg"}:
            stats[source_name] = _timescaledb_view_stats(source_name, start_date, end_date)
            continue
        query, freshness_column = _source_query(source_name)
        if query is None:
            stats[source_name] = {"count": 0, "latest": None, "range_count": 0, "distinct_buckets": 0}
            continue
        count = int(query.count())
        latest = _coerce_datetime(query.with_entities(func.max(freshness_column)).scalar())
        range_start, range_end = _range_bounds_for_source(source_name, start_date, end_date)
        ranged_query = query.filter(freshness_column >= range_start, freshness_column <= range_end)
        range_count = int(ranged_query.count())
        distinct_buckets = int(
            ranged_query.with_entities(func.count(func.distinct(freshness_column))).scalar() or 0
        )
        stats[source_name] = {
            "count": count,
            "latest": latest,
            "range_count": range_count,
            "distinct_buckets": distinct_buckets,
        }
    return stats


def _freshness_threshold_seconds(report_type: str) -> int:
    if report_type in {"alerts", "productivity", "tracking-operations"}:
        return 6 * 3600
    if report_type == "inventory-assets":
        return 7 * 24 * 3600
    return 48 * 3600


def _freshness_band_seconds(report_type: str) -> tuple[int, int]:
    stale_seconds = _freshness_threshold_seconds(report_type)
    delayed_seconds = max(300, stale_seconds // 2)
    return delayed_seconds, stale_seconds


def _expected_hour_buckets(start_date: datetime, end_date: datetime) -> int:
    span_seconds = max(0.0, (end_date - start_date).total_seconds())
    return max(1, int((span_seconds + 3599) // 3600))


def _expected_day_buckets(start_date: datetime, end_date: datetime) -> int:
    return max(1, (end_date.date() - start_date.date()).days + 1)


def _coverage_warning(source_name: str, coverage: float) -> str:
    return f"rollup_coverage_low: {source_name} coverage is {coverage * 100:.1f}% for the requested range."


def _build_completeness_warnings(report_type: str, start_date: datetime, end_date: datetime, source_stats: dict) -> list[str]:
    span = end_date - start_date
    warnings: list[str] = []
    timescaledb_enabled = TimescaleDBService.is_timescaledb_enabled()
    server_health_hourly_source = (
        "server_health_hourly_cagg"
        if timescaledb_enabled
        else "server_health_hourly_rollups"
    )
    server_health_daily_source = (
        "server_health_daily_cagg"
        if timescaledb_enabled
        else "server_health_daily_rollups"
    )

    def _count(source_name: str) -> int:
        return int((source_stats.get(source_name) or {}).get("count") or 0)

    def _distinct_buckets(source_name: str) -> int:
        return int((source_stats.get(source_name) or {}).get("distinct_buckets") or 0)

    def _append_rollup_coverage(source_name: str, expected_buckets: int):
        if expected_buckets <= 0:
            return
        actual_buckets = _distinct_buckets(source_name)
        if actual_buckets <= 0:
            return
        coverage = actual_buckets / float(expected_buckets)
        if coverage < 0.8:
            warnings.append(_coverage_warning(source_name, coverage))

    if report_type in {"executive", "network"} and _count("daily_device_stats") == 0:
        warnings.append("daily_device_stats is empty; uptime rollups are not ready for enterprise reporting.")

    if report_type == "network":
        if _count("device_interfaces") == 0:
            warnings.append("device_interfaces is empty; interface inventory is missing.")
        if _count("interface_traffic_history") == 0:
            warnings.append("interface_traffic_history is empty; bandwidth reporting is unavailable.")

    if report_type in {"device-health", "operational"} and not timescaledb_enabled:
        if span > timedelta(hours=24) and _count(server_health_hourly_source) == 0:
            warnings.append(
                f"{server_health_hourly_source} is empty for the requested range; hourly summaries need backfill."
            )
        if span > timedelta(days=30) and _count(server_health_daily_source) == 0:
            warnings.append(
                f"{server_health_daily_source} is empty for the requested range; daily summaries need backfill."
            )

    if report_type in {"productivity", "tracking-operations"} and not timescaledb_enabled:
        if span > timedelta(hours=24) and _count("tracking_hourly_rollups") == 0:
            warnings.append("tracking_hourly_rollups is empty for the requested range; long-range tracking summaries are incomplete.")
        if span > timedelta(days=30) and _count("tracking_daily_rollups") == 0:
            warnings.append("tracking_daily_rollups is empty for the requested range; daily tracking summaries are incomplete.")

    if report_type == "maintenance-availability" and _count("daily_device_stats") == 0:
        warnings.append("daily_device_stats is empty; maintenance reporting is using raw/fallback availability data.")

    if report_type == "inventory-assets":
        if _count("sites") == 0:
            warnings.append("sites is empty; site-level inventory alignment is not configured.")
        if _count("departments") == 0:
            warnings.append("departments is empty; department-level inventory alignment is not configured.")
        if _count("subnets") == 0:
            warnings.append("subnets is empty; subnet alignment reporting is unavailable.")

    if report_type == "security-compliance":
        if _count("tracking_history_integrity_audit") == 0:
            warnings.append("tracking_history_integrity_audit is empty; integrity exception reporting has no persisted audit data.")
        if _count("server_metric_threshold_state") == 0:
            warnings.append("server_metric_threshold_state is empty; threshold compliance state is not yet populated.")

    if report_type == "printer-operations":
        if _count("printer_metrics") == 0:
            warnings.append("printer_metrics is empty; printer health rollups are not being collected.")
        if _count("print_job_audit") == 0:
            warnings.append("print_job_audit is empty; print job reporting is unavailable.")

    expected_day_buckets = _expected_day_buckets(start_date, end_date)
    expected_hour_buckets = _expected_hour_buckets(start_date, end_date)

    if report_type in {"executive", "network", "maintenance-availability"}:
        _append_rollup_coverage("daily_device_stats", expected_day_buckets)

    if report_type in {"device-health", "operational"} and span > timedelta(hours=24) and not timescaledb_enabled:
        _append_rollup_coverage(server_health_hourly_source, expected_hour_buckets)
        if span > timedelta(days=30):
            _append_rollup_coverage(server_health_daily_source, expected_day_buckets)

    if report_type in {"productivity", "tracking-operations"} and span > timedelta(hours=24) and not timescaledb_enabled:
        _append_rollup_coverage("tracking_hourly_rollups", expected_hour_buckets)
        if span > timedelta(days=30):
            _append_rollup_coverage("tracking_daily_rollups", expected_day_buckets)

    return warnings


def build_report_meta(
    report_type: str,
    payload: dict,
    *,
    start_date: datetime,
    end_date: datetime,
    row_count: int,
    cache_hit: bool,
    cache_ttl_seconds: int,
    cache_age_seconds: float,
) -> dict:
    generated_at = datetime.now(timezone.utc)
    scope = _scope()
    selected_granularity = (
        payload.get("granularity")
        or payload.get("heatmap_granularity")
        or payload.get("bucket_size")
        or "range"
    )
    report_definition = get_report_definition(report_type, selected_granularity)
    source_stats = _collect_source_stats(report_type, start_date, end_date, selected_granularity)
    warnings = _build_completeness_warnings(report_type, start_date, end_date, source_stats)

    freshness_sources = report_definition.get("freshness_sources") or report_definition.get("source_tables") or []
    source_latest_values = [
        item.get("latest")
        for source_name, item in source_stats.items()
        if source_name in freshness_sources and item.get("latest") is not None
    ]
    data_as_of = max(source_latest_values) if source_latest_values else None
    lag_values = [
        max(0, int((generated_at - latest).total_seconds()))
        for latest in source_latest_values
        if latest is not None
    ]
    max_source_lag_seconds = max(lag_values) if lag_values else None
    freshness_lag_seconds = max(0, int((generated_at - data_as_of).total_seconds())) if data_as_of else None

    delayed_seconds, stale_seconds = _freshness_band_seconds(report_type)
    if not source_latest_values:
        freshness_state = "empty"
    elif (freshness_lag_seconds or 0) > stale_seconds:
        freshness_state = "stale"
    elif (freshness_lag_seconds or 0) > delayed_seconds:
        freshness_state = "delayed"
    else:
        freshness_state = "fresh"

    scope_id = None
    if scope.get("scope_type") == "site":
        scope_id = scope.get("site_id")
    elif scope.get("scope_type") == "department":
        scope_id = scope.get("department_id")

    return {
        "report_type": report_type,
        "generated_at": generated_at.isoformat(),
        "scope_type": scope.get("scope_type"),
        "scope_id": scope_id,
        "granularity": selected_granularity,
        "source_tables": report_definition.get("source_tables", []),
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "freshness_state": freshness_state,
        "max_source_lag_seconds": max_source_lag_seconds,
        "cache_hit": bool(cache_hit),
        "cache_ttl_seconds": int(cache_ttl_seconds or 0),
        "cache_age_seconds": round(float(cache_age_seconds or 0.0), 3),
        "row_count": int(row_count or 0),
        "exportable_formats": report_definition.get("exportable_formats", ["pdf"]),
        "completeness_warnings": warnings,
        "freshness_sources": list(freshness_sources),
    }
