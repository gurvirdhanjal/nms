from __future__ import annotations

import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from flask import abort, session as flask_session
from sqlalchemy import and_, desc, func, or_, text

from config import Config
from extensions import db
from models.department import Department
from models.tracked_device import (
    DeviceApplicationLog,
    DeviceResourceLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingSample,
)
from models.user import User
from services.tracking_history import parse_workstation_window
from services.tracking_freshness import build_workstation_report_freshness

_DEVICE_EVENT_LOCKS: dict[int, threading.Lock] = {}
_DEVICE_EVENT_LOCKS_GUARD = threading.Lock()


def _is_postgres() -> bool:
    try:
        return db.engine.url.get_backend_name() == "postgresql"
    except Exception:
        return False


def _normalize_status(value: Any) -> str:
    status = str(value or "offline").strip().lower()
    if status not in ("online", "degraded", "offline"):
        return "offline"
    return status


def _get_device_lock(device_id: int) -> threading.Lock:
    with _DEVICE_EVENT_LOCKS_GUARD:
        lock = _DEVICE_EVENT_LOCKS.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _DEVICE_EVENT_LOCKS[device_id] = lock
        return lock


def _resolve_scope_context(current_user=None) -> tuple[str, int | None, int | None]:
    role = str(flask_session.get("role") or "").strip().lower()
    site_id = flask_session.get("site_id")
    department_id = flask_session.get("department_id")
    user_id = flask_session.get("user_id")

    if current_user is not None:
        role = str(getattr(current_user, "role", role) or "").strip().lower() or role
        site_id = getattr(current_user, "site_id", site_id)
        department_id = getattr(current_user, "department_id", department_id)

    if current_user is None and user_id and (site_id is None or department_id is None):
        user = db.session.get(User, user_id)
        if user:
            site_id = user.site_id
            department_id = user.department_id

    return role, site_id, department_id


def _department_ids_for_site(site_id: int | None) -> list[int]:
    if site_id is None:
        return []
    return [row[0] for row in db.session.query(Department.id).filter(Department.site_id == site_id).all()]


def scoped_tracked_device_query(
    current_user=None,
    include_archived: bool = False,
    include_unscoped_for_admin: bool = True,
):
    query = TrackedDevice.query
    if not include_archived:
        query = query.filter(db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None)))

    role, site_id, department_id = _resolve_scope_context(current_user=current_user)

    if role == "admin":
        if include_unscoped_for_admin:
            return query
        return query.filter(or_(TrackedDevice.site_id.isnot(None), TrackedDevice.department_id.isnot(None)))

    if role == "manager":
        if site_id is None:
            return query.filter(False)
        dept_ids = _department_ids_for_site(site_id)
        filters = [TrackedDevice.site_id == site_id]
        if dept_ids:
            filters.append(TrackedDevice.department_id.in_(dept_ids))
        return query.filter(or_(*filters))

    if role in ("operator", "viewer", "user"):
        filters = []
        if department_id is not None:
            filters.append(TrackedDevice.department_id == department_id)
        elif site_id is not None:
            filters.append(TrackedDevice.site_id == site_id)
        if not filters:
            return query.filter(False)
        return query.filter(or_(*filters))

    return query.filter(False)


def allowed_device_ids_subquery(current_user=None, include_archived: bool = True):
    return scoped_tracked_device_query(
        current_user=current_user,
        include_archived=include_archived,
        include_unscoped_for_admin=True,
    ).with_entities(TrackedDevice.id).subquery()


def get_scoped_tracked_device_or_404(device_id: int, include_archived: bool = True) -> TrackedDevice:
    device = scoped_tracked_device_query(
        include_archived=include_archived,
        include_unscoped_for_admin=True,
    ).filter(TrackedDevice.id == int(device_id)).first()
    if not device:
        abort(404)
    return device


