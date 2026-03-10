from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func

from config import Config
from extensions import db
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingSample,
)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _iso_or_none(value: datetime | None) -> str | None:
    normalized = _utc_naive(value)
    return normalized.isoformat() if normalized else None


def _age_seconds(now_utc: datetime, candidate: datetime | None) -> int | None:
    normalized = _utc_naive(candidate)
    if not normalized:
        return None
    return max(0, int((now_utc - normalized).total_seconds()))


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _expected_slots(start_utc: datetime, end_utc: datetime, slot_seconds: int) -> int:
    normalized_start = _utc_naive(start_utc) or datetime.utcnow()
    normalized_end = _utc_naive(end_utc) or normalized_start
    if normalized_end <= normalized_start:
        return 0
    elapsed_seconds = max(0, int((normalized_end - normalized_start).total_seconds()))
    if elapsed_seconds <= 0:
        return 0
    interval = max(30, int(slot_seconds or 180))
    return int((elapsed_seconds + interval - 1) // interval)


def _coverage_pct(sample_count: int, expected_slots: int) -> float:
    if expected_slots <= 0:
        return 0.0
    return round(min(100.0, (float(sample_count) / float(expected_slots)) * 100.0), 2)


def _weighted_confidence(integrity_counts: dict[str, int]) -> float:
    verified = int(integrity_counts.get("verified", 0))
    partial = int(integrity_counts.get("partial", 0))
    legacy = int(integrity_counts.get("legacy_approx", 0))
    total = int(sum(int(value or 0) for value in integrity_counts.values()))
    if total <= 0:
        return 0.0
    numerator = verified + (0.5 * partial) + (0.25 * legacy)
    return round((float(numerator) / float(total)) * 100.0, 2)


def _max_timestamp_map(
    model,
    device_ids: list[int],
    column,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[int, datetime]:
    if not device_ids:
        return {}
    query = db.session.query(model.device_id, func.max(column)).filter(model.device_id.in_(device_ids))
    if start_utc is not None:
        query = query.filter(column >= start_utc)
    if end_utc is not None:
        query = query.filter(column < end_utc)
    rows = query.group_by(model.device_id).all()
    return {int(device_id): _utc_naive(value) for device_id, value in rows if device_id and value}


def _count_map(model, device_ids: list[int], column, start_utc: datetime, end_utc: datetime) -> dict[int, int]:
    if not device_ids:
        return {}
    rows = (
        db.session.query(model.device_id, func.count(model.id))
        .filter(
            model.device_id.in_(device_ids),
            column >= start_utc,
            column < end_utc,
        )
        .group_by(model.device_id)
        .all()
    )
    return {int(device_id): int(count or 0) for device_id, count in rows if device_id}


def map_probe_error_to_ui_reason(error_code) -> str:
    code = str(error_code or "").strip().upper()
    if not code:
        return "Agent state is unavailable."

    friendly = {
        "AGENT_UNREACHABLE": "Agent service did not respond.",
        "AGENT_REQUEST_FAILED": "Agent request failed before telemetry could be collected.",
        "DEVICE_NO_IP": "Device has no known IP address for agent probing.",
        "AGENT_PUBLIC_IP_SKIPPED": "Agent probe was skipped because the device IP is public.",
        "AGENT_LINK_LOCAL_SKIPPED": "Agent probe was skipped because the device IP is link-local.",
        "AGENT_SERVICE_NOT_IDENTIFIED": "Tracking agent could not be identified on the device.",
        "IDENTITY_HTTP_401": "Agent identity probe was rejected.",
        "IDENTITY_HTTP_403": "Agent identity probe was denied.",
        "STATS_HTTP_401": "Agent telemetry probe was rejected.",
        "STATS_HTTP_403": "Agent telemetry probe was denied.",
        "HEALTH_HTTP_404": "Agent health endpoint is not available.",
    }
    if code in friendly:
        return friendly[code]
    if code.startswith("IDENTITY_HTTP_"):
        return "Agent identity probe returned an unexpected HTTP status."
    if code.startswith("STATS_HTTP_"):
        return "Agent telemetry probe returned an unexpected HTTP status."
    if code.startswith("HEALTH_HTTP_"):
        return "Agent health probe returned an unexpected HTTP status."
    return code.replace("_", " ").title()


def build_controls_contract(telemetry_state: str, reason_code: str | None) -> dict[str, dict[str, Any]]:
    normalized_state = str(telemetry_state or "offline-empty").strip().lower()
    enabled = normalized_state in {"live", "degraded", "stale"}
    reason = None if enabled else (reason_code or "AGENT_UNREACHABLE")
    return {
        "remote_view": {"enabled": enabled, "reason_code": reason},
        "camera": {"enabled": enabled, "reason_code": reason},
        "mic": {"enabled": enabled, "reason_code": reason},
        "message": {"enabled": enabled, "reason_code": reason},
    }


def build_live_freshness(
    device,
    live_payload,
    now_utc,
    checkin_window_seconds,
    stale_minutes,
) -> dict[str, Any]:
    normalized_now = _utc_naive(now_utc) or datetime.utcnow()
    payload = live_payload if isinstance(live_payload, dict) else {}
    checkin_seconds = max(30, int(checkin_window_seconds or 180))
    _ = max(1, int(stale_minutes or 15))

    last_agent_sync_at = _utc_naive(getattr(device, "last_agent_sync_at", None))
    last_successful_sample_at = (
        db.session.query(func.max(TrackingSample.received_at))
        .filter(TrackingSample.device_id == int(device.id))
        .scalar()
    )
    last_successful_sample_at = _utc_naive(last_successful_sample_at)
    last_availability_event_at = (
        db.session.query(func.max(TrackedDeviceAvailabilityEvent.observed_at))
        .filter(TrackedDeviceAvailabilityEvent.device_id == int(device.id))
        .scalar()
    )
    last_availability_event_at = _utc_naive(last_availability_event_at)

    probe_failed = bool(payload.get("probe_failed"))
    metrics_missing = bool(payload.get("metrics_missing"))
    persisted_fallback_eligible = bool(payload.get("persisted_fallback_eligible"))
    reason_code = str(
        payload.get("reason_code")
        or payload.get("probe_error_code")
        or getattr(device, "probe_error_code", "")
        or ""
    ).strip() or None
    data_source = str(payload.get("data_source") or "none").strip() or "none"
    probe_latency_ms = payload.get("probe_latency_ms")
    try:
        probe_latency_ms = float(probe_latency_ms) if probe_latency_ms is not None else None
    except (TypeError, ValueError):
        probe_latency_ms = None

    agent_sync_age_seconds = _age_seconds(normalized_now, last_agent_sync_at)
    sample_age_seconds = _age_seconds(normalized_now, last_successful_sample_at)
    heartbeat_old = (
        agent_sync_age_seconds is not None and
        agent_sync_age_seconds > checkin_seconds
    )
    latency_high = probe_latency_ms is not None and probe_latency_ms > 100.0

    if probe_failed:
        telemetry_state = "offline-fallback" if persisted_fallback_eligible else "offline-empty"
    elif metrics_missing:
        telemetry_state = "stale"
    elif latency_high or heartbeat_old:
        telemetry_state = "degraded"
    else:
        telemetry_state = "live"

    if telemetry_state == "offline-fallback" and data_source == "none":
        data_source = "sync_recent_fallback"
    elif telemetry_state == "offline-empty":
        data_source = "none"
    elif telemetry_state == "stale" and data_source == "none":
        data_source = "db_snapshot"
    elif telemetry_state in {"live", "degraded"} and data_source == "none":
        data_source = "live_probe"

    return {
        "telemetry_state": telemetry_state,
        "data_source": data_source,
        "is_fallback": telemetry_state in {"stale", "offline-fallback"},
        "reason_code": reason_code,
        "last_agent_sync_at": _iso_or_none(last_agent_sync_at),
        "last_successful_sample_at": _iso_or_none(last_successful_sample_at),
        "last_availability_event_at": _iso_or_none(last_availability_event_at),
        "agent_sync_age_seconds": agent_sync_age_seconds,
        "sample_age_seconds": sample_age_seconds,
        "stale_after_seconds": checkin_seconds,
        "report_eligible": last_successful_sample_at is not None,
    }


def build_workstation_report_freshness(device_id: int, start_utc: datetime, end_utc: datetime) -> dict[str, Any]:
    normalized_start = _utc_naive(start_utc) or datetime.utcnow()
    normalized_end = _utc_naive(end_utc) or normalized_start
    checkin_seconds = max(30, int(getattr(Config, "TRACKING_AGENT_CHECKIN_WINDOW_SECONDS", 180) or 180))
    stale_minutes = max(1, int(getattr(Config, "TRACKING_WORKSTATION_STALE_MINUTES", 15) or 15))
    expected_slots = _expected_slots(normalized_start, normalized_end, checkin_seconds)

    last_sample_at = (
        db.session.query(func.max(TrackingSample.received_at))
        .filter(
            TrackingSample.device_id == int(device_id),
            TrackingSample.received_at >= normalized_start,
            TrackingSample.received_at < normalized_end,
        )
        .scalar()
    )
    last_sample_at = _utc_naive(last_sample_at)

    last_availability_event_at = (
        db.session.query(func.max(TrackedDeviceAvailabilityEvent.observed_at))
        .filter(
            TrackedDeviceAvailabilityEvent.device_id == int(device_id),
            TrackedDeviceAvailabilityEvent.observed_at >= normalized_start,
            TrackedDeviceAvailabilityEvent.observed_at < normalized_end,
        )
        .scalar()
    )
    last_availability_event_at = _utc_naive(last_availability_event_at)

    sample_count = int(
        db.session.query(func.count(TrackingSample.id))
        .filter(
            TrackingSample.device_id == int(device_id),
            TrackingSample.received_at >= normalized_start,
            TrackingSample.received_at < normalized_end,
        )
        .scalar()
        or 0
    )

    integrity_rows = (
        db.session.query(TrackingSample.integrity_status, func.count(TrackingSample.id))
        .filter(
            TrackingSample.device_id == int(device_id),
            TrackingSample.received_at >= normalized_start,
            TrackingSample.received_at < normalized_end,
        )
        .group_by(TrackingSample.integrity_status)
        .all()
    )
    integrity_counts = {
        str(status or "unknown"): int(count or 0)
        for status, count in integrity_rows
    }

    coverage_pct = _coverage_pct(sample_count, expected_slots)
    report_eligible = sample_count >= 1 and coverage_pct >= 10.0
    freshness_ref = max(
        [value for value in (last_sample_at, last_availability_event_at) if value],
        default=None,
    )
    stale_cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
    is_stale = bool(report_eligible and freshness_ref and freshness_ref < stale_cutoff)

    return {
        "source_basis": "persisted_samples",
        "last_sample_at": _iso_or_none(last_sample_at),
        "last_availability_event_at": _iso_or_none(last_availability_event_at),
        "is_stale": is_stale,
        "stale_after_minutes": stale_minutes,
        "data_confidence_pct": _weighted_confidence(integrity_counts),
        "coverage_pct": coverage_pct,
        "sample_count": sample_count,
        "report_eligible": report_eligible,
    }


def build_productivity_freshness_summary(
    device_ids: list[int],
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    normalized_start = _utc_naive(start_utc) or datetime.utcnow()
    normalized_end = _utc_naive(end_utc) or normalized_start
    unique_device_ids = sorted({int(device_id) for device_id in (device_ids or []) if device_id})
    if not unique_device_ids:
        return {
            "source_basis": "persisted_samples",
            "devices": {},
            "totals": {
                "fresh_devices": 0,
                "stale_devices": 0,
                "empty_devices": 0,
            },
        }

    device_rows = (
        db.session.query(TrackedDevice.id, TrackedDevice.device_name)
        .filter(TrackedDevice.id.in_(unique_device_ids))
        .all()
    )
    device_names = {int(device_id): str(device_name or f"Device {device_id}") for device_id, device_name in device_rows}

    last_samples = _max_timestamp_map(
        TrackingSample,
        unique_device_ids,
        TrackingSample.received_at,
        normalized_start,
        normalized_end,
    )
    last_activity = _max_timestamp_map(
        DeviceActivityLog,
        unique_device_ids,
        DeviceActivityLog.timestamp,
        normalized_start,
        normalized_end,
    )
    last_applications = _max_timestamp_map(
        DeviceApplicationLog,
        unique_device_ids,
        DeviceApplicationLog.timestamp,
        normalized_start,
        normalized_end,
    )
    sample_counts = _count_map(TrackingSample, unique_device_ids, TrackingSample.received_at, normalized_start, normalized_end)

    checkin_seconds = max(30, int(getattr(Config, "TRACKING_AGENT_CHECKIN_WINDOW_SECONDS", 180) or 180))
    stale_minutes = max(1, int(getattr(Config, "TRACKING_WORKSTATION_STALE_MINUTES", 15) or 15))
    expected_slots = _expected_slots(normalized_start, normalized_end, checkin_seconds)
    stale_cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)

    devices_payload: dict[str, dict[str, Any]] = {}
    totals = {"fresh_devices": 0, "stale_devices": 0, "empty_devices": 0}

    for device_id in unique_device_ids:
        sample_count = int(sample_counts.get(device_id, 0))
        coverage_pct = _coverage_pct(sample_count, expected_slots)
        report_eligible = sample_count >= 1 and coverage_pct >= 10.0
        latest_relevant = max(
            [
                value
                for value in (
                    last_samples.get(device_id),
                    last_activity.get(device_id),
                    last_applications.get(device_id),
                )
                if value
            ],
            default=None,
        )
        if not report_eligible:
            freshness_state = "empty"
            totals["empty_devices"] += 1
        elif latest_relevant and latest_relevant < stale_cutoff:
            freshness_state = "stale"
            totals["stale_devices"] += 1
        else:
            freshness_state = "fresh"
            totals["fresh_devices"] += 1

        devices_payload[str(device_id)] = {
            "device_name": device_names.get(device_id, f"Device {device_id}"),
            "last_sample_at": _iso_or_none(last_samples.get(device_id)),
            "last_activity_at": _iso_or_none(last_activity.get(device_id)),
            "last_application_at": _iso_or_none(last_applications.get(device_id)),
            "freshness_state": freshness_state,
            "coverage_pct": coverage_pct,
            "sample_count": sample_count,
            "report_eligible": report_eligible,
        }

    return {
        "source_basis": "persisted_samples",
        "devices": devices_payload,
        "totals": totals,
    }
