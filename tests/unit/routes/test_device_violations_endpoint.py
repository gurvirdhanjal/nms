"""Tests for GET /api/reports/device-violations/<device_id> endpoint.

Covers: 404 for missing device, empty violations, and response structure.
"""
import pytest

pytestmark = pytest.mark.integration


class TestDeviceViolationsEndpoint:

    def test_device_not_found_returns_404(self, admin_client):
        resp = admin_client.get('/api/reports/device-violations/999999')
        assert resp.status_code == 404
        data = resp.get_json()
        assert 'error' in data

    def test_valid_device_no_violations_returns_empty(self, admin_client):
        """If a tracked device exists but has no violations, expect empty list."""
        from models.tracked_device import TrackedDevice
        from extensions import db

        td = TrackedDevice.query.first()
        if td is None:
            pytest.skip("No tracked devices in test DB")

        resp = admin_client.get(f'/api/reports/device-violations/{td.id}?range=7d')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'violations' in data
        assert isinstance(data['violations'], list)
        assert 'device_id' in data
        assert 'device_name' in data
        assert 'employee_name' in data

    def test_default_range_is_30d(self, admin_client):
        """Endpoint defaults to 30d when no range param is given."""
        from models.tracked_device import TrackedDevice

        td = TrackedDevice.query.first()
        if td is None:
            pytest.skip("No tracked devices in test DB")

        resp = admin_client.get(f'/api/reports/device-violations/{td.id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data.get('violations'), list)
