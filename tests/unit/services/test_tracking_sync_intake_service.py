from datetime import datetime

import pytest

from extensions import db
from models.restricted_site_policy import RestrictedSiteDomainMeta, RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from models.tracking_sync_envelope import TrackingSyncEnvelope
from services import tracking_sync_intake_service as service


pytestmark = pytest.mark.unit


def test_current_sync_mode_respects_environment(monkeypatch):
    monkeypatch.delenv('TRACKING_SYNC_QUEUE_ENABLED', raising=False)
    monkeypatch.delenv('TRACKING_SYNC_SHADOW_COMPARE', raising=False)
    monkeypatch.delenv('TRACKING_SYNC_ASYNC_ENABLED', raising=False)
    assert service.current_sync_mode() == 'inline'

    monkeypatch.setenv('TRACKING_SYNC_QUEUE_ENABLED', '1')
    assert service.current_sync_mode() == 'queued_inline'

    monkeypatch.setenv('TRACKING_SYNC_SHADOW_COMPARE', 'true')
    assert service.current_sync_mode() == 'shadow'

    monkeypatch.setenv('TRACKING_SYNC_ASYNC_ENABLED', 'yes')
    assert service.current_sync_mode() == 'async'


def test_queue_sync_envelope_persists_summary_and_identity():
    envelope = service.queue_sync_envelope(
        payload={'hostname': 'pc-queue', 'current_stats': {'system_metrics': {'cpu': 20}}},
        normalized_mac='AA:BB:CC:DD:EE:71',
        unique_client_id='client-71',
        tracked_device_id=None,
    )
    db.session.commit()

    stored = TrackingSyncEnvelope.query.get(envelope.id)
    assert stored.normalized_mac == 'AA:BB:CC:DD:EE:71'
    assert stored.unique_client_id == 'client-71'
    assert stored.inline_summary_json['hostname'] == 'pc-queue'
    assert stored.core_status == 'pending'
    assert stored.violation_status == 'pending'


def test_build_sync_policy_payload_returns_policy_only_when_version_differs():
    tracked = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:72',
        device_name='Sync Policy Device',
        availability_status='online',
        last_policy_version_seen='agent-v0',
        last_policy_sync_at=datetime(2026, 3, 6, 14, 0, 0),
    )
    db.session.add(tracked)
    db.session.flush()
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['global-sync.example'])
    db.session.add(RestrictedSiteDomainMeta(device_id=tracked.id, domain='device-sync.example', category='Custom'))
    db.session.commit()

    mismatch = service.build_sync_policy_payload(tracked.id, client_policy_version='old-version')
    matched = service.build_sync_policy_payload(tracked.id, client_policy_version=mismatch['restricted_sites_policy_version'])

    assert mismatch['restricted_sites_policy_version']
    assert mismatch['restricted_sites_policy']['blocked_domains'] == ['device-sync.example', 'global-sync.example']
    assert 'restricted_sites_policy' not in matched
