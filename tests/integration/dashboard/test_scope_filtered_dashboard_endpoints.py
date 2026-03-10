from datetime import datetime

import pytest

from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.site import Site
from models.department import Department
from models.user import User


pytestmark = pytest.mark.integration


def _seed_scope_split_devices():
    site_alpha = Site.query.filter_by(site_name='Alpha Site').first()
    site_beta = Site.query.filter_by(site_name='Beta Site').first()
    dept_alpha = Department.query.filter_by(name='Alpha Department').first()
    dept_beta = Department.query.filter_by(name='Beta Department').first()

    device_alpha = Device(
        device_name='Scoped Alpha Device',
        device_type='workstation',
        device_ip='10.0.1.10',
        site_id=site_alpha.id,
        department_id=dept_alpha.id,
    )
    device_beta = Device(
        device_name='Scoped Beta Device',
        device_type='workstation',
        device_ip='10.0.2.20',
        site_id=site_beta.id,
        department_id=dept_beta.id,
    )
    db.session.add_all([device_alpha, device_beta])
    db.session.flush()

    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=device_alpha.device_ip,
                device_name=device_alpha.device_name,
                status='Online',
                ping_time_ms=180,
                packet_loss=1,
                scan_timestamp=datetime.utcnow(),
            ),
            DeviceScanHistory(
                device_ip=device_beta.device_ip,
                device_name=device_beta.device_name,
                status='Online',
                ping_time_ms=280,
                packet_loss=2,
                scan_timestamp=datetime.utcnow(),
            ),
            DashboardEvent(
                event_id='scope-alpha-alert',
                device_id=device_alpha.device_id,
                device_ip=device_alpha.device_ip,
                severity='WARNING',
                event_type='THRESHOLD',
                message='Alpha warning',
                resolved=False,
                timestamp=datetime.utcnow(),
            ),
            DashboardEvent(
                event_id='scope-beta-alert',
                device_id=device_beta.device_id,
                device_ip=device_beta.device_ip,
                severity='CRITICAL',
                event_type='THRESHOLD',
                message='Beta critical',
                resolved=False,
                timestamp=datetime.utcnow(),
            ),
        ]
    )
    db.session.commit()
    return device_alpha, device_beta


def test_dashboard_endpoints_are_scope_filtered_for_manager(manager_client):
    device_alpha, device_beta = _seed_scope_split_devices()
    manager = User.query.get(2)
    from routes import dashboard as dashboard_routes
    dashboard_routes._cache.clear()
    dashboard_routes._cache_ttl.clear()

    summary_response = manager_client.get('/api/dashboard/summary')
    assert summary_response.status_code == 200
    summary = summary_response.get_json()
    assert summary['counts']['total_inventory'] == 1

    top_response = manager_client.get('/api/dashboard/top-problems?fresh=1')
    assert top_response.status_code == 200
    top_payload = top_response.get_json()
    top_ips = {
        row.get('ip')
        for key in ('high_latency', 'high_packet_loss', 'recently_down')
        for row in top_payload.get(key, [])
    }
    assert device_beta.device_ip not in top_ips

    alerts_response = manager_client.get('/api/dashboard/alerts?status=active&limit=100')
    assert alerts_response.status_code == 200
    alerts = alerts_response.get_json()
    assert all(alert.get('device_id') == device_alpha.device_id for alert in alerts)

    inventory_response = manager_client.get('/api/dashboard/inventory')
    assert inventory_response.status_code == 200
    inventory = inventory_response.get_json()
    assert inventory['total_devices'] == 1
    assert all(device.get('device_id') == device_alpha.device_id for device in inventory.get('devices', []))

    snapshot_response = manager_client.get('/api/dashboard/full_snapshot?fresh=1')
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.get_json()
    assert snapshot['meta']['scope_key'] == f"site:{manager.site_id}"
    assert snapshot['summary']['counts']['total_inventory'] == 1
    snapshot_alerts = snapshot.get('alerts') or []
    assert all(alert.get('device_id') == device_alpha.device_id for alert in snapshot_alerts)
