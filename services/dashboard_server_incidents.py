from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func

from extensions import db
from models.dashboard import DashboardEvent
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthDailyRollup, ServerHealthHourlyRollup
from services.server_thresholds import (
    PRIMARY_HEALTH_METRICS,
    alert_metric_name,
    build_chart_threshold_bands,
    evaluate_metrics_for_log,
    get_merged_thresholds,
    metric_display_name,
)
from utils.server_health import compute_server_health, query_latest_server_health_logs


METRIC_VALUE_ATTR = {
    "cpu_usage_pct": "cpu_usage",
    "memory_usage_pct": "memory_usage",
    "disk_usage_pct": "disk_usage",
}

METRIC_SHORT_KEY = {
    "cpu_usage_pct": "cpu",
    "memory_usage_pct": "memory",
    "disk_usage_pct": "disk",
}

METRIC_PRESSURE_LABEL = {
    "cpu_usage_pct": "CPU Pressure",
    "memory_usage_pct": "Memory Pressure",
    "disk_usage_pct": "Disk Pressure",
}

PRIMARY_METRIC_ORDER = ("memory_usage_pct", "cpu_usage_pct", "disk_usage_pct")
LEGACY_ALERT_ALIASES = {
    "health_cpu_usage_pct": ("health_cpu",),
    "health_memory_usage_pct": ("health_ram",),
    "health_disk_usage_pct": ("health_disk",),
}

SEVERITY_WEIGHT = {
    "critical": 300.0,
    "warning": 200.0,
    "offline": 300.0,
    "healthy": 0.0,
    "unknown": 0.0,
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso_utc(ts: datetime | None) -> str | None:
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def _severity_label(state: str) -> str:
    normalized = str(state or "").strip().lower()
    if normalized == "critical":
        return "Critical"
    if normalized == "warning":
        return "Warning"
    if normalized == "offline":
        return "Critical"
    if normalized == "healthy":
        return "Healthy"
    return "Unknown"


def _severity_rank(state: str) -> int:
    normalized = str(state or "").strip().lower()
    if normalized in {"critical", "offline"}:
        return 3
    if normalized == "warning":
        return 2
    if normalized == "healthy":
        return 1
    return 0


def _safe_float(value) -> float | None:
    try:
        numeric = float(value)
        return numeric if numeric == numeric else None
    except (TypeError, ValueError):
        return None


def _format_metric_value(value: float | None, unit: str = "%") -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}{unit}"


def _sort_numeric(values: list[float]) -> list[float]:
    return sorted(value for value in values if value is not None)


def _calc_p95(values: list[float]) -> float:
    ordered = _sort_numeric(values)
    if not ordered:
        return 0.0
    index = int(len(ordered) * 0.95)
    return float(ordered[min(index, len(ordered) - 1)])


def _metric_threshold_value(metric_threshold: dict, state: str) -> float:
    if str(state).lower() == "critical":
        return float(metric_threshold.get("critical") or 0.0)
    return float(metric_threshold.get("warning") or 0.0)


def _metric_breach_score(value: float | None, metric_threshold: dict, state: str) -> float:
    if value is None:
        return 0.0
    threshold_value = _metric_threshold_value(metric_threshold, state)
    return max(0.0, float(value) - threshold_value)


def _matches_metric_event(event: DashboardEvent, metric_key: str) -> bool:
    metric_name = str(getattr(event, "metric_name", "") or "")
    canonical = alert_metric_name(metric_key)
    if metric_name == canonical:
        return True
    return metric_name in LEGACY_ALERT_ALIASES.get(canonical, ())


def _query_latest_scan_map(device_ips: list[str]) -> dict[str, DeviceScanHistory]:
    if not device_ips:
        return {}

    latest_scans_subq = (
        db.session.query(
            DeviceScanHistory.device_ip,
            func.max(DeviceScanHistory.scan_id).label("max_scan_id"),
        )
        .filter(DeviceScanHistory.device_ip.in_(device_ips))
        .group_by(DeviceScanHistory.device_ip)
        .subquery()
    )

    latest_scans = (
        db.session.query(DeviceScanHistory)
        .join(latest_scans_subq, DeviceScanHistory.scan_id == latest_scans_subq.c.max_scan_id)
        .all()
    )
    return {scan.device_ip: scan for scan in latest_scans}


