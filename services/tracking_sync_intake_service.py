from __future__ import annotations

import os
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
        core_next_run_at=datetime.utcnow(),
        violation_next_run_at=datetime.utcnow(),
        dedupe_key=plan.dedupe_key,
    )
    db.session.add(envelope)
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
    return response
