"""Tests for reports route parameter validation.

Covers: hours parameter boundary validation on /api/device_history endpoint.
"""
import pytest

pytestmark = pytest.mark.integration


class TestHoursParameterValidation:

    def test_hours_abc_returns_400(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=abc')
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'hours' in data.get('error', '').lower()

    def test_hours_zero_returns_400(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=0')
        assert resp.status_code == 400

    def test_hours_negative_returns_400(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=-1')
        assert resp.status_code == 400

    def test_hours_above_max_returns_400(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=9999')
        assert resp.status_code == 400

    def test_hours_24_returns_200(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=24')
        assert resp.status_code == 200

    def test_hours_168_returns_200(self, admin_client):
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=168')
        assert resp.status_code == 200

    def test_hours_8760_returns_200(self, admin_client):
        """Maximum valid value: 1 year."""
        resp = admin_client.get('/api/device_history?device_ip=10.0.0.1&hours=8760')
        assert resp.status_code == 200
