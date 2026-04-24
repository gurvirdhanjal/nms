import time

import pytest

from extensions import db
from models.department import Department
from models.device import Device


pytestmark = pytest.mark.integration


def test_bulk_delete_devices_deletes_in_batches(admin_client):
    department = Department.query.filter_by(name="Alpha Department").first()
    devices = [
        Device(
            device_name=f"BulkDelete-{idx}",
            device_type="Switch",
            device_ip=f"10.91.0.{idx}",
            site_id=department.site_id,
            department_id=department.id,
        )
        for idx in range(1, 4)
    ]
    db.session.add_all(devices)
    db.session.commit()

    device_ids = [device.device_id for device in devices]

    response = admin_client.post(
        "/api/devices/bulk_delete",
        json={"device_ids": device_ids, "batch_size": 2},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["queued"] is True
    assert payload["eligible"] == 3
    assert payload["batch_size"] == 2
    assert payload["job_id"]

    status_payload = None
    for _ in range(20):
        status_response = admin_client.get(f"/api/devices/bulk_delete/{payload['job_id']}")
        assert status_response.status_code == 200
        status_payload = status_response.get_json()
        if status_payload["status"] == "completed":
            break
        time.sleep(0.1)

    assert status_payload is not None
    assert status_payload["status"] == "completed"
    assert status_payload["deleted"] == 3

    remaining = Device.query.filter(Device.device_id.in_(device_ids)).count()
    assert remaining == 0
