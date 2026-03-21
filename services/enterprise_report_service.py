"""
Enterprise uptime/downtime report service.

Aggregates availability data from both the inventory device fleet (server_agent.py targets)
and the tracked employee device fleet (service.py targets).  Returns a structured dict
consumed by enterprise_pdf_service.generate_enterprise_pdf().

fleet parameter:
  None         — both fleets (legacy behaviour)
  "server"     — inventory/server devices only (tracked_rows will be [])
  "workstation"— tracked/employee devices only (server_rows will be [])
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, text

from extensions import db
from models.compliance_profile import ComplianceProfile
from models.dashboard import DailyDeviceStats
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.server_health_rollups import ServerHealthHourlyRollup, ServerHealthDailyRollup
from models.restricted_site_policy import RestrictedSiteEvent
from models.tracked_device import (
    TrackedDevice, TrackedDeviceAvailabilityEvent,
    TrackingDailyRollup, TrackingHourlyRollup,
    DeviceApplicationLog, DeviceActivityLog,
)

logger = logging.getLogger(__name__)

# ── SLA tier thresholds (%) ─────────────────────────────────────────────────
SLA_GOLD = 99.9
SLA_SILVER = 99.5
SLA_BRONZE = 99.0
SLA_WARNING = 95.0

_VALID_FLEETS = (None, "server", "workstation")


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


def _bulk_load_sla_thresholds(device_profile_map: Dict[int, Optional[int]]) -> Tuple[Dict[int, Dict[str, float]], Dict[str, int]]:
    """Bulk-load ComplianceProfile SLA thresholds for a set of devices.

    Returns a tuple:
      - {device_id: {sla_gold: X, sla_silver: Y, ...}} for devices with custom SLA keys
      - {profile_name: device_count} summary of profiles in use

    Devices without a profile or without SLA keys in rules_json are not
    in the first dict (callers should treat missing entries as "use defaults").
    """
    profile_ids = set(pid for pid in device_profile_map.values() if pid is not None)
    if not profile_ids:
        return {}, {}
    profile_objs = ComplianceProfile.query.filter(ComplianceProfile.id.in_(profile_ids)).all()
    profiles = {p.id: (p.name, p.rules_json or {}) for p in profile_objs}
    sla_keys = {"sla_gold", "sla_silver", "sla_bronze", "sla_warning"}
    result: Dict[int, Dict[str, float]] = {}
    profile_usage: Dict[str, int] = {}
    for device_id, profile_id in device_profile_map.items():
        if profile_id is None or profile_id not in profiles:
            continue
        name, rules = profiles[profile_id]
        sla_overrides = {k: float(v) for k, v in rules.items() if k in sla_keys and v is not None}
        if sla_overrides:
            result[device_id] = sla_overrides
            profile_usage[name] = profile_usage.get(name, 0) + 1
    return result, profile_usage


def downtime_hours(uptime_pct: Optional[float], period_hours: float) -> Optional[float]:
    if uptime_pct is None:
        return None
    frac = max(0.0, 1.0 - uptime_pct / 100.0)
    return _safe_round(frac * period_hours)


# ── Uptime calculators ────────────────────────────────────────────────────────

def _inventory_uptime(device_ip: Optional[str], device_id: int,
                      start: datetime, end: datetime) -> Optional[float]:
    """
    Uptime % for an inventory device over [start, end].
    Primary: DailyDeviceStats aggregates (fast).
    Fallback: raw DeviceScanHistory by IP.
    """
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

    # Fallback — scan history keyed by IP
    if not device_ip:
        return None
    total = (
        db.session.query(func.count(DeviceScanHistory.scan_id))
        .filter(
            DeviceScanHistory.device_ip == device_ip,
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
            DeviceScanHistory.device_ip == device_ip,
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
            func.lower(DeviceScanHistory.status) == "online",
        )
        .scalar()
        or 0
    )
    return _safe_round((online / total) * 100.0)


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
) -> Tuple[Optional[float], List[dict]]:
    """
    Pure state-machine helper: derive uptime % and incident list from a
    pre-fetched list of availability event objects.

    Each object must expose `.status` (str) and `.observed_at` (datetime).
    Returns (uptime_pct, incidents).  Both are None / [] when there is no data
    or when the time window is degenerate.
    """
    if not events:
        return None, []

    period_seconds = (end - start).total_seconds()
    if period_seconds <= 0:
        return None, []

    incidents: List[dict] = []
    offline_since: Optional[datetime] = None

    for ev in events:
        status = (ev.status or "").lower()
        if status in ("offline", "degraded") and offline_since is None:
            offline_since = ev.observed_at
        elif status == "online" and offline_since is not None:
            dur_min = _safe_round((ev.observed_at - offline_since).total_seconds() / 60.0)
            incidents.append({"start": offline_since.isoformat(), "end": ev.observed_at.isoformat(),
                               "duration_min": dur_min})
            offline_since = None

    # Open-ended incident still in progress at window end
    if offline_since is not None:
        dur_min = _safe_round((end - offline_since).total_seconds() / 60.0)
        incidents.append({"start": offline_since.isoformat(), "end": end.isoformat(),
                           "duration_min": dur_min, "open": True})

    total_down_s = sum(inc["duration_min"] * 60 for inc in incidents)
    uptime_pct = _safe_round(max(0.0, 1.0 - total_down_s / period_seconds) * 100.0)
    return uptime_pct, incidents


def _tracked_uptime_and_incidents(
    device_id: int, start: datetime, end: datetime
) -> Tuple[Optional[float], List[dict]]:
    """
    Uptime % and incident list for a tracked device over [start, end].
    Derived from TrackedDeviceAvailabilityEvent stream.
    """
    events = (
        TrackedDeviceAvailabilityEvent.query
        .filter(
            TrackedDeviceAvailabilityEvent.device_id == device_id,
            TrackedDeviceAvailabilityEvent.observed_at >= start,
            TrackedDeviceAvailabilityEvent.observed_at <= end,
        )
        .order_by(TrackedDeviceAvailabilityEvent.observed_at.asc())
        .all()
    )
    return _compute_uptime_from_events(events, start, end)


def _bulk_uptime_and_incidents(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> "Dict[int, Tuple[Optional[float], List[dict]]]":
    """Bulk version: one query for all device IDs, groups in Python.
    Returns {device_id: (uptime_pct, incidents)} for every id in device_ids.
    Devices with no events get (None, []).
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


