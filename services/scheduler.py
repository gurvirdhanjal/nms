import schedule
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from services.device_monitor import DeviceMonitor
from services.operational_error_handling import log_operational_exception, summarize_exception
import asyncio
from extensions import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level job registry — stores last_run per job name.
# Keyed by the canonical job name used in /api/admin/scheduler/status.
# Written by _record_run(); read by the status endpoint.
# ---------------------------------------------------------------------------
_JOB_REGISTRY: dict[str, dict] = {}
_JOB_REGISTRY_LOCK = threading.Lock()

# Maps job method name → (display_name, interval_seconds)
# Used for "status" classification in the health endpoint.
JOB_META: dict[str, tuple[str, int]] = {
    "run_server_health_hourly_rollup": ("server_health_hourly_rollup",  3600),
    "run_tracking_hourly_rollup":      ("tracking_hourly_rollup",        3600),
    "run_daily_device_stats_rollup":   ("daily_device_stats_rollup",    86400),
    "run_server_health_daily_rollup":  ("server_health_daily_rollup",   86400),
    "run_tracking_daily_rollup":       ("tracking_daily_rollup",        86400),
    "run_metrics_retention":           ("metrics_retention",            86400),
    "run_rollup_integrity_check":      ("rollup_integrity_check",       86400),
    "run_tracking_history_integrity":  ("tracking_history_integrity",   86400),
    "run_tracking_history_retention":  ("tracking_history_retention",   86400),
    "enqueue_config_backup_tasks":     ("backup_device_configs",        86400),
}


def _record_run(job_key: str, success: bool) -> None:
    """Record last_run timestamp and outcome for a job."""
    with _JOB_REGISTRY_LOCK:
        _JOB_REGISTRY[job_key] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "last_success": success,
        }


