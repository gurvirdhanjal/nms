"""Tests for POST /devices/save — AJAX vs non-AJAX response paths."""
import pytest
from extensions import db
from models.compliance_profile import ComplianceProfile
from models.device import Device
from models.snmp_config import DeviceSnmpConfig

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


@pytest.fixture()
def compliance_profile(app):
    profile = ComplianceProfile(
        name='Route Test Profile',
        rules_json={'cpu_warning': 72, 'memory_warning': 81, 'disk_critical': 94},
    )
    db.session.add(profile)
    db.session.commit()
    return profile


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

    def test_ajax_create_persists_compliance_profile_id(self, admin_client, compliance_profile):
        """AJAX create must persist the selected compliance profile."""
        form = {**_BASE_FORM, 'compliance_profile_id': str(compliance_profile.id)}
        resp = admin_client.post(
            '/devices/save',
            data=form,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        saved = Device.query.get(data['device_id'])
        assert saved is not None
        assert saved.compliance_profile_id == compliance_profile.id

    def test_ajax_update_persists_compliance_profile_id(self, admin_client, existing_device, compliance_profile):
        """AJAX update must round-trip compliance_profile_id through the existing save route."""
        form = {
            **_BASE_FORM,
            'device_id': str(existing_device.device_id),
            'device_name': 'Updated With Profile',
            'compliance_profile_id': str(compliance_profile.id),
        }
        resp = admin_client.post(
            '/devices/save',
            data=form,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        db.session.refresh(existing_device)
        assert existing_device.compliance_profile_id == compliance_profile.id

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

    def test_ajax_save_returns_inventory_device_payload(self, admin_client):
        """AJAX save should return the device payload needed for in-place UI refreshes."""
        resp = admin_client.post(
            '/devices/save',
            data=_BASE_FORM,
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert isinstance(data.get('device'), dict)
        assert data['device']['device_id'] == data['device_id']
        assert data['device']['monitoring_mode'] == 'ping'

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

    def test_snmp_patch_enables_snmp_monitoring(self, admin_client, existing_device):
        """SNMP modal save path should enable SNMP and return refreshed row payload."""
        resp = admin_client.patch(
            f'/api/devices/{existing_device.device_id}/snmp',
            json={
                'enabled': True,
                'snmp_version': 'v2c',
                'snmp_port': 161,
                'snmp_community': 'private',
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['device']['snmp_enabled'] is True
        assert data['device']['monitoring_mode'] == 'snmp'

        db.session.refresh(existing_device)
        assert existing_device.monitoring_mode == 'snmp'
        assert existing_device.is_monitored is True

        config = DeviceSnmpConfig.query.filter_by(device_id=existing_device.device_id).first()
        assert config is not None
        assert config.is_enabled is True
        assert config.community_string == 'private'

    def test_snmp_patch_disables_snmp_and_falls_back_to_ping(self, admin_client, existing_device):
        """Disabling SNMP should leave the device in a non-SNMP mode and turn config polling off."""
        existing_device.monitoring_mode = 'snmp'
        existing_device.is_monitored = True
        existing_device.snmp_config = DeviceSnmpConfig(
            snmp_version='2c',
            community_string='private',
            snmp_port=161,
            is_enabled=True,
        )
        db.session.commit()

        resp = admin_client.patch(
            f'/api/devices/{existing_device.device_id}/snmp',
            json={'enabled': False},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['device']['snmp_enabled'] is False
        assert data['device']['monitoring_mode'] == 'ping'

        db.session.refresh(existing_device)
        assert existing_device.monitoring_mode == 'ping'
        assert existing_device.snmp_config is not None
        assert existing_device.snmp_config.is_enabled is False

    def test_snmp_patch_rejects_invalid_port(self, admin_client, existing_device):
        """SNMP update should validate the port before mutating device state."""
        resp = admin_client.patch(
            f'/api/devices/{existing_device.device_id}/snmp',
            json={'enabled': True, 'snmp_port': 70000},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False
