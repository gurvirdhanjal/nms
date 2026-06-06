from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from flask import has_request_context, request, session
from sqlalchemy import func, or_, text

from config import Config
from extensions import db
from models.alert_fanout_task import AlertFanoutTask
from models.audit_log import AuditLog
from models.device import Device
from models.device_effective_policy_cache import DeviceEffectivePolicyCache
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.policy_rebuild_task import PolicyRebuildTask
from models.restricted_site_policy import (
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
    TrackingAgentKeyBinding,
)
from models.scan_history import DeviceScanHistory
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    DeviceResourceLog,
    RemoteDeviceScanHistory,
    TrackedDevice,
    TrackedDeviceAvailabilityEvent,
    TrackedDeviceIpHistory,
    TrackingDailyRollup,
    TrackingHistoryIntegrityAudit,
    TrackingHourlyRollup,
    TrackingSample,
)
from models.tracking_sync_envelope import TrackingSyncEnvelope
from services.tracking_reconcile import normalize_mac
from services.tracking_sync_intake_service import (
    build_envelope_dedupe_key,
    queue_sync_envelope,
    upsert_sync_envelope,
)


@dataclass(frozen=True)
class ActorContext:
    username: str
    role: str
    user_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class InventoryEvidence:
    device_id: int
    authoritative_mac: str
    resolution_path: str
    scan_timestamp: datetime | None = None


@dataclass(frozen=True)
class IdentityInput:
    normalized_payload_mac: str | None
    unique_client_id: str | None
    hostname: str | None
    resolved_ip: str | None
    payload_ip: str | None
    payload_ip_candidates: tuple[str, ...]
    network_signature: str | None
    now_utc: datetime
    device_name_hint: str | None = None

    @property
    def dedupe_key(self) -> str:
        return build_envelope_dedupe_key(
            normalized_mac=self.normalized_payload_mac,
            unique_client_id=self.unique_client_id,
            network_signature=self.network_signature,
            hostname=self.hostname,
            resolved_ip=self.resolved_ip or self.payload_ip,
        )

    @property
    def ip_candidates(self) -> tuple[str, ...]:
        candidates: list[str] = []
        seen = set()
        for candidate in self.payload_ip_candidates:
            value = str(candidate or "").strip()
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)
        for candidate in (self.payload_ip, self.resolved_ip):
            value = str(candidate or "").strip()
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)
        return tuple(candidates)


@dataclass
class IdentityResolutionResult:
    device: TrackedDevice | None
    identity_status: str
    visible_in_tracking: bool
    authoritative_mac: str | None
    authoritative_mac_source: str
    resolution_path: str
    resolution_source: str
    resolved_inventory_device_id: int | None = None
    merged_duplicate_device_id: int | None = None
    merged_duplicate_device_ids: list[int] = field(default_factory=list)
    merge_reason: str | None = None
    envelope: TrackingSyncEnvelope | None = None
    created_device: bool = False
    identity_confirmed: bool = False


def build_actor_context(
    *,
    username: str | None = None,
    role: str | None = None,
    user_id: int | None = None,
) -> ActorContext:
    if has_request_context():
        actor_username = str(username or session.get("username") or "system").strip() or "system"
        actor_role = str(role or session.get("role") or "system").strip() or "system"
        actor_user_id = user_id if user_id is not None else session.get("user_id")
        return ActorContext(
            username=actor_username,
            role=actor_role,
            user_id=int(actor_user_id) if actor_user_id is not None else None,
            ip_address=request.remote_addr,
            user_agent=(request.headers.get("User-Agent") or "")[:200],
        )

    return ActorContext(
        username=str(username or "system").strip() or "system",
        role=str(role or "system").strip() or "system",
        user_id=int(user_id) if user_id is not None else None,
        ip_address=None,
        user_agent=None,
    )


def build_identity_input(
    *,
    normalized_payload_mac: str | None,
    unique_client_id: str | None,
    hostname: str | None,
    resolved_ip: str | None,
    payload_ip: str | None,
    payload_ip_candidates: list[str] | tuple[str, ...] | None,
    network_signature: str | None,
    now_utc: datetime | None = None,
    device_name_hint: str | None = None,
) -> IdentityInput:
    normalized_candidates = []
    seen = set()
    for candidate in payload_ip_candidates or []:
        value = str(candidate or "").strip()
        if value and value not in seen:
            seen.add(value)
            normalized_candidates.append(value)

    return IdentityInput(
        normalized_payload_mac=normalize_mac(normalized_payload_mac),
        unique_client_id=str(unique_client_id or "").strip() or None,
        hostname=str(hostname or "").strip() or None,
        resolved_ip=str(resolved_ip or "").strip() or None,
        payload_ip=str(payload_ip or "").strip() or None,
        payload_ip_candidates=tuple(normalized_candidates),
        network_signature=str(network_signature or "").strip() or None,
        now_utc=now_utc or datetime.utcnow(),
        device_name_hint=str(device_name_hint or "").strip() or None,
    )


def build_identity_input_for_tracked_device(
    tracked_device: TrackedDevice,
    *,
    now_utc: datetime | None = None,
) -> IdentityInput:
    return build_identity_input(
        normalized_payload_mac=tracked_device.mac_address,
        unique_client_id=tracked_device.unique_client_id,
        hostname=tracked_device.hostname,
        resolved_ip=tracked_device.ip_address,
        payload_ip=tracked_device.ip_address,
        payload_ip_candidates=[tracked_device.ip_address] if tracked_device.ip_address else [],
        network_signature=None,
        now_utc=now_utc,
        device_name_hint=tracked_device.device_name,
    )


def _is_auto_discovered(device: TrackedDevice | None) -> bool:
    if device is None:
        return False
    employee_name = str(getattr(device, "employee_name", "") or "").strip().lower()
    notes = str(getattr(device, "notes", "") or "").strip().lower()
    return employee_name == "auto-discovered" or "auto-registered by service agent sync" in notes


