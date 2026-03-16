from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import and_, desc, func, or_

from config import Config
from extensions import db
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    DeviceResourceLog,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackingDailyRollup,
    TrackingHistoryIntegrityAudit,
    TrackingHourlyRollup,
    TrackingSample,
)
from services.timescaledb_service import TimescaleDBService
from services.tracking_reconcile import normalize_mac

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DAYS = 7
MAX_RAW_HISTORY_DAYS = 30
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000
DEFAULT_HISTORY_MAX_LIMIT = max(1, int(getattr(Config, "TRACKING_REPORT_PAGE_MAX_LIMIT", 200) or 200))
MAX_WORKSTATION_HISTORY_DAYS = max(1, int(getattr(Config, "TRACKING_REPORT_MAX_DAYS", 90) or 90))


@dataclass
class IngestResult:
    sample_id: int
    created: bool
    idempotency_key: str
    integrity_status: str
    integrity_notes: dict[str, Any]
    logs_written: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "created": self.created,
            "idempotency_key": self.idempotency_key,
            "integrity_status": self.integrity_status,
            "integrity_notes": self.integrity_notes,
            "logs_written": self.logs_written,
        }


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def serialize_utc_ms(dt: datetime | None) -> str | None:
    normalized = _ensure_utc(dt)
    if not normalized:
        return None
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.") + f"{normalized.microsecond // 1000:03d}Z"


def to_epoch_ms(dt: datetime | None) -> int | None:
    normalized = _ensure_utc(dt)
    if not normalized:
        return None
    return int(normalized.timestamp() * 1000)


def _parse_iso_like_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if value is None:
        return None
    try:
        parsed = _parse_datetime(value)
        return parsed
    except Exception:
        return None


def _attach_timestamp_fields(
    payload: dict[str, Any],
    source_key: str,
    utc_key: str,
    epoch_key: str,
) -> dict[str, Any]:
    dt = _parse_iso_like_datetime(payload.get(source_key))
    payload[utc_key] = serialize_utc_ms(dt)
    payload[epoch_key] = to_epoch_ms(dt)
    return payload


def _assert_desc_order(rows: list[Any], ts_accessor) -> None:
    for previous, current in zip(rows, rows[1:]):
        previous_ts = ts_accessor(previous)
        current_ts = ts_accessor(current)
        previous_id = int(getattr(previous, "id", 0) or 0)
        current_id = int(getattr(current, "id", 0) or 0)
        if previous_ts is None or current_ts is None:
            continue
        if previous_ts < current_ts:
            raise AssertionError("Ordering invariant failed: timestamps must be DESC")
        if previous_ts == current_ts and previous_id < current_id:
            raise AssertionError("Ordering invariant failed: ids must be DESC on equal timestamps")


def _canonical_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    device_info = payload.get("device_info") if isinstance(payload.get("device_info"), dict) else {}
    current_activity = payload.get("current_activity") if isinstance(payload.get("current_activity"), dict) else {}
    today_stats = payload.get("today_stats") if isinstance(payload.get("today_stats"), dict) else {}
    system_metrics = payload.get("system_metrics") if isinstance(payload.get("system_metrics"), dict) else {}

    apps_used_raw = today_stats.get("applications_used")
    if isinstance(apps_used_raw, list):
        applications_used = sorted({str(item).strip() for item in apps_used_raw if str(item).strip()})
    else:
        applications_used = []

    app_usage_raw = today_stats.get("app_usage_seconds")
    app_usage_seconds: dict[str, float] = {}
    if isinstance(app_usage_raw, dict):
        for key, raw_value in app_usage_raw.items():
            app_name = _clean_string(key)
            duration = _canonical_number(raw_value)
            if app_name and duration is not None and duration >= 0:
                app_usage_seconds[app_name] = duration

    disk_usage = _canonical_number(system_metrics.get("disk_usage"))
    used_gb = _canonical_number(system_metrics.get("used_gb"))
    total_gb = _canonical_number(system_metrics.get("total_gb"))

    return {
        "device_info": {
            "hostname": _clean_string(device_info.get("hostname")),
            "mac_address": normalize_mac(device_info.get("mac_address")),
            "system": _clean_string(device_info.get("system")),
        },
        "current_activity": {
            "keyboard_active": bool(current_activity.get("keyboard_active")),
            "mouse_active": bool(current_activity.get("mouse_active")),
            "idle_seconds": _canonical_number(current_activity.get("idle_seconds")),
            "current_application": _clean_string(current_activity.get("current_application")),
        },
        "today_stats": {
            "total_active_hours": _canonical_number(today_stats.get("total_active_hours")),
            "keyboard_events": int(today_stats.get("keyboard_events") or 0),
            "mouse_events": int(today_stats.get("mouse_events") or 0),
            "characters_typed": int(today_stats.get("characters_typed") or 0),
            "applications_used": applications_used,
            "app_usage_seconds": dict(sorted(app_usage_seconds.items())),
        },
        "system_metrics": {
            "cpu_percent": _canonical_number(system_metrics.get("cpu_percent")),
            "memory_percent": _canonical_number(system_metrics.get("memory_percent")),
            "disk_usage": disk_usage,
            "used_gb": used_gb,
            "total_gb": total_gb,
        },
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_idempotency_key(
    source: str,
    sample_uuid: str | None,
    received_minute_bucket: datetime,
    payload_hash: str,
) -> str:
    if sample_uuid:
        return f"uuid:{sample_uuid}"
    return f"legacy:{source}:{received_minute_bucket.isoformat()}:{payload_hash}"


def _resolve_integrity_status(
    payload: dict[str, Any],
    app_usage_seconds: dict[str, int],
) -> tuple[str, dict[str, Any]]:
    notes: dict[str, Any] = {}
    integrity_status = "verified"

    today_stats = payload.get("today_stats") if isinstance(payload.get("today_stats"), dict) else {}
    applications_used = today_stats.get("applications_used")
    has_legacy_app_list = isinstance(applications_used, list) and len(applications_used) > 0

    if has_legacy_app_list and not app_usage_seconds:
        integrity_status = "legacy_approx"
        notes["application_duration"] = "app_usage_seconds_missing; durations set to null"

    if not isinstance(payload.get("current_activity"), dict) and not isinstance(payload.get("system_metrics"), dict):
        notes["missing_sections"] = ["current_activity", "system_metrics"]
        if integrity_status == "verified":
            integrity_status = "partial"

    return integrity_status, notes


def _coerce_non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _extract_app_usage_seconds(payload: dict[str, Any]) -> dict[str, int]:
    today_stats = payload.get("today_stats") if isinstance(payload.get("today_stats"), dict) else {}
    app_usage_raw = today_stats.get("app_usage_seconds")
    normalized: dict[str, int] = {}
    if not isinstance(app_usage_raw, dict):
        return normalized
    for app_name, duration in app_usage_raw.items():
        key = _clean_string(app_name)
        parsed = _coerce_non_negative_int(duration)
        if key and parsed is not None:
            normalized[key] = parsed
    return normalized


def ingest_tracking_sample(
    device_id: int,
    payload: dict[str, Any] | None,
    source: str,
    received_at: datetime | None = None,
) -> IngestResult:
    payload = payload if isinstance(payload, dict) else {}
    source = (_clean_string(source) or "sync").lower()
    received_at_utc = _parse_datetime(received_at) or datetime.utcnow()
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

    sample_uuid = _clean_string(meta.get("sample_uuid"))
    schema_version = _clean_string(meta.get("schema_version")) or "1"
    sampled_at = _parse_datetime(meta.get("sampled_at_utc") or meta.get("sampled_at"))
    received_minute_bucket = _floor_to_minute(received_at_utc)

    stable_payload = _extract_stable_payload(payload)
    payload_hash = _payload_hash(stable_payload)
    idempotency_key = _build_idempotency_key(source, sample_uuid, received_minute_bucket, payload_hash)

    existing = TrackingSample.query.filter_by(
        device_id=device_id,
        idempotency_key=idempotency_key,
    ).first()
    if existing:
        return IngestResult(
            sample_id=existing.id,
            created=False,
            idempotency_key=idempotency_key,
            integrity_status=existing.integrity_status,
            integrity_notes=existing.integrity_notes or {},
            logs_written=0,
        )

    app_usage_seconds = _extract_app_usage_seconds(payload)
    integrity_status, integrity_notes = _resolve_integrity_status(payload, app_usage_seconds)

    previous_sample = TrackingSample.query.filter(
        TrackingSample.device_id == device_id
    ).order_by(desc(TrackingSample.received_at), desc(TrackingSample.id)).first()

    sample = TrackingSample(
        device_id=device_id,
        sample_uuid=sample_uuid,
        idempotency_key=idempotency_key,
        sampled_at=sampled_at,
        received_at=received_at_utc,
        source=source,
        schema_version=schema_version,
        integrity_status=integrity_status,
        integrity_notes=integrity_notes,
        received_minute_bucket=received_minute_bucket,
        payload_hash=payload_hash,
        previous_sample_id=previous_sample.id if previous_sample else None,
    )
    db.session.add(sample)
    db.session.flush()

    logs_written = 0
    current_activity = payload.get("current_activity") if isinstance(payload.get("current_activity"), dict) else {}
    if current_activity:
        db.session.add(
            DeviceActivityLog(
                device_id=device_id,
                sample_id=sample.id,
                timestamp=received_at_utc,
                activity_type="status_update",
                event_count=1,
                details=json.dumps(current_activity),
            )
        )
        logs_written += 1

    system_metrics = payload.get("system_metrics") if isinstance(payload.get("system_metrics"), dict) else {}
    if system_metrics:
        network_metrics = payload.get("network") if isinstance(payload.get("network"), dict) else {}
        if not network_metrics:
            network_metrics = system_metrics.get("network_speed") if isinstance(system_metrics.get("network_speed"), dict) else {}
        db.session.add(
            DeviceResourceLog(
                device_id=device_id,
                sample_id=sample.id,
                timestamp=received_at_utc,
                cpu_usage=system_metrics.get("cpu_percent"),
                memory_usage=system_metrics.get("memory_percent"),
                disk_usage=system_metrics.get("disk_usage"),
                upload_kbps=network_metrics.get("upload_speed_kbps", 0.0),
                download_kbps=network_metrics.get("download_speed_kbps", 0.0),
            )
        )
        logs_written += 1

    # Populate current_application column on DeviceActivityLog if present
    cur_app = current_activity.get("current_application") if current_activity else None
    if cur_app:
        try:
            db.session.query(DeviceActivityLog).filter(
                DeviceActivityLog.sample_id == sample.id,
                DeviceActivityLog.activity_type == "status_update",
            ).update({"current_application": cur_app}, synchronize_session=False)
        except Exception:
            pass  # column may not exist yet (migration pending)

    today_stats = payload.get("today_stats") if isinstance(payload.get("today_stats"), dict) else {}

    # Prefer structured app_sessions list (per app-switch, includes window_title)
    # over legacy app_usage_seconds dict (cumulative totals, no window titles)
    app_sessions = today_stats.get("app_sessions") if isinstance(today_stats.get("app_sessions"), list) else []
    _apps_to_classify: set[str] = set()
    if app_sessions:
        for sess in app_sessions:
            app_name = _clean_string(sess.get("app") or "")
            if not app_name:
                continue
            db.session.add(
                DeviceApplicationLog(
                    device_id=device_id,
                    sample_id=sample.id,
                    timestamp=received_at_utc,
                    application_name=app_name,
                    window_title=(sess.get("window_title") or None),
                    status="active",
                    duration=int(sess.get("duration_s") or 0),
                )
            )
            _apps_to_classify.add(app_name)
            logs_written += 1
    elif app_usage_seconds:
        # Legacy path: cumulative app_usage_seconds dict (no window_title)
        for app_name, duration_seconds in sorted(app_usage_seconds.items()):
            db.session.add(
                DeviceApplicationLog(
                    device_id=device_id,
                    sample_id=sample.id,
                    timestamp=received_at_utc,
                    application_name=app_name,
                    status="active",
                    duration=duration_seconds,
                )
            )
            _apps_to_classify.add(app_name)
            logs_written += 1
    else:
        # Final fallback: applications_used list (no durations, no window titles)
        applications = today_stats.get("applications_used")
        if isinstance(applications, list):
            unique_apps: list[str] = []
            seen: set = set()
            for app_name in applications:
                normalized = _clean_string(app_name)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    unique_apps.append(normalized)
            for app_name in unique_apps:
                db.session.add(
                    DeviceApplicationLog(
                        device_id=device_id,
                        sample_id=sample.id,
                        timestamp=received_at_utc,
                        application_name=app_name,
                        status="active",
                        duration=None,
                    )
                )
                _apps_to_classify.add(app_name)
                logs_written += 1

    # Populate AppCategoryCache for any new app names seen (best-effort; never blocks ingest)
    if _apps_to_classify:
        try:
            from services.app_classifier import classify_app
            for _app in _apps_to_classify:
                classify_app(_app)
        except Exception:
            pass

    return IngestResult(
        sample_id=sample.id,
        created=True,
        idempotency_key=idempotency_key,
        integrity_status=integrity_status,
        integrity_notes=integrity_notes,
        logs_written=logs_written,
    )


def parse_history_window(
    from_raw: str | None,
    to_raw: str | None,
    default_days: int = DEFAULT_HISTORY_DAYS,
    max_days: int = MAX_RAW_HISTORY_DAYS,
) -> tuple[datetime, datetime]:
    """
    Backward-compatible parser for legacy call sites.
    Invalid ranges are normalized instead of rejected.
    """
    try:
        return parse_history_window_strict(
            from_raw,
            to_raw,
            default_days=default_days,
            max_days=max_days,
        )
    except ValueError:
        now = datetime.utcnow()
        end = _parse_datetime(to_raw) or now
        start = _parse_datetime(from_raw) or (end - timedelta(days=max(1, int(default_days or DEFAULT_HISTORY_DAYS))))
        if start > end:
            start, end = end, start
        if (end - start).total_seconds() > max(1, int(max_days or MAX_RAW_HISTORY_DAYS)) * 86400:
            start = end - timedelta(days=max(1, int(max_days or MAX_RAW_HISTORY_DAYS)))
        if start == end:
            end = start + timedelta(seconds=1)
        return start, end


def parse_history_window_strict(
    from_raw: str | None,
    to_raw: str | None,
    default_days: int = DEFAULT_HISTORY_DAYS,
    max_days: int = MAX_RAW_HISTORY_DAYS,
) -> tuple[datetime, datetime]:
    default_days = max(1, int(default_days or DEFAULT_HISTORY_DAYS))
    max_days = max(1, int(max_days or MAX_RAW_HISTORY_DAYS))

    now = datetime.utcnow()
    end = _parse_datetime(to_raw) or now
    start = _parse_datetime(from_raw) or (end - timedelta(days=default_days))

    if start >= end:
        raise ValueError("Invalid time window: end must be greater than start.")

    if (end - start).total_seconds() > max_days * 86400:
        raise ValueError(f"Requested time window exceeds maximum of {max_days} days.")

    return start, end


def parse_workstation_window(
    from_raw: str | None,
    to_raw: str | None,
    default_days: int = DEFAULT_HISTORY_DAYS,
    max_days: int = MAX_WORKSTATION_HISTORY_DAYS,
) -> tuple[datetime, datetime]:
    default_days = max(1, int(default_days or DEFAULT_HISTORY_DAYS))
    max_days = max(1, int(max_days or MAX_WORKSTATION_HISTORY_DAYS))

    now = datetime.utcnow()
    end = _parse_datetime(to_raw) or now
    start = _parse_datetime(from_raw) or (end - timedelta(days=default_days))

    if start >= end:
        raise ValueError("Invalid time window: end must be greater than start.")
    if (end - start).total_seconds() > max_days * 86400:
        raise ValueError(f"Requested time window exceeds maximum of {max_days} days.")
    return start, end


def build_history_envelope(request_obj, start: datetime, end: datetime) -> dict[str, Any]:
    timezone_requested = str(request_obj.args.get("tz") or "local").strip() or "local"
    timezone_used = "UTC"
    server_now = datetime.utcnow()
    server_now_epoch_ms = to_epoch_ms(server_now)

    server_clock_offset_ms = None
    raw_client_epoch = request_obj.headers.get("X-Client-Epoch-Ms")
    if raw_client_epoch is not None:
        try:
            client_epoch_ms = int(str(raw_client_epoch).strip())
            server_clock_offset_ms = int(server_now_epoch_ms - client_epoch_ms) if server_now_epoch_ms is not None else None
        except (TypeError, ValueError):
            server_clock_offset_ms = None

    return {
        "window_start_utc": serialize_utc_ms(start),
        "window_end_utc": serialize_utc_ms(end),
        "timezone_requested": timezone_requested,
        "timezone_used": timezone_used,
        "server_now_utc": serialize_utc_ms(server_now),
        "server_now_epoch_ms": server_now_epoch_ms,
        "server_clock_offset_ms": server_clock_offset_ms,
    }


def _clamp_limit(raw_limit: Any) -> int:
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_LIMIT
    hard_max_limit = min(MAX_PAGE_LIMIT, DEFAULT_HISTORY_MAX_LIMIT)
    return max(1, min(parsed, hard_max_limit))


def encode_cursor(cursor_ts: datetime, cursor_id: int) -> str:
    payload = {
        "cursor_ts": serialize_utc_ms(cursor_ts),
        "cursor_id": int(cursor_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(raw_cursor: str | None) -> tuple[datetime, int] | None:
    if not raw_cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(raw_cursor.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        cursor_ts = _parse_datetime(payload.get("cursor_ts") or payload.get("ts"))
        cursor_id = int(payload.get("cursor_id") if payload.get("cursor_id") is not None else payload.get("id"))
        if not cursor_ts:
            return None
        return cursor_ts, cursor_id
    except Exception:
        return None


def _apply_cursor_filter(query, model, ts_column, cursor: tuple[datetime, int] | None):
    if not cursor:
        return query
    cursor_ts, cursor_id = cursor
    return query.filter(
        or_(
            ts_column < cursor_ts,
            and_(ts_column == cursor_ts, model.id < cursor_id),
        )
    )


def query_history_summary(device_id: int, start: datetime, end: datetime) -> dict[str, Any]:
    sample_query = TrackingSample.query.filter(
        TrackingSample.device_id == device_id,
        TrackingSample.received_at >= start,
        TrackingSample.received_at < end,
    )
    sample_count = sample_query.count()
    integrity_counts = (
        db.session.query(TrackingSample.integrity_status, func.count(TrackingSample.id))
        .filter(
            TrackingSample.device_id == device_id,
            TrackingSample.received_at >= start,
            TrackingSample.received_at < end,
        )
        .group_by(TrackingSample.integrity_status)
        .all()
    )

    activity_count = DeviceActivityLog.query.filter(
        DeviceActivityLog.device_id == device_id,
        DeviceActivityLog.timestamp >= start,
        DeviceActivityLog.timestamp < end,
    ).count()
    resource_count = DeviceResourceLog.query.filter(
        DeviceResourceLog.device_id == device_id,
        DeviceResourceLog.timestamp >= start,
        DeviceResourceLog.timestamp < end,
    ).count()
    app_rows = DeviceApplicationLog.query.filter(
        DeviceApplicationLog.device_id == device_id,
        DeviceApplicationLog.timestamp >= start,
        DeviceApplicationLog.timestamp < end,
    )
    application_count = app_rows.count()
    total_app_seconds = db.session.query(func.coalesce(func.sum(DeviceApplicationLog.duration), 0)).filter(
        DeviceApplicationLog.device_id == device_id,
        DeviceApplicationLog.timestamp >= start,
        DeviceApplicationLog.timestamp < end,
        DeviceApplicationLog.duration.isnot(None),
    ).scalar() or 0
    unique_apps = db.session.query(func.count(func.distinct(DeviceApplicationLog.application_name))).filter(
        DeviceApplicationLog.device_id == device_id,
        DeviceApplicationLog.timestamp >= start,
        DeviceApplicationLog.timestamp < end,
    ).scalar() or 0
    avg_cpu = db.session.query(func.avg(DeviceResourceLog.cpu_usage)).filter(
        DeviceResourceLog.device_id == device_id,
        DeviceResourceLog.timestamp >= start,
        DeviceResourceLog.timestamp < end,
    ).scalar()

    integrity = {str(status or "unknown"): int(count) for status, count in integrity_counts}
    return {
        "sample_count": int(sample_count),
        "activity_count": int(activity_count),
        "resource_count": int(resource_count),
        "application_count": int(application_count),
        "total_app_seconds": int(total_app_seconds),
        "unique_apps": int(unique_apps),
        "avg_cpu": round(float(avg_cpu), 2) if avg_cpu is not None else 0.0,
        "integrity": integrity,
    }


def query_activity_page(
    device_id: int,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    limit = _clamp_limit(limit)
    decoded_cursor = decode_cursor(cursor)

    query = DeviceActivityLog.query.filter(
        DeviceActivityLog.device_id == device_id,
        DeviceActivityLog.timestamp >= start,
        DeviceActivityLog.timestamp < end,
    )
    query = _apply_cursor_filter(query, DeviceActivityLog, DeviceActivityLog.timestamp, decoded_cursor)
    rows = query.order_by(desc(DeviceActivityLog.timestamp), desc(DeviceActivityLog.id)).limit(limit + 1).all()
    _assert_desc_order(rows, lambda item: item.timestamp)

    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_next and rows:
        tail = rows[-1]
        next_cursor = encode_cursor(tail.timestamp, tail.id)

    data: list[dict[str, Any]] = []
    for row in rows:
        row_payload = row.to_dict()
        _attach_timestamp_fields(
            row_payload,
            source_key="timestamp",
            utc_key="timestamp_utc",
            epoch_key="timestamp_epoch_ms",
        )
        data.append(row_payload)
    return data, next_cursor


def _bucket_seconds(bucket: str) -> int | None:
    mapping = {
        "raw": None,
        "1m": 60,
        "5m": 300,
        "1h": 3600,
    }
    return mapping.get(str(bucket or "raw").lower())


def _bucketize_resource_rows(rows: list[DeviceResourceLog], bucket_size_seconds: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[datetime, int], dict[str, Any]] = {}
    for row in rows:
        if not row.timestamp:
            continue
        ts_seconds = int(row.timestamp.timestamp())
        bucket_ts = datetime.utcfromtimestamp((ts_seconds // bucket_size_seconds) * bucket_size_seconds)
        key = (bucket_ts, row.device_id)
        agg = grouped.setdefault(
            key,
            {
                "timestamp": bucket_ts,
                "device_id": row.device_id,
                "sample_count": 0,
                "cpu_total": 0.0,
                "cpu_count": 0,
                "memory_total": 0.0,
                "memory_count": 0,
                "disk_total": 0.0,
                "disk_count": 0,
                "upload_total": 0.0,
                "upload_count": 0,
                "download_total": 0.0,
                "download_count": 0,
            },
        )
        agg["sample_count"] += 1
        if row.cpu_usage is not None:
            agg["cpu_total"] += float(row.cpu_usage)
            agg["cpu_count"] += 1
        if row.memory_usage is not None:
            agg["memory_total"] += float(row.memory_usage)
            agg["memory_count"] += 1
        if row.disk_usage is not None:
            agg["disk_total"] += float(row.disk_usage)
            agg["disk_count"] += 1
        if row.upload_kbps is not None:
            agg["upload_total"] += float(row.upload_kbps)
            agg["upload_count"] += 1
        if row.download_kbps is not None:
            agg["download_total"] += float(row.download_kbps)
            agg["download_count"] += 1

    results = []
    for (_, _), agg in grouped.items():
        bucket_timestamp = agg["timestamp"]
        results.append(
            {
                "timestamp": bucket_timestamp.isoformat(),
                "timestamp_utc": serialize_utc_ms(bucket_timestamp),
                "timestamp_epoch_ms": to_epoch_ms(bucket_timestamp),
                "device_id": agg["device_id"],
                "sample_count": agg["sample_count"],
                "cpu_usage": round(agg["cpu_total"] / agg["cpu_count"], 2) if agg["cpu_count"] else None,
                "memory_usage": round(agg["memory_total"] / agg["memory_count"], 2) if agg["memory_count"] else None,
                "disk_usage": round(agg["disk_total"] / agg["disk_count"], 2) if agg["disk_count"] else None,
                "upload_kbps": round(agg["upload_total"] / agg["upload_count"], 2) if agg["upload_count"] else None,
                "download_kbps": round(agg["download_total"] / agg["download_count"], 2) if agg["download_count"] else None,
            }
        )

    results.sort(key=lambda item: item["timestamp"], reverse=True)
    return results


def query_resource_page(
    device_id: int,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None = None,
    bucket: str = "raw",
) -> tuple[list[dict[str, Any]], str | None]:
    bucket_size_seconds = _bucket_seconds(bucket)
    limit = _clamp_limit(limit)

    if bucket_size_seconds is None:
        decoded_cursor = decode_cursor(cursor)
        query = DeviceResourceLog.query.filter(
            DeviceResourceLog.device_id == device_id,
            DeviceResourceLog.timestamp >= start,
            DeviceResourceLog.timestamp < end,
        )
        query = _apply_cursor_filter(query, DeviceResourceLog, DeviceResourceLog.timestamp, decoded_cursor)
        rows = query.order_by(desc(DeviceResourceLog.timestamp), desc(DeviceResourceLog.id)).limit(limit + 1).all()
        _assert_desc_order(rows, lambda item: item.timestamp)
        has_next = len(rows) > limit
        rows = rows[:limit]
        next_cursor = None
        if has_next and rows:
            tail = rows[-1]
            next_cursor = encode_cursor(tail.timestamp, tail.id)
        data: list[dict[str, Any]] = []
        for row in rows:
            row_payload = row.to_dict()
            _attach_timestamp_fields(
                row_payload,
                source_key="timestamp",
                utc_key="timestamp_utc",
                epoch_key="timestamp_epoch_ms",
            )
            data.append(row_payload)
        return data, next_cursor

    rows = DeviceResourceLog.query.filter(
        DeviceResourceLog.device_id == device_id,
        DeviceResourceLog.timestamp >= start,
        DeviceResourceLog.timestamp < end,
    ).order_by(desc(DeviceResourceLog.timestamp), desc(DeviceResourceLog.id)).limit(5000).all()
    _assert_desc_order(rows, lambda item: item.timestamp)
    bucketed = _bucketize_resource_rows(rows, bucket_size_seconds)
    return bucketed[:limit], None


def query_application_page(
    device_id: int,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None = None,
    group_by: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    group_by = (group_by or "").strip().lower()
    limit = _clamp_limit(limit)

    if group_by == "application":
        rows = (
            db.session.query(
                DeviceApplicationLog.application_name.label("application_name"),
                func.count(DeviceApplicationLog.id).label("sessions"),
                func.coalesce(func.sum(DeviceApplicationLog.duration), 0).label("total_duration"),
                func.max(DeviceApplicationLog.timestamp).label("last_used"),
            )
            .filter(
                DeviceApplicationLog.device_id == device_id,
                DeviceApplicationLog.timestamp >= start,
                DeviceApplicationLog.timestamp < end,
            )
            .group_by(DeviceApplicationLog.application_name)
            .order_by(desc(func.max(DeviceApplicationLog.timestamp)), desc(func.count(DeviceApplicationLog.id)))
            .limit(limit)
            .all()
        )
        data = [
            {
                "application_name": row.application_name,
                "sessions": int(row.sessions or 0),
                "total_duration": int(row.total_duration or 0),
                "last_used": row.last_used.isoformat() if row.last_used else None,
                "last_used_utc": serialize_utc_ms(row.last_used),
                "last_used_epoch_ms": to_epoch_ms(row.last_used),
            }
            for row in rows
        ]
        return data, None

    decoded_cursor = decode_cursor(cursor)
    query = DeviceApplicationLog.query.filter(
        DeviceApplicationLog.device_id == device_id,
        DeviceApplicationLog.timestamp >= start,
        DeviceApplicationLog.timestamp < end,
    )
    query = _apply_cursor_filter(query, DeviceApplicationLog, DeviceApplicationLog.timestamp, decoded_cursor)
    rows = query.order_by(desc(DeviceApplicationLog.timestamp), desc(DeviceApplicationLog.id)).limit(limit + 1).all()
    _assert_desc_order(rows, lambda item: item.timestamp)
    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_next and rows:
        tail = rows[-1]
        next_cursor = encode_cursor(tail.timestamp, tail.id)
    data: list[dict[str, Any]] = []
    for row in rows:
        row_payload = row.to_dict()
        _attach_timestamp_fields(
            row_payload,
            source_key="timestamp",
            utc_key="timestamp_utc",
            epoch_key="timestamp_epoch_ms",
        )
        data.append(row_payload)
    return data, next_cursor


def query_integrity_page(
    device_id: int,
    start: datetime,
    end: datetime,
    limit: int,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    limit = _clamp_limit(limit)
    decoded_cursor = decode_cursor(cursor)

    sample_query = TrackingSample.query.filter(
        TrackingSample.device_id == device_id,
        TrackingSample.received_at >= start,
        TrackingSample.received_at < end,
    )
    sample_query = _apply_cursor_filter(sample_query, TrackingSample, TrackingSample.received_at, decoded_cursor)
    rows = sample_query.order_by(desc(TrackingSample.received_at), desc(TrackingSample.id)).limit(limit + 1).all()
    _assert_desc_order(rows, lambda item: item.received_at)

    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_next and rows:
        tail = rows[-1]
        next_cursor = encode_cursor(tail.received_at, tail.id)

    data: list[dict[str, Any]] = []
    for row in rows:
        row_payload = row.to_dict()
        _attach_timestamp_fields(
            row_payload,
            source_key="received_at",
            utc_key="received_at_utc",
            epoch_key="received_at_epoch_ms",
        )
        if row_payload.get("sampled_at") is not None:
            _attach_timestamp_fields(
                row_payload,
                source_key="sampled_at",
                utc_key="sampled_at_utc",
                epoch_key="sampled_at_epoch_ms",
            )
        data.append(row_payload)
    return data, next_cursor


def _normalize_status(value: Any) -> str:
    status = str(value or "offline").strip().lower()
    if status not in {"online", "degraded", "offline"}:
        return "offline"
    return status


def _pct(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round((float(value) / float(total)) * 100.0, 2)


def _compute_sampling_interval_seconds(device_id: int, start: datetime, end: datetime) -> int | None:
    sample_times = (
        db.session.query(TrackingSample.received_at)
        .filter(
            TrackingSample.device_id == device_id,
            TrackingSample.received_at >= start,
            TrackingSample.received_at < end,
        )
        .order_by(desc(TrackingSample.received_at), desc(TrackingSample.id))
        .limit(32)
        .all()
    )
    values = [row[0] for row in sample_times if row and row[0]]
    if len(values) < 2:
        return None
    values = sorted(values)
    intervals = []
    for previous, current in zip(values, values[1:]):
        seconds = (current - previous).total_seconds()
        if seconds > 0:
            intervals.append(seconds)
    if not intervals:
        return None
    return int(round(float(median(intervals))))


def _build_integrity_timeline(device_id: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    range_seconds = max((end - start).total_seconds(), 1.0)
    if range_seconds <= 2 * 86400:
        bucket_seconds = 3600
    elif range_seconds <= 14 * 86400:
        bucket_seconds = 21600
    else:
        bucket_seconds = 86400

    rows = (
        db.session.query(TrackingSample.received_at, TrackingSample.integrity_status)
        .filter(
            TrackingSample.device_id == device_id,
            TrackingSample.received_at >= start,
            TrackingSample.received_at < end,
        )
        .order_by(desc(TrackingSample.received_at), desc(TrackingSample.id))
        .all()
    )

    buckets: dict[datetime, dict[str, Any]] = {}
    for received_at, integrity_status in rows:
        if received_at is None:
            continue
        bucket_epoch = int(received_at.timestamp() // bucket_seconds) * bucket_seconds
        bucket_start = datetime.utcfromtimestamp(bucket_epoch)
        current = buckets.setdefault(
            bucket_start,
            {
                "bucket_start_utc": serialize_utc_ms(bucket_start),
                "bucket_start_epoch_ms": to_epoch_ms(bucket_start),
                "verified": 0,
                "legacy_approx": 0,
                "partial": 0,
                "invalid": 0,
                "unknown": 0,
                "total": 0,
            },
        )
        normalized = str(integrity_status or "unknown").strip().lower()
        if normalized not in {"verified", "legacy_approx", "partial", "invalid"}:
            normalized = "unknown"
        current[normalized] = int(current.get(normalized, 0)) + 1
        current["total"] = int(current.get("total", 0)) + 1

    timeline = sorted(buckets.values(), key=lambda item: int(item.get("bucket_start_epoch_ms") or 0), reverse=True)
    return timeline


def _extract_idle_seconds(details_raw: Any) -> float | None:
    if details_raw is None:
        return None
    if isinstance(details_raw, dict):
        details = details_raw
    else:
        text = str(details_raw).strip()
        if not text:
            return None
        try:
            details = json.loads(text)
        except Exception:
            return None
    try:
        return float(details.get("idle_seconds"))
    except (TypeError, ValueError, AttributeError):
        return None


def query_history_dashboard(device: TrackedDevice, start: datetime, end: datetime) -> dict[str, Any]:
    summary = query_history_summary(device.id, start, end)
    range_seconds = max((end - start).total_seconds(), 1.0)
    range_hours = max(range_seconds / 3600.0, 1.0 / 60.0)

    status_rows = (
        db.session.query(
            TrackedDeviceAvailabilityEvent.status,
            func.count(TrackedDeviceAvailabilityEvent.id),
        )
        .filter(
            TrackedDeviceAvailabilityEvent.device_id == device.id,
            TrackedDeviceAvailabilityEvent.observed_at >= start,
            TrackedDeviceAvailabilityEvent.observed_at < end,
        )
        .group_by(TrackedDeviceAvailabilityEvent.status)
        .all()
    )
    status_counts = {str(status or "offline"): int(count or 0) for status, count in status_rows}
    online_events = int(status_counts.get("online", 0))
    degraded_events = int(status_counts.get("degraded", 0))
    offline_events = int(status_counts.get("offline", 0))
    total_availability_events = int(online_events + degraded_events + offline_events)
    reachability_pct = _pct(online_events + degraded_events, total_availability_events)

    ordered_events = (
        TrackedDeviceAvailabilityEvent.query.filter(
            TrackedDeviceAvailabilityEvent.device_id == device.id,
            TrackedDeviceAvailabilityEvent.observed_at >= start,
            TrackedDeviceAvailabilityEvent.observed_at < end,
        )
        .order_by(
            TrackedDeviceAvailabilityEvent.observed_at.asc(),
            TrackedDeviceAvailabilityEvent.id.asc(),
        )
        .all()
    )
    transitions = 0
    previous_status = None
    for event in ordered_events:
        current_status = _normalize_status(event.status)
        if previous_status is not None and current_status != previous_status:
            transitions += 1
        previous_status = current_status
    transitions_per_hour = round(float(transitions) / float(range_hours), 4)
    flap_resilience = round(max(0.0, 100.0 - (min(transitions_per_hour, 5.0) * 20.0)), 2)

    integrity_counts = summary.get("integrity", {})
    integrity_total = int(sum(int(value or 0) for value in integrity_counts.values()))
    verified_count = int(integrity_counts.get("verified", 0))
    partial_count = int(integrity_counts.get("partial", 0))
    legacy_count = int(integrity_counts.get("legacy_approx", 0))
    invalid_count = int(integrity_counts.get("invalid", 0))
    confidence_numerator = float(verified_count) + (0.5 * float(partial_count)) + (0.25 * float(legacy_count))
    data_confidence_pct = _pct(confidence_numerator, integrity_total)
    invalid_pct = _pct(invalid_count, integrity_total)
    partial_pct = _pct(partial_count, integrity_total)
    legacy_pct = _pct(legacy_count, integrity_total)
    verified_pct = _pct(verified_count, integrity_total)
    drift_detected = bool(invalid_pct > 5.0 or data_confidence_pct < 70.0)

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
    last_seen_candidates = [value for value in (device.last_seen, last_sample_at, last_availability_event_at) if value]
    last_seen = max(last_seen_candidates) if last_seen_candidates else None

    stale_minutes = max(1, int(getattr(Config, "TRACKING_WORKSTATION_STALE_MINUTES", 15) or 15))
    stale_seconds = stale_minutes * 60
    if last_seen is None:
        freshness_score = 0.0
        stale_data = True
    else:
        age_seconds = max(0.0, (datetime.utcnow() - last_seen).total_seconds())
        freshness_score = round(max(0.0, 100.0 - min(100.0, (age_seconds / stale_seconds) * 100.0)), 2)
        stale_data = bool(age_seconds > stale_seconds)

    sampling_interval_seconds = _compute_sampling_interval_seconds(device.id, start, end)
    expected_samples = (
        int(range_seconds // max(1, sampling_interval_seconds))
        if sampling_interval_seconds
        else None
    )
    received_samples = int(summary.get("sample_count", 0))
    sampling_health_pct = (
        round(min(100.0, (received_samples / expected_samples) * 100.0), 2)
        if expected_samples and expected_samples > 0
        else None
    )

    cpu_spike_count = (
        db.session.query(func.count(DeviceResourceLog.id))
        .filter(
            DeviceResourceLog.device_id == device.id,
            DeviceResourceLog.timestamp >= start,
            DeviceResourceLog.timestamp < end,
            DeviceResourceLog.cpu_usage.isnot(None),
            DeviceResourceLog.cpu_usage >= 90.0,
        )
        .scalar()
        or 0
    )
    idle_activity_rows = (
        DeviceActivityLog.query.filter(
            DeviceActivityLog.device_id == device.id,
            DeviceActivityLog.timestamp >= start,
            DeviceActivityLog.timestamp < end,
        )
        .order_by(desc(DeviceActivityLog.timestamp), desc(DeviceActivityLog.id))
        .limit(1000)
        .all()
    )
    excess_idle_count = sum(1 for row in idle_activity_rows if (_extract_idle_seconds(row.details) or 0.0) >= 1800.0)

    anomaly_badges: list[dict[str, Any]] = []
    if int(cpu_spike_count) >= 3:
        anomaly_badges.append({
            "code": "CPU_SPIKE",
            "severity": "high",
            "label": "CPU Spikes",
            "count": int(cpu_spike_count),
        })
    if transitions_per_hour > 3.0:
        anomaly_badges.append({
            "code": "CONNECTIVITY_FLAP",
            "severity": "high",
            "label": "Connectivity Flapping",
            "count": int(transitions),
            "transitions_per_hour": transitions_per_hour,
        })
    if drift_detected:
        anomaly_badges.append({
            "code": "INTEGRITY_DRIFT",
            "severity": "medium",
            "label": "Integrity Drift",
            "count": 1,
        })
    if int(excess_idle_count) >= 3:
        anomaly_badges.append({
            "code": "EXCESS_IDLE",
            "severity": "medium",
            "label": "Excess Idle",
            "count": int(excess_idle_count),
        })
    if stale_data:
        anomaly_badges.append({
            "code": "STALE_DATA",
            "severity": "medium",
            "label": "Data Stale",
            "count": 1,
        })

    stability_score = round(
        (0.40 * float(reachability_pct))
        + (0.30 * float(data_confidence_pct))
        + (0.20 * float(flap_resilience))
        + (0.10 * float(freshness_score)),
        2,
    )

    has_high_anomaly = any(str(item.get("severity", "")).lower() == "high" for item in anomaly_badges)
    has_moderate_anomaly = any(str(item.get("severity", "")).lower() == "medium" for item in anomaly_badges)
    if reachability_pct < 90.0 or data_confidence_pct < 70.0 or has_high_anomaly:
        health_verdict = "Unstable"
        if reachability_pct < 90.0:
            health_reason = "Reachability below 90% in selected window."
        elif data_confidence_pct < 70.0:
            health_reason = "Data confidence below 70%."
        else:
            health_reason = "High-severity anomaly detected."
    elif (90.0 <= reachability_pct <= 97.99) or (70.0 <= data_confidence_pct <= 84.99) or has_moderate_anomaly:
        health_verdict = "Degraded"
        if 90.0 <= reachability_pct <= 97.99:
            health_reason = "Reachability is below healthy target."
        elif 70.0 <= data_confidence_pct <= 84.99:
            health_reason = "Data confidence needs improvement."
        else:
            health_reason = "Moderate anomaly detected."
    else:
        health_verdict = "Healthy"
        health_reason = "Reachability and confidence are within healthy targets."

    integrity_timeline = _build_integrity_timeline(device.id, start, end)
    return {
        "current_status": _normalize_status(getattr(device, "availability_status", "offline")),
        "last_seen": serialize_utc_ms(last_seen),
        "last_seen_utc": serialize_utc_ms(last_seen),
        "last_seen_epoch_ms": to_epoch_ms(last_seen),
        "last_sample_at": serialize_utc_ms(last_sample_at),
        "last_sample_at_utc": serialize_utc_ms(last_sample_at),
        "last_sample_epoch_ms": to_epoch_ms(last_sample_at),
        "last_availability_event_at": serialize_utc_ms(last_availability_event_at),
        "last_availability_event_at_utc": serialize_utc_ms(last_availability_event_at),
        "last_availability_event_epoch_ms": to_epoch_ms(last_availability_event_at),
        "reachability_7d": reachability_pct,
        "stability_score": stability_score,
        "health_verdict": health_verdict,
        "health_verdict_reason": health_reason,
        "sample_count": int(summary.get("sample_count", 0)),
        "activity_count": int(summary.get("activity_count", 0)),
        "resource_count": int(summary.get("resource_count", 0)),
        "application_count": int(summary.get("application_count", 0)),
        "unique_apps": int(summary.get("unique_apps", 0)),
        "avg_cpu": float(summary.get("avg_cpu", 0.0)),
        "data_confidence_pct": data_confidence_pct,
        "sampling_interval_seconds": sampling_interval_seconds,
        "expected_samples": expected_samples,
        "received_samples": received_samples,
        "sampling_health_pct": sampling_health_pct,
        "anomaly_badges": anomaly_badges,
        "integrity_summary": {
            "verified": verified_count,
            "legacy_approx": legacy_count,
            "partial": partial_count,
            "invalid": invalid_count,
            "total": integrity_total,
            "data_confidence_pct": data_confidence_pct,
            "invalid_pct": invalid_pct,
            "partial_pct": partial_pct,
            "legacy_pct": legacy_pct,
            "verified_pct": verified_pct,
            "drift_detected": drift_detected,
        },
        "integrity_timeline": integrity_timeline,
        "flap_metrics": {
            "transitions": int(transitions),
            "transitions_per_hour": transitions_per_hour,
            "flap_resilience": flap_resilience,
        },
        "freshness_score": freshness_score,
    }


def run_tracking_integrity_checks(lookback_days: int = 7) -> dict[str, Any]:
    lookback_days = max(1, int(lookback_days or 7))
    window_start = datetime.utcnow() - timedelta(days=lookback_days)
    run_id = secrets.token_hex(8)
    created = 0
    details: list[dict[str, Any]] = []

    duplicate_keys = (
        db.session.query(
            TrackingSample.device_id,
            TrackingSample.idempotency_key,
            func.count(TrackingSample.id).label("count"),
        )
        .filter(TrackingSample.received_at >= window_start)
        .group_by(TrackingSample.device_id, TrackingSample.idempotency_key)
        .having(func.count(TrackingSample.id) > 1)
        .all()
    )
    for entry in duplicate_keys:
        audit = TrackingHistoryIntegrityAudit(
            run_id=run_id,
            device_id=entry.device_id,
            check_name="duplicate_idempotency_key",
            severity="high",
            details={
                "idempotency_key": entry.idempotency_key,
                "count": int(entry.count),
            },
        )
        db.session.add(audit)
        created += 1
        details.append(audit.to_dict())

    candidate_samples = TrackingSample.query.filter(
        TrackingSample.received_at >= window_start,
        TrackingSample.sampled_at.isnot(None),
    ).all()
    inverted_times = []
    for sample in candidate_samples:
        if not sample.sampled_at or not sample.received_at:
            continue
        if sample.sampled_at > sample.received_at + timedelta(minutes=5):
            inverted_times.append(sample)
    for sample in inverted_times:
        audit = TrackingHistoryIntegrityAudit(
            run_id=run_id,
            device_id=sample.device_id,
            check_name="sampled_after_received",
            severity="medium",
            details={
                "sample_id": sample.id,
                "sampled_at": sample.sampled_at.isoformat() if sample.sampled_at else None,
                "received_at": sample.received_at.isoformat() if sample.received_at else None,
            },
        )
        db.session.add(audit)
        created += 1
        details.append(audit.to_dict())

    db.session.commit()
    return {
        "success": True,
        "run_id": run_id,
        "checks_created": created,
        "window_start": window_start.isoformat(),
        "records": details,
    }


def run_tracking_retention(
    raw_days: int = 30,
    hourly_days: int = 365,
    daily_days: int = 1095,
) -> dict[str, Any]:
    backend = db.engine.url.get_backend_name()
    if backend == "postgresql" and TimescaleDBService.is_timescaledb_enabled():
        return {
            "success": True,
            "skipped": True,
            "policy_managed": True,
            "task": "run_tracking_retention",
            "backend": backend,
            "reason": "Managed by TimescaleDB",
            "detail": "Tracking raw hypertables are retention-managed by TimescaleDB and legacy tracking rollup tables are not maintained.",
        }

    raw_cutoff = datetime.utcnow() - timedelta(days=max(1, int(raw_days or 30)))
    hourly_cutoff = datetime.utcnow() - timedelta(days=max(1, int(hourly_days or 365)))
    daily_cutoff = (datetime.utcnow() - timedelta(days=max(1, int(daily_days or 1095)))).date()

    deleted = {
        "activity_logs": 0,
        "resource_logs": 0,
        "application_logs": 0,
        "tracking_samples": 0,
        "tracking_hourly_rollups": 0,
        "tracking_daily_rollups": 0,
        "typed_text_policy_alerts": 0,
    }

    deleted["activity_logs"] = DeviceActivityLog.query.filter(
        DeviceActivityLog.timestamp < raw_cutoff
    ).delete(synchronize_session=False)
    deleted["resource_logs"] = DeviceResourceLog.query.filter(
        DeviceResourceLog.timestamp < raw_cutoff
    ).delete(synchronize_session=False)
    deleted["application_logs"] = DeviceApplicationLog.query.filter(
        DeviceApplicationLog.timestamp < raw_cutoff
    ).delete(synchronize_session=False)
    deleted["tracking_samples"] = TrackingSample.query.filter(
        TrackingSample.received_at < raw_cutoff
    ).delete(synchronize_session=False)
    deleted["tracking_hourly_rollups"] = TrackingHourlyRollup.query.filter(
        TrackingHourlyRollup.bucket_hour < hourly_cutoff
    ).delete(synchronize_session=False)
    deleted["tracking_daily_rollups"] = TrackingDailyRollup.query.filter(
        TrackingDailyRollup.bucket_day < daily_cutoff
    ).delete(synchronize_session=False)

    # Purge typed-text policy alerts older than raw_cutoff (same 30-day window)
    try:
        from models.typed_text_policy_alert import TypedTextPolicyAlert
        deleted["typed_text_policy_alerts"] = TypedTextPolicyAlert.query.filter(
            TypedTextPolicyAlert.detected_at < raw_cutoff
        ).delete(synchronize_session=False)
    except Exception:
        pass  # table may not exist yet on older installs

    db.session.commit()

    return {
        "success": True,
        "raw_cutoff": raw_cutoff.isoformat(),
        "hourly_cutoff": hourly_cutoff.isoformat(),
        "daily_cutoff": daily_cutoff.isoformat(),
        "deleted": {key: int(value or 0) for key, value in deleted.items()},
    }
