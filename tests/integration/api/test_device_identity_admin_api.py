import pytest

from extensions import db
from models.audit_log import AuditLog
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def _seed_candidate(mac='AA:BB:CC:DD:EE:91'):
    device = Device(device_name='Inventory Identity', device_type='workstation', device_ip='10.0.0.91', macaddress=mac)
    tracked = TrackedDevice(device_name='Tracked Identity', mac_address=mac, availability_status='online')
    db.session.add_all([device, tracked])
    db.session.flush()
    candidate = DeviceIdentityLinkCandidate(
        device_id=device.device_id,
        tracked_device_id=tracked.id,
        normalized_mac=mac,
        ambiguity_group_key=f'mac:{mac}',
        status='pending',
    )
    db.session.add(candidate)
    db.session.commit()
    return device, tracked, candidate


def test_device_identity_links_page_and_list_api(admin_client):
    device, tracked, candidate = _seed_candidate()

    page = admin_client.get('/admin/device-identity-links')
    api = admin_client.get('/api/admin/device-identity-links?status=pending')

    assert page.status_code == 200
    assert b'Device Identity Links' in page.data
    assert f'{device.device_id} -> {tracked.id}'.encode() in page.data
    assert api.status_code == 200
    payload = api.get_json()
    assert payload['success'] is True
    assert payload['candidates'][0]['device_id'] == device.device_id
    assert payload['candidates'][0]['tracked_device_id'] == tracked.id


def test_device_identity_confirm_creates_link_and_audits(admin_client):
    device, tracked, candidate = _seed_candidate('AA:BB:CC:DD:EE:92')
    competing = TrackedDevice(device_name='Tracked Identity 2', mac_address='AA:BB:CC:DD:EE:93', availability_status='online')
    db.session.add(competing)
    db.session.flush()
    db.session.add(
        DeviceIdentityLinkCandidate(
            device_id=device.device_id,
            tracked_device_id=competing.id,
            normalized_mac='AA:BB:CC:DD:EE:92',
            ambiguity_group_key='mac:AA:BB:CC:DD:EE:92',
            status='pending',
        )
    )
    db.session.commit()

    response = admin_client.post(
        '/api/admin/device-identity-links',
        json={'candidate_id': candidate.id, 'action': 'confirm', 'reason': 'validated'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    link = DeviceIdentityLink.query.filter_by(device_id=device.device_id, tracked_device_id=tracked.id).first()
    assert link is not None
    assert AuditLog.query.filter_by(entity_type='device_identity_link').count() == 1
    statuses = {row.id: row.status for row in DeviceIdentityLinkCandidate.query.order_by(DeviceIdentityLinkCandidate.id.asc()).all()}
    assert statuses[candidate.id] == 'confirmed'
    assert 'rejected' in statuses.values()


def test_device_identity_reject_marks_candidate(admin_client):
    _, _, candidate = _seed_candidate('AA:BB:CC:DD:EE:93')

    response = admin_client.post(
        '/api/admin/device-identity-links',
        json={'candidate_id': candidate.id, 'action': 'reject', 'reason': 'not the same host'},
    )

    assert response.status_code == 200
    db.session.refresh(candidate)
    assert candidate.status == 'rejected'
    assert DeviceIdentityLink.query.count() == 0


def test_device_identity_decision_validates_payload(admin_client):
    response = admin_client.post('/api/admin/device-identity-links', json={'action': 'confirm'})
    assert response.status_code == 400
    assert response.get_json()['success'] is False

    response = admin_client.post(
        '/api/admin/device-identity-links',
        json={'candidate_id': 1, 'action': 'invalid'},
    )
    assert response.status_code == 400


def test_device_identity_list_filters_active_links(admin_client):
    device, tracked, candidate = _seed_candidate('AA:BB:CC:DD:EE:94')
    db.session.add(
        DeviceIdentityLink(
            device_id=device.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:94',
            link_source='manual',
            is_active=True,
        )
    )
    db.session.commit()

    response = admin_client.get(
        f'/api/admin/device-identity-links?status=active&mac=AA-BB-CC-DD-EE-94&device_id={device.device_id}&tracked_device_id={tracked.id}'
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert len(payload['links']) == 1
    assert payload['links'][0]['device_id'] == device.device_id
    assert payload['links'][0]['tracked_device_id'] == tracked.id
    assert payload['candidates'][0]['device_id'] == device.device_id


def test_device_identity_decision_uses_fallback_payload_when_result_has_no_to_dict(admin_client, monkeypatch):
    _, _, candidate = _seed_candidate('AA:BB:CC:DD:EE:95')

    class BareResult:
        pass

    monkeypatch.setattr('routes.device_identity_admin.DeviceLinkService.decide_candidate', lambda *args, **kwargs: BareResult())

    response = admin_client.post(
        '/api/admin/device-identity-links',
        json={'candidate_id': candidate.id, 'action': 'confirm'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['result'] == {'candidate_id': candidate.id, 'status': 'confirm'}
