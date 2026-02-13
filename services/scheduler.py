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
        schedule.every(5).minutes.do(self.check_snmp_health)
        
        # Auto-discovery check every 1 minute (actual scan fires only when interval elapsed)
        schedule.every(1).minutes.do(self.maybe_run_auto_discovery)
        
        # Daily report at 23:59
        schedule.every().day.at("23:59").do(self.generate_daily_report)
        
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
        """Check if auto-discovery should run, and fire light sweep if interval elapsed."""
        with self.app.app_context():
            try:
                from models.discovery_config import get_config
                cfg = get_config()
                if not cfg.enabled:
                    return

                from datetime import datetime, timedelta
                now = datetime.utcnow()

                # Light sweep check
                interval = timedelta(minutes=cfg.light_interval_min or 30)
                if cfg.last_light_scan is None or (now - cfg.last_light_scan) >= interval:
                    from services.auto_discovery_service import get_auto_discovery_service
                    svc = get_auto_discovery_service()
                    svc.trigger_light_sweep(self.app)

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

    def check_snmp_health(self):
        """Check server health (CPU, RAM, Disk) via SNMP for configured devices."""
        with self.app.app_context():
            try:
                from models.device import Device
                from models.snmp_config import DeviceSnmpConfig
                from models.server_health import ServerHealthLog
                from services.snmp_service import snmp_service

                # Find devices with SNMP enabled
                configs = DeviceSnmpConfig.query.filter_by(is_enabled=True).all()
                if not configs: return

                print(f"Running SNMP Health Check for {len(configs)} devices...")
                
                for config in configs:
                    device = Device.query.get(config.device_id)
                    if not device: continue
                    
                    # Skip if device is not monitored globally
                    if not device.is_monitored: continue
                    
                    metrics = snmp_service.get_server_health_snmp(
                        device.device_ip, 
                        config.community_string or 'public', 
                        config.snmp_version or '2c', 
                        config.snmp_port or 161
                    )
                    
                    if metrics:
                        # Fetch uptime if possible to complete the log
                        sys_info = snmp_service.get_system_info(
                            device.device_ip, 
                            config.community_string or 'public', 
                            config.snmp_version or '2c', 
                            config.snmp_port or 161
                        )
                        uptime = str(sys_info.get('sys_uptime_seconds', ''))

                        log = ServerHealthLog(
                            device_id=device.device_id,
                            cpu_usage=metrics.get('cpu_usage'),
                            memory_usage=metrics.get('memory_usage'),
                            disk_usage=metrics.get('disk_usage'),
                            uptime=uptime,
                            source='snmp'
                        )
                        db.session.add(log) # Add log only if metrics exist
                        config.last_successful_poll = datetime.utcnow()
                        config.last_poll_error = None
                    else:
                        print(f"No metrics for {device.device_ip}")
                
                db.session.commit()
                print("SNMP Health Check Completed.")

            except Exception as e:
                print(f"Error in check_snmp_health: {e}")
