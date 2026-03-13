"""Maintenance API routes for Network Monitoring System.
Provides endpoints to trigger and monitor database maintenance tasks
and manage device maintenance windows.
"""
import logging
from flask import Blueprint, jsonify, request, render_template
from datetime import datetime, date, timezone
from extensions import db
from middleware.rbac import require_login, require_role

logger = logging.getLogger(__name__)

maintenance_bp = Blueprint('maintenance_bp', __name__, url_prefix='/api/maintenance')


# ============================================================
# Page route — Maintenance Window UI
# ============================================================
@maintenance_bp.route('/window')
@require_login
def maintenance_page():
    """Render the maintenance window management page."""
    return render_template('maintenance_window.html')


# ============================================================
# POST /api/maintenance/cleanup
# ============================================================
@maintenance_bp.route('/cleanup', methods=['POST'])
@require_role('admin')
def run_cleanup():
    """
    Run database cleanup tasks.
    Body (optional):
    {
      scan_days,
      metrics_days,
      events_days,
      server_health_raw_days,
      server_health_hourly_days,
      server_health_daily_days
    }
    """
    try:
        from services.maintenance_service import maintenance_service
        
        data = request.get_json() or {}
        
        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'tasks': {}
        }
        
        # Cleanup scan history
        scan_days = data.get('scan_days', 7)
        results['tasks']['scan_history'] = maintenance_service.cleanup_old_scan_history(scan_days)
        
        # Cleanup interface metrics
        metrics_days = data.get('metrics_days', 3)
        results['tasks']['interface_metrics'] = maintenance_service.cleanup_old_interface_metrics(metrics_days)
        
        # Cleanup events
        events_days = data.get('events_days', 30)
        results['tasks']['events'] = maintenance_service.cleanup_old_events(events_days)

        # Server health retention cleanup (raw/hourly/daily)
        server_health_raw_days = data.get('server_health_raw_days', 7)
        server_health_hourly_days = data.get('server_health_hourly_days', 30)
        server_health_daily_days = data.get('server_health_daily_days', 365)
        results['tasks']['server_health_raw'] = maintenance_service.cleanup_old_server_health_logs(server_health_raw_days)
        results['tasks']['server_health_hourly'] = maintenance_service.cleanup_old_server_health_hourly_rollups(server_health_hourly_days)
        results['tasks']['server_health_daily'] = maintenance_service.cleanup_old_server_health_daily_rollups(server_health_daily_days)
        
        return jsonify(results)
        
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# POST /api/maintenance/aggregate
# ============================================================
@maintenance_bp.route('/aggregate', methods=['POST'])
@require_role('admin')
def run_aggregation():
    """
    Run daily stats aggregation.
    Body (optional): { date: "YYYY-MM-DD" }
    """
    try:
        from services.maintenance_service import maintenance_service
        
        data = request.get_json() or {}
        target_date = None
        
        if 'date' in data:
            target_date = date.fromisoformat(data['date'])
        
        result = maintenance_service.aggregate_daily_stats(target_date)
        
        return jsonify(result)
        
    except ValueError as e:
        return jsonify({'error': f'Invalid date format: {e}'}), 400
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# POST /api/maintenance/run-all
# ============================================================
@maintenance_bp.route('/run-all', methods=['POST'])
@require_role('admin')
def run_all_maintenance():
    """Run all maintenance tasks (aggregation + cleanup)."""
    try:
        from services.maintenance_service import maintenance_service
        
        result = maintenance_service.run_all_maintenance()
        
        return jsonify(result)
        
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# POST /api/maintenance/backfill-rollups
# ============================================================
@maintenance_bp.route('/backfill-rollups', methods=['POST'])
@require_role('admin')
def backfill_rollups():
    """Backfill reporting rollups across daily stats, server health, and tracking."""
    try:
        from services.maintenance_service import maintenance_service

        data = request.get_json() or {}
        days = int(data.get('days', 90) or 90)
        rebuild_daily_stats = bool(data.get('rebuild_daily_stats', False))

        result = maintenance_service.backfill_reporting_rollups(
            days=days,
            rebuild_daily_stats=rebuild_daily_stats,
        )
        return jsonify(result)
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/admin/scheduler/status
# ============================================================
@maintenance_bp.route('/scheduler/status', methods=['GET'])
@require_role('admin')
def scheduler_status():
    """
    Return live scheduler job health — last_run, next_run, and status
    for every registered rollup job.

    Status values:
      "ok"        — last_run within 2× the job interval
      "late"      — last_run overdue (app is running but job missed its window)
      "never_run" — job has not completed a single run since the app booted
    """
    from services.scheduler import get_scheduler_status
    jobs = get_scheduler_status()
    any_late = any(j['status'] == 'late' for j in jobs)
    any_never = any(j['status'] == 'never_run' for j in jobs)
    if any_late:
        overall = 'degraded'
    elif any_never:
        overall = 'pending'  # normal on fresh boot before first scheduled window
    else:
        overall = 'ok'
    return jsonify({
        'overall': overall,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'jobs': jobs,
    })


