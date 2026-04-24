import socket
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request, session
from sqlalchemy import func

from extensions import db
from middleware.rbac import current_scope_cache_fragment, require_login, require_role, scoped_query
from models.audit_log import AuditLog
from models.device import Device
from models.server_health import ServerHealthLog
from services.dashboard_server_incidents import build_server_incident_snapshot
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
from utils.server_health import compute_server_health, is_server_device, query_latest_server_health_logs

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


def _resolve_reverse_dns(ip_address, *, allow_lookup=True):
    if not ip_address:
        return None
    cached = _REVERSE_DNS_CACHE.get(ip_address)
    now = datetime.now(timezone.utc).timestamp()
    if cached and now < float(cached.get("expires_at", 0)):
        return cached.get("hostname")
    if not allow_lookup:
        return None
    try:
        hostname = socket.gethostbyaddr(ip_address)[0]
    except Exception:
        hostname = None
    _REVERSE_DNS_CACHE[ip_address] = {
        "hostname": hostname,
        "expires_at": now + _REVERSE_DNS_CACHE_TTL_SECONDS,
    }
    return hostname


def _build_connection_snapshot(last_log, *, allow_reverse_dns=False):
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
        resolved_hostname = (
            inventory_hostname
            or row.get("agent_hostname")
            or _resolve_reverse_dns(row["remote_ip"], allow_lookup=allow_reverse_dns)
            or row["remote_ip"]
        )
        resolution_source = (
            "inventory"
            if device_match
            else (
                "agent"
                if row.get("agent_hostname")
                else ("reverse_dns" if resolved_hostname and resolved_hostname != row["remote_ip"] else "ip")
            )
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

    # ── Availability from scan history (always a fixed 24h window) ──────────────
    from models.scan_history import DeviceScanHistory as _DSH
    _cutoff_24h = _utcnow_naive() - timedelta(hours=24)
    _scans_24h = (
        _DSH.query
        .filter(_DSH.device_ip == device.device_ip, _DSH.scan_timestamp >= _cutoff_24h)
        .order_by(_DSH.scan_timestamp.asc())
        .all()
    )
    _sc_total = len(_scans_24h)
    _sc_online = sum(1 for s in _scans_24h if (s.status or "").lower() == "online")
    availability_24h_pct = round(_sc_online / _sc_total * 100, 1) if _sc_total > 0 else None
    _dt_secs = 0.0
    for _i, _s in enumerate(_scans_24h):
        if (_s.status or "").lower() != "online":
            _dt_secs += (_scans_24h[_i + 1].scan_timestamp - _s.scan_timestamp).total_seconds() \
                if _i + 1 < _sc_total else 300.0
    downtime_24h_min = round(_dt_secs / 60.0, 1) if _sc_total > 0 else None
    _now = _utcnow_naive()
    uptime_timeline = []
    for _h in range(23, -1, -1):
        _bend = _now - timedelta(hours=_h)
        _bstart = _bend - timedelta(hours=1)
        _bucket = [s for s in _scans_24h if _bstart <= s.scan_timestamp < _bend]
        if not _bucket:
            uptime_timeline.append("unknown")
        else:
            _up = sum(1 for s in _bucket if (s.status or "").lower() == "online")
            _r = _up / len(_bucket)
            uptime_timeline.append("up" if _r >= 0.8 else ("partial" if _r > 0 else "down"))

    profile, threshold_payload = _merged_threshold_payload()
    latest_metrics = extract_latest_metrics(last_log)
    health_summary = summarize_health(last_log, {"metrics": threshold_payload.get("metrics", {})}) if last_log else None
    health_state = compute_server_health(last_log)
    connection_snapshot = _build_connection_snapshot(last_log, allow_reverse_dns=False)
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
        "availability_24h_pct": availability_24h_pct,
        "downtime_24h_min": downtime_24h_min,
        "uptime_timeline": uptime_timeline,
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


def _scoped_server_cache_key(base_key):
    return f"{base_key}:{current_scope_cache_fragment()}"


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


@server_metrics_bp.route("/devices/<int:device_id>/server-monitoring")
@require_login
def server_monitoring_page(device_id):
    """Full-page server telemetry view for a single device."""
    from flask import render_template, session
    from models.device import Device
    from middleware.rbac import scoped_query

    device = scoped_query(Device).get_or_404(device_id)
    return render_template(
        "server_details_page.html",
        device=device,
        can_edit_server_thresholds=str(session.get("role") or "").strip().lower() == "admin",
    )


_RANGE_HOURS_MAP = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720}


