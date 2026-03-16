import io

import pytest

from extensions import db
from models.tracked_device import TrackedDevice
from routes import tracking as tracking_routes


pytestmark = pytest.mark.integration


class FakeJsonResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {'Content-Type': 'application/json'}

    def json(self):
        return self._payload

    def close(self):
        return None


class FakeStreamResponse:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {
            'Content-Type': 'application/octet-stream',
            'Content-Disposition': 'attachment; filename="workstation.txt"',
        }
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index:index + chunk_size]

    def close(self):
        self.closed = True


def _create_tracked_device(**overrides):
    device = TrackedDevice(
        mac_address=overrides.pop('mac_address', 'AA:BB:CC:DD:EE:91'),
        device_name=overrides.pop('device_name', 'Files Device'),
        hostname=overrides.pop('hostname', 'files-host'),
        ip_address=overrides.pop('ip_address', '10.90.0.91'),
        availability_status=overrides.pop('availability_status', 'online'),
        **overrides,
    )
    db.session.add(device)
    db.session.commit()
    return device


def test_tracked_device_file_list_prefers_last_agent_sync_ip(admin_client, monkeypatch):
    device = _create_tracked_device(
        mac_address='AA:BB:CC:DD:EE:91',
        ip_address='127.0.0.1',
        last_agent_sync_ip='10.55.0.6',
    )
    captured = {}

    def fake_get(url, timeout=2.0, headers=None, stream=False, silent=False, params=None):
        captured['url'] = url
        captured['timeout'] = timeout
        captured['headers'] = headers
        captured['params'] = params
        return FakeJsonResponse({
            'success': True,
            'current_path': r'C:\Users\APL',
            'parent_path': r'C:\Users',
            'items': [{'name': 'Downloads', 'path': r'C:\Users\APL\Downloads', 'is_dir': True}],
        })

    monkeypatch.setattr(tracking_routes, 'SHARED_API_KEY', 'test-shared-key')
    monkeypatch.setattr(tracking_routes, '_agent_http_get', fake_get)

    response = admin_client.post(
        f'/api/tracking/devices/{device.id}/files/list',
        json={'path': r'C:\Users\APL'},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['agent_ip'] == '10.55.0.6'
    assert captured['url'] == 'http://10.55.0.6:5002/api/files/list'
    assert captured['params'] == {'path': r'C:\Users\APL'}
    assert captured['headers']['X-API-Key'] == 'test-shared-key'


def test_tracked_device_file_upload_proxies_browser_file_to_agent(admin_client, monkeypatch):
    device = _create_tracked_device(mac_address='AA:BB:CC:DD:EE:92')
    captured = {}

    def fake_post(url, timeout=2.0, headers=None, json_data=None, stream=False, silent=False, data=None, files=None):
        captured['url'] = url
        captured['timeout'] = timeout
        captured['headers'] = headers
        captured['data'] = data
        captured['files'] = files
        uploaded_stream = files[0][1][1]
        captured['content'] = uploaded_stream.read()
        uploaded_stream.seek(0)
        return FakeJsonResponse({'success': True, 'uploaded': 1, 'uploaded_files': [{'filename': 'installer.msi'}]})

    monkeypatch.setattr(tracking_routes, 'SHARED_API_KEY', 'test-shared-key')
    monkeypatch.setattr(tracking_routes, '_agent_http_post', fake_post)

    response = admin_client.post(
        f'/api/tracking/devices/{device.id}/files/upload',
        data={
            'path': r'C:\Deploy',
            'file': (io.BytesIO(b'msi-binary'), 'installer.msi'),
        },
        content_type='multipart/form-data',
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['uploaded'] == 1
    assert captured['url'] == 'http://10.90.0.91:5002/api/files/upload'
    assert captured['headers']['X-API-Key'] == 'test-shared-key'
    assert captured['data'] == {'path': r'C:\Deploy'}
    assert captured['files'][0][0] == 'file'
    assert captured['files'][0][1][0] == 'installer.msi'
    assert captured['content'] == b'msi-binary'


def test_tracked_device_file_download_streams_agent_payload(admin_client, monkeypatch):
    device = _create_tracked_device(mac_address='AA:BB:CC:DD:EE:93')

    def fake_get(url, timeout=2.0, headers=None, stream=False, silent=False, params=None):
        assert url == 'http://10.90.0.91:5002/api/files/download'
        assert headers['X-API-Key'] == 'test-shared-key'
        assert stream is True
        assert params == {'path': r'C:\Deploy\workstation.txt'}
        return FakeStreamResponse(b'hello-from-workstation')

    monkeypatch.setattr(tracking_routes, 'SHARED_API_KEY', 'test-shared-key')
    monkeypatch.setattr(tracking_routes, '_agent_http_get', fake_get)

    response = admin_client.post(
        f'/api/tracking/devices/{device.id}/files/download',
        json={'path': r'C:\Deploy\workstation.txt', 'name': 'workstation.txt'},
    )

    assert response.status_code == 200
    assert response.data == b'hello-from-workstation'
    assert response.headers['Content-Disposition'] == 'attachment; filename="workstation.txt"'
    assert response.mimetype == 'application/octet-stream'


def test_tracked_device_file_routes_are_admin_only(viewer_client):
    response = viewer_client.post('/api/tracking/devices/1/files/list', json={})
    assert response.status_code in {302, 403}
