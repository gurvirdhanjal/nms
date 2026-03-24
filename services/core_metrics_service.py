"""
core_metrics_service.py — canonical device metrics builder.

Provides two public bulk builders:
  get_server_metrics_bulk()      — server/infra fleet, 3-5 DB queries total
  get_workstation_metrics_bulk() — tracked device fleet, 2 DB queries total

Both return lists of dicts conforming to the canonical 18-field device row contract:
  device_id, device_name, device_ip, fleet,
  uptime_pct, downtime_hours, sla_tier,
  avg_latency_ms, max_latency_ms, avg_packet_loss_pct,
  timeout_count, incident_count, mttr_min,
  violation_count, has_violation, last_violation_time,
  anomaly_flag, anomaly_reason

All private helpers are also importable for use by enterprise_report_service.py
(which re-exports them to preserve backward-compatible test imports).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func

from extensions import db
from models.dashboard import DailyDeviceStats
from models.device import Device
from models.restricted_site_policy import RestrictedSiteEvent
from models.scan_history import DeviceScanHistory
from models.tracked_device import TrackedDevice, TrackedDeviceAvailabilityEvent

logger = logging.getLogger(__name__)


# ── SLA tier thresholds (%) ─────────────────────────────────────────────────
SLA_GOLD = 99.9
SLA_SILVER = 99.5
SLA_BRONZE = 99.0
SLA_WARNING = 95.0

# ── Anomaly detection thresholds ─────────────────────────────────────────────
ANOMALY_LATENCY_MS      = 300.0   # avg latency above this → anomaly
ANOMALY_PACKET_LOSS_PCT = 50.0    # packet loss above this → anomaly
ANOMALY_UPTIME_PCT      = 90.0    # uptime below this → anomaly
ANOMALY_VIOLATION_COUNT = 10      # site violations above this → anomaly

# ── Incident deduplication constants ─────────────────────────────────────────
FLAP_MERGE_GAP_S = 120          # Merge incidents closer than 2 minutes apart
MAX_INCIDENT_DURATION_H = 72    # Cap single incident at 72h (likely data gap)
MIN_INCIDENT_DURATION_S = 10    # Ignore incidents < 10s (probe jitter)

# ── Monitoring gap detection ────────────────────────────────────────────────
GAP_THRESHOLD_S = 1800           # 30 min = 6× ICMP interval; silence → system was down


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_round(val, decimals: int = 2):
    """Round a numeric value, preserving None to distinguish 'no data' from zero."""
    if val is None:
        return None
    try:
        return round(float(val), decimals)
    except (TypeError, ValueError):
        return None


def sla_tier(uptime_pct: Optional[float], thresholds: Optional[Dict[str, float]] = None) -> str:
    """Assign SLA tier based on uptime percentage.

    If *thresholds* is provided, uses per-device SLA thresholds from
    ComplianceProfile.rules_json (keys: sla_gold, sla_silver, sla_bronze,
    sla_warning).  Missing keys fall back to module-level constants.
    """
    if uptime_pct is None:
        return "Unknown"
    t = thresholds or {}
    gold    = t.get("sla_gold",    SLA_GOLD)
    silver  = t.get("sla_silver",  SLA_SILVER)
    bronze  = t.get("sla_bronze",  SLA_BRONZE)
    warning = t.get("sla_warning", SLA_WARNING)
    if uptime_pct >= gold:
        return "Gold"
    if uptime_pct >= silver:
        return "Silver"
    if uptime_pct >= bronze:
        return "Bronze"
    if uptime_pct >= warning:
        return "Warning"
    return "Critical"


def downtime_hours(uptime_pct: Optional[float], period_hours: float,
                   observed_hours: Optional[float] = None) -> Optional[float]:
    """Downtime in hours.  Uses *observed_hours* (actual monitored time) when
    provided so that monitoring system gaps don't inflate the number."""
    if uptime_pct is None:
        return None
    effective = observed_hours if observed_hours is not None else period_hours
    frac = max(0.0, 1.0 - uptime_pct / 100.0)
    return _safe_round(frac * effective)