def _hour_label(hour_value) -> str:
    """Normalize a date_trunc/strftime/date value to 'YYYY-MM-DDTHH:00:00' for consistent comparison.

    PostgreSQL date_trunc returns a datetime object whose str() is space-separated
    ('2026-03-23 12:00:00'), while the comparison target uses the T-separator format.
    SQLite strftime already returns the T-format string.
    Daily rollup bucket_day is a date object — formatted as 'YYYY-MM-DDT00:00:00'.
    """
    if isinstance(hour_value, datetime):
        return hour_value.strftime("%Y-%m-%dT%H:00:00")
    if isinstance(hour_value, date):
        return hour_value.strftime("%Y-%m-%dT00:00:00")
    if hour_value is not None:
        return str(hour_value)[:19].replace(" ", "T")
    return ""


def _build_hour_bucket():
    backend = db.engine.url.get_backend_name()
    if backend == "sqlite":
        return func.strftime("%Y-%m-%dT%H:00:00", ServerHealthLog.timestamp).label("hour")
    return func.date_trunc("hour", ServerHealthLog.timestamp).label("hour")


def _query_metric_trends(device_ids: list[int], start_time: datetime):
    """Raw hourly aggregation from ServerHealthLog (agent source). Used for ≤24h range."""
    if not device_ids:
        return []
    hour_bucket = _build_hour_bucket()
    return (
        db.session.query(
            hour_bucket,
            func.avg(ServerHealthLog.cpu_usage).label("avg_cpu"),
            func.avg(ServerHealthLog.memory_usage).label("avg_memory"),
            func.avg(ServerHealthLog.disk_usage).label("avg_disk"),
            func.max(ServerHealthLog.cpu_usage).label("max_cpu"),
            func.max(ServerHealthLog.memory_usage).label("max_memory"),
            func.max(ServerHealthLog.disk_usage).label("max_disk"),
            func.avg(ServerHealthLog.network_in_bps).label("avg_net_in"),
            func.avg(ServerHealthLog.network_out_bps).label("avg_net_out"),
        )
        .filter(
            ServerHealthLog.source == "agent",
            ServerHealthLog.timestamp >= start_time,
            ServerHealthLog.device_id.in_(device_ids),
        )
        .group_by(hour_bucket)
        .order_by(hour_bucket)
        .all()
    )


def _query_latency_trend_raw(device_ids: list[int], start_time: datetime):
    """Raw hourly ICMP latency aggregation. Used for ≤24h range."""
    if not device_ids:
        return []
    hour_bucket = _build_hour_bucket()
    return (
        db.session.query(
            hour_bucket,
            func.avg(ServerHealthLog.ping_latency_ms).label("avg_latency"),
        )
        .filter(
            ServerHealthLog.source == "icmp",
            ServerHealthLog.timestamp >= start_time,
            ServerHealthLog.device_id.in_(device_ids),
        )
        .group_by(hour_bucket)
        .order_by(hour_bucket)
        .all()
    )


def _query_metric_trends_hourly(device_ids: list[int], start_time: datetime):
    """Hourly rollup aggregation. Used for 25h–168h (7d) range."""
    if not device_ids:
        return []
    return (
        db.session.query(
            ServerHealthHourlyRollup.bucket_hour,
            func.avg(ServerHealthHourlyRollup.avg_cpu_usage).label("avg_cpu"),
            func.avg(ServerHealthHourlyRollup.avg_memory_usage).label("avg_memory"),
            func.avg(ServerHealthHourlyRollup.avg_disk_usage).label("avg_disk"),
            func.avg(ServerHealthHourlyRollup.max_cpu_usage).label("max_cpu"),
            func.avg(ServerHealthHourlyRollup.max_memory_usage).label("max_memory"),
            # max_disk_usage was never added to the hourly rollup model — use avg as proxy.
            # Peak spikes won't surface here; raw-log path (≤24h) provides true max.
            func.avg(ServerHealthHourlyRollup.avg_disk_usage).label("max_disk"),
            func.avg(ServerHealthHourlyRollup.avg_network_in_bps).label("avg_net_in"),
            func.avg(ServerHealthHourlyRollup.avg_network_out_bps).label("avg_net_out"),
        )
        .filter(
            ServerHealthHourlyRollup.source == "agent",
            ServerHealthHourlyRollup.bucket_hour >= start_time,
            ServerHealthHourlyRollup.device_id.in_(device_ids),
        )
        .group_by(ServerHealthHourlyRollup.bucket_hour)
        .order_by(ServerHealthHourlyRollup.bucket_hour)
        .all()
    )