def _clamp_page_limit(raw_limit: Any) -> int:
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        parsed = 100
    max_limit = max(1, int(getattr(Config, "TRACKING_REPORT_PAGE_MAX_LIMIT", 200) or 200))
    return max(1, min(parsed, max_limit))


def _encode_cursor(ts: datetime, cursor_id: int) -> str:
    payload = {"ts": ts.isoformat(), "id": int(cursor_id)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(raw_cursor: str | None) -> tuple[datetime, int] | None:
    if not raw_cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        ts = payload.get("ts")
        cursor_id = int(payload.get("id"))
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed, cursor_id
    except Exception:
        return None


def persist_availability_event(
    device: TrackedDevice,
    probe_result: dict[str, Any] | None,
    source: str,
    dry_run: bool = False,
):
    if dry_run:
        return None

    payload = probe_result if isinstance(probe_result, dict) else {}
    status = _normalize_status(payload.get("availability_status") or payload.get("status"))
    observed_at = payload.get("observed_at")
    if isinstance(observed_at, datetime):
        now_utc = observed_at.replace(tzinfo=None)
    else:
        now_utc = datetime.utcnow()
    heartbeat_seconds = max(60, int(getattr(Config, "TRACKING_HEARTBEAT_INTERVAL_SECONDS", 300) or 300))
    heartbeat_cutoff = now_utc - timedelta(seconds=heartbeat_seconds)
    sample_id = payload.get("sample_id")
    try:
        sample_id = int(sample_id) if sample_id is not None else None
    except (TypeError, ValueError):
        sample_id = None

    def _write_event_locked():
        latest = (
            TrackedDeviceAvailabilityEvent.query.filter(
                TrackedDeviceAvailabilityEvent.device_id == device.id
            )
            .order_by(
                desc(TrackedDeviceAvailabilityEvent.observed_at),
                desc(TrackedDeviceAvailabilityEvent.id),
            )
            .first()
        )
        event_type = None
        if latest is None:
            event_type = "bootstrap"
        elif latest.status != status:
            event_type = "status_change"
        elif latest.observed_at and latest.observed_at <= heartbeat_cutoff:
            event_type = "heartbeat"

        if not event_type:
            return None

        event = TrackedDeviceAvailabilityEvent(
            device_id=device.id,
            sample_id=sample_id,
            observed_at=now_utc,
            status=status,
            event_type=event_type,
            source=str(source or payload.get("source") or "unknown").strip().lower()[:20] or "unknown",
            probe_method=(payload.get("probe_method") or device.probe_method),
            probe_error_code=(payload.get("probe_error_code") or device.probe_error_code),
            metrics_available=bool(payload.get("metrics_available")),
        )
        db.session.add(event)
        db.session.flush()
        return event

    if _is_postgres():
        try:
            db.session.execute(
                text("SELECT id FROM tracked_devices WHERE id = :device_id FOR UPDATE"),
                {"device_id": int(device.id)},
            )
        except Exception:
            # Lock acquisition failed — propagate rather than calling _write_event_locked()
            # inside an already-aborted transaction.
            raise
        return _write_event_locked()

    lock = _get_device_lock(int(device.id))
    with lock:
        return _write_event_locked()


def query_availability_events_page(
    device_id: int,
    start_utc: datetime,
    end_utc: datetime,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    limit = _clamp_page_limit(limit)
    decoded_cursor = _decode_cursor(cursor)

    query = TrackedDeviceAvailabilityEvent.query.filter(
        TrackedDeviceAvailabilityEvent.device_id == device_id,
        TrackedDeviceAvailabilityEvent.observed_at >= start_utc,
        TrackedDeviceAvailabilityEvent.observed_at < end_utc,
    )
    if decoded_cursor:
        cursor_ts, cursor_id = decoded_cursor
        query = query.filter(
            or_(
                TrackedDeviceAvailabilityEvent.observed_at < cursor_ts,
                and_(
                    TrackedDeviceAvailabilityEvent.observed_at == cursor_ts,
                    TrackedDeviceAvailabilityEvent.id < cursor_id,
                ),
            )
        )

    rows = (
        query.order_by(
            desc(TrackedDeviceAvailabilityEvent.observed_at),
            desc(TrackedDeviceAvailabilityEvent.id),
        )
        .limit(limit + 1)
        .all()
    )
    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_next and rows:
        tail = rows[-1]
        next_cursor = _encode_cursor(tail.observed_at, tail.id)

    return [row.to_dict() for row in rows], next_cursor


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _display_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _bounded_online_seconds(
    status: str,
    segment_start_utc: datetime,
    segment_end_utc: datetime,
    status_anchor_utc: datetime | None,
    heartbeat_seconds: int,
) -> float:
    if status not in ("online", "degraded"):
        return 0.0
    if segment_end_utc <= segment_start_utc:
        return 0.0

    anchor = status_anchor_utc or segment_start_utc
    age_at_segment_start = max(0.0, (segment_start_utc - anchor).total_seconds())
    remaining_lease = max(0.0, float(heartbeat_seconds) - age_at_segment_start)
    if remaining_lease <= 0:
        return 0.0

    segment_span = max(0.0, (segment_end_utc - segment_start_utc).total_seconds())
    return min(segment_span, remaining_lease)


def _default_daily_uptime_snapshot(reference: datetime, heartbeat_seconds: int) -> dict[str, Any]:
    day_start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = max(0, int((reference - day_start).total_seconds()))
    expected_heartbeats = (
        int((elapsed_seconds + heartbeat_seconds - 1) // heartbeat_seconds)
        if elapsed_seconds > 0
        else 0
    )
    return {
        "window_start": day_start.isoformat(),
        "window_end": reference.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "heartbeat_interval_seconds": heartbeat_seconds,
        "received_heartbeats": 0,
        "expected_heartbeats": int(expected_heartbeats),
        "sample_coverage_percent": 0.0 if expected_heartbeats > 0 else None,
        "online_seconds": 0,
        "downtime_seconds": int(elapsed_seconds),
        "uptime_percent": 0.0,
    }


def calculate_daily_uptime_snapshot(device_id: int, now_utc: datetime | None = None) -> dict[str, Any]:
    """
    Calculate "today" uptime from heartbeat-like availability events.

    This uses heartbeat lease expiry so a long gap between events is treated as downtime
    even when there is no explicit offline event.
    """
    reference = now_utc or datetime.utcnow()
    if reference.tzinfo:
        reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
    day_start = reference.replace(hour=0, minute=0, second=0, microsecond=0)

    elapsed_seconds = max(0, int((reference - day_start).total_seconds()))
    heartbeat_seconds = max(
        60,
        int(getattr(Config, "TRACKING_HEARTBEAT_INTERVAL_SECONDS", 300) or 300),
    )
    try:
        previous_event = (
            TrackedDeviceAvailabilityEvent.query.filter(
                TrackedDeviceAvailabilityEvent.device_id == int(device_id),
                TrackedDeviceAvailabilityEvent.observed_at < day_start,
            )
            .order_by(
                desc(TrackedDeviceAvailabilityEvent.observed_at),
                desc(TrackedDeviceAvailabilityEvent.id),
            )
            .first()
        )
        day_events = (
            TrackedDeviceAvailabilityEvent.query.filter(
                TrackedDeviceAvailabilityEvent.device_id == int(device_id),
                TrackedDeviceAvailabilityEvent.observed_at >= day_start,
                TrackedDeviceAvailabilityEvent.observed_at <= reference,
            )
            .order_by(
                TrackedDeviceAvailabilityEvent.observed_at.asc(),
                TrackedDeviceAvailabilityEvent.id.asc(),
            )
            .all()
        )
    except Exception:
        return _default_daily_uptime_snapshot(reference, heartbeat_seconds)

    observed_deltas = []
    previous_observed_at = None
    for event in day_events:
        observed_at = getattr(event, "observed_at", None)
        if not observed_at:
            continue
        if previous_observed_at is not None:
            delta_seconds = (observed_at - previous_observed_at).total_seconds()
            if 10 <= delta_seconds <= 3600:
                observed_deltas.append(delta_seconds)
        previous_observed_at = observed_at

    if observed_deltas:
        inferred_interval = int(round(float(median(observed_deltas))))
        heartbeat_seconds = max(30, min(600, inferred_interval))

    expected_heartbeats = (
        int((elapsed_seconds + heartbeat_seconds - 1) // heartbeat_seconds)
        if elapsed_seconds > 0
        else 0
    )

    reachable_slots = set()
    for event in day_events:
        observed_at = getattr(event, "observed_at", None)
        if not observed_at:
            continue
        if _normalize_status(getattr(event, "status", "offline")) in ("online", "degraded"):
            slot = int(max(0.0, (observed_at - day_start).total_seconds()) // max(1, heartbeat_seconds))
            reachable_slots.add(slot)

    received_heartbeats = len(reachable_slots)

    current_status = _normalize_status(previous_event.status if previous_event else "offline")
    current_anchor = previous_event.observed_at if previous_event and previous_event.observed_at else day_start
    cursor = day_start
    online_seconds_float = 0.0

    for event in day_events:
        observed_at = getattr(event, "observed_at", None)
        if not observed_at:
            continue
        if observed_at > reference:
            observed_at = reference
        if observed_at > cursor:
            online_seconds_float += _bounded_online_seconds(
                status=current_status,
                segment_start_utc=cursor,
                segment_end_utc=observed_at,
                status_anchor_utc=current_anchor,
                heartbeat_seconds=heartbeat_seconds,
            )
            cursor = observed_at

        current_status = _normalize_status(event.status)
        current_anchor = event.observed_at or observed_at
        if cursor >= reference:
            break

    if cursor < reference:
        online_seconds_float += _bounded_online_seconds(
            status=current_status,
            segment_start_utc=cursor,
            segment_end_utc=reference,
            status_anchor_utc=current_anchor,
            heartbeat_seconds=heartbeat_seconds,
        )

    online_seconds = int(round(min(float(elapsed_seconds), max(0.0, online_seconds_float))))
    downtime_seconds = max(0, elapsed_seconds - online_seconds)
    uptime_percent = round((online_seconds / elapsed_seconds) * 100.0, 2) if elapsed_seconds > 0 else 0.0
    sample_coverage_percent = (
        round(min(100.0, (received_heartbeats / expected_heartbeats) * 100.0), 2)
        if expected_heartbeats > 0
        else None
    )

    return {
        "window_start": day_start.isoformat(),
        "window_end": reference.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "heartbeat_interval_seconds": heartbeat_seconds,
        "received_heartbeats": int(received_heartbeats),
        "expected_heartbeats": int(expected_heartbeats),
        "sample_coverage_percent": sample_coverage_percent,
        "online_seconds": int(online_seconds),
        "downtime_seconds": int(downtime_seconds),
        "uptime_percent": uptime_percent,
    }


def query_workstation_reports(device_id: int, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    status_counts_rows = (
        db.session.query(
            TrackedDeviceAvailabilityEvent.status,
            func.count(TrackedDeviceAvailabilityEvent.id),
        )
        .filter(
            TrackedDeviceAvailabilityEvent.device_id == device_id,
            TrackedDeviceAvailabilityEvent.observed_at >= start_utc,
            TrackedDeviceAvailabilityEvent.observed_at < end_utc,
        )
        .group_by(TrackedDeviceAvailabilityEvent.status)
        .all()
    )
    status_counts = {str(status or "offline"): int(count) for status, count in status_counts_rows}
    total_availability_events = int(sum(status_counts.values()))
    online_events = int(status_counts.get("online", 0))
    degraded_events = int(status_counts.get("degraded", 0))
    offline_events = int(status_counts.get("offline", 0))

    reachability_pct = _pct(online_events + degraded_events, total_availability_events)
    degraded_impact_pct = _pct(degraded_events, total_availability_events)

    integrity_rows = (
        db.session.query(TrackingSample.integrity_status, func.count(TrackingSample.id))
        .filter(
            TrackingSample.device_id == device_id,
            TrackingSample.received_at >= start_utc,
            TrackingSample.received_at < end_utc,
        )
        .group_by(TrackingSample.integrity_status)
        .all()
    )
    integrity = {str(status or "unknown"): int(count) for status, count in integrity_rows}
    integrity_total = int(sum(integrity.values()))
    verified = int(integrity.get("verified", 0))
    partial = int(integrity.get("partial", 0))
    legacy = int(integrity.get("legacy_approx", 0))
    invalid = int(integrity.get("invalid", 0))
    confidence_numerator = verified + (0.5 * partial) + (0.25 * legacy)
    data_confidence_pct = _pct(confidence_numerator, integrity_total)
    invalid_ratio_pct = _pct(invalid, integrity_total)

    total_app_rows = (
        db.session.query(func.count(DeviceApplicationLog.id))
        .filter(
            DeviceApplicationLog.device_id == device_id,
            DeviceApplicationLog.timestamp >= start_utc,
            DeviceApplicationLog.timestamp < end_utc,
        )
        .scalar()
        or 0
    )
    app_rows_with_duration = (
        db.session.query(func.count(DeviceApplicationLog.id))
        .filter(
            DeviceApplicationLog.device_id == device_id,
            DeviceApplicationLog.timestamp >= start_utc,
            DeviceApplicationLog.timestamp < end_utc,
            DeviceApplicationLog.duration.isnot(None),
        )
        .scalar()
        or 0
    )
    app_duration_coverage_pct = _pct(app_rows_with_duration, total_app_rows)

    return {
        "total_availability_events": total_availability_events,
        "online_events": online_events,
        "degraded_events": degraded_events,
        "offline_events": offline_events,
        "reachability_pct": reachability_pct,
        "reachability_display": _display_pct(reachability_pct),
        "degraded_impact_pct": degraded_impact_pct,
        "degraded_impact_display": _display_pct(degraded_impact_pct),
        "data_confidence_pct": data_confidence_pct,
        "data_confidence_display": _display_pct(data_confidence_pct),
        "integrity_invalid_ratio_pct": invalid_ratio_pct,
        "app_duration_coverage_pct": app_duration_coverage_pct,
        "app_duration_coverage_display": _display_pct(app_duration_coverage_pct),
        "app_rows_with_duration": int(app_rows_with_duration),
        "total_app_rows": int(total_app_rows),
        "integrity_counts": integrity,
        "integrity_total": integrity_total,
        "freshness": build_workstation_report_freshness(device_id, start_utc, end_utc),
    }


def _freshness_reference(last_sample_at: datetime | None, last_event_at: datetime | None) -> datetime | None:
    candidates = [candidate for candidate in (last_sample_at, last_event_at) if candidate]
    if not candidates:
        return None
    return max(candidates)


def query_workstation_overview(device: TrackedDevice) -> dict[str, Any]:
    last_sample_at = (
        db.session.query(func.max(TrackingSample.received_at))
        .filter(TrackingSample.device_id == device.id)
        .scalar()
    )
    last_availability_event_at = (
        db.session.query(func.max(TrackedDeviceAvailabilityEvent.observed_at))
        .filter(TrackedDeviceAvailabilityEvent.device_id == device.id)
        .scalar()
    )
    freshness_ref = _freshness_reference(last_sample_at, last_availability_event_at)
    stale_minutes = max(1, int(getattr(Config, "TRACKING_WORKSTATION_STALE_MINUTES", 15) or 15))
    is_stale = True
    if freshness_ref is not None:
        is_stale = (datetime.utcnow() - freshness_ref) > timedelta(minutes=stale_minutes)

    default_start, default_end = parse_workstation_window(None, None)
    report = query_workstation_reports(device.id, default_start, default_end)
    return {
        "device": {
            "id": device.id,
            "device_name": device.device_name,
            "employee_name": device.employee_name,
            "mac_address": device.mac_address,
            "ip_address": device.ip_address,
            "hostname": device.hostname,
            "site_id": device.site_id,
            "department_id": device.department_id,
            "availability_status": _normalize_status(device.availability_status),
            "probe_method": device.probe_method,
            "probe_error_code": device.probe_error_code,
            "metrics_available": bool(device.metrics_available),
            "last_probe_at": device.last_probe_at.isoformat() if device.last_probe_at else None,
        },
        "last_sample_at": last_sample_at.isoformat() if last_sample_at else None,
        "last_availability_event_at": (
            last_availability_event_at.isoformat() if last_availability_event_at else None
        ),
        "is_stale": bool(is_stale),
        "stale_after_minutes": stale_minutes,
        "kpi_summary": report,
    }


def query_workstation_anomalies(
    device: TrackedDevice,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    reports = query_workstation_reports(device.id, start_utc, end_utc)

    cpu_spike_count = (
        db.session.query(func.count(DeviceResourceLog.id))
        .filter(
            DeviceResourceLog.device_id == device.id,
            DeviceResourceLog.timestamp >= start_utc,
            DeviceResourceLog.timestamp < end_utc,
            DeviceResourceLog.cpu_usage.isnot(None),
            DeviceResourceLog.cpu_usage >= 90.0,
        )
        .scalar()
        or 0
    )
    if cpu_spike_count >= 3:
        anomalies.append(
            {
                "code": "CPU_SPIKE",
                "severity": "high",
                "details": {"samples_ge_90": int(cpu_spike_count), "threshold": 3},
            }
        )

    events = (
        TrackedDeviceAvailabilityEvent.query.filter(
            TrackedDeviceAvailabilityEvent.device_id == device.id,
            TrackedDeviceAvailabilityEvent.observed_at >= start_utc,
            TrackedDeviceAvailabilityEvent.observed_at < end_utc,
        )
        .order_by(
            TrackedDeviceAvailabilityEvent.observed_at.asc(),
            TrackedDeviceAvailabilityEvent.id.asc(),
        )
        .all()
    )
    transitions = 0
    previous_status = None
    for event in events:
        status = _normalize_status(event.status)
        if previous_status is not None and status != previous_status:
            transitions += 1
        previous_status = status

    range_hours = max((end_utc - start_utc).total_seconds() / 3600.0, 1.0 / 60.0)
    transitions_per_hour = round(float(transitions) / range_hours, 4)
    if transitions_per_hour > 3.0:
        anomalies.append(
            {
                "code": "CONNECTIVITY_FLAP",
                "severity": "high",
                "details": {
                    "transitions": int(transitions),
                    "range_hours": round(range_hours, 3),
                    "transitions_per_hour": transitions_per_hour,
                    "threshold": 3.0,
                },
            }
        )

    invalid_ratio = reports.get("integrity_invalid_ratio_pct")
    confidence = reports.get("data_confidence_pct")
    if (invalid_ratio is not None and invalid_ratio > 5.0) or (confidence is not None and confidence < 70.0):
        anomalies.append(
            {
                "code": "INTEGRITY_DRIFT",
                "severity": "medium",
                "details": {
                    "invalid_ratio_pct": invalid_ratio,
                    "data_confidence_pct": confidence,
                    "invalid_threshold_pct": 5.0,
                    "confidence_threshold_pct": 70.0,
                },
            }
        )

    overview = query_workstation_overview(device)
    if overview.get("is_stale"):
        anomalies.append(
            {
                "code": "STALE_DATA",
                "severity": "medium",
                "details": {
                    "last_sample_at": overview.get("last_sample_at"),
                    "last_availability_event_at": overview.get("last_availability_event_at"),
                    "stale_after_minutes": overview.get("stale_after_minutes"),
                },
            }
        )

    return anomalies
