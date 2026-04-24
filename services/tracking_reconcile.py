from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
import json
import logging
import threading
import time

from flask import has_request_context, request, session as flask_session
from sqlalchemy import text

from config import Config
from extensions import db
from models.audit_log import AuditLog
from models.tracked_device import (
    TrackedDevice,
    TrackedDeviceIpHistory,
    DeviceActivityLog,
    DeviceApplicationLog,
    DeviceResourceLog,
    TrackingSample,
)
from services.operational_error_handling import log_operational_exception, summarize_exception
from services.tracked_device_ip_change import apply_tracked_device_ip_change, TrackedDeviceIpSyncError

logger = logging.getLogger(__name__)

RECONCILIATION_LOCK_KEY = 937511
DISCOVERY_CACHE_MAX_AGE_SECONDS = 300
_PROCESS_RECONCILIATION_LOCK = threading.Lock()


@dataclass
class MergeResult:
    canonical_id: int
    duplicate_id: int
    reason: str
    dry_run: bool
    skipped: bool = False
    safety_violation: str | None = None
    moved_logs_count: dict[str, int] = field(default_factory=dict)

    @property
    def moved_logs_total(self) -> int:
        return int(sum(self.moved_logs_count.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "deleted_id": self.duplicate_id,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "skipped": self.skipped,
            "safety_violation": self.safety_violation,
            "moved_logs_count": self.moved_logs_count,
            "moved_logs_total": self.moved_logs_total,
        }


@dataclass
class DiscoveryCache:
    last_results: list[dict[str, Any]] = field(default_factory=list)
    last_discovery_at: float = 0.0
    force_discovery: bool = False
    scanner_factory: Callable[[], Any] | None = None
    last_relocation_plan: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReconcileReport:
    success: bool = False
    dry_run: bool = False
    lock_acquired: bool = False
    error_code: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    total_devices_before: int = 0
    total_devices_after: int = 0
    groups_scanned: int = 0
    duplicate_groups: int = 0
    canonical_by_group: dict[str, int] = field(default_factory=dict)
    proposed_deletes: list[int] = field(default_factory=list)
    deleted_ids: list[int] = field(default_factory=list)
    skipped_merges: list[dict[str, Any]] = field(default_factory=list)
    safety_violations: list[dict[str, Any]] = field(default_factory=list)
    moved_logs_count: dict[str, int] = field(
        default_factory=lambda: {"activity": 0, "resource": 0, "application": 0, "sample": 0}
    )
    probe_counts: dict[str, int] = field(
        default_factory=lambda: {"online": 0, "degraded": 0, "offline": 0}
    )
    offline_candidates: int = 0
    relocated_count: int = 0
    ip_changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "success": self.success,
            "dry_run": self.dry_run,
            "lock_acquired": self.lock_acquired,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_devices_before": self.total_devices_before,
            "total_devices_after": self.total_devices_after,
            "groups_scanned": self.groups_scanned,
            "duplicate_groups": self.duplicate_groups,
            "canonical_by_group": self.canonical_by_group,
            "proposed_deletes": self.proposed_deletes,
            "deleted_ids": self.deleted_ids,
            "skipped_merges": self.skipped_merges,
            "safety_violations": self.safety_violations,
            "moved_logs_count": self.moved_logs_count,
            "probe_counts": self.probe_counts,
            "offline_candidates": self.offline_candidates,
            "relocated_count": self.relocated_count,
            "ip_changes": self.ip_changes,
            "error_code": self.error_code,
            "error": self.error,
        }
        payload["reachable_devices"] = int(
            payload["probe_counts"].get("online", 0)
            + payload["probe_counts"].get("degraded", 0)
        )
        payload["offline_devices"] = int(payload["probe_counts"].get("offline", 0))
        return payload


_DISCOVERY_CACHE = DiscoveryCache()


def normalize_mac(mac: str | None) -> str | None:
    if mac is None:
        return None
    value = str(mac).strip().upper().replace("-", ":")
    if value in ("", "N/A", "UNKNOWN"):
        return None
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return None
    try:
        int("".join(parts), 16)
    except ValueError:
        return None
    return ":".join(parts)


def _dt_to_ts(dt: datetime | None) -> float:
    if not dt:
        return 0.0
    try:
        return float(dt.timestamp())
    except Exception:
        return 0.0


