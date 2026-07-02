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
    "maybe_run_auto_discovery":        ("auto_discovery",               60),
    "purge_old_alerts":                ("alert_retention",              86400),
    "purge_old_scan_history":          ("scan_history_retention",        86400),
    "purge_old_activity_logs":         ("activity_log_retention",        86400),
    "purge_old_task_queues":           ("task_queue_retention",          86400),
    "drain_alert_fanout_queue":        ("alert_fanout_drain",            10),
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
        self._loop = None  # reused event loop — avoids per-cycle asyncio.run() leak
        self.scheduler_thread = None
        # Dynamic interval tracking — updated each run; read from AppSettings (60s cache).
        # A 1s scheduler heartbeat lets us honor sub-minute cadences like 15s.
        self._heartbeat_seconds = 1
        self._monitoring_last_run: float = 0.0
        self._snmp_last_run: float = 0.0
        self._monitoring_lock = threading.Lock()
        self._snmp_lock = threading.Lock()

    def _get_monitoring_interval(self) -> int:
        """Return current monitoring interval in seconds from DB/env (cached)."""
        try:
            from services.settings_service import get_monitoring_interval
            with self.app.app_context():
                return get_monitoring_interval()
        except Exception:
            pass
        return max(10, min(3600, self.app.config.get('MONITORING_INTERVAL', 300)))

    def start_scheduled_monitoring(self):
        """Start the scheduled monitoring tasks"""
        # Tick every second; actual fire rate is controlled by _monitoring_last_run
        # so sub-minute intervals can be honored without a scheduler restart.
        schedule.every(self._heartbeat_seconds).seconds.do(self.run_monitoring_task)
        schedule.every(self._heartbeat_seconds).seconds.do(self.enqueue_snmp_tasks)
        
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

        # Nightly alert retention — delete resolved alerts older than 90 days
        schedule.every().day.at("03:30").do(self.purge_old_alerts)

        # Nightly scan history retention — device_scan_history is the only high-volume
        # table with NO cleanup job. At 5-min intervals × 239 devices it grows ~70 K rows/day.
        # Default retention: 30 days (configurable via SCAN_HISTORY_RETENTION_DAYS).
        schedule.every().day.at("04:00").do(self.purge_old_scan_history)

        # Activity log retention — device_activity/resource/application_logs had no cleanup.
        # Default 30 days (ACTIVITY_LOG_RETENTION_DAYS).
        schedule.every().day.at("02:30").do(self.purge_old_activity_logs)

        # Task queue retention — poll_tasks and alert_fanout_tasks accumulate completed/failed
        # rows indefinitely without cleanup. Default 7 days (POLL_TASK_RETENTION_DAYS).
        schedule.every().day.at("04:30").do(self.purge_old_task_queues)

        # Alert fanout drain — claims pending AlertFanoutTask rows and delivers them
        # via broadcast_event() (SSE → Redis) or email. Without this, alert rows pile
        # up in the DB as pending forever and the dashboard never gets push updates.
        schedule.every(10).seconds.do(self.drain_alert_fanout_queue)

        # SQLite WAL checkpoint — WAL file grows indefinitely without periodic checkpointing.
        # No-op on PostgreSQL deployments.
        schedule.every().week.do(self.run_sqlite_maintenance)

        # Sync maintenance windows to devices every minute
        schedule.every(1).minutes.do(self.sync_maintenance_windows)

        # Pre-warm expensive report queries into Redis so every user request is a
        # cache hit. Runs in a background thread so the scheduler loop is not blocked.
        schedule.every(8).minutes.do(self.prewarm_report_cache)

        # Refresh the dashboard snapshot every 2 minutes so users always get
        # fresh data without triggering inline computation under concurrent load.
        schedule.every(2).minutes.do(self.warm_dashboard_snapshot)

        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self._run_scheduler_with_watchdog)
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
                    try:
                        if not _backfill_needed():
                            logger.info("Startup backfill skipped — recent daily_device_stats exist")
                            return
                        from services.maintenance_service import MaintenanceService
                        result = MaintenanceService().backfill_daily_stats(days=90)
                        logger.info("Startup backfill complete: %s", result)
                    finally:
                        db.session.remove()
            except Exception:
                logger.exception("Startup backfill failed (non-fatal)")

        t_backfill = threading.Thread(target=run_startup_backfill, daemon=True, name="startup-backfill")
        t_backfill.start()

        logger.info("Scheduled monitoring started (initial scan triggered).")
    
    def stop_scheduled_monitoring(self):
        """Stop the scheduled monitoring"""
        self.is_running = False
        logger.info("Scheduled monitoring stopped.")
    
    def run_scheduler(self):
        """Inner scheduler loop — runs pending jobs every second."""
        while self.is_running:
            schedule.run_pending()
            time.sleep(1)

    def _run_scheduler_with_watchdog(self):
        """Watchdog wrapper: restarts the inner loop after any unhandled exception."""
        while self.is_running:
            try:
                self.run_scheduler()
            except Exception:
                logger.exception("[Scheduler] Thread crashed — restarting in 10s")
                time.sleep(10)

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

    
    def prewarm_report_cache(self):
        """Dispatch pre-warm to a background daemon thread so the scheduler loop is not blocked."""
        import threading
        t = threading.Thread(target=self._prewarm_report_cache_bg, daemon=True, name="report-prewarm")
        t.start()

    def _prewarm_report_cache_bg(self):
        """Compute executive reports and cache them in Redis (TTL 15 min).

        Runs off the scheduler thread — Redis failure or slow DB query never
        impacts the main monitoring loop.

        Uses test_request_context with role='admin' so build_scope_context()
        returns global scope without needing a real HTTP session.
        """
        from extensions import redis_client, is_redis_available
        if not is_redis_available():
            return
        import json
        from datetime import datetime, timedelta
        from flask import session as flask_session
        from services.reporting_service import ReportingService
        end_dt = datetime.utcnow()
        for range_days, redis_key in [(30, 'nms:report:executive:30d'), (7, 'nms:report:executive:7d')]:
            start_dt = end_dt - timedelta(days=range_days)
            try:
                with self.app.test_request_context():
                    flask_session['role'] = 'admin'
                    svc = ReportingService()
                    payload = svc.get_executive_fleet_health(start_dt, end_dt)
                    db.session.remove()
                redis_client.setex(redis_key, 900, json.dumps(payload, default=str))
                logger.info('[PreWarm] Cached executive %dd in Redis', range_days)
            except Exception as exc:
                logger.warning('[PreWarm] executive %dd failed: %s', range_days, exc)

        # Enterprise-uptime report (30d, all fleets — default dashboard view)
        start_dt_ent = end_dt - timedelta(days=30)
        try:
            with self.app.test_request_context():
                flask_session['role'] = 'admin'
                from services.enterprise_report_service import build_enterprise_uptime_report
                payload = build_enterprise_uptime_report(start_date=start_dt_ent, end_date=end_dt)
                db.session.remove()
                try:
                    from services.report_narrative_service import ReportNarrativeService
                    svc_n = ReportNarrativeService()
                    payload['narratives'] = {
                        'executive':     svc_n.generate_narrative('executive', payload),
                        'server_fleet':  svc_n.generate_narrative('server-fleet', payload),
                        'tracked_fleet': svc_n.generate_narrative('tracked-fleet', payload),
                    }
                    payload['cross_report'] = None
                except Exception:
                    payload['narratives'] = {}
                    payload['cross_report'] = None
                try:
                    from services.report_intelligence_rules import ReportIntelligenceRules
                    payload['intelligence_annotations'] = ReportIntelligenceRules().annotate('enterprise', payload)
                except Exception:
                    payload['intelligence_annotations'] = []
            redis_client.setex('nms:report:enterprise-uptime:30d', 900, json.dumps(payload, default=str))
            logger.info('[PreWarm] Cached enterprise-uptime 30d in Redis')
        except Exception as exc:
            logger.warning('[PreWarm] enterprise-uptime 30d failed: %s', exc)

    def run_monitoring_task(self):
        """Run monitoring task within application context.

        Respects the live monitoring interval from AppSettings. A short
        scheduler heartbeat plus a non-blocking lock lets sub-minute cadences
        work without overlapping scan cycles.
        """
        if not self._monitoring_lock.acquire(blocking=False):
            logger.debug("Scheduled monitoring skipped because a prior cycle is still running")
            return
        with self.app.app_context():
            try:
                now = time.time()
                interval = self._get_monitoring_interval()
                if now - self._monitoring_last_run < interval:
                    return  # Not enough time has elapsed — skip this tick.
                self._monitoring_last_run = now
                if self._loop is None or self._loop.is_closed():
                    self._loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self.monitor.monitor_stored_devices())
                logger.debug("Scheduled monitoring completed at %s", datetime.now())
            except Exception as e:
                logger.exception("Error in scheduled monitoring: %s", e)
            finally:
                # Ensure session is cleaned up after background task
                db.session.remove()
                self._monitoring_lock.release()

    def warm_dashboard_snapshot(self):
        """Dispatch snapshot warm to a daemon thread so the scheduler loop is not blocked."""
        import threading
        t = threading.Thread(target=self._warm_dashboard_snapshot_bg, daemon=True, name="snapshot-warm")
        t.start()

    def _warm_dashboard_snapshot_bg(self):
        """Pre-compute and upsert DashboardSnapshot rows every 2 min.

        Mirrors dashboard_worker.py but runs inside the existing web process so
        no separate container is needed. Eliminates 'Snapshot lock busy' warnings
        and the slow inline fallback path on every user request.
        """
        import json
        from models.dashboard import DashboardSnapshot

        raw = self.app.config.get('WORKER_SCOPES') or \
              __import__('os').environ.get('WORKER_SCOPES', 'admin__global:24h:active:200')
        scopes = []
        for entry in raw.split(','):
            parts = [p.strip() for p in entry.strip().split(':')]
            if len(parts) == 4 and all(parts):
                scopes.append(parts)
        if not scopes:
            scopes = [['admin__global', '24h', 'active', '200']]

        with self.app.app_context():
            try:
                with self.app.test_client() as client:
                    with client.session_transaction() as sess:
                        sess['logged_in'] = True
                        sess['role'] = 'admin'
                        sess['user_id'] = 'dashboard-worker'
                        sess['last_activity'] = datetime.utcnow().isoformat()

                    for scope_fragment, time_range, alert_status, alert_limit in scopes:
                        url = (
                            f'/api/dashboard/full_snapshot'
                            f'?worker_compute=true&range={time_range}'
                            f'&status={alert_status}&limit={alert_limit}'
                        )
                        response = client.get(url)
                        if response.status_code != 200:
                            logger.warning(
                                "[Scheduler] Dashboard snapshot warm failed HTTP %s for %s",
                                response.status_code, scope_fragment,
                            )
                            continue

                        raw_json = json.dumps(json.loads(response.data))
                        cache_key = (
                            f"full_snapshot_{scope_fragment}_{time_range}"
                            f"_{alert_status}_{alert_limit}"
                        )
                        # The test_client request may have left the session in an
                        # aborted state if any section raised a DB exception.
                        # Roll back before querying to get a clean transaction.
                        db.session.rollback()
                        snapshot = DashboardSnapshot.query.filter_by(cache_key=cache_key).first()
                        if not snapshot:
                            snapshot = DashboardSnapshot(cache_key=cache_key, payload=raw_json)
                            db.session.add(snapshot)
                        else:
                            snapshot.payload = raw_json
                            snapshot.updated_at = datetime.utcnow()
                        db.session.commit()
            except Exception:
                logger.exception("[Scheduler] Dashboard snapshot warm error")
                db.session.rollback()
            finally:
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
            fired = False
            try:
                from models.discovery_config import get_config
                cfg = get_config()
                if not cfg.enabled:
                    return

                from datetime import datetime, timedelta
                now = datetime.utcnow()

                heavy_interval = timedelta(minutes=cfg.heavy_interval_min or 1440)
                if cfg.last_heavy_scan is None or (now - cfg.last_heavy_scan) >= heavy_interval:
                    from services.auto_discovery_service import get_auto_discovery_service
                    svc = get_auto_discovery_service()
                    svc.trigger_heavy_scan(self.app)
                    fired = True
                    logger.info("[AutoDiscovery] Heavy scan triggered by scheduler")

                _record_run("auto_discovery", True)

            except Exception as e:
                _record_run("auto_discovery", False)
                logger.error("[AutoDiscovery] maybe_run_auto_discovery failed: %s", e)
            finally:
                db.session.remove()

    def generate_daily_report(self):
        """Generate daily report"""
        with self.app.app_context():
            try:
                report = self.monitor.get_daily_report()
                logger.info("Daily report generated for %s", report['date'])
                # Here you can add email sending or other reporting mechanisms
            except Exception as e:
                logger.exception("Error generating daily report: %s", e)
            finally:
                db.session.remove()

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

        ssh_profile_id is commented out of the Device ORM and may not exist in the DB yet.
        We check column existence at runtime before querying to avoid startup errors.
        """
        with self.app.app_context():
            try:
                from models.poll_task import PollTask
                from sqlalchemy import text

                col_exists = db.session.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'device' AND column_name = 'ssh_profile_id'"
                )).scalar()
                if not col_exists:
                    _record_run("backup_device_configs", True)
                    logger.debug("[scheduler] backup_device_configs: ssh_profile_id column absent, skipping")
                    return

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

    def purge_old_scan_history(self):
        """Delete device_scan_history rows older than SCAN_HISTORY_RETENTION_DAYS (default 30).

        At 5-min intervals × 239 devices this table accumulates ~70 K rows/day and
        ~2 M rows/month with no cleanup. The DELETE is bounded and batched to avoid
        long-running transactions that could spike lock contention.
        """
        with self.app.app_context():
            try:
                from models.scan_history import DeviceScanHistory
                retention_days = int(self.app.config.get('SCAN_HISTORY_RETENTION_DAYS', 30))
                cutoff = datetime.utcnow() - timedelta(days=retention_days)
                # Batch delete to avoid a single enormous transaction
                total_deleted = 0
                while True:
                    # Use a subquery + LIMIT to bound each DELETE to 10,000 rows
                    subq = db.session.query(DeviceScanHistory.scan_id).filter(
                        DeviceScanHistory.scan_timestamp < cutoff
                    ).limit(10000).subquery()
                    deleted = DeviceScanHistory.query.filter(
                        DeviceScanHistory.scan_id.in_(subq)
                    ).delete(synchronize_session=False)
                    db.session.commit()
                    total_deleted += deleted
                    if deleted < 10000:
                        break  # No more rows to delete
                logger.info(
                    "[SCHEDULER] purge_old_scan_history: deleted %d rows older than %d days",
                    total_deleted, retention_days,
                )
            except Exception:
                db.session.rollback()
                logger.exception("[SCHEDULER] purge_old_scan_history failed")
            finally:
                db.session.remove()

    def drain_alert_fanout_queue(self):
        """Claim and deliver pending AlertFanoutTask rows (SSE + email channels).

        Runs every 10 s. Drains up to _FANOUT_BATCH_MAX rows per tick so a
        burst of alerts doesn't block the scheduler thread for more than a few
        seconds. Each claim is DB-locked via claim_token so multiple workers
        (e.g. Gunicorn) don't double-deliver.
        """
        _FANOUT_BATCH_MAX = 50
        with self.app.app_context():
            try:
                from workers.alert_fanout_worker import run_once as _fanout_run_once
                delivered = 0
                for _ in range(_FANOUT_BATCH_MAX):
                    task = _fanout_run_once()
                    if task is None:
                        break
                    delivered += 1
                if delivered:
                    logger.info("[SCHEDULER] drain_alert_fanout_queue: delivered %d task(s)", delivered)
                _record_run("alert_fanout_drain", True)
            except Exception as exc:
                _record_run("alert_fanout_drain", False)
                logger.warning("[SCHEDULER] drain_alert_fanout_queue error: %s", exc)

    def purge_old_alerts(self):
        """Delete resolved dashboard_events older than 90 days."""
        with self.app.app_context():
            try:
                from models.dashboard import DashboardEvent
                cutoff = datetime.utcnow() - timedelta(days=90)
                deleted = DashboardEvent.query.filter(
                    DashboardEvent.resolved.is_(True),
                    DashboardEvent.resolved_at < cutoff,
                ).delete(synchronize_session=False)
                db.session.commit()
                _record_run("alert_retention", True)
                logger.info("[SCHEDULER] purge_old_alerts: deleted %d resolved alerts older than 90d", deleted)
            except Exception:
                db.session.rollback()
                _record_run("alert_retention", False)
                logger.exception("[SCHEDULER] purge_old_alerts failed")
            finally:
                db.session.remove()

    def run_sqlite_maintenance(self):
        """Run WAL checkpoint and PRAGMA optimize for SQLite deployments.

        WAL mode never shrinks the WAL file unless explicitly checkpointed.
        PRAGMA optimize updates query planner statistics.  Both are no-ops on
        PostgreSQL so this is safe to schedule unconditionally.
        """
        with self.app.app_context():
            try:
                db_uri = self.app.config.get('SQLALCHEMY_DATABASE_URI', '')
                if 'sqlite' not in db_uri:
                    return
                from sqlalchemy import text
                db.session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
                db.session.execute(text("PRAGMA optimize"))
                db.session.commit()
                logger.info("[SCHEDULER] run_sqlite_maintenance: WAL checkpoint + optimize done")
            except Exception:
                db.session.rollback()
                logger.exception("[SCHEDULER] run_sqlite_maintenance failed")
            finally:
                db.session.remove()

    def purge_old_activity_logs(self):
        """Delete device_activity/resource/application_logs older than ACTIVITY_LOG_RETENTION_DAYS.

        These three tables had no cleanup job and grew unbounded.  Batched 10 K rows per
        transaction to avoid lock spikes, matching the pattern in purge_old_scan_history.
        """
        with self.app.app_context():
            try:
                from models.tracked_device import DeviceActivityLog, DeviceResourceLog, DeviceApplicationLog
                retention_days = int(self.app.config.get('ACTIVITY_LOG_RETENTION_DAYS', 30))
                cutoff = datetime.utcnow() - timedelta(days=retention_days)
                total_deleted = 0

                for Model in (DeviceActivityLog, DeviceResourceLog, DeviceApplicationLog):
                    table_deleted = 0
                    while True:
                        subq = db.session.query(Model.id).filter(
                            Model.timestamp < cutoff
                        ).limit(10000).subquery()
                        deleted = Model.query.filter(
                            Model.id.in_(subq)
                        ).delete(synchronize_session=False)
                        db.session.commit()
                        table_deleted += deleted
                        if deleted < 10000:
                            break
                    total_deleted += table_deleted

                _record_run("activity_log_retention", True)
                logger.info(
                    "[SCHEDULER] purge_old_activity_logs: deleted %d rows older than %d days",
                    total_deleted, retention_days,
                )
            except Exception:
                db.session.rollback()
                _record_run("activity_log_retention", False)
                logger.exception("[SCHEDULER] purge_old_activity_logs failed")
            finally:
                db.session.remove()

    def purge_old_task_queues(self):
        """Delete completed/failed poll_tasks and alert_fanout_tasks older than retention window.

        Both tables accumulate terminal-state rows (done/failed/delivered) indefinitely.
        Default 7 days (POLL_TASK_RETENTION_DAYS / ALERT_FANOUT_RETENTION_DAYS).
        """
        with self.app.app_context():
            try:
                from models.poll_task import PollTask
                from models.alert_fanout_task import AlertFanoutTask

                poll_days = int(self.app.config.get('POLL_TASK_RETENTION_DAYS', 7))
                fanout_days = int(self.app.config.get('ALERT_FANOUT_RETENTION_DAYS', 7))
                total_deleted = 0

                # poll_tasks: remove done/failed rows past retention window
                poll_cutoff = datetime.utcnow() - timedelta(days=poll_days)
                while True:
                    subq = db.session.query(PollTask.id).filter(
                        PollTask.status.in_(['done', 'failed']),
                        PollTask.created_at < poll_cutoff,
                    ).limit(10000).subquery()
                    deleted = PollTask.query.filter(
                        PollTask.id.in_(subq)
                    ).delete(synchronize_session=False)
                    db.session.commit()
                    total_deleted += deleted
                    if deleted < 10000:
                        break

                # alert_fanout_tasks: remove terminal rows (delivered/failed/pending)
                # past retention window. Include 'pending' so rows that were never
                # drained (pre-fix backlog) don't grow unbounded.
                fanout_cutoff = datetime.utcnow() - timedelta(days=fanout_days)
                while True:
                    subq = db.session.query(AlertFanoutTask.id).filter(
                        AlertFanoutTask.status.in_(['delivered', 'failed', 'pending']),
                        AlertFanoutTask.created_at < fanout_cutoff,
                    ).limit(10000).subquery()
                    deleted = AlertFanoutTask.query.filter(
                        AlertFanoutTask.id.in_(subq)
                    ).delete(synchronize_session=False)
                    db.session.commit()
                    total_deleted += deleted
                    if deleted < 10000:
                        break

                _record_run("task_queue_retention", True)
                logger.info(
                    "[SCHEDULER] purge_old_task_queues: deleted %d rows (poll≤%dd, fanout≤%dd)",
                    total_deleted, poll_days, fanout_days,
                )
            except Exception:
                db.session.rollback()
                _record_run("task_queue_retention", False)
                logger.exception("[SCHEDULER] purge_old_task_queues failed")
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

        Respects the live monitoring interval from AppSettings — same cadence
        as run_monitoring_task, with overlap protection.

        RULE: Scheduler performs ZERO network I/O.
        This method only INSERTs PollTask rows with status='pending'.
        Actual SNMP execution happens in workers/snmp_worker.py.

        Duplicate protection: skips devices that already have a
        pending or running task for the same task_type.
        """
        if not self._snmp_lock.acquire(blocking=False):
            logger.debug("SNMP enqueue skipped because a prior cycle is still running")
            return
        with self.app.app_context():
            try:
                now = time.time()
                interval = self._get_monitoring_interval()
                if now - self._snmp_last_run < interval:
                    return  # Not enough time has elapsed — skip this tick.
                self._snmp_last_run = now
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
                    logger.info("[SCHEDULER] SNMP tasks: %d enqueued, %d skipped (already pending)", enqueued, skipped)

            except Exception as e:
                db.session.rollback()
                logger.exception("[SCHEDULER] Error enqueuing SNMP tasks: %s", e)
            finally:
                db.session.remove()
                self._snmp_lock.release()