def coverage_level(pct: Optional[float]) -> str:
    """Classify monitoring coverage into human-readable tiers."""
    if pct is None:
        return "unknown"
    if pct >= 95:
        return "high"
    if pct >= 80:
        return "medium"
    return "low"


# ── Uptime calculators ────────────────────────────────────────────────────────

def _inventory_uptime(device_ip: Optional[str], device_id: int,
                      start: datetime, end: datetime,
                      device_name: Optional[str] = None) -> Optional[float]:
    """
    Uptime % for an inventory device over [start, end].
    Primary: DailyDeviceStats aggregates (fast).
    Fallback: raw DeviceScanHistory by IP or hostname (identity-aware).
    """
    from sqlalchemy import or_, and_

    # Fast path — pre-aggregated daily rollup
    avg = (
        db.session.query(func.avg(DailyDeviceStats.uptime_percent))
        .filter(
            DailyDeviceStats.device_id == device_id,
            DailyDeviceStats.date >= start.date(),
            DailyDeviceStats.date <= end.date(),
        )
        .scalar()
    )
    if avg is not None:
        return _safe_round(float(avg))

    # Fallback — scan history by IP or hostname (captures IP changes)
    ip_or_name_filters = []
    if device_ip:
        ip_or_name_filters.append(DeviceScanHistory.device_ip == device_ip)
    if device_name:
        ip_or_name_filters.append(DeviceScanHistory.device_name == device_name)
    if not ip_or_name_filters:
        return None
    identity_filter = or_(*ip_or_name_filters) if len(ip_or_name_filters) > 1 else ip_or_name_filters[0]

    total = (
        db.session.query(func.count(DeviceScanHistory.scan_id))
        .filter(
            identity_filter,
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
        )
        .scalar()
        or 0
    )
    if total == 0:
        return None
    online = (
        db.session.query(func.count(DeviceScanHistory.scan_id))
        .filter(
            identity_filter,
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
            func.lower(DeviceScanHistory.status) == "online",
        )
        .scalar()
        or 0
    )
    return _safe_round((online / total) * 100.0)


def _count_no_response_scans(device_ip: Optional[str], start: datetime, end: datetime) -> int:
    """Count scans where the device was offline with no ping response (request timeout)."""
    if not device_ip:
        return 0
    count = (
        db.session.query(func.count(DeviceScanHistory.scan_id))
        .filter(
            DeviceScanHistory.device_ip == device_ip,
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
            func.lower(DeviceScanHistory.status) == "offline",
            DeviceScanHistory.ping_time_ms.is_(None),
        )
        .scalar()
    )
    return int(count or 0)


def _inventory_network_stats(device_id: int, start: datetime, end: datetime) -> dict:
    """
    Network-quality stats from DailyDeviceStats: latency, packet-loss, alerts.
    Returns empty dict when no rows exist for the period.
    """
    row = (
        db.session.query(
            func.avg(DailyDeviceStats.avg_latency_ms).label("avg_lat"),
            func.max(DailyDeviceStats.max_latency_ms).label("max_lat"),
            func.avg(DailyDeviceStats.avg_packet_loss_pct).label("avg_pkt"),
            func.sum(DailyDeviceStats.total_alerts).label("total_alerts"),
        )
        .filter(
            DailyDeviceStats.device_id == device_id,
            DailyDeviceStats.date >= start.date(),
            DailyDeviceStats.date <= end.date(),
        )
        .first()
    )
    if not row or row.avg_lat is None:
        return {}
    return {
        "avg_latency_ms": _safe_round(row.avg_lat) if row.avg_lat is not None else None,
        "max_latency_ms": _safe_round(row.max_lat) if row.max_lat is not None else None,
        "avg_packet_loss_pct": _safe_round(row.avg_pkt) if row.avg_pkt is not None else None,
        "total_alerts": int(row.total_alerts or 0),
    }