def group_tracking_candidates(
    tracked_devices: list[TrackedDevice],
) -> dict[str, list[TrackedDevice]]:
    grouped: dict[str, list[TrackedDevice]] = {}
    for device in tracked_devices:
        unique_client_id = (device.unique_client_id or "").strip()
        normalized_mac = normalize_mac(getattr(device, "mac_address", None))
        ip_address = (device.ip_address or "").strip()

        if unique_client_id:
            key = f"uid:{unique_client_id.lower()}"
        elif normalized_mac:
            key = f"mac:{normalized_mac}"
        elif ip_address:
            key = f"ip:{ip_address}"
        else:
            key = f"orphan:{device.id}"

        grouped.setdefault(key, []).append(device)
    return grouped


def choose_canonical(devices: list[TrackedDevice]) -> TrackedDevice:
    if not devices:
        raise ValueError("devices list cannot be empty")

    def score(device: TrackedDevice) -> tuple[int, int, int, float, float, int]:
        has_uid = 1 if (device.unique_client_id or "").strip() else 0
        has_mac = 1 if normalize_mac(getattr(device, "mac_address", None)) else 0
        has_name = 1 if (device.device_name or "").strip() else 0
        updated_ts = _dt_to_ts(getattr(device, "updated_at", None))
        created_ts = _dt_to_ts(getattr(device, "created_at", None))
        device_id = int(getattr(device, "id", 0) or 0)
        return (has_uid, has_mac, has_name, updated_ts, created_ts, device_id)

    return max(devices, key=score)


def _is_postgres() -> bool:
    try:
        return db.engine.url.get_backend_name() == "postgresql"
    except Exception:
        return False


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _audit_actor() -> tuple[int | None, str, str, str | None, str | None]:
    if has_request_context():
        user_id = flask_session.get("user_id")
        username = flask_session.get("username", "system")
        user_role = flask_session.get("role", "system")
        ip_address = request.remote_addr
        user_agent = (request.headers.get("User-Agent") or "")[:200]
        return user_id, username, user_role, ip_address, user_agent
    return None, "system", "system", None, "tracking_reconcile"


