import re
from datetime import datetime

import pytest

from extensions import db
from models.department import Department
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog
from models.site import Site


pytestmark = pytest.mark.integration


def _row_has_status(html, device, expected_status):
    pattern = (
        rf'<tr data-device-id="{device.device_id}".*?'
        rf'data-device-status="{expected_status}">{expected_status.title()}</span>.*?'
        rf'<strong>{re.escape(device.device_name)}</strong>'
    )
    return re.search(pattern, html, re.S) is not None


def test_site_dashboard_uses_dashboard_availability_source_of_truth(admin_client):
    site = Site.query.filter_by(site_name="Alpha Site").first()
    department = Department.query.filter_by(name="Alpha Department").first()

    recent_health_but_offline = Device(
        device_name="Recent Health But Offline",
        device_type="workstation",
        device_ip="10.50.0.10",
        site_id=site.id,
        department_id=department.id,
    )
    no_health_but_online = Device(
        device_name="No Health But Online",
        device_type="workstation",
        device_ip="10.50.0.11",
        site_id=site.id,
        department_id=department.id,
    )
    degraded_online = Device(
        device_name="Degraded But Online",
        device_type="workstation",
        device_ip="10.50.0.12",
        site_id=site.id,
        department_id=department.id,
    )
    no_scan = Device(
        device_name="No Scan Device",
        device_type="workstation",
        device_ip="10.50.0.13",
        site_id=site.id,
        department_id=department.id,
    )
    db.session.add_all([recent_health_but_offline, no_health_but_online, degraded_online, no_scan])
    db.session.flush()

    db.session.add(
        ServerHealthLog(
            device_id=recent_health_but_offline.device_id,
            source="agent",
            timestamp=datetime.utcnow(),
            cpu_usage=20,
        )
    )
    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=recent_health_but_offline.device_ip,
                device_name=recent_health_but_offline.device_name,
                status="Offline",
                ping_time_ms=None,
                packet_loss=100,
                scan_timestamp=datetime.utcnow(),
            ),
            DeviceScanHistory(
                device_ip=no_health_but_online.device_ip,
                device_name=no_health_but_online.device_name,
                status="Online",
                ping_time_ms=25,
                packet_loss=0,
                scan_timestamp=datetime.utcnow(),
            ),
            DeviceScanHistory(
                device_ip=degraded_online.device_ip,
                device_name=degraded_online.device_name,
                status="Online",
                ping_time_ms=275,
                packet_loss=0,
                scan_timestamp=datetime.utcnow(),
            ),
        ]
    )
    db.session.commit()

    response = admin_client.get(f"/sites/{site.id}/dashboard")

    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'id="siteKpiTotal">4<' in html
    assert 'id="siteKpiOnline">2<' in html
    assert 'id="siteKpiOffline">2<' in html
    assert re.search(
        r'data-department-name="Alpha Department".*?<td>4</td>\s*<td>2</td>\s*<td>2</td>\s*<td>0</td>',
        html,
        re.S,
    )

    assert _row_has_status(html, recent_health_but_offline, "offline")
    assert _row_has_status(html, no_health_but_online, "online")
    assert _row_has_status(html, degraded_online, "online")
    assert _row_has_status(html, no_scan, "offline")
