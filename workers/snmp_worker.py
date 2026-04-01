"""
SNMP Worker — Standalone process that executes poll tasks from the DB queue.

Architecture:
    Scheduler → PollTask(status='pending') → Worker → Execute → PollTask(status='done')

Concurrency Safety:
    Uses SELECT FOR UPDATE SKIP LOCKED to prevent duplicate execution
    across multiple worker instances.

Usage:
    python workers/snmp_worker.py              # Production mode
    python workers/snmp_worker.py --dry-run    # Preview mode (no SNMP calls)

Requires:
    - Flask app context (imports app factory)
    - PostgreSQL for SKIP LOCKED support
"""
import sys
import os
import time
import signal
import logging
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path so imports work when running standalone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from extensions import db
from sqlalchemy import text
from config import Config
from flask import Flask

# ── Robust App Factory for Worker ──
def create_worker_app():
    """
    Minimal app factory for the worker process.
    Bypasses `app.py` to avoid side-effects (print statements, complex init)
    that cause crashes in CLI/subprocess environments.
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
BATCH_SIZE = 20
MAX_WORKERS = 20
SLEEP_INTERVAL = 1  # seconds between polling cycles when idle
STALE_TASK_MINUTES = 15  # tasks stuck in 'running' longer than this are reclaimed

log = logging.getLogger('snmp_worker')


# ─────────────────────────────────────────────
# Graceful Shutdown
# ─────────────────────────────────────────────
_shutdown_requested = False

def _signal_handler(signum, frame):
    global _shutdown_requested
    log.info(f"[WORKER] Shutdown signal received (signal={signum}). Finishing current batch...")
    _shutdown_requested = True

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─────────────────────────────────────────────
# Task Fetching (Concurrency-Safe)
# ─────────────────────────────────────────────
def fetch_pending_tasks(limit=BATCH_SIZE):
    """
    Atomically claim a batch of pending tasks.

    Uses SELECT FOR UPDATE SKIP LOCKED to:
      - Lock rows so other workers can't pick the same tasks
      - SKIP already-locked rows (non-blocking)

    Returns list of PollTask IDs that were marked 'running'.
    """
    now = datetime.utcnow()

    # Raw SQL for FOR UPDATE SKIP LOCKED (not available in SQLAlchemy ORM easily)
    select_sql = text("""
        SELECT id FROM poll_tasks
        WHERE status = 'pending'
          AND next_run_at <= :now
        ORDER BY priority ASC, created_at ASC
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    """)

    update_sql = text("""
        UPDATE poll_tasks
        SET status = 'running', started_at = :now
        WHERE id = ANY(:ids)
    """)

    try:
        result = db.session.execute(select_sql, {'now': now, 'limit': limit})
        task_ids = [row[0] for row in result]

        if task_ids:
            db.session.execute(update_sql, {'now': now, 'ids': task_ids})
            db.session.commit()
            log.info(f"[WORKER] Claimed {len(task_ids)} tasks: {task_ids}")
        return task_ids

    except Exception as e:
        db.session.rollback()
        log.error(f"[WORKER] Error fetching tasks: {e}")
        return []


def reclaim_stale_tasks():
    """
    Recover tasks stuck in 'running' state (worker crashed mid-execution).
    Re-queues them as 'pending' if under retry limit.
    """
    cutoff = datetime.utcnow()
    cutoff_sql = text("""
        UPDATE poll_tasks
        SET status = 'pending',
            started_at = NULL,
            retry_count = retry_count + 1,
            next_run_at = NOW() + INTERVAL '5 seconds'
        WHERE status = 'running'
          AND started_at < :cutoff - INTERVAL ':minutes minutes'
          AND retry_count < 3
    """.replace(':minutes', str(STALE_TASK_MINUTES)))

    try:
        result = db.session.execute(cutoff_sql, {'cutoff': cutoff})
        if result.rowcount > 0:
            db.session.commit()
            log.warning(f"[WORKER] Reclaimed {result.rowcount} stale tasks")
    except Exception as e:
        db.session.rollback()
        log.error(f"[WORKER] Error reclaiming stale tasks: {e}")


# ─────────────────────────────────────────────
# Task Execution
# ─────────────────────────────────────────────
def execute_task(app, task_id, dry_run=False):
    """
    Execute a single poll task within Flask app context.

    Routes by task_type:
        snmp_health → poll device health + AlertManager thresholds
        interface   → poll interface counters
        discovery   → SNMP discovery enrichment

    Returns (task_id, success: bool, error_code, error_message)
    """
    with app.app_context():
        try:
            from models.poll_task import PollTask
            from models.device import Device

            task = PollTask.query.get(task_id)
            if not task:
                return (task_id, False, 'TASK_NOT_FOUND', 'Task row missing')

            device = Device.query.get(task.device_id)
            if not device:
                return (task_id, False, 'DEVICE_NOT_FOUND', f'Device {task.device_id} not found')

            if dry_run:
                log.info(
                    f"[DRY-RUN] Would execute {task.task_type} "
                    f"for {device.device_name} ({device.device_ip})"
                )
                return (task_id, True, None, None)

            # ── Route by task_type ────────────────────────────
            if task.task_type == 'snmp_health':
                return _execute_snmp_health(task_id, device)
            elif task.task_type == 'interface':
                return _execute_interface_poll(task_id, device)
            elif task.task_type == 'discovery':
                return _execute_discovery(task_id, device)
            elif task.task_type == 'config_backup':
                return _execute_config_backup(task_id, device)
            else:
                return (task_id, False, 'UNKNOWN_TASK_TYPE', f'Unknown type: {task.task_type}')

        except Exception as e:
            log.error(f"[WORKER] Unhandled error in task {task_id}: {e}")
            return (task_id, False, 'UNKNOWN_ERROR', str(e)[:500])
        finally:
            db.session.remove()


def _execute_snmp_health(task_id, device):
    """Execute SNMP health poll + threshold evaluation."""
    from models.snmp_config import DeviceSnmpConfig
    from models.server_health import ServerHealthLog
    from services.snmp_service import snmp_service
    from services.alert_manager import AlertManager

    # Get SNMP config
    config = DeviceSnmpConfig.query.filter_by(
        device_id=device.device_id, is_enabled=True
    ).first()

    if not config:
        return (task_id, False, 'SNMP_NOT_CONFIGURED', 'No SNMP config for device')

    community = config.community_string or 'public'
    version = config.snmp_version or '2c'
    port = config.snmp_port or 161

    # Poll health metrics
    metrics = snmp_service.get_server_health_snmp(
        device.device_ip, community, version, port
    )

    if not metrics:
        # Check if it's a classified error by attempting sys info
        sys_info = snmp_service.get_system_info(
            device.device_ip, community, version, port
        )
        error_code = sys_info.get('error_code', 'SNMP_NO_DATA')
        error_msg = sys_info.get('error', 'No metrics returned')
        config.last_poll_error = f"[{error_code}] {error_msg}"
        db.session.commit()
        return (task_id, False, error_code, error_msg)

    # Fetch uptime for log completeness
    sys_info = snmp_service.get_system_info(
        device.device_ip, community, version, port
    )
    _raw_uptime = sys_info.get('sys_uptime_seconds')
    uptime = str(int(float(_raw_uptime))) if _raw_uptime not in (None, '') else None

    # Store health log
    health_log = ServerHealthLog(
        device_id=device.device_id,
        cpu_usage=metrics.get('cpu_usage'),
        memory_usage=metrics.get('memory_usage'),
        disk_usage=metrics.get('disk_usage'),
        uptime=uptime,
        source='snmp'
    )
    db.session.add(health_log)

    # Update SNMP config tracking
    config.last_successful_poll = datetime.utcnow()
    if sys_info.get('error_code'):
        config.last_poll_error = f"[{sys_info['error_code']}] {sys_info.get('error', '')}"
    else:
        config.last_poll_error = None

    # ── Evaluate thresholds via AlertManager ──
    AlertManager.check_server_health(device, health_log, commit=False)

    db.session.commit()

    log.info(
        f"[WORKER] SNMP health OK for {device.device_ip}: "
        f"CPU={metrics.get('cpu_usage')}% "
        f"RAM={metrics.get('memory_usage')}% "
        f"Disk={metrics.get('disk_usage')}%"
    )
    return (task_id, True, None, None)


def _execute_interface_poll(task_id, device):
    """Execute interface counter poll (placeholder for Phase 2 wiring)."""
    from services.interface_poller import interface_poller

    result = interface_poller.poll_device_interfaces(device.device_id)

    if result.get('success'):
        return (task_id, True, None, None)
    else:
        return (task_id, False, 'INTERFACE_POLL_FAILED', result.get('error', 'Unknown'))


def _execute_config_backup(task_id, device):
    """Execute SSH config capture and persist as DeviceConfigSnapshot."""
    from services.config_backup_service import capture_config

    result = capture_config(
        device_id=device.device_id,
        source='scheduled',
        user_id=None,
    )

    if result.get('success'):
        log.info(
            "[WORKER] config_backup: device_id=%d (%s) snapshot_id=%d changed=%s",
            device.device_id, device.device_ip,
            result['snapshot_id'], result['changed'],
        )
        return (task_id, True, None, None)
    else:
        error = result.get('error', 'unknown')
        log.warning(
            "[WORKER] config_backup: device_id=%d (%s) failed — %s",
            device.device_id, device.device_ip, error,
        )
        return (task_id, False, 'CONFIG_BACKUP_FAILED', error)


def _execute_discovery(task_id, device):
    """Execute SNMP discovery enrichment for a device."""
    from models.snmp_config import DeviceSnmpConfig
    from services.snmp_service import snmp_service

    config = DeviceSnmpConfig.query.filter_by(
        device_id=device.device_id, is_enabled=True
    ).first()

    if not config:
        return (task_id, False, 'SNMP_NOT_CONFIGURED', 'No SNMP config')

    community = config.community_string or 'public'
    version = config.snmp_version or '2c'
    port = config.snmp_port or 161

    sys_info = snmp_service.get_system_info(
        device.device_ip, community, version, port
    )

    if sys_info.get('error'):
        error_code = sys_info.get('error_code', 'SNMP_ERROR')
        return (task_id, False, error_code, sys_info['error'])

    # Enrich device with SNMP data
    changed = False
    sys_name = sys_info.get('sys_name', '')
    if sys_name and (not device.hostname or device.hostname in ('Unknown', 'N/A', '')):
        device.hostname = sys_name
        changed = True

    if changed:
        db.session.commit()

    return (task_id, True, None, None)


# ─────────────────────────────────────────────
# Result Processing
# ─────────────────────────────────────────────
def finalize_task(app, task_id, success, error_code, error_message):
    """Update task status after execution."""
    with app.app_context():
        try:
            from models.poll_task import PollTask
            task = PollTask.query.get(task_id)
            if not task:
                return

            if success:
                task.mark_done()
            else:
                task.mark_failed(error_code or 'UNKNOWN_ERROR', error_message or '')

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            log.error(f"[WORKER] Error finalizing task {task_id}: {e}")
        finally:
            db.session.remove()


# ─────────────────────────────────────────────
# Main Worker Loop
# ─────────────────────────────────────────────
def run_worker(app, dry_run=False):
    """
    Main worker loop.

    1. Reclaim stale tasks (crashed workers)
    2. Fetch batch of pending tasks (FOR UPDATE SKIP LOCKED)
    3. Execute in ThreadPoolExecutor
    4. Finalize results
    5. Sleep if idle
    """
    log.info(f"[WORKER] Starting SNMP worker (dry_run={dry_run}, workers={MAX_WORKERS})")

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    cycle = 0

    try:
        while not _shutdown_requested:
            with app.app_context():
                # Periodic stale task reclaim (every 60 cycles ~ 1 min)
                cycle += 1
                if cycle % 60 == 0:
                    reclaim_stale_tasks()

                # Fetch batch
                task_ids = fetch_pending_tasks(limit=BATCH_SIZE)

            if not task_ids:
                time.sleep(SLEEP_INTERVAL)
                continue

            # Submit to thread pool
            futures = {
                executor.submit(execute_task, app, tid, dry_run): tid
                for tid in task_ids
            }

            # Collect results
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    task_id, success, error_code, error_message = future.result(timeout=30)
                    finalize_task(app, task_id, success, error_code, error_message)
                except Exception as e:
                    log.error(f"[WORKER] Task {tid} raised: {e}")
                    finalize_task(app, tid, False, 'WORKER_EXCEPTION', str(e)[:500])

    except KeyboardInterrupt:
        log.info("[WORKER] Keyboard interrupt. Shutting down...")
    finally:
        executor.shutdown(wait=True, cancel_futures=False)
        log.info("[WORKER] Shutdown complete.")


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
def setup_logging():
    """Configure logging for standalone worker process."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SNMP Worker Process')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview mode: fetch and log tasks without executing SNMP calls'
    )
    args = parser.parse_args()

    app = create_worker_app()

    with app.app_context():
        # Ensure poll_tasks table exists
        # Catch errors here to avoid crashing if specialized binds fail
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            if not inspector.has_table('poll_tasks'):
                print("[WORKER] poll_tasks table missing, attempting creation...")
                db.create_all()
            else:
                print("[WORKER] poll_tasks table exists.")
        except Exception as e:
            print(f"[WORKER] Warning: Table check/creation failed: {e}")
            print("[WORKER] Proceeding anyway (assuming manual migration ran).")

    run_worker(app, dry_run=args.dry_run)
