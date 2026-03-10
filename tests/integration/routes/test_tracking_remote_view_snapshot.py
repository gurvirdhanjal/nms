import pytest

from extensions import db
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.integration


def test_remote_view_single_snapshot_returns_single_jpeg(admin_client, monkeypatch):
    device = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:81',
        device_name='Remote View Device',
        hostname='remote-view-host',
        ip_address='10.60.0.81',
        availability_status='online',
    )
    db.session.add(device)
    db.session.commit()

    from routes import tracking as tracking_routes

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.closed = False

        def iter_content(self, chunk_size=4096):
            payload = tracking_routes.generate_placeholder_image('Frame Ready')
            for index in range(0, len(payload), 97):
                yield payload[index:index + 97]

        def close(self):
            self.closed = True

    monkeypatch.setattr(tracking_routes, '_agent_http_get', lambda *args, **kwargs: FakeResponse())

    response = admin_client.get(f'/api/tracking/stream/screenshot/{device.mac_address}?single=1')

    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'
    assert response.data.startswith(b'\xff\xd8')
    assert response.data.endswith(b'\xff\xd9')