def _compute_focus_score(device_id: int, start: datetime, end: datetime) -> Optional[float]:
    """
    Compute Focus Score (0–100) from DeviceActivityLog rows (60s cadence).

    Prefers the dedicated current_application column (Option A, fast).
    Falls back to parsing details JSON if the column is NULL / missing.
    Streak duration measured from actual timestamps — handles dropped samples.
    Score = sum(top-3 streaks ≥ 25 min) / total_window_minutes × 100.

    Input window capped at 7 days regardless of report range to bound memory
    usage (43,200 rows/device for 30-day reports is too large).
    """
    import json
    focus_start = max(start, end - timedelta(days=7))
    rows = (
        db.session.query(
            DeviceActivityLog.timestamp,
            DeviceActivityLog.current_application,
            DeviceActivityLog.details,
        )
        .filter(
            DeviceActivityLog.device_id == device_id,
            DeviceActivityLog.timestamp >= focus_start,
            DeviceActivityLog.timestamp <= end,
        )
        .order_by(DeviceActivityLog.timestamp.asc())
        .all()
    )
    if not rows:
        return None

    streaks: List[float] = []
    current_app: Optional[str] = None
    streak_start_ts: Optional[datetime] = None
    prev_ts: Optional[datetime] = None

    for ts, col_app, details_json in rows:
        app = col_app or ""
        if not app and details_json:
            try:
                app = json.loads(details_json).get("current_application") or ""
            except Exception:
                app = ""

        if app and app == current_app:
            prev_ts = ts
        else:
            if current_app and streak_start_ts and prev_ts:
                dur_min = (prev_ts - streak_start_ts).total_seconds() / 60.0
                if dur_min >= 25:
                    streaks.append(dur_min)
            current_app = app
            streak_start_ts = ts
            prev_ts = ts

    # Close final open streak
    if current_app and streak_start_ts and prev_ts:
        dur_min = (prev_ts - streak_start_ts).total_seconds() / 60.0
        if dur_min >= 25:
            streaks.append(dur_min)

    if not streaks:
        # Activity rows exist but no streak reached 25 min — distinguish from no-data (None).
        # Return None so the UI renders "--" rather than "0", which implies zero focus work.
        return None
    total_minutes = max(1.0, (end - start).total_seconds() / 60.0)
    return _safe_round(sum(sorted(streaks, reverse=True)[:3]) / total_minutes * 100.0, 1)


