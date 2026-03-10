import pytest

from services.tracking_sync_core_service import (
    apply_sync_core_plan,
    build_sync_dedupe_key,
    plan_sync_core_mutations,
)


pytestmark = pytest.mark.unit


def test_build_sync_dedupe_key_is_stable():
    payload = {
        'hostname': 'pc-1',
        'restricted_sites_policy_version': 'v1',
        'current_stats': {'system_metrics': {'cpu': 10}},
        'restricted_site_events': [{'domain': 'example.com'}],
    }

    key_one = build_sync_dedupe_key(payload, 'AA:BB:CC:DD:EE:61', 'client-1')
    key_two = build_sync_dedupe_key(dict(payload), 'AA:BB:CC:DD:EE:61', 'client-1')

    assert key_one == key_two


def test_plan_sync_core_mutations_handles_current_stats_and_restricted_events():
    payload = {
        'hostname': 'pc-2',
        'restricted_sites_policy_version': 'v2',
        'current_stats': {
            'current_activity': {'keyboard_active': True},
            'system_metrics': {'cpu': 25},
            'network': {'download': 10},
        },
        'restricted_site_events': [{'domain': 'example.com'}],
    }

    plan = plan_sync_core_mutations(payload, 'AA:BB:CC:DD:EE:62', 'client-2')

    assert plan.hostname == 'pc-2'
    assert plan.current_stats_valid is True
    assert plan.has_current_activity is True
    assert plan.has_system_metrics is True
    assert plan.has_network_metrics is True
    assert plan.restricted_event_count == 1
    assert plan.policy_version_seen == 'v2'


def test_plan_sync_core_mutations_supports_legacy_payload_shape():
    payload = {
        'hostname': 'pc-3',
        'current_activity': {'idle_seconds': 5},
        'system_metrics': {'cpu': 3},
        'restricted_site_events': {'events': [{'domain': 'example.com'}]},
    }

    plan = plan_sync_core_mutations(payload, 'AA:BB:CC:DD:EE:63', None)
    result = apply_sync_core_plan(plan)

    assert plan.current_stats_valid is True
    assert plan.has_current_activity is True
    assert plan.has_system_metrics is True
    assert result.applied is True
    assert result.summary['restricted_event_count'] == 1
