import schedule
import time
import threading
from datetime import datetime
from services.device_monitor import DeviceMonitor
import asyncio
from extensions import db

class MonitoringScheduler:
    def __init__(self, app):
        self.app = app
        self.monitor = DeviceMonitor()
        self.is_running = False
        self.scheduler_thread = None
    
    def start_scheduled_monitoring(self):
        """Start the scheduled monitoring tasks"""
        # Monitor every 5 minutes
        schedule.every(5).minutes.do(self.run_monitoring_task)
        schedule.every(5).minutes.do(self.enqueue_snmp_tasks)
        
        # Auto-discovery check every 1 minute (actual scan fires only when interval elapsed)
        schedule.every(1).minutes.do(self.maybe_run_auto_discovery)
        
        # Daily report at 23:59
        schedule.every().day.at("23:59").do(self.generate_daily_report)

        # Server metrics retention + rollups
        retention_time = self.app.config.get('SERVER_HEALTH_RETENTION_SCHEDULE', '02:00')
        schedule.every().day.at(retention_time).do(self.run_metrics_retention)

        # Rollup integrity validation + repair
        integrity_time = self.app.config.get('SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE', '03:00')
        schedule.every().day.at(integrity_time).do(self.run_rollup_integrity_check)
        
        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self.run_scheduler)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        # Run immediate scan in background so UI has data
        threading.Thread(target=self.run_monitoring_task).start()
        
        print("Scheduled monitoring started (initial scan triggered)...")
    
    def stop_scheduled_monitoring(self):
        """Stop the scheduled monitoring"""
        self.is_running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        print("Scheduled monitoring stopped.")
    
    def run_scheduler(self):
        """Run the scheduler loop"""
        while self.is_running:
            schedule.run_pending()
            time.sleep(1)
    
    def run_monitoring_task(self):
        """Run monitoring task within application context"""
        with self.app.app_context():
            try:
                asyncio.run(self.monitor.monitor_stored_devices())
                print(f"Scheduled monitoring completed at {datetime.now()}")
            except Exception as e:
                print(f"Error in scheduled monitoring: {e}")
            finally:
                # Ensure session is cleaned up after background task
                db.session.remove()
    
    def maybe_run_auto_discovery(self):
        """Check if auto-discovery should run and fire heavy scan when due."""
        with self.app.app_context():
            try:
                from models.discovery_config import get_config
                cfg = get_config()
                if not cfg.enabled:
                    return

                from datetime import datetime, timedelta
                now = datetime.utcnow()

                # Heavy scan check
                heavy_interval = timedelta(minutes=cfg.heavy_interval_min or 1440)
                if cfg.last_heavy_scan is None or (now - cfg.last_heavy_scan) >= heavy_interval:
                    from services.auto_discovery_service import get_auto_discovery_service
                    svc = get_auto_discovery_service()
                    svc.trigger_heavy_scan(self.app)

            except Exception as e:
                print(f"Error in auto-discovery check: {e}")
            finally:
                db.session.remove()

    def generate_daily_report(self):
        """Generate daily report"""
        with self.app.app_context():
            try:
                report = self.monitor.get_daily_report()
                print(f"Daily report generated for {report['date']}")
                # Here you can add email sending or other reporting mechanisms
            except Exception as e:
                print(f"Error generating daily report: {e}")

    def run_metrics_retention(self):
        """Run server health rollups and retention cleanup."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                result = maintenance_service.run_server_health_retention(
                    raw_days=self.app.config.get('SERVER_HEALTH_RAW_RETENTION_DAYS', 7),
                    hourly_days=self.app.config.get('SERVER_HEALTH_HOURLY_RETENTION_DAYS', 30),
                    daily_days=self.app.config.get('SERVER_HEALTH_DAILY_RETENTION_DAYS', 365),
                )
                print(f"Server health retention completed: success={result.get('success')}")
            except Exception as e:
                print(f"Error running metrics retention: {e}")
            finally:
                db.session.remove()

    def run_rollup_integrity_check(self):
        """Validate and repair missing server health rollup buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                result = maintenance_service.validate_and_repair_server_health_rollups(
                    lookback_days=self.app.config.get('SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS', 45)
                )
                print(
                    "[ROLLUP] Integrity check completed: "
                    f"success={result.get('success')} "
                    f"hourly_missing={result.get('hourly', {}).get('missing', 0)} "
                    f"daily_missing={result.get('daily', {}).get('missing', 0)}"
                )
            except Exception as e:
                print(f"Error running rollup integrity check: {e}")
            finally:
                db.session.remove()

    def check_snmp_health(self):
        """
        Backward-compatible alias.
        Legacy code may still call this method name.
        """
        self.enqueue_snmp_tasks()

    def enqueue_snmp_tasks(self):
        """Enqueue SNMP health poll tasks for all enabled devices.
        
        RULE: Scheduler performs ZERO network I/O.
        This method only INSERTs PollTask rows with status='pending'.
        Actual SNMP execution happens in workers/snmp_worker.py.
        
        Duplicate protection: skips devices that already have a
        pending or running task for the same task_type.
        """
        with self.app.app_context():
            try:
                from models.device import Device
                from models.snmp_config import DeviceSnmpConfig
                from models.poll_task import PollTask
                from models.server_health import ServerHealthLog
                from datetime import datetime, timedelta

                # Find devices with SNMP enabled
                configs = DeviceSnmpConfig.query.filter_by(is_enabled=True).all()
                if not configs:
                    return

                enqueued = 0
                skipped = 0
                now = datetime.utcnow()
                stale_threshold = now - timedelta(minutes=5)

                for config in configs:
                    device = Device.query.get(config.device_id)
                    if not device or not device.is_monitored:
                        continue
                        
                    # Skip SNMP polling for Servers if the Python Agent is actively reporting
                    if device.device_type == 'server':
                        latest_agent_log = ServerHealthLog.query.filter_by(
                            device_id=device.device_id, 
                            source='agent'
                        ).order_by(ServerHealthLog.timestamp.desc()).first()
                        
                        if latest_agent_log and latest_agent_log.timestamp >= stale_threshold:
                            skipped += 1
                            continue # Agent is fresh, no need for SNMP fallback

                    # Map device criticality to priority
                    tier = getattr(device, 'cos_tier', 'Standard') or 'Standard'
                    priority_map = {'Critical': 1, 'Standard': 5, 'Low': 9}
                    priority = priority_map.get(tier, 5)

                    # Enqueue with duplicate protection
                    task = PollTask.enqueue(
                        device_id=device.device_id,
                        task_type='snmp_health',
                        priority=priority
                    )

                    if task:
                        enqueued += 1
                    else:
                        skipped += 1

                if enqueued > 0:
                    db.session.commit()

                if enqueued > 0 or skipped > 0:
                    print(f"[SCHEDULER] SNMP tasks: {enqueued} enqueued, {skipped} skipped (already pending)")

            except Exception as e:
                db.session.rollback()
                print(f"[SCHEDULER] Error enqueuing SNMP tasks: {e}")
            finally:
                db.session.remove()