def _fresh_online_inventory_base_query(now_utc: datetime):
    freshness_minutes = max(
        1,
        int(getattr(Config, "TRACKING_IDENTITY_SCAN_FRESHNESS_MINUTES", 15) or 15),
    )
    fresh_cutoff = now_utc - timedelta(minutes=freshness_minutes)
    latest_scan_subq = (
        db.session.query(
            DeviceScanHistory.device_ip.label("device_ip"),
            func.max(DeviceScanHistory.scan_id).label("max_scan_id"),
        )
        .filter(DeviceScanHistory.scan_timestamp >= fresh_cutoff)
        .group_by(DeviceScanHistory.device_ip)
        .subquery()
    )
    return (
        db.session.query(Device, DeviceScanHistory)
        .join(latest_scan_subq, Device.device_ip == latest_scan_subq.c.device_ip)
        .join(DeviceScanHistory, DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id)
        .filter(
            Device.is_active.is_(True),
            Device.device_ip.isnot(None),
            Device.device_ip != "",
            func.lower(DeviceScanHistory.status) == "online",
            DeviceScanHistory.scan_timestamp >= fresh_cutoff,
        )
    )


def _resolve_inventory_candidate(
    identity_input: IdentityInput,
    *,
    linked_inventory_device_id: int | None = None,
) -> tuple[InventoryEvidence | None, bool]:
    # IP narrows the candidate set, but MAC remains the durable identity key.
    if linked_inventory_device_id:
        linked_device = Device.query.get(int(linked_inventory_device_id))
        authoritative_mac = normalize_mac(getattr(linked_device, "macaddress", None))
        if linked_device is not None and authoritative_mac:
            return (
                InventoryEvidence(
                    device_id=int(linked_device.device_id),
                    authoritative_mac=authoritative_mac,
                    resolution_path="active_link_match",
                    scan_timestamp=None,
                ),
                False,
            )

    base_query = _fresh_online_inventory_base_query(identity_input.now_utc)
    ip_candidates = [value for value in identity_input.ip_candidates if value]
    hostname_value = str(identity_input.hostname or "").strip().lower()

    ip_matches = []
    if ip_candidates:
        ip_matches = (
            base_query.filter(Device.device_ip.in_(ip_candidates))
            .order_by(Device.device_id.asc())
            .all()
        )

    unique_ip_device_ids = {int(device.device_id) for device, _scan in ip_matches}
    if len(unique_ip_device_ids) == 1:
        device, scan = ip_matches[0]
        authoritative_mac = normalize_mac(getattr(device, "macaddress", None))
        if authoritative_mac:
            return (
                InventoryEvidence(
                    device_id=int(device.device_id),
                    authoritative_mac=authoritative_mac,
                    resolution_path="inventory_ip_match",
                    scan_timestamp=getattr(scan, "scan_timestamp", None),
                ),
                False,
            )
    if len(unique_ip_device_ids) > 1:
        return None, True

    if not hostname_value:
        return None, False

    hostname_matches = (
        base_query.filter(func.lower(func.coalesce(Device.hostname, "")) == hostname_value)
        .order_by(Device.device_id.asc())
        .all()
    )
    unique_hostname_device_ids = {int(device.device_id) for device, _scan in hostname_matches}
    if len(unique_hostname_device_ids) == 1:
        device, scan = hostname_matches[0]
        authoritative_mac = normalize_mac(getattr(device, "macaddress", None))
        if authoritative_mac:
            return (
                InventoryEvidence(
                    device_id=int(device.device_id),
                    authoritative_mac=authoritative_mac,
                    resolution_path="inventory_hostname_match",
                    scan_timestamp=getattr(scan, "scan_timestamp", None),
                ),
                False,
            )
    if len(unique_hostname_device_ids) > 1:
        return None, True

    return None, False


def _find_tracked_by_uuid(unique_client_id: str | None) -> TrackedDevice | None:
    if not unique_client_id:
        return None
    return TrackedDevice.query.filter_by(unique_client_id=unique_client_id).first()


def _find_tracked_by_mac(mac_address: str | None) -> TrackedDevice | None:
    if not mac_address:
        return None
    return TrackedDevice.query.filter_by(mac_address=mac_address).first()


def _resolve_active_link_for_tracked(tracked_device_id: int | None) -> DeviceIdentityLink | None:
    if not tracked_device_id:
        return None
    return (
        DeviceIdentityLink.query.filter_by(tracked_device_id=int(tracked_device_id), is_active=True)
        .order_by(DeviceIdentityLink.id.desc())
        .first()
    )


def _lock_tracked_devices(device_ids: list[int]) -> dict[int, TrackedDevice]:
    if not device_ids:
        return {}
    rows = (
        TrackedDevice.query.filter(TrackedDevice.id.in_(sorted({int(value) for value in device_ids if value})))
        .order_by(TrackedDevice.id.asc())
        .with_for_update()
        .all()
    )
    return {int(row.id): row for row in rows}


def _lock_active_links(
    *,
    inventory_device_id: int | None = None,
    tracked_device_ids: list[int] | None = None,
) -> list[DeviceIdentityLink]:
    filters = []
    if inventory_device_id is not None:
        filters.append(DeviceIdentityLink.device_id == int(inventory_device_id))
    tracked_ids = [int(value) for value in (tracked_device_ids or []) if value is not None]
    if tracked_ids:
        filters.append(DeviceIdentityLink.tracked_device_id.in_(tracked_ids))
    if not filters:
        return []
    return (
        DeviceIdentityLink.query.filter(DeviceIdentityLink.is_active.is_(True))
        .filter(or_(*filters))
        .order_by(DeviceIdentityLink.id.asc())
        .with_for_update()
        .all()
    )


def _lock_inventory_device(device_id: int | None) -> Device | None:
    if device_id is None:
        return None
    # NOWAIT: fail immediately instead of blocking up to lock_timeout (5 s).
    # The caller (api_tracking_sync) catches LockNotAvailable and returns 503
    # so the agent retries — much better than silently hanging for 5 s then 500-ing.
    return (
        Device.query.filter(Device.device_id == int(device_id))
        .with_for_update(nowait=True)
        .first()
    )


def _lock_pending_envelope(dedupe_key: str) -> TrackingSyncEnvelope | None:
    if not dedupe_key:
        return None
    return (
        TrackingSyncEnvelope.query.filter_by(dedupe_key=dedupe_key)
        .order_by(TrackingSyncEnvelope.received_at.desc(), TrackingSyncEnvelope.id.desc())
        .with_for_update()
        .first()
    )


def _lock_envelope_key(dedupe_key: str) -> None:
    if not dedupe_key:
        return
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:dedupe_key))"),
        {"dedupe_key": dedupe_key},
    )