@server_metrics_bp.route("/api/server/fleet-metrics")
def get_fleet_metrics():
    try:
        range_param = request.args.get("range", "24h")
        range_hours = _RANGE_HOURS_MAP.get(range_param, 24)

        scoped_servers, scoped_server_ids = _scoped_server_devices()
        if not scoped_servers:
            return jsonify(
                {
                    "health": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                    "aggregates": {"cpu": 0, "memory": 0, "disk": 0},
                    "p95": {"cpu": 0, "memory": 0, "disk": 0},
                    "alerts": [],
                    "active_issues": [],
                    "dominant_issue": None,
                    "impact_summary": {
                        "affected_servers": 0,
                        "healthy_servers": 0,
                        "total_servers": 0,
                        "fleet_pct": 0,
                        "primary_issue_label": "No active server issues",
                        "primary_issue_severity": "Healthy",
                        "unaffected_domains": ["CPU", "Memory", "Disk"],
                    },
                    "filters": {"all": 0, "problem": 0, "healthy": 0, "critical": 0, "warning": 0},
                    "metric_cards": {},
                    "trends": {"labels": [], "range_hours": range_hours, "range_label": range_param, "cpu": {}, "memory": {}, "disk": {}, "network_in": {}, "network_out": {}, "latency": {}},
                    "uptime": {"current_24h_pct": 0.0, "previous_24h_pct": 0.0, "delta_pct": 0.0},
                    "synthetic_alerts": [],
                    "thresholds": {},
                }
            )
        return jsonify(build_server_incident_snapshot(scoped_servers, range_hours=range_hours))
    except Exception as exc:
        current_app.logger.exception("[fleet_metrics] Failed to build fleet snapshot")
        return jsonify({"error": str(exc)}), 500


@server_metrics_bp.route("/api/server/health")
def get_server_health_summary():
    """Get server health summary with Redis caching for performance."""
    from extensions import redis_client, is_redis_available
    import json
    
    # Try Redis cache first (30 second TTL)
    cache_key = _scoped_server_cache_key("server:health:summary")
    if is_redis_available():
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return jsonify(json.loads(cached))
        except Exception as e:
            current_app.logger.warning("[ServerHealth] Redis cache read failed: %s", e)

    try:
        scoped_servers, _ = _scoped_server_devices()
        if not scoped_servers:
            empty_response = {
                "timestamp": _iso_utc(_utcnow_naive()),
                "counts": {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "offline": 0},
                "filters": {"all": 0, "problem": 0, "healthy": 0, "critical": 0, "warning": 0},
                "dominant_issue": None,
                "active_issues": [],
                "servers": [],
            }
            return jsonify(empty_response)

        snapshot = build_server_incident_snapshot(scoped_servers)
        response_data = {
            "timestamp": snapshot.get("timestamp"),
            "counts": snapshot.get("counts", {}),
            "filters": snapshot.get("filters", {}),
            "dominant_issue": snapshot.get("dominant_issue"),
            "active_issues": snapshot.get("active_issues", []),
            "servers": snapshot.get("servers", []),
        }
        
        # Cache in Redis for 30 seconds
        if is_redis_available():
            try:
                redis_client.setex(cache_key, 30, json.dumps(response_data, default=str))
            except Exception as e:
                current_app.logger.warning("[ServerHealth] Redis cache write failed: %s", e)

        return jsonify(response_data)
    except Exception as exc:
        current_app.logger.exception("[server_health] Failed to build health summary")
        return jsonify({"error": str(exc)}), 500


