
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models.device import Device
from services.alert_manager import AlertManager
from models.dashboard import DashboardEvent

def run_test():
    try:
        app = create_app()
        with app.app_context():
            print("--- SETUP ---")
            db.create_all() # Ensure tables exist
            # Cleanup first
            try:
                Device.query.filter(Device.device_name.like('TEST_%')).delete()
                DashboardEvent.query.filter(DashboardEvent.message.like('%TEST_%')).delete()
                db.session.commit()
            except Exception as e:
                print(f"Cleanup warning: {e}")
                db.session.rollback()

            # 1. Maintenance Mode Test
            print("\n--- TEST 1: Maintenance Mode ---")
            d1 = Device(
                device_name='TEST_MAINT',
                device_ip='10.10.10.201',
                device_type='Server',
                is_monitored=True,
                maintenance_mode=True,
                offline_strikes=0
            )
            db.session.add(d1)
            db.session.commit()
            
            print(f"Created device {d1.device_name} (Maint={d1.maintenance_mode})")
            
            AlertManager.process_scan_result(d1, is_online=False, latency_ms=None, packet_loss_pct=100)
            db.session.refresh(d1)
            
            print(f"Strikes after offline scan: {d1.offline_strikes}")
            if d1.offline_strikes == 0:
                print("PASS: Maintenance mode blocked strikes.")
            else:
                print("FAIL: Strikes increased!")

            # 2. 3-Strike Rule Test
            print("\n--- TEST 2: 3-Strike Rule ---")
            d2 = Device(
                device_name='TEST_SERVER',
                device_ip='10.10.10.202',
                device_type='Server',
                is_monitored=True,
                maintenance_mode=False,
                offline_strikes=0
            )
            db.session.add(d2)
            db.session.commit()
            
            # Strike 1
            AlertManager.process_scan_result(d2, is_online=False, latency_ms=None, packet_loss_pct=100, commit=True)
            db.session.refresh(d2)
            print(f"Strike 1: {d2.offline_strikes}")
            
            # Strike 2
            AlertManager.process_scan_result(d2, is_online=False, latency_ms=None, packet_loss_pct=100, commit=True)
            db.session.refresh(d2)
            print(f"Strike 2: {d2.offline_strikes}")
            
            # Strike 3
            AlertManager.process_scan_result(d2, is_online=False, latency_ms=None, packet_loss_pct=100, commit=True)
            db.session.refresh(d2)
            print(f"Strike 3: {d2.offline_strikes}")
            
            # Check Alert
            alert = DashboardEvent.query.filter_by(device_id=d2.device_id, resolved=False).first()
            if alert:
                print(f"PASS: Alert triggered: {alert.message}")
            else:
                print("FAIL: No alert triggered!")

            print("\n--- TEARDOWN ---")
            Device.query.filter(Device.device_name.like('TEST_%')).delete()
            DashboardEvent.query.filter(DashboardEvent.message.like('%TEST_%')).delete()
            db.session.commit()
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"CRITICAL ERROR: {e}")

if __name__ == '__main__':
    run_test()