def _workstation_behavioral_metrics(
    device_id: int,
    start: datetime,
    end: datetime,
    category_cache: Optional[Dict[str, str]] = None,
) -> dict:
    """
    Aggregate behavioral KPIs for a tracked device over [start, end]:
      - keyboard/mouse/active from TrackingDailyRollup (COALESCE at DB level)
      - top app from DeviceApplicationLog
      - policy violation count from RestrictedSiteEvent (via unique_client_id)
      - productivity score from app usage × category weights
      - focus score from DeviceActivityLog consecutive same-app streaks

    Returns a dict with all keys; values are None when no data exists.
    Classification is read from AppCategoryCache only — never triggers API calls here.

    category_cache: pre-loaded {app_name: category} dict (avoids N+1 when called in
    a loop over many devices). If None, loads from DB on each call.
    """

    # ── Category weights for productivity score ───────────────────────────────
    CATEGORY_WEIGHTS: Dict[str, float] = {
        "Development":   1.0,
        "Productivity":  0.9,
        "Communication": 0.8,
        "Browser":       0.5,
        "Utility":       0.5,
        "Entertainment": 0.1,
        "Unknown":       0.5,
    }

    # ── Daily rollup aggregates ───────────────────────────────────────────────
    row = (
        db.session.query(
            func.coalesce(func.sum(TrackingDailyRollup.keyboard_events), 0).label("kb"),
            func.coalesce(func.sum(TrackingDailyRollup.mouse_events), 0).label("ms"),
            func.coalesce(func.sum(TrackingDailyRollup.active_seconds), 0).label("active_s"),
            func.avg(TrackingDailyRollup.cpu_avg).label("cpu"),
        )
        .filter(
            TrackingDailyRollup.device_id == device_id,
            TrackingDailyRollup.bucket_day >= start.date(),
            TrackingDailyRollup.bucket_day <= end.date(),
        )
        .first()
    )
    has_rollup = row is not None and (row.kb > 0 or row.ms > 0 or row.active_s > 0)

    # ── Top app + app rows for productivity score ─────────────────────────────
    app_rows = (
        db.session.query(
            DeviceApplicationLog.application_name,
            func.coalesce(func.sum(DeviceApplicationLog.duration), 0).label("total_s"),
        )
        .filter(
            DeviceApplicationLog.device_id == device_id,
            DeviceApplicationLog.timestamp >= start,
            DeviceApplicationLog.timestamp <= end,
        )
        .group_by(DeviceApplicationLog.application_name)
        .order_by(func.sum(DeviceApplicationLog.duration).desc())
        .all()
    )
    top_app = app_rows[0].application_name if app_rows else None

    # ── Productivity score (read AppCategoryCache — no API calls) ─────────────
    productivity_score: Optional[float] = None
    if app_rows:
        if category_cache is None:
            from models.app_category_cache import AppCategoryCache
            category_cache = {r.app_name: r.category for r in AppCategoryCache.query.all()}
        # Return None (not 0.0 or 50.0) when cache is empty — no data to score
        if category_cache:
            total_s = sum(r.total_s for r in app_rows) or 1
            weighted = sum(
                r.total_s * CATEGORY_WEIGHTS.get(category_cache.get(r.application_name, "Unknown"), 0.5)
                for r in app_rows
            )
            productivity_score = _safe_round((weighted / total_s) * 100.0, 1)

    # ── Policy violation count via unique_client_id → agent_key_id ────────────
    violation_count: Optional[int] = None
    try:
        dev = TrackedDevice.query.get(device_id)
        if dev and getattr(dev, "unique_client_id", None):
            violation_count = (
                db.session.query(func.count(RestrictedSiteEvent.id))
                .filter(
                    RestrictedSiteEvent.agent_key_id == dev.unique_client_id,
                    RestrictedSiteEvent.observed_at_utc >= start,
                    RestrictedSiteEvent.observed_at_utc <= end,
                )
                .scalar()
            ) or 0
    except Exception as exc:
        logger.warning("[EnterpriseReport] violation count query failed for device %s: %s", device_id, exc)

    # ── Focus score ───────────────────────────────────────────────────────────
    focus_score = _compute_focus_score(device_id, start, end)

    days = max(1, (end.date() - start.date()).days)
    return {
        "total_keyboard_events": int(row.kb) if has_rollup else None,
        "total_mouse_events":    int(row.ms) if has_rollup else None,
        "total_active_hours":    _safe_round(row.active_s / 3600.0, 1) if has_rollup else None,
        "avg_active_hours_day":  _safe_round(row.active_s / 3600.0 / days, 1) if has_rollup else None,
        "avg_cpu_during_active": _safe_round(row.cpu, 1) if (has_rollup and row.cpu) else None,
        "top_app":               top_app,
        "policy_violations":     violation_count,
        "productivity_score":    productivity_score,
        "focus_score":           focus_score,
    }


