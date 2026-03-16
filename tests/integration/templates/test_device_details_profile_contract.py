from datetime import datetime

import pytest

from extensions import db
from models.device import Device
from models.interfaces import DeviceInterface
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.snmp_config import DeviceSnmpConfig


pytestmark = pytest.mark.integration


def test_device_details_page_is_inventory_first_without_snmp(admin_client):
    device = Device(
        device_name='App Server 01',
        device_type='server',
        device_ip='10.20.30.40',
        monitoring_mode='agent',
        site_id=1,
        department_id=1,
        is_monitored=True,
    )
    db.session.add(device)
    db.session.flush()

    db.session.add(
        DeviceScanHistory(
            device_ip=device.device_ip,
            device_name=device.device_name,
            status='online',
            ping_time_ms=8.4,
        )
    )
    db.session.add(
        ServerHealthLog(
            device_id=device.device_id,
            source='agent',
            cpu_usage=42.0,
            memory_usage=57.5,
            disk_usage=68.2,
            timestamp=datetime.utcnow(),
        )
    )
    db.session.commit()

    response = admin_client.get(f'/devices/{device.device_id}/details')
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert 'Inventory Profile' in html
    assert 'Monitoring Data Sources' in html
    assert 'Open Server Monitoring' in html
    assert 'Uses the newest telemetry source available, not SNMP by default' in html
    assert 'Supplementary SNMP Data' not in html
    assert 'Poll SNMP' not in html
    assert 'Refresh Interfaces' not in html


def test_device_details_page_shows_snmp_as_optional_supplement(admin_client):
    device = Device(
        device_name='Switch Edge 01',
        device_type='switch',
        device_ip='10.20.31.10',
        monitoring_mode='ping',
        is_monitored=True,
    )
    db.session.add(device)
    db.session.flush()

    db.session.add(
        DeviceSnmpConfig(
            device_id=device.device_id,
            snmp_version='2c',
            snmp_port=161,
            is_enabled=True,
            last_successful_poll=datetime.utcnow(),
        )
    )
    db.session.add(
        ServerHealthLog(
            device_id=device.device_id,
            source='snmp',
            cpu_usage=11.0,
            memory_usage=26.0,
            disk_usage=5.0,
            timestamp=datetime.utcnow(),
        )
    )
    db.session.add(
        DeviceInterface(
            device_id=device.device_id,
            if_index=1,
            name='Gi0/1',
            canonical_name='Gi0/1',
            oper_status='up',
            speed_bps=1000000000,
            last_poll_time=datetime.utcnow(),
        )
    )
    db.session.commit()

    response = admin_client.get(f'/devices/{device.device_id}/details')
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert 'Inventory Profile' in html
    assert 'Supplementary SNMP Data' in html
    assert 'Shown only when SNMP exists; not required for the profile page' in html
    assert 'Gi0/1' in html
    assert 'Poll SNMP' not in html
