from datetime import datetime, timedelta

import pytest

from extensions import db
from models.device import Device
from models.scan_history import DeviceScanHistory


pytestmark = pytest.mark.integration


def test_availability_details_24h_uses_interval_majority_instead_of_raw_scan_counts(admin_client):
    from routes import dashboard as dashboard_routes

    device_a = Device(device_name='Interval Majority Device', device_type='switch', device_ip='10.50.0.10')
    device_b = Device(device_name='Always Down Device', device_type='switch', device_ip='10.50.0.11')
    db.session.add_all([device_a, device_b])
    db.session.flush()

    now = datetime.utcnow()
    bucket_anchor = dashboard_routes._floor_utc_bucket_start(now, 2)
    bucket_start = bucket_anchor - timedelta(hours=22)

    db.session.add_all([
        DeviceScanHistory(
            device_ip=device_a.device_ip,
            device_name=device_a.device_name,
            status='Online',
            scan_timestamp=bucket_start + timedelta(hours=20, minutes=10),
        ),
        DeviceScanHistory(
            device_ip=device_a.device_ip,
            device_name=device_a.device_name,
            status='Offline',
            scan_timestamp=bucket_start + timedelta(hours=20, minutes=45),
        ),
        DeviceScanHistory(
            device_ip=device_a.device_ip,
            device_name=device_a.device_name,
            status='Offline',
            scan_timestamp=bucket_start + timedelta(hours=22, minutes=10),
        ),
        DeviceScanHistory(
            device_ip=device_a.device_ip,
            device_name=device_a.device_name,
            status='Offline',
            scan_timestamp=bucket_start + timedelta(hours=22, minutes=40),
        ),
        DeviceScanHistory(
            device_ip=device_b.device_ip,
            device_name=device_b.device_name,
            status='Offline',
            scan_timestamp=bucket_start + timedelta(hours=22, minutes=20),
        ),
    ])
    db.session.commit()

    dashboard_routes._cache.clear()
    dashboard_routes._cache_ttl.clear()

    response = admin_client.get('/api/dashboard/availability-details?range=24h&fresh=1')

    assert response.status_code == 200
    payload = response.get_json()

    assert payload['meta']['range'] == '24h'
    assert payload['meta']['bucket_hours'] == 2
    assert payload['meta']['bucket_count'] == 12
    assert len(payload['heatmap']) == 12

    device_a_row = next(row for row in payload['downtime_contributors'] if row['ip'] == device_a.device_ip)
    assert device_a_row['observed_intervals'] == 2
    assert device_a_row['down_intervals'] == 1
    assert device_a_row['uptime_pct'] == 50.0


def test_availability_details_30d_exposes_daily_bucket_metadata(admin_client):
    from routes import dashboard as dashboard_routes

    device = Device(device_name='Thirty Day Device', device_type='switch', device_ip='10.50.0.30')
    db.session.add(device)
    db.session.flush()

    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    db.session.add_all([
        DeviceScanHistory(
            device_ip=device.device_ip,
            device_name=device.device_name,
            status='Online',
            scan_timestamp=now - timedelta(days=3),
        ),
        DeviceScanHistory(
            device_ip=device.device_ip,
            device_name=device.device_name,
            status='Offline',
            scan_timestamp=now - timedelta(days=1),
        ),
    ])
    db.session.commit()

    dashboard_routes._cache.clear()
    dashboard_routes._cache_ttl.clear()

    response = admin_client.get('/api/dashboard/availability-details?range=30d&fresh=1')

    assert response.status_code == 200
    payload = response.get_json()

    assert payload['meta']['range'] == '30d'
    assert payload['meta']['bucket_hours'] == 24
    assert payload['meta']['bucket_count'] == 30
    assert len(payload['heatmap']) == 30