def _query_latency_trend_hourly(device_ids: list[int], start_time: datetime):
    """Hourly ICMP latency rollup. Used for 25h–168h (7d) range."""
    if not device_ids:
        return []
    return (
        db.session.query(
            ServerHealthHourlyRollup.bucket_hour,
            func.avg(ServerHealthHourlyRollup.avg_ping_latency_ms).label("avg_latency"),
        )
        .filter(
            ServerHealthHourlyRollup.source == "icmp",
            ServerHealthHourlyRollup.bucket_hour >= start_time,
            ServerHealthHourlyRollup.device_id.in_(device_ids),
        )
        .group_by(ServerHealthHourlyRollup.bucket_hour)
        .order_by(ServerHealthHourlyRollup.bucket_hour)
        .all()
    )


def _query_metric_trends_daily(device_ids: list[int], start_time: datetime):
    """Daily rollup aggregation. Used for >168h (30d) range."""
    if not device_ids:
        return []
    return (
        db.session.query(
            ServerHealthDailyRollup.bucket_day,
            func.avg(ServerHealthDailyRollup.avg_cpu_usage).label("avg_cpu"),
            func.avg(ServerHealthDailyRollup.avg_memory_usage).label("avg_memory"),
            func.avg(ServerHealthDailyRollup.avg_disk_usage).label("avg_disk"),
            func.avg(ServerHealthDailyRollup.max_cpu_usage).label("max_cpu"),
            func.avg(ServerHealthDailyRollup.max_memory_usage).label("max_memory"),
            # max_disk_usage was never added to the daily rollup model — use avg as proxy.
            func.avg(ServerHealthDailyRollup.avg_disk_usage).label("max_disk"),
            func.avg(ServerHealthDailyRollup.avg_network_in_bps).label("avg_net_in"),
            func.avg(ServerHealthDailyRollup.avg_network_out_bps).label("avg_net_out"),
        )
        .filter(
            ServerHealthDailyRollup.source == "agent",
            ServerHealthDailyRollup.bucket_day >= start_time.date(),
            ServerHealthDailyRollup.device_id.in_(device_ids),
        )
        .group_by(ServerHealthDailyRollup.bucket_day)
        .order_by(ServerHealthDailyRollup.bucket_day)
        .all()
    )


def _query_latency_trend_daily(device_ids: list[int], start_time: datetime):
    """Daily ICMP latency rollup. Used for >168h (30d) range."""
    if not device_ids:
        return []
    return (
        db.session.query(
            ServerHealthDailyRollup.bucket_day,
            func.avg(ServerHealthDailyRollup.avg_ping_latency_ms).label("avg_latency"),
        )
        .filter(
            ServerHealthDailyRollup.source == "icmp",
            ServerHealthDailyRollup.bucket_day >= start_time.date(),
            ServerHealthDailyRollup.device_id.in_(device_ids),
        )
        .group_by(ServerHealthDailyRollup.bucket_day)
        .order_by(ServerHealthDailyRollup.bucket_day)
        .all()
    )


def _query_hourly_coverage(device_ids: list[int], start_time: datetime):
    if not device_ids:
        return set()
    hour_bucket = _build_hour_bucket()
    rows = (
        db.session.query(hour_bucket, ServerHealthLog.device_id)
        .filter(
            ServerHealthLog.source == "agent",
            ServerHealthLog.timestamp >= start_time,
            ServerHealthLog.device_id.in_(device_ids),
        )
        .group_by(hour_bucket, ServerHealthLog.device_id)
        .all()
    )
    return {
        (_hour_label(row[0]), int(row[1]))
        for row in rows
        if row[0] is not None and row[1] is not None
    }


