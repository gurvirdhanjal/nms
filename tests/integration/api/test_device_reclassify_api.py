import types

import pytest

from extensions import db
from models.department import Department
from models.device import Device
import routes.devices as devices_routes


pytestmark = pytest.mark.integration


def test_reclassify_all_accepts_four_value_ping_result(admin_client, monkeypatch):
    department = Department.query.filter_by(name="Alpha Department").first()
    device = Device(
        device_name="Needs-Classify",
        device_type="Unknown",
        device_ip="172.16.2.65",
        site_id=department.site_id,
        department_id=department.id,
        classification_confidence="Low",
    )
    db.session.add(device)
    db.session.commit()

    class FakeScanner:
        async def ping_device(self, ip, timeout=2, count=4):
            return "Online", 2.5, 0.0, 0.3

        def get_mac_address(self, ip):
            return "AA:BB:CC:DD:EE:65"

        def get_hostname(self, ip):
            return "printer-ops-01"

        async def get_manufacturer(self, mac):
            return "HP"

        async def scan_ports(self, ip):
            return [{"port": 9100}]

    monkeypatch.setattr(
        devices_routes,
        "get_discovery_service",
        lambda: types.SimpleNamespace(scanner=FakeScanner()),
    )

    response = admin_client.get("/api/devices/reclassify_all?force=true")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["updated_count"] == 1

    db.session.refresh(device)
    assert device.device_type == "printer"
    assert device.hostname == "printer-ops-01"


def test_reclassify_all_skips_devices_deleted_mid_run(admin_client, monkeypatch):
    department = Department.query.filter_by(name="Alpha Department").first()
    keep_device = Device(
        device_name="Keep-Me",
        device_type="Unknown",
        device_ip="172.16.2.66",
        site_id=department.site_id,
        department_id=department.id,
        classification_confidence="Low",
    )
    delete_device = Device(
        device_name="Delete-Me",
        device_type="Unknown",
        device_ip="172.16.2.67",
        site_id=department.site_id,
        department_id=department.id,
        classification_confidence="Low",
    )
    db.session.add_all([keep_device, delete_device])
    db.session.commit()

    calls = {"count": 0}

    class FakeScanner:
        async def ping_device(self, ip, timeout=2, count=4):
            return "Online", 2.5, 0.0, 0.3

        def get_mac_address(self, ip):
            return "AA:BB:CC:DD:EE:66"

        def get_hostname(self, ip):
            calls["count"] += 1
            if calls["count"] == 1:
                doomed = db.session.get(Device, delete_device.device_id)
                if doomed is not None:
                    db.session.delete(doomed)
                    db.session.commit()
            return "printer-ops-02"

        async def get_manufacturer(self, mac):
            return "HP"

        async def scan_ports(self, ip):
            return [{"port": 9100}]

    monkeypatch.setattr(
        devices_routes,
        "get_discovery_service",
        lambda: types.SimpleNamespace(scanner=FakeScanner()),
    )

    response = admin_client.get("/api/devices/reclassify_all?force=true")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["updated_count"] == 1

    db.session.expire_all()
    refreshed_keep_device = db.session.get(Device, keep_device.device_id)
    refreshed_deleted_device = db.session.get(Device, delete_device.device_id)
    assert refreshed_keep_device.device_type == "printer"
    assert refreshed_deleted_device is None
