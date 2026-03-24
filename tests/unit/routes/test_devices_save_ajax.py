"""Tests for POST /devices/save — AJAX vs non-AJAX response paths."""
import pytest
from extensions import db
from models.device import Device

pytestmark = pytest.mark.integration

# Minimum form data required by save_device()
_BASE_FORM = {
    'device_name': 'Test Device',
    'device_ip': '10.0.0.50',
    'device_type': 'workstation',
}


@pytest.fixture()
def existing_device(app):
    d = Device(device_name='Existing Device', device_type='workstation', device_ip='10.0.0.99')
    db.session.add(d)
    db.session.commit()
    return d


class TestSaveDeviceAjax:

    def test_ajax_create_returns_json_success(self, admin_client):
        """AJAX new-device POST must return JSON with success=True and device_id."""
        resp = admin_client.post(
            '/devices/save',
            data=_BASE_FORM,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data['success'] is True
        assert 'device_id' in data
        assert isinstance(data['device_id'], int)

    def test_non_ajax_create_redirects(self, admin_client):
        """Non-AJAX new-device POST must return a redirect (302), not JSON."""
        resp = admin_client.post('/devices/save', data=_BASE_FORM)
        assert resp.status_code == 302

    def test_ajax_update_returns_json_success(self, admin_client, existing_device):
        """AJAX edit POST must return JSON with the correct device_id."""
        form = {**_BASE_FORM, 'device_id': str(existing_device.device_id), 'device_name': 'Updated'}
        resp = admin_client.post(
            '/devices/save',
            data=form,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['device_id'] == existing_device.device_id

    def test_ajax_update_deleted_device_returns_404(self, admin_client):
        """AJAX edit for a non-existent device_id must return 404 JSON."""
        form = {**_BASE_FORM, 'device_id': '99999'}
        resp = admin_client.post(
            '/devices/save',
            data=form,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data is not None
        assert data['success'] is False

    def test_non_ajax_update_deleted_device_redirects(self, admin_client):
        """Non-AJAX edit for a non-existent device_id must redirect, not raise 500."""
        form = {**_BASE_FORM, 'device_id': '99999'}
        resp = admin_client.post('/devices/save', data=form)
        assert resp.status_code == 302

    def test_ajax_save_requires_login(self, client):
        """Unauthenticated AJAX save must not succeed — expect redirect to login or 401/403."""
        resp = client.post(
            '/devices/save',
            data=_BASE_FORM,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        # Flask @require_login redirects (302) to login page for unauthenticated requests
        assert resp.status_code in (302, 401, 403)