def _select_survivor(
    *,
    linked_tracked: TrackedDevice | None,
    authoritative_tracked: TrackedDevice | None,
    tracked_by_uuid: TrackedDevice | None,
    candidates: list[TrackedDevice],
) -> TrackedDevice | None:
    if linked_tracked is not None:
        return linked_tracked
    if authoritative_tracked is not None:
        return authoritative_tracked
    if tracked_by_uuid is not None:
        return tracked_by_uuid
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda device: (
            1 if _is_auto_discovered(device) else 0,
            device.created_at or datetime.utcnow(),
            int(device.id or 0),
        ),
    )
    return ranked[0]


def _determine_merge_reason(
    *,
    linked_tracked: TrackedDevice | None,
    authoritative_tracked: TrackedDevice | None,
    tracked_by_uuid: TrackedDevice | None,
    survivor: TrackedDevice,
    loser: TrackedDevice,
) -> str:
    if linked_tracked is not None and int(linked_tracked.id) != int(survivor.id):
        return "active_link_conflict"
    if authoritative_tracked is not None and tracked_by_uuid is not None and int(authoritative_tracked.id) != int(tracked_by_uuid.id):
        return "authoritative_mac_conflict"
    if tracked_by_uuid is not None and int(tracked_by_uuid.id) == int(loser.id):
        return "uuid_conflict"
    return "cleanup_inventory_mac_match"


def _build_audit_log(
    *,
    actor: ActorContext,
    action: str,
    entity_type: str,
    entity_id: int | None,
    entity_name: str | None,
    description: str,
    changes: dict[str, Any] | None = None,
) -> None:
    db.session.add(
        AuditLog(
            user_id=actor.user_id,
            username=actor.username,
            user_role=actor.role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            changes=changes,
            ip_address=actor.ip_address,
            user_agent=actor.user_agent,
        )
    )


def _upsert_active_link(
    *,
    inventory_device: Device,
    survivor: TrackedDevice,
    authoritative_mac: str,
    actor: ActorContext,
    resolution_path: str,
    touched_links: list[DeviceIdentityLink],
) -> DeviceIdentityLink:
    existing = None
    for link in touched_links:
        if int(link.device_id) == int(inventory_device.device_id) and int(link.tracked_device_id) == int(survivor.id):
            existing = link
        elif int(link.device_id) == int(inventory_device.device_id) or int(link.tracked_device_id) == int(survivor.id):
            link.is_active = False

    if existing is None:
        existing = DeviceIdentityLink(
            device_id=int(inventory_device.device_id),
            tracked_device_id=int(survivor.id),
            normalized_mac=authoritative_mac,
            link_source="scanner_inventory_sync",
            confidence=100,
            is_active=True,
            resolved_by=actor.username,
            resolution_reason=resolution_path,
        )
        db.session.add(existing)
    else:
        existing.normalized_mac = authoritative_mac
        existing.link_source = "scanner_inventory_sync"
        existing.confidence = max(int(existing.confidence or 0), 100)
        existing.is_active = True
        existing.resolved_by = actor.username
        existing.resolution_reason = resolution_path
    db.session.flush()
    return existing


def _coalesce_datetime(first_value, second_value):
    if first_value and second_value:
        return max(first_value, second_value)
    return first_value or second_value


def _weighted_average(current_avg, current_count, loser_avg, loser_count):
    current_weight = int(current_count or 0)
    loser_weight = int(loser_count or 0)
    total_weight = current_weight + loser_weight
    if total_weight <= 0:
        return current_avg or loser_avg
    current_value = float(current_avg or 0.0)
    loser_value = float(loser_avg or 0.0)
    return ((current_value * current_weight) + (loser_value * loser_weight)) / total_weight


def _map_sample_id(sample_id, sample_map: dict[int, int] | None = None):
    if sample_id is None:
        return None
    try:
        normalized_sample_id = int(sample_id)
    except (TypeError, ValueError):
        return None
    if sample_map and normalized_sample_id in sample_map:
        return int(sample_map[normalized_sample_id])
    return normalized_sample_id


def _merge_survivor_metadata(survivor: TrackedDevice, loser: TrackedDevice) -> None:
    if (not survivor.unique_client_id) and loser.unique_client_id:
        survivor.unique_client_id = loser.unique_client_id
    if (not survivor.hostname) and loser.hostname:
        survivor.hostname = loser.hostname
    if (not survivor.device_name) and loser.device_name:
        survivor.device_name = loser.device_name
    if (_is_auto_discovered(survivor) or not survivor.employee_name) and loser.employee_name and not _is_auto_discovered(loser):
        survivor.employee_name = loser.employee_name
    if (not survivor.notes) and loser.notes:
        survivor.notes = loser.notes
    if (not survivor.ip_address) and loser.ip_address:
        survivor.ip_address = loser.ip_address
    if survivor.site_id is None and loser.site_id is not None:
        survivor.site_id = loser.site_id
    if survivor.department_id is None and loser.department_id is not None:
        survivor.department_id = loser.department_id
    if (not survivor.department) and loser.department:
        survivor.department = loser.department

    survivor.last_seen = _coalesce_datetime(survivor.last_seen, loser.last_seen)
    survivor.updated_at = _coalesce_datetime(survivor.updated_at, loser.updated_at) or datetime.utcnow()
    survivor.last_agent_sync_at = _coalesce_datetime(survivor.last_agent_sync_at, loser.last_agent_sync_at)
    survivor.last_policy_sync_at = _coalesce_datetime(survivor.last_policy_sync_at, loser.last_policy_sync_at)
    survivor.last_probe_at = _coalesce_datetime(survivor.last_probe_at, loser.last_probe_at)
    if not survivor.last_agent_sync_ip and loser.last_agent_sync_ip:
        survivor.last_agent_sync_ip = loser.last_agent_sync_ip
    if not survivor.last_policy_version_seen and loser.last_policy_version_seen:
        survivor.last_policy_version_seen = loser.last_policy_version_seen

    priority = {"online": 3, "degraded": 2, "offline": 1}
    survivor_status = str(survivor.availability_status or "offline").strip().lower()
    loser_status = str(loser.availability_status or "offline").strip().lower()
    if priority.get(loser_status, 0) > priority.get(survivor_status, 0):
        survivor.availability_status = loser_status
        survivor.metrics_available = bool(loser.metrics_available)
        survivor.probe_error_code = loser.probe_error_code
        survivor.probe_method = loser.probe_method
        survivor.tracking_data = loser.tracking_data or survivor.tracking_data


