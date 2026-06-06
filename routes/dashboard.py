"""
Dashboard API endpoints for Network Monitoring System.
Provides aggregated health, trends, and problem detection.
"""
import logging
import os
from flask import Blueprint, current_app, jsonify, request, session
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, desc, case, or_
import time

logger = logging.getLogger(__name__)
from services.dashboard_availability import build_device_availability_snapshot
from utils.server_health import compute_server_health, is_server_device, query_latest_server_health_logs
from extensions import db
from middleware.rbac import (
    current_scope_cache_fragment,
    get_ui_rbac_context,
    require_login,
    require_permission,
    scoped_query,
)

dashboard_bp = Blueprint('dashboard_bp', __name__, url_prefix='/api/dashboard')


def _parse_limit(default: int = 100, max_val: int = 500) -> int:
    """Parse and cap the ?limit= query parameter."""
    return min(max(1, request.args.get('limit', default, type=int)), max_val)


@dashboard_bp.before_request
@require_login
def _dashboard_auth_guard():
    return None

# ============================================================
# Distributed Cache (Redis) with local fallback
# ============================================================
import json
import threading
from extensions import redis_client

_cache = {}
_cache_ttl = {}
CACHE_NAMESPACE = 'dashboard'
CACHE_VERSION = 'v1'

# Per-key threading.Lock used as local fallback when Redis is unavailable.
# Prevents multiple threads from computing the same cache entry simultaneously.
_local_stampede_locks: dict = {}
_local_stampede_registry_lock = threading.Lock()


def _versioned_cache_key(key: str) -> str:
    return f"{CACHE_NAMESPACE}:{str(key).strip()}:{CACHE_VERSION}"

def get_cached(key, ttl_seconds=30):
    """Get value from cache if not expired."""
    cache_key = _versioned_cache_key(key)
    use_redis = bool(redis_client) and not current_app.config.get('TESTING')
    if use_redis:
        try:
            val = redis_client.get(cache_key)
            if val:
                return json.loads(val)
            return None
        except Exception as e:
            # Fallback to in-memory silently
            import logging
            logging.getLogger(__name__).warning(f"Redis get failed for {key}: {e}")
            pass

    # Local fallback
    if cache_key in _cache:
        if datetime.utcnow() < _cache_ttl.get(cache_key, datetime.min):
            return _cache[cache_key]
    return None

def set_cached(key, value, ttl_seconds=30):
    """Set value in cache with TTL."""
    cache_key = _versioned_cache_key(key)
    use_redis = bool(redis_client) and not current_app.config.get('TESTING')
    if use_redis:
        try:
            # We serialize to JSON strings for Redis
            payload = json.dumps(value)
            redis_client.setex(cache_key, ttl_seconds, payload)
            return
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Redis set failed for {key}: {e}")
            pass

    # Local fallback — evict if approaching limit
    _MAX_CACHE_KEYS = 500
    if len(_cache) >= _MAX_CACHE_KEYS:
        now = datetime.utcnow()
        expired = [k for k, t in list(_cache_ttl.items()) if t < now]
        to_drop = expired if expired else [next(iter(_cache))]
        for k in to_drop[:10]:
            _cache.pop(k, None)
            _cache_ttl.pop(k, None)
    _cache[cache_key] = value
    _cache_ttl[cache_key] = datetime.utcnow() + timedelta(seconds=ttl_seconds)

def _get_local_stampede_lock(lock_key: str) -> threading.Lock:
    """Return (creating if needed) the per-key threading.Lock used when Redis is unavailable."""
    with _local_stampede_registry_lock:
        if lock_key not in _local_stampede_locks:
            _local_stampede_locks[lock_key] = threading.Lock()
        return _local_stampede_locks[lock_key]


def acquire_stampede_lock(lock_key, ttl_seconds=10):
    """
    Acquire a distributed lock to prevent thundering herd when rebuilding cache.
    Returns True if acquired, False if someone else is building it.

    When Redis is available: uses SET NX EX for cross-process coordination.
    When Redis is down: falls back to a per-key threading.Lock so concurrent
    threads in the same process do not all stampede the DB simultaneously.
    """
    versioned_lock_key = _versioned_cache_key(f"lock:{lock_key}")
    use_redis = bool(redis_client) and not current_app.config.get('TESTING')
    if use_redis:
        try:
            acquired = redis_client.set(versioned_lock_key, "1", nx=True, ex=ttl_seconds)
            return bool(acquired)
        except Exception:
            pass  # Redis down — fall through to local lock

    # Local in-process lock (non-blocking try-acquire)
    local_lock = _get_local_stampede_lock(lock_key)
    return local_lock.acquire(blocking=False)


def release_stampede_lock(lock_key):
    """Best-effort lock release for cache rebuild coordination."""
    versioned_lock_key = _versioned_cache_key(f"lock:{lock_key}")
    use_redis = bool(redis_client) and not current_app.config.get('TESTING')
    if use_redis:
        try:
            redis_client.delete(versioned_lock_key)
        except Exception:
            pass

    # Always attempt to release local lock (no-op if not held by this thread)
    local_lock = _local_stampede_locks.get(lock_key)
    if local_lock is not None:
        try:
            local_lock.release()
        except RuntimeError:
            pass  # Not held — safe to ignore


def _extract_json_payload(result):
    """Normalize Flask view return values into (payload, status_code)."""
    status_code = 200
    response_obj = result

    if isinstance(result, tuple):
        response_obj = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]

    if hasattr(response_obj, 'status_code'):
        status_code = getattr(response_obj, 'status_code', status_code)

    if hasattr(response_obj, 'get_json'):
        payload = response_obj.get_json(silent=True)
    else:
        payload = response_obj

    return payload, status_code


def _collect_section(section_name, builder):
    """Execute a section builder and return (payload, error_message)."""
    try:
        payload, status_code = _extract_json_payload(builder())
        if status_code >= 400:
            if isinstance(payload, dict) and payload.get('error'):
                return None, payload.get('error')
            return None, f'{section_name} returned HTTP {status_code}'
        if isinstance(payload, dict) and payload.get('error'):
            return None, payload.get('error')
        return payload, None
    except Exception as exc:
        return None, str(exc)


