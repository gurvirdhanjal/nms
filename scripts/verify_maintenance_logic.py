
import sys
import os
import unittest
from datetime import datetime

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models.device import Device
from services.alert_manager import AlertManager
from models.dashboard import DashboardEvent

class TestMaintenanceAndAlerts(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Use in-memory DB for testing logic if possible, but app uses file. 
        # We will create temporary test devices in the real DB and clean them up.
        self.client = self.app.test_client()

    def tearDown(self):
        # Cleanup
        db.session.rollback()
        Device.query.filter(Device.device_name.like('TEST_DEVICE_%')).delete()
        DashboardEvent.query.filter(DashboardEvent.message.like('%TEST_DEVICE_%')).delete()
        db.session.commit()
        self.app_context.pop()

    def test_maintenance_mode_skips_alerts(self):
        """Test that maintenance mode prevents alerts"""
        print("\n[TEST] Maintenance Mode Alert Suppression")
        device = Device(
            device_name='TEST_DEVICE_MAINT',
            device_ip='10.10.10.254',
            device_type='Server',
            is_monitored=True,
            maintenance_mode=True
        )
        db.session.add(device)
        db.session.commit()

        # Simulate Offline Scan
        AlertManager.process_scan_result(device, is_online=False, latency_ms=None, packet_loss_pct=100)
        
        # Verify strikes didn't increase
        self.assertEqual(device.offline_strikes, 0, "Strikes should not increase in maintenance mode")
        
        print("PASS: Maintenance mode suppressed alert/strikes")

    def test_3_strike_rule_server(self):
        """Test 3-strike rule for servers"""
        print("\n[TEST] 3-Strike Rule for Servers")
        device = Device(
            device_name='TEST_DEVICE_SERVER',
            device_ip='10.10.10.253',
            device_type='Server',
            is_monitored=True,
            maintenance_mode=False,
            offline_strikes=0
        )
        db.session.add(device)
        db.session.commit()

        # Strike 1
        AlertManager.process_scan_result(device, is_online=False, latency_ms=None, packet_loss_pct=1000)
        print(f"Strike 1: Current strikes = {device.offline_strikes}")
        self.assertEqual(device.offline_strikes, 1)
        
        # Check NO alert
        alert = DashboardEvent.query.filter_by(device_id=device.device_id, resolved=False).first()
        self.assertIsNone(alert, "Should not alert on 1st strike")

        # Strike 2
        AlertManager.process_scan_result(device, is_online=False, latency_ms=None, packet_loss_pct=100)
        print(f"Strike 2: Current strikes = {device.offline_strikes}")
        self.assertEqual(device.offline_strikes, 2)
        
        # Check NO alert
        alert = DashboardEvent.query.filter_by(device_id=device.device_id, resolved=False).first()
        self.assertIsNone(alert, "Should not alert on 2nd strike")

        # Strike 3
        AlertManager.process_scan_result(device, is_online=False, latency_ms=None, packet_loss_pct=100)
        print(f"Strike 3: Current strikes = {device.offline_strikes}")
        self.assertEqual(device.offline_strikes, 3)

        # Check YES alert
        alert = DashboardEvent.query.filter_by(device_id=device.device_id, resolved=False).first()
        self.assertIsNotNone(alert, "Should alert on 3rd strike")
        self.assertEqual(alert.severity, 'CRITICAL')
        print("PASS: Alert triggered on 3rd strike")

        # Recovery
        AlertManager.process_scan_result(device, is_online=True, latency_ms=10, packet_loss_pct=0)
        self.assertEqual(device.offline_strikes, 0, "Strikes should reset on recovery")
        
        # Check Alert Resolved
        alert = DashboardEvent.query.filter_by(device_id=device.device_id, resolved=False).first()
        self.assertIsNone(alert, "Alert should be resolved")
        print("PASS: Recovery reset strikes and resolved alert")

    def test_non_server_logic(self):
        """Test non-server logic (no alerts)"""
        print("\n[TEST] Non-Server Logic")
        device = Device(
            device_name='TEST_DEVICE_SWITCH',
            device_ip='10.10.10.252',
            device_type='Switch',
            is_monitored=True
        )
        db.session.add(device)
        db.session.commit()

        # Simulate Offline
        AlertManager.process_scan_result(device, is_online=False, latency_ms=None, packet_loss_pct=100)
        
        # Should NOT increment strikes (logic mainly for servers) OR increment but logic prevents alert?
        # My implementation: "should_monitor = device.is_monitored and is_server". 
        # So it skips the block.
        
        self.assertEqual(getattr(device, 'offline_strikes', 0), 0)
        
        alert = DashboardEvent.query.filter_by(device_id=device.device_id, resolved=False).first()
        self.assertIsNone(alert, "Non-server should not alert")
        print("PASS: Non-server ignored")

if __name__ == '__main__':
    unittest.main()
