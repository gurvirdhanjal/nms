import pytest

from config import Config
from extensions import db
from models.restricted_site_policy import RestrictedSitePolicy
from models.tracked_device import TrackedDevice
from models.tracking_sync_envelope import TrackingSyncEnvelope


pytestmark = pytest.mark.integration


def test_tracking_sync_async_mode_keeps_response_contract(client, monkeypatch):
    monkeypatch.setenv('TRACKING_SYNC_ASYNC_ENABLED', '1')
    policy = RestrictedSitePolicy.get_singleton()
    policy.enabled = True
    policy.apply_domains(['async.example'])
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:95', device_name='Async Device', availability_status='online')
    db.session.add(device)
    db.session.commit()

    response = client.post(
        '/api/tracking/sync',
        json={
            'mac_address': device.mac_address,
            'hostname': 'async-host',
            'restricted_sites_policy_version': '',
        },
        headers={'X-API-Key': Config.API_KEY},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['device']['mac_address'] == device.mac_address
    assert payload['restricted_sites_policy_version']
    assert payload['sync_mode'] == 'async'
    assert payload['queue_accepted'] is True
    assert TrackingSyncEnvelope.query.filter_by(id=payload['sync_envelope_id']).count() == 1