def _compute_uptime_from_events(
    events: list, start: datetime, end: datetime
) -> Tuple[Optional[float], List[dict], dict]:
    """
    Pure state-machine helper: derive uptime % and incident list from a
    pre-fetched list of availability event objects.

    Each object must expose `.status` (str) and `.observed_at` (datetime).
    Returns (uptime_pct, incidents, coverage_meta).

    Denominator is the *monitored* window: (first event → window end) minus
    any monitoring gaps (silences > GAP_THRESHOLD_S between consecutive events).
    Time before the first event and mid-period monitoring gaps are excluded
    rather than treated as uptime or downtime.
    """
    if not events:
        total_s = max(0.0, (end - start).total_seconds())
        return None, [], {
            "observed_seconds": 0.0,
            "total_gap_seconds": total_s,
            "gap_count": 1 if total_s > 0 else 0,
            "monitoring_coverage_pct": 0.0,
        }

    period_seconds = (end - start).total_seconds()
    if period_seconds <= 0:
        return None, [], {
            "observed_seconds": 0.0, "total_gap_seconds": 0.0,
            "gap_count": 0, "monitoring_coverage_pct": 0.0,
        }

    # Raw window: first event → report end
    window_seconds = (end - events[0].observed_at).total_seconds()
    if window_seconds <= 0:
        return None, [], {
            "observed_seconds": 0.0, "total_gap_seconds": 0.0,
            "gap_count": 0, "monitoring_coverage_pct": 0.0,
        }

    # ── Phase 1: Run the state machine (unchanged logic) ─────────────────
    incidents: List[dict] = []
    total_down_s = 0.0
    offline_since: Optional[datetime] = None

    for ev in events:
        status = (ev.status or "").lower()
        if status in ("offline", "degraded") and offline_since is None:
            offline_since = ev.observed_at
        elif status == "online" and offline_since is not None:
            dur_s = (ev.observed_at - offline_since).total_seconds()
            total_down_s += dur_s
            incidents.append({
                "start": offline_since.isoformat(),
                "end": ev.observed_at.isoformat(),
                "duration_min": _safe_round(dur_s / 60.0),
            })
            offline_since = None

    # Open-ended incident still in progress at window end
    if offline_since is not None:
        dur_s = (end - offline_since).total_seconds()
        total_down_s += dur_s
        incidents.append({
            "start": offline_since.isoformat(),
            "end": end.isoformat(),
            "duration_min": _safe_round(dur_s / 60.0),
            "open": True,
        })

    # ── Phase 2: Detect monitoring gaps ──────────────────────────────────
    gap_intervals: List[Tuple[datetime, datetime]] = []
    total_gap_s = 0.0

    for i in range(1, len(events)):
        gap_s = (events[i].observed_at - events[i - 1].observed_at).total_seconds()
        if gap_s > GAP_THRESHOLD_S:
            gap_intervals.append((events[i - 1].observed_at, events[i].observed_at))
            total_gap_s += gap_s

    # NOTE: Tail gap (last event → window end) is NOT detected here.
    # Availability events are state-change-driven, not periodic heartbeats.
    # A single "online" event with no follow-up means the device stayed online,
    # not that the monitoring system stopped.

    # ── Phase 3: Subtract gap/incident overlap from downtime ─────────────
    gap_overlap_s = 0.0
    for inc in incidents:
        try:
            inc_start = datetime.fromisoformat(inc["start"])
            inc_end = datetime.fromisoformat(inc["end"])
        except (KeyError, ValueError):
            continue
        inc_overlap_s = 0.0
        for gap_start, gap_end in gap_intervals:
            overlap_start = max(gap_start, inc_start)
            overlap_end = min(gap_end, inc_end)
            if overlap_start < overlap_end:
                inc_overlap_s += (overlap_end - overlap_start).total_seconds()
        if inc_overlap_s > 0:
            gap_overlap_s += inc_overlap_s
            inc["duration_min"] = max(0.0, _safe_round(
                (inc["duration_min"] or 0.0) - inc_overlap_s / 60.0
            ))

    # ── Phase 4: Adjusted uptime ─────────────────────────────────────────
    monitored_seconds = max(0.0, window_seconds - total_gap_s)

    if monitored_seconds <= 0:
        coverage_meta = {
            "observed_seconds": 0.0,
            "total_gap_seconds": total_gap_s,
            "gap_count": len(gap_intervals),
            "monitoring_coverage_pct": 0.0,
        }
        return None, incidents, coverage_meta

    adjusted_down = max(0.0, total_down_s - gap_overlap_s)
    uptime_pct = _safe_round(max(0.0, 1.0 - adjusted_down / monitored_seconds) * 100.0)

    coverage_meta = {
        "observed_seconds": monitored_seconds,
        "total_gap_seconds": total_gap_s,
        "gap_count": len(gap_intervals),
        "monitoring_coverage_pct": _safe_round(
            monitored_seconds / window_seconds * 100.0
        ),
    }
    return uptime_pct, incidents, coverage_meta


