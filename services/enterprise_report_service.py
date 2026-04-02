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
from models.typed_text_policy_alert import TypedTextPolicyAlert
from models.tracked_device import (
    TrackedDevice, TrackedDeviceAvailabilityEvent,
    TrackingDailyRollup, TrackingHourlyRollup,
    DeviceApplicationLog, DeviceActivityLog,
)

logger = logging.getLogger(__name__)

# Re-export all shared functions/constants from core_metrics_service so that
# existing callers (tests, routes) can continue to import them from here.
from services.core_metrics_service import (  # noqa: E402
    _safe_round, sla_tier, downtime_hours, coverage_level,
    _inventory_uptime, _count_no_response_scans, _inventory_network_stats,
    _compute_uptime_from_events, _bulk_uptime_and_incidents,
    _merge_flapping_incidents, _mttr_mtbf, _detect_anomaly,
    get_device_violations, _bulk_icmp_coverage,
    SLA_GOLD, SLA_SILVER, SLA_BRONZE, SLA_WARNING,
    ANOMALY_LATENCY_MS, ANOMALY_PACKET_LOSS_PCT, ANOMALY_UPTIME_PCT,
    ANOMALY_VIOLATION_COUNT,
    FLAP_MERGE_GAP_S, MIN_INCIDENT_DURATION_S, MAX_INCIDENT_DURATION_H,
    GAP_THRESHOLD_S,
)

_VALID_FLEETS = (None, "server", "workstation")


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
            total_s = sum(r.total_s for r in app_rows)
            if total_s == 0:
                # App rows exist but all have zero duration — no meaningful score
                productivity_score = None
            else:
                weighted = sum(
                    r.total_s * CATEGORY_WEIGHTS.get(category_cache.get(r.application_name, "Unknown"), 0.5)
                    for r in app_rows
                )
                productivity_score = _safe_round((weighted / total_s) * 100.0, 1)

    # ── Policy violation count via unique_client_id → agent_key_id ────────────
    violation_count: Optional[int] = None
    typed_text_count: Optional[int] = None
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
        # Typed text alerts are keyed by device_id directly
        typed_text_count = (
            db.session.query(func.count(TypedTextPolicyAlert.id))
            .filter(
                TypedTextPolicyAlert.device_id == device_id,
                TypedTextPolicyAlert.detected_at >= start,
                TypedTextPolicyAlert.detected_at <= end,
            )
            .scalar()
        ) or 0
    except Exception as exc:
        logger.warning("[EnterpriseReport] violation count query failed for device %s: %s", device_id, exc)

    # Weighted violation score: site violations (weight 5) + typed text (weight 10)
    violation_score: Optional[int] = None
    if violation_count is not None or typed_text_count is not None:
        violation_score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10

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
        "typed_text_alerts":     typed_text_count,
        "violation_score":       violation_score,
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


