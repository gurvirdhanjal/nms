from __future__ import annotations

from datetime import datetime, timedelta

from extensions import db
from models.device_effective_policy_cache import DeviceEffectivePolicyCache
from models.policy_rebuild_task import PolicyRebuildTask
from models.restricted_site_policy import RestrictedSiteDomainMeta, RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from services.device_link_service import DeviceLinkService

CACHE_FRESH_SECONDS = 60


class EffectivePolicyUnavailable(RuntimeError):
    pass


def build_effective_policy_version(policy: RestrictedSitePolicy, effective_domains: list[str]) -> str:
    return f'{policy.policy_version}_{len(effective_domains)}'


def build_effective_policy(tracked_device_id: int) -> dict:
    tracked_device = TrackedDevice.query.get(int(tracked_device_id))
    if tracked_device is None:
        raise EffectivePolicyUnavailable('tracked device not found')

    policy = RestrictedSitePolicy.get_singleton()
    domain_rows = (
        RestrictedSiteDomainMeta.query.filter_by(device_id=int(tracked_device.id))
        .order_by(RestrictedSiteDomainMeta.domain.asc())
        .all()
    )
    global_domains = sorted(set(policy.blocked_domains or []))
    device_domains = [row.domain for row in domain_rows]
    effective_domains = sorted(set(global_domains + device_domains))
    effective_version = build_effective_policy_version(policy, effective_domains)
    identity_status = DeviceLinkService.link_status_for_tracked_device(int(tracked_device.id)).to_dict()

    return {
        'tracked_device_id': int(tracked_device.id),
        'policy_enabled': bool(policy.enabled),
        'global_restricted_sites': global_domains,
        'device_restricted_sites': device_domains,
        'effective_restricted_sites': effective_domains,
        'effective_policy_version': effective_version,
        'agent_policy_version': tracked_device.last_policy_version_seen,
        'agent_policy_last_seen_at': tracked_device.last_policy_sync_at.isoformat() if tracked_device.last_policy_sync_at else None,
        **identity_status,
    }


def enqueue_policy_rebuild(tracked_device_id: int, priority: int = 100) -> PolicyRebuildTask:
    existing = PolicyRebuildTask.query.filter(
        PolicyRebuildTask.tracked_device_id == int(tracked_device_id),
        PolicyRebuildTask.status.in_(['pending', 'running']),
    ).order_by(PolicyRebuildTask.id.desc()).first()
    if existing is not None:
        return existing

    task = PolicyRebuildTask(
        tracked_device_id=int(tracked_device_id),
        status='pending',
        priority=int(priority),
        next_run_at=datetime.utcnow(),
    )
    db.session.add(task)
    db.session.flush()
    return task


def enqueue_policy_rebuild_for_all_tracked_devices(priority: int = 100) -> int:
    count = 0
    for tracked_device in TrackedDevice.query.filter_by(is_archived=False).all():
        enqueue_policy_rebuild(int(tracked_device.id), priority=priority)
        count += 1
    return count


def rebuild_effective_policy_cache(tracked_device_id: int) -> dict:
    snapshot = build_effective_policy(int(tracked_device_id))
    cache_row = DeviceEffectivePolicyCache.query.get(int(tracked_device_id))
    if cache_row is None:
        cache_row = DeviceEffectivePolicyCache(tracked_device_id=int(tracked_device_id))
        db.session.add(cache_row)

    cache_row.global_domains_json = list(snapshot['global_restricted_sites'])
    cache_row.device_domains_json = list(snapshot['device_restricted_sites'])
    cache_row.effective_domains_json = list(snapshot['effective_restricted_sites'])
    cache_row.effective_policy_version = snapshot['effective_policy_version']
    cache_row.updated_at = datetime.utcnow()
    db.session.flush()

    payload = dict(snapshot)
    payload['policy_cache_state'] = 'rebuilt_inline'
    payload['policy_cache_age_seconds'] = 0
    payload['policy_stale'] = False
    payload['rebuild_enqueued'] = False
    return payload


def _build_payload_from_cache(cache_row: DeviceEffectivePolicyCache, tracked_device: TrackedDevice) -> dict:
    identity_status = DeviceLinkService.link_status_for_tracked_device(int(tracked_device.id)).to_dict()
    age_seconds = max(0, int((datetime.utcnow() - cache_row.updated_at).total_seconds())) if cache_row.updated_at else 0
    return {
        'tracked_device_id': int(tracked_device.id),
        'policy_enabled': True,
        'global_restricted_sites': list(cache_row.global_domains_json or []),
        'device_restricted_sites': list(cache_row.device_domains_json or []),
        'effective_restricted_sites': list(cache_row.effective_domains_json or []),
        'effective_policy_version': cache_row.effective_policy_version or '',
        'agent_policy_version': tracked_device.last_policy_version_seen,
        'agent_policy_last_seen_at': tracked_device.last_policy_sync_at.isoformat() if tracked_device.last_policy_sync_at else None,
        'policy_cache_age_seconds': age_seconds,
        **identity_status,
    }


def get_effective_policy(tracked_device_id: int, allow_rebuild: bool = True) -> dict:
    tracked_device = TrackedDevice.query.get(int(tracked_device_id))
    if tracked_device is None:
        raise EffectivePolicyUnavailable('tracked device not found')

    cache_row = DeviceEffectivePolicyCache.query.get(int(tracked_device_id))
    if cache_row is None:
        try:
            return rebuild_effective_policy_cache(int(tracked_device_id))
        except Exception as exc:
            # rebuild_effective_policy_cache calls db.session.flush() which may fail
            # (lock timeout, constraint violation). That poisons the session — roll back
            # before propagating so the caller's next DB operation works cleanly.
            try:
                db.session.rollback()
            except Exception:
                pass
            raise EffectivePolicyUnavailable('effective_policy_unavailable') from exc

    cached_payload = _build_payload_from_cache(cache_row, tracked_device)
    cache_age_seconds = cached_payload['policy_cache_age_seconds']
    if cache_age_seconds <= CACHE_FRESH_SECONDS:
        cached_payload['policy_cache_state'] = 'fresh'
        cached_payload['policy_stale'] = False
        cached_payload['rebuild_enqueued'] = False
        return cached_payload

    if allow_rebuild:
        try:
            return rebuild_effective_policy_cache(int(tracked_device_id))
        except Exception:
            # rebuild_effective_policy_cache flushes the cache row. If that flush fails
            # (e.g. lock_timeout from a concurrent writer), the session is in
            # PendingRollbackError state. Rollback here before enqueue_policy_rebuild
            # touches the DB — otherwise its query will raise immediately.
            try:
                db.session.rollback()
            except Exception:
                pass
            enqueue_policy_rebuild(int(tracked_device_id))
            db.session.commit()
            cached_payload['policy_cache_state'] = 'stale_fallback'
            cached_payload['policy_stale'] = True
            cached_payload['rebuild_enqueued'] = True
            return cached_payload

    cached_payload['policy_cache_state'] = 'stale_fallback'
    cached_payload['policy_stale'] = True
    cached_payload['rebuild_enqueued'] = False
    return cached_payload
