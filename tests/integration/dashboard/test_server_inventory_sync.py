from datetime import datetime

import pytest

from extensions import redis_client
from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.device_identity_link import DeviceIdentityLink
from models.server_health import ServerHealthLog
from models.tracked_device import TrackedDevice
from services.tracked_device_ip_change import apply_tracked_device_ip_change


pytestmark = pytest.mark.integration


def _clear_server_health_cache():
    if redis_client is None:
        return
    try:
        redis_client.delete('server:health:summary')
    except Exception:
        pass


def test_linked_tracked_ip_change_updates_inventory_and_server_endpoints(admin_client):
    server = Device(
        device_name='Server Alpha',
        device_type='server',
        device_ip='10.0.1.10',
        subnet_cidr='10.0.1.0/24',
        macaddress='AA:BB:CC:DD:EE:FF',
        hostname='server-alpha-old',
    )
    tracked = TrackedDevice(
        mac_address='AA:BB:CC:DD:EE:FF',
        device_name='Tracked Server Alpha',
        hostname='server-alpha',
        ip_address='10.0.1.10',
    )
    db.session.add_all([server, tracked])
    db.session.flush()

    db.session.add(
        DeviceIdentityLink(
            device_id=server.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='AA:BB:CC:DD:EE:FF',
            is_active=True,
            link_source='manual',
            confidence=100,
        )
    )
    db.session.add(
        ServerHealthLog(
            device_id=server.device_id,
            source='agent',
            cpu_usage=40,
            memory_usage=50,
            disk_usage=30,
            timestamp=datetime.utcnow(),
        )
    )
    db.session.add(
        DashboardEvent(
            event_id='evt-server-ip-sync',
            device_id=server.device_id,
            device_ip='10.0.1.10',
            event_type='THRESHOLD',
            severity='WARNING',
            metric_name='health_cpu_usage_pct',
            message='CPU warning',
            resolved=False,
        )
    )
    db.session.commit()

    apply_tracked_device_ip_change(
        tracked_device=tracked,
        new_ip='10.0.2.20',
        resolved_hostname='server-alpha-renamed',
        now_utc=datetime.utcnow(),
        payload_ip='10.0.2.20',
        payload_candidates=['10.0.2.20'],
        transport_remote_ip='10.0.2.20',
        transport_forwarded_for=None,
        agent_key_id=None,
        reason='TEST_SYNC',
        ip_source='test',
        network_signature=None,
        update_last_seen=False,
        update_updated_at=True,
        sync_reason='TEST_SYNC',
    )
    db.session.commit()

    db.session.refresh(server)
    assert server.device_ip == '10.0.2.20'
    assert server.subnet_cidr == '10.0.2.0/24'
    assert server.hostname == 'server-alpha-renamed'
    _clear_server_health_cache()

    health_response = admin_client.get('/api/server/health')
    assert health_response.status_code == 200
    server_payload = health_response.get_json()['servers'][0]
    assert server_payload['ip'] == '10.0.2.20'

    metrics_response = admin_client.get(f'/api/server/{server.device_id}/metrics')
    assert metrics_response.status_code == 200
    assert metrics_response.get_json()['ip'] == '10.0.2.20'

    alerts_response = admin_client.get('/api/dashboard/alerts')
    assert alerts_response.status_code == 200
    alert_payload = alerts_response.get_json()[0]
    assert alert_payload['device_ip'] == '10.0.2.20'
    assert alert_payload['original_device_ip'] == '10.0.1.10'


def test_unlinked_tracked_ip_change_does_not_update_inventory(admin_client):
    server = Device(
        device_name='Server Beta',
        device_type='server',
        device_ip='10.0.3.10',
        subnet_cidr='10.0.3.0/24',
        macaddress='11:22:33:44:55:66',
    )
    tracked = TrackedDevice(
        mac_address='11:22:33:44:55:66',
        device_name='Tracked Server Beta',
        hostname='server-beta',
        ip_address='10.0.3.10',
    )
    db.session.add_all([server, tracked])
    db.session.commit()

    apply_tracked_device_ip_change(
        tracked_device=tracked,
        new_ip='10.0.4.20',
        now_utc=datetime.utcnow(),
        payload_ip='10.0.4.20',
        payload_candidates=['10.0.4.20'],
        transport_remote_ip='10.0.4.20',
        transport_forwarded_for=None,
        agent_key_id=None,
        reason='TEST_NO_LINK',
        ip_source='test',
        network_signature=None,
        update_last_seen=False,
        update_updated_at=True,
        sync_reason='TEST_NO_LINK',
    )
    db.session.commit()

    db.session.refresh(server)
    assert server.device_ip == '10.0.3.10'


def test_linked_tracked_ip_change_merges_same_mac_collision_and_updates_hostname(admin_client):
    server = Device(
        device_name='Server Gamma',
        device_type='server',
        device_ip='10.0.5.10',
        subnet_cidr='10.0.5.0/24',
        macaddress='22:33:44:55:66:77',
        hostname='server-gamma-old',
    )
    collision = Device(
        device_name='Device-10.0.6.20',
        device_type='server',
        device_ip='10.0.6.20',
        subnet_cidr='10.0.6.0/24',
        macaddress='22:33:44:55:66:77',
        hostname='server-gamma-stale',
    )
    tracked = TrackedDevice(
        mac_address='22:33:44:55:66:77',
        device_name='Tracked Server Gamma',
        hostname='server-gamma',
        ip_address='10.0.5.10',
    )
    db.session.add_all([server, collision, tracked])
    db.session.flush()

    db.session.add(
        DeviceIdentityLink(
            device_id=server.device_id,
            tracked_device_id=tracked.id,
            normalized_mac='22:33:44:55:66:77',
            is_active=True,
            link_source='manual',
            confidence=100,
        )
    )
    db.session.add(
        ServerHealthLog(
            device_id=collision.device_id,
            source='agent',
            cpu_usage=25,
            memory_usage=30,
            disk_usage=20,
            timestamp=datetime.utcnow(),
        )
    )
    db.session.commit()

    collision_id = collision.device_id

    apply_tracked_device_ip_change(
        tracked_device=tracked,
        new_ip='10.0.6.20',
        resolved_hostname='server-gamma',
        now_utc=datetime.utcnow(),
        payload_ip='10.0.6.20',
        payload_candidates=['10.0.6.20'],
        transport_remote_ip='10.0.6.20',
        transport_forwarded_for=None,
        agent_key_id=None,
        reason='TEST_COLLISION_MERGE',
        ip_source='test',
        network_signature=None,
        update_last_seen=False,
        update_updated_at=True,
        sync_reason='TEST_COLLISION_MERGE',
    )
    db.session.commit()

    db.session.refresh(server)
    assert server.device_ip == '10.0.6.20'
    assert server.subnet_cidr == '10.0.6.0/24'
    assert server.hostname == 'server-gamma'
    assert db.session.get(Device, collision_id) is None
    assert ServerHealthLog.query.filter_by(device_id=server.device_id).count() == 1