def _merge_tracking_samples(survivor: TrackedDevice, loser: TrackedDevice) -> dict[int, int]:
    sample_map: dict[int, int] = {}
    survivor_samples = {
        str(sample.idempotency_key): sample
        for sample in TrackingSample.query.filter_by(device_id=int(survivor.id)).all()
    }
    loser_samples = TrackingSample.query.filter_by(device_id=int(loser.id)).order_by(TrackingSample.id.asc()).all()
    for sample in loser_samples:
        existing = survivor_samples.get(str(sample.idempotency_key))
        if existing is None:
            sample.device_id = int(survivor.id)
            survivor_samples[str(sample.idempotency_key)] = sample
            continue
        sample_map[int(sample.id)] = int(existing.id)
        db.session.delete(sample)
    db.session.flush()
    return sample_map


def _merge_exact_duplicate_rows(
    *,
    model,
    survivor_device_id: int,
    loser_device_id: int,
    sample_map: dict[int, int] | None = None,
    duplicate_key_builder,
) -> None:
    survivor_rows = model.query.filter_by(device_id=int(survivor_device_id)).all()
    survivor_keys = {
        duplicate_key_builder(row, sample_map=sample_map)
        for row in survivor_rows
    }
    loser_rows = model.query.filter_by(device_id=int(loser_device_id)).all()
    for row in loser_rows:
        mapped_sample_map = sample_map or {}
        duplicate_key = duplicate_key_builder(row, sample_map=mapped_sample_map)
        if duplicate_key in survivor_keys:
            db.session.delete(row)
            continue
        row.device_id = int(survivor_device_id)
        if hasattr(row, "sample_id") and row.sample_id is not None and int(row.sample_id) in mapped_sample_map:
            row.sample_id = int(mapped_sample_map[int(row.sample_id)])
        survivor_keys[duplicate_key] = row


def _merge_tracking_rollups(survivor: TrackedDevice, loser: TrackedDevice) -> None:
    for model, bucket_column in (
        (TrackingHourlyRollup, TrackingHourlyRollup.bucket_hour),
        (TrackingDailyRollup, TrackingDailyRollup.bucket_day),
    ):
        survivor_rows = {
            getattr(row, bucket_column.key): row
            for row in model.query.filter_by(device_id=int(survivor.id)).all()
        }
        loser_rows = model.query.filter_by(device_id=int(loser.id)).all()
        for row in loser_rows:
            bucket_value = getattr(row, bucket_column.key)
            existing = survivor_rows.get(bucket_value)
            if existing is None:
                row.device_id = int(survivor.id)
                survivor_rows[bucket_value] = row
                continue
            combined_sample_count = int(existing.sample_count or 0) + int(row.sample_count or 0)
            existing.active_seconds = int(existing.active_seconds or 0) + int(row.active_seconds or 0)
            existing.keyboard_events = int(existing.keyboard_events or 0) + int(row.keyboard_events or 0)
            existing.mouse_events = int(existing.mouse_events or 0) + int(row.mouse_events or 0)
            existing.cpu_avg = _weighted_average(existing.cpu_avg, existing.sample_count, row.cpu_avg, row.sample_count)
            existing.memory_avg = _weighted_average(existing.memory_avg, existing.sample_count, row.memory_avg, row.sample_count)
            existing.sample_count = combined_sample_count
            db.session.delete(row)


def _merge_restricted_state(survivor: TrackedDevice, loser: TrackedDevice) -> None:
    survivor_alerts = {
        str(row.domain): row
        for row in RestrictedSiteAlertState.query.filter_by(device_id=int(survivor.id)).all()
    }
    for row in RestrictedSiteAlertState.query.filter_by(device_id=int(loser.id)).all():
        existing = survivor_alerts.get(str(row.domain))
        if existing is None:
            row.device_id = int(survivor.id)
            survivor_alerts[str(row.domain)] = row
            continue
        existing.hit_count = int(existing.hit_count or 0) + int(row.hit_count or 0)
        existing.first_seen_at = min(filter(None, [existing.first_seen_at, row.first_seen_at]), default=existing.first_seen_at or row.first_seen_at)
        existing.last_seen_at = max(filter(None, [existing.last_seen_at, row.last_seen_at]), default=existing.last_seen_at or row.last_seen_at)
        existing.last_alerted_at = max(filter(None, [existing.last_alerted_at, row.last_alerted_at]), default=existing.last_alerted_at or row.last_alerted_at)
        existing.last_emailed_at = max(filter(None, [existing.last_emailed_at, row.last_emailed_at]), default=existing.last_emailed_at or row.last_emailed_at)
        if not existing.active_dashboard_event_id and row.active_dashboard_event_id:
            existing.active_dashboard_event_id = row.active_dashboard_event_id
        db.session.delete(row)

    survivor_meta = {
        str(row.domain): row
        for row in RestrictedSiteDomainMeta.query.filter_by(device_id=int(survivor.id)).all()
    }
    for row in RestrictedSiteDomainMeta.query.filter_by(device_id=int(loser.id)).all():
        existing = survivor_meta.get(str(row.domain))
        if existing is None:
            row.device_id = int(survivor.id)
            survivor_meta[str(row.domain)] = row
            continue
        if not existing.category and row.category:
            existing.category = row.category
        if not existing.reason and row.reason:
            existing.reason = row.reason
        if not existing.created_by and row.created_by:
            existing.created_by = row.created_by
        if not existing.updated_by and row.updated_by:
            existing.updated_by = row.updated_by
        existing.created_at = min(filter(None, [existing.created_at, row.created_at]), default=existing.created_at or row.created_at)
        existing.updated_at = max(filter(None, [existing.updated_at, row.updated_at]), default=existing.updated_at or row.updated_at)
        db.session.delete(row)


