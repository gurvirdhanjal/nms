from datetime import datetime

import pytest

from extensions import db
from models.device import Device
from models.department import Department
from models.site import Site
from models.server_health import ServerHealthLog


pytestmark = pytest.mark.integration


def _seed_server_scope_split():
    site_alpha = Site.query.filter_by(site_name='Alpha Site').first()
    site_beta = Site.query.filter_by(site_name='Beta Site').first()
    dept_alpha = Department.query.filter_by(name='Alpha Department').first()
    dept_beta = Department.query.filter_by(name='Beta Department').first()

    server_alpha = Device(
        device_name='Server Alpha',
        device_type='server',
        device_ip='10.0.1.50',
        site_id=site_alpha.id,
        department_id=dept_alpha.id,
    )
    server_beta = Device(
        device_name='Server Beta',
        device_type='server',
        device_ip='10.0.2.60',
        site_id=site_beta.id,
        department_id=dept_beta.id,
    )
    db.session.add_all([server_alpha, server_beta])
    db.session.flush()

    db.session.add_all(
        [
            ServerHealthLog(
                device_id=server_alpha.device_id,
                source='agent',
                cpu_usage=42,
                memory_usage=55,
                disk_usage=31,
                timestamp=datetime.utcnow(),
            ),
            ServerHealthLog(
                device_id=server_beta.device_id,
                source='agent',
                cpu_usage=84,
                memory_usage=86,
                disk_usage=73,
                timestamp=datetime.utcnow(),
            ),
        ]
    )
    db.session.commit()
    return server_alpha, server_beta


def test_server_metrics_endpoints_are_scope_filtered_for_manager(manager_client):
    server_alpha, server_beta = _seed_server_scope_split()

    fleet_response = manager_client.get('/api/server/fleet-metrics')
    assert fleet_response.status_code == 200
    fleet_payload = fleet_response.get_json()
    assert fleet_payload['health']['total'] == 1

    health_response = manager_client.get('/api/server/health')
    assert health_response.status_code == 200
    health_payload = health_response.get_json()
    assert health_payload['counts']['total'] == 1
    assert all(server.get('device_id') == server_alpha.device_id for server in health_payload.get('servers', []))

    scoped_metrics_response = manager_client.get(f'/api/server/{server_alpha.device_id}/metrics')
    assert scoped_metrics_response.status_code == 200
    assert scoped_metrics_response.get_json()['device_name'] == 'Server Alpha'

    out_of_scope_metrics_response = manager_client.get(f'/api/server/{server_beta.device_id}/metrics')
    assert out_of_scope_metrics_response.status_code == 404

    scoped_telemetry_response = manager_client.get(f'/api/devices/{server_alpha.device_id}/telemetry')
    assert scoped_telemetry_response.status_code == 200
    assert scoped_telemetry_response.get_json()['device_name'] == 'Server Alpha'

    out_of_scope_telemetry_response = manager_client.get(f'/api/devices/{server_beta.device_id}/telemetry')
    assert out_of_scope_telemetry_response.status_code == 404


def test_server_telemetry_endpoint_returns_composite_health_and_connection_resolution(admin_client):
    server = Device(
        device_name='Server Gamma',
        device_type='server',
        device_ip='10.0.5.10',
        hostname='server-gamma',
    )
    peer = Device(
        device_name='Database Peer',
        device_type='server',
        device_ip='10.0.5.20',
        hostname='db-peer',
    )
    db.session.add_all([server, peer])
    db.session.flush()

    db.session.add(
        ServerHealthLog(
            device_id=server.device_id,
            source='agent',
            cpu_usage=92,
            memory_usage=88,
            disk_usage=40,
            network_connections_total=156,
            network_connections_established=120,
            network_connections_unique_ips=1,
            network_top_remote_ips=[
                {
                    'ip': '10.0.5.20',
                    'count': 120,
                    'connection_type': 'HTTPS',
                    'hostname': 'db-peer',
                }
            ],
            top_processes=[
                {
                    'name': 'sqlservr.exe',
                    'pid': 4321,
                    'memory_percent': 47.5,
                    'status': 'running',
                    'path': 'C:\\Program Files\\SQL\\sqlservr.exe',
                }
            ],
            top_processes_cpu=[
                {
                    'name': 'sqlservr.exe',
                    'pid': 4321,
                    'cpu_percent': 61.0,
                    'status': 'running',
                }
            ],
            os_name='Windows Server 2022',
            os_version='22H2',
            os_arch='x64',
            uptime='7200',
            swap_total_mb=4096,
            swap_used_mb=2048,
            swap_percent=50,
            timestamp=datetime.utcnow(),
        )
    )
    db.session.commit()

    response = admin_client.get(f'/api/devices/{server.device_id}/telemetry?range=24h')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['device_name'] == 'Server Gamma'
    assert payload['health'] == 'Critical'
    assert payload['health_score'] == 55
    assert payload['memory_paging_label'] == 'Pagefile Usage'
    assert payload['boot_time'] is not None
    assert payload['telemetry_refreshed_at'] is not None
    assert payload['health_penalties']

    snapshot_rows = payload['connection_snapshot']['rows']
    assert snapshot_rows
    assert snapshot_rows[0]['remote_ip'] == '10.0.5.20'
    assert snapshot_rows[0]['connection_type'] == 'HTTPS'
    assert snapshot_rows[0]['remote_device_name'] == 'Database Peer'
    assert snapshot_rows[0]['resolution_source'] == 'inventory'

    assert payload['process_catalog']
    assert payload['process_catalog'][0]['name'] == 'sqlservr.exe'
    assert payload['process_catalog'][0]['path'].endswith('sqlservr.exe')
