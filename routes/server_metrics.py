from flask import Blueprint, jsonify, request
from extensions import db
from models.server_health import ServerHealthLog
from models.device import Device
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from utils.server_health import compute_server_health, is_server_device
from middleware.rbac import require_login

server_metrics_bp = Blueprint('server_metrics_bp', __name__)


@server_metrics_bp.before_request
@require_login
def _server_metrics_auth_guard():
    return None


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


def _iso_utc(ts):
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


@server_metrics_bp.route('/api/server/fleet-metrics')
def get_fleet_metrics():
    try:
        # 1. Identify active servers (last 24h)
        cutoff = datetime.utcnow() - timedelta(hours=24)
        
        # Latest log per device
        latest_subq = db.session.query(
            ServerHealthLog.device_id,
            func.max(ServerHealthLog.id).label('max_id')
        ).filter(
            ServerHealthLog.source == 'agent',
            ServerHealthLog.timestamp >= cutoff
        ).group_by(ServerHealthLog.device_id).subquery()

        latest_logs = db.session.query(ServerHealthLog).join(
            latest_subq,
            ServerHealthLog.id == latest_subq.c.max_id
        ).all()
        
        # 2. Calculate Aggregates
        total_servers = len(latest_logs)
        if total_servers == 0:
            return jsonify({
                'health': {'total': 0, 'healthy': 0, 'warning': 0, 'critical': 0, 'offline': 0},
                'aggregates': {'cpu': 0, 'memory': 0, 'disk': 0},
                'p95': {'cpu': 0, 'memory': 0},
                'alerts': [],
                'trends': {'cpu': [], 'memory': [], 'labels': []}
            })

        health_counts = {'total': total_servers, 'healthy': 0, 'warning': 0, 'critical': 0, 'offline': 0}
        cpu_values = []
        mem_values = []
        disk_values = []
        critical_servers = []

        for log in latest_logs:
            # Health Counts
            health = compute_server_health(log)
            health_lower = health.lower()
            if health_lower in health_counts:
                health_counts[health_lower] += 1
            else:
                health_counts['offline'] += 1

            # Metric Collections
            if log.cpu_usage is not None: cpu_values.append(log.cpu_usage)
            if log.memory_usage is not None: mem_values.append(log.memory_usage)
            if log.disk_usage is not None: disk_values.append(log.disk_usage)

            # Check for critical thresholds (for Alert Bar)
            alerts = []
            if log.cpu_usage and log.cpu_usage > 80: alerts.append(f"CPU {log.cpu_usage:.1f}%")
            if log.memory_usage and log.memory_usage > 85: alerts.append(f"Mem {log.memory_usage:.1f}%")
            if log.disk_usage and log.disk_usage > 90: alerts.append(f"Disk {log.disk_usage:.1f}%")
            
            if alerts:
                device = Device.query.get(log.device_id)
                critical_servers.append({
                    'name': device.device_name if device else f"ID {log.device_id}",
                    'alerts': alerts
                })

        # Calculate Averages
        avg_cpu = sum(cpu_values) / len(cpu_values) if cpu_values else 0
        avg_mem = sum(mem_values) / len(mem_values) if mem_values else 0
        avg_disk = sum(disk_values) / len(disk_values) if disk_values else 0

        # Calculate P95 (Capacity Planning)
        def calc_p95(values):
            if not values: return 0
            values.sort()
            idx = int(len(values) * 0.95)
            return values[min(idx, len(values)-1)]

        p95_cpu = calc_p95(cpu_values)
        p95_mem = calc_p95(mem_values)

        # 3. Trends (24h Aggregate Sparklines)
        # Group by hour and take average of all servers.
        # DB session timezone is forced to UTC at connection time.
        hour_bucket = func.date_trunc('hour', ServerHealthLog.timestamp).label('hour')
        trend_query = db.session.query(
            hour_bucket,
            func.avg(ServerHealthLog.cpu_usage).label('avg_cpu'),
            func.avg(ServerHealthLog.memory_usage).label('avg_mem')
        ).filter(
            ServerHealthLog.source == 'agent',
            ServerHealthLog.timestamp >= cutoff
        ).group_by(hour_bucket).order_by(hour_bucket).all()

        trend_labels = [_iso_utc(row.hour) for row in trend_query]
        trend_cpu = [float(row.avg_cpu) if row.avg_cpu else 0 for row in trend_query]
        trend_mem = [float(row.avg_mem) if row.avg_mem else 0 for row in trend_query]

        return jsonify({
            'health': health_counts,
            'aggregates': {
                'cpu': round(avg_cpu, 1),
                'memory': round(avg_mem, 1),
                'disk': round(avg_disk, 1)
            },
            'p95': {
                'cpu': round(p95_cpu, 1),
                'memory': round(p95_mem, 1)
            },
            'alerts': critical_servers,
            'trends': {
                'labels': trend_labels,
                'cpu': trend_cpu,
                'memory': trend_mem
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@server_metrics_bp.route('/api/server/health')
def get_server_health_summary():
    # Original logic continues below...
    try:
        # Latest agent log per device
        latest_subq = db.session.query(
            ServerHealthLog.device_id,
            func.max(ServerHealthLog.id).label('max_id')
        ).filter(
            ServerHealthLog.source == 'agent'
        ).group_by(ServerHealthLog.device_id).subquery()
        
        # ... (rest of the existing function)
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
            Device.device_id.in_(agent_device_ids)
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

            # Enhanced Server List Metrics (CPU/Mem with trends)
            # Simple trend simulation: compare current with previous (not implemented here for speed)
            # Just returning raw values for frontend arrows
            
            server_list.append({
                'device_id': device.device_id,
                'device_name': device.device_name,
                'hostname': device.hostname,
                'ip': device.device_ip,
                'health': health,
                'last_seen': _iso_utc(log.timestamp) if log and log.timestamp else None,
                # New enhanced columns
                'cpu_usage': log.cpu_usage,
                'memory_usage': log.memory_usage,
                'disk_usage': log.disk_usage,
                'os': log.os_name
            })

        return jsonify({
            'timestamp': _iso_utc(datetime.utcnow()),
            'counts': counts,
            'servers': server_list
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@server_metrics_bp.route('/api/server/<int:device_id>/metrics')
def get_server_metrics(device_id):
    time_range = request.args.get('range', '24h')
    
    # Determine cutoff
    if time_range == '15m':
        cutoff = datetime.utcnow() - timedelta(minutes=15)
    elif time_range == '1h':
        cutoff = datetime.utcnow() - timedelta(hours=1)
    elif time_range == '6h':
        cutoff = datetime.utcnow() - timedelta(hours=6)
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

        logs_q = ServerHealthLog.query.filter(
            ServerHealthLog.device_id == device_id,
            ServerHealthLog.timestamp >= cutoff,
            ServerHealthLog.source == 'agent'
        ).order_by(ServerHealthLog.timestamp.desc())

        labels = []
        cpu_data = []
        mem_data = []
        disk_data = []
        net_in_data = []
        net_out_data = []
        
        # Downsample to avoid UI freezes on large datasets
        max_points = 300
        if time_range == '15m':
            max_points = 120
        elif time_range == '1h':
            max_points = 120
        elif time_range == '6h':
            max_points = 240
        elif time_range == '7d':
            max_points = 336

        # Only pull the most recent window to keep payloads small
        logs = logs_q.limit(max_points * 4).all()
        logs.reverse()

        last_log = logs[-1] if logs else None
        hardware_specs = device.hardware_specs if isinstance(device.hardware_specs, dict) else {}
        if not hardware_specs and last_log:
            hardware_specs = {
                'memory_total_gb': last_log.memory_total_gb,
                'disk_total_gb': last_log.disk_total_gb,
                'architecture': last_log.os_arch
            }

        buckets = _downsample_logs(logs, max_points)
        for bucket in buckets:
            if isinstance(bucket, list):
                labels.append(_iso_utc(bucket[-1].timestamp))
                cpu_data.append(_avg([b.cpu_usage for b in bucket]))
                mem_data.append(_avg([b.memory_usage for b in bucket]))
                disk_data.append(_avg([b.disk_usage for b in bucket]))
                net_in_data.append(_avg([b.network_in_bps for b in bucket]))
                net_out_data.append(_avg([b.network_out_bps for b in bucket]))
            else:
                labels.append(_iso_utc(bucket.timestamp))
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
            'last_seen': _iso_utc(last_log.timestamp) if last_log and last_log.timestamp else None,
            'os': {
                'name': last_log.os_name if last_log else None,
                'version': last_log.os_version if last_log else None,
                'arch': last_log.os_arch if last_log else None
            },
            'hardware_specs': hardware_specs,
            'health': health,
            'cpu_iowait_percent': last_log.cpu_iowait_percent if last_log else None,
            'cpu_steal_percent': last_log.cpu_steal_percent if last_log else None,
            # Enhanced metrics (latest values)
            'load_average': {
                '1min': last_log.load_avg_1min if last_log else None,
                '5min': last_log.load_avg_5min if last_log else None,
                '15min': last_log.load_avg_15min if last_log else None
            },
            'swap': {
                'total_mb': last_log.swap_total_mb if last_log else None,
                'used_mb': last_log.swap_used_mb if last_log else None,
                'percent': last_log.swap_percent if last_log else None
            },
            'memory_detail': {
                'used_gb': last_log.memory_used_gb if last_log else None,
                'total_gb': last_log.memory_total_gb if last_log else None,
                'page_faults_per_sec': last_log.page_faults_per_sec if last_log else None
            },
            'disk_detail': {
                'used_gb': last_log.disk_used_gb if last_log else None,
                'free_gb': last_log.disk_free_gb if last_log else None,
                'total_gb': last_log.disk_total_gb if last_log else None
            },
            'disk_io': {
                'read_bytes': last_log.disk_read_bytes if last_log else None,
                'write_bytes': last_log.disk_write_bytes if last_log else None,
                'read_count': last_log.disk_read_count if last_log else None,
                'write_count': last_log.disk_write_count if last_log else None,
                'read_latency_ms': last_log.disk_read_latency_ms if last_log else None,
                'write_latency_ms': last_log.disk_write_latency_ms if last_log else None,
                'busy_percent': last_log.disk_busy_percent if last_log else None
            },
            'network_connections': {
                'total': last_log.network_connections_total if last_log else None,
                'established': last_log.network_connections_established if last_log else None,
                'tcp_retransmits_delta': last_log.tcp_retransmits_delta if last_log else None
            },
            'network_per_interface': last_log.network_per_interface if last_log and last_log.network_per_interface else {},
            'processes': {
                'total': last_log.process_count if last_log else None,
                'zombie': last_log.zombie_count if last_log else None,
                'context_switches_per_sec': last_log.context_switches_per_sec if last_log else None,
                'open_fds': last_log.open_fds if last_log else None,
                'fd_limit': last_log.fd_limit if last_log else None,
                'fd_percent': last_log.fd_percent if last_log else None
            },
            'top_processes': last_log.top_processes if last_log and last_log.top_processes else [],
            'top_processes_cpu': last_log.top_processes_cpu if last_log and last_log.top_processes_cpu else [],
            'alerts': last_log.alerts if last_log and last_log.alerts else []
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