def _fleet_typed_text_violations(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> List[dict]:
    """Fleet-wide typed-text policy alert breakdown grouped by (device, pattern_type, severity).

    Returns a flat list of dicts ordered by alert_count DESC, capped at 500.
    """
    if not device_ids:
        return []
    try:
        rows = (
            db.session.query(
                TrackedDevice.id.label("device_id"),
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                TypedTextPolicyAlert.pattern_type,
                TypedTextPolicyAlert.severity,
                func.count(TypedTextPolicyAlert.id).label("alert_count"),
                func.max(TypedTextPolicyAlert.detected_at).label("last_detected"),
            )
            .join(TrackedDevice, TrackedDevice.id == TypedTextPolicyAlert.device_id)
            .filter(
                TypedTextPolicyAlert.device_id.in_(device_ids),
                TypedTextPolicyAlert.detected_at >= start,
                TypedTextPolicyAlert.detected_at <= end,
            )
            .group_by(
                TrackedDevice.id,
                TrackedDevice.device_name,
                TrackedDevice.employee_name,
                TypedTextPolicyAlert.pattern_type,
                TypedTextPolicyAlert.severity,
            )
            .order_by(func.count(TypedTextPolicyAlert.id).desc())
            .limit(500)
            .all()
        )
        return [
            {
                "device_id": r.device_id,
                "device_name": r.device_name or "—",
                "employee_name": r.employee_name or "—",
                "pattern_type": r.pattern_type,
                "severity": r.severity,
                "alert_count": int(r.alert_count),
                "last_detected": r.last_detected.isoformat() if r.last_detected else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("[EnterpriseReport] _fleet_typed_text_violations failed: %s", exc)
        return []


def _build_violation_trend(
    device_ids: List[int],
    start: datetime,
    end: datetime,
) -> List[dict]:
    """Daily violation counts for trend chart."""
    if not device_ids:
        return []
    try:
        # Restricted site events by day
        site_rows = (
            db.session.query(
                func.date(RestrictedSiteEvent.observed_at_utc).label("day"),
                func.count(RestrictedSiteEvent.id).label("count"),
            )
            .filter(
                RestrictedSiteEvent.device_id.in_(device_ids),
                RestrictedSiteEvent.observed_at_utc >= start,
                RestrictedSiteEvent.observed_at_utc <= end,
            )
            .group_by(func.date(RestrictedSiteEvent.observed_at_utc))
            .all()
        )
        # Typed text alerts by day
        text_rows = (
            db.session.query(
                func.date(TypedTextPolicyAlert.detected_at).label("day"),
                func.count(TypedTextPolicyAlert.id).label("count"),
            )
            .filter(
                TypedTextPolicyAlert.device_id.in_(device_ids),
                TypedTextPolicyAlert.detected_at >= start,
                TypedTextPolicyAlert.detected_at <= end,
            )
            .group_by(func.date(TypedTextPolicyAlert.detected_at))
            .all()
        )
        # Merge into daily totals
        daily: Dict[str, dict] = {}
        for r in site_rows:
            day_str = str(r.day)
            daily.setdefault(day_str, {"date": day_str, "site_violations": 0, "typed_text_alerts": 0})
            daily[day_str]["site_violations"] = int(r.count)
        for r in text_rows:
            day_str = str(r.day)
            daily.setdefault(day_str, {"date": day_str, "site_violations": 0, "typed_text_alerts": 0})
            daily[day_str]["typed_text_alerts"] = int(r.count)
        return sorted(daily.values(), key=lambda d: d["date"])
    except Exception as exc:
        logger.warning("[EnterpriseReport] _build_violation_trend failed: %s", exc)
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


def _count_by_type(rows: List[dict]) -> Dict[str, int]:
    """Count devices by type from row dicts."""
    counts: Dict[str, int] = {}
    for r in rows:
        t = r.get("device_type", "Unknown")
        counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items()))


# ── PR 17: Classification, dedup, segmentation, gap diagnostics ─────────────

_ASSET_CLASS_INFRASTRUCTURE = frozenset({'server', 'switch', 'router', 'firewall', 'access_point'})
_ASSET_CLASS_ENDPOINT = frozenset({'workstation', 'mobile', 'printer'})


def _compute_classification_flags(dev) -> List[str]:
    """Compute classification quality flags for a device."""
    flags = []
    conf = (getattr(dev, "classification_confidence", None) or "").strip().lower()
    if conf == "low":
        flags.append("low_confidence_classification")
    if hasattr(dev, "device_name") and dev.device_name and dev.device_name.startswith("Device-"):
        flags.append("auto_named_needs_review")
    score = getattr(dev, "confidence_score", None) or 0
    if conf != "manual" and score < 70:
        flags.append("classification_review_needed")
    return flags


def _dedup_rank(row: dict) -> tuple:
    """Deterministic ranking for duplicate IP resolution.
    Higher tuple = preferred row. Stable across runs."""
    return (
        1 if row.get("uptime_pct") is not None else 0,
        row.get("sample_count") or 0,
        1 if (row.get("classification_confidence") or "").lower() == "manual" else 0,
        row.get("device_id", 0),
    )


def _deduplicate_server_rows(rows: List[dict]):
    """If two rows share same device_ip, keep the higher-ranked one.
    Deterministic: same input always produces same output.
    Returns (deduplicated_rows, duplicate_entries)."""
    seen_ips: Dict[str, dict] = {}
    dupes = []
    for row in rows:
        ip = row.get("device_ip")
        if not ip or ip == "—":
            # No IP — keep the row, key by device_id
            seen_ips[f"_id_{row.get('device_id', id(row))}"] = row
            continue
        if ip in seen_ips:
            existing = seen_ips[ip]
            if _dedup_rank(row) > _dedup_rank(existing):
                dupes.append(existing)
                seen_ips[ip] = row
            else:
                dupes.append(row)
        else:
            seen_ips[ip] = row
    return list(seen_ips.values()), dupes


def _segment_rows(rows: List[dict]) -> dict:
    """Segment rows by asset class: infrastructure, endpoints, unclassified."""
    infra, endpoints, unclassified = [], [], []
    for r in rows:
        dtype = (r.get("device_type") or "unknown").lower().replace(' ', '_')
        if dtype in _ASSET_CLASS_INFRASTRUCTURE:
            infra.append(r)
        elif dtype in _ASSET_CLASS_ENDPOINT:
            endpoints.append(r)
        else:
            unclassified.append(r)

    def _avg_uptime(subset):
        vals = [float(r["uptime_pct"]) for r in subset if r.get("uptime_pct") is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "infrastructure": {
            "count": len(infra),
            "avg_uptime": _avg_uptime(infra),
            "worst_3": sorted(
                [r for r in infra if r.get("uptime_pct") is not None],
                key=lambda r: float(r["uptime_pct"])
            )[:3],
        },
        "endpoints": {
            "count": len(endpoints),
            "avg_uptime": _avg_uptime(endpoints),
        },
        "unclassified": {
            "count": len(unclassified),
            "device_types": list({r.get("device_type", "unknown") for r in unclassified}),
        },
    }


def _batch_scan_existence(device_ips: List[str], start: datetime, end: datetime) -> set:
    """Single query: returns set of IPs that have at least one scan in the period.
    Replaces per-device EXISTS check — no N+1."""
    if not device_ips:
        return set()
    from models.scan_history import DeviceScanHistory
    rows = (
        db.session.query(DeviceScanHistory.device_ip)
        .filter(
            DeviceScanHistory.device_ip.in_(device_ips),
            DeviceScanHistory.scan_timestamp >= start,
            DeviceScanHistory.scan_timestamp <= end,
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _compute_data_gaps(row: dict, ips_with_scans: set) -> Optional[dict]:
    """Compute reasons why a device has missing/unknown data. Pure Python (no DB)."""
    gaps = {}
    if row.get("uptime_pct") is None:
        ip = row.get("device_ip", "")
        gaps["uptime"] = "rollup_missing" if ip in ips_with_scans else "no_scans_in_period"
    if row.get("avg_cpu") is None and (row.get("sample_count") or 0) == 0:
        dtype = (row.get("device_type") or "").lower().replace(' ', '_')
        if dtype in ('switch', 'access_point', 'router', 'firewall'):
            gaps["telemetry"] = "device_type_unsupported_for_agent"
        else:
            gaps["telemetry"] = "no_agent_data"
    if row.get("auto_named"):
        gaps["identity"] = "auto_named_from_ip_only"
    return gaps if gaps else None


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
    _dup_entries: List[dict] = []  # PR 17: duplicate IP entries (populated only for server fleet)
    sla_profiles_used: Dict[str, int] = {}   # {profile_name: device_count}
    if fleet in (None, "server"):
        # Filter by infrastructure device types from config (default: server, switch, AP, router, firewall)
        try:
            from flask import current_app
            infra_types = [t.lower() for t in current_app.config.get(
                'INFRASTRUCTURE_DEVICE_TYPES',
                ['server', 'switch', 'access_point', 'router', 'firewall']
            )]
        except RuntimeError:
            # Outside Flask app context (e.g. unit tests)
            infra_types = ['server', 'switch', 'access_point', 'router', 'firewall']

        inv_devices = (
            Device.query
            .filter(
                Device.is_active.isnot(False),
                func.replace(func.lower(Device.device_type), ' ', '_').in_(infra_types),
            )
            .order_by(Device.device_name.asc())
            .all()
        )
        inv_device_ids = [dev.device_id for dev in inv_devices]
        all_metrics = _server_metrics_bulk(inv_device_ids, start_dt, end_dt)
        icmp_cov = _bulk_icmp_coverage(inv_device_ids, start_dt, end_dt)

        # Bulk-load per-device SLA thresholds from ComplianceProfile
        inv_profile_map = {
            dev.device_id: getattr(dev, "compliance_profile_id", None)
            for dev in inv_devices
        }
        inv_sla_thresholds, inv_sla_profiles = _bulk_load_sla_thresholds(inv_profile_map)
        sla_profiles_used.update(inv_sla_profiles)

        for dev in inv_devices:
            try:
                up = _inventory_uptime(dev.device_ip, dev.device_id, start_dt, end_dt,
                                      device_name=dev.device_name)
                metrics = all_metrics.get(dev.device_id, {})
                net_stats = _inventory_network_stats(dev.device_id, start_dt, end_dt)
                dev_thresholds = inv_sla_thresholds.get(dev.device_id)
                tier = sla_tier(up, dev_thresholds)
                dev_cov = icmp_cov.get(dev.device_id, {})
                row = {
                    "device_id": dev.device_id,
                    "device_name": (dev.device_name or "").strip().rstrip("-").strip() or f"Device-{dev.device_ip}",
                    "device_ip": dev.device_ip or "—",
                    "device_type": dev.device_type or "Unknown",
                    "uptime_pct": up,
                    "downtime_hours": downtime_hours(up, period_hours,
                                                     observed_hours=dev_cov.get("observed_hours")),
                    "uptime_hours": _safe_round((up / 100.0) * period_hours) if up is not None else None,
                    "downtime_pct": _safe_round(100.0 - up, 2) if up is not None else None,
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
                    "timeout_count": _count_no_response_scans(dev.device_ip, start_dt, end_dt),
                    "sla_thresholds": "custom" if dev_thresholds else "default",
                    # PR 17: Classification confidence annotations
                    "classification_confidence": getattr(dev, "classification_confidence", None) or "Low",
                    "confidence_score": getattr(dev, "confidence_score", None) or 0,
                    "auto_named": bool(dev.device_name and dev.device_name.startswith("Device-")),
                    "_classification_flags": _compute_classification_flags(dev),
                    "monitoring_coverage_pct": dev_cov.get("coverage_pct"),
                    "observed_hours": dev_cov.get("observed_hours"),
                    "coverage_level": coverage_level(dev_cov.get("coverage_pct")),
                }
                row["anomaly_flag"], row["anomaly_reason"] = _detect_anomaly(row)
                server_rows.append(row)
            except Exception as exc:
                logger.warning("[EnterpriseReport] server device_id=%s error=%s", dev.device_id, exc)

        # PR 17: Report-level IP deduplication (deterministic)
        server_rows, _dup_entries = _deduplicate_server_rows(server_rows)

        # PR 17: Data gap diagnostics (batched — single query)
        _gap_device_ips = [
            r["device_ip"] for r in server_rows
            if r.get("uptime_pct") is None and r.get("device_ip") and r["device_ip"] != "—"
        ]
        _ips_with_scans = _batch_scan_existence(_gap_device_ips, start_dt, end_dt)
        for r in server_rows:
            gaps = _compute_data_gaps(r, _ips_with_scans)
            if gaps:
                r["_data_gaps"] = gaps

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
        _bulk_violations = get_device_violations(tracked_ids, start_dt, end_dt)

        for dev in tracked_devices:
            try:
                up, raw_incidents, cov_meta = bulk_uptime.get(dev.id, (None, [], {}))
                merged_incidents, flap_count = _merge_flapping_incidents(raw_incidents)
                mttr, mtbf = _mttr_mtbf(merged_incidents)
                tier = sla_tier(up)   # TrackedDevices use default SLA thresholds
                beh = _workstation_behavioral_metrics(dev.id, start_dt, end_dt, _category_cache)
                viol = _bulk_violations.get(dev.id, {})
                _obs_s = cov_meta.get("observed_seconds", 0)
                _obs_h = _safe_round(_obs_s / 3600.0) if _obs_s else None
                _cov_pct = cov_meta.get("monitoring_coverage_pct")
                _tracked_row = {
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
                    "downtime_hours": downtime_hours(up, period_hours,
                                                     observed_hours=_obs_h),
                    "uptime_hours": _safe_round((up / 100.0) * period_hours) if up is not None else None,
                    "downtime_pct": _safe_round(100.0 - up, 2) if up is not None else None,
                    "sla_tier": tier,
                    "incident_count": len(merged_incidents),
                    "raw_incident_count": len(raw_incidents),
                    "flap_suppressed": flap_count,
                    "flapping_score": _safe_round(flap_count / max(1, len(raw_incidents)), 2) if raw_incidents else None,
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
                    # Violation fields (bulk-fetched, single query for all devices)
                    "violation_count":       viol.get("violation_count", 0),
                    "has_violation":         viol.get("has_violation", False),
                    "last_violation_time":   viol.get("last_violation_time"),
                    "top_domains":           viol.get("top_domains", []),
                    "monitoring_coverage_pct": _cov_pct,
                    "observed_hours":         _obs_h,
                    "coverage_level":         coverage_level(_cov_pct),
                }
                _tracked_row["anomaly_flag"], _tracked_row["anomaly_reason"] = _detect_anomaly(_tracked_row)
                tracked_rows.append(_tracked_row)
            except Exception as exc:
                logger.warning("[EnterpriseReport] tracked device_id=%s error=%s", dev.id, exc)

    # ── Cross-fleet deduplication: server fleet takes precedence ─────────────
    # A device enrolled in both Device and TrackedDevice tables must appear
    # only in the server fleet.  O(1) set lookup scales with device count.
    _server_ips = {r['device_ip'] for r in server_rows if r.get('device_ip')}
    tracked_rows = [r for r in tracked_rows if r.get('device_ip') not in _server_ips]

    # ── Website violation detail breakdown (workstation fleet only) ───────────
    website_violation_details: List[dict] = []
    typed_text_violation_details: List[dict] = []
    violation_trend: List[dict] = []
    if fleet in (None, "workstation") and tracked_ids:
        website_violation_details = _fleet_violation_summary(tracked_ids, start_dt, end_dt)
        typed_text_violation_details = _fleet_typed_text_violations(tracked_ids, start_dt, end_dt)
        violation_trend = _build_violation_trend(tracked_ids, start_dt, end_dt)

    total_site_violations = sum(v["violation_count"] for v in website_violation_details)
    total_typed_text_alerts = sum(v["alert_count"] for v in typed_text_violation_details)

    # Top offenders by combined violation count
    offender_map: Dict[int, dict] = {}
    for v in website_violation_details:
        did = v["device_id"]
        offender_map.setdefault(did, {"device_id": did, "device_name": v["device_name"], "employee_name": v["employee_name"], "site_violations": 0, "typed_text_alerts": 0})
        offender_map[did]["site_violations"] += v["violation_count"]
    for v in typed_text_violation_details:
        did = v["device_id"]
        offender_map.setdefault(did, {"device_id": did, "device_name": v["device_name"], "employee_name": v["employee_name"], "site_violations": 0, "typed_text_alerts": 0})
        offender_map[did]["typed_text_alerts"] += v["alert_count"]
    top_offenders = sorted(
        offender_map.values(),
        key=lambda o: o["site_violations"] + o["typed_text_alerts"],
        reverse=True,
    )[:10]

    violations_section = {
        "restricted_site_events": website_violation_details,
        "typed_text_alerts": typed_text_violation_details,
        "total_site_violations": total_site_violations,
        "total_typed_text_alerts": total_typed_text_alerts,
        "top_offenders": top_offenders,
        "trend": violation_trend,
    }

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

    # Two-tier split: degraded (online but struggling) vs chronically offline
    _degraded_rows = [r for r in rows_with_data if r.get("uptime_pct") is not None and r["uptime_pct"] > 0]
    _offline_rows = [r for r in rows_with_data if r.get("uptime_pct") is None or r["uptime_pct"] == 0]

    def _deg_score(r):
        """Inline degradation score matching ReportingServiceBase._degradation_score."""
        u = r.get("uptime_pct")
        l = r.get("avg_latency_ms")
        p = r.get("avg_packet_loss_pct")
        score = 0.0
        score += (100.0 - max(0, min(100, float(u or 0)))) * 0.5
        if l is not None:
            score += min(float(l) / 500.0, 1.0) * 25.0
        if p is not None:
            score += min(float(p) / 20.0, 1.0) * 25.0
        return score

    _degraded_rows.sort(key=_deg_score, reverse=True)
    # Worst 5: prefer degraded, backfill from offline only if needed
    worst_five = _degraded_rows[:5]
    if len(worst_five) < 5:
        worst_five += _offline_rows[:5 - len(worst_five)]
    best_three = sorted(rows_with_data, key=lambda r: r["uptime_pct"], reverse=True)[:3]

    # PR 17: Asset class segmentation
    segments = _segment_rows(server_rows)

    # PR 17: Data quality summary
    gap_rows = [r for r in server_rows if r.get("_data_gaps")]
    gap_reasons: Dict[str, int] = {}
    for r in gap_rows:
        for _key, reason in r["_data_gaps"].items():
            gap_reasons[reason] = gap_reasons.get(reason, 0) + 1
    review_count = sum(1 for r in server_rows if "low_confidence_classification" in r.get("_classification_flags", []))

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

    _SOURCE_LABELS = {
        "rollup":              "daily rollup aggregates",
        "daily_rollup":        "daily rollup aggregates",
        "hourly_rollup":       "hourly rollup aggregates",
        "availability_events": "availability event stream",
        "scan_history":        "raw scan history",
        "unknown":             "source unknown",
    }
    def _fmt_sources(sources: set) -> str | None:
        if not sources:
            return None
        return ", ".join(_SOURCE_LABELS.get(s, s) for s in sorted(sources))

    _confidence = {
        "fleet_avg_uptime": {
            "level": "HIGH" if rows_with_data else "NO_DATA",
            "source": "Computed from device availability records",
        },
        "server_fleet": {
            "level": _confidence_level(server_data_sources),
            "source": _fmt_sources(server_data_sources),
        },
        "tracked_fleet": {
            "level": _confidence_level(tracked_data_sources),
            "source": _fmt_sources(tracked_data_sources),
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
            "device_type_breakdown": _count_by_type(server_rows),
            "worst_devices": worst_five,
            "best_devices": best_three,
            "segments": segments,
            "data_quality": {
                "devices_with_gaps": len(gap_rows),
                "gap_reasons": dict(sorted(gap_reasons.items(), key=lambda x: x[1], reverse=True)),
                "devices_needing_review": review_count,
            },
            "_duplicate_ips_merged": len(_dup_entries),
            # Monitoring coverage: fleet-level aggregates
            "monitoring_coverage_pct": _safe_round(
                sum(r["monitoring_coverage_pct"] for r in all_rows
                    if r.get("monitoring_coverage_pct") is not None)
                / max(1, sum(1 for r in all_rows if r.get("monitoring_coverage_pct") is not None))
            ) if any(r.get("monitoring_coverage_pct") is not None for r in all_rows) else None,
            "low_coverage_device_count": sum(
                1 for r in all_rows
                if r.get("coverage_level") in ("low", "unknown")
            ),
        },
        "server_rows": server_rows,
        "tracked_rows": tracked_rows,
        "website_violation_details": website_violation_details,
        "violations": violations_section,
        "sla_profiles_used": sla_profiles_used if sla_profiles_used else None,
        "generated_at": datetime.utcnow().isoformat(),
        "_confidence": _confidence,
    }

    # ── Generate insights (rule-based mandatory, Gemini optional) ──────────
    try:
        from services.report_insight_engine import ReportInsightEngine
        engine = ReportInsightEngine()
        report_dict = result  # alias for clarity
        insights = engine.generate_insights(report_dict, "enterprise")
        try:
            from flask import current_app
            if current_app.config.get("GEMINI_REPORT_INSIGHTS_ENABLED"):
                insights = engine.enhance_with_gemini(insights, report_dict)
        except RuntimeError:
            pass  # Outside app context
        result["insights"] = insights
        result["_severities"] = insights.get("metric_severities", {})
    except Exception as exc:
        logger.warning("[EnterpriseReport] Insight generation failed: %s", exc)
        result["insights"] = None
        result["_severities"] = {}

    return result
