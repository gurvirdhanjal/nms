import sys
import os
sys.path.append(os.getcwd())
from app import create_app
from models.scan_history import DeviceScanHistory

app = create_app()
with app.app_context():
    print("SCAN_HISTORY_START")
    scans = DeviceScanHistory.query.order_by(DeviceScanHistory.scan_timestamp.desc()).limit(20).all()
    for s in scans:
        print(f"IP:{s.device_ip}|Status:{s.status}|Latency:{s.ping_time_ms}|Loss:{s.packet_loss}|Jitter:{s.jitter}")
    print("SCAN_HISTORY_END")
