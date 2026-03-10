import socket
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import func

from extensions import db
from middleware.rbac import require_login, require_role, scoped_query
from models.audit_log import AuditLog
from models.device import Device
from models.server_health import ServerHealthLog
from services.dashboard_cache_service import invalidate_dashboard_threshold_views
from services.server_thresholds import (
    METRIC_CATALOG,
    PRIMARY_HEALTH_METRICS,
    ThresholdValidationError,
    build_chart_threshold_bands,
    extract_latest_metrics,
    evaluate_metrics_for_log,
    get_merged_thresholds,
    save_threshold_config,
    serialize_threshold_profile,
    summarize_health,
)
from utils.server_health import compute_server_health, is_server_device

server_metrics_bp = Blueprint("server_metrics_bp", __name__)
_REVERSE_DNS_CACHE: dict[str, dict[str, object]] = {}
_REVERSE_DNS_CACHE_TTL_SECONDS = 900


@server_metrics_bp.before_request
@require_login
def _server_metrics_auth_guard():
    return None


def _avg(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _downsample_logs(logs, max_points):
    if len(logs) <= max_points:
        return logs
    step = max(1, int((len(logs) + max_points - 1) / max_points))
    buckets = []
    for i in range(0, len(logs), step):
        buckets.append(logs[i : i + step])
    return buckets


def _iso_utc(ts):
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat().replace("+00:00", "Z")


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_float(value):
    try:
        numeric = float(value)
        return numeric if numeric == numeric else None
    except (TypeError, ValueError):
        return None


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _detect_os_family(device, last_log):
    candidates = [
        getattr(last_log, "os_name", None),
        getattr(device, "agent_os_type", None),
    ]
    normalized = "unknown"
    for candidate in candidates:
        text = str(candidate or "").strip().lower()
        if not text:
            continue
        if "win" in text:
            normalized = "windows"
            break
        if any(token in text for token in ("linux", "ubuntu", "debian", "centos", "rhel", "rocky", "alma")):
            normalized = "linux"
            break
    return normalized


def _parse_uptime_seconds(raw_value):
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        numeric = int(float(text))
        return max(numeric, 0)
    except (TypeError, ValueError):
        return None


def _summarize_numeric_series(values):
    points = [float(value) for value in values if value is not None]
    if not points:
        return {"current": None, "average": None, "peak": None}
    return {
        "current": points[-1],
        "average": sum(points) / len(points),
        "peak": max(points),
    }


def _normalize_process_row(row):
    if not isinstance(row, dict):
        return None
    pid = _safe_int(row.get("pid"))
    name = str(row.get("name") or row.get("process_name") or "").strip()
    if not name and pid is None:
        return None
    executable_path = (
        row.get("path")
        or row.get("exe")
        or row.get("executable")
        or row.get("executable_path")
        or row.get("cmdline")
    )
    return {
        "name": name or (f"PID {pid}" if pid is not None else "Unknown"),
        "pid": pid,
        "cpu_percent": _safe_float(row.get("cpu_percent")),
        "memory_percent": _safe_float(row.get("memory_percent")),
        "status": str(row.get("status") or "").strip() or None,
        "path": str(executable_path or "").strip() or None,
    }


def _merge_process_rows(memory_rows, cpu_rows):
    merged = {}
    ordered = []
    for source_rows in (memory_rows or [], cpu_rows or []):
        for source_row in source_rows:
            normalized = _normalize_process_row(source_row)
            if normalized is None:
                continue
            key = normalized["pid"] if normalized["pid"] is not None else normalized["name"].lower()
            if key not in merged:
                merged[key] = normalized
                ordered.append(key)
                continue
            existing = merged[key]
            for field_name, field_value in normalized.items():
                if existing.get(field_name) in (None, "", 0) and field_value not in (None, ""):
                    existing[field_name] = field_value
    return [merged[key] for key in ordered]


def _compute_disk_io_rates(logs):
    if not logs or len(logs) < 2:
        return {
            "current_read_mb_s": None,
            "current_write_mb_s": None,
            "current_iops": None,
            "average_read_mb_s": None,
            "average_write_mb_s": None,
            "peak_read_mb_s": None,
            "peak_write_mb_s": None,
            "peak_iops": None,
            "queue_length": None,
            "busy_percent": None,
        }

    read_rates = []
    write_rates = []
    iops_rates = []
    for previous, current in zip(logs[:-1], logs[1:]):
        if not previous.timestamp or not current.timestamp:
            continue
        delta_seconds = max((current.timestamp - previous.timestamp).total_seconds(), 0)
        if delta_seconds <= 0:
            continue

        prev_read_bytes = _safe_float(previous.disk_read_bytes)
        curr_read_bytes = _safe_float(current.disk_read_bytes)
        prev_write_bytes = _safe_float(previous.disk_write_bytes)
        curr_write_bytes = _safe_float(current.disk_write_bytes)
        prev_read_count = _safe_float(previous.disk_read_count)
        curr_read_count = _safe_float(current.disk_read_count)
        prev_write_count = _safe_float(previous.disk_write_count)
        curr_write_count = _safe_float(current.disk_write_count)

        if prev_read_bytes is not None and curr_read_bytes is not None and curr_read_bytes >= prev_read_bytes:
            read_rates.append((curr_read_bytes - prev_read_bytes) / delta_seconds / (1024.0 * 1024.0))
        if prev_write_bytes is not None and curr_write_bytes is not None and curr_write_bytes >= prev_write_bytes:
            write_rates.append((curr_write_bytes - prev_write_bytes) / delta_seconds / (1024.0 * 1024.0))

        total_iops = 0.0
        has_iops = False
        if prev_read_count is not None and curr_read_count is not None and curr_read_count >= prev_read_count:
            total_iops += (curr_read_count - prev_read_count) / delta_seconds
            has_iops = True
        if prev_write_count is not None and curr_write_count is not None and curr_write_count >= prev_write_count:
            total_iops += (curr_write_count - prev_write_count) / delta_seconds
            has_iops = True
        if has_iops:
            iops_rates.append(total_iops)

    def _series_summary(series):
        if not series:
            return (None, None, None)
        return (series[-1], sum(series) / len(series), max(series))

    current_read, average_read, peak_read = _series_summary(read_rates)
    current_write, average_write, peak_write = _series_summary(write_rates)
    current_iops, _average_iops, peak_iops = _series_summary(iops_rates)
    last_log = logs[-1]
    return {
        "current_read_mb_s": current_read,
        "current_write_mb_s": current_write,
        "current_iops": current_iops,
        "average_read_mb_s": average_read,
        "average_write_mb_s": average_write,
        "peak_read_mb_s": peak_read,
        "peak_write_mb_s": peak_write,
        "peak_iops": peak_iops,
        "queue_length": None,
        "busy_percent": _safe_float(getattr(last_log, "disk_busy_percent", None)),
    }


def _resolve_reverse_dns(ip_address):
    if not ip_address:
        return None
    cached = _REVERSE_DNS_CACHE.get(ip_address)
    now = datetime.now(timezone.utc).timestamp()
    if cached and now < float(cached.get("expires_at", 0)):
        return cached.get("hostname")
    try:
        hostname = socket.gethostbyaddr(ip_address)[0]
    except Exception:
        hostname = None
    _REVERSE_DNS_CACHE[ip_address] = {
        "hostname": hostname,
        "expires_at": now + _REVERSE_DNS_CACHE_TTL_SECONDS,
    }
    return hostname


def _build_connection_snapshot(last_log):
    raw_rows = last_log.network_top_remote_ips if last_log else []
    if not isinstance(raw_rows, list):
        raw_rows = []

    normalized_rows = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        remote_ip = str(item.get("ip") or item.get("remote_ip") or "").strip()
        if not remote_ip:
            continue
        connection_count = _safe_int(item.get("count") or item.get("connection_count"), default=0) or 0
        normalized_rows.append(
            {
                "remote_ip": remote_ip,
                "connection_count": max(connection_count, 0),
                "agent_hostname": str(
                    item.get("hostname")
                    or item.get("remote_hostname")
                    or item.get("dns_name")
                    or ""
                ).strip()
                or None,
                "connection_type": str(
                    item.get("connection_type")
                    or item.get("protocol")
                    or item.get("state")
                    or "ESTABLISHED"
                ).strip()
                or "ESTABLISHED",
            }
        )
    normalized_rows.sort(key=lambda row: row["connection_count"], reverse=True)
    normalized_rows = normalized_rows[:20]

    known_map = (
        {
            device.device_ip: device
            for device in scoped_query(Device)
            .filter(Device.device_ip.in_([row["remote_ip"] for row in normalized_rows if row.get("remote_ip")]))
            .all()
        }
        if normalized_rows
        else {}
    )

    resolved_rows = []
    for row in normalized_rows:
        device_match = known_map.get(row["remote_ip"])
        inventory_hostname = str(getattr(device_match, "hostname", "") or "").strip() or None
        resolved_hostname = inventory_hostname or row.get("agent_hostname") or _resolve_reverse_dns(row["remote_ip"]) or row["remote_ip"]
        resolution_source = (
            "inventory"
            if device_match
            else ("agent" if row.get("agent_hostname") else ("reverse_dns" if resolved_hostname and resolved_hostname != row["remote_ip"] else "ip"))
        )
        resolved_rows.append(
            {
                "remote_ip": row["remote_ip"],
                "connection_count": row["connection_count"],
                "remote_hostname": resolved_hostname,
                "resolved_label": getattr(device_match, "device_name", None) or resolved_hostname or row["remote_ip"],
                "connection_type": row.get("connection_type") or "ESTABLISHED",
                "remote_device_id": getattr(device_match, "device_id", None),
                "remote_device_name": getattr(device_match, "device_name", None),
                "remote_device_type": getattr(device_match, "device_type", None),
                "resolution_source": resolution_source,
            }
        )

    snapshot_timestamp = getattr(last_log, "timestamp", None) if last_log else None
    unique_ips = _safe_int(getattr(last_log, "network_connections_unique_ips", None), default=len(resolved_rows))
    total_connections = _safe_int(getattr(last_log, "network_connections_established", None), default=sum(row["connection_count"] for row in resolved_rows))
    snapshot_age_seconds = None
    if snapshot_timestamp is not None:
        snapshot_age_seconds = max(0, int((_utcnow_naive() - snapshot_timestamp).total_seconds()))

    return {
        "rows": resolved_rows,
        "meta": {
            "timestamp": _iso_utc(snapshot_timestamp),
            "unique_remote_ips_count": unique_ips,
            "total_connections": total_connections,
            "snapshot_age_seconds": snapshot_age_seconds,
            "top_limit": 20,
        },
    }


def _build_server_telemetry_payload(device, time_range):
    cutoff = _time_range_cutoff(time_range)
    max_points = _max_points_for_range(time_range)
    logs_q = (
        ServerHealthLog.query.filter(
            ServerHealthLog.device_id == device.device_id,
            ServerHealthLog.timestamp >= cutoff,
            ServerHealthLog.source == "agent",
        )
        .order_by(ServerHealthLog.timestamp.desc())
    )

    logs = logs_q.limit(max_points * 4).all()
    logs.reverse()
    last_log = logs[-1] if logs else None
    os_family = _detect_os_family(device, last_log)

    hardware_specs = device.hardware_specs if isinstance(device.hardware_specs, dict) else {}
    if not hardware_specs and last_log:
        hardware_specs = {
            "memory_total_gb": last_log.memory_total_gb,
            "disk_total_gb": last_log.disk_total_gb,
            "architecture": last_log.os_arch,
        }

    labels = []
    cpu_data = []
    mem_data = []
    disk_data = []
    net_in_data = []
    net_out_data = []
    buckets = _downsample_logs(logs, max_points)
    for bucket in buckets:
        if isinstance(bucket, list):
            labels.append(_iso_utc(bucket[-1].timestamp))
            cpu_data.append(_avg([b.cpu_usage for b in bucket]))
            mem_data.append(_avg([b.memory_usage for b in bucket]))
            disk_data.append(_avg([b.disk_usage for b in bucket]))
            net_in_data.append(_avg([b.network_in_bps for b in bucket]))
            net_out_data.append(_avg([b.network_out_bps for b in bucket]))
        else:
            labels.append(_iso_utc(bucket.timestamp))
            cpu_data.append(bucket.cpu_usage)
            mem_data.append(bucket.memory_usage)
            disk_data.append(bucket.disk_usage)
            net_in_data.append(bucket.network_in_bps)
            net_out_data.append(bucket.network_out_bps)

    profile, threshold_payload = _merged_threshold_payload()
    latest_metrics = extract_latest_metrics(last_log)
    health_summary = summarize_health(last_log, {"metrics": threshold_payload.get("metrics", {})}) if last_log else None
    health_state = compute_server_health(last_log)
    connection_snapshot = _build_connection_snapshot(last_log)
    merged_processes = _merge_process_rows(
        last_log.top_processes if last_log and isinstance(last_log.top_processes, list) else [],
        last_log.top_processes_cpu if last_log and isinstance(last_log.top_processes_cpu, list) else [],
    )
    uptime_seconds = _parse_uptime_seconds(last_log.uptime if last_log else None)
    boot_time = (
        _iso_utc(last_log.timestamp - timedelta(seconds=uptime_seconds))
        if last_log and last_log.timestamp and uptime_seconds is not None
        else None
    )
    net_in_mb = [value / (1024.0 * 1024.0) if value is not None else None for value in net_in_data]
    net_out_mb = [value / (1024.0 * 1024.0) if value is not None else None for value in net_out_data]
    network_summary = {
        "inbound_mb_s": _summarize_numeric_series(net_in_mb),
        "outbound_mb_s": _summarize_numeric_series(net_out_mb),
    }

    evaluation_payload = {}
    if health_summary:
        for metric_key, evaluation in health_summary.evaluations.items():
            evaluation_payload[metric_key] = {
                "state": evaluation.state,
                "value": evaluation.value,
                "label": evaluation.threshold.get("label") or METRIC_CATALOG.get(metric_key, {}).get("label") or metric_key,
                "unit": evaluation.threshold.get("unit") or "",
                "warning": evaluation.threshold.get("warning"),
                "critical": evaluation.threshold.get("critical"),
            }

    return {
        "time_range": time_range,
        "telemetry_refreshed_at": _iso_utc(_utcnow_naive()),
        "labels": labels,
        "cpu": cpu_data,
        "memory": mem_data,
        "disk": disk_data,
        "net_in": net_in_data,
        "net_out": net_out_data,
        "device_name": device.device_name,
        "ip": device.device_ip,
        "hostname": device.hostname,
        "uptime": last_log.uptime if last_log else "N/A",
        "uptime_seconds": uptime_seconds,
        "boot_time": boot_time,
        "last_seen": _iso_utc(last_log.timestamp) if last_log and last_log.timestamp else None,
        "display_timezone": "browser-local",
        "metric_catalog_version": profile["version"],
        "threshold_profile": {
            "scope": "global",
            "version": profile["version"],
            "updated_at": profile["updated_at"],
            "updated_by": profile["updated_by"],
            "change_reason": profile["change_reason"],
        },
        "thresholds": threshold_payload,
        "latest_metrics": latest_metrics,
        "health": health_state,
        "health_score": health_summary.score if health_summary else 0,
        "health_penalties": health_summary.penalties if health_summary else [],
        "health_evaluations": evaluation_payload,
        "os_family": os_family,
        "memory_paging_label": "Pagefile Usage" if os_family == "windows" else "Swap Usage",
        "os": {
            "name": last_log.os_name if last_log else None,
            "version": last_log.os_version if last_log else None,
            "arch": last_log.os_arch if last_log else None,
        },
        "hardware_specs": hardware_specs,
        "cpu_iowait_percent": last_log.cpu_iowait_percent if last_log else None,
        "cpu_steal_percent": last_log.cpu_steal_percent if last_log else None,
        "load_average": {
            "1min": last_log.load_avg_1min if last_log else None,
            "5min": last_log.load_avg_5min if last_log else None,
            "15min": last_log.load_avg_15min if last_log else None,
        },
        "queue_metrics": {
            "cpu_queue_length": None,
            "processor_queue_length": None,
        },
        "swap": {
            "total_mb": last_log.swap_total_mb if last_log else None,
            "used_mb": last_log.swap_used_mb if last_log else None,
            "percent": last_log.swap_percent if last_log else None,
        },
        "memory_detail": {
            "used_gb": last_log.memory_used_gb if last_log else None,
            "total_gb": last_log.memory_total_gb if last_log else None,
            "page_faults_per_sec": last_log.page_faults_per_sec if last_log else None,
        },
        "disk_detail": {
            "used_gb": last_log.disk_used_gb if last_log else None,
            "free_gb": last_log.disk_free_gb if last_log else None,
            "total_gb": last_log.disk_total_gb if last_log else None,
        },
        "disk_io": {
            "read_bytes": last_log.disk_read_bytes if last_log else None,
            "write_bytes": last_log.disk_write_bytes if last_log else None,
            "read_count": last_log.disk_read_count if last_log else None,
            "write_count": last_log.disk_write_count if last_log else None,
            "read_latency_ms": last_log.disk_read_latency_ms if last_log else None,
            "write_latency_ms": last_log.disk_write_latency_ms if last_log else None,
            "busy_percent": last_log.disk_busy_percent if last_log else None,
        },
        "disk_io_rates": _compute_disk_io_rates(logs),
        "network_summary": network_summary,
        "network_connections": {
            "total": last_log.network_connections_total if last_log else None,
            "established": last_log.network_connections_established if last_log else None,
            "tcp_retransmits_delta": last_log.tcp_retransmits_delta if last_log else None,
        },
        "network_per_interface": last_log.network_per_interface if last_log and last_log.network_per_interface else {},
        "network_top_remote_ips": connection_snapshot["rows"],
        "network_connections_unique_ips": connection_snapshot["meta"]["unique_remote_ips_count"],
        "connection_snapshot": connection_snapshot,
        "processes": {
            "total": last_log.process_count if last_log else None,
            "zombie": last_log.zombie_count if last_log else None,
            "context_switches_per_sec": last_log.context_switches_per_sec if last_log else None,
            "open_fds": last_log.open_fds if last_log else None,
            "fd_limit": last_log.fd_limit if last_log else None,
            "fd_percent": last_log.fd_percent if last_log else None,
        },
        "top_processes": last_log.top_processes if last_log and last_log.top_processes else [],
        "top_processes_cpu": last_log.top_processes_cpu if last_log and last_log.top_processes_cpu else [],
        "process_catalog": merged_processes,
        "alerts": last_log.alerts if last_log and last_log.alerts else [],
    }


def _sanitize_top_remote_ips(raw_rows):
    if not isinstance(raw_rows, list):
        return []

    cleaned = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        ip_value = str(row.get("ip") or "").strip()
        if not ip_value:
            continue
        try:
            count = int(row.get("count"))
        except (TypeError, ValueError):
            count = 0
        cleaned.append({"ip": ip_value, "count": max(count, 0)})

    cleaned.sort(key=lambda item: item["count"], reverse=True)
    return cleaned[:20]


def _resolve_top_remote_ips(rows):
    ip_values = [row["ip"] for row in rows if row.get("ip")]
    if not ip_values:
        return rows

    known_map = {
        d.device_ip: {"id": d.device_id, "name": d.device_name, "type": d.device_type}
        for d in Device.query.filter(Device.device_ip.in_(ip_values)).all()
    }

    for row in rows:
        match = known_map.get(row["ip"])
        if match:
            row["remote_device_id"] = match["id"]
            row["remote_device_name"] = match["name"]
            row["remote_device_type"] = match["type"]
        else:
            row["remote_device_id"] = None
            row["remote_device_name"] = "Unknown Device"
            row["remote_device_type"] = "unknown"
    return rows


def _scoped_server_devices():
    scoped_devices = scoped_query(Device).all()
    servers = [device for device in scoped_devices if is_server_device(getattr(device, "device_type", None))]
    server_ids = [device.device_id for device in servers]
    return servers, server_ids


def _merged_threshold_payload():
    profile = serialize_threshold_profile()
    metrics = {}
    for metric_key, config in profile["metrics"].items():
        metric_payload = dict(config)
        catalog = METRIC_CATALOG.get(metric_key, {})
        metric_payload["default_enabled"] = bool(catalog.get("default_enabled", False))
        metric_payload["default_warning"] = float(catalog.get("default_warning", config.get("warning", 0)))
        metric_payload["default_critical"] = float(catalog.get("default_critical", config.get("critical", 0)))
        metric_payload["bands"] = build_chart_threshold_bands(metric_key, {"metrics": profile["metrics"]})
        metrics[metric_key] = metric_payload
    return profile, {"metrics": metrics}


def _time_range_cutoff(time_range):
    if time_range == "15m":
        return _utcnow_naive() - timedelta(minutes=15)
    if time_range == "1h":
        return _utcnow_naive() - timedelta(hours=1)
    if time_range == "6h":
        return _utcnow_naive() - timedelta(hours=6)
    if time_range == "7d":
        return _utcnow_naive() - timedelta(days=7)
    return _utcnow_naive() - timedelta(hours=24)


def _max_points_for_range(time_range):
    if time_range in {"15m", "1h"}:
        return 120
    if time_range == "6h":
        return 240
    if time_range == "7d":
        return 336
    return 240


def _create_threshold_audit_log(previous_version, next_version, changed_metric_keys, change_reason):
    db.session.add(
        AuditLog(
            user_id=session.get("user_id"),
            username=session.get("username", "system"),
            user_role=session.get("role", "system"),
            action="update",
            entity_type="server_threshold_config",
            entity_id=1,
            entity_name="global_server_thresholds",
            description=f"Updated server threshold profile {previous_version} -> {next_version}",
            changes={
                "previous_version": int(previous_version),
                "new_version": int(next_version),
                "changed_metric_keys": list(changed_metric_keys or []),
                "change_reason": change_reason,
            },
            ip_address=request.remote_addr,
            user_agent=(request.headers.get("User-Agent") or "")[:200],
        )
    )


@server_metrics_bp.route("/api/server/fleet-metrics")
def get_fleet_metrics():
    try:
        scoped_servers, scoped_server_ids = _scoped_server_devices()
        if not scoped_server_ids:
            return jsonify(
                {
                    "health": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                    "aggregates": {"cpu": 0, "memory": 0, "disk": 0},
                    "p95": {"cpu": 0, "memory": 0},
                    "alerts": [],
                    "trends": {"cpu": [], "memory": [], "labels": []},
                }
            )

        server_map = {device.device_id: device for device in scoped_servers}
        thresholds = get_merged_thresholds()

        cutoff = _utcnow_naive() - timedelta(hours=24)
        latest_subq = (
            db.session.query(
                ServerHealthLog.device_id,
                func.max(ServerHealthLog.id).label("max_id"),
            )
            .filter(
                ServerHealthLog.source == "agent",
                ServerHealthLog.timestamp >= cutoff,
                ServerHealthLog.device_id.in_(scoped_server_ids),
            )
            .group_by(ServerHealthLog.device_id)
            .subquery()
        )

        latest_logs = db.session.query(ServerHealthLog).join(
            latest_subq, ServerHealthLog.id == latest_subq.c.max_id
        ).all()

        total_servers = len(latest_logs)
        if total_servers == 0:
            return jsonify(
                {
                    "health": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                    "aggregates": {"cpu": 0, "memory": 0, "disk": 0},
                    "p95": {"cpu": 0, "memory": 0},
                    "alerts": [],
                    "trends": {"cpu": [], "memory": [], "labels": []},
                }
            )

        health_counts = {"total": total_servers, "healthy": 0, "warning": 0, "critical": 0, "offline": 0}
        cpu_values = []
        mem_values = []
        disk_values = []
        critical_servers = []

        for log in latest_logs:
            health = compute_server_health(log)
            health_lower = health.lower()
            if health_lower in health_counts:
                health_counts[health_lower] += 1
            else:
                health_counts["offline"] += 1

            if log.cpu_usage is not None:
                cpu_values.append(log.cpu_usage)
            if log.memory_usage is not None:
                mem_values.append(log.memory_usage)
            if log.disk_usage is not None:
                disk_values.append(log.disk_usage)

            evaluations = evaluate_metrics_for_log(log, thresholds)
            alerts = []
            for metric_key in PRIMARY_HEALTH_METRICS:
                evaluation = evaluations.get(metric_key)
                if evaluation and evaluation.state in {"warning", "critical"}:
                    alerts.append(f"{metric_key.replace('_pct', '').replace('_', ' ').title()} {evaluation.value:.1f}{evaluation.threshold.get('unit') or ''}")

            if alerts:
                device = server_map.get(log.device_id)
                critical_servers.append(
                    {
                        "name": device.device_name if device else f"ID {log.device_id}",
                        "alerts": alerts,
                    }
                )

        def calc_p95(values):
            if not values:
                return 0
            values.sort()
            idx = int(len(values) * 0.95)
            return values[min(idx, len(values) - 1)]

        backend = db.engine.url.get_backend_name()
        if backend == "sqlite":
            hour_bucket = func.strftime("%Y-%m-%dT%H:00:00", ServerHealthLog.timestamp).label("hour")
        else:
            hour_bucket = func.date_trunc("hour", ServerHealthLog.timestamp).label("hour")
        trend_query = (
            db.session.query(
                hour_bucket,
                func.avg(ServerHealthLog.cpu_usage).label("avg_cpu"),
                func.avg(ServerHealthLog.memory_usage).label("avg_mem"),
            )
            .filter(
                ServerHealthLog.source == "agent",
                ServerHealthLog.timestamp >= cutoff,
                ServerHealthLog.device_id.in_(scoped_server_ids),
            )
            .group_by(hour_bucket)
            .order_by(hour_bucket)
            .all()
        )

        return jsonify(
            {
                "health": health_counts,
                "aggregates": {
                    "cpu": round(sum(cpu_values) / len(cpu_values), 1) if cpu_values else 0,
                    "memory": round(sum(mem_values) / len(mem_values), 1) if mem_values else 0,
                    "disk": round(sum(disk_values) / len(disk_values), 1) if disk_values else 0,
                },
                "p95": {
                    "cpu": round(calc_p95(cpu_values), 1),
                    "memory": round(calc_p95(mem_values), 1),
                },
                "alerts": critical_servers,
                "trends": {
                    "labels": [row.hour if isinstance(row.hour, str) else _iso_utc(row.hour) for row in trend_query],
                    "cpu": [float(row.avg_cpu) if row.avg_cpu else 0 for row in trend_query],
                    "memory": [float(row.avg_mem) if row.avg_mem else 0 for row in trend_query],
                },
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@server_metrics_bp.route("/api/server/health")
def get_server_health_summary():
    """Get server health summary with Redis caching for performance."""
    from extensions import redis_client, is_redis_available
    import json
    
    # Try Redis cache first (30 second TTL)
    cache_key = "server:health:summary"
    if is_redis_available():
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return jsonify(json.loads(cached))
        except Exception as e:
            logger.warning(f"[ServerHealth] Redis cache read failed: {e}")
    
    try:
        _, scoped_server_ids = _scoped_server_devices()
        if not scoped_server_ids:
            empty_response = {
                "timestamp": _iso_utc(_utcnow_naive()),
                "counts": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                "servers": [],
            }
            return jsonify(empty_response)

        latest_subq = (
            db.session.query(
                ServerHealthLog.device_id,
                func.max(ServerHealthLog.id).label("max_id"),
            )
            .filter(
                ServerHealthLog.source == "agent",
                ServerHealthLog.device_id.in_(scoped_server_ids),
            )
            .group_by(ServerHealthLog.device_id)
            .subquery()
        )

        latest_logs = db.session.query(ServerHealthLog).join(
            latest_subq, ServerHealthLog.id == latest_subq.c.max_id
        ).all()

        health_map = {log.device_id: log for log in latest_logs}
        agent_device_ids = list(health_map.keys())
        if not agent_device_ids:
            empty_response = {
                "timestamp": _iso_utc(_utcnow_naive()),
                "counts": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                "servers": [],
            }
            return jsonify(empty_response)

        servers = Device.query.filter(Device.device_id.in_(agent_device_ids)).all()
        
        from models.scan_history import DeviceScanHistory
        
        # Fetch latest scan records for these agents
        latest_scans_subq = (
            db.session.query(
                DeviceScanHistory.device_ip,
                func.max(DeviceScanHistory.scan_id).label("max_scan_id"),
            )
            .filter(DeviceScanHistory.device_ip.in_([d.device_ip for d in servers if d.device_ip]))
            .group_by(DeviceScanHistory.device_ip)
            .subquery()
        )
        
        latest_scans = db.session.query(DeviceScanHistory).join(
            latest_scans_subq, DeviceScanHistory.scan_id == latest_scans_subq.c.max_scan_id
        ).all()
        
        scan_map = {scan.device_ip: scan for scan in latest_scans}

        counts = {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0}
        server_list = []
        for device in servers:
            counts["total"] += 1
            log = health_map.get(device.device_id)
            health = compute_server_health(log)
            counts[health.lower()] = counts.get(health.lower(), 0) + 1
            
            scan = scan_map.get(device.device_ip)
            
            server_list.append(
                {
                    "device_id": device.device_id,
                    "device_name": device.device_name,
                    "hostname": device.hostname,
                    "ip": device.device_ip,
                    "health": health,
                    "last_seen": _iso_utc(log.timestamp) if log and log.timestamp else None,
                    "cpu_usage": log.cpu_usage if log else None,
                    "memory_usage": log.memory_usage if log else None,
                    "disk_usage": log.disk_usage if log else None,
                    "os": log.os_name if log else None,
                    "uptime": log.uptime if log else None,
                    "latency": scan.ping_time_ms if scan else None,
                    "packet_loss": scan.packet_loss if scan else None,
                    "jitter": scan.jitter if scan else None,
                }
            )

        response_data = {
            "timestamp": _iso_utc(_utcnow_naive()), 
            "counts": counts, 
            "servers": server_list
        }
        
        # Cache in Redis for 30 seconds
        if is_redis_available():
            try:
                redis_client.setex(cache_key, 30, json.dumps(response_data))
            except Exception as e:
                logger.warning(f"[ServerHealth] Redis cache write failed: {e}")
        
        return jsonify(response_data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _get_server_device_for_metrics(device_id):
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return None, (jsonify({"error": "Device not found"}), 404)
    if not is_server_device(device.device_type):
        return None, (jsonify({"error": "Device is not a server"}), 400)
    return device, None


def _serve_server_telemetry(device_id):
    time_range = request.args.get("range", "24h")
    try:
        device, error_response = _get_server_device_for_metrics(device_id)
        if error_response:
            return error_response
        return jsonify(_build_server_telemetry_payload(device, time_range))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@server_metrics_bp.route("/api/server/<int:device_id>/metrics")
def get_server_metrics(device_id):
    return _serve_server_telemetry(device_id)


@server_metrics_bp.route("/api/devices/<int:device_id>/telemetry")
def get_device_server_telemetry(device_id):
    return _serve_server_telemetry(device_id)


@server_metrics_bp.route("/api/server/thresholds", methods=["GET"])
def get_server_thresholds():
    profile, threshold_payload = _merged_threshold_payload()
    return jsonify(
        {
            "scope": "global",
            "version": profile["version"],
            "updated_at": profile["updated_at"],
            "updated_by": profile["updated_by"],
            "change_reason": profile["change_reason"],
            "display_timezone": "browser-local",
            "metrics": threshold_payload["metrics"],
        }
    )


@server_metrics_bp.route("/api/server/thresholds", methods=["POST"])
@require_role("admin")
def save_server_thresholds():
    payload = request.get_json(silent=True) or {}
    raw_version = payload.get("version")
    try:
        expected_version = int(raw_version)
    except (TypeError, ValueError):
        return jsonify({"error": "version is required"}), 400

    change_reason = str(payload.get("change_reason") or "").strip() or None
    actor = str(session.get("username") or "system").strip() or "system"

    try:
        row, merged, changed_metric_keys = save_threshold_config(
            payload=payload,
            actor=actor,
            expected_version=expected_version,
            change_reason=change_reason,
        )
    except ThresholdValidationError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        db.session.rollback()
        if str(exc) == "CONFLICT_VERSION":
            return jsonify({"error": "Threshold config version is stale", "code": "CONFLICT_VERSION"}), 409
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 500

    previous_version = row.version - 1
    _create_threshold_audit_log(previous_version, row.version, changed_metric_keys, change_reason)
    db.session.commit()

    try:
        invalidate_dashboard_threshold_views()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.warning("Threshold cache invalidation failed: %s", exc)

    profile = serialize_threshold_profile(row=row, thresholds=merged)
    metrics = {}
    for metric_key, config in profile["metrics"].items():
        metric_payload = dict(config)
        catalog = METRIC_CATALOG.get(metric_key, {})
        metric_payload["default_enabled"] = bool(catalog.get("default_enabled", False))
        metric_payload["default_warning"] = float(catalog.get("default_warning", config.get("warning", 0)))
        metric_payload["default_critical"] = float(catalog.get("default_critical", config.get("critical", 0)))
        metric_payload["bands"] = build_chart_threshold_bands(metric_key, {"metrics": profile["metrics"]})
        metrics[metric_key] = metric_payload

    return jsonify(
        {
            "scope": "global",
            "version": profile["version"],
            "updated_at": profile["updated_at"],
            "updated_by": profile["updated_by"],
            "change_reason": profile["change_reason"],
            "metrics": metrics,
        }
    )