def _build_subnet_health(all_devices, latest_scans, ip_to_subnet):
    """Build per-subnet online/offline breakdown.

    Uses the SAME status logic as the main KPI cards:
    - 'online' status → online
    - everything else → offline
    - devices with no scan → counted in total but not online

    Returns a list sorted by subnet name.
    """
    from collections import defaultdict

    # Count totals per subnet from Device table
    subnet_totals = defaultdict(int)
    for dev in all_devices:
        sn = dev.subnet_cidr or 'Unassigned'
        subnet_totals[sn] += 1

    # Count online per subnet from latest scans
    subnet_online = defaultdict(int)
    for scan in latest_scans:
        status = (scan.status or '').lower()
        if status == 'online':
            sn = ip_to_subnet.get(scan.device_ip, 'Unassigned')
            subnet_online[sn] += 1

    # Build result
    result = []
    for sn in sorted(subnet_totals.keys()):
        total = subnet_totals[sn]
        online = subnet_online.get(sn, 0)
        result.append({
            'subnet': sn,
            'total': total,
            'online': online,
            'offline': total - online
        })
    return result


def _scope_cache_suffix():
    return current_scope_cache_fragment().replace(':', '__')


AVAILABILITY_RANGE_CONFIG = {
    '24h': {
        'label': 'Last 24 Hours',
        'bucket_count': 12,
        'bucket_hours': 2,
    },
    '7d': {
        'label': 'Last 7 Days',
        'bucket_count': 14,
        'bucket_hours': 12,
    },
    '30d': {
        'label': 'Last 30 Days',
        'bucket_count': 30,
        'bucket_hours': 24,
    },
}
AVAILABILITY_ONLINE_INTERVAL_THRESHOLD_PCT = 50.0


def _get_availability_range_config(range_key):
    key = str(range_key or '24h').strip().lower()
    config = AVAILABILITY_RANGE_CONFIG.get(key, AVAILABILITY_RANGE_CONFIG['24h']).copy()
    config['key'] = key if key in AVAILABILITY_RANGE_CONFIG else '24h'
    return config


