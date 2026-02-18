"""Maintenance API routes for Network Monitoring System.
Provides endpoints to trigger and monitor database maintenance tasks
and manage device maintenance windows.
"""
from flask import Blueprint, jsonify, request, render_template
from datetime import datetime, date
from extensions import db
from middleware.rbac import require_login, require_role

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
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        
        return jsonify(status)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

        db.session.commit()

        action = 'enabled' if device.maintenance_mode else 'disabled'
        print(f"[MAINTENANCE] {action} for {device.device_name} ({device.device_ip})")

        return jsonify({
            'success': True,
            'device_id': device.device_id,
            'maintenance_mode': device.maintenance_mode,
            'message': f"Maintenance mode {action} for {device.device_name}"
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
