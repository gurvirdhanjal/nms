from datetime import datetime, timedelta

import pytest

from extensions import db
from models.dashboard import DailyDeviceStats
from models.department import Department
from models.device import Device
from models.scan_history import DeviceScanHistory
from models.tracked_device import (
    DeviceActivityLog,
    DeviceApplicationLog,
    DeviceResourceLog,
    TrackedDevice,
    TrackingDailyRollup,
    TrackingHourlyRollup,
    TrackingSample,
)


pytestmark = pytest.mark.integration


def test_backfill_rollups_populates_daily_and_tracking_rollups(admin_client):
    department = Department.query.filter_by(name="Alpha Department").first()
    inventory_device = Device(
        device_name="Backfill-Inventory",
        device_type="Server",
        device_ip="10.90.0.10",
        site_id=department.site_id,
        department_id=department.id,
    )
    tracked_device = TrackedDevice(
        mac_address="AA:BB:CC:DD:EE:F1",
        device_name="Backfill-Tracked",
        employee_name="Report User",
        hostname="backfill-tracked",
        ip_address="10.90.0.11",
        site_id=department.site_id,
        department_id=department.id,
        availability_status="online",
    )
    db.session.add_all([inventory_device, tracked_device])
    db.session.commit()

    yesterday = datetime.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=inventory_device.device_ip,
                device_name=inventory_device.device_name,
                status="Online",
                ping_time_ms=5.0,
                packet_loss=0.0,
                scan_timestamp=yesterday + timedelta(hours=1),
            ),
            DeviceScanHistory(
                device_ip=inventory_device.device_ip,
                device_name=inventory_device.device_name,
                status="Offline",
                ping_time_ms=None,
                packet_loss=100.0,
                scan_timestamp=yesterday + timedelta(hours=2),
            ),
        ]
    )

    first_sample = yesterday + timedelta(hours=3)
    second_sample = yesterday + timedelta(hours=4)
    db.session.add_all(
        [
            TrackingSample(
                device_id=tracked_device.id,
                idempotency_key=f"{tracked_device.id}:sample:1",
                received_at=first_sample,
                sampled_at=first_sample,
                integrity_status="verified",
            ),
            TrackingSample(
                device_id=tracked_device.id,
                idempotency_key=f"{tracked_device.id}:sample:2",
                received_at=second_sample,
                sampled_at=second_sample,
                integrity_status="verified",
            ),
            DeviceResourceLog(
                device_id=tracked_device.id,
                timestamp=first_sample,
                cpu_usage=20.0,
                memory_usage=40.0,
            ),
            DeviceResourceLog(
                device_id=tracked_device.id,
                timestamp=second_sample,
                cpu_usage=30.0,
                memory_usage=50.0,
            ),
            DeviceActivityLog(
                device_id=tracked_device.id,
                timestamp=first_sample,
                activity_type="keyboard",
                event_count=7,
            ),
            DeviceActivityLog(
                device_id=tracked_device.id,
                timestamp=second_sample,
                activity_type="mouse",
                event_count=5,
            ),
            DeviceApplicationLog(
                device_id=tracked_device.id,
                timestamp=first_sample,
                application_name="Microsoft Word",
                duration=600,
                status="active",
            ),
            DeviceApplicationLog(
                device_id=tracked_device.id,
                timestamp=second_sample,
                application_name="Google Chrome",
                duration=300,
                status="active",
            ),
        ]
    )
    db.session.commit()

    response = admin_client.post(
        "/api/maintenance/backfill-rollups",
        json={"days": 7, "rebuild_daily_stats": True},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    assert DailyDeviceStats.query.count() == 1
    assert TrackingHourlyRollup.query.count() == 2
    assert TrackingDailyRollup.query.count() == 1

    daily_stat = DailyDeviceStats.query.first()
    assert daily_stat.total_scans == 2
    assert daily_stat.online_scans == 1

    daily_rollup = TrackingDailyRollup.query.first()
    assert daily_rollup.sample_count == 2
    assert daily_rollup.keyboard_events == 7
    assert daily_rollup.mouse_events == 5