def _compute_window_uptime_pct(
    hourly_pairs: set[tuple[str, int]],
    window_start: datetime,
    window_end: datetime,
    total_servers: int,
) -> float:
    if total_servers <= 0:
        return 0.0
    start_label = window_start.strftime("%Y-%m-%dT%H:00:00")
    end_label = window_end.strftime("%Y-%m-%dT%H:00:00")
    unique_hours = {
        (hour_label, device_id)
        for hour_label, device_id in hourly_pairs
        if hour_label and device_id is not None and start_label <= hour_label < end_label
    }
    expected = total_servers * 24
    if expected <= 0:
        return 0.0
    return round((len(unique_hours) / expected) * 100.0, 1)


_METRIC_AVG_ATTR = {
    "cpu_usage_pct": "avg_cpu",
    "memory_usage_pct": "avg_memory",
    "disk_usage_pct": "avg_disk",
}
_METRIC_MAX_ATTR = {
    "cpu_usage_pct": "max_cpu",
    "memory_usage_pct": "max_memory",
    "disk_usage_pct": "max_disk",
}


def _build_series_payload(metric_key: str, rows, current_window_start: datetime):
    threshold_profile = get_merged_thresholds().get("metrics", {}).get(metric_key, {})
    attr = _METRIC_AVG_ATTR[metric_key]
    max_attr = _METRIC_MAX_ATTR[metric_key]
    labels = []
    values = []
    peak_values = []
    previous_values = []

    window_start_str = current_window_start.strftime("%Y-%m-%dT%H:00:00")
    for row in rows:
        hour_value = row[0]
        label = _hour_label(hour_value)
        numeric_value = _safe_float(getattr(row, attr, None))
        peak_value = _safe_float(getattr(row, max_attr, None))
        if label >= window_start_str:
            labels.append(label)
            values.append(round(numeric_value or 0.0, 2))
            peak_values.append(round(peak_value, 2) if peak_value is not None else None)
        elif numeric_value is not None:
            previous_values.append(numeric_value)

    markers = []
    warning = float(threshold_profile.get("warning") or 0.0)
    critical = float(threshold_profile.get("critical") or 0.0)
    for index, value in enumerate(values):
        state = None
        if critical and value >= critical:
            state = "critical"
        elif warning and value >= warning:
            state = "warning"
        if state:
            markers.append({"index": index, "value": value, "state": state})

    current_avg = round(sum(values) / len(values), 1) if values else None
    previous_avg = round(sum(previous_values) / len(previous_values), 1) if previous_values else None
    delta = None
    if current_avg is not None and previous_avg is not None:
        delta = round(current_avg - previous_avg, 1)

    return {
        "labels": labels,
        "values": values,
        "peak": peak_values,
        "warning": warning,
        "critical": critical,
        "bands": build_chart_threshold_bands(metric_key),
        "markers": markers,
        "current_avg": current_avg,
        "previous_avg": previous_avg,
        "delta": delta,
    }


def _build_network_series(rows, current_window_start: datetime, attr: str) -> dict:
    """Build a simple time-series payload for network in/out bps."""
    labels = []
    values = []
    window_start_str = current_window_start.strftime("%Y-%m-%dT%H:00:00")
    for row in rows:
        label = _hour_label(row[0])
        if label >= window_start_str:
            labels.append(label)
            raw = _safe_float(getattr(row, attr, None))
            values.append(round(raw, 2) if raw is not None else 0.0)
    return {"labels": labels, "values": values}


def _build_latency_series(rows) -> dict:
    """Build a simple time-series payload for ICMP latency (ms)."""
    labels = []
    values = []
    for row in rows:
        label = _hour_label(row[0])
        raw = _safe_float(getattr(row, "avg_latency", None))
        labels.append(label)
        values.append(round(raw, 2) if raw is not None else 0.0)
    return {"labels": labels, "values": values}


