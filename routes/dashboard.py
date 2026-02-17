"""
Dashboard API endpoints for Network Monitoring System.
Provides aggregated health, trends, and problem detection.
"""
from flask import Blueprint, jsonify, request, session
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, desc, case
from utils.server_health import compute_server_health, is_server_device
from extensions import db

dashboard_bp = Blueprint('dashboard_bp', __name__, url_prefix='/api/dashboard')

# ============================================================
# In-memory cache (simple TTL-based, no Redis required)
# ============================================================
_cache = {}
_cache_ttl = {}

def get_cached(key, ttl_seconds=30):
    """Get value from cache if not expired."""
    if key in _cache:
        if datetime.utcnow() < _cache_ttl.get(key, datetime.min):
            return _cache[key]
    return None

def set_cached(key, value, ttl_seconds=30):
    """Set value in cache with TTL."""
    _cache[key] = value
    _cache_ttl[key] = datetime.utcnow() + timedelta(seconds=ttl_seconds)


# ============================================================
# GET /api/dashboard/summary
# ============================================================
@dashboard_bp.route('/summary')
def get_summary():
    """
    Returns aggregated dashboard summary:
    - Device counts (up/down/degraded)
    - Network health (avg latency, packet loss)
    - Active alerts by severity
    Cache: 30s
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Check cache
    cached = get_cached('summary')
    if cached:
        return jsonify(cached)
    
    try:
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from models.dashboard import DashboardEvent
        
        # 1. Device Counts
        # Total Inventory (All devices)
        # Total Inventory (All devices)
        inventory_count = Device.query.count()
        
        # Maintenance Count
        maintenance_count = Device.query.filter_by(maintenance_mode=True).count()
        
        # DEBUG LOGGING
        db_path = db.engine.url.database
        import os
        print(f"[LIVE DEBUG] DB Path: {os.path.abspath(db_path)} | Count: {inventory_count}")
        
        # Monitored Devices (The denominator for availability)
        # USER REQUEST: Removed is_monitored filter to show ALL devices
        monitored_count = Device.query.count()
        # Fallback to avoid division by zero
        monitored_denominator = monitored_count if monitored_count > 0 else 1
        
        # Get latest scan per device
        latest_subq = db.session.query(
            DeviceScanHistory.device_ip,
            func.max(DeviceScanHistory.scan_id).label('max_id')
        ).group_by(DeviceScanHistory.device_ip).subquery()
        
        latest_scans = db.session.query(DeviceScanHistory).join(
            latest_subq,
            (DeviceScanHistory.device_ip == latest_subq.c.device_ip) &
            (DeviceScanHistory.scan_id == latest_subq.c.max_id)
        ).join(
            Device,
            Device.device_ip == DeviceScanHistory.device_ip
        ).all()
        
        healthy_count = 0
        degraded_count = 0
        offline_count = 0
        latencies = []
        packet_losses = []
        
        DEGRADED_LATENCY_THRESHOLD = 200  # ms
        DEGRADED_PACKET_LOSS_THRESHOLD = 5  # %
        
        scanned_ips = set()
        
        for scan in latest_scans:
            scanned_ips.add(scan.device_ip)
            status = (scan.status or '').lower()
            
            # Count as offline if status is explicitly 'offline' OR anything other than 'online'
            if status == 'offline' or status == 'unknown' or status == '':
                offline_count += 1
                continue
                
            # Only process if status is 'online'
            if status != 'online':
                # Non-standard status: count as offline
                offline_count += 1
                continue

            # Device is online - check if degraded
            is_degraded = False
            if scan.ping_time_ms and scan.ping_time_ms > DEGRADED_LATENCY_THRESHOLD:
                is_degraded = True
            if scan.packet_loss and scan.packet_loss > DEGRADED_PACKET_LOSS_THRESHOLD:
                is_degraded = True

            if is_degraded:
                degraded_count += 1
            else:
                healthy_count += 1

            if scan.ping_time_ms:
                latencies.append(scan.ping_time_ms)
            if scan.packet_loss is not None:
                packet_losses.append(scan.packet_loss)
        
        # CRITICAL FIX: Count devices without scan history as "Unknown"
        # Get all device IPs from Device table
        all_devices = Device.query.all()
        all_device_ips = set([d.device_ip for d in all_devices])
        
        # Devices without any scan history are truly "Unknown"
        devices_without_scans = all_device_ips - scanned_ips
        unknown_count = len(devices_without_scans)
        
        # Online count = healthy + degraded (only from scanned devices with status='online')
        online_count = healthy_count + degraded_count
        
        # If we have monitored devices that weren't in latest_scans, treat as Unknown/Offline
        # But for the "Online" metric, we strictly use confirmed online.
        
        # Instant Availability (Current State)
        # Cap at 100% to prevent display bugs (e.g., 300% when calculations are off)
        availability_live = min(round((online_count / monitored_denominator) * 100, 1), 100.0)

        # ----------------------------------------
        # Calculate Real 24h Availability (Average Uptime)
        # ----------------------------------------
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)
        
        # Get uptime % for EACH monitored device over last 24h
        # Query: device_ip, total_scans, online_scans
        stats_24h = db.session.query(
            DeviceScanHistory.device_ip,
            func.count(DeviceScanHistory.scan_id).label('total'),
            func.sum(case((DeviceScanHistory.status == 'Online', 1), else_=0)).label('online')
        ).join(
            Device,
            Device.device_ip == DeviceScanHistory.device_ip
        ).filter(
            DeviceScanHistory.scan_timestamp >= cutoff_24h
        ).group_by(DeviceScanHistory.device_ip).all()
        
        total_uptime_pct = 0
        devices_with_history = 0
        
        for stat in stats_24h:
            if stat.total > 0:
                dev_uptime = (stat.online / stat.total) * 100
                total_uptime_pct += dev_uptime
                devices_with_history += 1
        
        # Average of averages
        availability_24h = round(total_uptime_pct / devices_with_history, 1) if devices_with_history > 0 else 0.0

        # Network Health Stats
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0
        avg_packet_loss = round(sum(packet_losses) / len(packet_losses), 2) if packet_losses else 0
        
        # Alerts
        critical_count = DashboardEvent.query.filter_by(severity='CRITICAL', resolved=False).count()
        warning_count = DashboardEvent.query.filter_by(severity='WARNING', resolved=False).count()
        info_count = DashboardEvent.query.filter_by(severity='INFO', resolved=False).count()
        
        result = {
            'timestamp': datetime.utcnow().isoformat(),
            'counts': {
                'total_inventory': inventory_count,
                'monitored': monitored_count,
                'up': healthy_count, 
                'degraded': degraded_count,
                'down': offline_count,
                'maintenance': maintenance_count,
                'online_total': online_count
            },
            # Backward compatibility for frontend
            'devices': {
                'total': inventory_count,
                'monitored': monitored_count,
                'up': online_count,
                'online': online_count,
                'down': offline_count,
                'offline': offline_count,
                'degraded': degraded_count,
                'healthy': healthy_count,
                'maintenance': maintenance_count,
                'unknown': unknown_count,
                'up_percent': availability_live,
                'online_percent': availability_live
            },
            'availability': {
                'live_pct': availability_live,
                'history_24h_pct': availability_24h
            },
            'network_health': {
                'avg_latency_ms': avg_latency,
                'avg_packet_loss_pct': avg_packet_loss,
                'packet_loss': avg_packet_loss  # Alternative name for consistency
            },
            'active_alerts': {
                'critical': critical_count,
                'warning': warning_count,
                'info': info_count,
                'total': critical_count + warning_count + info_count
            },
            'meta': {
                'tooltips': {
                    'live_pct': 'Percentage of devices currently online.',
                    'history_24h_pct': 'Average uptime percentage of all devices over the last 24 hours.',
                    'avg_latency_ms': 'Average pong latency of currently online devices.',
                    'network_health': 'General health based on latency and packet loss.'
                }
            }
        }
        
        set_cached('summary', result, ttl_seconds=30)
        return jsonify(result)
        
    except Exception as e:
        print(f"Dashboard summary error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/dashboard/top-problems
# ============================================================
@dashboard_bp.route('/top-problems')
def get_top_problems():
    """
    Returns top problem devices:
    - High latency devices
    - High packet loss devices
    - Most alerting devices (24h)
    - Recently down devices
    Cache: 60s
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    force_fresh = request.args.get('fresh', '').lower() in ('1', 'true', 'yes')
    if not force_fresh:
        cached = get_cached('top-problems', 10)
        if cached:
            return jsonify(cached)
    
    try:
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from models.dashboard import DashboardEvent
        
        # Latest scan per device (same subquery pattern)
        latest_subq = db.session.query(
            DeviceScanHistory.device_ip,
            func.max(DeviceScanHistory.scan_id).label('max_id')
        ).group_by(DeviceScanHistory.device_ip).subquery()
        
        latest_scans = db.session.query(DeviceScanHistory, Device).join(
            latest_subq,
            (DeviceScanHistory.device_ip == latest_subq.c.device_ip) &
            (DeviceScanHistory.scan_id == latest_subq.c.max_id)
        ).join(
            Device,
            Device.device_ip == DeviceScanHistory.device_ip
        ).all()
        
        # High Latency (Top 5)
        # s is now (DeviceScanHistory, Device)
        online_scans = [s for s in latest_scans if s[0].status == 'Online' and s[0].ping_time_ms]
        high_latency = sorted(online_scans, key=lambda x: x[0].ping_time_ms or 0, reverse=True)[:5]
        
        # High Packet Loss (Top 5) - Only show ONLINE devices with partial loss (0 < loss < 100)
        with_packet_loss = [
            s for s in latest_scans 
            if s[0].packet_loss is not None and 0 < s[0].packet_loss < 100 and s[0].status == 'Online'
        ]
        high_loss = sorted(with_packet_loss, key=lambda x: x[0].packet_loss or 0, reverse=True)[:5]

        def iso_utc(ts):
            if not ts:
                return None
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.isoformat()

        def build_ping_stats(device_ip, limit=10):
            scans = DeviceScanHistory.query.filter(
                DeviceScanHistory.device_ip == device_ip
            ).order_by(DeviceScanHistory.scan_id.desc()).limit(limit).all()
            latencies = [s.ping_time_ms for s in scans if s.ping_time_ms is not None]
            losses = [s.packet_loss for s in scans if s.packet_loss is not None]
            jitters = [s.jitter for s in scans if s.jitter is not None]
            stats = {
                'latency_avg': round(sum(latencies) / len(latencies), 2) if latencies else None,
                'latency_min': round(min(latencies), 2) if latencies else None,
                'latency_max': round(max(latencies), 2) if latencies else None,
                'loss_avg': round(sum(losses) / len(losses), 2) if losses else None,
                'loss_max': round(max(losses), 2) if losses else None,
                'jitter_avg': round(sum(jitters) / len(jitters), 2) if jitters else None
            }
            return stats
        
        # Recently Down (Top 5 offline devices)
        offline_scans = sorted(
            [s for s in latest_scans if s[0].status == 'Offline'],
            key=lambda x: x[0].scan_timestamp or datetime.min,
            reverse=True
        )[:5]
        
        # Recent Alerts (Top 5 Active/Unresolved)
        # We prioritize unresolved alerts. If none, maybe show resolved? User wants "Active Alerts".
        recent_alerts = DashboardEvent.query.filter(
            DashboardEvent.resolved == False
        ).order_by(
            DashboardEvent.timestamp.desc()
        ).limit(10).all()
        
        result = {
            'high_latency': [
                {
                    'device_name': s[0].device_name, 
                    'ip': s[0].device_ip, 
                    'value': s[0].ping_time_ms, 
                    'unit': 'ms',
                    'device_id': s[1].device_id,
                    'time': iso_utc(s[0].scan_timestamp),
                    **build_ping_stats(s[0].device_ip)
                }
                for s in high_latency
            ],
            'high_packet_loss': [
                {
                    'device_name': s[0].device_name, 
                    'ip': s[0].device_ip, 
                    'value': s[0].packet_loss, 
                    'unit': '%',
                    'device_id': s[1].device_id,
                    'time': iso_utc(s[0].scan_timestamp),
                    **build_ping_stats(s[0].device_ip)
                }
                for s in high_loss
            ],
            'recently_down': [
                {
                    'device_name': s[0].device_name, 
                    'ip': s[0].device_ip, 
                    'time': iso_utc(s[0].scan_timestamp),
                    'device_id': s[1].device_id
                }
                for s in offline_scans
            ],
            'recent_alerts': [
                {
                    'id': e.event_id,
                    'device_ip': e.device_ip,
                    'message': e.message, 
                    'severity': e.severity, 
                    'time': iso_utc(e.timestamp),
                    'is_acknowledged': e.is_acknowledged
                }
                for e in recent_alerts
            ]
        }
        
        set_cached('top-problems', result, ttl_seconds=10)
        return jsonify(result)
        
    except Exception as e:
        print(f"Top problems error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/alerts (Full List)
# ============================================================
@dashboard_bp.route('/alerts')
def get_all_alerts():
    """
    Get all alerts with filtering capabilities.
    Query params: status=active|resolved|all, limit=100
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from models.dashboard import DashboardEvent
        from models.device import Device
        
        status = request.args.get('status', 'active')
        limit = int(request.args.get('limit', 100))
        
        query = DashboardEvent.query
        
        if status == 'active':
            query = query.filter_by(resolved=False)
        elif status == 'resolved':
            query = query.filter_by(resolved=True)
            
        alerts = query.order_by(DashboardEvent.timestamp.desc()).limit(limit).all()

        device_ids = [a.device_id for a in alerts if a.device_id]
        devices = Device.query.filter(Device.device_id.in_(device_ids)).all() if device_ids else []
        device_map = {d.device_id: d for d in devices}

        def classify_scope(device_type: str) -> str:
            t = (device_type or '').strip().lower()
            if t == 'server':
                return 'Server'
            if t in ('router', 'switch', 'firewall', 'access_point', 'network device'):
                return 'Network'
            return 'Device'

        def iso_utc(ts):
            if not ts:
                return None
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.isoformat()

        return jsonify([{
            'id': e.event_id,
            'device_id': e.device_id,
            'device_ip': e.device_ip,
            'device_name': device_map.get(e.device_id).device_name if e.device_id in device_map else None,
            'device_type': device_map.get(e.device_id).device_type if e.device_id in device_map else None,
            'scope': classify_scope(device_map.get(e.device_id).device_type) if e.device_id in device_map else 'Device',
            'event_type': e.event_type,
            'severity': e.severity,
            'message': e.message,
            'timestamp': iso_utc(e.timestamp),
            'resolved': e.resolved,
            'is_acknowledged': e.is_acknowledged,
            'acknowledged_by': e.acknowledged_by,
            'acknowledged_at': iso_utc(e.acknowledged_at)
        } for e in alerts])

    except Exception as e:
        return jsonify({'error': str(e)}), 500



# ============================================================
# POST /api/alerts/<id>/acknowledge
# ============================================================
@dashboard_bp.route('/alerts/<event_id>/acknowledge', methods=['POST'])
def acknowledge_alert(event_id):
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from models.dashboard import DashboardEvent
        event = DashboardEvent.query.get(event_id)
        
        if not event:
            return jsonify({'error': 'Alert not found'}), 404
            
        event.is_acknowledged = True
        event.acknowledged_at = datetime.utcnow()
        event.acknowledged_by = session.get('user_id', 'admin') # Default to admin if no user_id
        
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Alert acknowledged'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# POST /api/alerts/<id>/resolve
# ============================================================
@dashboard_bp.route('/alerts/<event_id>/resolve', methods=['POST'])
def resolve_alert(event_id):
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from models.dashboard import DashboardEvent
        event = DashboardEvent.query.get(event_id)
        
        if not event:
            return jsonify({'error': 'Alert not found'}), 404
            
        event.resolved = True
        event.resolved_at = datetime.utcnow()
        event.message += " [MANUALLY RESOLVED]"
        
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Alert resolved'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# GET /api/dashboard/trends
# ============================================================
@dashboard_bp.route('/trends')
def get_trends():
    """
    Returns time-series data for sparklines:
    - Availability trend
    - Latency trend
    Query params: range=1h|24h|7d
    Cache: 5min
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    time_range = request.args.get('range', '24h')
    cache_key = f'trends_{time_range}'
    
    cached = get_cached(cache_key, 300)
    if cached:
        return jsonify(cached)
    
    try:
        from models.scan_history import DeviceScanHistory
        
        # Determine cutoff
        if time_range == '1h':
            cutoff = datetime.utcnow() - timedelta(hours=1)
            bucket_minutes = 5
        elif time_range == '7d':
            cutoff = datetime.utcnow() - timedelta(days=7)
            bucket_minutes = 360  # 6 hours
        elif time_range == '30d':
            cutoff = datetime.utcnow() - timedelta(days=30)
            bucket_minutes = 1440 # 24 hours (1 day)
        else:  # 24h default
            cutoff = datetime.utcnow() - timedelta(hours=24)
            bucket_minutes = 60
        
        # Pre-fill buckets to ensure continuous timeline
        buckets = {}
        current_time_step = cutoff
        now = datetime.utcnow()
        
        # Helper: Round timestamp to bucket start
        def get_bucket_key(ts, mins):
            if mins < 60:
                return ts.replace(minute=(ts.minute // mins) * mins, second=0, microsecond=0)
            elif mins == 60:
                return ts.replace(minute=0, second=0, microsecond=0)
            elif mins == 360:
                new_hour = (ts.hour // 6) * 6
                return ts.replace(hour=new_hour, minute=0, second=0, microsecond=0)
            elif mins == 1440:
                return ts.replace(hour=0, minute=0, second=0, microsecond=0)
            return ts.replace(minute=0, second=0, microsecond=0)

        # Generate zero-filled buckets
        while current_time_step <= now:
            ts_key = get_bucket_key(current_time_step, bucket_minutes)
            key = ts_key.isoformat()
            if key not in buckets:
                buckets[key] = {'online': 0, 'total': 0, 'latencies': []}
            current_time_step += timedelta(minutes=bucket_minutes)

        scans = DeviceScanHistory.query.filter(
            DeviceScanHistory.scan_timestamp >= cutoff
        ).order_by(DeviceScanHistory.scan_timestamp).all()
        
        # Fill with actual data
        for scan in scans:
            if not scan.scan_timestamp:
                continue
            
            ts = scan.scan_timestamp
            bucket_time = get_bucket_key(ts, bucket_minutes)
            key = bucket_time.isoformat()
            
            if key in buckets:
                buckets[key]['total'] += 1
                if scan.status == 'Online':
                    buckets[key]['online'] += 1
                    if scan.ping_time_ms:
                        buckets[key]['latencies'].append(scan.ping_time_ms)
        
        availability_trend = []
        latency_trend = []
        
        for time_key in sorted(buckets.keys()):
            b = buckets[time_key]
            
            if b['total'] > 0:
                 avail = round((b['online'] / b['total']) * 100, 1)
                 avg_lat = round(sum(b['latencies']) / len(b['latencies']), 2) if b['latencies'] else 0
            else:
                 # Default values for empty periods
                 avail = 0 
                 avg_lat = 0
            
            availability_trend.append({
                'time': time_key,
                'value': avail,
                'online': int(b['online']),
                'total': int(b['total'])
            })
            latency_trend.append({'time': time_key, 'value': avg_lat})
        
        result = {
            'range': time_range,
            'availability_trend': availability_trend,
            'latency_trend': latency_trend
        }
        
        set_cached(cache_key, result, ttl_seconds=300)
        return jsonify(result)
        
    except Exception as e:
        print(f"Trends error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/dashboard/availability-details
# ============================================================
@dashboard_bp.route('/availability-details')
def get_availability_details():
    """
    Returns availability detail data for the last 24 hours:
    - 24h uptime heatmap (hourly buckets)
    - Devices contributing to downtime (offline scans)
    - Top 5 worst availability
    Cache: 60s (unless fresh=1)
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    force_fresh = request.args.get('fresh', '').lower() in ('1', 'true', 'yes')
    if not force_fresh:
        cached = get_cached('availability-details', 60)
        if cached:
            return jsonify(cached)

    try:
        from models.scan_history import DeviceScanHistory
        from models.device import Device

        now = datetime.utcnow()
        bucket_start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
        cutoff = bucket_start

        # Build 24 hourly buckets
        buckets = {}
        for i in range(24):
            ts = bucket_start + timedelta(hours=i)
            buckets[ts] = {'online': 0, 'total': 0}

        scans = DeviceScanHistory.query.filter(
            DeviceScanHistory.scan_timestamp >= cutoff
        ).all()

        for scan in scans:
            if not scan.scan_timestamp:
                continue
            ts_bucket = scan.scan_timestamp.replace(minute=0, second=0, microsecond=0)
            if ts_bucket in buckets:
                buckets[ts_bucket]['total'] += 1
                if scan.status == 'Online':
                    buckets[ts_bucket]['online'] += 1

        heatmap = []
        for ts in sorted(buckets.keys()):
            total = buckets[ts]['total']
            online = buckets[ts]['online']
            pct = round((online / total) * 100, 1) if total > 0 else 0.0
            heatmap.append({
                'time': ts.replace(tzinfo=timezone.utc).isoformat(),
                'value': pct,
                'online': int(online),
                'total': int(total)
            })

        stats_subq = db.session.query(
            DeviceScanHistory.device_ip.label('device_ip'),
            func.count(DeviceScanHistory.scan_id).label('total'),
            func.sum(case((DeviceScanHistory.status == 'Online', 1), else_=0)).label('online')
        ).filter(
            DeviceScanHistory.scan_timestamp >= cutoff
        ).group_by(DeviceScanHistory.device_ip).subquery()

        stats = db.session.query(
            stats_subq.c.device_ip,
            stats_subq.c.total,
            stats_subq.c.online,
            Device.device_name,
            Device.device_type
        ).outerjoin(
            Device,
            Device.device_ip == stats_subq.c.device_ip
        ).all()

        devices = []
        for row in stats:
            total = int(row.total or 0)
            online = int(row.online or 0)
            offline = max(total - online, 0)
            uptime_pct = round((online / total) * 100, 1) if total > 0 else 0.0
            downtime_pct = round(100.0 - uptime_pct, 1) if total > 0 else 0.0
            devices.append({
                'device_name': row.device_name or row.device_ip or 'Unknown',
                'ip': row.device_ip,
                'device_type': row.device_type or 'Unknown',
                'total_scans': total,
                'offline_scans': offline,
                'uptime_pct': uptime_pct,
                'downtime_pct': downtime_pct
            })

        downtime_contributors = sorted(
            [d for d in devices if d['offline_scans'] > 0],
            key=lambda d: (d['offline_scans'], d['downtime_pct']),
            reverse=True
        )[:10]

        worst_availability = sorted(
            [d for d in devices if d['total_scans'] > 0],
            key=lambda d: (d['uptime_pct'], -d['total_scans'])
        )[:5]

        result = {
            'generated_at': now.replace(tzinfo=timezone.utc).isoformat(),
            'heatmap': heatmap,
            'downtime_contributors': downtime_contributors,
            'worst_availability': worst_availability
        }

        set_cached('availability-details', result, ttl_seconds=60)
        return jsonify(result)

    except Exception as e:
        print(f"Availability details error: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================
# GET /api/dashboard/inventory
# ============================================================
@dashboard_bp.route('/inventory')
def get_inventory_stats():
    """
    Get inventory statistics for charts.
    - Vendor distribution
    - Device Type distribution
    - SNMP adoption rate
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        from models.device import Device
        from models.snmp_config import DeviceSnmpConfig
        from sqlalchemy import func
        
        # 1. Vendor Distribution
        vendor_query = db.session.query(
            Device.manufacturer, 
            func.count(Device.device_id)
        ).group_by(Device.manufacturer).all()
        
        by_vendor = {
            (v[0] or 'Unknown'): v[1] 
            for v in vendor_query
        }
        
        def normalize_inventory_type(value: str) -> str:
            raw = (value or '').strip().lower()
            if raw in ('camera', 'camera/iot', 'camera_iot'):
                return 'Camera/IoT'
            if not raw or raw == 'unknown':
                return 'Unknown'
            return raw.replace('_', ' ').title()

        # 2. Device Type Distribution
        type_query = db.session.query(
            Device.device_type, 
            func.count(Device.device_id)
        ).group_by(Device.device_type).all()

        by_type = {}
        for dtype, count in type_query:
            label = normalize_inventory_type(dtype)
            by_type[label] = by_type.get(label, 0) + count
        
        # 3. SNMP Stats
        total_devices = Device.query.count()
        snmp_enabled = DeviceSnmpConfig.query.filter_by(is_enabled=True).count()
        
        # 4. Full Device List (for table)
        devices = Device.query.all()
        
        # calculate server health for each device (agent metrics only)
        from models.server_health import ServerHealthLog
        
        # Get latest agent health log for each device
        latest_health_subq = db.session.query(
            ServerHealthLog.device_id,
            func.max(ServerHealthLog.id).label('max_id')
        ).filter(
            ServerHealthLog.source == 'agent'
        ).group_by(ServerHealthLog.device_id).subquery()
        
        latest_health_logs = db.session.query(ServerHealthLog).join(
            latest_health_subq,
            (ServerHealthLog.id == latest_health_subq.c.max_id)
        ).all()
        
        health_map = {log.device_id: log for log in latest_health_logs}
        
        device_list = []
        for d in devices:
            d_dict = d.to_dict()

            # Default to unknown/standard
            d_dict['server_health'] = 'Unknown'

            if is_server_device(d.device_type):
                log = health_map.get(d.device_id)
                d_dict['server_health'] = compute_server_health(log)
            
            device_list.append(d_dict)
        
        return jsonify({
            'total_devices': total_devices,
            'by_vendor': by_vendor,
            'by_type': by_type,
            'devices': device_list,
            'snmp_status': {
                'enabled': snmp_enabled,
                'disabled': total_devices - snmp_enabled,
                'percent_enabled': round((snmp_enabled / total_devices * 100), 1) if total_devices > 0 else 0
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# GET /api/dashboard/realtime/interfaces
# ============================================================
@dashboard_bp.route('/realtime/interfaces')
def get_top_interfaces():
    """
    Returns top 5 interfaces by utilization (RX + TX).
    Lookback: last 2 minutes to ensure recent data.
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from models.interfaces import DeviceInterface, InterfaceTrafficHistory
        from models.device import Device
        
        # Get latest data point time
        last_entry = db.session.query(func.max(InterfaceTrafficHistory.timestamp)).scalar()
        if not last_entry:
            return jsonify([])

        # Filter for data in the last collection window (e.g. same second)
        # Using a small window around the max timestamp
        window_start = last_entry - timedelta(seconds=15)

        # Query top utilization
        # We assume utilization is 0-100
        stats = db.session.query(
            InterfaceTrafficHistory,
            DeviceInterface,
            Device
        ).join(
            DeviceInterface, DeviceInterface.interface_id == InterfaceTrafficHistory.interface_id
        ).join(
            Device, Device.device_id == DeviceInterface.device_id
        ).filter(
            InterfaceTrafficHistory.timestamp >= window_start
        ).all()

        # Deduplicate by interface_id to avoid repeated bars for the same interface
        latest_by_iface = {}
        for history, iface, device in stats:
            key = history.interface_id
            prev = latest_by_iface.get(key)
            if not prev or (history.timestamp and prev[0].timestamp and history.timestamp > prev[0].timestamp):
                latest_by_iface[key] = (history, iface, device)
            elif not prev:
                latest_by_iface[key] = (history, iface, device)

        deduped = list(latest_by_iface.values())

        # Sort in Python (simpler than complex SQL ordering for (rx+tx))
        # utilization = max(rx, tx) or avg(rx, tx)? Usually we care about the link being full either way.
        # Let's use max(rx_util, tx_util) for ranking "busiest"
        deduped.sort(key=lambda x: max(x[0].rx_utilization_pct or 0, x[0].tx_utilization_pct or 0), reverse=True)
        
        top_5 = deduped[:5]
        
        result = []
        for history, iface, device in top_5:
            # Determine max util for the bar
            max_util = max(history.rx_utilization_pct or 0, history.tx_utilization_pct or 0)
            
            result.append({
                'name': f"{iface.alias or iface.name}",
                'device': device.device_name,
                'utilization_pct': round(max_util, 1),
                'rx_bps': history.rx_bps,
                'tx_bps': history.tx_bps,
                'speed': iface.speed_bps
            })
            
        return jsonify(result)

    except Exception as e:
        print(f"Top Interfaces Error: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================================
# GET /api/dashboard/realtime/network-io
# ============================================================
@dashboard_bp.route('/realtime/network-io')
def get_network_io_trend():
    """
    Returns aggregated Network I/O (Sum of all interfaces) for the last hour.
    """
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        from models.interfaces import InterfaceTrafficHistory
        
        # Last hour
        cutoff = datetime.utcnow() - timedelta(hours=1)
        
        # Bucket to 1 minute (PostgreSQL date_trunc).
        bucket = func.date_trunc('minute', InterfaceTrafficHistory.timestamp)
        data = db.session.query(
            bucket.label('bucket'),
            func.sum(InterfaceTrafficHistory.rx_bps).label('total_rx'),
            func.sum(InterfaceTrafficHistory.tx_bps).label('total_tx')
        ).filter(
            InterfaceTrafficHistory.timestamp >= cutoff
        ).group_by(
            bucket
        ).order_by(
            bucket
        ).all()
        
        labels = []
        in_data = []
        out_data = []
        
        for bucket_ts, rx, tx in data:
            # Convert bps to Mbps
            if bucket_ts and getattr(bucket_ts, 'tzinfo', None) is None:
                bucket_ts = bucket_ts.replace(tzinfo=timezone.utc)
            label = bucket_ts.isoformat().replace("+00:00", "Z") if bucket_ts else None
            labels.append(label)
            in_data.append(round((rx or 0) / 1_000_000, 2))
            out_data.append(round((tx or 0) / 1_000_000, 2))
            
        return jsonify({
            'labels': labels,
            'inbound': in_data,
            'outbound': out_data
        })

    except Exception as e:
        print(f"Network I/O Error: {e}")
        return jsonify({'error': str(e)}), 500
