from flask import Blueprint, jsonify, request, session
from extensions import db
from models.server_health import ServerHealthLog
from models.device import Device
from datetime import datetime, timedelta
from sqlalchemy import func
from utils.server_health import compute_server_health, is_server_device

server_metrics_bp = Blueprint('server_metrics_bp', __name__)


def _avg(values):
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _downsample_logs(logs, max_points):
    if len(logs) <= max_points:
        return logs
    step = max(1, int((len(logs) + max_points - 1) / max_points))
    buckets = []
    for i in range(0, len(logs), step):
        buckets.append(logs[i:i + step])
    return buckets


@server_metrics_bp.route('/api/server/health')
def get_server_health_summary():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        # Latest agent log per device
        latest_subq = db.session.query(
            ServerHealthLog.device_id,
            func.max(ServerHealthLog.id).label('max_id')
        ).filter(
            ServerHealthLog.source == 'agent'
        ).group_by(ServerHealthLog.device_id).subquery()

        latest_logs = db.session.query(ServerHealthLog).join(
            latest_subq,
            ServerHealthLog.id == latest_subq.c.max_id
        ).all()

        health_map = {log.device_id: log for log in latest_logs}
        agent_device_ids = list(health_map.keys())

        if not agent_device_ids:
            return jsonify({
                'timestamp': datetime.utcnow().isoformat(),
                'counts': {
                    'total': 0,
                    'healthy': 0,
                    'warning': 0,
                    'critical': 0,
                    'offline': 0
                },
                'servers': []
            })

        servers = Device.query.filter(
            Device.device_id.in_(agent_device_ids),
            func.lower(Device.device_type) == 'server'
        ).all()

        counts = {
            'total': 0,
            'healthy': 0,
            'warning': 0,
            'critical': 0,
            'offline': 0
        }

        server_list = []
        for device in servers:
            if not is_server_device(device.device_type):
                continue

            counts['total'] += 1
            log = health_map.get(device.device_id)
            health = compute_server_health(log)

            if health == 'Healthy':
                counts['healthy'] += 1
            elif health == 'Warning':
                counts['warning'] += 1
            elif health == 'Critical':
                counts['critical'] += 1
            else:
                counts['offline'] += 1

            server_list.append({
                'device_id': device.device_id,
                'device_name': device.device_name,
                'hostname': device.hostname,
                'ip': device.device_ip,
                'health': health,
                'last_seen': log.timestamp.isoformat() if log and log.timestamp else None,
            })

        return jsonify({
            'timestamp': datetime.utcnow().isoformat(),
            'counts': counts,
            'servers': server_list
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@server_metrics_bp.route('/api/server/<int:device_id>/metrics')
def get_server_metrics(device_id):
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    time_range = request.args.get('range', '24h')
    
    # Determine cutoff
    if time_range == '1h':
        cutoff = datetime.utcnow() - timedelta(hours=1)
    elif time_range == '7d':
        cutoff = datetime.utcnow() - timedelta(days=7)
    else:  # 24h
        cutoff = datetime.utcnow() - timedelta(hours=24)

    try:
        device = Device.query.get(device_id)
        if not device:
             return jsonify({'error': 'Device not found'}), 404
        if not is_server_device(device.device_type):
             return jsonify({'error': 'Device is not a server'}), 400

        logs = ServerHealthLog.query.filter(
            ServerHealthLog.device_id == device_id,
            ServerHealthLog.timestamp >= cutoff,
            ServerHealthLog.source == 'agent'
        ).order_by(ServerHealthLog.timestamp).all()

        labels = []
        cpu_data = []
        mem_data = []
        disk_data = []
        net_in_data = []
        net_out_data = []
        
        last_log = logs[-1] if logs else None

        # Downsample to avoid UI freezes on large datasets
        max_points = 300
        if time_range == '1h':
            max_points = 120
        elif time_range == '7d':
            max_points = 336

        buckets = _downsample_logs(logs, max_points)
        for bucket in buckets:
            if isinstance(bucket, list):
                labels.append(bucket[-1].timestamp.isoformat())
                cpu_data.append(_avg([b.cpu_usage for b in bucket]))
                mem_data.append(_avg([b.memory_usage for b in bucket]))
                disk_data.append(_avg([b.disk_usage for b in bucket]))
                net_in_data.append(_avg([b.network_in_bps for b in bucket]))
                net_out_data.append(_avg([b.network_out_bps for b in bucket]))
            else:
                labels.append(bucket.timestamp.isoformat())
                cpu_data.append(bucket.cpu_usage)
                mem_data.append(bucket.memory_usage)
                disk_data.append(bucket.disk_usage)
                net_in_data.append(bucket.network_in_bps)
                net_out_data.append(bucket.network_out_bps)
        health = compute_server_health(last_log)

        return jsonify({
            'labels': labels,
            'cpu': cpu_data,
            'memory': mem_data,
            'disk': disk_data,
            'net_in': net_in_data,
            'net_out': net_out_data,
            'device_name': device.device_name,
            'ip': device.device_ip,
            'hostname': device.hostname,
            'uptime': last_log.uptime if last_log else "N/A",
            'last_seen': last_log.timestamp.isoformat() if last_log and last_log.timestamp else None,
            'os': {
                'name': last_log.os_name if last_log else None,
                'version': last_log.os_version if last_log else None,
                'arch': last_log.os_arch if last_log else None
            },
            'health': health
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