def _fleet_violation_summary(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> List[dict]:
    """
    Fleet-wide website policy violation breakdown grouped by (device, domain).

    Returns a flat list of dicts ordered by violation_count DESC, capped at 500.
    Each dict: {device_id, device_name, employee_name, domain, violation_count, last_violation}.

    # cf. reporting_service.py:1479 — similar query for operational summary
    """
    if not device_ids:
        return []
    try:
        rows = (
            db.session.query(
                TrackedDevice.id.label("device_id"),
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                RestrictedSiteEvent.domain,
                func.count(RestrictedSiteEvent.id).label("violation_count"),
                func.max(RestrictedSiteEvent.observed_at_utc).label("last_violation"),
            )
            .join(TrackedDevice, TrackedDevice.id == RestrictedSiteEvent.device_id)
            .filter(
                RestrictedSiteEvent.device_id.in_(device_ids),
                RestrictedSiteEvent.observed_at_utc >= start,
                RestrictedSiteEvent.observed_at_utc <= end,
            )
            .group_by(
                TrackedDevice.id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                RestrictedSiteEvent.domain,
            )
            .order_by(func.count(RestrictedSiteEvent.id).desc())
            .limit(500)
            .all()
        )
        return [
            {
                "device_id": r.device_id,
                "device_name": r.device_name or "—",
                "employee_name": r.employee_name or "—",
                "domain": r.domain,
                "violation_count": int(r.violation_count),
                "last_violation": r.last_violation.isoformat() if r.last_violation else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("[EnterpriseReport] _fleet_violation_summary failed: %s", exc)
        return []


def _server_metrics_bulk(device_ids: list, start: datetime, end: datetime) -> dict:
    """
    Returns {device_id: metrics_dict} in ONE bulk query (not N+1).
    Routes to the correct data tier based on period length:
      ≤24h  → raw server_health_logs (source='agent')
      ≤30d  → server_health_hourly_rollups
      >30d  → server_health_daily_rollups
    """
    if not device_ids:
        return {}

    period_hours = (end - start).total_seconds() / 3600.0
    if period_hours <= 24:
        tier = 'raw'
    elif period_hours <= 720:   # 30d
        tier = 'hourly'
    else:
        tier = 'daily'

    result: dict = {}

    if tier == 'raw':
        rows = (
            db.session.query(
                ServerHealthLog.device_id,
                func.avg(ServerHealthLog.cpu_usage).label("avg_cpu"),
                func.max(ServerHealthLog.cpu_usage).label("max_cpu"),
                func.avg(ServerHealthLog.memory_usage).label("avg_mem"),
                func.max(ServerHealthLog.memory_usage).label("max_mem"),
                func.avg(ServerHealthLog.disk_usage).label("avg_disk"),
                func.max(ServerHealthLog.disk_usage).label("max_disk"),
                func.avg(ServerHealthLog.load_avg_1min).label("avg_load"),
                func.avg(ServerHealthLog.network_in_bps).label("avg_net_in"),
                func.avg(ServerHealthLog.network_out_bps).label("avg_net_out"),
                func.avg(ServerHealthLog.disk_read_latency_ms).label("avg_disk_r"),
                func.avg(ServerHealthLog.disk_write_latency_ms).label("avg_disk_w"),
                func.count(ServerHealthLog.id).label("n"),
            )
            .filter(
                ServerHealthLog.device_id.in_(device_ids),
                ServerHealthLog.source == 'agent',
                ServerHealthLog.timestamp >= start,
                ServerHealthLog.timestamp <= end,
            )
            .group_by(ServerHealthLog.device_id)
            .all()
        )
        for row in rows:
            if not row.n:
                continue
            result[row.device_id] = {
                "avg_cpu":           _safe_round(row.avg_cpu)     if row.avg_cpu     is not None else None,
                "max_cpu":           _safe_round(row.max_cpu)     if row.max_cpu     is not None else None,
                "avg_mem":           _safe_round(row.avg_mem)     if row.avg_mem     is not None else None,
                "max_mem":           _safe_round(row.max_mem)     if row.max_mem     is not None else None,
                "avg_disk":          _safe_round(row.avg_disk)    if row.avg_disk    is not None else None,
                "max_disk":          _safe_round(row.max_disk)    if row.max_disk    is not None else None,
                "avg_load_1m":       _safe_round(row.avg_load)    if row.avg_load    is not None else None,
                "avg_net_in_bps":    _safe_round(row.avg_net_in)  if row.avg_net_in  is not None else None,
                "avg_net_out_bps":   _safe_round(row.avg_net_out) if row.avg_net_out is not None else None,
                "avg_disk_read_ms":  _safe_round(row.avg_disk_r)  if row.avg_disk_r  is not None else None,
                "avg_disk_write_ms": _safe_round(row.avg_disk_w)  if row.avg_disk_w  is not None else None,
                "sample_count":      row.n,
                "source_tier":       "raw",
            }

    elif tier == 'hourly':
        rows = (
            db.session.query(
                ServerHealthHourlyRollup.device_id,
                func.avg(ServerHealthHourlyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthHourlyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthHourlyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthHourlyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthHourlyRollup.avg_disk_usage).label("avg_disk"),
                func.avg(ServerHealthHourlyRollup.avg_network_in_bps).label("avg_net_in"),
                func.avg(ServerHealthHourlyRollup.avg_network_out_bps).label("avg_net_out"),
                func.sum(ServerHealthHourlyRollup.sample_count).label("total_n"),
            )
            .filter(
                ServerHealthHourlyRollup.device_id.in_(device_ids),
                ServerHealthHourlyRollup.source == 'agent',
                ServerHealthHourlyRollup.bucket_hour >= start,
                ServerHealthHourlyRollup.bucket_hour <= end,
            )
            .group_by(ServerHealthHourlyRollup.device_id)
            .all()
        )
        for row in rows:
            n = row.total_n or 0
            if not n:
                continue
            result[row.device_id] = {
                "avg_cpu":           _safe_round(row.avg_cpu)     if row.avg_cpu     is not None else None,
                "max_cpu":           _safe_round(row.max_cpu)     if row.max_cpu     is not None else None,
                "avg_mem":           _safe_round(row.avg_mem)     if row.avg_mem     is not None else None,
                "max_mem":           _safe_round(row.max_mem)     if row.max_mem     is not None else None,
                "avg_disk":          _safe_round(row.avg_disk)    if row.avg_disk    is not None else None,
                "max_disk":          None,  # not in hourly rollup
                "avg_load_1m":       None,  # not in hourly rollup
                "avg_net_in_bps":    _safe_round(row.avg_net_in)  if row.avg_net_in  is not None else None,
                "avg_net_out_bps":   _safe_round(row.avg_net_out) if row.avg_net_out is not None else None,
                "avg_disk_read_ms":  None,  # not in hourly rollup
                "avg_disk_write_ms": None,  # not in hourly rollup
                "sample_count":      n,
                "source_tier":       "hourly",
            }

    else:  # daily (>30d)
        rows = (
            db.session.query(
                ServerHealthDailyRollup.device_id,
                func.avg(ServerHealthDailyRollup.avg_cpu_usage).label("avg_cpu"),
                func.max(ServerHealthDailyRollup.max_cpu_usage).label("max_cpu"),
                func.avg(ServerHealthDailyRollup.avg_memory_usage).label("avg_mem"),
                func.max(ServerHealthDailyRollup.max_memory_usage).label("max_mem"),
                func.avg(ServerHealthDailyRollup.avg_disk_usage).label("avg_disk"),
                func.avg(ServerHealthDailyRollup.avg_network_in_bps).label("avg_net_in"),
                func.avg(ServerHealthDailyRollup.avg_network_out_bps).label("avg_net_out"),
                func.sum(ServerHealthDailyRollup.sample_count).label("total_n"),
            )
            .filter(
                ServerHealthDailyRollup.device_id.in_(device_ids),
                ServerHealthDailyRollup.source == 'agent',
                ServerHealthDailyRollup.bucket_day >= start.date(),
                ServerHealthDailyRollup.bucket_day <= end.date(),
            )
            .group_by(ServerHealthDailyRollup.device_id)
            .all()
        )
        for row in rows:
            n = row.total_n or 0
            if not n:
                continue
            result[row.device_id] = {
                "avg_cpu":           _safe_round(row.avg_cpu)     if row.avg_cpu     is not None else None,
                "max_cpu":           _safe_round(row.max_cpu)     if row.max_cpu     is not None else None,
                "avg_mem":           _safe_round(row.avg_mem)     if row.avg_mem     is not None else None,
                "max_mem":           _safe_round(row.max_mem)     if row.max_mem     is not None else None,
                "avg_disk":          _safe_round(row.avg_disk)    if row.avg_disk    is not None else None,
                "max_disk":          None,  # not in daily rollup
                "avg_load_1m":       None,  # not in daily rollup
                "avg_net_in_bps":    _safe_round(row.avg_net_in)  if row.avg_net_in  is not None else None,
                "avg_net_out_bps":   _safe_round(row.avg_net_out) if row.avg_net_out is not None else None,
                "avg_disk_read_ms":  None,  # not in daily rollup
                "avg_disk_write_ms": None,  # not in daily rollup
                "sample_count":      n,
                "source_tier":       "daily",
            }

    return result


# ── Main builder ─────────────────────────────────────────────────────────────

def build_enterprise_uptime_report(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    fleet: Optional[str] = None,
    device_ids: Optional[List[int]] = None,
) -> dict:
    """
    Build the enterprise uptime/downtime report.

    fleet:
      None         — both server and workstation fleets (default)
      "server"     — inventory devices only; tracked_rows will be []
      "workstation"— tracked/employee devices only; server_rows will be []

    device_ids:
      Optional list of TrackedDevice IDs to restrict the workstation fleet.
      When None (default) all non-archived tracked devices are included.

    Returns a dict with keys:
      period, summary, server_rows, tracked_rows, generated_at
    """
    if fleet not in _VALID_FLEETS:
        raise ValueError(f"Invalid fleet value: {fleet!r}. Must be one of {_VALID_FLEETS}")

    end_dt = end_date or datetime.utcnow()
    start_dt = start_date or (end_dt - timedelta(days=30))
    period_hours = (end_dt - start_dt).total_seconds() / 3600.0

    # ── Inventory / Server fleet (server_agent.py devices) ───────────────────
    server_rows: List[dict] = []
    sla_profiles_used: Dict[str, int] = {}   # {profile_name: device_count}
    if fleet in (None, "server"):
        inv_devices = (
            Device.query
            .filter(Device.is_active.isnot(False))
            .order_by(Device.device_name.asc())
            .all()
        )
        inv_device_ids = [dev.device_id for dev in inv_devices]
        all_metrics = _server_metrics_bulk(inv_device_ids, start_dt, end_dt)

        # Bulk-load per-device SLA thresholds from ComplianceProfile
        inv_profile_map = {
            dev.device_id: getattr(dev, "compliance_profile_id", None)
            for dev in inv_devices
        }
        inv_sla_thresholds = _bulk_load_sla_thresholds(inv_profile_map)

        for dev in inv_devices:
            try:
                up = _inventory_uptime(dev.device_ip, dev.device_id, start_dt, end_dt)
                metrics = all_metrics.get(dev.device_id, {})
                net_stats = _inventory_network_stats(dev.device_id, start_dt, end_dt)
                dev_thresholds = inv_sla_thresholds.get(dev.device_id)
                tier = sla_tier(up, dev_thresholds)
                server_rows.append({
                    "device_id": dev.device_id,
                    "device_name": dev.device_name or f"Device-{dev.device_ip}",
                    "device_ip": dev.device_ip or "—",
                    "device_type": dev.device_type or "Unknown",
                    "uptime_pct": up,
                    "downtime_hours": downtime_hours(up, period_hours),
                    "sla_tier": tier,
                    # ServerHealthLog / rollup metrics
                    "avg_cpu": metrics.get("avg_cpu"),
                    "max_cpu": metrics.get("max_cpu"),
                    "avg_mem": metrics.get("avg_mem"),
                    "max_mem": metrics.get("max_mem"),
                    "avg_disk": metrics.get("avg_disk"),
                    "max_disk": metrics.get("max_disk"),
                    "avg_load_1m": metrics.get("avg_load_1m"),
                    "avg_net_in_bps": metrics.get("avg_net_in_bps"),
                    "avg_net_out_bps": metrics.get("avg_net_out_bps"),
                    "avg_disk_read_ms": metrics.get("avg_disk_read_ms"),
                    "avg_disk_write_ms": metrics.get("avg_disk_write_ms"),
                    "sample_count": metrics.get("sample_count", 0),
                    "data_source": metrics.get("source_tier", "unknown"),
                    # DailyDeviceStats network quality
                    "avg_latency_ms": net_stats.get("avg_latency_ms"),
                    "max_latency_ms": net_stats.get("max_latency_ms"),
                    "avg_packet_loss_pct": net_stats.get("avg_packet_loss_pct"),
                    "total_alerts": net_stats.get("total_alerts", 0),
                    "sla_thresholds": "custom" if dev_thresholds else "default",
                })
            except Exception as exc:
                logger.warning("[EnterpriseReport] server device_id=%s error=%s", dev.device_id, exc)

    # ── Tracked / Employee fleet (service.py devices) ────────────────────────
    tracked_rows: List[dict] = []
    if fleet in (None, "workstation"):
        # Pre-load AppCategoryCache once — avoids N+1 (one query per device)
        try:
            from models.app_category_cache import AppCategoryCache
            _category_cache: Optional[Dict[str, str]] = {
                r.app_name: r.category for r in AppCategoryCache.query.all()
            }
        except Exception:
            _category_cache = None

        td_query = (
            TrackedDevice.query
            .filter(TrackedDevice.is_archived.isnot(True))
            .order_by(TrackedDevice.device_name.asc())
        )
        if device_ids:
            td_query = td_query.filter(TrackedDevice.id.in_(device_ids))
        tracked_devices = td_query.all()

        # One bulk availability query instead of N per-device queries.
        # Apply a 5-second statement timeout to guard against table scans on
        # large event histories.
        if db.engine.url.get_backend_name() == 'postgresql':
            db.session.execute(text("SET LOCAL statement_timeout = '5000'"))
        tracked_ids = [dev.id for dev in tracked_devices]
        bulk_uptime = _bulk_uptime_and_incidents(tracked_ids, start_dt, end_dt)

        for dev in tracked_devices:
            try:
                up, incidents = bulk_uptime.get(dev.id, (None, []))
                mttr, mtbf = _mttr_mtbf(incidents)
                tier = sla_tier(up)
                beh = _workstation_behavioral_metrics(dev.id, start_dt, end_dt, _category_cache)
                tracked_rows.append({
                    "device_id": dev.id,
                    "device_name": dev.device_name or dev.hostname or dev.mac_address,
                    "employee_name": dev.employee_name or "—",
                    "device_ip": dev.ip_address or "—",
                    "hostname": dev.hostname or "—",
                    "department": getattr(dev, "department", None) or "—",
                    "probe_method": getattr(dev, "probe_method", None) or "—",
                    "last_agent_sync_at": (
                        dev.last_agent_sync_at.isoformat()
                        if getattr(dev, "last_agent_sync_at", None) else None
                    ),
                    "uptime_pct": up,
                    "downtime_hours": downtime_hours(up, period_hours),
                    "sla_tier": tier,
                    "incident_count": len(incidents),
                    "mttr_min": mttr,
                    "mtbf_hours": mtbf,
                    "last_seen": dev.last_seen.isoformat() if dev.last_seen else None,
                    "availability_status": dev.availability_status or "unknown",
                    "data_source": "availability_events" if up is not None else "unknown",
                    # Behavioral fields
                    "total_keyboard_events": beh.get("total_keyboard_events"),
                    "total_mouse_events":    beh.get("total_mouse_events"),
                    "total_active_hours":    beh.get("total_active_hours"),
                    "avg_active_hours_day":  beh.get("avg_active_hours_day"),
                    "avg_cpu_during_active": beh.get("avg_cpu_during_active"),
                    "top_app":               beh.get("top_app"),
                    "policy_violations":     beh.get("policy_violations"),
                    "productivity_score":    beh.get("productivity_score"),
                    "focus_score":           beh.get("focus_score"),
                })
            except Exception as exc:
                logger.warning("[EnterpriseReport] tracked device_id=%s error=%s", dev.id, exc)

    # ── Website violation detail breakdown (workstation fleet only) ───────────
    website_violation_details: List[dict] = []
    if fleet in (None, "workstation") and tracked_ids:
        website_violation_details = _fleet_violation_summary(tracked_ids, start_dt, end_dt)

    # ── Fleet resource averages (server fleet only, agent-equipped devices) ──
    agent_server_rows = [r for r in server_rows if (r.get("sample_count") or 0) > 0]

    def _fleet_avg(key: str) -> Optional[float]:
        vals = [r[key] for r in agent_server_rows if r.get(key) is not None]
        return _safe_round(sum(vals) / len(vals)) if vals else None

    # ── Executive summary ─────────────────────────────────────────────────────
    all_rows = server_rows + tracked_rows
    rows_with_data = [r for r in all_rows if r["uptime_pct"] is not None]
    fleet_avg = (
        _safe_round(sum(r["uptime_pct"] for r in rows_with_data) / len(rows_with_data))
        if rows_with_data else None
    )

    sla_dist: Dict[str, int] = {"Gold": 0, "Silver": 0, "Bronze": 0,
                                 "Warning": 0, "Critical": 0, "Unknown": 0}
    for row in all_rows:
        key = row["sla_tier"]
        sla_dist[key] = sla_dist.get(key, 0) + 1

    worst_five = sorted(rows_with_data, key=lambda r: r["uptime_pct"])[:5]
    best_three = sorted(rows_with_data, key=lambda r: r["uptime_pct"], reverse=True)[:3]

    logger.info("[EnterpriseReport] Built report: fleet=%s, servers=%d, tracked=%d, tier=%s",
                fleet, len(server_rows), len(tracked_rows),
                sla_tier(fleet_avg) if fleet_avg is not None else "N/A")

    # ── Per-row data source tracking for confidence ──────────────────────
    server_data_sources = set()
    tracked_data_sources = set()
    for r in server_rows:
        server_data_sources.add(r.get("data_source", "unknown"))
    for r in tracked_rows:
        tracked_data_sources.add(r.get("data_source", "unknown"))

    def _confidence_level(sources: set) -> str:
        if not sources or sources == {"unknown"}:
            return "NO_DATA"
        if "rollup" in sources or "daily_rollup" in sources or "hourly_rollup" in sources:
            return "HIGH"
        if "raw" in sources or "availability_events" in sources or "scan_history" in sources:
            return "MEDIUM"
        return "LOW"

    _confidence = {
        "fleet_avg_uptime": {
            "level": "HIGH" if rows_with_data else "NO_DATA",
            "source": "computed_from_device_rows",
        },
        "server_fleet": {
            "level": _confidence_level(server_data_sources),
            "source": ", ".join(sorted(server_data_sources)) if server_data_sources else None,
        },
        "tracked_fleet": {
            "level": _confidence_level(tracked_data_sources),
            "source": ", ".join(sorted(tracked_data_sources)) if tracked_data_sources else None,
        },
    }

    return {
        "period": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "days": round((end_dt - start_dt).days),
            "hours": _safe_round(period_hours),
        },
        "summary": {
            "total_devices": len(all_rows),
            "server_devices": len(server_rows),
            "tracked_devices": len(tracked_rows),
            "devices_with_data": len(rows_with_data),
            "fleet_avg_uptime": fleet_avg,
            "fleet_avg_cpu":    _fleet_avg("avg_cpu"),
            "fleet_avg_mem":    _fleet_avg("avg_mem"),
            "fleet_avg_disk":   _fleet_avg("avg_disk"),
            "agent_deployed_count": len(agent_server_rows),
            "sla_distribution": sla_dist,
            "worst_devices": worst_five,
            "best_devices": best_three,
        },
        "server_rows": server_rows,
        "tracked_rows": tracked_rows,
        "website_violation_details": website_violation_details,
        "generated_at": datetime.utcnow().isoformat(),
        "_confidence": _confidence,
    }
