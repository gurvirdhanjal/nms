import pytest

from middleware.rbac import ENDPOINT_PERMISSIONS
from models.tracked_device import TrackedDevice
from extensions import db


pytestmark = pytest.mark.integration


def _make_device(mac):
    device = TrackedDevice(mac_address=mac, device_name='RBAC Device', availability_status='online')
    db.session.add(device)
    db.session.commit()
    return device


def test_endpoint_permission_map_contains_device_console_routes():
    assert ENDPOINT_PERMISSIONS['device_console_bp.get_device_website_policy'] == 'tracking.history.view'
    assert ENDPOINT_PERMISSIONS['device_console_bp.add_device_website_policy'] == 'devices.edit'
    assert ENDPOINT_PERMISSIONS['device_console_bp.remove_device_website_policy'] == 'devices.edit'
    assert ENDPOINT_PERMISSIONS['device_console_bp.get_device_alerts'] == 'tracking.history.view'
    assert ENDPOINT_PERMISSIONS['device_console_bp.acknowledge_device_alert'] == 'devices.edit'


def test_viewer_can_read_but_cannot_mutate_policy(viewer_client):
    device = _make_device('AA:BB:CC:DD:EE:40')

    get_response = viewer_client.get(f'/api/devices/{device.id}/website-policy')
    assert get_response.status_code in {200, 404}

    post_response = viewer_client.post(
        f'/api/devices/{device.id}/website-policy',
        json={'domain': 'blocked.example'},
    )
    assert post_response.status_code in {403, 404}

    delete_response = viewer_client.delete(
        f'/api/devices/{device.id}/website-policy',
        json={'domains': ['blocked.example']},
    )
    assert delete_response.status_code in {403, 404}
