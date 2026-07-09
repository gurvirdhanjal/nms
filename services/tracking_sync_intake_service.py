from __future__ import annotations

import os
import hashlib
from datetime import datetime

from extensions import db
from models.restricted_site_policy import RestrictedSitePolicy
from models.tracking_sync_envelope import TrackingSyncEnvelope
from services.effective_policy_service import get_effective_policy
from services.tracking_sync_core_service import plan_sync_core_mutations


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def current_sync_mode() -> str:
    async_enabled = _flag('TRACKING_SYNC_ASYNC_ENABLED', False)
    shadow_enabled = _flag('TRACKING_SYNC_SHADOW_COMPARE', False)
    queue_enabled = _flag('TRACKING_SYNC_QUEUE_ENABLED', False) or async_enabled or shadow_enabled
    if async_enabled:
        return 'async'
    if shadow_enabled:
        return 'shadow'
    if queue_enabled:
        return 'queued_inline'
    return 'inline'


def build_envelope_dedupe_key(
    *,
    normalized_mac: str | None,
    unique_client_id: str | None,
    network_signature: str | None = None,
    hostname: str | None = None,
    resolved_ip: str | None = None,
) -> str:
    if unique_client_id:
        return f"uid:{str(unique_client_id).strip()}"
    if normalized_mac:
        return f"mac:{str(normalized_mac).strip()}"

    fingerprint_source = "|".join(
        [
            str(network_signature or "").strip().lower(),
            str(hostname or "").strip().lower(),
            str(resolved_ip or "").strip().lower(),
        ]
    )
    digest = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
    return f"fp:{digest}"


def queue_sync_envelope(payload: dict, normalized_mac: str, unique_client_id: str | None, tracked_device_id: int | None) -> TrackingSyncEnvelope:
    plan = plan_sync_core_mutations(payload, normalized_mac, unique_client_id, now_utc=datetime.utcnow())
    envelope = TrackingSyncEnvelope(
        normalized_mac=normalized_mac,
        unique_client_id=unique_client_id,
        tracked_device_id=tracked_device_id,
        payload_json=payload,
        received_at=datetime.utcnow(),
        inline_summary_json=plan.to_summary(),
        shadow_status='pending',
        core_status='pending',
        violation_status='pending',
        domain_status='skipped',
        core_next_run_at=datetime.utcnow(),
        violation_next_run_at=datetime.utcnow(),
        domain_next_run_at=datetime.utcnow(),
        dedupe_key=plan.dedupe_key,
    )
    db.session.add(envelope)
    db.session.flush()
    return envelope


def upsert_sync_envelope(
    *,
    payload: dict,
    normalized_mac: str,
    unique_client_id: str | None,
    tracked_device_id: int | None,
    dedupe_key: str,
    resolution_metadata: dict | None = None,
) -> TrackingSyncEnvelope:
    now_utc = datetime.utcnow()
    plan = plan_sync_core_mutations(payload, normalized_mac, unique_client_id, now_utc=now_utc)
    inline_summary = plan.to_summary()
    if isinstance(resolution_metadata, dict) and resolution_metadata:
        inline_summary.update(resolution_metadata)

    envelope = (
        TrackingSyncEnvelope.query.filter_by(dedupe_key=dedupe_key)
        .filter(
            TrackingSyncEnvelope.core_status.in_(("pending", "running")),
            TrackingSyncEnvelope.violation_status.in_(("pending", "running")),
        )
        .order_by(TrackingSyncEnvelope.received_at.desc(), TrackingSyncEnvelope.id.desc())
        .first()
    )
    if envelope is None:
        envelope = TrackingSyncEnvelope(
            normalized_mac=normalized_mac,
            unique_client_id=unique_client_id,
            tracked_device_id=tracked_device_id,
            payload_json=payload,
            received_at=now_utc,
            inline_summary_json=inline_summary,
            shadow_status='pending',
            core_status='pending',
            violation_status='pending',
            domain_status='skipped',
            core_next_run_at=now_utc,
            violation_next_run_at=now_utc,
            domain_next_run_at=now_utc,
            dedupe_key=dedupe_key,
        )
        db.session.add(envelope)
        db.session.flush()
        return envelope

    envelope.normalized_mac = normalized_mac
    envelope.unique_client_id = unique_client_id
    envelope.tracked_device_id = tracked_device_id
    envelope.payload_json = payload
    envelope.received_at = now_utc
    envelope.inline_summary_json = inline_summary
    envelope.dedupe_key = dedupe_key
    envelope.core_next_run_at = now_utc
    envelope.violation_next_run_at = now_utc
    if envelope.core_status not in {"pending", "running"}:
        envelope.core_status = "pending"
        envelope.core_retry_count = 0
    if envelope.violation_status not in {"pending", "running"}:
        envelope.violation_status = "pending"
        envelope.violation_retry_count = 0
    # domain_status is set by the route after checking payload content
    db.session.flush()
    return envelope


def build_sync_policy_payload(tracked_device_id: int, client_policy_version: str) -> dict:
    policy = RestrictedSitePolicy.get_singleton()
    policy_payload = get_effective_policy(int(tracked_device_id), allow_rebuild=True)
    response = {
        'restricted_sites_policy_version': policy_payload['effective_policy_version'],
    }
    if str(client_policy_version or '').strip() != str(policy_payload['effective_policy_version'] or '').strip():
        response['restricted_sites_policy'] = {
            'enabled': bool(policy.enabled),
            'blocked_domains': list(policy_payload.get('effective_restricted_sites') or []),
            'cooldown_seconds': int(policy.cooldown_seconds or 900),
            'dns_poll_seconds': int(policy.dns_poll_seconds or 60),
            'window_poll_seconds': int(policy.window_poll_seconds or 10),
            'dns_seen_ttl_seconds': int(policy.dns_seen_ttl_seconds or 1800),
            'policy_version': policy_payload['effective_policy_version'],
        }

    # Push a location sample request on every sync.
    # The agent gates execution via LOCATION_MIN_INTERVAL_SECONDS (~12 h by default)
    # so this doesn't cause high-frequency polling.  An admin-triggered force request
    # includes urgent=True, which tells the agent to bypass its local interval gate.
    try:
        import uuid as _uuid
        from models.tracked_device import TrackedDevice as _TD
        from extensions import db as _db
        from datetime import datetime as _dt

        _req = {'request_id': str(_uuid.uuid4())}
        _dev = _TD.query.get(tracked_device_id)
        if _dev and _dev.location_force_until and _dev.location_force_until > _dt.utcnow():
            _req['urgent'] = True
            _dev.location_force_until = None
            _db.session.commit()
        response['pending_location_requests'] = [_req]
    except Exception:
        pass

    # Deliver queued patch commands and mark them sent
    try:
        from models.patch_command import PatchCommand
        from extensions import db
        from datetime import datetime
        queued = (
            PatchCommand.query
            .filter_by(tracked_device_id=tracked_device_id, status='queued')
            .order_by(PatchCommand.created_at)
            .limit(10)
            .all()
        )
        if queued:
            now = datetime.utcnow()
            cmds = []
            for cmd in queued:
                cmds.append({
                    'command_id': cmd.id,
                    'package_manager': cmd.package_manager,
                    'package_name': cmd.package_name,
                    'target_version': cmd.target_version,
                })
                cmd.status = 'sent'
                cmd.sent_at = now
            db.session.commit()
            response['pending_patch_commands'] = cmds
    except Exception:
        pass  # never let command delivery break the sync response

    return response
