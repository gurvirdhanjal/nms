"""Tests for POST /api/snmp/<device_id>/toggle-enabled"""
import pytest
from extensions import db
from models.device import Device
from models.snmp_config import DeviceSnmpConfig

pytestmark = pytest.mark.integration


@pytest.fixture()
def device(app):
    d = Device(device_name='Test Device', device_type='workstation', device_ip='10.0.0.1')
    db.session.add(d)
    db.session.commit()
    return d


@pytest.fixture()
def device_with_snmp_on(app):
    d = Device(device_name='SNMP Device', device_type='switch', device_ip='10.0.0.2')
    db.session.add(d)
    db.session.flush()
    cfg = DeviceSnmpConfig(
        device_id=d.device_id,
        community_string='public',
        snmp_version='2c',
        snmp_port=161,
        poll_interval_seconds=300,
        is_enabled=True,
    )
    db.session.add(cfg)
    db.session.commit()
    return d


class TestSnmpToggle:

    def test_toggle_requires_admin(self, client, device):
        """Unauthenticated request must be rejected."""
        resp = client.post(f'/api/snmp/{device.device_id}/toggle-enabled')
        assert resp.status_code in (401, 403)

    def test_toggle_requires_admin_not_viewer(self, viewer_client, device):
        """Viewer role must be rejected."""
        resp = viewer_client.post(f'/api/snmp/{device.device_id}/toggle-enabled')
        assert resp.status_code in (401, 403)

    def test_toggle_creates_config_when_none_exists(self, admin_client, device):
        """When no DeviceSnmpConfig exists, creates one with is_enabled=True."""
        resp = admin_client.post(f'/api/snmp/{device.device_id}/toggle-enabled')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['is_enabled'] is True

        cfg = DeviceSnmpConfig.query.filter_by(device_id=device.device_id).first()
        assert cfg is not None
        assert cfg.is_enabled is True

    def test_toggle_flips_enabled_to_disabled(self, admin_client, device_with_snmp_on):
        """Existing config with is_enabled=True must flip to False."""
        resp = admin_client.post(f'/api/snmp/{device_with_snmp_on.device_id}/toggle-enabled')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['is_enabled'] is False

        cfg = DeviceSnmpConfig.query.filter_by(device_id=device_with_snmp_on.device_id).first()
        assert cfg.is_enabled is False

    def test_toggle_double_flip_restores_original(self, admin_client, device_with_snmp_on):
        """Two consecutive toggles must return to the original state."""
        admin_client.post(f'/api/snmp/{device_with_snmp_on.device_id}/toggle-enabled')
        resp = admin_client.post(f'/api/snmp/{device_with_snmp_on.device_id}/toggle-enabled')
        data = resp.get_json()
        assert data['is_enabled'] is True

    def test_toggle_returns_404_for_unknown_device(self, admin_client):
        """Non-existent device_id must return 404."""
        resp = admin_client.post('/api/snmp/99999/toggle-enabled')
        assert resp.status_code == 404
