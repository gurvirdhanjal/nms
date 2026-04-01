import sys
import os
sys.path.append(os.getcwd())
from app import create_app
from models.scan_history import DeviceScanHistory

app = create_app()
with app.app_context():
    print("SPECIFIC_SCAN_START")
    scans = DeviceScanHistory.query.filter_by(device_ip='127.0.0.1').order_by(DeviceScanHistory.scan_timestamp.desc()).all()
    if not scans:
        print("No scans found for 127.0.0.1")
    for s in scans:
        print(f"IP:{s.device_ip}|Status:{s.status}|Latency:{s.ping_time_ms}|Loss:{s.packet_loss}|Timestamp:{s.scan_timestamp}")
    print("SPECIFIC_SCAN_END")
