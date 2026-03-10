import pytest
from datetime import datetime

from extensions import db
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.restricted_site_policy import RestrictedSiteDomainMeta, RestrictedSiteEvent, RestrictedSitePolicy
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def _create_tracked_device():
    from extensions import db

    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:10',
        device_name='Tracked-Policy-Device',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()
    return device


def test_get_website_policy_defaults(admin_client):
    device = _create_tracked_device()

    response = admin_client.get(f'/api/devices/{device.id}/website-policy')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['success'] is True
    assert payload['mode'] == 'active'
    assert payload['restricted_sites'] == []
    assert payload['global_restricted_sites'] == []
    assert payload['effective_restricted_sites'] == []
    assert payload['effective_policy_version']
    assert payload['policy_cache_state'] in {'fresh', 'rebuilt_inline'}
    assert payload['identity_link_status'] == 'unlinked'
    assert payload['violations_today'] == 0
    assert isinstance(payload['recent_violations'], list)


def test_add_domain_then_fetch_policy(admin_client):
    device = _create_tracked_device()
    policy = RestrictedSitePolicy.get_singleton()
    policy.apply_domains(['global-policy.example'])
    inventory = Device(device_name='Inventory Policy', device_type='workstation', device_ip='10.0.0.200', macaddress=device.mac_address)
    db.session.add(inventory)
    db.session.flush()
    db.session.add(
        DeviceIdentityLink(
            device_id=inventory.device_id,
            tracked_device_id=device.id,
            normalized_mac=device.mac_address,
            link_source='manual',
            is_active=True,
        )
    )
    db.session.commit()

    post_response = admin_client.post(
        f'/api/devices/{device.id}/website-policy',
        json={'domain': 'youtube.com', 'category': 'Productivity', 'reason': 'focus time'},
    )
    assert post_response.status_code == 201
    post_payload = post_response.get_json()
    assert post_payload['success'] is True
    assert post_payload['domain'] == 'youtube.com'

    stored = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id, domain='youtube.com').first()
    assert stored is not None
    assert stored.category == 'Productivity'
    assert stored.reason == 'focus time'

    get_response = admin_client.get(f'/api/devices/{device.id}/website-policy')
    get_payload = get_response.get_json()
    assert 'youtube.com' in get_payload['restricted_sites']
    assert get_payload['restricted_site_meta'][0]['category'] == 'Productivity'
    assert get_payload['global_restricted_sites'] == ['global-policy.example']
    assert get_payload['effective_restricted_sites'] == ['global-policy.example', 'youtube.com']
    assert get_payload['agent_policy_version'] is None
    assert get_payload['linked_inventory_device_id'] == inventory.device_id
    assert get_payload['identity_link_status'] == 'linked'


def test_remove_domains_from_policy(admin_client):
    device = _create_tracked_device()
    db.session.add(RestrictedSiteDomainMeta(device_id=device.id, domain='example.com', category='Custom'))
    db.session.add(RestrictedSiteDomainMeta(device_id=device.id, domain='chatgpt.com', category='Security'))
    db.session.commit()

    response = admin_client.delete(
        f'/api/devices/{device.id}/website-policy',
        json={'domains': ['example.com']},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['deleted'] == 1

    remaining = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id).all()
    assert len(remaining) == 1
    assert remaining[0].domain == 'chatgpt.com'


def test_get_website_policy_includes_recent_violations(admin_client):
    device = _create_tracked_device()
    violation = RestrictedSiteEvent(
        device_id=device.id,
        domain='youtube.com',
        matched_rule='youtube.com',
        source='dns_cache',
        confidence='MEDIUM',
        policy_version='v1',
        observed_at_utc=datetime.utcnow(),
    )
    db.session.add(violation)
    db.session.commit()

    response = admin_client.get(f'/api/devices/{device.id}/website-policy')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['violations_today'] == 1
    assert payload['recent_violations']
    assert payload['recent_violations'][0]['domain'] == 'youtube.com'


def test_add_domain_validates_and_updates_existing_row(admin_client):
    device = _create_tracked_device()

    bad_response = admin_client.post(
        f'/api/devices/{device.id}/website-policy',
        json={'domain': 'not a domain'},
    )
    assert bad_response.status_code == 400

    create_response = admin_client.post(
        f'/api/devices/{device.id}/website-policy',
        json={'domain': 'example.com', 'category': 'Custom', 'reason': 'first'},
    )
    assert create_response.status_code == 201

    update_response = admin_client.post(
        f'/api/devices/{device.id}/website-policy',
        json={'domain': 'example.com', 'category': 'Security', 'reason': 'updated'},
    )
    assert update_response.status_code == 200
    payload = update_response.get_json()
    assert payload['message'] == 'Policy updated'
    assert payload['created'] is False

    stored = RestrictedSiteDomainMeta.query.filter_by(device_id=device.id, domain='example.com').first()
    assert stored.category == 'Security'
    assert stored.reason == 'updated'


def test_remove_domains_requires_non_empty_domain_list(admin_client):
    device = _create_tracked_device()

    response = admin_client.delete(f'/api/devices/{device.id}/website-policy', json={'domains': []})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