def _add_audit_entry(
    action: str,
    entity_id: int | None,
    entity_name: str | None,
    description: str,
    changes: dict[str, Any] | None = None,
) -> None:
    user_id, username, user_role, ip_address, user_agent = _audit_actor()
    db.session.add(
        AuditLog(
            user_id=user_id,
            username=username,
            user_role=user_role,
            action=action,
            entity_type="tracked_device",
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            changes=changes,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )


def _count_related_logs(device_id: int) -> dict[str, int]:
    return {
        "activity": int(
            DeviceActivityLog.query.filter_by(device_id=device_id).count()
        ),
        "resource": int(
            DeviceResourceLog.query.filter_by(device_id=device_id).count()
        ),
        "application": int(
            DeviceApplicationLog.query.filter_by(device_id=device_id).count()
        ),
        "sample": int(
            TrackingSample.query.filter_by(device_id=device_id).count()
        ),
    }


def _merge_metadata(canonical: TrackedDevice, duplicate: TrackedDevice) -> None:
    if not (canonical.employee_name or "").strip() and (duplicate.employee_name or "").strip():
        canonical.employee_name = duplicate.employee_name
    if not (canonical.department or "").strip() and (duplicate.department or "").strip():
        canonical.department = duplicate.department
    if not (canonical.notes or "").strip() and (duplicate.notes or "").strip():
        canonical.notes = duplicate.notes
    if not (canonical.hostname or "").strip() and (duplicate.hostname or "").strip():
        canonical.hostname = duplicate.hostname
    if not (canonical.ip_address or "").strip() and (duplicate.ip_address or "").strip():
        canonical.ip_address = duplicate.ip_address
    if not (canonical.unique_client_id or "").strip() and (duplicate.unique_client_id or "").strip():
        canonical.unique_client_id = duplicate.unique_client_id
    canonical_mac = normalize_mac(getattr(canonical, "mac_address", None))
    duplicate_mac = normalize_mac(getattr(duplicate, "mac_address", None))
    if not canonical_mac and duplicate_mac:
        canonical.mac_address = duplicate_mac


def _merge_tracking_samples(canonical_id: int, duplicate_id: int) -> int:
    moved_samples = 0
    duplicate_samples = TrackingSample.query.filter_by(device_id=duplicate_id).all()
    for duplicate_sample in duplicate_samples:
        canonical_sample = TrackingSample.query.filter_by(
            device_id=canonical_id,
            idempotency_key=duplicate_sample.idempotency_key,
        ).first()
        if canonical_sample:
            DeviceActivityLog.query.filter_by(sample_id=duplicate_sample.id).update(
                {"sample_id": canonical_sample.id},
                synchronize_session=False,
            )
            DeviceResourceLog.query.filter_by(sample_id=duplicate_sample.id).update(
                {"sample_id": canonical_sample.id},
                synchronize_session=False,
            )
            DeviceApplicationLog.query.filter_by(sample_id=duplicate_sample.id).update(
                {"sample_id": canonical_sample.id},
                synchronize_session=False,
            )
            db.session.delete(duplicate_sample)
        else:
            duplicate_sample.device_id = canonical_id
        moved_samples += 1
    return moved_samples


def merge_device_rows(
    canonical: TrackedDevice,
    duplicate: TrackedDevice,
    dry_run: bool,
) -> MergeResult:
    duplicate_counts = _count_related_logs(duplicate.id)
    duplicate_mac = normalize_mac(getattr(duplicate, "mac_address", None))

    if not duplicate_mac:
        reason = "BLANK_MAC"
    elif (
        (canonical.unique_client_id or "").strip()
        and (duplicate.unique_client_id or "").strip()
        and canonical.unique_client_id == duplicate.unique_client_id
    ):
        reason = "DUPLICATE_IDENTITY"
    elif (
        (canonical.ip_address or "").strip()
        and (duplicate.ip_address or "").strip()
        and canonical.ip_address == duplicate.ip_address
    ):
        reason = "DUPLICATE_IP"
    else:
        reason = "DUPLICATE_IDENTITY"

    canonical_uid = (canonical.unique_client_id or "").strip()
    duplicate_uid = (duplicate.unique_client_id or "").strip()
    if duplicate_uid and canonical_uid != duplicate_uid:
        violation = (
            "Duplicate row has unique_client_id that does not match canonical unique_client_id."
        )
        return MergeResult(
            canonical_id=canonical.id,
            duplicate_id=duplicate.id,
            reason="SAFETY_UNIQUE_CLIENT_ID_VIOLATION",
            dry_run=dry_run,
            skipped=True,
            safety_violation=violation,
            moved_logs_count=duplicate_counts,
        )

    if dry_run:
        return MergeResult(
            canonical_id=canonical.id,
            duplicate_id=duplicate.id,
            reason=reason,
            dry_run=True,
            moved_logs_count=duplicate_counts,
        )

    DeviceActivityLog.query.filter_by(device_id=duplicate.id).update(
        {"device_id": canonical.id},
        synchronize_session=False,
    )
    DeviceResourceLog.query.filter_by(device_id=duplicate.id).update(
        {"device_id": canonical.id},
        synchronize_session=False,
    )
    DeviceApplicationLog.query.filter_by(device_id=duplicate.id).update(
        {"device_id": canonical.id},
        synchronize_session=False,
    )
    duplicate_counts["sample"] = _merge_tracking_samples(canonical.id, duplicate.id)

    _merge_metadata(canonical, duplicate)
    canonical.updated_at = datetime.utcnow()

    db.session.delete(duplicate)
    _add_audit_entry(
        action="reconcile_merge_delete",
        entity_id=canonical.id,
        entity_name=canonical.device_name,
        description=f"Merged tracked device {duplicate.id} into canonical {canonical.id} ({reason}).",
        changes={
            "canonical_id": canonical.id,
            "deleted_id": duplicate.id,
            "reason": reason,
            "moved_logs_count": duplicate_counts,
            "dry_run": False,
        },
    )

    return MergeResult(
        canonical_id=canonical.id,
        duplicate_id=duplicate.id,
        reason=reason,
        dry_run=False,
        moved_logs_count=duplicate_counts,
    )


def persist_probe_state(
    device: TrackedDevice,
    probe_result: dict[str, Any] | None,
    dry_run: bool = False,
) -> None:
    now = datetime.utcnow()
    payload = probe_result if isinstance(probe_result, dict) else {}
    availability_status = str(payload.get("availability_status") or "offline").strip().lower()
    if availability_status not in ("online", "degraded", "offline"):
        availability_status = "offline"

    tracking_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    metrics_available = bool(payload.get("metrics_available"))
    probe_method = payload.get("probe_method")
    probe_error_code = payload.get("probe_error_code")

    if availability_status == "offline" and not probe_error_code:
        probe_error_code = "DEVICE_NO_IP" if not (device.ip_address or "").strip() else "AGENT_UNREACHABLE"

    if dry_run:
        return

    device.availability_status = availability_status
    device.metrics_available = metrics_available
    device.tracking_data = json.dumps(tracking_data) if tracking_data else None
    device.probe_error_code = probe_error_code
    device.probe_method = probe_method
    device.last_probe_at = now
    if availability_status in ("online", "degraded"):
        device.last_seen = now
    device.updated_at = now

    # Keep durable availability event history with deterministic heartbeat policy.
    from services.tracking_workstation import persist_availability_event

    persist_availability_event(
        device=device,
        probe_result={
            "availability_status": availability_status,
            "metrics_available": metrics_available,
            "probe_method": probe_method,
            "probe_error_code": probe_error_code,
            "observed_at": now,
        },
        source="reconcile",
        dry_run=dry_run,
    )


def _extract_result_identity(device_payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    unique_client_id = (device_payload.get("unique_client_id") or "").strip() or None
    mac_address = normalize_mac(device_payload.get("mac_address"))
    hostname = (device_payload.get("hostname") or "").strip() or None
    return unique_client_id, mac_address, hostname


def attempt_identity_relocation(
    offline_devices: list[TrackedDevice],
    discovery_cache: DiscoveryCache,
    dry_run: bool,
) -> int:
    discovery_cache.last_relocation_plan = []
    if not offline_devices:
        return 0

    now_ts = time.time()
    use_cached = bool(
        discovery_cache.last_results
        and discovery_cache.last_discovery_at
        and not discovery_cache.force_discovery
        and (now_ts - discovery_cache.last_discovery_at) <= DISCOVERY_CACHE_MAX_AGE_SECONDS
    )

    if use_cached:
        discovered = discovery_cache.last_results
    else:
        if not discovery_cache.scanner_factory:
            return 0
        scanner = discovery_cache.scanner_factory()
        if hasattr(scanner, "timeout"):
            scanner.timeout = max(float(getattr(scanner, "timeout", 2.0)), 2.5)
        discovered = scanner.scan_for_trackable_devices()
        discovery_cache.last_results = discovered if isinstance(discovered, list) else []
        discovery_cache.last_discovery_at = now_ts

    by_uid: dict[str, dict[str, Any]] = {}
    by_mac: dict[str, dict[str, Any]] = {}
    for result in discovery_cache.last_results:
        if not isinstance(result, dict):
            continue
        status = str(result.get("status") or "").strip().lower()
        availability_status = str(result.get("availability_status") or "").strip().lower()
        if status != "tracking_active" and availability_status not in ("online", "degraded"):
            continue

        uid, mac, _ = _extract_result_identity(result)
        if uid and uid not in by_uid:
            by_uid[uid] = result
        if mac and mac not in by_mac:
            by_mac[mac] = result

    updated_count = 0
    for device in offline_devices:
        current_ip = (device.ip_address or "").strip()
        target: dict[str, Any] | None = None

        uid = (device.unique_client_id or "").strip()
        if uid:
            target = by_uid.get(uid)

        if not target:
            mac = normalize_mac(device.mac_address)
            if mac:
                target = by_mac.get(mac)

        if not target:
            continue

        new_ip = (target.get("ip") or "").strip()
        if not new_ip or new_ip == current_ip:
            continue

        update_item = {
            "device_id": device.id,
            "device_name": device.device_name,
            "old_ip": current_ip,
            "new_ip": new_ip,
            "reason": "IDENTITY_RELOCATION",
            "status": "planned" if dry_run else "pending",
        }
        discovery_cache.last_relocation_plan.append(update_item)

        if dry_run:
            updated_count += 1
            continue

        new_hostname = (target.get("hostname") or "").strip() or None
        try:
            ip_change = apply_tracked_device_ip_change(
                tracked_device=device,
                new_ip=new_ip,
                resolved_hostname=new_hostname,
                now_utc=datetime.utcnow(),
                payload_ip=new_ip,
                payload_candidates=[new_ip],
                transport_remote_ip=None,
                transport_forwarded_for=None,
                agent_key_id=None,
                reason='RECONCILE_RELOCATION',
                ip_source='identity_relocation',
                network_signature=None,
                update_last_seen=False,
                update_updated_at=True,
                sync_reason='RECONCILE_RELOCATION',
            )
        except TrackedDeviceIpSyncError as exc:
            update_item["status"] = "blocked"
            update_item["sync_reason_code"] = exc.reason_code
            collision_device_id = exc.sync_result.get("collision_device_id")
            if collision_device_id:
                update_item["collision_device_id"] = collision_device_id
            logger.warning(
                "[TrackingReconcile] relocation blocked device_id=%s old_ip=%s new_ip=%s reason=%s collision_device_id=%s",
                device.id,
                current_ip,
                new_ip,
                exc.reason_code,
                collision_device_id,
            )
            continue

        sync_result = (ip_change or {}).get("sync_result") or {}
        if sync_result.get("resolution_action"):
            update_item["resolution_action"] = sync_result["resolution_action"]
        if sync_result.get("hostname_updated"):
            update_item["hostname_updated"] = True
        update_item["status"] = "applied"
        updated_count += 1

    return updated_count


class _ReconciliationLockContext:
    def __init__(self):
        self.process_lock_acquired = False
        self.db_lock_acquired = False

    def acquire(self) -> tuple[bool, str | None]:
        if not _PROCESS_RECONCILIATION_LOCK.acquire(blocking=False):
            return False, "process_lock_busy"
        self.process_lock_acquired = True

        if _is_postgres():
            try:
                locked = bool(
                    db.session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                        {"lock_key": RECONCILIATION_LOCK_KEY},
                    ).scalar()
                )
            except Exception:
                self.release()
                raise

            if not locked:
                self.release()
                return False, "db_lock_busy"
            self.db_lock_acquired = True

        return True, None

    def release(self) -> None:
        if self.process_lock_acquired and _PROCESS_RECONCILIATION_LOCK.locked():
            _PROCESS_RECONCILIATION_LOCK.release()
        self.process_lock_acquired = False
        self.db_lock_acquired = False


def is_reconciliation_locked() -> bool:
    if _PROCESS_RECONCILIATION_LOCK.locked():
        return True
    if not _is_postgres():
        return False

    try:
        with db.engine.connect() as conn:
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": RECONCILIATION_LOCK_KEY},
                ).scalar()
            )
            if acquired:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": RECONCILIATION_LOCK_KEY},
                )
                return False
            return True
    except Exception:
        return _PROCESS_RECONCILIATION_LOCK.locked()


