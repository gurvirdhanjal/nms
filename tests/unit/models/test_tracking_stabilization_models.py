from datetime import datetime

import pytest

from models.alert_fanout_task import AlertFanoutTask
from models.device_effective_policy_cache import DeviceEffectivePolicyCache
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.policy_rebuild_task import PolicyRebuildTask
from models.tracking_sync_envelope import TrackingSyncEnvelope


pytestmark = pytest.mark.unit


def test_identity_link_to_dict():
    row = DeviceIdentityLink(
        id=1,
        device_id=11,
        tracked_device_id=21,
        normalized_mac='AA:BB:CC:DD:EE:01',
        link_source='manual',
        confidence=87,
        is_active=True,
        resolved_by='admin',
        resolution_reason='confirmed',
        created_at=datetime(2026, 1, 1, 10, 0, 0),
        updated_at=datetime(2026, 1, 1, 11, 0, 0),
    )

    payload = row.to_dict()

    assert payload['device_id'] == 11
    assert payload['tracked_device_id'] == 21
    assert payload['normalized_mac'] == 'AA:BB:CC:DD:EE:01'
    assert payload['link_source'] == 'manual'
    assert payload['confidence'] == 87
    assert payload['is_active'] is True
    assert payload['resolved_by'] == 'admin'


def test_identity_link_candidate_to_dict():
    row = DeviceIdentityLinkCandidate(
        id=2,
        device_id=12,
        tracked_device_id=22,
        normalized_mac='AA:BB:CC:DD:EE:02',
        ambiguity_group_key='mac:AA:BB:CC:DD:EE:02',
        candidate_source='mac',
        candidate_score=100,
        status='pending',
        detected_at=datetime(2026, 1, 2, 10, 0, 0),
        decided_at=datetime(2026, 1, 2, 11, 0, 0),
        decided_by='admin',
        decision_reason='reviewed',
    )

    payload = row.to_dict()

    assert payload['device_id'] == 12
    assert payload['tracked_device_id'] == 22
    assert payload['ambiguity_group_key'] == 'mac:AA:BB:CC:DD:EE:02'
    assert payload['candidate_score'] == 100
    assert payload['status'] == 'pending'
    assert payload['decided_by'] == 'admin'


def test_effective_policy_cache_to_dict():
    row = DeviceEffectivePolicyCache(
        tracked_device_id=5,
        global_domains_json=['global.example'],
        device_domains_json=['device.example'],
        effective_domains_json=['device.example', 'global.example'],
        effective_policy_version='v1_2',
        updated_at=datetime(2026, 1, 3, 8, 0, 0),
    )

    payload = row.to_dict()

    assert payload == {
        'tracked_device_id': 5,
        'global_restricted_sites': ['global.example'],
        'device_restricted_sites': ['device.example'],
        'effective_restricted_sites': ['device.example', 'global.example'],
        'effective_policy_version': 'v1_2',
        'updated_at': '2026-01-03T08:00:00',
    }


def test_policy_rebuild_task_to_dict():
    row = PolicyRebuildTask(
        id=7,
        tracked_device_id=99,
        status='running',
        priority=120,
        retry_count=2,
        next_run_at=datetime(2026, 1, 4, 8, 0, 0),
        started_at=datetime(2026, 1, 4, 8, 1, 0),
        finished_at=datetime(2026, 1, 4, 8, 2, 0),
        error_code='ERR',
        error_message='failed once',
        claim_token='abc',
        claim_expires_at=datetime(2026, 1, 4, 8, 3, 0),
    )

    payload = row.to_dict()

    assert payload['id'] == 7
    assert payload['tracked_device_id'] == 99
    assert payload['status'] == 'running'
    assert payload['retry_count'] == 2
    assert payload['claim_token'] == 'abc'
    assert payload['error_code'] == 'ERR'


def test_alert_fanout_task_to_dict():
    row = AlertFanoutTask(
        id=9,
        dashboard_event_id='evt-1',
        tracked_device_id=3,
        channel='email',
        delivery_key='key-1',
        payload_json={'message': 'x'},
        status='pending',
        priority=200,
        retry_count=1,
        next_run_at=datetime(2026, 1, 5, 10, 0, 0),
        claim_token='claim-1',
        claim_expires_at=datetime(2026, 1, 5, 10, 1, 0),
        provider_message_id='msg-1',
    )

    payload = row.to_dict()

    assert payload['dashboard_event_id'] == 'evt-1'
    assert payload['channel'] == 'email'
    assert payload['delivery_key'] == 'key-1'
    assert payload['provider_message_id'] == 'msg-1'
    assert payload['retry_count'] == 1


def test_tracking_sync_envelope_to_dict():
    row = TrackingSyncEnvelope(
        id=10,
        normalized_mac='AA:BB:CC:DD:EE:10',
        unique_client_id='client-1',
        tracked_device_id=4,
        payload_json={'hostname': 'pc'},
        received_at=datetime(2026, 1, 6, 10, 0, 0),
        shadow_status='completed',
        core_status='pending',
        violation_status='running',
        core_retry_count=1,
        violation_retry_count=2,
        core_next_run_at=datetime(2026, 1, 6, 10, 1, 0),
        violation_next_run_at=datetime(2026, 1, 6, 10, 2, 0),
        dedupe_key='dedupe-1',
    )

    payload = row.to_dict()

    assert payload['normalized_mac'] == 'AA:BB:CC:DD:EE:10'
    assert payload['unique_client_id'] == 'client-1'
    assert payload['shadow_status'] == 'completed'
    assert payload['core_retry_count'] == 1
    assert payload['violation_retry_count'] == 2
    assert payload['dedupe_key'] == 'dedupe-1'
