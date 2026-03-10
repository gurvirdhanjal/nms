"""
Shared dashboard availability classification helpers.
"""
from collections import defaultdict

from sqlalchemy import func

from extensions import db
from models.scan_history import DeviceScanHistory


DEGRADED_LATENCY_THRESHOLD = 200
DEGRADED_PACKET_LOSS_THRESHOLD = 5


def _load_latest_scan_map(device_ips):
    normalized_ips = sorted({str(ip).strip() for ip in (device_ips or []) if str(ip or "").strip()})
    if not normalized_ips:
        return {}

    latest_subq = (
        db.session.query(
            DeviceScanHistory.device_ip,
            func.max(DeviceScanHistory.scan_id).label("max_id"),
        )
        .filter(DeviceScanHistory.device_ip.in_(normalized_ips))
        .group_by(DeviceScanHistory.device_ip)
        .subquery()
    )

    latest_scans = (
        db.session.query(DeviceScanHistory)
        .join(
            latest_subq,
            (DeviceScanHistory.device_ip == latest_subq.c.device_ip)
            & (DeviceScanHistory.scan_id == latest_subq.c.max_id),
        )
        .all()
    )
    return {scan.device_ip: scan for scan in latest_scans if getattr(scan, "device_ip", None)}


def _classify_scan_state(scan):
    if scan is None:
        return "unknown"

    status = str(getattr(scan, "status", "") or "").strip().lower()
    if status != "online":
        return "offline"

    if getattr(scan, "ping_time_ms", None) and scan.ping_time_ms > DEGRADED_LATENCY_THRESHOLD:
        return "degraded"
    if getattr(scan, "packet_loss", None) and scan.packet_loss > DEGRADED_PACKET_LOSS_THRESHOLD:
        return "degraded"
    return "healthy"


def _build_subnet_health(devices, device_states):
    subnet_totals = defaultdict(int)
    subnet_online = defaultdict(int)

    for device in devices:
        subnet = getattr(device, "subnet_cidr", None) or "Unassigned"
        subnet_totals[subnet] += 1
        if device_states.get(getattr(device, "device_id", None)) in ("healthy", "degraded"):
            subnet_online[subnet] += 1

    rows = []
    for subnet in sorted(subnet_totals.keys()):
        total = subnet_totals[subnet]
        online = subnet_online.get(subnet, 0)
        rows.append(
            {
                "subnet": subnet,
                "total": total,
                "online": online,
                "offline": max(total - online, 0),
            }
        )
    return rows


def build_device_availability_snapshot(devices, *, now_utc=None):
    """
    Build per-device availability state and aggregate counts using the same
    latest-scan classifier as the dashboard summary.
    """
    device_list = list(devices or [])
    latest_scan_map = _load_latest_scan_map([getattr(device, "device_ip", None) for device in device_list])

    device_states = {}
    online_device_ids = set()
    healthy_count = 0
    degraded_count = 0
    offline_count = 0
    unknown_count = 0
    latencies = []
    packet_losses = []

    for device in device_list:
        device_id = getattr(device, "device_id", None)
        scan = latest_scan_map.get(getattr(device, "device_ip", None))
        state = _classify_scan_state(scan)
        device_states[device_id] = state

        if state == "healthy":
            healthy_count += 1
            if device_id is not None:
                online_device_ids.add(device_id)
        elif state == "degraded":
            degraded_count += 1
            if device_id is not None:
                online_device_ids.add(device_id)
        elif state == "offline":
            offline_count += 1
        else:
            unknown_count += 1

        if state in ("healthy", "degraded") and scan is not None:
            if getattr(scan, "ping_time_ms", None):
                latencies.append(scan.ping_time_ms)
            if getattr(scan, "packet_loss", None) is not None:
                packet_losses.append(scan.packet_loss)

    total = len(device_list)
    online_total = healthy_count + degraded_count

    return {
        "generated_at": now_utc,
        "device_states": device_states,
        "online_device_ids": online_device_ids,
        "counts": {
            "total": total,
            "healthy": healthy_count,
            "degraded": degraded_count,
            "online_total": online_total,
            "offline": offline_count,
            "unknown": unknown_count,
        },
        "network_health": {
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "avg_packet_loss_pct": round(sum(packet_losses) / len(packet_losses), 2) if packet_losses else 0,
        },
        "latest_scan_map": latest_scan_map,
        "subnet_health": _build_subnet_health(device_list, device_states),
    }