def _normalize_probe_result(device: TrackedDevice, probe_result: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(probe_result, dict):
        return probe_result
    return {
        "availability_status": "offline",
        "metrics_available": False,
        "probe_error_code": "DEVICE_NO_IP"
        if not (device.ip_address or "").strip()
        else "AGENT_UNREACHABLE",
        "probe_method": "none",
        "data": {},
    }


def run_reconciliation(
    force_discovery: bool = False,
    dry_run: bool | None = None,
    scanner_factory: Callable[[], Any] | None = None,
) -> ReconcileReport:
    report = ReconcileReport()
    report.started_at = datetime.utcnow().isoformat()
    report.dry_run = _coerce_bool(dry_run, Config.TRACKING_RECONCILE_DRYRUN)

    if scanner_factory is None:
        from routes.tracking import NetworkScanner  # Local import to avoid module cycle at import-time.

        scanner_factory = NetworkScanner

    lock = _ReconciliationLockContext()
    try:
        acquired, reason = lock.acquire()
        if not acquired:
            report.error_code = "TRACKING_RECONCILIATION_BUSY"
            report.error = (
                "Tracking reconciliation is already running."
                if reason
                else "Tracking reconciliation is unavailable."
            )
            report.finished_at = datetime.utcnow().isoformat()
            return report

        report.lock_acquired = True
        devices = TrackedDevice.query.filter(
            db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None))
        ).order_by(TrackedDevice.id.asc()).all()
        report.total_devices_before = len(devices)

        grouped = group_tracking_candidates(devices)
        report.groups_scanned = len(grouped)

        for group_key, members in grouped.items():
            if len(members) <= 1:
                continue

            report.duplicate_groups += 1
            canonical = choose_canonical(members)
            report.canonical_by_group[group_key] = canonical.id

            for duplicate in members:
                if duplicate.id == canonical.id:
                    continue

                result = merge_device_rows(canonical, duplicate, report.dry_run)
                for key, value in result.moved_logs_count.items():
                    report.moved_logs_count[key] = report.moved_logs_count.get(key, 0) + int(value)

                if result.safety_violation:
                    report.safety_violations.append(
                        {
                            "group": group_key,
                            "canonical_id": result.canonical_id,
                            "duplicate_id": result.duplicate_id,
                            "message": result.safety_violation,
                        }
                    )
                    if not report.dry_run:
                        _add_audit_entry(
                            action="reconcile_safety_skip",
                            entity_id=result.duplicate_id,
                            entity_name=duplicate.device_name,
                            description=(
                                f"Safety violation skipped duplicate merge for tracked device {result.duplicate_id}."
                            ),
                            changes=result.to_dict(),
                        )

                if result.skipped:
                    report.skipped_merges.append(result.to_dict())
                    continue

                report.proposed_deletes.append(result.duplicate_id)
                if not report.dry_run:
                    report.deleted_ids.append(result.duplicate_id)

        if not report.dry_run:
            db.session.flush()
            devices = TrackedDevice.query.filter(
                db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None))
            ).order_by(TrackedDevice.id.asc()).all()

        scanner = scanner_factory()
        if hasattr(scanner, "timeout"):
            scanner.timeout = max(float(getattr(scanner, "timeout", 2.0)), 2.5)

        offline_devices: list[TrackedDevice] = []
        for device in devices:
            if (device.ip_address or "").strip():
                try:
                    probe_result = scanner.check_tracking_service(
                        device.ip_address,
                        profile="interactive",
                    )
                except TypeError:
                    probe_result = scanner.check_tracking_service(device.ip_address)
                except Exception as exc:
                    logger.warning(
                        "[TrackingReconcile] probe failed for device_id=%s ip=%s error=%s",
                        device.id,
                        device.ip_address,
                        exc,
                    )
                    probe_result = None
            else:
                probe_result = None

            normalized_probe = _normalize_probe_result(device, probe_result)
            status = str(normalized_probe.get("availability_status") or "offline").strip().lower()
            if status not in ("online", "degraded", "offline"):
                status = "offline"
            report.probe_counts[status] = report.probe_counts.get(status, 0) + 1

            if status == "offline":
                offline_devices.append(device)

            persist_probe_state(device, normalized_probe, dry_run=report.dry_run)

        report.offline_candidates = len(offline_devices)

        _DISCOVERY_CACHE.force_discovery = bool(force_discovery)
        _DISCOVERY_CACHE.scanner_factory = scanner_factory
        report.relocated_count = attempt_identity_relocation(
            offline_devices=offline_devices,
            discovery_cache=_DISCOVERY_CACHE,
            dry_run=report.dry_run,
        )
        report.ip_changes = list(_DISCOVERY_CACHE.last_relocation_plan)

        if report.dry_run:
            logger.info(
                "TRACKING_RECONCILE_DRYRUN report=%s",
                json.dumps(report.to_dict(), default=str),
            )
            db.session.rollback()
        else:
            for relocation in report.ip_changes:
                _add_audit_entry(
                    action="reconcile_ip_relocation",
                    entity_id=relocation.get("device_id"),
                    entity_name=relocation.get("device_name"),
                    description=(
                        f"Updated tracked device IP {relocation.get('old_ip')} -> {relocation.get('new_ip')}."
                    ),
                    changes={
                        "canonical_id": relocation.get("device_id"),
                        "deleted_id": None,
                        "reason": relocation.get("reason"),
                        "moved_logs_count": {"activity": 0, "resource": 0, "application": 0, "sample": 0},
                        "dry_run": False,
                    },
                )
            db.session.commit()

        report.total_devices_after = len(devices)
        report.success = True
    except Exception as exc:
        db.session.rollback()
        report.error_code = "TRACKING_RECONCILIATION_FAILED"
        report.error = summarize_exception(exc)
        log_operational_exception(
            logger,
            "[TrackingReconcile] reconciliation failed",
            exc,
            error_code=report.error_code,
        )
    finally:
        lock.release()
        report.finished_at = datetime.utcnow().isoformat()

    return report