def _merge_identity_links(survivor: TrackedDevice, loser: TrackedDevice) -> None:
    survivor_links = {
        int(link.device_id): link
        for link in DeviceIdentityLink.query.filter_by(tracked_device_id=int(survivor.id)).all()
    }
    for link in DeviceIdentityLink.query.filter_by(tracked_device_id=int(loser.id)).all():
        existing = survivor_links.get(int(link.device_id))
        if existing is None:
            link.tracked_device_id = int(survivor.id)
            survivor_links[int(link.device_id)] = link
            continue
        existing.is_active = bool(existing.is_active or link.is_active)
        existing.normalized_mac = existing.normalized_mac or link.normalized_mac
        existing.confidence = max(int(existing.confidence or 0), int(link.confidence or 0))
        existing.updated_at = datetime.utcnow()
        db.session.delete(link)

    survivor_candidates = {
        (int(candidate.device_id), str(candidate.ambiguity_group_key or ""))
        for candidate in DeviceIdentityLinkCandidate.query.filter_by(tracked_device_id=int(survivor.id)).all()
    }
    for candidate in DeviceIdentityLinkCandidate.query.filter_by(tracked_device_id=int(loser.id)).all():
        dedupe_key = (int(candidate.device_id), str(candidate.ambiguity_group_key or ""))
        if dedupe_key in survivor_candidates:
            db.session.delete(candidate)
            continue
        candidate.tracked_device_id = int(survivor.id)
        survivor_candidates.add(dedupe_key)


def _merge_as_is(model, field_name: str, survivor_id: int, loser_id: int) -> None:
    column = getattr(model, field_name)
    model.query.filter(column == int(loser_id)).update({field_name: int(survivor_id)}, synchronize_session=False)


def _merge_device_effective_policy_cache(survivor_id: int, loser_id: int) -> None:
    survivor_cache = DeviceEffectivePolicyCache.query.get(int(survivor_id))
    loser_cache = DeviceEffectivePolicyCache.query.get(int(loser_id))
    if loser_cache is None:
        return
    if survivor_cache is None:
        loser_cache.tracked_device_id = int(survivor_id)
        return
    survivor_cache.global_domains_json = list(survivor_cache.global_domains_json or loser_cache.global_domains_json or [])
    survivor_cache.device_domains_json = list(survivor_cache.device_domains_json or loser_cache.device_domains_json or [])
    survivor_cache.effective_domains_json = list(survivor_cache.effective_domains_json or loser_cache.effective_domains_json or [])
    survivor_cache.effective_policy_version = survivor_cache.effective_policy_version or loser_cache.effective_policy_version
    db.session.delete(loser_cache)


def _merge_remote_scan_history(survivor: TrackedDevice, loser: TrackedDevice) -> None:
    _consolidate_remote_scan_history(
        target_mac=normalize_mac(survivor.mac_address),
        source_macs=[loser.mac_address],
    )


def _consolidate_remote_scan_history(*, target_mac: str | None, source_macs: list[str | None]) -> None:
    normalized_target_mac = normalize_mac(target_mac)
    normalized_source_macs = sorted(
        {
            normalize_mac(value)
            for value in source_macs
            if normalize_mac(value) and normalize_mac(value) != normalized_target_mac
        }
    )
    if not normalized_target_mac or not normalized_source_macs:
        return

    duplicate_keys = {
        (
            row.scan_timestamp,
            str(row.ip_address or ""),
            str(row.status or ""),
        )
        for row in RemoteDeviceScanHistory.query.filter(
            func.replace(func.upper(RemoteDeviceScanHistory.mac_address), "-", ":") == normalized_target_mac
        ).all()
    }
    for source_mac in normalized_source_macs:
        for row in RemoteDeviceScanHistory.query.filter(
            func.replace(func.upper(RemoteDeviceScanHistory.mac_address), "-", ":") == source_mac
        ).all():
            dedupe_key = (
                row.scan_timestamp,
                str(row.ip_address or ""),
                str(row.status or ""),
            )
            if dedupe_key in duplicate_keys:
                db.session.delete(row)
                continue
            row.mac_address = normalized_target_mac
            duplicate_keys.add(dedupe_key)


def _merge_tracked_devices(
    *,
    survivor: TrackedDevice,
    loser: TrackedDevice,
    actor: ActorContext,
    resolved_inventory_device_id: int | None,
    resolution_path: str,
    resolution_source: str,
    merge_reason: str,
) -> None:
    if int(survivor.id) == int(loser.id):
        return

    _merge_survivor_metadata(survivor, loser)
    sample_map = _merge_tracking_samples(survivor, loser)

    _merge_exact_duplicate_rows(
        model=TrackedDeviceAvailabilityEvent,
        survivor_device_id=int(survivor.id),
        loser_device_id=int(loser.id),
        sample_map=sample_map,
        duplicate_key_builder=lambda row, sample_map=None: (
            _map_sample_id(row.sample_id, sample_map) or 0,
            row.observed_at,
            str(row.status or ""),
            str(row.source or ""),
            str(row.probe_method or ""),
            str(row.probe_error_code or ""),
        ),
    )
    _merge_exact_duplicate_rows(
        model=DeviceActivityLog,
        survivor_device_id=int(survivor.id),
        loser_device_id=int(loser.id),
        sample_map=sample_map,
        duplicate_key_builder=lambda row, sample_map=None: (
            _map_sample_id(row.sample_id, sample_map) or 0,
            row.timestamp,
            str(row.activity_type or ""),
            int(row.event_count or 0),
            str(row.details or ""),
        ),
    )
    _merge_exact_duplicate_rows(
        model=DeviceResourceLog,
        survivor_device_id=int(survivor.id),
        loser_device_id=int(loser.id),
        sample_map=sample_map,
        duplicate_key_builder=lambda row, sample_map=None: (
            _map_sample_id(row.sample_id, sample_map) or 0,
            row.timestamp,
            float(row.cpu_usage or 0),
            float(row.memory_usage or 0),
            float(row.disk_usage or 0),
            float(row.network_usage or 0),
            float(row.upload_kbps or 0),
            float(row.download_kbps or 0),
        ),
    )
    _merge_exact_duplicate_rows(
        model=DeviceApplicationLog,
        survivor_device_id=int(survivor.id),
        loser_device_id=int(loser.id),
        sample_map=sample_map,
        duplicate_key_builder=lambda row, sample_map=None: (
            _map_sample_id(row.sample_id, sample_map) or 0,
            row.timestamp,
            str(row.application_name or ""),
            str(row.window_title or ""),
            int(row.duration or 0),
            str(row.status or ""),
        ),
    )
    _merge_exact_duplicate_rows(
        model=RestrictedSiteEvent,
        survivor_device_id=int(survivor.id),
        loser_device_id=int(loser.id),
        sample_map=None,
        duplicate_key_builder=lambda row, sample_map=None: (
            row.observed_at_utc,
            str(row.domain or ""),
            str(row.matched_rule or ""),
            str(row.source or ""),
            str(row.policy_version or ""),
            str(row.raw_evidence or ""),
        ),
    )

    _merge_tracking_rollups(survivor, loser)
    _merge_restricted_state(survivor, loser)
    _merge_identity_links(survivor, loser)
    _merge_as_is(TrackedDeviceIpHistory, "device_id", int(survivor.id), int(loser.id))
    _merge_as_is(TrackingHistoryIntegrityAudit, "device_id", int(survivor.id), int(loser.id))
    _merge_as_is(TrackingSyncEnvelope, "tracked_device_id", int(survivor.id), int(loser.id))
    _merge_as_is(TrackingAgentKeyBinding, "tracked_device_id", int(survivor.id), int(loser.id))
    _merge_as_is(AlertFanoutTask, "tracked_device_id", int(survivor.id), int(loser.id))
    _merge_as_is(PolicyRebuildTask, "tracked_device_id", int(survivor.id), int(loser.id))
    _merge_device_effective_policy_cache(int(survivor.id), int(loser.id))

    _build_audit_log(
        actor=actor,
        action="delete",
        entity_type="tracked_device_merge",
        entity_id=int(survivor.id),
        entity_name=survivor.device_name,
        description=f"Merged duplicate tracked device {loser.id} into survivor {survivor.id}.",
        changes={
            "survivor_id": int(survivor.id),
            "loser_id": int(loser.id),
            "survivor_mac": survivor.mac_address,
            "loser_mac": loser.mac_address,
            "resolved_inventory_device_id": resolved_inventory_device_id,
            "resolution_path": resolution_path,
            "resolution_source": resolution_source,
            "merge_reason": merge_reason,
        },
    )
    db.session.delete(loser)
    db.session.flush()