def _build_issue_from_evaluation(device, log, evaluation, matching_event: DashboardEvent | None):
    metric_key = evaluation.metric_key
    metric_threshold = evaluation.threshold
    threshold_value = _metric_threshold_value(metric_threshold, evaluation.state)
    unit = metric_threshold.get("unit") or "%"
    value = _safe_float(evaluation.value)
    breach_score = _metric_breach_score(value, metric_threshold, evaluation.state)
    event_message = str(getattr(matching_event, "message", "") or "").strip()
    message = event_message or (
        f"{METRIC_PRESSURE_LABEL.get(metric_key, metric_display_name(metric_key))} "
        f"{_format_metric_value(value, unit)} ({_severity_label(evaluation.state)})"
    )
    event_time = getattr(matching_event, "timestamp", None) or getattr(log, "timestamp", None) or _utcnow_naive()

    return {
        "id": f"{device.device_id}:{metric_key}",
        "device_id": device.device_id,
        "device_name": device.device_name,
        "hostname": device.hostname or device.device_name or device.device_ip,
        "ip": device.device_ip,
        "severity": str(evaluation.state or "").upper(),
        "severity_label": _severity_label(evaluation.state),
        "severity_rank": _severity_rank(evaluation.state),
        "metric_key": metric_key,
        "metric_name": alert_metric_name(metric_key),
        "metric_label": METRIC_PRESSURE_LABEL.get(metric_key, metric_display_name(metric_key)),
        "value": value,
        "formatted_value": _format_metric_value(value, unit),
        "threshold_warning": float(metric_threshold.get("warning") or 0.0),
        "threshold_critical": float(metric_threshold.get("critical") or 0.0),
        "threshold_trigger": threshold_value,
        "message": message,
        "source": "persisted" if matching_event else "live_breach",
        "event_id": getattr(matching_event, "event_id", None),
        "is_acknowledged": bool(getattr(matching_event, "is_acknowledged", False)),
        "timestamp": _iso_utc(event_time),
        "raw_timestamp": event_time,
        "breach_score": round(breach_score, 2),
    }


def _build_offline_issue(device, log, matching_event: DashboardEvent | None):
    event_message = str(getattr(matching_event, "message", "") or "").strip()
    event_time = getattr(matching_event, "timestamp", None) or getattr(log, "timestamp", None) or _utcnow_naive()
    age_seconds = None
    if getattr(log, "timestamp", None):
        age_seconds = max(0, int((_utcnow_naive() - log.timestamp).total_seconds()))
    return {
        "id": f"{device.device_id}:status",
        "device_id": device.device_id,
        "device_name": device.device_name,
        "hostname": device.hostname or device.device_name or device.device_ip,
        "ip": device.device_ip,
        "severity": "CRITICAL",
        "severity_label": "Critical",
        "severity_rank": 3,
        "metric_key": "status",
        "metric_name": "status",
        "metric_label": "Server Offline",
        "value": None,
        "formatted_value": "Offline",
        "threshold_warning": None,
        "threshold_critical": None,
        "threshold_trigger": None,
        "message": event_message or f"Server {device.device_name or device.device_ip} is offline or telemetry is stale.",
        "source": "persisted" if matching_event else "live_breach",
        "event_id": getattr(matching_event, "event_id", None),
        "is_acknowledged": bool(getattr(matching_event, "is_acknowledged", False)),
        "timestamp": _iso_utc(event_time),
        "raw_timestamp": event_time,
        "breach_score": float(age_seconds or 0) + 100.0,
    }


def _issue_sort_key(issue: dict):
    return (
        issue.get("severity_rank", 0),
        issue.get("breach_score", 0.0),
        issue.get("raw_timestamp") or datetime.min,
    )


def _row_sort_key(row: dict):
    primary_issue = row.get("primary_issue") or {}
    return (
        primary_issue.get("severity_rank", 0),
        primary_issue.get("breach_score", 0.0),
        row.get("last_seen") or "",
        row.get("hostname") or row.get("device_name") or row.get("ip") or "",
    )