def get_scheduler_status() -> list[dict]:
    """
    Return a list of job status dicts for the health endpoint.
    Called from routes/maintenance.py — no app context needed.
    """
    now = datetime.now(timezone.utc)
    result = []
    with _JOB_REGISTRY_LOCK:
        registry_snapshot = dict(_JOB_REGISTRY)

    for method_name, (display_name, interval_seconds) in JOB_META.items():
        entry = registry_snapshot.get(display_name)
        if entry is None:
            status = "never_run"
            last_run = None
            last_success = None
            next_run = None
        else:
            last_run = entry["last_run"]
            last_success = entry["last_success"]
            last_run_dt = datetime.fromisoformat(last_run)
            elapsed = (now - last_run_dt).total_seconds()
            overdue_threshold = interval_seconds * 2
            status = "ok" if elapsed <= overdue_threshold else "late"
            next_run = (
                last_run_dt.replace(tzinfo=timezone.utc)
                + timedelta(seconds=interval_seconds)
            ).isoformat()

        result.append({
            "name": display_name,
            "interval_seconds": interval_seconds,
            "last_run": last_run,
            "next_run": next_run,
            "last_success": last_success,
            "status": status,
        })
    return result


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

        # Tracking reconciliation every 60 seconds
        schedule.every(60).seconds.do(self.run_tracking_reconciliation)

        # Reporting rollups: closed hours/days materialized before cleanup windows.
        schedule.every().hour.at(self.app.config.get('SERVER_HEALTH_HOURLY_ROLLUP_AT', ':08')).do(
            self.run_server_health_hourly_rollup
        )
        schedule.every().hour.at(self.app.config.get('TRACKING_HOURLY_ROLLUP_AT', ':12')).do(
            self.run_tracking_hourly_rollup
        )
        schedule.every().day.at(self.app.config.get('DAILY_DEVICE_STATS_SCHEDULE', '00:15')).do(
            self.run_daily_device_stats_rollup
        )
        schedule.every().day.at(self.app.config.get('SERVER_HEALTH_DAILY_ROLLUP_SCHEDULE', '00:25')).do(
            self.run_server_health_daily_rollup
        )
        schedule.every().day.at(self.app.config.get('TRACKING_DAILY_ROLLUP_SCHEDULE', '00:35')).do(
            self.run_tracking_daily_rollup
        )
        
        # Daily report at 23:59
        schedule.every().day.at("23:59").do(self.generate_daily_report)

        # Server metrics retention + rollups
        retention_time = self.app.config.get('SERVER_HEALTH_RETENTION_SCHEDULE', '02:00')
        schedule.every().day.at(retention_time).do(self.run_metrics_retention)

        # Rollup integrity validation + repair
        integrity_time = self.app.config.get('SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE', '03:00')
        schedule.every().day.at(integrity_time).do(self.run_rollup_integrity_check)

        # Tracking history integrity checks
        tracking_integrity_time = self.app.config.get('TRACKING_INTEGRITY_CHECK_SCHEDULE', '03:30')
        schedule.every().day.at(tracking_integrity_time).do(self.run_tracking_history_integrity)
        schedule.every().day.at(tracking_integrity_time).do(self.run_tracking_history_retention)

        # Daily config backup — enqueue SSH capture tasks for all eligible devices
        schedule.every().day.at(self.app.config.get('CONFIG_BACKUP_SCHEDULE', '02:00')).do(
            self.enqueue_config_backup_tasks
        )

        # Sync maintenance windows to devices every minute
        schedule.every(1).minutes.do(self.sync_maintenance_windows)
        
        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self.run_scheduler)
        self.scheduler_thread.daemon = True
        self.scheduler_thread.start()
        
        # Run immediate scan in background so UI has data
        t_monitoring = threading.Thread(target=self.run_monitoring_task, daemon=True)
        t_monitoring.start()

        # Run an immediate reconciliation pass in background for tracking status freshness.
        t_recon = threading.Thread(target=self.run_tracking_reconciliation, daemon=True)
        t_recon.start()

        # One-shot startup backfill: populate daily_device_stats from existing scan history
        # if the table has no recent data. Uses a recency guard to avoid stampede on
        # multi-worker setups (Gunicorn workers each spawn this thread independently).
        _app_ref = self.app  # capture before thread start

        def _backfill_needed():
            from datetime import date, timedelta as _td
            from models.dashboard import DailyDeviceStats
            cutoff = date.today() - _td(days=1)
            return db.session.query(DailyDeviceStats).filter(
                DailyDeviceStats.date >= cutoff
            ).limit(1).count() == 0

        def run_startup_backfill():
            import time as _time
            _time.sleep(5)  # wait for DB pool to settle
            try:
                with _app_ref.app_context():
                    if not _backfill_needed():
                        logger.info("Startup backfill skipped — recent daily_device_stats exist")
                        return
                    from services.maintenance_service import MaintenanceService
                    result = MaintenanceService().backfill_daily_stats(days=90)
                    logger.info("Startup backfill complete: %s", result)
            except Exception:
                logger.exception("Startup backfill failed (non-fatal)")

        t_backfill = threading.Thread(target=run_startup_backfill, daemon=True, name="startup-backfill")
        t_backfill.start()

        print("Scheduled monitoring started (initial scan triggered)...")
    
    def stop_scheduled_monitoring(self):
        """Stop the scheduled monitoring"""
        self.is_running = False
        print("Scheduled monitoring stopped.")
    
    def run_scheduler(self):
        """Run the scheduler loop"""
        while self.is_running:
            schedule.run_pending()
            time.sleep(1)

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

    def run_tracking_reconciliation(self):
        """Run tracking reconciliation every minute."""
        with self.app.app_context():
            try:
                from services.tracking_reconcile import run_reconciliation

                report = run_reconciliation(force_discovery=False, dry_run=None)
                if not report.success and report.error_code != 'TRACKING_RECONCILIATION_BUSY':
                    logger.warning(
                        "[TRACKING] reconciliation failed: code=%s error=%s",
                        report.error_code,
                        summarize_exception(Exception(report.error or 'unknown')),
                    )
            except Exception as e:
                log_operational_exception(
                    logger,
                    "[TRACKING] scheduler reconciliation error",
                    e,
                    error_code='TRACKING_SCHEDULER_FAILED',
                )
            finally:
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

                logger.info("[SCHEDULER] run_metrics_retention started at %s", datetime.utcnow())
                result = maintenance_service.run_server_health_retention(
                    raw_days=self.app.config.get('SERVER_HEALTH_RAW_RETENTION_DAYS', 7),
                    hourly_days=self.app.config.get('SERVER_HEALTH_HOURLY_RETENTION_DAYS', 30),
                    daily_days=self.app.config.get('SERVER_HEALTH_DAILY_RETENTION_DAYS', 365),
                )
                _record_run("metrics_retention", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_metrics_retention completed at %s — success=%s",
                    datetime.utcnow(), result.get('success')
                )
            except Exception as e:
                _record_run("metrics_retention", False)
                logger.error("[SCHEDULER] run_metrics_retention failed: %s", e)
            finally:
                db.session.remove()

    def run_daily_device_stats_rollup(self):
        """Aggregate the previous day's scan history into daily stats."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_daily_device_stats_rollup started at %s", datetime.utcnow())
                result = maintenance_service.aggregate_daily_stats()
                _record_run("daily_device_stats_rollup", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_daily_device_stats_rollup completed at %s — success=%s date=%s devices=%s",
                    datetime.utcnow(), result.get('success'), result.get('target_date'), result.get('devices_aggregated', 0)
                )
            except Exception as e:
                _record_run("daily_device_stats_rollup", False)
                logger.error("[SCHEDULER] run_daily_device_stats_rollup failed: %s", e)
            finally:
                db.session.remove()

    def run_server_health_hourly_rollup(self):
        """Materialize closed hourly server-health buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_server_health_hourly_rollup started at %s", datetime.utcnow())
                result = maintenance_service.rollup_server_health_hourly()
                _record_run("server_health_hourly_rollup", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_server_health_hourly_rollup completed at %s — success=%s rolled=%s",
                    datetime.utcnow(), result.get('success'), result.get('rolled_buckets', 0)
                )
            except Exception as e:
                _record_run("server_health_hourly_rollup", False)
                logger.error("[SCHEDULER] run_server_health_hourly_rollup failed: %s", e)
            finally:
                db.session.remove()

    def run_server_health_daily_rollup(self):
        """Materialize closed daily server-health buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_server_health_daily_rollup started at %s", datetime.utcnow())
                result = maintenance_service.rollup_server_health_daily()
                _record_run("server_health_daily_rollup", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_server_health_daily_rollup completed at %s — success=%s rolled=%s",
                    datetime.utcnow(), result.get('success'), result.get('rolled_buckets', 0)
                )
            except Exception as e:
                _record_run("server_health_daily_rollup", False)
                logger.error("[SCHEDULER] run_server_health_daily_rollup failed: %s", e)
            finally:
                db.session.remove()

    def run_tracking_hourly_rollup(self):
        """Materialize closed hourly tracking buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_tracking_hourly_rollup started at %s", datetime.utcnow())
                result = maintenance_service.rollup_tracking_hourly()
                _record_run("tracking_hourly_rollup", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_tracking_hourly_rollup completed at %s — success=%s rolled=%s",
                    datetime.utcnow(), result.get('success'), result.get('rolled_buckets', 0)
                )
            except Exception as e:
                _record_run("tracking_hourly_rollup", False)
                logger.error("[SCHEDULER] run_tracking_hourly_rollup failed: %s", e)
            finally:
                db.session.remove()

    def run_tracking_daily_rollup(self):
        """Materialize closed daily tracking buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_tracking_daily_rollup started at %s", datetime.utcnow())
                result = maintenance_service.rollup_tracking_daily()
                _record_run("tracking_daily_rollup", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_tracking_daily_rollup completed at %s — success=%s rolled=%s",
                    datetime.utcnow(), result.get('success'), result.get('rolled_buckets', 0)
                )
            except Exception as e:
                _record_run("tracking_daily_rollup", False)
                logger.error("[SCHEDULER] run_tracking_daily_rollup failed: %s", e)
            finally:
                db.session.remove()

    def run_rollup_integrity_check(self):
        """Validate and repair missing server health rollup buckets."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_rollup_integrity_check started at %s", datetime.utcnow())
                result = maintenance_service.validate_and_repair_server_health_rollups(
                    lookback_days=self.app.config.get('SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS', 45)
                )
                _record_run("rollup_integrity_check", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_rollup_integrity_check completed at %s — success=%s hourly_missing=%s daily_missing=%s",
                    datetime.utcnow(), result.get('success'),
                    result.get('hourly', {}).get('missing', 0), result.get('daily', {}).get('missing', 0)
                )
            except Exception as e:
                _record_run("rollup_integrity_check", False)
                logger.error("[SCHEDULER] run_rollup_integrity_check failed: %s", e)
            finally:
                db.session.remove()

    def run_tracking_history_integrity(self):
        """Run tracking sample integrity checks."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_tracking_history_integrity started at %s", datetime.utcnow())
                result = maintenance_service.run_tracking_history_integrity_check()
                _record_run("tracking_history_integrity", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_tracking_history_integrity completed at %s — success=%s checks=%s",
                    datetime.utcnow(), result.get('success'), result.get('checks_created', 0)
                )
            except Exception as e:
                _record_run("tracking_history_integrity", False)
                logger.error("[SCHEDULER] run_tracking_history_integrity failed: %s", e)
            finally:
                db.session.remove()

    def run_tracking_history_retention(self):
        """Run tracking history retention cleanup."""
        with self.app.app_context():
            try:
                from services.maintenance_service import maintenance_service

                logger.info("[SCHEDULER] run_tracking_history_retention started at %s", datetime.utcnow())
                result = maintenance_service.run_tracking_history_retention(
                    raw_days=self.app.config.get('TRACKING_RAW_RETENTION_DAYS', 30),
                    hourly_days=self.app.config.get('TRACKING_HOURLY_RETENTION_DAYS', 365),
                    daily_days=self.app.config.get('TRACKING_DAILY_RETENTION_DAYS', 1095),
                )
                _record_run("tracking_history_retention", bool(result.get('success')))
                logger.info(
                    "[SCHEDULER] run_tracking_history_retention completed at %s — success=%s deleted=%s",
                    datetime.utcnow(), result.get('success'), result.get('deleted')
                )
            except Exception as e:
                _record_run("tracking_history_retention", False)
                logger.error("[SCHEDULER] run_tracking_history_retention failed: %s", e)
            finally:
                db.session.remove()

    def enqueue_config_backup_tasks(self):
        """Enqueue config backup poll tasks for all monitored devices with an SSH profile.

        RULE: Scheduler performs ZERO network I/O.
        This method only INSERTs PollTask rows with status='pending'.
        Actual SSH capture happens in workers/snmp_worker.py (_execute_config_backup).

        ssh_profile_id is commented out of the Device ORM (column exists in DB).
        We use raw SQL so we don't load all devices into Python just to filter.
        """
        with self.app.app_context():
            try:
                from models.poll_task import PollTask
                from sqlalchemy import text

                rows = db.session.execute(text(
                    "SELECT device_id FROM device "
                    "WHERE is_monitored = true AND ssh_profile_id IS NOT NULL"
                )).fetchall()

                device_ids = [row[0] for row in rows]
                enqueued = 0
                skipped = 0

                for device_id in device_ids:
                    task = PollTask.enqueue(
                        device_id=device_id,
                        task_type='config_backup',
                        priority=9,  # Low — SSH is expensive; health tasks take precedence
                    )
                    if task:
                        enqueued += 1
                    else:
                        skipped += 1

                if enqueued > 0:
                    db.session.commit()

                n = len(device_ids)
                _record_run("backup_device_configs", True)
                logger.info(
                    f"[scheduler] backup_device_configs: {n} devices, {enqueued} enqueued"
                    + (f", {skipped} skipped (already pending)" if skipped else "")
                )

            except Exception as e:
                _record_run("backup_device_configs", False)
                db.session.rollback()
                logger.error("[scheduler] backup_device_configs failed: %s", e)
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
