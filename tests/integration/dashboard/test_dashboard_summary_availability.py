from datetime import datetime

import pytest

from extensions import db
from models.device import Device
from models.scan_history import DeviceScanHistory


pytestmark = pytest.mark.integration


def test_dashboard_summary_preserves_online_total_after_shared_availability_refactor(admin_client):
    devices = [
        Device(device_name="Healthy Summary", device_type="switch", device_ip="10.40.0.10"),
        Device(device_name="Degraded Summary", device_type="switch", device_ip="10.40.0.11"),
        Device(device_name="Offline Summary", device_type="switch", device_ip="10.40.0.12"),
        Device(device_name="Unknown Summary", device_type="switch", device_ip="10.40.0.13"),
    ]
    db.session.add_all(devices)
    db.session.flush()

    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=devices[0].device_ip,
                device_name=devices[0].device_name,
                status="Online",
                ping_time_ms=30,
                packet_loss=0,
                scan_timestamp=datetime.utcnow(),
            ),
            DeviceScanHistory(
                device_ip=devices[1].device_ip,
                device_name=devices[1].device_name,
                status="Online",
                ping_time_ms=260,
                packet_loss=0,
                scan_timestamp=datetime.utcnow(),
            ),
            DeviceScanHistory(
                device_ip=devices[2].device_ip,
                device_name=devices[2].device_name,
                status="Offline",
                ping_time_ms=None,
                packet_loss=100,
                scan_timestamp=datetime.utcnow(),
            ),
        ]
    )
    db.session.commit()

    from routes import dashboard as dashboard_routes

    dashboard_routes._cache.clear()
    dashboard_routes._cache_ttl.clear()

    response = admin_client.get("/api/dashboard/summary")

    assert response.status_code == 200
    payload = response.get_json()

    assert payload["counts"]["total_inventory"] == 4
    assert payload["counts"]["up"] == 1
    assert payload["counts"]["degraded"] == 1
    assert payload["counts"]["down"] == 1
    assert payload["counts"]["online_total"] == 2
    assert payload["devices"]["online"] == 2
    assert payload["devices"]["unknown"] == 1
    assert payload["devices"]["online"] == payload["counts"]["up"] + payload["counts"]["degraded"]
