import os
import sys
import uuid
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from extensions import db


def verify_metrics_retention():
    print("Verifying server metrics retention/rollups...")

    app = create_app({'TESTING': True})
    created_device_id = None
    try:
        with app.app_context():
            backend = db.engine.url.get_backend_name()
            if backend != 'postgresql':
                print(f"SKIPPED: retention verification requires PostgreSQL, got '{backend}'.")
                return 0

            from models.device import Device
            from models.interfaces import DeviceInterface, InterfaceTrafficHistory
            from models.server_health import ServerHealthLog
            from models.server_health_rollups import (
                ServerHealthHourlyRollup,
                ServerHealthDailyRollup,
                ServerHealthRollupState,
            )
            from services.maintenance_service import maintenance_service

            now = datetime.utcnow()
            old_ts_1 = now - timedelta(days=7, hours=6, minutes=10)  # in [now-8d, now-7d)
            old_ts_2 = old_ts_1 + timedelta(minutes=20)
            recent_ts = now - timedelta(minutes=10)

            suffix = uuid.uuid4().hex[:8]
            device = Device(
                device_name=f'Retention Test Device {suffix}',
                device_type='server',
                device_ip=f'10.250.{int(suffix[:2], 16) % 250}.{int(suffix[2:4], 16) % 250}',
                is_monitored=True,
            )
            db.session.add(device)
            db.session.flush()
            created_device_id = device.device_id

            iface = DeviceInterface(
                device_id=device.device_id,
                if_index=9999,
                name='retention-test-if',
                speed_bps=1_000_000_000,
            )
            db.session.add(iface)
            db.session.flush()

            # Seed old (to be rolled) and recent (must remain for live APIs) server logs.
            db.session.add_all([
                ServerHealthLog(
                    device_id=device.device_id,
                    source='agent',
                    cpu_usage=30.0,
                    memory_usage=40.0,
                    disk_usage=50.0,
                    network_in_bps=1_000_000.0,
                    network_out_bps=500_000.0,
                    timestamp=old_ts_1,
                ),
                ServerHealthLog(
                    device_id=device.device_id,
                    source='agent',
                    cpu_usage=50.0,
                    memory_usage=60.0,
                    disk_usage=70.0,
                    network_in_bps=1_500_000.0,
                    network_out_bps=700_000.0,
                    timestamp=old_ts_2,
                ),
                ServerHealthLog(
                    device_id=device.device_id,
                    source='agent',
                    cpu_usage=25.0,
                    memory_usage=35.0,
                    disk_usage=45.0,
                    network_in_bps=900_000.0,
                    network_out_bps=450_000.0,
                    timestamp=recent_ts,
                ),
            ])

            db.session.add(
                InterfaceTrafficHistory(
                    interface_id=iface.interface_id,
                    timestamp=recent_ts,
                    rx_bps=2_000_000.0,
                    tx_bps=1_000_000.0,
                )
            )

            # Reset rollup cursors so this verifier can be re-run deterministically.
            ServerHealthRollupState.query.filter(
                ServerHealthRollupState.name.in_(['raw_to_hourly', 'hourly_to_daily'])
            ).delete(synchronize_session=False)
            db.session.commit()

            result_1 = maintenance_service.run_server_health_retention(
                raw_days=7,
                hourly_days=30,
                daily_days=365,
            )
            if not result_1.get('success'):
                print(f"FAILED: First retention run failed: {result_1}")
                return 1

            first_hourly_roll = result_1.get('tasks', {}).get('hourly_rollup', {})
            if int(first_hourly_roll.get('rolled_buckets', 0)) <= 0:
                print("FAILED: Hourly rollup reported zero rolled buckets.")
                return 1

            after_hourly = ServerHealthHourlyRollup.query.filter_by(device_id=device.device_id).count()
            after_daily = ServerHealthDailyRollup.query.filter_by(device_id=device.device_id).count()

            # Second run should be idempotent for the same closed window.
            result_2 = maintenance_service.run_server_health_retention(
                raw_days=7,
                hourly_days=30,
                daily_days=365,
            )
            if not result_2.get('success'):
                print(f"FAILED: Second retention run failed: {result_2}")
                return 1

            idempotent_hourly = ServerHealthHourlyRollup.query.filter_by(device_id=device.device_id).count()
            idempotent_daily = ServerHealthDailyRollup.query.filter_by(device_id=device.device_id).count()
            if idempotent_hourly != after_hourly or idempotent_daily != after_daily:
                print("FAILED: Retention is not idempotent; rollup counts changed on second run.")
                return 1

            cutoff = datetime.utcnow() - timedelta(days=7)
            old_raw_left = ServerHealthLog.query.filter(
                ServerHealthLog.device_id == device.device_id,
                ServerHealthLog.timestamp < cutoff
            ).count()
            if old_raw_left != 0:
                print(f"FAILED: Old raw logs were not cleaned up (remaining={old_raw_left}).")
                return 1

        # Verify dashboard endpoints still work after retention.
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['logged_in'] = True
                sess['username'] = 'retention_tester'
                sess['last_activity'] = datetime.utcnow().isoformat()

            fleet_resp = client.get('/api/server/fleet-metrics')
            if fleet_resp.status_code != 200:
                print(f"FAILED: /api/server/fleet-metrics returned {fleet_resp.status_code}")
                return 1
            fleet_data = fleet_resp.get_json() or {}
            if 'health' not in fleet_data or fleet_data.get('health', {}).get('total', 0) <= 0:
                print("FAILED: /api/server/fleet-metrics returned no health totals.")
                return 1

            net_resp = client.get('/api/dashboard/realtime/network-io')
            if net_resp.status_code != 200:
                print(f"FAILED: /api/dashboard/realtime/network-io returned {net_resp.status_code}")
                return 1
            net_data = net_resp.get_json() or {}
            if 'labels' not in net_data:
                print("FAILED: /api/dashboard/realtime/network-io missing 'labels'.")
                return 1

        print("SUCCESS: Server metrics retention/rollups verified.")
        return 0
    finally:
        if created_device_id is not None:
            with app.app_context():
                try:
                    from models.device import Device
                    from models.interfaces import DeviceInterface, InterfaceTrafficHistory
                    from models.server_health import ServerHealthLog
                    from models.server_health_rollups import (
                        ServerHealthHourlyRollup,
                        ServerHealthDailyRollup,
                    )

                    ServerHealthDailyRollup.query.filter_by(device_id=created_device_id).delete(synchronize_session=False)
                    ServerHealthHourlyRollup.query.filter_by(device_id=created_device_id).delete(synchronize_session=False)
                    ServerHealthLog.query.filter_by(device_id=created_device_id).delete(synchronize_session=False)

                    iface_ids = [
                        row.interface_id
                        for row in DeviceInterface.query.filter_by(device_id=created_device_id).all()
                    ]
                    if iface_ids:
                        InterfaceTrafficHistory.query.filter(
                            InterfaceTrafficHistory.interface_id.in_(iface_ids)
                        ).delete(synchronize_session=False)
                        DeviceInterface.query.filter(
                            DeviceInterface.interface_id.in_(iface_ids)
                        ).delete(synchronize_session=False)

                    Device.query.filter_by(device_id=created_device_id).delete(synchronize_session=False)
                    db.session.commit()
                except Exception:
                    db.session.rollback()


if __name__ == '__main__':
    raise SystemExit(verify_metrics_retention())
