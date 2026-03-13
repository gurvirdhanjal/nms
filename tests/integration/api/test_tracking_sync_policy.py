import json
from unittest.mock import patch
import pytest
from extensions import db
from datetime import datetime, timezone
from models.restricted_site_policy import (
    RestrictedSitePolicy,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
    RestrictedSiteAlertState,
)
from models.tracked_device import TrackedDevice, TrackingSample
from config import Config

pytestmark = pytest.mark.integration

def _create_tracked_device(mac_address='AA:BB:CC:00:11:22'):
    device = TrackedDevice(
        mac_address=mac_address,
        device_name='Sync-Test-Device',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()
    return device

def test_tracking_sync_merges_global_and_device_policy(client):
    # Setup global policy
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['global1.com', 'global2.com'])
    db.session.commit()

    # Setup device and device-specific policy
    device = _create_tracked_device()
    meta1 = RestrictedSiteDomainMeta(device_id=device.id, domain='device1.com', category='Custom')
    meta2 = RestrictedSiteDomainMeta(device_id=device.id, domain='global1.com', category='Custom') # overlap
    db.session.add_all([meta1, meta2])
    db.session.commit()

    # Perform sync request with correct API key
    payload = {
        'mac_address': device.mac_address,
        'version': '1.0',
        'hostname': 'Test-PC',
        'restricted_sites_policy_version': '' # force full download
    }

    response = client.post(
        '/api/tracking/sync',
        json=payload,
        headers={'X-API-Key': Config.API_KEY},
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.get_json()}"
    data = response.get_json()

    assert data['success'] is True
    assert 'restricted_sites_policy' in data
    assert data['restricted_sites_policy_version']
    assert data['sync_mode'] == 'inline'

    sync_policy = data['restricted_sites_policy']
    merged_domains = sync_policy.get('blocked_domains', [])
    
    # Assert merged lists and deduplication
    assert len(merged_domains) == 3, f"Expected 3 merged domains, got {len(merged_domains)}: {merged_domains}"
    assert 'global1.com' in merged_domains
    assert 'global2.com' in merged_domains
    assert 'device1.com' in merged_domains
    db.session.refresh(device)
    assert device.last_policy_sync_at is not None


def test_tracking_sync_ingests_restricted_site_events(client):
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['example.com'])
    db.session.commit()

    device = _create_tracked_device(mac_address='AA:BB:CC:00:11:33')
    observed_at = datetime.now(timezone.utc).isoformat()

    payload = {
        'mac_address': device.mac_address,
        'version': '1.0',
        'hostname': 'Alert-Test-PC',
        'restricted_sites_policy_version': '',
        'restricted_site_events': [
            {
                'domain': 'example.com',
                'source': 'window_title',
                'process_name': 'chrome.exe',
                'raw_evidence': 'Example Domain - Google Chrome',
                'observed_at_utc': observed_at,
            }
        ],
    }

    response = client.post(
        '/api/tracking/sync',
        json=payload,
        headers={'X-API-Key': Config.API_KEY},
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.get_json()}"
    data = response.get_json()
    assert data['success'] is True
    assert int((data.get('restricted_site_ingest') or {}).get('ingested_events', 0)) == 1
    db.session.refresh(device)
    assert device.last_policy_sync_at is not None

    persisted_event = RestrictedSiteEvent.query.filter_by(device_id=device.id, domain='example.com').first()
    assert persisted_event is not None
    assert persisted_event.matched_rule == 'example.com'

    alert_state = RestrictedSiteAlertState.query.filter_by(device_id=device.id, domain='example.com').first()
    assert alert_state is not None
    assert int(alert_state.hit_count or 0) == 1

def test_tracking_sync_persists_agent_policy_version(client):
    device = _create_tracked_device(mac_address='AA:BB:CC:00:11:44')

    response = client.post(
        '/api/tracking/sync',
        json={
            'mac_address': device.mac_address,
            'hostname': 'Policy-Version-PC',
            'restricted_sites_policy_version': 'agent-v9',
        },
        headers={'X-API-Key': Config.API_KEY},
    )

    assert response.status_code == 200
    db.session.refresh(device)
    assert device.last_policy_version_seen == 'agent-v9'
    assert device.last_policy_sync_at is not None


def test_tracking_sync_normalizes_legacy_service_payload(client):
    device = _create_tracked_device(mac_address='AA:BB:CC:00:11:55')

    response = client.post(
        '/api/tracking/sync',
        json={
            'mac_address': device.mac_address,
            'hostname': 'Legacy-Service-PC',
            'current_stats': {
                'activity': {
                    'keyboard_active': True,
                    'mouse_active': False,
                    'idle_seconds': 9,
                    'total_active_today': 5400,
                    'keyboard_count': 11,
                    'mouse_count': 6,
                },
                'system': {
                    'cpu': 24.5,
                    'memory': 58.0,
                    'current_app': 'Code.exe',
                },
                'network': {
                    'upload_speed_kbps': 32.0,
                    'download_speed_kbps': 256.0,
                },
            },
        },
        headers={'X-API-Key': Config.API_KEY},
    )

    assert response.status_code == 200, response.get_json()
    db.session.refresh(device)

    tracking_payload = json.loads(device.tracking_data)
    assert device.metrics_available is True
    assert device.availability_status == 'online'
    assert tracking_payload['current_activity']['keyboard_active'] is True
    assert tracking_payload['current_activity']['current_application'] == 'Code.exe'
    assert tracking_payload['today_stats']['total_active_hours'] == pytest.approx(1.5)
    assert tracking_payload['today_stats']['keyboard_events'] == 11
    assert tracking_payload['system_metrics']['cpu_percent'] == pytest.approx(24.5)
    assert tracking_payload['system_metrics']['network_speed']['download_speed_kbps'] == pytest.approx(256.0)
    assert TrackingSample.query.filter_by(device_id=device.id).count() == 1


def test_tracking_register_accepts_bound_agent_key_without_shared_api_key(client):
    device = _create_tracked_device(mac_address='AA:BB:CC:00:11:66')

    sync_response = client.post(
        '/api/tracking/sync',
        json={
            'mac_address': device.mac_address,
            'hostname': 'Bound-Agent-PC',
        },
        headers={'X-API-Key': Config.API_KEY},
    )
    assert sync_response.status_code == 200, sync_response.get_json()
    binding = (sync_response.get_json() or {}).get('agent_binding') or {}
    assert binding.get('key_id')
    assert binding.get('agent_key')

    register_response = client.get(
        '/api/tracking/register',
        headers={
            'X-Agent-Key-Id': binding['key_id'],
            'X-Agent-Key': binding['agent_key'],
        },
    )

    assert register_response.status_code == 200, register_response.get_json()
    payload = register_response.get_json()
    assert payload['success'] is True
    assert payload['status'] == 'active'