def _bulk_uptime_and_incidents(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> "Dict[int, Tuple[Optional[float], List[dict], dict]]":
    """Bulk version: one query for all device IDs, groups in Python.
    Returns {device_id: (uptime_pct, incidents, coverage_meta)} for every id.
    Devices with no events get (None, [], {}).
    """
    if not device_ids:
        return {}
    all_events = (
        TrackedDeviceAvailabilityEvent.query
        .filter(
            TrackedDeviceAvailabilityEvent.device_id.in_(device_ids),
            TrackedDeviceAvailabilityEvent.observed_at >= start,
            TrackedDeviceAvailabilityEvent.observed_at <= end,
        )
        .order_by(
            TrackedDeviceAvailabilityEvent.device_id.asc(),
            TrackedDeviceAvailabilityEvent.observed_at.asc(),
        )
        .all()
    )
    # Group by device_id
    grouped: "Dict[int, list]" = {did: [] for did in device_ids}
    for ev in all_events:
        if ev.device_id in grouped:
            grouped[ev.device_id].append(ev)
    return {
        did: _compute_uptime_from_events(evs, start, end)
        for did, evs in grouped.items()
    }


def _merge_flapping_incidents(incidents: List[dict]) -> Tuple[List[dict], int]:
    """Merge incidents separated by < FLAP_MERGE_GAP_S into one.

    Also filters sub-threshold incidents and caps absurdly long ones.
    Returns (merged_incidents, flap_suppressed_count).
    """
    if not incidents:
        return [], 0

    merged: List[dict] = []
    flap_count = 0

    for inc in incidents:
        dur_min = inc.get("duration_min", 0)

        # Skip sub-threshold incidents (probe jitter)
        if dur_min < MIN_INCIDENT_DURATION_S / 60.0:
            flap_count += 1
            continue

        # Cap absurdly long incidents (likely data gap, not real outage)
        inc = dict(inc)
        if dur_min > MAX_INCIDENT_DURATION_H * 60:
            inc["duration_min"] = MAX_INCIDENT_DURATION_H * 60
            inc["capped"] = True

        if not merged:
            merged.append(inc)
            continue

        # Check gap between end of previous incident and start of this one
        prev = merged[-1]
        try:
            prev_end = datetime.fromisoformat(prev.get("end", prev["start"]))
            inc_start = datetime.fromisoformat(inc["start"])
            gap_s = (inc_start - prev_end).total_seconds()
        except (KeyError, ValueError):
            gap_s = float("inf")

        if gap_s < FLAP_MERGE_GAP_S:
            # Merge: extend previous incident
            try:
                inc_end_str = inc.get("end", inc["start"])
                inc_end = datetime.fromisoformat(inc_end_str)
                if inc.get("duration_min"):
                    inc_end = datetime.fromisoformat(inc["start"]) + timedelta(minutes=inc["duration_min"])
                prev_start = datetime.fromisoformat(prev["start"])
                prev["end"] = inc_end.isoformat()
                prev["duration_min"] = _safe_round((inc_end - prev_start).total_seconds() / 60.0)
                prev["merged_count"] = prev.get("merged_count", 1) + 1
            except (KeyError, ValueError):
                merged.append(inc)
                continue
            flap_count += 1
        else:
            merged.append(inc)

    return merged, flap_count


def _mttr_mtbf(incidents: List[dict]) -> Tuple[Optional[float], Optional[float]]:
    """
    Returns (mttr_min, mtbf_hours).
    MTTR = mean incident duration.
    MTBF = mean time between incident start times.
    """
    if not incidents:
        return None, None

    mttr = _safe_round(sum(inc["duration_min"] for inc in incidents) / len(incidents))

    if len(incidents) < 2:
        return mttr, None

    starts: List[datetime] = []
    for inc in incidents:
        try:
            starts.append(datetime.fromisoformat(inc["start"]))
        except Exception:
            pass
    if len(starts) < 2:
        return mttr, None

    gaps_h = [(starts[i + 1] - starts[i]).total_seconds() / 3600.0 for i in range(len(starts) - 1)]
    mtbf = _safe_round(sum(gaps_h) / len(gaps_h)) if gaps_h else None
    return mttr, mtbf


def get_device_violations(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> Dict[int, dict]:
    """Bulk violation aggregation for a set of tracked device IDs.

    Single query — no N+1. Returns a dict keyed by device_id:
      {
        "violation_count":     int,   # total RestrictedSiteEvents in period
        "has_violation":       bool,
        "last_violation_time": str,   # ISO 8601 UTC, or None
        "top_domains":         list,  # up to 3 [{domain, count}] desc
      }
    Devices with no violations are absent from the result dict.
    """
    if not device_ids:
        return {}
    try:
        rows = (
            db.session.query(
                RestrictedSiteEvent.device_id,
                RestrictedSiteEvent.domain,
                func.count(RestrictedSiteEvent.id).label("cnt"),
                func.max(RestrictedSiteEvent.observed_at_utc).label("last_seen"),
            )
            .filter(
                RestrictedSiteEvent.device_id.in_(device_ids),
                RestrictedSiteEvent.observed_at_utc >= start,
                RestrictedSiteEvent.observed_at_utc <= end,
            )
            .group_by(RestrictedSiteEvent.device_id, RestrictedSiteEvent.domain)
            .order_by(func.count(RestrictedSiteEvent.id).desc())
            .all()
        )
    except Exception as exc:
        logger.warning("[CoreMetrics] get_device_violations failed: %s", exc)
        return {}

    result: Dict[int, dict] = {}
    for row in rows:
        did = row.device_id
        if did not in result:
            result[did] = {
                "violation_count": 0,
                "has_violation": True,
                "last_violation_time": None,
                "top_domains": [],
            }
        entry = result[did]
        entry["violation_count"] += int(row.cnt)
        if row.last_seen:
            if entry["last_violation_time"] is None or row.last_seen > entry["last_violation_time"]:
                entry["last_violation_time"] = row.last_seen
        if len(entry["top_domains"]) < 3:
            entry["top_domains"].append({"domain": row.domain, "count": int(row.cnt)})

    for entry in result.values():
        if entry["last_violation_time"] is not None:
            entry["last_violation_time"] = entry["last_violation_time"].isoformat()

    return result


# ── Anomaly detection ────────────────────────────────────────────────────────

def _detect_anomaly(row: dict) -> tuple:
    """Rule-based anomaly detection on a device metric row.

    Checks (in order):
      - avg_latency_ms > ANOMALY_LATENCY_MS      (300 ms)
      - avg_packet_loss_pct > ANOMALY_PACKET_LOSS_PCT (50 %)
      - uptime_pct < ANOMALY_UPTIME_PCT           (90 %)
      - violation_count > ANOMALY_VIOLATION_COUNT (10)

    All checks are None-guarded — fields absent or None never trigger a flag.
    Returns (anomaly_flag: bool, anomaly_reason: Optional[str]).
    """
    reasons = []
    latency = row.get("avg_latency_ms")
    if latency is not None and latency > ANOMALY_LATENCY_MS:
        reasons.append(f"latency {latency:.1f}ms")
    pkt_loss = row.get("avg_packet_loss_pct")
    if pkt_loss is not None and pkt_loss > ANOMALY_PACKET_LOSS_PCT:
        reasons.append(f"packet_loss {pkt_loss:.1f}%")
    uptime = row.get("uptime_pct")
    if uptime is not None and uptime < ANOMALY_UPTIME_PCT:
        reasons.append(f"uptime {uptime:.2f}%")
    vc = row.get("violation_count")
    if vc is not None and vc > ANOMALY_VIOLATION_COUNT:
        reasons.append(f"{vc} violations")
    if reasons:
        return True, "; ".join(reasons)
    return False, None


# ── Bulk server helpers (3 queries total for N devices) ──────────────────────

def _bulk_inventory_uptime(
    devices: List[Device],
    start: datetime,
    end: datetime,
) -> Dict[int, Optional[float]]:
    """One DailyDeviceStats query for all device IDs (fast path).
    Falls back to a single DeviceScanHistory query for devices with no daily stats.
    Returns {device_id: uptime_pct_or_None}.
    """
    if not devices:
        return {}
    device_ids = [d.device_id for d in devices]

    # Fast path — pre-aggregated daily stats, one query for all devices
    agg_rows = (
        db.session.query(
            DailyDeviceStats.device_id,
            func.avg(DailyDeviceStats.uptime_percent).label("avg_up"),
        )
        .filter(
            DailyDeviceStats.device_id.in_(device_ids),
            DailyDeviceStats.date >= start.date(),
            DailyDeviceStats.date <= end.date(),
        )
        .group_by(DailyDeviceStats.device_id)
        .all()
    )
    result: Dict[int, Optional[float]] = {
        row.device_id: _safe_round(float(row.avg_up))
        for row in agg_rows
        if row.avg_up is not None
    }

    # Fallback — scan history for devices not covered by daily stats
    missing = [d for d in devices if d.device_id not in result]
    if not missing:
        return result
    from sqlalchemy import or_
    ip_to_id = {d.device_ip: d.device_id for d in missing if d.device_ip}
    name_to_id = {d.device_name: d.device_id for d in missing if d.device_name}
    ip_filters = [DeviceScanHistory.device_ip.in_(list(ip_to_id))] if ip_to_id else []
    name_filters = [DeviceScanHistory.device_name.in_(list(name_to_id))] if name_to_id else []
    combined = ip_filters + name_filters
    if not combined:
        return result
    scan_rows = (
        db.session.query(
            DeviceScanHistory.device_ip,
            DeviceScanHistory.device_name,
            func.count(DeviceScanHistory.scan_id).label("total"),
            func.sum(
                func.cast(
                    func.lower(DeviceScanHistory.status) == "online",
                    db.Integer,
                )
            ).label("online"),
        )
        .filter(
            or_(*combined),
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
        )
        .group_by(DeviceScanHistory.device_ip, DeviceScanHistory.device_name)
        .all()
    )
    for row in scan_rows:
        did = ip_to_id.get(row.device_ip or "") or name_to_id.get(row.device_name or "")
        if did and row.total:
            result[did] = _safe_round((int(row.online or 0) / int(row.total)) * 100.0)
    return result


def _bulk_inventory_network_stats(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> Dict[int, dict]:
    """One DailyDeviceStats query for avg/max latency and packet loss for all device IDs.
    Returns {device_id: {avg_latency_ms, max_latency_ms, avg_packet_loss_pct}}.
    Absent for devices with no daily stats in the period.
    """
    if not device_ids:
        return {}
    rows = (
        db.session.query(
            DailyDeviceStats.device_id,
            func.avg(DailyDeviceStats.avg_latency_ms).label("avg_lat"),
            func.max(DailyDeviceStats.max_latency_ms).label("max_lat"),
            func.avg(DailyDeviceStats.avg_packet_loss_pct).label("avg_pkt"),
        )
        .filter(
            DailyDeviceStats.device_id.in_(device_ids),
            DailyDeviceStats.date >= start.date(),
            DailyDeviceStats.date <= end.date(),
        )
        .group_by(DailyDeviceStats.device_id)
        .all()
    )
    return {
        row.device_id: {
            "avg_latency_ms":      _safe_round(row.avg_lat) if row.avg_lat is not None else None,
            "max_latency_ms":      _safe_round(row.max_lat) if row.max_lat is not None else None,
            "avg_packet_loss_pct": _safe_round(row.avg_pkt) if row.avg_pkt is not None else None,
        }
        for row in rows
        if row.avg_lat is not None
    }


def _bulk_count_no_response_scans(
    ip_map: Dict[str, int],
    start: datetime,
    end: datetime,
) -> Dict[int, int]:
    """One DeviceScanHistory query for offline+no-ping-response scans across all IPs.
    ip_map: {device_ip: device_id}
    Returns {device_id: timeout_count}. Absent device_ids have count 0.
    """
    ips = [ip for ip in ip_map if ip]
    if not ips:
        return {}
    rows = (
        db.session.query(
            DeviceScanHistory.device_ip,
            func.count(DeviceScanHistory.scan_id).label("cnt"),
        )
        .filter(
            DeviceScanHistory.device_ip.in_(ips),
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
            func.lower(DeviceScanHistory.status) == "offline",
            DeviceScanHistory.ping_time_ms.is_(None),
        )
        .group_by(DeviceScanHistory.device_ip)
        .all()
    )
    return {
        ip_map[row.device_ip]: int(row.cnt)
        for row in rows
        if row.device_ip in ip_map
    }


# ── Monitoring coverage helpers ───────────────────────────────────────────────

def _bulk_icmp_coverage(
    device_ids: List[int], start: datetime, end: datetime,
) -> Dict[int, dict]:
    """Monitoring coverage for ICMP devices based on actual scan counts vs
    expected scans (5-min interval).

    Returns {device_id: {"actual_scans": N, "expected_scans": N,
                          "coverage_pct": float, "observed_hours": float}}.
    Single grouped query — no N+1.
    """
    if not device_ids:
        return {}

    # Expected scans: 5-min interval → 12/hr
    total_seconds = max(300.0, (end - start).total_seconds())
    expected_scans = total_seconds / 300.0
    total_hours = total_seconds / 3600.0

    # Join Device → DeviceScanHistory via IP to get per-device scan counts
    rows = (
        db.session.query(
            Device.device_id,
            func.count(DeviceScanHistory.scan_id).label("scan_count"),
        )
        .join(DeviceScanHistory, DeviceScanHistory.device_ip == Device.device_ip)
        .filter(
            Device.device_id.in_(device_ids),
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
        )
        .group_by(Device.device_id)
        .all()
    )
    result = {}
    for row in rows:
        actual = int(row.scan_count)
        cov = min(100.0, actual / expected_scans * 100.0) if expected_scans > 0 else 0.0
        obs_h = (actual / expected_scans) * total_hours if expected_scans > 0 else 0.0
        result[row.device_id] = {
            "actual_scans": actual,
            "expected_scans": int(expected_scans),
            "coverage_pct": _safe_round(cov),
            "observed_hours": _safe_round(obs_h),
        }
    return result


# ── Public bulk builders ──────────────────────────────────────────────────────

def get_server_metrics_bulk(
    devices: List[Device],
    start: datetime,
    end: datetime,
    period_hours: float,
) -> List[dict]:
    """Return canonical device rows for server/infra fleet.
    Uses 3 bulk queries total (not 3N per device).
    Excludes CPU/mem/disk/agent telemetry — those remain in enterprise_report_service.
    """
    if not devices:
        return []
    device_ids = [d.device_id for d in devices]
    ip_map = {d.device_ip: d.device_id for d in devices if d.device_ip}

    # 4 queries total regardless of fleet size
    bulk_uptime   = _bulk_inventory_uptime(devices, start, end)
    bulk_net      = _bulk_inventory_network_stats(device_ids, start, end)
    bulk_timeouts = _bulk_count_no_response_scans(ip_map, start, end)
    bulk_cov      = _bulk_icmp_coverage(device_ids, start, end)

    rows = []
    for dev in devices:
        try:
            up  = bulk_uptime.get(dev.device_id)
            net = bulk_net.get(dev.device_id, {})
            cov = bulk_cov.get(dev.device_id, {})
            row = {
                "device_id":           dev.device_id,
                "device_name":         dev.device_name or f"Device-{dev.device_ip}",
                "device_ip":           dev.device_ip or "—",
                "fleet":               "server",
                "uptime_pct":          up,
                "downtime_hours":      downtime_hours(up, period_hours,
                                                      observed_hours=cov.get("observed_hours")),
                "sla_tier":            sla_tier(up),
                "avg_latency_ms":      net.get("avg_latency_ms"),
                "max_latency_ms":      net.get("max_latency_ms"),
                "avg_packet_loss_pct": net.get("avg_packet_loss_pct"),
                "timeout_count":       bulk_timeouts.get(dev.device_id, 0),
                "incident_count":      None,
                "mttr_min":            None,
                "violation_count":     0,
                "has_violation":       False,
                "last_violation_time": None,
                "monitoring_coverage_pct": cov.get("coverage_pct"),
                "observed_hours":         cov.get("observed_hours"),
                "coverage_level":         coverage_level(cov.get("coverage_pct")),
            }
            row["anomaly_flag"], row["anomaly_reason"] = _detect_anomaly(row)
        except Exception as exc:
            logger.warning("[CoreMetrics] server device_id=%s skipped: %s", dev.device_id, exc)
            continue
        rows.append(row)
    return rows


def get_workstation_metrics_bulk(
    tracked_devices: List[TrackedDevice],
    start: datetime,
    end: datetime,
    period_hours: float,
) -> List[dict]:
    """Return canonical device rows for workstation fleet.
    Uses 2 bulk queries total (availability events + violations).
    """
    if not tracked_devices:
        return []
    tracked_ids = [dev.id for dev in tracked_devices]
    bulk_uptime = _bulk_uptime_and_incidents(tracked_ids, start, end)
    bulk_viol   = get_device_violations(tracked_ids, start, end)
    rows = []
    for dev in tracked_devices:
        try:
            up, raw_incidents, cov_meta = bulk_uptime.get(dev.id, (None, [], {}))
            merged, _ = _merge_flapping_incidents(raw_incidents)
            mttr, _   = _mttr_mtbf(merged)
            viol      = bulk_viol.get(dev.id, {})
            obs_s = cov_meta.get("observed_seconds", 0)
            obs_h = _safe_round(obs_s / 3600.0) if obs_s else None
            row = {
                "device_id":           dev.id,
                "device_name":         dev.device_name or dev.hostname or "—",
                "device_ip":           dev.ip_address or "—",
                "fleet":               "workstation",
                "uptime_pct":          up,
                "downtime_hours":      downtime_hours(up, period_hours,
                                                      observed_hours=obs_h),
                "sla_tier":            sla_tier(up),
                "avg_latency_ms":      None,
                "max_latency_ms":      None,
                "avg_packet_loss_pct": None,
                "timeout_count":       None,
                "incident_count":      len(merged),
                "mttr_min":            mttr,
                "violation_count":     viol.get("violation_count", 0),
                "has_violation":       viol.get("has_violation", False),
                "last_violation_time": viol.get("last_violation_time"),
                "monitoring_coverage_pct": cov_meta.get("monitoring_coverage_pct"),
                "observed_hours":         obs_h,
                "coverage_level":         coverage_level(cov_meta.get("monitoring_coverage_pct")),
            }
            row["anomaly_flag"], row["anomaly_reason"] = _detect_anomaly(row)
        except Exception as exc:
            logger.warning("[CoreMetrics] workstation device_id=%s skipped: %s", dev.id, exc)
            continue
        rows.append(row)
    return rows
