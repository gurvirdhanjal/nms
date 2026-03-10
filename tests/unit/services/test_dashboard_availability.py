from extensions import db
from models.device import Device
from models.scan_history import DeviceScanHistory
from services.dashboard_availability import build_device_availability_snapshot


def test_build_device_availability_snapshot_uses_latest_scan_and_classifies_states():
    healthy = Device(device_name="Healthy", device_type="switch", device_ip="10.30.0.10")
    degraded = Device(device_name="Degraded", device_type="switch", device_ip="10.30.0.11")
    offline = Device(device_name="Offline", device_type="switch", device_ip="10.30.0.12")
    missing = Device(device_name="Missing", device_type="switch", device_ip="10.30.0.13")
    latest_wins = Device(device_name="Latest Wins", device_type="switch", device_ip="10.30.0.14")
    unknown_scan = Device(device_name="Unknown Scan", device_type="switch", device_ip="10.30.0.15")
    db.session.add_all([healthy, degraded, offline, missing, latest_wins, unknown_scan])
    db.session.flush()

    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=healthy.device_ip,
                device_name=healthy.device_name,
                status="Online",
                ping_time_ms=45,
                packet_loss=0,
            ),
            DeviceScanHistory(
                device_ip=degraded.device_ip,
                device_name=degraded.device_name,
                status="Online",
                ping_time_ms=250,
                packet_loss=1,
            ),
            DeviceScanHistory(
                device_ip=offline.device_ip,
                device_name=offline.device_name,
                status="Offline",
                ping_time_ms=None,
                packet_loss=100,
            ),
            DeviceScanHistory(
                device_ip=latest_wins.device_ip,
                device_name=latest_wins.device_name,
                status="Online",
                ping_time_ms=10,
                packet_loss=0,
            ),
            DeviceScanHistory(
                device_ip=latest_wins.device_ip,
                device_name=latest_wins.device_name,
                status="Offline",
                ping_time_ms=None,
                packet_loss=100,
            ),
            DeviceScanHistory(
                device_ip=unknown_scan.device_ip,
                device_name=unknown_scan.device_name,
                status="Unknown",
                ping_time_ms=None,
                packet_loss=None,
            ),
        ]
    )
    db.session.commit()

    snapshot = build_device_availability_snapshot(
        [healthy, degraded, offline, missing, latest_wins, unknown_scan]
    )

    assert snapshot["device_states"][healthy.device_id] == "healthy"
    assert snapshot["device_states"][degraded.device_id] == "degraded"
    assert snapshot["device_states"][offline.device_id] == "offline"
    assert snapshot["device_states"][missing.device_id] == "unknown"
    assert snapshot["device_states"][latest_wins.device_id] == "offline"
    assert snapshot["device_states"][unknown_scan.device_id] == "offline"

    assert snapshot["online_device_ids"] == {healthy.device_id, degraded.device_id}
    assert snapshot["counts"] == {
        "total": 6,
        "healthy": 1,
        "degraded": 1,
        "online_total": 2,
        "offline": 3,
        "unknown": 1,
    }
    assert snapshot["network_health"]["avg_latency_ms"] == 147.5
    assert snapshot["network_health"]["avg_packet_loss_pct"] == 0.5
