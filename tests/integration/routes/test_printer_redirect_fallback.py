import pytest

from extensions import db
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def test_printer_route_redirects_to_live_tracking_for_tracked_device_id(admin_client):
    tracked = TrackedDevice(mac_address='AA:BB:CC:DD:EE:60', device_name='Tracked Printer Route Device')
    db.session.add(tracked)
    db.session.commit()

    response = admin_client.get(f'/printer/{tracked.id}', follow_redirects=False)
    assert response.status_code in {301, 302}
    location = response.headers.get('Location', '')
    assert '/tracking/live' in location
    assert 'mac=AA:BB:CC:DD:EE:60' in location


def test_printer_route_renders_detail_with_warning_when_no_live_mapping(admin_client):
    device = Device(device_name='Office Printer', device_type='printer', device_ip='10.0.0.12', macaddress='AA:BB:CC:DD:EE:61')
    db.session.add(device)
    db.session.commit()

    response = admin_client.get(f'/printer/{device.device_id}')
    assert response.status_code == 200
    assert b'no live tracked device matched' in response.data.lower()


def test_printer_route_uses_identity_link_for_live_tracking_redirect(admin_client):
    device = Device(device_name='Linked Printer', device_type='printer', device_ip='10.0.0.13', macaddress='AA:BB:CC:DD:EE:62')
    tracked = TrackedDevice(mac_address='AA:BB:CC:DD:EE:62', device_name='Tracked Linked Printer')
    db.session.add_all([device, tracked])
    db.session.flush()
    db.session.add(
        DeviceIdentityLink(
            device_id=device.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:62',
            link_source='manual',
            is_active=True,
        )
    )
    db.session.commit()

    response = admin_client.get(f'/printer/{device.device_id}')

    assert response.status_code == 200
    assert b'/tracking/live?mac=AA:BB:CC:DD:EE:62' in response.data