def _update_survivor_identity(
    *,
    survivor: TrackedDevice,
    identity_input: IdentityInput,
    authoritative_mac: str,
    authoritative_mac_source: str,
    inventory_device: Device | None,
) -> None:
    if authoritative_mac and normalize_mac(survivor.mac_address) != authoritative_mac:
        competing = _find_tracked_by_mac(authoritative_mac)
        if competing is not None and int(competing.id) != int(survivor.id):
            raise ValueError(f"Authoritative MAC {authoritative_mac} belongs to tracked device {competing.id}")
        survivor.mac_address = authoritative_mac

    if identity_input.unique_client_id and not survivor.unique_client_id:
        survivor.unique_client_id = identity_input.unique_client_id
    if identity_input.hostname:
        survivor.hostname = identity_input.hostname
    if inventory_device is not None and not survivor.hostname and inventory_device.hostname:
        survivor.hostname = inventory_device.hostname
    if not survivor.device_name:
        survivor.device_name = (
            identity_input.device_name_hint
            or identity_input.hostname
            or (inventory_device.device_name if inventory_device is not None else None)
            or "Tracked Device"
        )
    if not survivor.employee_name:
        survivor.employee_name = "Auto-Discovered"
    if not survivor.department:
        survivor.department = "Unassigned"
    if authoritative_mac_source == "scanner_inventory" and (not survivor.notes or "auto-registered by service agent sync" in str(survivor.notes).lower()):
        survivor.notes = "Auto-registered by service agent sync (scanner-confirmed)"
    if getattr(survivor, "is_archived", False):
        survivor.is_archived = False
        survivor.archived_at = None
        survivor.archived_reason = None
        survivor.archived_by = None
        survivor.is_active = True
    survivor.last_seen = identity_input.now_utc
    survivor.updated_at = identity_input.now_utc
    db.session.flush()


