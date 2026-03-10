from uuid import uuid4

from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from services.device_identity import upsert_device_from_identity


def test_upsert_device_from_identity_preserves_canonical_device_on_ip_move():
    canonical = Device(
        device_name="Printer-A",
        device_type="printer",
        device_ip="10.10.1.10",
        macaddress="aa:bb:cc:dd:ee:ff",
        hostname="printer-a",
        manufacturer="HP",
        is_monitored=True,
    )
    db.session.add(canonical)
    db.session.flush()

    dashboard_event = DashboardEvent(
        event_id=str(uuid4()),
        device_id=canonical.device_id,
        device_ip=canonical.device_ip,
        event_type="STATUS_CHANGE",
        severity="WARNING",
        message="Offline",
    )
    db.session.add(dashboard_event)
    db.session.flush()

    placeholder = Device(
        device_name="Device-10.10.2.55",
        device_type="unknown",
        device_ip="10.10.2.55",
        macaddress="N/A",
        hostname="Unknown",
        manufacturer="Unknown",
        is_monitored=False,
    )
    db.session.add(placeholder)
    db.session.commit()

    device, action, previous_ip = upsert_device_from_identity(
        ip="10.10.2.55",
        mac="AA-BB-CC-DD-EE-FF",
        hostname="printer-a",
        manufacturer="HP",
        device_type="printer",
        is_monitored=True,
        is_active=True,
    )
    db.session.commit()

    assert action == "updated"
    assert previous_ip == "10.10.1.10"
    assert device.device_id == canonical.device_id
    assert device.device_ip == "10.10.2.55"
    assert Device.query.count() == 1
    assert db.session.get(Device, placeholder.device_id) is None

    persisted_event = db.session.get(DashboardEvent, dashboard_event.event_id)
    assert persisted_event is not None
    assert persisted_event.device_id == canonical.device_id