# ============================================================
# GET /api/maintenance/status
# ============================================================
@maintenance_bp.route('/status')
@require_login
def get_maintenance_status():
    """
    Get database statistics for maintenance monitoring.
    Returns counts and oldest records for each table.
    """
    try:
        from models.scan_history import DeviceScanHistory
        from models.interfaces import InterfaceTrafficHistory
        from models.dashboard import DashboardEvent, DailyDeviceStats
        from models.server_health import ServerHealthLog
        from models.server_health_rollups import (
            ServerHealthHourlyRollup,
            ServerHealthDailyRollup,
            ServerHealthRollupState
        )
        from models.tracked_device import (
            DeviceActivityLog,
            DeviceApplicationLog,
            TrackingDailyRollup,
            TrackingHourlyRollup,
            TrackingSample,
        )
        
        status = {
            'timestamp': datetime.utcnow().isoformat(),
            'tables': {}
        }
        
        # Scan history stats
        scan_count = DeviceScanHistory.query.count()
        oldest_scan = DeviceScanHistory.query.order_by(
            DeviceScanHistory.scan_timestamp
        ).first()
        
        status['tables']['scan_history'] = {
            'count': scan_count,
            'oldest_record': oldest_scan.scan_timestamp.isoformat() if oldest_scan and oldest_scan.scan_timestamp else None
        }
        
        # Interface metrics stats
        metrics_count = InterfaceTrafficHistory.query.count()
        oldest_metric = InterfaceTrafficHistory.query.order_by(
            InterfaceTrafficHistory.timestamp
        ).first()
        
        status['tables']['interface_metrics'] = {
            'count': metrics_count,
            'oldest_record': oldest_metric.timestamp.isoformat() if oldest_metric and oldest_metric.timestamp else None
        }

        # Server health raw logs
        server_health_count = ServerHealthLog.query.count()
        oldest_server_health = ServerHealthLog.query.order_by(
            ServerHealthLog.timestamp
        ).first()
        status['tables']['server_health_logs'] = {
            'count': server_health_count,
            'oldest_record': oldest_server_health.timestamp.isoformat() if oldest_server_health and oldest_server_health.timestamp else None
        }

        # Server health hourly rollups
        hourly_rollup_count = ServerHealthHourlyRollup.query.count()
        oldest_hourly_rollup = ServerHealthHourlyRollup.query.order_by(
            ServerHealthHourlyRollup.bucket_hour
        ).first()
        status['tables']['server_health_hourly_rollups'] = {
            'count': hourly_rollup_count,
            'oldest_record': oldest_hourly_rollup.bucket_hour.isoformat() if oldest_hourly_rollup and oldest_hourly_rollup.bucket_hour else None
        }

        # Server health daily rollups
        daily_rollup_count = ServerHealthDailyRollup.query.count()
        oldest_daily_rollup = ServerHealthDailyRollup.query.order_by(
            ServerHealthDailyRollup.bucket_day
        ).first()
        status['tables']['server_health_daily_rollups'] = {
            'count': daily_rollup_count,
            'oldest_record': oldest_daily_rollup.bucket_day.isoformat() if oldest_daily_rollup and oldest_daily_rollup.bucket_day else None
        }

        # Rollup checkpoints
        rollup_states = ServerHealthRollupState.query.order_by(ServerHealthRollupState.name).all()
        status['tables']['server_health_rollup_state'] = {
            'count': len(rollup_states),
            'states': [
                {
                    'name': state.name,
                    'rolled_until': state.rolled_until.isoformat() if state.rolled_until else None
                }
                for state in rollup_states
            ]
        }
        
        # Events stats
        events_count = DashboardEvent.query.count()
        unresolved_count = DashboardEvent.query.filter_by(resolved=False).count()
        
        status['tables']['dashboard_events'] = {
            'count': events_count,
            'unresolved': unresolved_count
        }
        
        # Daily stats
        daily_count = DailyDeviceStats.query.count()
        latest_stat = DailyDeviceStats.query.order_by(
            DailyDeviceStats.date.desc()
        ).first()
        
        status['tables']['daily_device_stats'] = {
            'count': daily_count,
            'latest_date': latest_stat.date.isoformat() if latest_stat and latest_stat.date else None
        }

        tracking_sample_count = TrackingSample.query.count()
        latest_tracking_sample = TrackingSample.query.order_by(TrackingSample.received_at.desc()).first()
        status['tables']['tracking_samples'] = {
            'count': tracking_sample_count,
            'latest_record': latest_tracking_sample.received_at.isoformat() if latest_tracking_sample and latest_tracking_sample.received_at else None
        }

        tracking_activity_count = DeviceActivityLog.query.count()
        latest_tracking_activity = DeviceActivityLog.query.order_by(DeviceActivityLog.timestamp.desc()).first()
        status['tables']['device_activity_logs'] = {
            'count': tracking_activity_count,
            'latest_record': latest_tracking_activity.timestamp.isoformat() if latest_tracking_activity and latest_tracking_activity.timestamp else None
        }

        tracking_app_count = DeviceApplicationLog.query.count()
        latest_tracking_app = DeviceApplicationLog.query.order_by(DeviceApplicationLog.timestamp.desc()).first()
        status['tables']['device_application_logs'] = {
            'count': tracking_app_count,
            'latest_record': latest_tracking_app.timestamp.isoformat() if latest_tracking_app and latest_tracking_app.timestamp else None
        }

        tracking_hourly_count = TrackingHourlyRollup.query.count()
        latest_tracking_hourly = TrackingHourlyRollup.query.order_by(TrackingHourlyRollup.bucket_hour.desc()).first()
        status['tables']['tracking_hourly_rollups'] = {
            'count': tracking_hourly_count,
            'latest_record': latest_tracking_hourly.bucket_hour.isoformat() if latest_tracking_hourly and latest_tracking_hourly.bucket_hour else None
        }

        tracking_daily_count = TrackingDailyRollup.query.count()
        latest_tracking_daily = TrackingDailyRollup.query.order_by(TrackingDailyRollup.bucket_day.desc()).first()
        status['tables']['tracking_daily_rollups'] = {
            'count': tracking_daily_count,
            'latest_record': latest_tracking_daily.bucket_day.isoformat() if latest_tracking_daily and latest_tracking_daily.bucket_day else None
        }

        tracking_rollup_states = ServerHealthRollupState.query.filter(
            ServerHealthRollupState.name.like('tracking_%')
        ).order_by(ServerHealthRollupState.name).all()
        status['tables']['tracking_rollup_state'] = {
            'count': len(tracking_rollup_states),
            'states': [
                {
                    'name': state.name,
                    'rolled_until': state.rolled_until.isoformat() if state.rolled_until else None
                }
                for state in tracking_rollup_states
            ]
        }

        return jsonify(status)
        
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/maintenance/devices  — List devices with maintenance status
# ============================================================
@maintenance_bp.route('/devices')
@require_login
def get_maintenance_devices():
    """Return all devices with their maintenance mode status."""
    try:
        from models.device import Device
        devices = Device.query.order_by(Device.device_name).all()

        result = []
        for d in devices:
            result.append({
                'device_id': d.device_id,
                'device_name': d.device_name,
                'device_ip': d.device_ip,
                'device_type': d.device_type,
                'is_active': d.is_active,
                'is_monitored': d.is_monitored,
                'maintenance_mode': getattr(d, 'maintenance_mode', False) or False,
                'health_alert_strikes': getattr(d, 'health_alert_strikes', 0) or 0,
            })

        return jsonify({'devices': result})

    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/maintenance/windows
