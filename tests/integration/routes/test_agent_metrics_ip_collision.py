import pytest

from extensions import db
from models.device import Device
from models.server_health import ServerHealthLog
import routes.agent as agent_routes
from services.alert_manager import AlertManager


pytestmark = pytest.mark.integration


def test_agent_metrics_skips_ip_reassignment_when_target_ip_is_owned(client, monkeypatch):
    reporting_device = Device(
        device_name='Agent Device',
        device_type='server',
        device_ip='172.16.2.10',
        hostname='apldeveloper',
        agent_token='agent-token-1',
        monitoring_mode='agent',
    )
    collision_device = Device(
        device_name='Existing Device',
        device_type='server',
        device_ip='172.16.2.74',
        hostname='existing-host',
        agent_token='agent-token-2',
        monitoring_mode='agent',
    )
    db.session.add_all([reporting_device, collision_device])
    db.session.commit()

    client.application.config['REQUIRE_POSTGRES_ONLY'] = False
    monkeypatch.setattr(AlertManager, 'check_server_health', lambda device, log, commit=True: None)

    response = client.post(
        '/api/agent/metrics',
        headers={'X-Agent-Token': 'agent-token-1'},
        json={
            'hostname': 'apldeveloper',
            'ip_address': '172.16.2.74',
            'cpu': {'cpu_percent': 17.5},
            'memory': {'percent': 42.0},
            'disk': {'percent': 63.0},
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {'success': True}

    db.session.expire_all()
    refreshed_reporting_device = db.session.get(Device, reporting_device.device_id)
    refreshed_collision_device = db.session.get(Device, collision_device.device_id)

    assert refreshed_reporting_device.device_ip == '172.16.2.10'
    assert refreshed_collision_device.device_ip == '172.16.2.74'

    saved_log = ServerHealthLog.query.filter_by(device_id=reporting_device.device_id).one()
    assert saved_log.source == 'agent'
    assert saved_log.cpu_usage == 17.5


def test_agent_metrics_returns_accepted_when_device_row_is_locked(client, monkeypatch):
    reporting_device = Device(
        device_name='Agent Device',
        device_type='server',
        device_ip='172.16.2.10',
        hostname='apldeveloper',
        agent_token='agent-token-3',
        monitoring_mode='agent',
    )
    db.session.add(reporting_device)
    db.session.commit()

    client.application.config['REQUIRE_POSTGRES_ONLY'] = False
    monkeypatch.setattr(AlertManager, 'check_server_health', lambda device, log, commit=True: None)

    class FakePgError(Exception):
        pgcode = '55P03'

    operational_error = agent_routes.OperationalError(
        "INSERT INTO server_health_logs ...",
        {},
        FakePgError("canceling statement due to lock timeout"),
    )

    monkeypatch.setattr(agent_routes.db.session, 'commit', lambda: (_ for _ in ()).throw(operational_error))

    response = client.post(
        '/api/agent/metrics',
        headers={'X-Agent-Token': 'agent-token-3'},
        json={
            'hostname': 'apldeveloper',
            'cpu': {'cpu_percent': 17.5},
            'memory': {'percent': 42.0},
            'disk': {'percent': 63.0},
        },
    )

    assert response.status_code == 202
    assert response.get_json() == {'success': False, 'skipped': 'device_locked'}
