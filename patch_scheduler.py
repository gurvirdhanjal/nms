with open(r'd:\device_monitoring_tactical\services\scheduler.py', 'r') as f:
    text = f.read()

import re

# 1. Inject the schedule task
text = re.sub(
    r'(schedule\.every\(\)\.day\.at\(tracking_integrity_time\)\.do\(self\.run_tracking_history_retention\))',
    r'\1\n        \n        # Sync maintenance windows to devices every minute\n        schedule.every(1).minutes.do(self.sync_maintenance_windows)',
    text,
    count=1
)

# 2. Inject the function after run_scheduler
func = '''
    def sync_maintenance_windows(self):
        """Synchronize active maintenance windows with the boolean device.maintenance_mode column."""
        with self.app.app_context():
            try:
                from models.device import Device
                from services.maintenance_window_service import maintenance_window_service
                
                devices = Device.query.all()
                device_ids = [d.device_id for d in devices]
                active_map = maintenance_window_service.get_active_window_map(device_ids)
                
                updates = 0
                for device in devices:
                    current_status = bool(getattr(device, "maintenance_mode", False))
                    should_be_in_maintenance = device.device_id in active_map
                    
                    if current_status != should_be_in_maintenance:
                        device.maintenance_mode = should_be_in_maintenance
                        updates += 1
                        
                if updates > 0:
                    db.session.commit()
                    logger.info(f"[MAINTENANCE] Synced maintenance mode for {updates} devices.")
                    
            except Exception as e:
                logger.error(f"[MAINTENANCE] Error syncing maintenance windows: {e}")
                db.session.rollback()
            finally:
                db.session.remove()
'''

text = re.sub(
    r'(def run_scheduler\(self\):.*?time\.sleep\(1\))',
    r'\1\n' + func,
    text,
    flags=re.DOTALL,
    count=1
)

with open(r'd:\device_monitoring_tactical\services\scheduler.py', 'w') as f:
    f.write(text)

print('Scheduler successfully modified.')
