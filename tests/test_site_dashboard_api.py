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

    def test_dept_aggregates_empty_for_no_depts(self, admin_client, app):
        """A site with no departments should return empty dept_aggregates."""
        with app.app_context():
            site_empty = Site(site_name='Empty Site', address='', timezone='UTC')
            _db.session.add(site_empty)
            _db.session.commit()
            site_empty_id = site_empty.id

        try:
            rv = admin_client.get(f'/api/sites/{site_empty_id}/dashboard-stats')
            assert rv.status_code == 200
            data = rv.get_json()
            assert data['dept_aggregates'] == []
            assert data['active_alert_count'] == 0
        finally:
            with app.app_context():
                site_obj = Site.query.get(site_empty_id)
                if site_obj:
                    _db.session.delete(site_obj)
                    _db.session.commit()

    def test_unassigned_device_excluded_from_dept_aggregates(self, admin_client, seed_data, app):
        """Devices with department_id=None should not cause errors; they're excluded from dept_aggregates."""
        with app.app_context():
            site_id = seed_data['site_id']
            dev_unassigned = Device(
                device_name='unassigned-dev',
                device_ip='10.99.99.99',
                device_type='Switch',
                site_id=site_id,
                department_id=None,
            )
            _db.session.add(dev_unassigned)
            _db.session.commit()
            unassigned_dev_id = dev_unassigned.device_id

        try:
            rv = admin_client.get(f'/api/sites/{seed_data["site_id"]}/dashboard-stats')
            assert rv.status_code == 200
            data = rv.get_json()
            # Unassigned device should not appear in any dept_aggregates entry
            all_dept_totals = sum(d['total'] for d in data['dept_aggregates'])
            # We seeded 3 original devices; this new one should be excluded from dept breakdown
            assert all_dept_totals == 3  # only the 3 assigned devices
        finally:
            with app.app_context():
                dev_obj = Device.query.get(unassigned_dev_id)
                if dev_obj:
                    _db.session.delete(dev_obj)
                    _db.session.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def login(client):
    """Inject an admin session into the test client."""
    from datetime import datetime
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['role'] = 'admin'
        sess['username'] = 'test-admin'
        sess['user_id'] = 1
        sess['last_activity'] = datetime.utcnow().isoformat()


class TestDeviceModal:
    def test_modal_returns_200(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        assert rv.status_code == 200

    def test_modal_device_fields(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert data['device']['device_name'] == 'server-hr-01'
        assert data['device']['device_ip'] == '10.0.0.3'

    def test_modal_network_section(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'network' in data
        assert 'state' in data['network']
        assert 'ping_ms' in data['network']
        assert 'packet_loss' in data['network']
        assert 'last_scan_at' in data['network']

    def test_modal_health_section(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'health' in data
        assert 'available' in data['health']

    def test_modal_active_alerts(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'active_alerts' in data
        assert len(data['active_alerts']) == 1
        assert data['active_alerts'][0]['severity'] == 'CRITICAL'

    def test_modal_floor_plan_placement(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/{seed_data["site_id"]}/device/{seed_data["dev3_id"]}/modal')
        data = rv.get_json()
        assert 'floor_plan_placement' in data
        assert 'has_placement' in data['floor_plan_placement']

    def test_modal_wrong_site_returns_404(self, client, seed_data):
        login(client)
        rv = client.get(f'/api/sites/9999/device/{seed_data["dev3_id"]}/modal')
        assert rv.status_code == 404