@server_metrics_bp.route("/api/server/<int:device_id>/snapshot")
@require_login
def server_snapshot(device_id):
    """Lightweight operational snapshot for the modal quick-view.

    Returns only the data needed for the compact modal:
    uptime, 24h availability/downtime, ping stats, CPU/mem/disk summaries,
    SNMP config, mini CPU chart (1h), and hourly uptime timeline (24h).
    """
    from models.scan_history import DeviceScanHistory
    from models.snmp_config import DeviceSnmpConfig

    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({"error": "Device not found"}), 404

    # Latest agent telemetry log
    last_log = (
        ServerHealthLog.query
        .filter_by(device_id=device_id, source="agent")
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    # 24h scan history (keyed by device IP — no device_id FK on this table)
    cutoff_24h = _utcnow_naive() - timedelta(hours=24)
    scans = (
        DeviceScanHistory.query
        .filter(
            DeviceScanHistory.device_ip == device.device_ip,
            DeviceScanHistory.scan_timestamp >= cutoff_24h,
        )
        .order_by(DeviceScanHistory.scan_timestamp.asc())
        .all()
    )

    total = len(scans)
    online_ct = sum(1 for s in scans if (s.status or "").lower() == "online")
    availability_pct = round(online_ct / total * 100, 1) if total > 0 else None

    # Downtime from consecutive offline spans
    downtime_secs = 0.0
    for i, scan in enumerate(scans):
        if (scan.status or "").lower() != "online":
            if i + 1 < len(scans):
                delta = (scans[i + 1].scan_timestamp - scan.scan_timestamp).total_seconds()
                downtime_secs += max(0.0, delta)
            else:
                downtime_secs += 300.0  # trailing offline ~ one poll interval
    downtime_min = round(downtime_secs / 60.0, 1) if total > 0 else None

    # Ping / jitter / packet loss averages
    pings = [s.ping_time_ms for s in scans if s.ping_time_ms is not None]
    losses = [s.packet_loss for s in scans if s.packet_loss is not None]
    jitters = [s.jitter for s in scans if s.jitter is not None]
    avg_ping = round(sum(pings) / len(pings), 1) if pings else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    avg_jitter = round(sum(jitters) / len(jitters), 1) if jitters else None

    def _net_status(ping, loss):
        if ping is None and loss is None:
            return "no data"
        if (loss or 0) >= 5 or (ping or 0) >= 200:
            return "degraded"
        if (loss or 0) >= 1 or (ping or 0) >= 100:
            return "warning"
        return "stable"

    # SNMP config
    snmp_cfg = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()
    snmp_enabled = bool(snmp_cfg and snmp_cfg.is_enabled)

    # Health + uptime
    health_status = compute_server_health(last_log)
    health_summary = summarize_health(last_log) if last_log else None
    uptime_sec = _parse_uptime_seconds(last_log.uptime if last_log else None)

    # Mini CPU chart — last 1h, capped at 60 points
    cutoff_1h = _utcnow_naive() - timedelta(hours=1)
    cpu_logs = (
        ServerHealthLog.query
        .filter(
            ServerHealthLog.device_id == device_id,
            ServerHealthLog.source == "agent",
            ServerHealthLog.timestamp >= cutoff_1h,
        )
        .order_by(ServerHealthLog.timestamp.asc())
        .limit(60)
        .all()
    )
    cpu_labels = [_iso_utc(lg.timestamp) for lg in cpu_logs]
    cpu_data = [_safe_float(lg.cpu_usage) for lg in cpu_logs]

    # Hourly uptime timeline — 24 buckets oldest-first
    now = _utcnow_naive()
    timeline = []
    for h in range(23, -1, -1):
        bucket_end = now - timedelta(hours=h)
        bucket_start = bucket_end - timedelta(hours=1)
        bucket = [s for s in scans if bucket_start <= s.scan_timestamp < bucket_end]
        if not bucket:
            timeline.append("unknown")
        else:
            up_ct = sum(1 for s in bucket if (s.status or "").lower() == "online")
            ratio = up_ct / len(bucket)
            timeline.append("up" if ratio >= 0.8 else ("partial" if ratio > 0 else "down"))

    return jsonify({
        "device_id": device_id,
        "device_name": device.device_name,
        "ip": device.device_ip,
        "hostname": device.hostname,
        "os_name": last_log.os_name if last_log else None,
        "monitoring_mode": getattr(device, "monitoring_mode", "ping"),
        "status": health_status,
        "health_score": health_summary.score if health_summary else 0,
        "last_seen": _iso_utc(last_log.timestamp) if last_log and last_log.timestamp else None,
        "uptime_seconds": uptime_sec,
        "availability_24h_pct": availability_pct,
        "downtime_24h_min": downtime_min,
        "ping_ms": avg_ping,
        "jitter_ms": avg_jitter,
        "packet_loss_pct": avg_loss,
        "network_status": _net_status(avg_ping, avg_loss),
        "snmp_enabled": snmp_enabled,
        "snmp_version": snmp_cfg.snmp_version if snmp_cfg else None,
        "snmp_port": snmp_cfg.snmp_port if snmp_cfg else None,
        "snmp_last_poll": _iso_utc(snmp_cfg.last_successful_poll) if snmp_cfg and snmp_cfg.last_successful_poll else None,
        "cpu_current": _safe_float(last_log.cpu_usage if last_log else None),
        "memory_current": _safe_float(last_log.memory_usage if last_log else None),
        "disk_current": _safe_float(last_log.disk_usage if last_log else None),
        "cpu_chart_labels": cpu_labels,
        "cpu_chart_data": cpu_data,
        "uptime_timeline": timeline,
        "alerts": last_log.alerts if last_log and last_log.alerts else [],
    })


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
@require_login
def get_server_metrics(device_id):
    return _serve_server_telemetry(device_id)


@server_metrics_bp.route("/api/devices/<int:device_id>/telemetry")
@require_login
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