# ============================================================
@maintenance_bp.route('/windows')
@require_login
def get_maintenance_windows():
    """Get the schedule of maintenance windows."""
    try:
        from services.maintenance_window_service import maintenance_window_service
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        windows = maintenance_window_service.list_windows(include_inactive=include_inactive)
        
        return jsonify({'windows': [w.to_dict() for w in windows]})
    except Exception:
        logger.exception("Maintenance endpoint error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# POST /api/maintenance/schedule
# ============================================================
@maintenance_bp.route('/schedule', methods=['POST'])
@require_role('admin')
def schedule_maintenance():
    """Schedule a new maintenance window."""
    try:
        from services.maintenance_window_service import maintenance_window_service
        from flask import session
        
        data = request.get_json() or {}
        device_id = data.get('device_id')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        reason = data.get('reason')
        
        if not all([device_id, start_time_str, end_time_str]):
            return jsonify({'error': 'device_id, start_time, and end_time are required'}), 400
            
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
        
        # Remove tzinfo for storing in DB (we assume UTC across the board)
        start_time = start_time.replace(tzinfo=None)
        end_time = end_time.replace(tzinfo=None)
        
        created_by = session.get('username') or session.get('user_id') or 'admin'
        
        window = maintenance_window_service.schedule_window(
            device_id=device_id,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_by=created_by
        )
        
        return jsonify({
            'success': True,
            'message': 'Maintenance window scheduled successfully',
            'window': window.to_dict()
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception:
        logger.exception("Maintenance endpoint error")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# POST /api/maintenance/windows/<id>/cancel
# ============================================================
@maintenance_bp.route('/windows/<int:window_id>/cancel', methods=['POST'])
@require_role('admin')
def cancel_maintenance_window(window_id):
    """Cancel an active scheduled maintenance window."""
    try:
        from services.maintenance_window_service import maintenance_window_service
        
        window = maintenance_window_service.cancel_window(window_id)
        
        return jsonify({
            'success': True,
            'message': 'Maintenance window cancelled successfully',
            'window': window.to_dict()
        })
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except Exception:
        logger.exception("Maintenance endpoint error")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500



# ============================================================
# POST /api/maintenance/toggle  — Toggle maintenance mode
# ============================================================
@maintenance_bp.route('/toggle', methods=['POST'])
@require_role('admin')
def toggle_maintenance():
    """Toggle maintenance_mode for a device.
    Body: { "device_id": int }
    """
    try:
        from models.device import Device
        from services.maintenance_window_service import maintenance_window_service
        from flask import session
        from datetime import timedelta

        data = request.get_json() or {}
        device_id = data.get('device_id')
        if not device_id:
            return jsonify({'error': 'device_id is required'}), 400

        device = Device.query.get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404

        device.maintenance_mode = not (device.maintenance_mode or False)

        # Reset strikes when toggling maintenance off
        if not device.maintenance_mode:
            device.health_alert_strikes = 0
            
            # Cancel any scheduled windows
            windows = maintenance_window_service.list_windows()
            for w in windows:
                if w.device_id == device.device_id and w.is_active:
                    maintenance_window_service.cancel_window(w.id)
        else:
            # Create a manual 24-hour window
            created_by = session.get('username') or session.get('user_id') or 'admin'
            maintenance_window_service.schedule_window(
                device_id=device.device_id,
                start_time=datetime.utcnow() - timedelta(minutes=1), # small buffer
                end_time=datetime.utcnow() + timedelta(hours=24),
                reason="Dashboard Quick Toggle",
                created_by=created_by
            )

        db.session.commit()

        action = 'enabled' if device.maintenance_mode else 'disabled'
        logger.info("[MAINTENANCE] %s for %s (%s)", action, device.device_name, device.device_ip)

        return jsonify({
            'success': True,
            'device_id': device.device_id,
            'maintenance_mode': device.maintenance_mode,
            'message': f"Maintenance mode {action} for {device.device_name}"
        })

    except Exception:
        logger.exception("Maintenance endpoint error")
        db.session.rollback()
        return jsonify({'error': 'Internal server error'}), 500