def reconcile_tracking_identity(
    *,
    identity_input: IdentityInput,
    payload: dict,
    actor: ActorContext,
    sync_mode: str,
    resolution_source: str,
    allow_create: bool = True,
) -> IdentityResolutionResult:
    tracked_by_uuid = _find_tracked_by_uuid(identity_input.unique_client_id)
    existing_link = _resolve_active_link_for_tracked(int(tracked_by_uuid.id)) if tracked_by_uuid is not None else None
    inventory_evidence, inventory_ambiguous = _resolve_inventory_candidate(
        identity_input,
        linked_inventory_device_id=int(existing_link.device_id) if existing_link is not None else None,
    )

    authoritative_mac = None
    authoritative_mac_source = "agent_payload"
    if inventory_evidence is not None:
        authoritative_mac = inventory_evidence.authoritative_mac
        authoritative_mac_source = "scanner_inventory"
    elif identity_input.normalized_payload_mac:
        authoritative_mac = identity_input.normalized_payload_mac

    candidate_tracked_ids = []
    if tracked_by_uuid is not None:
        candidate_tracked_ids.append(int(tracked_by_uuid.id))
    if existing_link is not None:
        candidate_tracked_ids.append(int(existing_link.tracked_device_id))
    authoritative_match = _find_tracked_by_mac(authoritative_mac) if authoritative_mac else None
    if authoritative_match is not None:
        candidate_tracked_ids.append(int(authoritative_match.id))

    linked_inventory_device = _lock_inventory_device(inventory_evidence.device_id if inventory_evidence is not None else None)
    touched_links = _lock_active_links(
        inventory_device_id=int(linked_inventory_device.device_id) if linked_inventory_device is not None else None,
        tracked_device_ids=candidate_tracked_ids,
    )
    for link in touched_links:
        candidate_tracked_ids.append(int(link.tracked_device_id))
    locked_tracked = _lock_tracked_devices(candidate_tracked_ids)
    _lock_envelope_key(identity_input.dedupe_key)
    pending_envelope = _lock_pending_envelope(identity_input.dedupe_key)

    tracked_by_uuid = locked_tracked.get(int(tracked_by_uuid.id)) if tracked_by_uuid is not None and int(tracked_by_uuid.id) in locked_tracked else tracked_by_uuid
    authoritative_match = locked_tracked.get(int(authoritative_match.id)) if authoritative_match is not None and int(authoritative_match.id) in locked_tracked else authoritative_match
    linked_tracked = None
    if linked_inventory_device is not None:
        for link in touched_links:
            if int(link.device_id) == int(linked_inventory_device.device_id) and link.is_active:
                linked_tracked = locked_tracked.get(int(link.tracked_device_id)) or TrackedDevice.query.get(int(link.tracked_device_id))
                break

    hostname_only_ambiguous = inventory_ambiguous and not identity_input.ip_candidates and existing_link is None
    if hostname_only_ambiguous:
        resolution_metadata = {
            "identity_status": "pending_confirmation",
            "visible_in_tracking": False,
            "authoritative_mac": authoritative_mac,
            "authoritative_mac_source": authoritative_mac_source,
            "resolution_path": "payload_only_pending",
            "resolution_source": resolution_source,
            "resolved_inventory_device_id": None,
            "merge_reason": None,
        }
        envelope = upsert_sync_envelope(
            payload=payload,
            normalized_mac=identity_input.normalized_payload_mac or authoritative_mac or "00:00:00:00:00:00",
            unique_client_id=identity_input.unique_client_id,
            tracked_device_id=None,
            dedupe_key=identity_input.dedupe_key,
            resolution_metadata=resolution_metadata,
        )
        return IdentityResolutionResult(
            device=None,
            identity_status="pending_confirmation",
            visible_in_tracking=False,
            authoritative_mac=authoritative_mac,
            authoritative_mac_source=authoritative_mac_source,
            resolution_path="payload_only_pending",
            resolution_source=resolution_source,
            resolved_inventory_device_id=None,
            envelope=envelope,
            identity_confirmed=False,
        )

    candidate_rows = [row for row in {tracked_by_uuid, authoritative_match, linked_tracked} if row is not None]
    survivor = None
    created_device = False
    if linked_inventory_device is not None:
        survivor = _select_survivor(
            linked_tracked=linked_tracked,
            authoritative_tracked=authoritative_match,
            tracked_by_uuid=tracked_by_uuid,
            candidates=candidate_rows,
        )
        if survivor is None and allow_create:
            fallback_name = identity_input.device_name_hint or identity_input.hostname or linked_inventory_device.hostname or linked_inventory_device.device_name or "Tracked Device"
            survivor = TrackedDevice(
                mac_address=authoritative_mac,
                unique_client_id=identity_input.unique_client_id,
                device_name=fallback_name,
                employee_name="Auto-Discovered",
                hostname=identity_input.hostname or linked_inventory_device.hostname,
                ip_address=None,
                department="Unassigned",
                notes="Auto-registered by service agent sync (scanner-confirmed)",
                last_seen=identity_input.now_utc,
                is_archived=False,
            )
            db.session.add(survivor)
            db.session.flush()
            created_device = True
    else:
        survivor = tracked_by_uuid or authoritative_match

    if survivor is None:
        resolution_metadata = {
            "identity_status": "pending_confirmation",
            "visible_in_tracking": False,
            "authoritative_mac": authoritative_mac,
            "authoritative_mac_source": authoritative_mac_source,
            "resolution_path": "payload_only_pending",
            "resolution_source": resolution_source,
            "resolved_inventory_device_id": None,
            "merge_reason": None,
        }
        envelope = upsert_sync_envelope(
            payload=payload,
            normalized_mac=identity_input.normalized_payload_mac or authoritative_mac or "00:00:00:00:00:00",
            unique_client_id=identity_input.unique_client_id,
            tracked_device_id=None,
            dedupe_key=identity_input.dedupe_key,
            resolution_metadata=resolution_metadata,
        )
        return IdentityResolutionResult(
            device=None,
            identity_status="pending_confirmation",
            visible_in_tracking=False,
            authoritative_mac=authoritative_mac,
            authoritative_mac_source=authoritative_mac_source,
            resolution_path="payload_only_pending",
            resolution_source=resolution_source,
            resolved_inventory_device_id=None,
            envelope=envelope,
            identity_confirmed=False,
        )

    losers = [
        row
        for row in candidate_rows
        if row is not None and int(row.id) != int(survivor.id)
    ]
    source_scan_macs = [survivor.mac_address]
    merge_reason = None
    for loser in sorted(losers, key=lambda row: int(row.id)):
        source_scan_macs.append(loser.mac_address)
        merge_reason = _determine_merge_reason(
            linked_tracked=linked_tracked,
            authoritative_tracked=authoritative_match,
            tracked_by_uuid=tracked_by_uuid,
            survivor=survivor,
            loser=loser,
        )
        _merge_tracked_devices(
            survivor=survivor,
            loser=loser,
            actor=actor,
            resolved_inventory_device_id=int(linked_inventory_device.device_id) if linked_inventory_device is not None else None,
            resolution_path=inventory_evidence.resolution_path if inventory_evidence is not None else ("unique_client_id_match" if tracked_by_uuid is not None else "payload_mac_match"),
            resolution_source=resolution_source,
            merge_reason=merge_reason,
        )

    _update_survivor_identity(
        survivor=survivor,
        identity_input=identity_input,
        authoritative_mac=authoritative_mac or survivor.mac_address,
        authoritative_mac_source=authoritative_mac_source,
        inventory_device=linked_inventory_device,
    )
    _consolidate_remote_scan_history(
        target_mac=survivor.mac_address,
        source_macs=source_scan_macs,
    )

    if linked_inventory_device is not None and authoritative_mac:
        _upsert_active_link(
            inventory_device=linked_inventory_device,
            survivor=survivor,
            authoritative_mac=authoritative_mac,
            actor=actor,
            resolution_path=inventory_evidence.resolution_path,
            touched_links=touched_links,
        )

    resolution_path = (
        "duplicate_merged"
        if losers
        else inventory_evidence.resolution_path if inventory_evidence is not None
        else "unique_client_id_match" if tracked_by_uuid is not None
        else "payload_mac_match"
    )
    identity_status = "merged_duplicate" if losers else "confirmed"
    resolution_metadata = {
        "identity_status": identity_status,
        "visible_in_tracking": True,
        "authoritative_mac": authoritative_mac,
        "authoritative_mac_source": authoritative_mac_source,
        "resolution_path": resolution_path,
        "resolution_source": resolution_source,
        "resolved_inventory_device_id": int(linked_inventory_device.device_id) if linked_inventory_device is not None else None,
        "merged_duplicate_device_id": int(losers[0].id) if losers else None,
        "merged_duplicate_device_ids": [int(row.id) for row in losers],
        "merge_reason": merge_reason,
    }

    envelope = None
    if pending_envelope is not None or sync_mode in {"queued_inline", "shadow", "async"}:
        envelope = upsert_sync_envelope(
            payload=payload,
            normalized_mac=identity_input.normalized_payload_mac or authoritative_mac or survivor.mac_address,
            unique_client_id=identity_input.unique_client_id,
            tracked_device_id=int(survivor.id),
            dedupe_key=identity_input.dedupe_key,
            resolution_metadata=resolution_metadata,
        ) if pending_envelope is not None else queue_sync_envelope(
            payload=payload,
            normalized_mac=identity_input.normalized_payload_mac or authoritative_mac or survivor.mac_address,
            unique_client_id=identity_input.unique_client_id,
            tracked_device_id=int(survivor.id),
        )

    return IdentityResolutionResult(
        device=survivor,
        identity_status=identity_status,
        visible_in_tracking=True,
        authoritative_mac=authoritative_mac or survivor.mac_address,
        authoritative_mac_source=authoritative_mac_source,
        resolution_path=resolution_path,
        resolution_source=resolution_source,
        resolved_inventory_device_id=int(linked_inventory_device.device_id) if linked_inventory_device is not None else None,
        merged_duplicate_device_id=int(losers[0].id) if losers else None,
        merged_duplicate_device_ids=[int(row.id) for row in losers],
        merge_reason=merge_reason,
        envelope=envelope,
        created_device=created_device,
        identity_confirmed=True,
    )