def _floor_utc_bucket_start(ts, bucket_hours):
    bucket_hours = max(int(bucket_hours or 1), 1)
    normalized = ts.replace(minute=0, second=0, microsecond=0)
    if bucket_hours >= 24:
        return normalized.replace(hour=0)
    bucket_hour = (normalized.hour // bucket_hours) * bucket_hours
    return normalized.replace(hour=bucket_hour)


def _coerce_utc_naive(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        raw = ts.strip()
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        try:
            ts = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if getattr(ts, 'tzinfo', None) is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _iso_utc_naive(ts):
    normalized = _coerce_utc_naive(ts)
    return normalized.isoformat() if normalized else None


def _max_timestamp(*timestamps):
    normalized = [candidate for candidate in (_coerce_utc_naive(ts) for ts in timestamps) if candidate is not None]
    return max(normalized) if normalized else None


def _payload_source_freshness(payload):
    if isinstance(payload, dict):
        direct_freshness = _coerce_utc_naive(payload.get('source_data_freshness_at'))
        if direct_freshness is not None:
            return direct_freshness

        candidates = []
        for key in ('timestamp', 'generated_at', 'generated_at_utc'):
            candidate = _coerce_utc_naive(payload.get(key))
            if candidate is not None:
                candidates.append(candidate)
        for value in payload.values():
            nested = _payload_source_freshness(value)
            if nested is not None:
                candidates.append(nested)
        return max(candidates) if candidates else None

    if isinstance(payload, list):
        candidates = [candidate for candidate in (_payload_source_freshness(item) for item in payload) if candidate is not None]
        return max(candidates) if candidates else None

    return None


def _availability_hour_bucket_expr(scan_model):
    backend = db.engine.url.get_backend_name()
    if backend == 'sqlite':
        return func.strftime('%Y-%m-%dT%H:00:00', scan_model.scan_timestamp).label('hour')
    return func.date_trunc('hour', scan_model.scan_timestamp).label('hour')


def _event_device_ips(event, device_map):
    device = device_map.get(event.device_id) if event and getattr(event, 'device_id', None) in device_map else None
    current_ip = getattr(device, 'device_ip', None) if device else None
    original_ip = getattr(event, 'device_ip', None)
    return current_ip or original_ip, original_ip


def _snapshot_meta():
    context = get_ui_rbac_context()
    return {
        'role': context.get('role', 'guest'),
        'scope_key': context.get('scope_key', 'global'),
        'scope_label': context.get('scope_label', 'Global'),
        'generated_at_utc': datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
    }


def _scoped_devices(include_objects=False):
    from models.device import Device
    query = scoped_query(Device)
    if not include_objects:
        device_ids = [
            row[0]
            for row in query.with_entities(Device.device_id).all()
            if row and row[0] is not None
        ]
        return [], device_ids

    devices = query.all()
    return devices, [d.device_id for d in devices if getattr(d, 'device_id', None) is not None]


@dashboard_bp.route('/subnet-details')
def get_subnet_details():
    """
    Lightweight subnet details payload for the subnet modal.
    Query params:
    - subnet: required subnet cidr or "Unassigned"
    - limit: optional max devices returned (default 500, max 2000)
    """

    subnet_value = (request.args.get('subnet') or '').strip()
    if not subnet_value:
        return jsonify({'error': 'Missing required query param: subnet'}), 400

    try:
        requested_limit = int(request.args.get('limit', 500))
    except Exception:
        requested_limit = 500
    limit = max(1, min(requested_limit, 2000))

    def _normalize_subnet(value):
        cleaned = (value or '').strip()
        return cleaned if cleaned else 'Unassigned'

    def _iso_utc(ts):
        if not ts:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()

    def _normalize_device_type(value):
        raw = (value or '').strip().lower()
        if not raw:
            return 'Unknown'
        return raw.replace('_', ' ').title()

    try:
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from middleware.rbac import scoped_query

        normalized_subnet = _normalize_subnet(subnet_value)

        base_query = scoped_query(Device)
        if normalized_subnet.lower() == 'unassigned':
            base_query = base_query.filter(
                or_(
                    Device.subnet_cidr.is_(None),
                    Device.subnet_cidr == '',
                    Device.subnet_cidr == 'Unassigned'
                )
            )
        else:
            base_query = base_query.filter(Device.subnet_cidr == normalized_subnet)

        total_matching = base_query.count()
        devices = base_query.order_by(Device.device_ip.asc()).limit(limit).all()

        device_ips = [d.device_ip for d in devices if d.device_ip]
        latest_scan_map = {}
        if device_ips:
            latest_subq = db.session.query(
                DeviceScanHistory.device_ip,
                func.max(DeviceScanHistory.scan_id).label('max_id')
            ).filter(
                DeviceScanHistory.device_ip.in_(device_ips)
            ).group_by(
                DeviceScanHistory.device_ip
            ).subquery()

            latest_scans = db.session.query(DeviceScanHistory).join(
                latest_subq,
                (DeviceScanHistory.device_ip == latest_subq.c.device_ip) &
                (DeviceScanHistory.scan_id == latest_subq.c.max_id)
            ).all()
            latest_scan_map = {scan.device_ip: scan for scan in latest_scans}

        rows = []
        online_count = 0
        monitored_count = 0
        server_count = 0
        type_counts = {}
        vendor_counts = {}

        for device in devices:
            scan = latest_scan_map.get(device.device_ip)
            scan_status = (scan.status or '').strip().lower() if scan else ''
            status = 'online' if scan_status == 'online' else 'offline'
            if status == 'online':
                online_count += 1

            if device.is_monitored:
                monitored_count += 1

            device_type = _normalize_device_type(device.device_type)
            vendor = (device.manufacturer or 'Unknown').strip() or 'Unknown'
            is_server = (device.device_type or '').strip().lower() == 'server'
            if is_server:
                server_count += 1

            type_counts[device_type] = type_counts.get(device_type, 0) + 1
            vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1

            rows.append({
                'device_id': device.device_id,
                'device_name': device.device_name,
                'hostname': device.hostname,
                'device_ip': device.device_ip,
                'device_type': device_type,
                'manufacturer': vendor,
                'is_monitored': bool(device.is_monitored),
                'status': status,
                'last_seen': _iso_utc(scan.scan_timestamp) if scan else None
            })

        offline_count = max(len(rows) - online_count, 0)
        health_pct = round((online_count / len(rows)) * 100, 1) if rows else 0.0
        top_types = sorted(type_counts.items(), key=lambda item: item[1], reverse=True)[:5]
        top_vendors = sorted(vendor_counts.items(), key=lambda item: item[1], reverse=True)[:5]

        snapshot_generated_at = datetime.utcnow().isoformat()
        source_data_freshness_at = _max_timestamp(
            *(getattr(scan, 'scan_timestamp', None) for scan in latest_scan_map.values())
        )

        return jsonify({
            'generated_at': snapshot_generated_at,
            'snapshot_generated_at': snapshot_generated_at,
            'source_data_freshness_at': _iso_utc_naive(source_data_freshness_at),
            'subnet': normalized_subnet,
            'summary': {
                'total': len(rows),
                'online': online_count,
                'offline': offline_count,
                'health_pct': health_pct,
                'monitored': monitored_count,
                'servers': server_count
            },
            'top_types': [{'name': name, 'count': count} for name, count in top_types],
            'top_vendors': [{'name': name, 'count': count} for name, count in top_vendors],
            'devices': rows,
            'is_truncated': total_matching > len(rows),
            'total_matching': total_matching
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


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
    
    scope_cache_key = f"summary:{_scope_cache_suffix()}"
    cached = get_cached(scope_cache_key)
    if cached:
        return jsonify(cached)
    
    try:
        from models.scan_history import DeviceScanHistory
        from models.dashboard import DashboardEvent

        scoped_devices, scoped_device_ids = _scoped_devices(include_objects=True)
        scoped_device_ips = {d.device_ip for d in scoped_devices if getattr(d, 'device_ip', None)}

        # 1. Device Counts
        inventory_count = len(scoped_devices)
        maintenance_count = sum(1 for device in scoped_devices if bool(getattr(device, 'maintenance_mode', False)))
        
        logger.debug("Dashboard inventory count: %d", inventory_count)
        
        # Monitored Devices (The denominator for availability)
        # USER REQUEST: Removed is_monitored filter to show ALL devices
        monitored_count = len(scoped_devices)
        # Fallback to avoid division by zero
        monitored_denominator = monitored_count if monitored_count > 0 else 1

        availability_snapshot = build_device_availability_snapshot(scoped_devices)
        availability_counts = availability_snapshot.get('counts') or {}
        network_health = availability_snapshot.get('network_health') or {}
        
        healthy_count = int(availability_counts.get('healthy') or 0)
        degraded_count = int(availability_counts.get('degraded') or 0)
        offline_count = int(availability_counts.get('offline') or 0)
        unknown_count = int(availability_counts.get('unknown') or 0)
        online_count = int(availability_counts.get('online_total') or 0)
        
        
        
        # Build device_ip → subnet_cidr map for subnet grouping
        # Instant Availability (Current State)
        # Cap at 100% to prevent display bugs (e.g., 300% when calculations are off)
        availability_live = min(round((online_count / monitored_denominator) * 100, 1), 100.0)

        # ----------------------------------------
        # Calculate Real 24h Availability (Average Uptime)
        # ----------------------------------------
        cutoff_24h = datetime.utcnow() - timedelta(hours=24)

        # Use cagg (15-min pre-aggregated buckets) — avoids 7-8s raw table GROUP BY
        stats_24h = []
        if scoped_device_ips:
            from sqlalchemy import text as _text
            _stmt = _text("""
                SELECT device_ip,
                       SUM(probe_count) AS total,
                       SUM(online_count) AS online
                FROM device_scan_history_15m_cagg
                WHERE device_ip = ANY(:ips)
                  AND bucket >= :cutoff
                GROUP BY device_ip
            """)
            try:
                stats_24h = db.session.execute(
                    _stmt, {"ips": list(scoped_device_ips), "cutoff": cutoff_24h}
                ).fetchall()
            except Exception:
                db.session.rollback()
                # Cagg unavailable — fall back to raw query
                stats_24h = db.session.query(
                    DeviceScanHistory.device_ip,
                    func.count(DeviceScanHistory.scan_id).label('total'),
                    func.sum(case((DeviceScanHistory.status == 'Online', 1), else_=0)).label('online')
                ).filter(
                    DeviceScanHistory.scan_timestamp >= cutoff_24h,
                    DeviceScanHistory.device_ip.in_(scoped_device_ips)
                ).group_by(DeviceScanHistory.device_ip).all()

        total_uptime_pct = 0.0
        devices_with_history = 0

        for stat in stats_24h:
            if (stat.total or 0) > 0:
                dev_uptime = float(stat.online or 0) / float(stat.total) * 100
                total_uptime_pct += dev_uptime
                devices_with_history += 1

        # Average of averages
        availability_24h = round(total_uptime_pct / devices_with_history, 1) if devices_with_history > 0 else 0.0

        # Network Health Stats
        avg_latency = network_health.get('avg_latency_ms') or 0
        avg_packet_loss = network_health.get('avg_packet_loss_pct') or 0
        
        # Alerts — single GROUP BY replaces 3 separate .count() queries.
        # Covered by idx_dashboard_events_device_sev_res_ts (device_id, severity, resolved, ts).
        critical_count = 0
        warning_count = 0
        info_count = 0
        if scoped_device_ids:
            sev_rows = db.session.query(
                DashboardEvent.severity,
                func.count(DashboardEvent.event_id).label('cnt'),
            ).filter(
                DashboardEvent.device_id.in_(scoped_device_ids),
                DashboardEvent.resolved.is_(False),
            ).group_by(DashboardEvent.severity).all()
            for sev, cnt in sev_rows:
                if sev == 'CRITICAL':
                    critical_count = cnt
                elif sev == 'WARNING':
                    warning_count = cnt
                elif sev == 'INFO':
                    info_count = cnt
        
        snapshot_generated_at = datetime.utcnow().isoformat()
        source_data_freshness_at = _max_timestamp(
            availability_snapshot.get('generated_at'),
            *(getattr(scan, 'scan_timestamp', None) for scan in (availability_snapshot.get('latest_scan_map') or {}).values())
        )

        result = {
            'timestamp': snapshot_generated_at,
            'snapshot_generated_at': snapshot_generated_at,
            'source_data_freshness_at': _iso_utc_naive(source_data_freshness_at),
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
            },
            'subnet_health': availability_snapshot.get('subnet_health') or []
        }
        
        set_cached(scope_cache_key, result, ttl_seconds=30)
        return jsonify(result)
        
    except Exception:
        logger.exception("Dashboard summary error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/dashboard/full_snapshot
# ============================================================
@dashboard_bp.route('/full_snapshot')
def get_full_snapshot():
    """
    Consolidated dashboard payload for a single network request and single render cycle.
    Phase 3: Now returns a native DB JSON string in O(1) time complexity.
    To force computation (used by the background worker), append ?worker_compute=true
    """
    from models.dashboard import DashboardSnapshot
    
    time_range = request.args.get('range', '24h')
    alerts_status = request.args.get('status', 'active')
    alerts_limit = request.args.get('limit', '200')
    worker_compute = request.args.get('worker_compute', '').lower() in ('1', 'true', 'yes')
    fresh_top_problems = request.args.get('fresh', '').lower() in ('1', 'true', 'yes')

    meta = _snapshot_meta()
    scope_fragment = _scope_cache_suffix()
    snapshot_cache_key = (
        f"full_snapshot_{scope_fragment}_{time_range}_{alerts_status}_{alerts_limit}"
    )
    snapshot_lock_key = (
        f"full_snapshot:{scope_fragment}:{time_range}:{alerts_status}:{alerts_limit}"
    )
    lock_acquired = False

    def _snapshot_response(snapshot_record):
        payload = snapshot_record.payload
        if isinstance(payload, str):
            payload = json.loads(payload)

        MAX_SNAPSHOT_AGE = int(os.environ.get("MAX_SNAPSHOT_AGE_SECONDS", 90))
        age = (datetime.utcnow() - snapshot_record.updated_at).total_seconds()
        if age > MAX_SNAPSHOT_AGE:
            payload["stale"] = True
            payload["stale_since_seconds"] = int(age)

        return jsonify(payload)

    if not worker_compute and not fresh_top_problems:
        # Phase 3: Ultra-fast O(1) Native Database Fetch
        snapshot = DashboardSnapshot.query.filter_by(cache_key=snapshot_cache_key).first()
        if snapshot:
            return _snapshot_response(snapshot)

        # If no snapshot exists yet, acquire distributed lock so only one worker rebuilds.
        lock_acquired = acquire_stampede_lock(snapshot_lock_key, ttl_seconds=60)
        if not lock_acquired:
            wait_deadline = time.monotonic() + 3.0
            while time.monotonic() < wait_deadline:
                time.sleep(0.15)
                snapshot = DashboardSnapshot.query.filter_by(cache_key=snapshot_cache_key).first()
                if snapshot:
                    return _snapshot_response(snapshot)

            # Lock still held after 3s — fall back to ANY stale snapshot rather than
            # computing inline (inline computation under concurrency exhausts the DB pool).
            any_snapshot = DashboardSnapshot.query.order_by(
                DashboardSnapshot.updated_at.desc()
            ).first()
            if any_snapshot:
                try:
                    from flask import current_app
                    current_app.logger.warning(
                        "Snapshot lock busy for key=%s; serving stale snapshot",
                        snapshot_cache_key
                    )
                except Exception:
                    pass
                return _snapshot_response(any_snapshot)

            # Absolute last resort: no snapshot at all.
            # Return a lightweight "initialising" placeholder rather than computing
            # inline — inline computation by multiple concurrent Waitress threads
            # exhausts the DB pool and kills the health endpoint.
            # The background warm thread already owns the lock and will write a real
            # snapshot within ~30s; the frontend will refresh automatically.
            try:
                from flask import current_app
                current_app.logger.warning(
                    "Snapshot lock busy, no stale fallback for key=%s; returning initialising placeholder",
                    snapshot_cache_key,
                )
            except Exception:
                pass
            return jsonify({
                'timestamp': datetime.utcnow().isoformat(),
                'snapshot_generated_at': datetime.utcnow().isoformat(),
                'initialising': True,
                'stale': True,
                'summary': None,
                'fleetMetrics': None,
                'topProblems': None,
                'trends': None,
                'inventory': None,
                'serverHealth': None,
                'alerts': [],
                'meta': meta,
            })

    try:
        from routes.server_metrics import get_server_health_summary, get_fleet_metrics

        sections = {
            'summary': get_summary,
            'fleetMetrics': get_fleet_metrics,
            'topProblems': get_top_problems,
            'trends': get_trends,
            'inventory': get_inventory_stats,
            'serverHealth': get_server_health_summary,
            'alerts': get_all_alerts
        }

        snapshot_generated_at = datetime.utcnow().isoformat()
        payload = {
            'timestamp': snapshot_generated_at,
            'snapshot_generated_at': snapshot_generated_at,
            'source_data_freshness_at': None,
            'summary': None,
            'fleetMetrics': None,
            'topProblems': None,
            'trends': None,
            'inventory': None,
            'serverHealth': None,
            'alerts': None,
            'meta': meta,
        }
        errors = {}

        for key, handler in sections.items():
            section_payload, section_error = _collect_section(key, handler)
            payload[key] = section_payload
            if section_error:
                errors[key] = section_error

        fleet_metrics = payload.get('fleetMetrics') if isinstance(payload.get('fleetMetrics'), dict) else {}
        synthetic_alerts = list(fleet_metrics.get('synthetic_alerts') or [])
        if synthetic_alerts and isinstance(payload.get('alerts'), list):
            existing_ids = {str(alert.get('id') or '') for alert in payload['alerts'] if isinstance(alert, dict)}
            merged_alerts = list(payload['alerts'])
            for alert in synthetic_alerts:
                alert_id = str(alert.get('id') or '')
                if alert_id and alert_id not in existing_ids:
                    merged_alerts.append(alert)
                    existing_ids.add(alert_id)
            payload['alerts'] = merged_alerts

            if isinstance(payload.get('summary'), dict):
                summary_alerts = payload['summary'].setdefault('active_alerts', {})
                critical = 0
                warning = 0
                info = 0
                for alert in merged_alerts:
                    severity = str(alert.get('severity') or '').upper()
                    if severity == 'CRITICAL':
                        critical += 1
                    elif severity == 'WARNING':
                        warning += 1
                    else:
                        info += 1
                summary_alerts['critical'] = critical
                summary_alerts['warning'] = warning
                summary_alerts['info'] = info
                summary_alerts['total'] = critical + warning + info

        if errors:
            payload['errors'] = errors

        payload['source_data_freshness_at'] = _iso_utc_naive(
            _max_timestamp(*(_payload_source_freshness(payload.get(key)) for key in sections.keys()))
        )

        if worker_compute:
            # Background worker mode: Just return the pure python dict to be serialized and stored.
            return jsonify(payload)

        # In case normal clients hit this block (bootstrapping phase or fresh_top_problems=true),
        # return it normally as a JSON response.
        return jsonify(payload)
    finally:
        if lock_acquired:
            release_stampede_lock(snapshot_lock_key)


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
    
    force_fresh = request.args.get('fresh', '').lower() in ('1', 'true', 'yes')
    scoped_cache_key = f"top-problems:{_scope_cache_suffix()}"
    if not force_fresh:
        cached = get_cached(scoped_cache_key, 10)
        if cached:
            return jsonify(cached)
    
    try:
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from models.dashboard import DashboardEvent

        scoped_devices, scoped_device_ids = _scoped_devices(include_objects=True)
        device_by_ip = {d.device_ip: d for d in scoped_devices if getattr(d, 'device_ip', None)}
        scoped_ips = set(device_by_ip.keys())

        latest_scans = []
        if scoped_ips:
            # DISTINCT ON (device_ip) ORDER BY device_ip, scan_timestamp DESC uses the
            # covering index idx_dsh_ip_time_covering and avoids a full max(scan_id) aggregate.
            from sqlalchemy import text as _text
            _latest_stmt = _text("""
                SELECT DISTINCT ON (device_ip)
                    scan_id, device_ip, scan_timestamp, status, ping_time_ms, packet_loss, jitter
                FROM device_scan_history
                WHERE device_ip = ANY(:ips)
                ORDER BY device_ip, scan_timestamp DESC
            """)
            _raw_rows = db.session.execute(_latest_stmt, {"ips": list(scoped_ips)}).fetchall()
            latest_scans = [
                (row, device_by_ip[row.device_ip])
                for row in _raw_rows
                if row.device_ip in device_by_ip
            ]
        
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
        if scoped_device_ids:
            recent_alerts = DashboardEvent.query.filter(
                DashboardEvent.device_id.in_(scoped_device_ids),
                DashboardEvent.resolved.is_(False),
            ).order_by(
                DashboardEvent.timestamp.desc()
            ).limit(10).all()
        else:
            recent_alerts = []

        alert_device_ids = [event.device_id for event in recent_alerts if event.device_id]
        alert_devices = scoped_query(Device).filter(Device.device_id.in_(alert_device_ids)).all() if alert_device_ids else []
        alert_device_map = {device.device_id: device for device in alert_devices}
        
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
                    'device_ip': _event_device_ips(e, alert_device_map)[0],
                    'original_device_ip': _event_device_ips(e, alert_device_map)[1],
                    'message': e.message, 
                    'severity': e.severity, 
                    'time': iso_utc(e.timestamp),
                    'is_acknowledged': e.is_acknowledged
                }
                for e in recent_alerts
            ]
        }
        
        set_cached(scoped_cache_key, result, ttl_seconds=10)
        return jsonify(result)
        
    except Exception:
        logger.exception("Top problems error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/alerts (Full List)
# ============================================================
@dashboard_bp.route('/alerts')
def get_all_alerts():
    """
    Get all alerts with filtering capabilities.
    Query params: status=active|resolved|all, limit=100
    """

    try:
        from models.dashboard import DashboardEvent
        from models.device import Device

        scoped_device_ids = [
            row[0]
            for row in scoped_query(Device).with_entities(Device.device_id).all()
            if row and row[0] is not None
        ]

        status = request.args.get('status', 'active')
        limit = _parse_limit(default=100, max_val=500)

        query = DashboardEvent.query
        if scoped_device_ids:
            query = query.filter(DashboardEvent.device_id.in_(scoped_device_ids))
        else:
            query = query.filter(False)
        
        if status == 'active':
            query = query.filter_by(resolved=False)
        elif status == 'resolved':
            query = query.filter_by(resolved=True)
            
        alerts = query.order_by(DashboardEvent.timestamp.desc()).limit(limit).all()

        device_ids = [a.device_id for a in alerts if a.device_id]
        devices = scoped_query(Device).filter(Device.device_id.in_(device_ids)).all() if device_ids else []
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
            'device_ip': _event_device_ips(e, device_map)[0],
            'original_device_ip': _event_device_ips(e, device_map)[1],
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
@require_permission('devices.edit')
def acknowledge_alert(event_id):
    try:
        from models.dashboard import DashboardEvent
        event = DashboardEvent.query.get(event_id)

        if not event:
            return jsonify({'error': 'Alert not found'}), 404

        event.is_acknowledged = True
        event.acknowledged_at = datetime.utcnow()
        event.acknowledged_by = session.get('user_id', 'admin') # Default to admin if no user_id

        db.session.commit()

        # Audit logging
        from middleware.rbac import create_audit_log
        device_name = event.device_ip or 'Unknown'
        if event.device_id:
            from models.device import Device
            device = Device.query.get(event.device_id)
            if device:
                device_name = device.device_name or device.device_ip

        create_audit_log(
            action='acknowledge',
            entity_type='alert',
            entity_id=None,  # Alert IDs are UUIDs, not integers
            entity_name=f"{event.event_id[:8]} - {device_name} - {event.severity}",
            description=f"Alert acknowledged: {event.message[:100]}"
        )

        return jsonify({'status': 'success', 'message': 'Alert acknowledged'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# POST /api/alerts/<id>/resolve
# ============================================================
@dashboard_bp.route('/alerts/<event_id>/resolve', methods=['POST'])
@require_permission('devices.edit')
def resolve_alert(event_id):
    try:
        from models.dashboard import DashboardEvent
        event = DashboardEvent.query.get(event_id)
        
        if not event:
            return jsonify({'error': 'Alert not found'}), 404
            
        event.resolved = True
        event.resolved_at = datetime.utcnow()
        event.message += " [MANUALLY RESOLVED]"
        
        db.session.commit()
        
        # Audit logging
        from middleware.rbac import create_audit_log
        device_name = event.device_ip or 'Unknown'
        if event.device_id:
            from models.device import Device
            device = Device.query.get(event.device_id)
            if device:
                device_name = device.device_name or device.device_ip
        
        create_audit_log(
            action='resolve',
            entity_type='alert',
            entity_id=None,  # Alert IDs are UUIDs, not integers
            entity_name=f"{event.event_id[:8]} - {device_name} - {event.severity}",
            description=f"Alert resolved: {event.message[:100]}"
        )
        
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
    
    time_range = request.args.get('range', '24h')
    cache_key = f"trends:{_scope_cache_suffix()}:{time_range}"
    
    cached = get_cached(cache_key, 300)
    if cached:
        return jsonify(cached)
    
    try:
        from models.scan_history import DeviceScanHistory
        scoped_devices, _ = _scoped_devices(include_objects=True)
        scoped_ips = {device.device_ip for device in scoped_devices if getattr(device, 'device_ip', None)}

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

        # Use cagg for ranges ≥ 24h; for 1h fall back to raw (cagg is 15-min granularity)
        cagg_rows = []
        use_cagg = time_range != '1h' and scoped_ips
        if use_cagg:
            from sqlalchemy import text as _text
            _cagg_stmt = _text("""
                SELECT time_bucket(:bucket_interval, bucket) AS tb,
                       SUM(probe_count) AS total,
                       SUM(online_count) AS online,
                       SUM(avg_rtt * probe_count) / NULLIF(SUM(probe_count), 0) AS avg_latency
                FROM device_scan_history_15m_cagg
                WHERE device_ip = ANY(:ips)
                  AND bucket >= :cutoff
                GROUP BY 1 ORDER BY 1
            """)
            try:
                cagg_rows = db.session.execute(
                    _cagg_stmt,
                    {"ips": list(scoped_ips), "cutoff": cutoff,
                     "bucket_interval": f"{bucket_minutes} minutes"}
                ).fetchall()
            except Exception:
                use_cagg = False

        if use_cagg:
            for row in cagg_rows:
                key = get_bucket_key(row.tb, bucket_minutes).isoformat()
                if key in buckets:
                    buckets[key]['total'] += int(row.total or 0)
                    buckets[key]['online'] += int(row.online or 0)
                    if row.avg_latency:
                        buckets[key]['latencies'].append(float(row.avg_latency))
        else:
            scans = []
            if scoped_ips:
                scans = DeviceScanHistory.query.filter(
                    DeviceScanHistory.scan_timestamp >= cutoff,
                    DeviceScanHistory.device_ip.in_(scoped_ips),
                ).order_by(DeviceScanHistory.scan_timestamp).all()
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
        
    except Exception:
        logger.exception("Trends error")
        return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# GET /api/dashboard/availability-details
# ============================================================
@dashboard_bp.route('/availability-details')
def get_availability_details():
    """
    Returns availability detail data for the selected range:
    - Interval-based uptime heatmap
    - Devices contributing to downtime across intervals
    - Top 5 worst availability over the selected range
    Cache: 60s (unless fresh=1)
    """

    range_config = _get_availability_range_config(request.args.get('range'))
    force_fresh = request.args.get('fresh', '').lower() in ('1', 'true', 'yes')
    scoped_cache_key = f"availability-details:{range_config['key']}:{_scope_cache_suffix()}"
    # Longer TTL for wider ranges — 30d data changes slowly, no need to recompute every 60s
    _AVAILABILITY_CACHE_TTL = {'24h': 60, '7d': 300, '30d': 600}
    cache_ttl = _AVAILABILITY_CACHE_TTL.get(range_config['key'], 60)
    if not force_fresh:
        cached = get_cached(scoped_cache_key, cache_ttl)
        if cached:
            return jsonify(cached)

    try:
        from models.scan_history import DeviceScanHistory

        scoped_devices, _ = _scoped_devices(include_objects=True)
        scoped_ips = {device.device_ip for device in scoped_devices if getattr(device, 'device_ip', None)}
        device_by_ip = {
            device.device_ip: device
            for device in scoped_devices
            if getattr(device, 'device_ip', None)
        }

        now = datetime.utcnow()
        bucket_count = int(range_config['bucket_count'])
        bucket_hours = int(range_config['bucket_hours'])
        bucket_anchor = _floor_utc_bucket_start(now, bucket_hours)
        bucket_start = bucket_anchor - timedelta(hours=(bucket_count - 1) * bucket_hours)
        cutoff = bucket_start

        bucket_times = [
            bucket_start + timedelta(hours=index * bucket_hours)
            for index in range(bucket_count)
        ]
        bucket_device_stats = [dict() for _ in range(bucket_count)]

        if scoped_ips:
            hour_bucket = _availability_hour_bucket_expr(DeviceScanHistory)
            hourly_rows = db.session.query(
                DeviceScanHistory.device_ip.label('device_ip'),
                hour_bucket,
                func.count(DeviceScanHistory.scan_id).label('total'),
                func.sum(case((DeviceScanHistory.status == 'Online', 1), else_=0)).label('online')
            ).filter(
                DeviceScanHistory.scan_timestamp >= cutoff,
                DeviceScanHistory.device_ip.in_(scoped_ips),
            ).group_by(
                DeviceScanHistory.device_ip,
                hour_bucket
            ).limit(50000).all()
        else:
            hourly_rows = []

        for row in hourly_rows:
            hour_ts = _coerce_utc_naive(row.hour)
            if not hour_ts or hour_ts < bucket_start:
                continue
            delta_hours = int((hour_ts - bucket_start).total_seconds() // 3600)
            if delta_hours < 0:
                continue
            bucket_index = delta_hours // bucket_hours
            if bucket_index < 0 or bucket_index >= bucket_count:
                continue

            ip_key = row.device_ip
            if not ip_key:
                continue
            current = bucket_device_stats[bucket_index].get(ip_key)
            if current is None:
                current = {'total': 0, 'online': 0}
                bucket_device_stats[bucket_index][ip_key] = current
            current['total'] += int(row.total or 0)
            current['online'] += int(row.online or 0)

        heatmap = []
        device_interval_stats = {}
        threshold_ratio = AVAILABILITY_ONLINE_INTERVAL_THRESHOLD_PCT / 100.0

        for bucket_index, ts in enumerate(bucket_times):
            device_stats = bucket_device_stats[bucket_index]
            observed_devices = 0
            online_devices = 0

            for ip_key, agg in device_stats.items():
                total = int(agg.get('total') or 0)
                online = int(agg.get('online') or 0)
                if total <= 0:
                    continue
                interval_ratio = online / total
                is_online_interval = interval_ratio >= threshold_ratio
                observed_devices += 1
                if is_online_interval:
                    online_devices += 1

                device_info = device_interval_stats.get(ip_key)
                if device_info is None:
                    scoped_device = device_by_ip.get(ip_key)
                    device_info = {
                        'device_name': getattr(scoped_device, 'device_name', None) or ip_key or 'Unknown',
                        'ip': ip_key,
                        'device_type': getattr(scoped_device, 'device_type', None) or 'Unknown',
                        'observed_intervals': 0,
                        'online_intervals': 0,
                    }
                    device_interval_stats[ip_key] = device_info

                device_info['observed_intervals'] += 1
                if is_online_interval:
                    device_info['online_intervals'] += 1

            pct = round((online_devices / observed_devices) * 100, 1) if observed_devices > 0 else 0.0
            heatmap.append({
                'time': ts.replace(tzinfo=timezone.utc).isoformat(),
                'value': pct,
                'online': int(online_devices),
                'total': int(observed_devices),
                'bucket_hours': bucket_hours,
            })

        devices = []
        for device_info in device_interval_stats.values():
            observed_intervals = int(device_info['observed_intervals'] or 0)
            online_intervals = int(device_info['online_intervals'] or 0)
            down_intervals = max(observed_intervals - online_intervals, 0)
            uptime_pct = round((online_intervals / observed_intervals) * 100, 1) if observed_intervals > 0 else 0.0
            downtime_pct = round(100.0 - uptime_pct, 1) if observed_intervals > 0 else 0.0
            devices.append({
                'device_name': device_info['device_name'],
                'ip': device_info['ip'],
                'device_type': device_info['device_type'],
                'observed_intervals': observed_intervals,
                'online_intervals': online_intervals,
                'down_intervals': down_intervals,
                'uptime_pct': uptime_pct,
                'downtime_pct': downtime_pct,
            })

        downtime_contributors = sorted(
            [d for d in devices if d['down_intervals'] > 0],
            key=lambda d: (d['down_intervals'], d['downtime_pct'], d['observed_intervals']),
            reverse=True
        )[:10]

        worst_availability = sorted(
            [d for d in devices if d['observed_intervals'] > 0],
            key=lambda d: (d['uptime_pct'], -d['observed_intervals'], -d['down_intervals'])
        )[:5]

        snapshot_generated_at = datetime.utcnow().isoformat()
        source_data_freshness_at = None
        if scoped_ips:
            source_data_freshness_at = db.session.query(
                func.max(DeviceScanHistory.scan_timestamp)
            ).filter(
                DeviceScanHistory.scan_timestamp >= cutoff,
                DeviceScanHistory.device_ip.in_(scoped_ips),
            ).scalar()

        bucket_minutes = bucket_hours * 60

        result = {
            'generated_at': snapshot_generated_at,
            'snapshot_generated_at': snapshot_generated_at,
            'source_data_freshness_at': _iso_utc_naive(source_data_freshness_at),
            'range': range_config['key'],
            'bucket_count': bucket_count,
            'bucket_minutes': bucket_minutes,
            'heatmap': heatmap,
            'downtime_contributors': downtime_contributors,
            'worst_availability': worst_availability,
            'meta': {
                'range': range_config['key'],
                'range_label': range_config['label'],
                'bucket_count': bucket_count,
                'bucket_hours': bucket_hours,
                'bucket_minutes': bucket_minutes,
                'bucket_label': f'{bucket_hours}h intervals',
                'interval_online_threshold_pct': AVAILABILITY_ONLINE_INTERVAL_THRESHOLD_PCT,
            },
        }

        set_cached(scoped_cache_key, result, ttl_seconds=60)
        return jsonify(result)

    except Exception:
        logger.exception("Availability details error")
        return jsonify({'error': 'Internal server error'}), 500


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
    
    try:
        from models.device import Device
        from models.snmp_config import DeviceSnmpConfig
        from models.scan_history import DeviceScanHistory
        from models.dashboard import DashboardEvent
        from sqlalchemy import func
        from middleware.rbac import scoped_query
        
        # Get scoped devices first
        scoped_devices = scoped_query(Device).all()
        scoped_device_ids = [d.device_id for d in scoped_devices]
        
        def normalize_inventory_type(value: str) -> str:
            raw = (value or '').strip().lower()
            if raw in ('camera', 'camera/iot', 'camera_iot'):
                return 'Camera/IoT'
            if not raw or raw == 'unknown':
                return 'Unknown'
            return raw.replace('_', ' ').title()

        # 1 & 2. Vendor and Device Type Distribution
        # Optimized: Computed in-memory using the already-loaded `scoped_devices`
        # instead of executing additional `GROUP BY` database queries with massive `IN` clauses.
        by_vendor = {}
        by_type = {}

        for d in scoped_devices:
            # Vendor grouping
            vendor = d.manufacturer or 'Unknown'
            by_vendor[vendor] = by_vendor.get(vendor, 0) + 1

            # Type grouping
            label = normalize_inventory_type(d.device_type)
            by_type[label] = by_type.get(label, 0) + 1
        
        # 3. SNMP Stats
        # SCOPE: total_devices here is scoped to user's site/dept and includes ALL active devices
        # (not filtered by is_monitored). Dashboard KPI uses this as the inventory total.
        total_devices = len(scoped_device_ids)
        snmp_enabled = DeviceSnmpConfig.query.filter(
            DeviceSnmpConfig.device_id.in_(scoped_device_ids) if scoped_device_ids else False,
            DeviceSnmpConfig.is_enabled == True
        ).count()
        
        # 4. Full Device List (for table)
        devices = scoped_devices
        
        # calculate server health for each device (agent metrics only)
        from models.server_health import ServerHealthLog
        
        latest_health_logs = query_latest_server_health_logs(source='agent')

        health_map = {log.device_id: log for log in latest_health_logs}

        scan_pairs_by_ip = {}
        if scoped_device_ips := [d.device_ip for d in scoped_devices if getattr(d, 'device_ip', None)]:
            ordered_scans = (
                DeviceScanHistory.query
                .filter(DeviceScanHistory.device_ip.in_(scoped_device_ips))
                .order_by(DeviceScanHistory.device_ip.asc(), DeviceScanHistory.scan_timestamp.desc(), DeviceScanHistory.scan_id.desc())
                .all()
            )
            for scan in ordered_scans:
                bucket = scan_pairs_by_ip.setdefault(scan.device_ip, [])
                if len(bucket) < 2:
                    bucket.append(scan)

        alert_counts = {}
        if scoped_device_ids:
            alert_rows = db.session.query(
                DashboardEvent.device_id,
                func.count(DashboardEvent.event_id).label('count')
            ).filter(
                DashboardEvent.device_id.in_(scoped_device_ids),
                DashboardEvent.resolved.is_(False),
            ).group_by(DashboardEvent.device_id).all()
            alert_counts = {row.device_id: int(row.count or 0) for row in alert_rows}

        device_list = []
        for d in devices:
            d_dict = d.to_dict()
            latest_scan_pair = scan_pairs_by_ip.get(d.device_ip or '', [])
            latest_scan = latest_scan_pair[0] if latest_scan_pair else None
            previous_scan = latest_scan_pair[1] if len(latest_scan_pair) > 1 else None
            latest_health = health_map.get(d.device_id)

            # Default to unknown/standard
            d_dict['server_health'] = 'Unknown'
            d_dict['active_alert_count'] = int(alert_counts.get(d.device_id, 0))
            d_dict['last_seen'] = None
            d_dict['availability_status'] = 'No Data'
            d_dict['status_label'] = 'No Data'
            d_dict['primary_metric_label'] = 'Latency'
            d_dict['primary_metric_value'] = None
            d_dict['primary_metric_unit'] = 'ms'
            d_dict['primary_metric_trend'] = None

            if is_server_device(d.device_type):
                d_dict['server_health'] = compute_server_health(latest_health)

            if bool(getattr(d, 'maintenance_mode', False)):
                d_dict['availability_status'] = 'Maintenance'
                d_dict['status_label'] = 'Maintenance'
            elif latest_scan:
                latest_scan_status = str(latest_scan.status or '').strip().lower()
                if latest_scan_status == 'online':
                    d_dict['availability_status'] = 'Healthy'
                    d_dict['status_label'] = 'Healthy'
                elif latest_scan_status:
                    d_dict['availability_status'] = 'Critical'
                    d_dict['status_label'] = 'Critical'

            if latest_health and latest_health.timestamp:
                d_dict['last_seen'] = latest_health.timestamp.isoformat()
            elif latest_scan and latest_scan.scan_timestamp:
                d_dict['last_seen'] = latest_scan.scan_timestamp.isoformat()

            if latest_health and is_server_device(d.device_type):
                cpu_now = latest_health.cpu_usage
                d_dict['primary_metric_label'] = 'CPU'
                d_dict['primary_metric_value'] = round(float(cpu_now), 1) if cpu_now is not None else None
                d_dict['primary_metric_unit'] = '%'
                previous_health = None
                for candidate in latest_health_logs:
                    if candidate.device_id == d.device_id and candidate.timestamp != latest_health.timestamp:
                        previous_health = candidate
                        break
                if previous_health and previous_health.cpu_usage is not None and cpu_now is not None:
                    delta = round(float(cpu_now) - float(previous_health.cpu_usage), 1)
                    d_dict['primary_metric_trend'] = delta
            elif latest_scan:
                latency_now = latest_scan.ping_time_ms
                d_dict['primary_metric_label'] = 'Ping'
                d_dict['primary_metric_value'] = round(float(latency_now), 1) if latency_now is not None else None
                d_dict['primary_metric_unit'] = 'ms'
                if previous_scan and previous_scan.ping_time_ms is not None and latency_now is not None:
                    d_dict['primary_metric_trend'] = round(float(latency_now) - float(previous_scan.ping_time_ms), 1)

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

    except Exception:
        logger.exception("Top interfaces error")
        return jsonify({'error': 'Internal server error'}), 500

# ============================================================
# GET /api/dashboard/realtime/network-io
# ============================================================
@dashboard_bp.route('/realtime/network-io')
def get_network_io_trend():
    """
    Returns aggregated Network I/O (Sum of all interfaces) for the last hour.
    """

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

    except Exception:
        logger.exception("Network I/O error")
        return jsonify({'error': 'Internal server error'}), 500
