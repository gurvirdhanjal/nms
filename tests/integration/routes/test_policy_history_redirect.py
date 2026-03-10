import pytest

from extensions import db
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def test_policy_history_redirect(admin_client):
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:50', device_name='Policy Redirect Device')
    db.session.add(device)
    db.session.commit()

    response = admin_client.get(f'/devices/{device.id}/policy-history', follow_redirects=False)
    assert response.status_code in {301, 302}
    location = response.headers.get('Location', '')
    assert f'/tracking/history/{device.id}' in location
    assert 'focus=policy' in location
