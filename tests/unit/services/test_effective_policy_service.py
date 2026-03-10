from datetime import datetime, timedelta

import pytest

from extensions import db
from models.device import Device
from models.device_effective_policy_cache import DeviceEffectivePolicyCache
from models.device_identity_link import DeviceIdentityLink
from models.policy_rebuild_task import PolicyRebuildTask
from models.restricted_site_policy import RestrictedSiteDomainMeta, RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from services import effective_policy_service as service


pytestmark = pytest.mark.unit


def _tracked_device(mac='AA:BB:CC:DD:EE:41'):
    device = TrackedDevice(
        mac_address=mac,
        device_name='Policy Device',
        availability_status='online',
        last_policy_version_seen='agent-v1',
        last_policy_sync_at=datetime(2026, 3, 6, 10, 0, 0),
    )
    db.session.add(device)
    db.session.flush()
    return device


def test_build_effective_policy_merges_domains_and_identity_status():
    tracked = _tracked_device()
    inventory = Device(device_name='Inventory Policy Device', device_type='workstation', device_ip='10.0.0.41', macaddress=tracked.mac_address)
    db.session.add(inventory)
    db.session.flush()
    db.session.add(
        DeviceIdentityLink(
            device_id=inventory.device_id,
            tracked_device_id=tracked.id,
            normalized_mac=tracked.mac_address,
            link_source='exact_mac',
            is_active=True,
        )
    )
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['global.example', 'shared.example'])
    db.session.add(RestrictedSiteDomainMeta(device_id=tracked.id, domain='device.example', category='Custom'))
    db.session.add(RestrictedSiteDomainMeta(device_id=tracked.id, domain='shared.example', category='Custom'))
    db.session.commit()

    payload = service.build_effective_policy(tracked.id)

    assert payload['global_restricted_sites'] == ['global.example', 'shared.example']
    assert payload['device_restricted_sites'] == ['device.example', 'shared.example']
    assert payload['effective_restricted_sites'] == ['device.example', 'global.example', 'shared.example']
    assert payload['agent_policy_version'] == 'agent-v1'
    assert payload['identity_link_status'] == 'linked'
    assert payload['linked_inventory_device_id'] == inventory.device_id


def test_get_effective_policy_returns_fresh_cache():
    tracked = _tracked_device('AA:BB:CC:DD:EE:42')
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['global.example'])
    db.session.commit()

    rebuilt = service.rebuild_effective_policy_cache(tracked.id)
    db.session.commit()
    payload = service.get_effective_policy(tracked.id)

    assert rebuilt['effective_restricted_sites'] == ['global.example']
    assert payload['policy_cache_state'] == 'fresh'
    assert payload['policy_stale'] is False
    assert payload['rebuild_enqueued'] is False


def test_get_effective_policy_rebuilds_stale_cache_inline():
    tracked = _tracked_device('AA:BB:CC:DD:EE:43')
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['old.example'])
    db.session.commit()

    service.rebuild_effective_policy_cache(tracked.id)
    cache_row = DeviceEffectivePolicyCache.query.get(tracked.id)
    cache_row.updated_at = datetime.utcnow() - timedelta(seconds=service.CACHE_FRESH_SECONDS + 10)
    policy.apply_domains(['new.example'])
    db.session.commit()

    payload = service.get_effective_policy(tracked.id)

    assert payload['policy_cache_state'] == 'rebuilt_inline'
    assert payload['effective_restricted_sites'] == ['new.example']
    assert payload['policy_stale'] is False


def test_get_effective_policy_returns_stale_fallback_and_enqueues_rebuild(monkeypatch):
    tracked = _tracked_device('AA:BB:CC:DD:EE:44')
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['cached.example'])
    db.session.commit()

    service.rebuild_effective_policy_cache(tracked.id)
    cache_row = DeviceEffectivePolicyCache.query.get(tracked.id)
    cache_row.updated_at = datetime.utcnow() - timedelta(seconds=service.CACHE_FRESH_SECONDS + 10)
    db.session.commit()

    monkeypatch.setattr(service, 'rebuild_effective_policy_cache', lambda tracked_device_id: (_ for _ in ()).throw(RuntimeError('boom')))

    payload = service.get_effective_policy(tracked.id)

    assert payload['policy_cache_state'] == 'stale_fallback'
    assert payload['policy_stale'] is True
    assert payload['rebuild_enqueued'] is True
    assert PolicyRebuildTask.query.filter_by(tracked_device_id=tracked.id).count() == 1


def test_get_effective_policy_raises_when_cache_missing_and_rebuild_fails(monkeypatch):
    tracked = _tracked_device('AA:BB:CC:DD:EE:45')
    monkeypatch.setattr(service, 'rebuild_effective_policy_cache', lambda tracked_device_id: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(service.EffectivePolicyUnavailable):
        service.get_effective_policy(tracked.id)


def test_build_effective_policy_raises_for_missing_tracked_device():
    with pytest.raises(service.EffectivePolicyUnavailable, match='tracked device not found'):
        service.build_effective_policy(999999)


def test_enqueue_policy_rebuild_for_all_tracked_devices_skips_archived_and_reuses_existing():
    tracked_one = _tracked_device('AA:BB:CC:DD:EE:46')
    tracked_two = _tracked_device('AA:BB:CC:DD:EE:47')
    tracked_two.is_archived = True
    db.session.add(PolicyRebuildTask(tracked_device_id=tracked_one.id, status='pending'))
    db.session.commit()

    count = service.enqueue_policy_rebuild_for_all_tracked_devices(priority=55)
    db.session.commit()

    assert count == 1
    assert PolicyRebuildTask.query.filter_by(tracked_device_id=tracked_one.id).count() == 1
    assert PolicyRebuildTask.query.filter_by(tracked_device_id=tracked_two.id).count() == 0


def test_get_effective_policy_without_rebuild_returns_stale_fallback():
    tracked = _tracked_device('AA:BB:CC:DD:EE:48')
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['cached-no-rebuild.example'])
    db.session.commit()

    service.rebuild_effective_policy_cache(tracked.id)
    cache_row = DeviceEffectivePolicyCache.query.get(tracked.id)
    cache_row.updated_at = datetime.utcnow() - timedelta(seconds=service.CACHE_FRESH_SECONDS + 5)
    db.session.commit()

    payload = service.get_effective_policy(tracked.id, allow_rebuild=False)

    assert payload['policy_cache_state'] == 'stale_fallback'
    assert payload['policy_stale'] is True
    assert payload['rebuild_enqueued'] is False


def test_get_effective_policy_raises_for_missing_tracked_device():
    with pytest.raises(service.EffectivePolicyUnavailable, match='tracked device not found'):
        service.get_effective_policy(424242)