def _build_synthetic_alert(issue: dict, row: dict) -> dict:
    return {
        "id": f"synthetic:{issue['device_id']}:{issue['metric_name']}",
        "device_id": issue["device_id"],
        "device_ip": issue["ip"],
        "original_device_ip": issue["ip"],
        "device_name": issue["device_name"],
        "device_type": "server",
        "scope": "Server",
        "event_type": "server_health",
        "severity": issue["severity"],
        "message": issue["message"],
        "timestamp": issue["timestamp"],
        "resolved": False,
        "is_acknowledged": False,
        "acknowledged_by": None,
        "acknowledged_at": None,
        "metric_name": issue["metric_name"],
        "synthetic": True,
        "health": row.get("health"),
    }


def build_server_incident_snapshot(scoped_servers, range_hours: int = 24) -> dict:
    thresholds = get_merged_thresholds()
    now = _utcnow_naive()
    server_ids = [int(device.device_id) for device in scoped_servers if getattr(device, "device_id", None) is not None]
    device_ips = [device.device_ip for device in scoped_servers if getattr(device, "device_ip", None)]

    latest_logs = query_latest_server_health_logs(device_ids=server_ids, source="agent")
    log_map = {int(log.device_id): log for log in latest_logs if getattr(log, "device_id", None) is not None}

    # Only monitor servers where the agent is installed — skip devices with no agent telemetry
    scoped_servers = [s for s in scoped_servers if int(s.device_id) in log_map]
    server_ids = [int(s.device_id) for s in scoped_servers]

    latest_scan_map = _query_latest_scan_map(device_ips)

    active_events = (
        DashboardEvent.query.filter(
            DashboardEvent.device_id.in_(server_ids) if server_ids else False,
            DashboardEvent.resolved.is_(False),
        ).all()
        if server_ids
        else []
    )
    events_by_device = defaultdict(list)
    for event in active_events:
        if event.device_id is not None:
            events_by_device[int(event.device_id)].append(event)

    metric_values = {metric_key: [] for metric_key in PRIMARY_HEALTH_METRICS}
    metric_impacts = {
        metric_key: {"warning": 0, "critical": 0}
        for metric_key in PRIMARY_HEALTH_METRICS
    }
    counts = {"total": len(scoped_servers), "healthy": 0, "warning": 0, "critical": 0, "offline": 0}
    rows = []
    active_issues = []
    synthetic_alerts = []

    for device in scoped_servers:
        device_id = int(device.device_id)
        log = log_map.get(device_id)
        scan = latest_scan_map.get(device.device_ip)
        health = compute_server_health(log)
        counts[str(health).lower()] = counts.get(str(health).lower(), 0) + 1

        evaluations = evaluate_metrics_for_log(log, thresholds) if log is not None and health != "Offline" else {}
        device_events = events_by_device.get(device_id, [])
        device_issues = []

        for metric_key in PRIMARY_HEALTH_METRICS:
            value = _safe_float(getattr(log, METRIC_VALUE_ATTR[metric_key], None) if log else None)
            if value is not None and health != "Offline":
                metric_values[metric_key].append(value)
            evaluation = evaluations.get(metric_key)
            if not evaluation or evaluation.state not in {"warning", "critical"}:
                continue
            metric_impacts[metric_key][evaluation.state] += 1
            matching_event = next((event for event in device_events if _matches_metric_event(event, metric_key)), None)
            issue = _build_issue_from_evaluation(device, log, evaluation, matching_event)
            device_issues.append(issue)

        if health == "Offline":
            status_event = next(
                (event for event in device_events if str(getattr(event, "metric_name", "") or "") == "status"),
                None,
            )
            device_issues.append(_build_offline_issue(device, log, status_event))

        device_issues.sort(key=_issue_sort_key, reverse=True)
        primary_issue = device_issues[0] if device_issues else None

        row = {
            "device_id": device_id,
            "device_name": device.device_name,
            "hostname": device.hostname,
            "ip": device.device_ip,
            "health": health,
            "last_seen": _iso_utc(getattr(log, "timestamp", None)),
            "cpu_usage": _safe_float(getattr(log, "cpu_usage", None) if log else None),
            "memory_usage": _safe_float(getattr(log, "memory_usage", None) if log else None),
            "disk_usage": _safe_float(getattr(log, "disk_usage", None) if log else None),
            "os": getattr(log, "os_name", None) if log else None,
            "uptime": getattr(log, "uptime", None) if log else None,
            "latency": _safe_float(getattr(scan, "ping_time_ms", None) if scan else None),
            "packet_loss": _safe_float(getattr(scan, "packet_loss", None) if scan else None),
            "jitter": _safe_float(getattr(scan, "jitter", None) if scan else None),
            "primary_issue": primary_issue,
            "issue_count": len(device_issues),
            "row_tone": (
                "critical"
                if health in {"Critical", "Offline"}
                else ("warning" if health == "Warning" else "healthy")
            ),
        }
        rows.append(row)

        if primary_issue:
            active_issues.append(
                {
                    **primary_issue,
                    "metrics": {
                        "cpu": row["cpu_usage"],
                        "memory": row["memory_usage"],
                        "disk": row["disk_usage"],
                    },
                    "health": health,
                }
            )
            if primary_issue.get("source") == "live_breach":
                synthetic_alerts.append(_build_synthetic_alert(primary_issue, row))

    rows.sort(key=_row_sort_key, reverse=True)
    active_issues.sort(key=_issue_sort_key, reverse=True)
    dominant_issue = active_issues[0] if active_issues else None

    # Uptime always computed over last 24h regardless of chart range
    start_48h = now - timedelta(hours=48)
    current_24h_start = now - timedelta(hours=24)

    # Trend range routing
    range_label_map = {1: "1h", 6: "6h", 24: "24h", 168: "7d", 720: "30d"}
    range_label = range_label_map.get(range_hours, f"{range_hours}h")

    if range_hours <= 24:
        trend_start = now - timedelta(hours=48)
        trend_window_start = current_24h_start
        trend_rows = _query_metric_trends(server_ids, trend_start)
        latency_rows = _query_latency_trend_raw(server_ids, current_24h_start)
    elif range_hours <= 168:
        trend_start = now - timedelta(hours=range_hours)
        trend_window_start = trend_start
        trend_rows = _query_metric_trends_hourly(server_ids, trend_start)
        latency_rows = _query_latency_trend_hourly(server_ids, trend_start)
    else:
        trend_start = now - timedelta(hours=range_hours)
        trend_window_start = trend_start
        trend_rows = _query_metric_trends_daily(server_ids, trend_start)
        latency_rows = _query_latency_trend_daily(server_ids, trend_start)

    trend_payload = {
        "cpu": _build_series_payload("cpu_usage_pct", trend_rows, trend_window_start),
        "memory": _build_series_payload("memory_usage_pct", trend_rows, trend_window_start),
        "disk": _build_series_payload("disk_usage_pct", trend_rows, trend_window_start),
        "network_in": _build_network_series(trend_rows, trend_window_start, "avg_net_in"),
        "network_out": _build_network_series(trend_rows, trend_window_start, "avg_net_out"),
        "latency": _build_latency_series(latency_rows),
    }

    hourly_pairs = _query_hourly_coverage(server_ids, start_48h)
    uptime_current = _compute_window_uptime_pct(hourly_pairs, current_24h_start, now, len(scoped_servers))
    uptime_previous = (
        _compute_window_uptime_pct(hourly_pairs, start_48h, current_24h_start, len(scoped_servers))
        if scoped_servers
        else 0.0
    )
    uptime_delta = round(uptime_current - uptime_previous, 1)

    problem_count = sum(1 for row in rows if row.get("primary_issue"))
    total_servers = len(scoped_servers)
    healthy_count = int(counts.get("healthy", 0))
    impact_pct = round((problem_count / total_servers) * 100.0, 1) if total_servers else 0.0

    metric_cards = {}
    for metric_key in PRIMARY_HEALTH_METRICS:
        short_key = METRIC_SHORT_KEY[metric_key]
        threshold_profile = thresholds.get("metrics", {}).get(metric_key, {})
        impact_counts = metric_impacts.get(metric_key, {})
        impacted_total = int(impact_counts.get("warning", 0) + impact_counts.get("critical", 0))
        severity_state = "healthy"
        if impact_counts.get("critical", 0):
            severity_state = "critical"
        elif impact_counts.get("warning", 0):
            severity_state = "warning"
        else:
            current_avg = trend_payload[short_key].get("current_avg")
            if current_avg is not None:
                if current_avg >= float(threshold_profile.get("critical") or 0.0):
                    severity_state = "critical"
                elif current_avg >= float(threshold_profile.get("warning") or 0.0):
                    severity_state = "warning"
        metric_cards[short_key] = {
            "metric_key": metric_key,
            "label": METRIC_PRESSURE_LABEL.get(metric_key, metric_display_name(metric_key)),
            "value": round(sum(metric_values[metric_key]) / len(metric_values[metric_key]), 1) if metric_values[metric_key] else None,
            "severity": severity_state,
            "severity_label": _severity_label(severity_state),
            "delta_24h": trend_payload[short_key].get("delta"),
            "impacted_servers": impacted_total,
            "warning": float(threshold_profile.get("warning") or 0.0),
            "critical": float(threshold_profile.get("critical") or 0.0),
            "unit": threshold_profile.get("unit") or "%",
        }

    unaffected_domains = [
        METRIC_PRESSURE_LABEL[metric_key].replace(" Pressure", "")
        for metric_key in PRIMARY_METRIC_ORDER
        if metric_impacts[metric_key]["warning"] + metric_impacts[metric_key]["critical"] == 0
    ]

    return {
        "timestamp": _iso_utc(now),
        "health": counts.copy(),
        "counts": counts.copy(),
        "aggregates": {
            "cpu": round(sum(metric_values["cpu_usage_pct"]) / len(metric_values["cpu_usage_pct"]), 1) if metric_values["cpu_usage_pct"] else 0.0,
            "memory": round(sum(metric_values["memory_usage_pct"]) / len(metric_values["memory_usage_pct"]), 1) if metric_values["memory_usage_pct"] else 0.0,
            "disk": round(sum(metric_values["disk_usage_pct"]) / len(metric_values["disk_usage_pct"]), 1) if metric_values["disk_usage_pct"] else 0.0,
        },
        "p95": {
            "cpu": round(_calc_p95(metric_values["cpu_usage_pct"]), 1),
            "memory": round(_calc_p95(metric_values["memory_usage_pct"]), 1),
            "disk": round(_calc_p95(metric_values["disk_usage_pct"]), 1),
        },
        "servers": rows,
        "active_issues": active_issues,
        "alerts": active_issues,
        "dominant_issue": dominant_issue,
        "impact_summary": {
            "affected_servers": problem_count,
            "healthy_servers": healthy_count,
            "total_servers": total_servers,
            "fleet_pct": impact_pct,
            "primary_issue_label": dominant_issue.get("metric_label") if dominant_issue else "No active server issues",
            "primary_issue_severity": dominant_issue.get("severity_label") if dominant_issue else "Healthy",
            "unaffected_domains": unaffected_domains,
        },
        "filters": {
            "all": total_servers,
            "problem": problem_count,
            "healthy": healthy_count,
            "critical": counts.get("critical", 0) + counts.get("offline", 0),
            "warning": counts.get("warning", 0),
        },
        "metric_cards": metric_cards,
        "trends": {
            "labels": trend_payload["cpu"]["labels"],
            "range_hours": range_hours,
            "range_label": range_label,
            "cpu": trend_payload["cpu"],
            "memory": trend_payload["memory"],
            "disk": trend_payload["disk"],
            "network_in": trend_payload["network_in"],
            "network_out": trend_payload["network_out"],
            "latency": trend_payload["latency"],
        },
        "uptime": {
            "current_24h_pct": uptime_current,
            "previous_24h_pct": uptime_previous,
            "delta_pct": uptime_delta,
        },
        "synthetic_alerts": synthetic_alerts,
        "thresholds": {
            short_key: {
                "warning": metric_cards[short_key]["warning"],
                "critical": metric_cards[short_key]["critical"],
                "unit": metric_cards[short_key]["unit"],
            }
            for short_key in ("cpu", "memory", "disk")
        },
    }