def resolve_scan_device_identity(device_payload: dict, *, now_utc: datetime | None = None) -> dict[str, Any]:
    payload_mac = normalize_mac(device_payload.get("mac_address"))
    unique_client_id = str(device_payload.get("unique_client_id") or "").strip() or None
    hostname = str(device_payload.get("hostname") or "").strip() or None
    resolved_ip = str(device_payload.get("ip") or "").strip() or None
    identity_input = build_identity_input(
        normalized_payload_mac=payload_mac,
        unique_client_id=unique_client_id,
        hostname=hostname,
        resolved_ip=resolved_ip,
        payload_ip=resolved_ip,
        payload_ip_candidates=[resolved_ip] if resolved_ip else [],
        network_signature=None,
        now_utc=now_utc,
        device_name_hint=hostname,
    )

    tracked_by_uuid = _find_tracked_by_uuid(identity_input.unique_client_id)
    existing_link = _resolve_active_link_for_tracked(int(tracked_by_uuid.id)) if tracked_by_uuid is not None else None
    inventory_evidence, _inventory_ambiguous = _resolve_inventory_candidate(
        identity_input,
        linked_inventory_device_id=int(existing_link.device_id) if existing_link is not None else None,
    )
    authoritative_mac = inventory_evidence.authoritative_mac if inventory_evidence is not None else payload_mac
    authoritative_mac_source = "scanner_inventory" if inventory_evidence is not None else "agent_payload"

    matched_device = None
    if inventory_evidence is not None:
        active_link = DeviceIdentityLink.query.filter_by(
            device_id=int(inventory_evidence.device_id),
            is_active=True,
        ).order_by(DeviceIdentityLink.id.desc()).first()
        if active_link is not None:
            matched_device = TrackedDevice.query.get(int(active_link.tracked_device_id))
    if matched_device is None and authoritative_mac:
        matched_device = _find_tracked_by_mac(authoritative_mac)
    if matched_device is None and unique_client_id:
        matched_device = _find_tracked_by_uuid(unique_client_id)

    return {
        "authoritative_mac": authoritative_mac,
        "reported_agent_mac": payload_mac,
        "authoritative_mac_source": authoritative_mac_source,
        "resolved_inventory_device_id": int(inventory_evidence.device_id) if inventory_evidence is not None else None,
        "matched_tracked_device_id": int(matched_device.id) if matched_device is not None else None,
        "identity_confirmed": bool(matched_device is not None),
        "resolution_path": inventory_evidence.resolution_path if inventory_evidence is not None else ("unique_client_id_match" if unique_client_id and matched_device is not None else "payload_mac_match"),
        "matched_device": matched_device,
    }


def preview_reconciliation_for_tracked_device(
    tracked_device_id: int,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    tracked_device = TrackedDevice.query.get(int(tracked_device_id))
    if tracked_device is None:
        return {
            "device_id": int(tracked_device_id),
            "status": "missing",
            "actionable": False,
        }

    identity_input = build_identity_input_for_tracked_device(tracked_device, now_utc=now_utc)
    tracked_by_uuid = _find_tracked_by_uuid(identity_input.unique_client_id)
    existing_link = _resolve_active_link_for_tracked(int(tracked_by_uuid.id)) if tracked_by_uuid is not None else None
    inventory_evidence, inventory_ambiguous = _resolve_inventory_candidate(
        identity_input,
        linked_inventory_device_id=int(existing_link.device_id) if existing_link is not None else None,
    )
    authoritative_mac = inventory_evidence.authoritative_mac if inventory_evidence is not None else normalize_mac(tracked_device.mac_address)
    authoritative_match = _find_tracked_by_mac(authoritative_mac)
    linked_tracked = None
    if inventory_evidence is not None:
        active_link = DeviceIdentityLink.query.filter_by(device_id=int(inventory_evidence.device_id), is_active=True).order_by(DeviceIdentityLink.id.desc()).first()
        if active_link is not None:
            linked_tracked = TrackedDevice.query.get(int(active_link.tracked_device_id))
    candidates = [row for row in {tracked_by_uuid, authoritative_match, linked_tracked} if row is not None]
    survivor = _select_survivor(
        linked_tracked=linked_tracked,
        authoritative_tracked=authoritative_match,
        tracked_by_uuid=tracked_by_uuid,
        candidates=candidates,
    )
    losers = [
        int(row.id)
        for row in candidates
        if survivor is not None and int(row.id) != int(survivor.id)
    ]
    return {
        "device_id": int(tracked_device.id),
        "authoritative_mac": authoritative_mac,
        "resolved_inventory_device_id": int(inventory_evidence.device_id) if inventory_evidence is not None else None,
        "resolution_path": inventory_evidence.resolution_path if inventory_evidence is not None else "unresolved",
        "inventory_ambiguous": bool(inventory_ambiguous),
        "survivor_id": int(survivor.id) if survivor is not None else None,
        "loser_ids": losers,
        "actionable": bool(losers),
        "status": "actionable" if losers else "noop",
    }
