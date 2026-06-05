"""Tests for /api/sites/<id>/dashboard-stats — dept_aggregates + active_alert_count."""
import pytest
import uuid
from datetime import datetime

from extensions import db as _db
from models.site import Site
from models.device import Device
from models.dashboard import DashboardEvent
from models.department import Department


# ---------------------------------------------------------------------------
# Fixtures
# Use function scope so data is (re)created after conftest's _reset_db wipes
# the database between tests.
# ---------------------------------------------------------------------------

@pytest.fixture()
def seed_data(app):
    """Create a site with 2 departments, 3 devices, and 1 unresolved alert."""
    with app.app_context():
        site = Site(site_name='Dashboard Test Site', address='123 Test St', timezone='UTC')
        _db.session.add(site)
        _db.session.flush()

        dept_it = Department(name='IT Dept', site_id=site.id)
        dept_hr = Department(name='HR Dept', site_id=site.id)
        _db.session.add_all([dept_it, dept_hr])
        _db.session.flush()

        dev1 = Device(device_name='switch-01', device_ip='10.0.0.1', device_type='Switch',
                      site_id=site.id, department_id=dept_it.id)
        dev2 = Device(device_name='ap-01', device_ip='10.0.0.2', device_type='AP',
                      site_id=site.id, department_id=dept_it.id)
        dev3 = Device(device_name='server-hr-01', device_ip='10.0.0.3', device_type='Server',
                      site_id=site.id, department_id=dept_hr.id)
        _db.session.add_all([dev1, dev2, dev3])
        _db.session.flush()

        alert = DashboardEvent(
            event_id=str(uuid.uuid4()),
            device_id=dev3.device_id,
            device_ip='10.0.0.3',
            severity='CRITICAL',
            message='Ping timeout',
            site_id=site.id,
            department_id=dept_hr.id,
            resolved=False,
        )
        _db.session.add(alert)
        _db.session.commit()
        return {
            'site_id': site.id,
            'dept_it_id': dept_it.id,
            'dept_it_name': dept_it.name,
            'dept_hr_id': dept_hr.id,
            'dept_hr_name': dept_hr.name,
            'dev3_id': dev3.device_id,
        }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDashboardStats:
    def test_dept_aggregates_present(self, admin_client, seed_data):
        rv = admin_client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        assert rv.status_code == 200
        data = rv.get_json()
        assert 'dept_aggregates' in data
        assert len(data['dept_aggregates']) == 2

    def test_dept_aggregate_fields(self, admin_client, seed_data):
        rv = admin_client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        dept = next(d for d in data['dept_aggregates'] if d['dept_name'] == seed_data['dept_hr_name'])
        assert 'dept_id' in dept
        assert 'total' in dept
        assert 'online' in dept
        assert 'offline' in dept
        assert 'alerts' in dept
        assert 'health_pct' in dept
        assert dept['alerts'] == 1

    def test_active_alert_count_present(self, admin_client, seed_data):
        rv = admin_client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        assert 'active_alert_count' in data
        assert data['active_alert_count'] == 1

    def test_health_pct_formula(self, admin_client, seed_data):
        rv = admin_client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
        data = rv.get_json()
        # IT: 2 devices, both unknown state (no scan history) → online=0, health_pct=0
        dept_it = next(d for d in data['dept_aggregates'] if d['dept_name'] == seed_data['dept_it_name'])
        expected_pct = round(dept_it['online'] / dept_it['total'] * 100) if dept_it['total'] > 0 else 0
        assert dept_it['health_pct'] == expected_pct
