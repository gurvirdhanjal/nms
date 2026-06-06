from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, abort, current_app
from werkzeug.security import generate_password_hash
from config import Config
from extensions import db
from services.network_scanner import NetworkScanner
from services.device_identity import upsert_device_from_identity, compute_subnet_cidr
from services.settings_service import get_monitoring_interval, format_monitoring_interval_label
from middleware.rbac import require_login, require_permission, has_permission
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
import threading
import time
import uuid
from sqlalchemy import inspect, or_, func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

devices_bp = Blueprint('devices_bp', __name__, url_prefix='')
logger = logging.getLogger(__name__)

_DEVICE_THRESHOLD_DEFAULTS = {
    'cpu_warning': 80.0,
    'cpu_critical': 95.0,
    'memory_warning': 85.0,
    'memory_critical': 95.0,
    'disk_warning': 80.0,
    'disk_critical': 95.0,
}

_bulk_delete_jobs = {}
_bulk_delete_jobs_lock = threading.Lock()


def _is_lock_timeout_error(error: Exception) -> bool:
    """Detect PostgreSQL lock-not-available / lock-timeout errors.

    Check order: SQLSTATE code → exception class name → message substring.
    String matching alone is brittle across driver versions and locales.
    """
    # 1. SQLSTATE (most reliable — set by the server, not the driver)
    orig = getattr(error, "orig", None)
    pgcode = getattr(orig, "pgcode", None)
    if pgcode in ("55P03", "57014", "40P01"):
        # 55P03 lock_not_available, 57014 query_canceled (lock_timeout),
        # 40P01 deadlock_detected
        return True
    # 2. psycopg2 exception class hierarchy
    if orig is not None:
        cls_name = type(orig).__name__
        if cls_name in ("LockNotAvailable", "QueryCanceled", "DeadlockDetected"):
            return True
    # 3. Fallback substring match (last resort)
    message = str(error).lower()
    return (
        "lock not available" in message
        or "lock timeout" in message
        or "canceling statement due to lock timeout" in message
        or "deadlock detected" in message
    )


def _create_bulk_delete_job(*, requested_ids, eligible_ids, batch_size, stopped_scans, initiated_by):
    job_id = uuid.uuid4().hex
    job = {
        'job_id': job_id,
        'status': 'queued',
        'requested_count': len(requested_ids),
        'eligible_count': len(eligible_ids),
        'deleted': 0,
        'processed': 0,
        'errors': [],
        'batch_size': batch_size,
        'stopped_scans': stopped_scans,
        'created_at': datetime.utcnow().isoformat(),
        'started_at': None,
        'finished_at': None,
        'initiated_by': initiated_by,
    }
    with _bulk_delete_jobs_lock:
        _bulk_delete_jobs[job_id] = job
    return job


def _update_bulk_delete_job(job_id, **updates):
    with _bulk_delete_jobs_lock:
        job = _bulk_delete_jobs.get(job_id)
        if not job:
            return None
        job.update(updates)
        return dict(job)


def _default_device_thresholds():
    return dict(_DEVICE_THRESHOLD_DEFAULTS)


def _get_device_surface_context():
    context = {
        'compliance_profiles': [],
        'global_thresholds': _default_device_thresholds(),
        'can_manage_compliance_profiles': str(session.get('role') or '').strip().lower() == 'admin',
    }

    try:
        from models.compliance_profile import ComplianceProfile

        context['compliance_profiles'] = ComplianceProfile.query.order_by(ComplianceProfile.name).all()
    except Exception:
        logger.warning('[Devices] Could not load compliance profiles for device surface', exc_info=True)

    try:
        from services.server_thresholds import get_merged_thresholds

        metrics = (get_merged_thresholds() or {}).get('metrics', {})
        context['global_thresholds'] = {
            'cpu_warning': float(metrics.get('cpu_usage_pct', {}).get('warning', _DEVICE_THRESHOLD_DEFAULTS['cpu_warning'])),
            'cpu_critical': float(metrics.get('cpu_usage_pct', {}).get('critical', _DEVICE_THRESHOLD_DEFAULTS['cpu_critical'])),
            'memory_warning': float(metrics.get('memory_usage_pct', {}).get('warning', _DEVICE_THRESHOLD_DEFAULTS['memory_warning'])),
            'memory_critical': float(metrics.get('memory_usage_pct', {}).get('critical', _DEVICE_THRESHOLD_DEFAULTS['memory_critical'])),
            'disk_warning': float(metrics.get('disk_usage_pct', {}).get('warning', _DEVICE_THRESHOLD_DEFAULTS['disk_warning'])),
            'disk_critical': float(metrics.get('disk_usage_pct', {}).get('critical', _DEVICE_THRESHOLD_DEFAULTS['disk_critical'])),
        }
    except Exception:
        logger.warning('[Devices] Could not load global threshold defaults for compliance preview', exc_info=True)

    return context


def _parse_limit(default: int = 100, max_val: int = 500) -> int:
    """Parse and cap the ?limit= query parameter."""
    return min(max(1, request.args.get('limit', default, type=int)), max_val)


def _iso_utc(ts):
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _exclude_agent_push_scan(DeviceScanHistory):
    """Ignore agent HTTP round-trip rows when building ICMP latency views."""
    return or_(
        DeviceScanHistory.scan_type.is_(None),
        DeviceScanHistory.scan_type != 'agent_push',
    )


_IST = timezone(timedelta(hours=5, minutes=30))
_WORKSTATION_TYPES = {'workstation', 'desktop', 'laptop', 'pc'}


def _format_ist(ts, fallback='Not available', include_tz=True):
    if not ts:
        return fallback
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    localized = ts.astimezone(_IST)
    suffix = ' IST' if include_tz else ''
    return localized.strftime('%d/%m/%Y, %H:%M:%S') + suffix


def _json_error_response(*, code, message, status, connections=None, agent_snapshot=None, meta=None):
    payload = {
        'error': {
            'code': code,
            'message': message,
        },
        'connections': connections if connections is not None else [],
        'agent_snapshot': agent_snapshot if agent_snapshot is not None else {
            'top_remote_ips': [],
            'unique_remote_ips_count': None,
            'timestamp': None,
        },
        'meta': meta if meta is not None else {},
    }
    return jsonify(payload), status


def _normalize_snmp_version(version):
    normalized = (version or "2c").strip().lower().replace("v", "")
    if normalized in ("1", "2c", "3"):
        return normalized
    return "2c"


def _upsert_device_snmp_config(
    device,
    monitoring_mode,
    is_monitored,
    snmp_version,
    snmp_port,
    snmp_community,
    snmp_username,
    snmp_auth_proto,
    snmp_auth_password,
    snmp_priv_proto,
    snmp_priv_password,
):
    from models.snmp_config import DeviceSnmpConfig

    normalized_version = _normalize_snmp_version(snmp_version)
    should_track = bool(device.snmp_config) or monitoring_mode in ("snmp", "agent") or bool((snmp_community or "").strip())
    if not should_track:
        return
    if not device.snmp_config:
        device.snmp_config = DeviceSnmpConfig()
    config = device.snmp_config

    config.snmp_version = normalized_version
    config.snmp_port = int(snmp_port or 161)
    config.community_string = (snmp_community or "public").strip() or "public"
    config.security_name = (snmp_username or "").strip() or None
    config.auth_protocol = (snmp_auth_proto or "").strip() or None
    config.auth_password = (snmp_auth_password or "").strip() or None
    config.priv_protocol = (snmp_priv_proto or "").strip() or None
    config.priv_password = (snmp_priv_password or "").strip() or None
    config.is_enabled = bool(is_monitored and monitoring_mode in ("snmp", "agent"))

    db.session.add(config)



@devices_bp.before_request
@require_login
def _devices_auth_guard():
    return None

from services.discovery_service import get_discovery_service


def _delete_device_with_dependencies(device, existing_tables=None):
    """Delete one device and its dependent rows that do not cascade automatically."""
    from models.device import Device
    from models.interfaces import DeviceInterface
    from models.topology import SwitchTopology

    device_id = device.device_id
    device_ip = device.device_ip
    if existing_tables is None:
        existing_tables = set(inspect(db.engine).get_table_names())

    interface_ids = [
        row[0]
        for row in db.session.query(DeviceInterface.interface_id).filter_by(device_id=device_id).all()
    ]

    # Break self/peer FK links first.
    Device.query.filter(Device.parent_switch_id == device_id).update(
        {Device.parent_switch_id: None},
        synchronize_session=False
    )
    if interface_ids:
        Device.query.filter(Device.parent_port_id.in_(interface_ids)).update(
            {Device.parent_port_id: None},
            synchronize_session=False
        )

    # Remove topology rows that point to this device or its interfaces.
    SwitchTopology.query.filter(
        or_(
            SwitchTopology.local_device_id == device_id,
            SwitchTopology.remote_device_id == device_id
        )
    ).delete(synchronize_session=False)
    if interface_ids:
        SwitchTopology.query.filter(
            SwitchTopology.local_interface_id.in_(interface_ids)
        ).delete(synchronize_session=False)

    # Cleanup tables without guaranteed FK cascade support.
    if 'device_scan_history' in existing_tables:
        from models.scan_history import DeviceScanHistory
        DeviceScanHistory.query.filter_by(device_ip=device_ip).delete(synchronize_session=False)

    if 'dashboard_events' in existing_tables:
        from models.dashboard import DashboardEvent
        DashboardEvent.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'daily_device_stats' in existing_tables:
        from models.dashboard import DailyDeviceStats
        DailyDeviceStats.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'device_snmp_config' in existing_tables:
        from models.snmp_config import DeviceSnmpConfig
        DeviceSnmpConfig.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'poll_tasks' in existing_tables:
        from models.poll_task import PollTask
        PollTask.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'server_health_hourly_rollups' in existing_tables:
        from models.server_health_rollups import ServerHealthHourlyRollup
        ServerHealthHourlyRollup.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    if 'server_health_daily_rollups' in existing_tables:
        from models.server_health_rollups import ServerHealthDailyRollup
        ServerHealthDailyRollup.query.filter_by(device_id=device_id).delete(synchronize_session=False)

    db.session.delete(device)


def _run_bulk_delete_job(*, app, job_id, device_ids, batch_size, audit_context=None):
    from models.device import Device

    with app.app_context():
        existing_tables = set(inspect(db.engine).get_table_names())
        deleted_count = 0
        errors = []
        deferred_ids = []

        _update_bulk_delete_job(
            job_id,
            status='running',
            started_at=datetime.utcnow().isoformat(),
        )

        # ── Phase 1: mark rows delete_pending so the monitor skips them ──────
        # This is a short, low-contention write that removes the race window
        # before we attempt the heavier cascading delete.
        try:
            Device.query.filter(Device.device_id.in_(device_ids)).update(
                {Device.delete_pending: True},
                synchronize_session=False,
            )
            db.session.commit()
        except Exception as mark_err:
            logger.warning("Bulk delete: could not mark delete_pending: %s", mark_err)
            db.session.rollback()

        def _try_delete_one(dev_id, *, nowait=False):
            """Attempt to delete one device. Returns True=deleted, None=retry, False=error."""
            try:
                with db.session.begin_nested():
                    q = Device.query.filter(Device.device_id == dev_id)
                    # SKIP LOCKED on first pass: if the monitor still holds the
                    # row (rare after delete_pending), defer rather than error.
                    # NOWAIT on retry pass: by then the monitor has moved on.
                    if nowait:
                        q = q.with_for_update(nowait=True)
                    else:
                        q = q.with_for_update(skip_locked=True)
                    device = q.first()
                    if device is None:
                        # Row is locked (skip_locked) or already gone.
                        return None if not nowait else True
                    _delete_device_with_dependencies(device, existing_tables=existing_tables)
                return True
            except OperationalError as e:
                db.session.rollback()
                if _is_lock_timeout_error(e):
                    logger.warning(
                        "Bulk delete lock/contention: device_id=%s — deferring", dev_id
                    )
                    return None  # retry later
                logger.warning("Bulk delete OperationalError: device_id=%s error=%s", dev_id, e)
                errors.append(f"Error deleting ID {dev_id}: {str(e)}")
                return False
            except Exception as e:
                db.session.rollback()
                logger.warning("Bulk delete failure: device_id=%s error=%s", dev_id, e)
                errors.append(f"Error deleting ID {dev_id}: {str(e)}")
                return False

        # ── Phase 2: delete in batches ────────────────────────────────────────
        for start in range(0, len(device_ids), batch_size):
            batch_ids = device_ids[start:start + batch_size]

            for dev_id in batch_ids:
                result = _try_delete_one(dev_id, nowait=False)
                if result is True:
                    deleted_count += 1
                elif result is None:
                    deferred_ids.append(dev_id)

            try:
                db.session.commit()
            except Exception as commit_err:
                logger.warning("Bulk delete batch commit failed: %s", commit_err)
                db.session.rollback()

            _update_bulk_delete_job(
                job_id,
                processed=min(start + len(batch_ids), len(device_ids)),
                deleted=deleted_count,
                errors=errors[:50],
            )
            time.sleep(0.05)

        # ── Phase 3: retry deferred rows ─────────────────────────────────────
        # Wait briefly so any in-flight monitor transaction can commit and
        # release its row lock before we retry with NOWAIT.
        if deferred_ids:
            logger.info("Bulk delete: retrying %d deferred devices", len(deferred_ids))
            time.sleep(1.5)
            for dev_id in deferred_ids:
                result = _try_delete_one(dev_id, nowait=True)
                if result is True:
                    deleted_count += 1
                elif result is None:
                    errors.append(f"Error deleting ID {dev_id}: still locked after retry — skipped")
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.warning("Bulk delete deferred commit failed: %s", e)

        _update_bulk_delete_job(
            job_id,
            status='completed',
            processed=len(device_ids),
            deleted=deleted_count,
            errors=errors[:50],
            finished_at=datetime.utcnow().isoformat(),
        )

        # ── Audit log — uses explicit context, no Flask session needed ────────
        if deleted_count > 0:
            try:
                from middleware.rbac import create_audit_log
                create_audit_log(
                    action='bulk_delete',
                    entity_type='device',
                    description=f"Bulk device deletion: {deleted_count} devices deleted",
                    context=audit_context,  # plain dict, no Flask proxies
                )
            except Exception:
                logger.warning("Bulk delete audit logging failed for job_id=%s", job_id, exc_info=True)


def _normalize_status_filter(raw_status):
    status_map = {
        'online': 'Online',
        'offline': 'Offline',
        'maintenance': 'Maintenance',
    }
    return status_map.get((raw_status or '').strip().lower(), '')


def _normalize_device_status(raw_status):
    """Normalize scan status into the UI's availability buckets."""
    value = (raw_status or '').strip().lower()
    if value in ('online', 'up'):
        return 'Online'
    if value in ('maintenance', 'maintaince'):
        return 'Maintenance'
    return 'Offline'


def _load_latest_scan_statuses(device_ips, *, DeviceScanHistory):
    """Return latest normalized status keyed by device_ip."""
    ips = sorted({ip for ip in device_ips if ip})
    if not ips:
        return {}

    latest_scan_subq = db.session.query(
        DeviceScanHistory.device_ip.label('device_ip'),
        func.max(DeviceScanHistory.scan_id).label('max_scan_id')
    ).filter(
        DeviceScanHistory.device_ip.in_(ips)
    ).group_by(DeviceScanHistory.device_ip).subquery()

    latest_rows = db.session.query(
        DeviceScanHistory.device_ip,
        DeviceScanHistory.status
    ).join(
        latest_scan_subq,
        (DeviceScanHistory.device_ip == latest_scan_subq.c.device_ip)
        & (DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id)
    ).all()

    return {
        row.device_ip: _normalize_device_status(row.status)
        for row in latest_rows
    }


def _resolve_device_status(device, latest_status_by_ip):
    if getattr(device, 'maintenance_mode', False):
        return 'Maintenance'
    return latest_status_by_ip.get(device.device_ip, 'Offline')


def _device_inventory_payload(device, *, latest_status_by_ip=None, snmp_config=None):
    payload = device.to_dict()
    cfg = snmp_config if snmp_config is not None else getattr(device, 'snmp_config', None)
    payload['monitoring_mode'] = (device.monitoring_mode or 'ping')
    payload['snmp_enabled'] = bool(cfg.is_enabled) if cfg else False
    payload['snmp_last_error'] = cfg.last_poll_error if cfg else None
    payload['snmp_last_poll'] = _iso_utc(cfg.last_successful_poll) if cfg and cfg.last_successful_poll else None
    if latest_status_by_ip is not None:
        payload['status'] = _resolve_device_status(device, latest_status_by_ip)
    return payload


def _apply_device_filters(query, *, Device, DeviceScanHistory, search='', device_type='', subnet='', status=''):
    """Apply consistent device filters for UI pages and cross-page selection endpoints."""
    filtered_query = query

    if subnet:
        filtered_query = filtered_query.filter(Device.subnet_cidr == subnet)

    if device_type and device_type != 'all':
        if device_type in ('camera', 'camera/iot', 'camera_iot'):
            filtered_query = filtered_query.filter(
                Device.device_type.in_(['camera', 'camera/iot', 'camera_iot'])
            )
        else:
            filtered_query = filtered_query.filter(Device.device_type == device_type)

    if search:
        pattern = f"%{search}%"
        filtered_query = filtered_query.filter(or_(
            Device.device_name.ilike(pattern),
            Device.device_ip.ilike(pattern),
            Device.hostname.ilike(pattern),
            Device.macaddress.ilike(pattern),
            Device.manufacturer.ilike(pattern)
        ))

    normalized_status = _normalize_status_filter(status)
    if normalized_status == 'Maintenance':
        filtered_query = filtered_query.filter(Device.maintenance_mode.is_(True))
    elif normalized_status in ('Online', 'Offline'):
        latest_scan_subq = db.session.query(
            DeviceScanHistory.device_ip.label('device_ip'),
            func.max(DeviceScanHistory.scan_id).label('max_scan_id')
        ).group_by(DeviceScanHistory.device_ip).subquery()

        if normalized_status == 'Online':
            filtered_query = (
                filtered_query
                .filter(Device.maintenance_mode.is_(False))
                .join(latest_scan_subq, Device.device_ip == latest_scan_subq.c.device_ip)
                .join(DeviceScanHistory, DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id)
                .filter(func.lower(DeviceScanHistory.status) == 'online')
            )
        else:
            # "Offline" includes stale/unknown/no-scan devices for KPI consistency.
            filtered_query = (
                filtered_query
                .filter(Device.maintenance_mode.is_(False))
                .outerjoin(latest_scan_subq, Device.device_ip == latest_scan_subq.c.device_ip)
                .outerjoin(DeviceScanHistory, DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id)
                .filter(or_(
                    DeviceScanHistory.scan_id.is_(None),
                    func.lower(DeviceScanHistory.status) != 'online'
                ))
            )

    return filtered_query

@devices_bp.route('/devices')
@require_login
def device_management():
    try:
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        from models.snmp_config import DeviceSnmpConfig

        page = request.args.get('page', default=1, type=int) or 1
        per_page = request.args.get('per_page', default=100, type=int) or 100
        allowed_per_page = {50, 100, 200}
        if per_page not in allowed_per_page:
            per_page = 100

        active_search = (request.args.get('search') or '').strip()
        active_type = (request.args.get('type') or '').strip().lower()
        active_subnet = (request.args.get('subnet') or '').strip()
        active_status = _normalize_status_filter(request.args.get('status'))

        # RBAC: scope devices to the current user's department/site (admins see all)
        from middleware.rbac import scoped_query
        base_query = scoped_query(Device)
        filtered_query = _apply_device_filters(
            base_query,
            Device=Device,
            DeviceScanHistory=DeviceScanHistory,
            search=active_search,
            device_type=active_type,
            subnet=active_subnet,
            status=active_status,
        )

        devices_query = filtered_query.order_by(Device.device_ip.asc())
        global_device_count = base_query.count()
        total_devices = devices_query.count()
        total_pages = max((total_devices + per_page - 1) // per_page, 1)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page
        devices = devices_query.offset(offset).limit(per_page).all()
        device_ids = [d.device_id for d in devices]
        snmp_by_device_id = {}
        if device_ids:
            configs = DeviceSnmpConfig.query.filter(DeviceSnmpConfig.device_id.in_(device_ids)).all()
            snmp_by_device_id = {cfg.device_id: cfg for cfg in configs}
        for d in devices:
            cfg = snmp_by_device_id.get(d.device_id)
            d.snmp_enabled = bool(cfg.is_enabled) if cfg else False
            d.snmp_last_poll = cfg.last_successful_poll if cfg else None
            d.snmp_last_error = cfg.last_poll_error if cfg else None
        logger.debug("Found %d devices in database", len(devices))
        
        device = None
        
        prefill_data = None
        if request.args.get('prefill') == 'true':
            prefill_data = {
                'device_ip': request.args.get('ip'),
                'hostname': request.args.get('hostname'),
                'macaddress': request.args.get('mac')
            }

        if 'edit_id' in request.args:
            device = scoped_query(Device).get(request.args.get('edit_id'))
            logger.debug("Editing device %s", device)

        if 'delete_id' in request.args:
            device = scoped_query(Device).get(request.args.get('delete_id'))
            if device:
                try:
                    _delete_device_with_dependencies(device)
                    db.session.commit()
                    logger.debug("Deleted device %s", device.device_id)
                except Exception as del_err:
                    db.session.rollback()
                    logger.error("[Devices] Failed to delete device %s: %s", device.device_id, del_err)
            redirect_params = {
                'page': page,
                'per_page': per_page,
                'search': active_search,
                'type': active_type,
                'subnet': active_subnet,
                'status': active_status,
            }
            return redirect(url_for('devices_bp.device_management', **redirect_params))

        # Count devices that still need auto-classification
        unclassified_count = 0
        for d in devices:
            dtype = (d.device_type or "").strip().lower()
            conf = (d.classification_confidence or "").strip().lower()
            if conf == "manual":
                continue
            if dtype in ("", "unknown", "network device"):
                unclassified_count += 1

        device_surface_context = _get_device_surface_context()

        return render_template(
            'devices.html',
            devices=devices,
            device=device,
            prefill_data=prefill_data,
            unclassified_count=unclassified_count,
            **device_surface_context,
            subnets=[
                row[0]
                for row in db.session.query(Device.subnet_cidr)
                .filter(Device.subnet_cidr.isnot(None))
                .distinct()
                .order_by(Device.subnet_cidr.asc())
                .all()
            ],
            page=page,
            per_page=per_page,
            total_devices=total_devices,
            total_pages=total_pages,
            global_device_count=global_device_count,
            active_search=active_search,
            active_type=active_type,
            active_subnet=active_subnet,
            active_status=active_status
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Internal Error: {str(e)} <br> <pre>{traceback.format_exc()}</pre>", 500


from services.snmp_service import snmp_service

@devices_bp.route('/api/check_connectivity', methods=['POST'])
def check_connectivity():
    data = request.get_json()
    ip = data.get('ip')
    mode = data.get('mode', 'ping')
    
    if not ip:
        return jsonify({'success': False, 'message': 'IP Address is required'})
    
    scanner = get_discovery_service().scanner
    
    try:
        if mode == 'ping':
            status, latency, packet_loss, *_extra = asyncio.run(scanner.ping_device(ip, timeout=2, count=2))
            status_detail = _extra[2] if len(_extra) >= 3 else None
            if status == 'Online':
                return jsonify({
                    'success': True, 
                    'message': f"Ping successful ({latency}ms)",
                    'latency': latency,
                    'status_detail': status_detail,
                })
            else:
                return jsonify({
                    'success': False,
                    'message': status_detail or 'Ping failed (Host unreachable)',
                    'status_detail': status_detail,
                })
        
        elif mode == 'snmp':
            community = data.get('snmp_community', 'public')
            version = data.get('snmp_version', 'v2c')
            port = int(data.get('snmp_port', 161))
            
            # Use sync wrapper for simplicity or async if available
            sys_info = snmp_service.get_system_info(ip, community, version, port)
            
            if 'error' in sys_info:
                return jsonify({'success': False, 'message': f"SNMP Failed: {sys_info['error']}"})
            else:
                return jsonify({
                    'success': True,
                    'message': f"SNMP Connected: {sys_info.get('sys_descr', 'System info retrieved')}"
                })
                
        elif mode == 'agent':
            # Check tactical agent
            agent_info = asyncio.run(scanner.check_tactical_agent(ip))
            if agent_info:
                return jsonify({
                    'success': True,
                    'message': f"Agent Detected: {agent_info.get('agent_version', 'Unknown Version')}"
                })
            else:
                return jsonify({'success': False, 'message': 'Agent not detected on port 5002'})
        
        elif mode == 'wmi':
            # Basic port check for RPC (135) or SMB (445)
            # Using scanner.check_port
            is_rpc = asyncio.run(scanner.check_port(ip, 135, timeout=2))
            if is_rpc and is_rpc[1]:
                 return jsonify({'success': True, 'message': 'WMI Port (RPC 135) is reachable'})
            
            is_smb = asyncio.run(scanner.check_port(ip, 445, timeout=2))
            if is_smb and is_smb[1]:
                 return jsonify({'success': True, 'message': 'WMI Port (SMB 445) is reachable'})
                 
            return jsonify({'success': False, 'message': 'WMI Ports (135/445) unreachable'})
            
        else:
            return jsonify({'success': False, 'message': 'Unknown monitoring mode'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@devices_bp.route('/devices/save', methods=['POST'])
@require_permission('devices.edit')
def save_device():
    try:
        from models.device import Device
        device_id = request.form.get('device_id')
        device_name = request.form['device_name']
        device_ip = request.form['device_ip']
        device_type = request.form['device_type']
        
        # Identity
        hostname = request.form.get('hostname', 'Unknown')
        mac_address = request.form.get('macaddress', 'N/A')
        manufacturer = request.form.get('manufacturer', 'Unknown')
        location = request.form.get('location', '')
        description = request.form.get('description', '')
        
        # Site & Department
        site_id = request.form.get('site_id') or None
        department_id = request.form.get('department_id') or None
        if site_id:
            site_id = int(site_id)
        if department_id:
            department_id = int(department_id)
            from models.department import Department
            department = Department.query.get(department_id)
            if not department:
                return jsonify({'success': False, 'message': 'Department not found'}), 400
            site_id = department.site_id
        
        # Monitoring Config
        is_monitored = request.form.get('is_monitored') == 'on'
        monitoring_mode = request.form.get('monitoring_mode', 'ping')
        
        # SNMP
        snmp_version = request.form.get('snmp_version', 'v2c')
        snmp_community = request.form.get('snmp_community', '')
        snmp_port = int(request.form.get('snmp_port', 161))
        snmp_timeout = int(request.form.get('snmp_timeout', 2))
        snmp_retries = int(request.form.get('snmp_retries', 1))
        snmp_username = request.form.get('snmp_username', '')
        snmp_auth_proto = request.form.get('snmp_auth_proto', '')
        snmp_auth_password = request.form.get('snmp_auth_password', '')
        snmp_priv_proto = request.form.get('snmp_priv_proto', '')
        snmp_priv_password = request.form.get('snmp_priv_password', '')

        # Agent
        agent_token = (request.form.get('agent_token', '') or '').strip()
        try:
            agent_interval = int(request.form.get('agent_interval', 300))
        except (TypeError, ValueError):
            agent_interval = 300
        if agent_interval <= 0:
            agent_interval = 300
        agent_os_type = (request.form.get('agent_os_type', '') or '').strip()

        # WMI
        wmi_username = request.form.get('wmi_username', '')
        wmi_password = request.form.get('wmi_password', '')
        wmi_domain = request.form.get('wmi_domain', '')
        
        # Operational
        maintenance_mode = request.form.get('maintenance_mode') == 'on'

        # Device Credentials
        device_username = request.form.get('device_username', '').strip() or None
        device_password_raw = request.form.get('device_password', '').strip()

        # Compliance profile
        _cp_raw = request.form.get('compliance_profile_id') or None
        compliance_profile_id = int(_cp_raw) if _cp_raw else None

        # Legacy fields mapping
        port = request.form.get('port', str(snmp_port))
        rstplink = request.form.get('rstplink')
        if rstplink is not None:
            rstplink = rstplink.strip() or None

        # Get shared scanner instance
        scanner = get_discovery_service().scanner

        # Get network information - fast path only
        status, latency, _packet_loss = "Unknown", None, 0.0
        if is_monitored and monitoring_mode == 'ping':
            try:
                # Fast timeout
                status, latency, _packet_loss, *_ = asyncio.run(scanner.ping_device(device_ip, timeout=1, count=1))
            except Exception:
                status, latency, _packet_loss = "Offline", None, 100.0
        
        # NOTE: We skip synchronous MAC/Hostname enrichment here to prevent UI blocking.
        # The background scanner will pick this up later.

        try:
            before_snapshot: dict = {}
            if device_id:
                # Update existing device
                from middleware.rbac import scoped_query
                device = scoped_query(Device).get(device_id)
                if not device:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({'success': False, 'message': 'Device not found'}), 404
                    return redirect(url_for('devices_bp.device_management'))
                before_snapshot = device.to_dict()
                device.device_name = device_name
                device.device_ip = device_ip
                device.device_type = device_type
                
                device.location = location
                device.description = description
                device.site_id = site_id
                device.department_id = department_id
                device.monitoring_mode = monitoring_mode
                
                # SNMP
                device.snmp_version = snmp_version
                device.snmp_community = snmp_community
                device.snmp_port = snmp_port
                device.snmp_timeout = snmp_timeout
                device.snmp_retries = snmp_retries
                device.snmp_username = snmp_username
                device.snmp_auth_proto = snmp_auth_proto
                device.snmp_auth_password = snmp_auth_password
                device.snmp_priv_proto = snmp_priv_proto
                device.snmp_priv_password = snmp_priv_password
                
                # Agent: preserve existing token unless a new non-empty token is provided.
                if agent_token:
                    device.agent_token = agent_token
                device.agent_interval = agent_interval
                device.agent_os_type = agent_os_type
                
                # WMI
                device.wmi_username = wmi_username
                device.wmi_password = wmi_password
                device.wmi_domain = wmi_domain
                
                device.maintenance_mode = maintenance_mode
                
                # Legacy & Common
                device.port = port
                if rstplink is not None:
                    device.rstplink = rstplink
                device.macaddress = mac_address
                device.hostname = hostname
                device.manufacturer = manufacturer
                device.is_monitored = is_monitored
                device.subnet_cidr = compute_subnet_cidr(device_ip)
                device.compliance_profile_id = compliance_profile_id

                # Credentials
                device.device_username = device_username
                # Only update password hash if a new password was provided
                if device_password_raw:
                    device.device_password_hash = generate_password_hash(
                        device_password_raw, method='pbkdf2:sha256', salt_length=16
                    )
            else:
                # Create new device
                device = Device(
                    device_name=device_name,
                    device_ip=device_ip,
                    device_type=device_type,
                    location=location,
                    description=description,
                    site_id=site_id,
                    department_id=department_id,
                    monitoring_mode=monitoring_mode,
                    subnet_cidr=compute_subnet_cidr(device_ip),
                    
                    # SNMP
                    snmp_version=snmp_version,
                    snmp_community=snmp_community,
                    snmp_port=snmp_port,
                    snmp_timeout=snmp_timeout,
                    snmp_retries=snmp_retries,
                    snmp_username=snmp_username,
                    snmp_auth_proto=snmp_auth_proto,
                    snmp_auth_password=snmp_auth_password,
                    snmp_priv_proto=snmp_priv_proto,
                    snmp_priv_password=snmp_priv_password,
                    
                    # Agent
                    agent_token=agent_token,
                    agent_interval=agent_interval,
                    agent_os_type=agent_os_type,
                    
                    # WMI
                    wmi_username=wmi_username,
                    wmi_password=wmi_password,
                    wmi_domain=wmi_domain,
                    
                    maintenance_mode=maintenance_mode,
                    compliance_profile_id=compliance_profile_id,

                    port=port,
                    rstplink=rstplink,
                    macaddress=mac_address,
                    hostname=hostname,
                    manufacturer=manufacturer,
                    is_monitored=is_monitored,

                    # Credentials
                    device_username=device_username,
                    device_password_hash=generate_password_hash(
                        device_password_raw, method='pbkdf2:sha256', salt_length=16
                    ) if device_password_raw else None
                )
                db.session.add(device)

            db.session.flush()
            _upsert_device_snmp_config(
                device=device,
                monitoring_mode=monitoring_mode,
                is_monitored=is_monitored,
                snmp_version=snmp_version,
                snmp_port=snmp_port,
                snmp_community=snmp_community,
                snmp_username=snmp_username,
                snmp_auth_proto=snmp_auth_proto,
                snmp_auth_password=snmp_auth_password,
                snmp_priv_proto=snmp_priv_proto,
                snmp_priv_password=snmp_priv_password,
            )
            db.session.commit()
            
            # Audit logging with field-level diff for updates
            from middleware.rbac import create_audit_log
            from utils.audit_helpers import capture_model_diff
            action = 'update' if device_id else 'create'
            changes = capture_model_diff(before_snapshot, device) if before_snapshot else {}
            create_audit_log(
                action=action,
                entity_type='device',
                entity_id=device.device_id,
                entity_name=device.device_name or device.device_ip,
                description=f"Device {action}d: {device.device_name or device.device_ip} ({device.device_ip})",
                changes=changes or None,
            )
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                from models.scan_history import DeviceScanHistory
                latest_status_by_ip = _load_latest_scan_statuses(
                    [device.device_ip],
                    DeviceScanHistory=DeviceScanHistory,
                )
                return jsonify({
                    'success': True,
                    'device_id': device.device_id,
                    'device': _device_inventory_payload(device, latest_status_by_ip=latest_status_by_ip),
                }), 200
            return redirect(url_for('devices_bp.device_management'))
        except Exception as e:
            db.session.rollback()
            db.session.remove()
            logger.error(f"[Devices] Failed to save device: {e}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': f'Error saving device: {str(e)}'}), 500
            from models.device import Device
            devices = Device.query.options(
                selectinload(Device.snmp_config),
            ).all()
            return render_template(
                'devices.html',
                devices=devices,
                error=f"Error saving device: {str(e)}",
                **_get_device_surface_context(),
            ), 500

    except Exception as e:
        db.session.rollback()
        db.session.remove()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': f'Error saving device: {str(e)}'}), 500
        from models.device import Device
        devices = Device.query.options(
            selectinload(Device.snmp_config),
        ).all()
        return render_template(
            'devices.html',
            devices=devices,
            error=f"Error saving device: {str(e)}",
            **_get_device_surface_context(),
        )

@devices_bp.route('/api/devices/subnets')
def api_device_subnets():
    """Return sorted list of distinct subnet_cidr values."""
    from models.device import Device
    rows = db.session.query(Device.subnet_cidr).distinct().limit(500).all()
    subnets = sorted([r[0] for r in rows if r[0]])
    return jsonify(subnets)

@devices_bp.route('/api/devices')
@require_login
def api_devices():
    from models.device import Device
    from models.scan_history import DeviceScanHistory

    search = (request.args.get('search') or '').strip()
    device_type = (request.args.get('type') or '').strip().lower()
    subnet = (request.args.get('subnet') or '').strip()
    status = _normalize_status_filter(request.args.get('status'))
    
    # RBAC: scope API results to current user's department/site
    from middleware.rbac import scoped_query
    base_q = scoped_query(Device)
    
    query = _apply_device_filters(
        base_q,
        Device=Device,
        DeviceScanHistory=DeviceScanHistory,
        search=search,
        device_type=device_type,
        subnet=subnet,
        status=status,
    )
    query = query.order_by(Device.device_ip.asc())
    
    if request.args.get('page') or request.args.get('paginate') == 'true':
        page = request.args.get('page', default=1, type=int)
        per_page = request.args.get('per_page', default=100, type=int)
        
        allowed_per_page = {50, 100, 200, 500}
        if per_page not in allowed_per_page:
            per_page = 100
            
        total_devices = query.count()
        total_pages = max((total_devices + per_page - 1) // per_page, 1)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * per_page
        devices = query.offset(offset).limit(per_page).all()
        
        device_ids = [d.device_id for d in devices]
        from models.snmp_config import DeviceSnmpConfig
        snmp_by_device_id = {}
        if device_ids:
            configs = DeviceSnmpConfig.query.filter(DeviceSnmpConfig.device_id.in_(device_ids)).all()
            snmp_by_device_id = {cfg.device_id: cfg for cfg in configs}
            
        result = []
        latest_status_by_ip = _load_latest_scan_statuses(
            [d.device_ip for d in devices],
            DeviceScanHistory=DeviceScanHistory,
        )
        for d in devices:
            cfg = snmp_by_device_id.get(d.device_id)
            result.append(
                _device_inventory_payload(
                    d,
                    latest_status_by_ip=latest_status_by_ip,
                    snmp_config=cfg,
                )
            )
            
        return jsonify({
            'devices': result,
            'total_devices': total_devices,
            'total_pages': total_pages,
            'page': page,
            'per_page': per_page,
            'global_device_count': Device.query.count()
        })
    else:
        devices = query.all()
        latest_status_by_ip = _load_latest_scan_statuses(
            [d.device_ip for d in devices],
            DeviceScanHistory=DeviceScanHistory,
        )
        device_dicts = []
        for d in devices:
            device_dicts.append(_device_inventory_payload(d, latest_status_by_ip=latest_status_by_ip))
        return jsonify(device_dicts)
@devices_bp.route('/api/devices/filter_ids')
def api_filtered_device_ids():
    """Return matching device IDs for current filters (for cross-page bulk selection)."""
    from models.device import Device
    from models.scan_history import DeviceScanHistory
    from middleware.rbac import scoped_query

    search = (request.args.get('search') or '').strip()
    device_type = (request.args.get('device_type') or '').strip().lower()
    subnet = (request.args.get('subnet') or '').strip()
    status = _normalize_status_filter(request.args.get('status'))

    max_ids = request.args.get('max_ids', default=100000, type=int) or 100000
    max_ids = max(1, min(max_ids, 100000))

    query = _apply_device_filters(
        scoped_query(Device),
        Device=Device,
        DeviceScanHistory=DeviceScanHistory,
        search=search,
        device_type=device_type,
        subnet=subnet,
        status=status,
    )

    total_matched = query.count()
    rows = (
        query.order_by(Device.device_id.asc())
        .with_entities(Device.device_id)
        .limit(max_ids + 1)
        .all()
    )

    truncated = len(rows) > max_ids
    if truncated:
        rows = rows[:max_ids]

    return jsonify({
        'device_ids': [row[0] for row in rows],
        'total_matched': total_matched,
        'selected_count': len(rows),
        'truncated': truncated,
        'max_ids': max_ids,
        'status_filter_applied': bool(status),
    })

@devices_bp.route('/api/devices/<int:device_id>')
@require_login
def api_device_detail(device_id):
    from models.device import Device
    from models.snmp_config import DeviceSnmpConfig
    from middleware.rbac import scoped_query
    device = scoped_query(Device).get(device_id)
    if device:
        snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device.device_id).first()
        device_data = device.to_dict()
        device_data.update({
            'monitoring_mode': (device.monitoring_mode or 'ping'),
            'agent_interval': int(device.agent_interval or 300),
            'agent_os_type': (device.agent_os_type or ''),
            'snmp_enabled': bool(snmp_config.is_enabled) if snmp_config else False,
            'snmp_last_error': (snmp_config.last_poll_error if snmp_config else None),
            'snmp_last_poll': _iso_utc(snmp_config.last_successful_poll) if snmp_config and snmp_config.last_successful_poll else None,
            'snmp_config': {
                'snmp_version': (snmp_config.snmp_version if snmp_config else None),
                'snmp_port': (snmp_config.snmp_port if snmp_config else None),
                'community_string': (snmp_config.community_string if snmp_config else None),
                'security_name': (snmp_config.security_name if snmp_config else None),
                'auth_protocol': (snmp_config.auth_protocol if snmp_config else None),
                'auth_password': (snmp_config.auth_password if snmp_config else None),
                'priv_protocol': (snmp_config.priv_protocol if snmp_config else None),
                'priv_password': (snmp_config.priv_password if snmp_config else None),
            },
        })
        if has_permission('devices.edit'):
            device_data['agent_token'] = (device.agent_token or '')
        return jsonify({'success': True, 'device': device_data})
    else:
        return jsonify({'success': False, 'error': 'Device not found'}), 404


def _parse_device_classification_details(raw_value):
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    try:
        parsed = json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _device_availability_summary(device, latest_scan):
    if getattr(device, 'maintenance_mode', False):
        return {
            'label': 'Maintenance',
            'tone': 'warning',
            'detail': 'Alerts suppressed while maintenance mode is enabled.',
        }

    if latest_scan is None:
        return {
            'label': 'Unknown',
            'tone': 'muted',
            'detail': 'No recent reachability sample is available.',
        }

    normalized = _normalize_device_status(getattr(latest_scan, 'status', None))
    if normalized == 'Online':
        latency = getattr(latest_scan, 'ping_time_ms', None)
        latency_text = f"Latency {float(latency):.1f} ms" if latency is not None else 'Reachable'
        return {
            'label': 'Online',
            'tone': 'success',
            'detail': latency_text,
        }
    return {
        'label': 'Offline',
        'tone': 'danger',
        'detail': getattr(latest_scan, 'status_detail', None) or 'Latest reachability check marked the device offline.',
    }


def _telemetry_source_label(raw_source):
    source = str(raw_source or '').strip().lower()
    if source == 'agent':
        return 'Agent'
    if source == 'snmp':
        return 'SNMP'
    if source == 'icmp':
        return 'Reachability'
    return source.upper() if source else 'Unknown'


@devices_bp.route('/devices/<int:device_id>/details')
def device_details_page(device_id):
    from models.device import Device
    from models.audit_log import AuditLog
    from models.device_identity_link import DeviceIdentityLink
    from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
    from models.interfaces import DeviceInterface
    from models.scan_history import DeviceScanHistory, PortScanResult
    from models.server_health import ServerHealthLog
    from models.snmp_config import DeviceSnmpConfig
    from models.tracked_device import TrackedDevice
    from middleware.rbac import scoped_query
    from datetime import timedelta

    device = scoped_query(Device).get(device_id)
    if device is None:
        tracked_device = TrackedDevice.query.get(device_id)
        if tracked_device is not None:
            active_link = (
                DeviceIdentityLink.query
                .filter_by(tracked_device_id=tracked_device.id, is_active=True)
                .order_by(DeviceIdentityLink.updated_at.desc(), DeviceIdentityLink.id.desc())
                .first()
            )
            if active_link and active_link.device_id:
                return redirect(url_for('devices_bp.device_details_page', device_id=active_link.device_id))

            fallback_device = None
            normalized_mac = str(getattr(tracked_device, 'mac_address', '') or '').strip().lower()
            if normalized_mac:
                fallback_device = (
                    scoped_query(Device)
                    .filter(func.lower(Device.macaddress) == normalized_mac)
                    .order_by(Device.updated_at.desc(), Device.device_id.desc())
                    .first()
                )
            if fallback_device is None and getattr(tracked_device, 'ip_address', None):
                fallback_device = (
                    scoped_query(Device)
                    .filter(Device.device_ip == tracked_device.ip_address)
                    .order_by(Device.updated_at.desc(), Device.device_id.desc())
                    .first()
                )
            if fallback_device is not None:
                return redirect(url_for('devices_bp.device_details_page', device_id=fallback_device.device_id))
        abort(404)

    snmp_config = DeviceSnmpConfig.query.filter_by(device_id=device_id).first()

    latest_snmp_log = (
        ServerHealthLog.query.filter(
            ServerHealthLog.device_id == device_id,
            ServerHealthLog.source == 'snmp',
        )
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    latest_agent_log = (
        ServerHealthLog.query.filter(
            ServerHealthLog.device_id == device_id,
            ServerHealthLog.source == 'agent',
        )
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    latest_health_log = (
        ServerHealthLog.query.filter(ServerHealthLog.device_id == device_id)
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    latest_scan = (
        DeviceScanHistory.query.filter(
            DeviceScanHistory.device_ip == device.device_ip,
            _exclude_agent_push_scan(DeviceScanHistory),
        )
        .order_by(DeviceScanHistory.scan_timestamp.desc(), DeviceScanHistory.scan_id.desc())
        .first()
    )

    interfaces = (
        DeviceInterface.query.filter_by(device_id=device_id)
        .order_by(DeviceInterface.if_index.asc())
        .all()
    )

    active_identity_link = (
        DeviceIdentityLink.query.filter_by(device_id=device_id, is_active=True)
        .order_by(DeviceIdentityLink.updated_at.desc(), DeviceIdentityLink.id.desc())
        .first()
    )

    pending_identity_candidates = (
        DeviceIdentityLinkCandidate.query.filter_by(device_id=device_id, status='pending')
        .order_by(DeviceIdentityLinkCandidate.detected_at.desc(), DeviceIdentityLinkCandidate.id.desc())
        .limit(3)
        .all()
    )

    recent_audit_entries = (
        AuditLog.query.filter(
            AuditLog.entity_type == 'device',
            AuditLog.entity_id == device_id,
        )
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(6)
        .all()
    )

    # ── Ping history (last 7 days, up to 100 scans for KPI + sparkline) ────────
    _seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_scans = (
        DeviceScanHistory.query
        .filter(
            DeviceScanHistory.device_ip == device.device_ip,
            DeviceScanHistory.scan_timestamp >= _seven_days_ago,
            _exclude_agent_push_scan(DeviceScanHistory),
        )
        .order_by(DeviceScanHistory.scan_timestamp.desc())
        .limit(100)
        .all()
    )
    _latencies  = [s.ping_time_ms for s in recent_scans if s.ping_time_ms and s.ping_time_ms > 0]
    _online     = [s for s in recent_scans if (s.status or '').lower() == 'online']
    _offline    = [s for s in recent_scans if (s.status or '').lower() != 'online']
    _pkt_vals   = [s.packet_loss for s in recent_scans if s.packet_loss is not None]
    _avg_ms     = round(sum(_latencies) / len(_latencies), 1) if _latencies else None
    ping_stats = {
        'avg_ms':          _avg_ms,
        'min_ms':          round(min(_latencies), 1) if _latencies else None,
        'max_ms':          round(max(_latencies), 1) if _latencies else None,
        'offline_count':   len(_offline),
        'total_scans':     len(recent_scans),
        'online_count':    len(_online),
        # avg_packet_loss: average only scans that actually reported a value
        'avg_packet_loss': round(sum(_pkt_vals) / len(_pkt_vals), 1) if _pkt_vals else None,
        'sparkline': [
            {
                't': s.scan_timestamp.strftime('%H:%M'),
                'ts': _iso_utc(s.scan_timestamp),
                'display_t': _format_ist(s.scan_timestamp, include_tz=False),
                'v': round(s.ping_time_ms, 1) if s.ping_time_ms else 0,
                'status': (s.status or '').lower(),
            }
            for s in reversed(recent_scans[:50])
        ],
        # CSS colour class for avg ping KPI card
        'avg_class': (
            'dd-kpi-good' if _avg_ms is not None and _avg_ms < 5 else
            'dd-kpi-ok'   if _avg_ms is not None and _avg_ms < 20 else
            'dd-kpi-warn' if _avg_ms is not None and _avg_ms < 50 else
            'dd-kpi-bad'  if _avg_ms is not None else ''
        ),
    }

    # ── Open ports ───────────────────────────────────────────────────────────
    open_ports = (
        PortScanResult.query
        .filter_by(device_ip=device.device_ip, status='open')
        .order_by(PortScanResult.port_number.asc())
        .all()
    )

    snmp_enabled = bool(snmp_config and snmp_config.is_enabled)
    has_snmp_metrics = bool(latest_snmp_log or interfaces)
    classification_details = _parse_device_classification_details(device.classification_details)
    availability_summary = _device_availability_summary(device, latest_scan)

    try:
        from models.compliance_profile import ComplianceProfile
        compliance_profiles = ComplianceProfile.query.order_by(ComplianceProfile.name).all()
    except Exception:
        compliance_profiles = []

    monitoring_sources = [
        {
            'name': 'Reachability',
            'status': availability_summary['label'],
            'timestamp': latest_scan.scan_timestamp if latest_scan else None,
            'detail': availability_summary['detail'],
        },
        {
            'name': 'Agent',
            'status': 'Reporting' if latest_agent_log else 'Not reporting',
            'timestamp': latest_agent_log.timestamp if latest_agent_log else None,
            'detail': 'Primary server/workstation telemetry' if latest_agent_log else 'No recent agent sample',
        },
        {
            'name': 'SNMP',
            'status': 'Available' if latest_snmp_log else ('Configured' if snmp_enabled else 'Optional'),
            'timestamp': latest_snmp_log.timestamp if latest_snmp_log else (snmp_config.last_successful_poll if snmp_config else None),
            'detail': 'Supplementary polling data' if snmp_config else 'Not required for this page',
        },
    ]

    import json
    workstation_tracking_data = None
    tracked_device = None
    if active_identity_link and getattr(active_identity_link, 'tracked_device_id', None):
        tracked_device = TrackedDevice.query.get(active_identity_link.tracked_device_id)
    if not tracked_device and getattr(device, 'macaddress', None):
        tracked_device = TrackedDevice.query.filter_by(mac_address=device.macaddress).first()
    
    if tracked_device and getattr(tracked_device, 'tracking_data', None):
        try:
            workstation_tracking_data = json.loads(tracked_device.tracking_data)
            monitoring_sources[1]['status'] = 'Reporting'
            monitoring_sources[1]['detail'] = 'Workstation agent telemetry'
            if workstation_tracking_data.get('timestamp'):
                from dateutil import parser
                try:
                    monitoring_sources[1]['timestamp'] = parser.parse(workstation_tracking_data['timestamp'])
                except Exception:
                    pass
        except Exception:
            pass
        if workstation_tracking_data and not latest_agent_log:
            class MockAgentLog:
                def __getattr__(self, name):
                    return None
            log = MockAgentLog()
            stats = workstation_tracking_data.get('current_stats', workstation_tracking_data)
            sys_metrics = stats.get('system_metrics', {})
            dev_info = stats.get('device_info', {})
            try:
                from dateutil import parser
                log.timestamp = parser.parse(workstation_tracking_data.get('timestamp', ''))
            except Exception:
                log.timestamp = None
            log.os_name = dev_info.get('system')
            log.os_version = dev_info.get('os_version')
            log.os_arch = dev_info.get('os_arch')
            log.uptime = "N/A"
            log.process_count = sys_metrics.get('process_count')
            log.cpu_usage = sys_metrics.get('cpu_percent')
            log.load_avg_1min = None
            log.cpu_iowait_percent = None
            log.memory_usage = sys_metrics.get('memory_percent')
            log.memory_total_gb = sys_metrics.get('total_gb')
            log.memory_used_gb = sys_metrics.get('used_gb')
            log.disk_usage = sys_metrics.get('disk_usage')
            log.disk_total_gb = sys_metrics.get('disk_total_gb')
            log.disk_used_gb = sys_metrics.get('disk_used_gb')
            log.swap_percent = sys_metrics.get('swap_percent')
            log.swap_total_mb = sys_metrics.get('swap_total_mb')
            log.swap_used_mb = sys_metrics.get('swap_used_mb')
            log.top_processes = sys_metrics.get('top_processes') or []
            log.top_processes_cpu = sys_metrics.get('top_processes') or []
            latest_agent_log = log

    hardware_specs = dict(device.hardware_specs or {}) if isinstance(device.hardware_specs, dict) else {}
    tracking_stats = {}
    tracking_device_info = {}
    tracking_hardware_specs = {}
    if isinstance(workstation_tracking_data, dict):
        tracking_stats = workstation_tracking_data.get('current_stats', workstation_tracking_data)
        if not isinstance(tracking_stats, dict):
            tracking_stats = {}
        tracking_device_info = tracking_stats.get('device_info') if isinstance(tracking_stats.get('device_info'), dict) else {}
        tracking_hardware_specs = tracking_stats.get('hardware_specs') if isinstance(tracking_stats.get('hardware_specs'), dict) else {}
        if tracking_hardware_specs:
            hardware_specs.update({k: v for k, v in tracking_hardware_specs.items() if v not in (None, '')})
        derived_hardware = {
            'cpu_model': tracking_device_info.get('processor'),
            'architecture': tracking_device_info.get('os_arch'),
            'memory_total_gb': (tracking_stats.get('system_metrics') or {}).get('total_gb') if isinstance(tracking_stats.get('system_metrics'), dict) else None,
            'disk_total_gb': (tracking_stats.get('system_metrics') or {}).get('disk_total_gb') if isinstance(tracking_stats.get('system_metrics'), dict) else None,
        }
        hardware_specs.update({k: v for k, v in derived_hardware.items() if v not in (None, '') and not hardware_specs.get(k)})

    if latest_agent_log and getattr(latest_agent_log, 'os_arch', None) and not hardware_specs.get('architecture'):
        hardware_specs['architecture'] = latest_agent_log.os_arch
    if latest_agent_log and getattr(latest_agent_log, 'memory_total_gb', None) is not None and not hardware_specs.get('memory_total_gb'):
        hardware_specs['memory_total_gb'] = latest_agent_log.memory_total_gb
    if latest_agent_log and getattr(latest_agent_log, 'disk_total_gb', None) is not None and not hardware_specs.get('disk_total_gb'):
        hardware_specs['disk_total_gb'] = latest_agent_log.disk_total_gb

    inventory_type = str(getattr(device, 'device_type', '') or '').strip().lower()
    is_workstation_profile = bool(tracked_device) or inventory_type in _WORKSTATION_TYPES
    is_camera_profile = inventory_type in {'camera', 'camera/iot', 'camera_iot', 'iot'}
    open_port_numbers = {
        int(getattr(port, 'port_number', 0) or 0)
        for port in open_ports
        if getattr(port, 'port_number', None) is not None
    }
    camera_scheme = 'https' if 443 in open_port_numbers or 8443 in open_port_numbers else 'http'
    camera_port = (
        443 if 443 in open_port_numbers else
        8443 if 8443 in open_port_numbers else
        80 if 80 in open_port_numbers else
        8080 if 8080 in open_port_numbers else
        None
    )
    if camera_port is None:
        try:
            raw_port = int(str(getattr(device, 'port', '') or '').strip())
        except (TypeError, ValueError):
            raw_port = None
        if raw_port and raw_port != 554:
            camera_port = raw_port
            if raw_port in {443, 8443}:
                camera_scheme = 'https'
    camera_host = str(getattr(device, 'device_ip', '') or '').strip()
    camera_web_url = None
    if camera_host:
        default_port = 443 if camera_scheme == 'https' else 80
        camera_web_url = f"{camera_scheme}://{camera_host}"
        if camera_port and camera_port != default_port:
            camera_web_url = f"{camera_web_url}:{camera_port}"

    camera_rtsp_url = str(getattr(device, 'rstplink', '') or '').strip() or None
    if not camera_rtsp_url and camera_host:
        camera_rtsp_url = f"rtsp://{camera_host}:554/"

    camera_access = {
        'is_camera': is_camera_profile,
        'web_url': camera_web_url,
        'rtsp_url': camera_rtsp_url,
        'username': getattr(device, 'device_username', None),
    }
    monitoring_interval_seconds = get_monitoring_interval()

    return render_template(
        'device_details.html',
        device=device,
        latest_health_log=latest_health_log,
        latest_scan=latest_scan,
        snmp_config=snmp_config,
        latest_snmp_log=latest_snmp_log,
        latest_agent_log=latest_agent_log,
        workstation_tracking_data=workstation_tracking_data,
        interfaces=interfaces,
        snmp_enabled=snmp_enabled,
        has_snmp_metrics=has_snmp_metrics,
        classification_details=classification_details,
        availability_summary=availability_summary,
        monitoring_sources=monitoring_sources,
        active_identity_link=active_identity_link,
        pending_identity_candidates=pending_identity_candidates,
        recent_audit_entries=recent_audit_entries,
        telemetry_source_label=_telemetry_source_label(getattr(latest_health_log, 'source', None)),
        enable_server_fullpage_telemetry=bool(
            current_app.config.get('ENABLE_SERVER_FULLPAGE_TELEMETRY', Config.ENABLE_SERVER_FULLPAGE_TELEMETRY)
        ),
        can_edit_server_thresholds=str(session.get('role') or '').strip().lower() == 'admin',
        ping_stats=ping_stats,
        monitoring_interval_seconds=monitoring_interval_seconds,
        monitoring_interval_label=format_monitoring_interval_label(monitoring_interval_seconds),
        open_ports=open_ports,
        compliance_profiles=compliance_profiles,
        is_workstation_profile=is_workstation_profile,
        tracked_device=tracked_device,
        hardware_specs=hardware_specs,
        camera_access=camera_access,
        format_ist=_format_ist,
    )

@devices_bp.route('/api/devices/<int:device_id>/connections', methods=['GET'])
def get_device_connections(device_id):
    from models.device import Device
    from models.server_health import ServerHealthLog

    def _base_meta(
        *,
        monitoring_mode='unknown',
        live_supported=False,
        live_attempted=False,
        cached=False,
        cache_age_seconds=None,
        rate_limited=False,
        retry_after_seconds=None,
        snapshot_available=False,
        snapshot_age_seconds=None,
        top_limit=20,
        total_connections=0,
        total_unique_remote_ips=0,
    ):
        return {
            'device_id': device_id,
            'live_method': 'agent_snapshot',
            'monitoring_mode': monitoring_mode,
            'wmi_live_fetch_planned': False,
            'live_supported': bool(live_supported),
            'live_attempted': bool(live_attempted),
            'cached': bool(cached),
            'cache_age_seconds': cache_age_seconds,
            'rate_limited': bool(rate_limited),
            'retry_after_seconds': retry_after_seconds,
            'snapshot_available': bool(snapshot_available),
            'snapshot_age_seconds': snapshot_age_seconds,
            'top_limit': int(top_limit or 20),
            'total_connections': int(total_connections or 0),
            'total_unique_remote_ips': int(total_unique_remote_ips or 0),
            'fetched_at': datetime.now(timezone.utc).isoformat(),
        }

    def _build_known_device_map(ip_values):
        safe_ips = [ip for ip in ip_values if isinstance(ip, str) and ip]
        if not safe_ips:
            return {}
        known_devices = Device.query.filter(Device.device_ip.in_(safe_ips)).all()
        return {
            d.device_ip: {
                'name': d.device_name,
                'hostname': (d.hostname or '').strip() or None,
                'type': d.device_type,
                'id': d.device_id,
            }
            for d in known_devices
        }

    def _apply_resolution(ip_key, row, known_map):
        value = row.get(ip_key)
        match = known_map.get(value)
        if match:
            row['remote_device_name'] = match['name']
            row['remote_hostname'] = match.get('hostname') or match['name'] or value
            row['remote_device_type'] = match['type']
            row['remote_device_id'] = match['id']
        else:
            row['remote_device_name'] = 'Unknown Device'
            row['remote_hostname'] = value or 'Unknown'
            row['remote_device_type'] = 'unknown'
            row['remote_device_id'] = None
        return row

    def _to_int(value, default=0):
        try:
            parsed = int(value)
            return parsed if parsed >= 0 else default
        except (TypeError, ValueError):
            return default

    def _sanitize_agent_snapshot(raw_snapshot):
        if not isinstance(raw_snapshot, list):
            return []

        cleaned = []
        for item in raw_snapshot:
            if not isinstance(item, dict):
                continue

            remote_ip = str(item.get('ip') or '').strip()
            if not remote_ip:
                continue

            try:
                count = int(item.get('count'))
            except (TypeError, ValueError):
                count = 0

            cleaned.append({
                'ip': remote_ip,
                'count': max(count, 0),
            })

        cleaned.sort(key=lambda row: row['count'], reverse=True)
        return cleaned[:20]

    def _snapshot_age_seconds(ts):
        if not ts:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return max(0, int(delta.total_seconds()))

    try:
        device = Device.query.get(device_id)
        if not device:
            return _json_error_response(
                code='DEVICE_NOT_FOUND',
                message='Device not found',
                status=404,
                meta=_base_meta(monitoring_mode='unknown'),
            )

        monitoring_mode = (device.monitoring_mode or 'unknown').strip().lower()
        if not device.device_type or device.device_type.lower() != 'server':
            return _json_error_response(
                code='NOT_SERVER_DEVICE',
                message='Connection snapshot is only supported for server devices.',
                status=400,
                meta=_base_meta(monitoring_mode=monitoring_mode),
            )

        latest_agent_log = (
            ServerHealthLog.query.filter(
                ServerHealthLog.device_id == device.device_id,
                ServerHealthLog.source == 'agent',
            )
            .order_by(ServerHealthLog.timestamp.desc())
            .first()
        )

        raw_snapshot_rows = _sanitize_agent_snapshot(
            latest_agent_log.network_top_remote_ips if latest_agent_log else []
        )
        known_map = _build_known_device_map([row['ip'] for row in raw_snapshot_rows])
        snapshot_rows = [
            _apply_resolution('ip', dict(row), known_map)
            for row in raw_snapshot_rows
        ]

        connections = []
        for row in snapshot_rows:
            connections.append({
                'remote_ip': row.get('ip'),
                'remote_hostname': row.get('remote_hostname') or row.get('ip') or 'Unknown',
                'connection_count': _to_int(row.get('count')),
                'state': 'ESTABLISHED',
                'remote_device_name': row.get('remote_device_name', 'Unknown Device'),
                'remote_device_type': row.get('remote_device_type', 'unknown'),
                'remote_device_id': row.get('remote_device_id'),
            })

        top_limit = 20
        unique_remote_ips = _to_int(
            latest_agent_log.network_connections_unique_ips if latest_agent_log else None,
            default=len(snapshot_rows),
        )
        established_connections = _to_int(
            latest_agent_log.network_connections_established if latest_agent_log else None,
            default=sum(row.get('count', 0) for row in raw_snapshot_rows),
        )
        snapshot_ts = latest_agent_log.timestamp if latest_agent_log else None

        actor = str(
            session.get('username')
            or session.get('user_id')
            or request.remote_addr
            or 'unknown'
        )
        logger.info(
            "[ConnSnapshot] actor=%s device_id=%s device_ip=%s rows=%s total=%s unique_ips=%s",
            actor,
            device.device_id,
            device.device_ip,
            len(connections),
            established_connections,
            unique_remote_ips,
        )

        agent_snapshot = {
            'top_remote_ips': snapshot_rows,
            'unique_remote_ips_count': unique_remote_ips,
            'timestamp': _iso_utc(snapshot_ts),
        }
        return jsonify({
            'connections': connections,
            'agent_snapshot': agent_snapshot,
            'meta': _base_meta(
                monitoring_mode=monitoring_mode,
                snapshot_available=bool(snapshot_ts),
                snapshot_age_seconds=_snapshot_age_seconds(snapshot_ts),
                top_limit=top_limit,
                total_connections=established_connections,
                total_unique_remote_ips=unique_remote_ips,
            ),
        })
    except Exception as exc:
        logger.exception(
            "[ConnSnapshot] failed device_id=%s",
            device_id,
        )
        return _json_error_response(
            code='CONNECTION_SNAPSHOT_FAILED',
            message=f'Failed to load connection snapshot: {exc}',
            status=500,
            meta=_base_meta(monitoring_mode='unknown'),
        )

@devices_bp.route('/api/devices/<int:device_id>/toggle_monitoring', methods=['POST', 'PATCH'])
@require_permission('devices.edit')
def toggle_device_monitoring(device_id):
    from models.device import Device
    from middleware.rbac import scoped_query
    device = scoped_query(Device).get(device_id)
    if device:
        device.is_monitored = not device.is_monitored
        db.session.commit()
        return jsonify({'success': True, 'is_monitored': device.is_monitored})
    else:
        return jsonify({'error': 'Device not found'}), 404


@devices_bp.route('/api/devices/<int:device_id>/snmp', methods=['PATCH'])
@require_permission('devices.edit')
def update_device_snmp(device_id):
    from models.device import Device
    from models.scan_history import DeviceScanHistory
    from middleware.rbac import scoped_query

    device = scoped_query(Device).get(device_id)
    if not device:
        return jsonify({'success': False, 'error': 'Device not found'}), 404

    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))

    current_mode = (device.monitoring_mode or 'ping').strip().lower() or 'ping'
    fallback_mode = 'ping' if current_mode == 'snmp' else current_mode

    snmp_version = data.get('snmp_version') or (device.snmp_config.snmp_version if device.snmp_config else device.snmp_version or 'v2c')
    snmp_community = data.get('snmp_community') if data.get('snmp_community') is not None else (device.snmp_config.community_string if device.snmp_config else device.snmp_community or 'public')
    snmp_username = data.get('snmp_username') if data.get('snmp_username') is not None else (device.snmp_config.security_name if device.snmp_config else device.snmp_username or '')
    snmp_auth_proto = data.get('snmp_auth_proto') if data.get('snmp_auth_proto') is not None else (device.snmp_config.auth_protocol if device.snmp_config else device.snmp_auth_proto or '')
    snmp_auth_password = data.get('snmp_auth_password') if data.get('snmp_auth_password') is not None else (device.snmp_config.auth_password if device.snmp_config else device.snmp_auth_password or '')
    snmp_priv_proto = data.get('snmp_priv_proto') if data.get('snmp_priv_proto') is not None else (device.snmp_config.priv_protocol if device.snmp_config else device.snmp_priv_proto or '')
    snmp_priv_password = data.get('snmp_priv_password') if data.get('snmp_priv_password') is not None else (device.snmp_config.priv_password if device.snmp_config else device.snmp_priv_password or '')

    raw_port = data.get('snmp_port')
    try:
        snmp_port = int(raw_port if raw_port is not None and str(raw_port).strip() != '' else (device.snmp_config.snmp_port if device.snmp_config else device.snmp_port or 161))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'SNMP port must be a valid integer.'}), 400

    if snmp_port <= 0 or snmp_port > 65535:
        return jsonify({'success': False, 'error': 'SNMP port must be between 1 and 65535.'}), 400

    device.monitoring_mode = 'snmp' if enabled else fallback_mode
    if enabled:
        device.is_monitored = True

    device.snmp_version = snmp_version
    device.snmp_port = snmp_port
    device.snmp_community = snmp_community
    device.snmp_username = snmp_username or None
    device.snmp_auth_proto = snmp_auth_proto or None
    device.snmp_auth_password = snmp_auth_password or None
    device.snmp_priv_proto = snmp_priv_proto or None
    device.snmp_priv_password = snmp_priv_password or None

    try:
        db.session.flush()
        _upsert_device_snmp_config(
            device=device,
            monitoring_mode=device.monitoring_mode,
            is_monitored=device.is_monitored,
            snmp_version=snmp_version,
            snmp_port=snmp_port,
            snmp_community=snmp_community,
            snmp_username=snmp_username,
            snmp_auth_proto=snmp_auth_proto,
            snmp_auth_password=snmp_auth_password,
            snmp_priv_proto=snmp_priv_proto,
            snmp_priv_password=snmp_priv_password,
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error("[Devices] Failed to update SNMP settings for device %s: %s", device_id, exc)
        return jsonify({'success': False, 'error': f'Failed to update SNMP settings: {exc}'}), 500

    latest_status_by_ip = _load_latest_scan_statuses(
        [device.device_ip],
        DeviceScanHistory=DeviceScanHistory,
    )
    return jsonify({
        'success': True,
        'device_id': device.device_id,
        'device': _device_inventory_payload(device, latest_status_by_ip=latest_status_by_ip),
    })

@devices_bp.route('/api/devices/bulk_add', methods=['POST'])
@require_permission('devices.edit')
def bulk_add_devices():
    try:
        from models.device import Device
        
        devices_data = request.get_json()
        if not devices_data or not isinstance(devices_data, list):
             return jsonify({'error': 'Invalid data format. Expected a list of devices.'}), 400

        added_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []

        seen_ips = set()
        
        for data in devices_data:
            ip_address = data.get('ip', '').strip()
            hostname = data.get('hostname', 'Unknown').strip()
            mac_address = data.get('mac', 'N/A').strip()
            manufacturer = data.get('manufacturer', 'Unknown').strip()
            from services.device_classifier import DeviceClassifier
            device_type_raw = (data.get('device_type') or data.get('type') or '').strip()
            device_type = DeviceClassifier.normalize_device_type(device_type_raw)
            confidence_score = data.get('confidence_score')
            classification_confidence = (data.get('classification_confidence') or '').strip()
            classification_details = data.get('classification_details')
            
            if not ip_address:
                continue
                
            if ip_address in seen_ips:
                continue
            seen_ips.add(ip_address)

            try:
                with db.session.begin_nested():
                    device, action, _prev_ip = upsert_device_from_identity(
                        ip=ip_address,
                        mac=mac_address,
                        hostname=hostname,
                        manufacturer=manufacturer,
                        device_type=device_type or 'unknown',
                        is_monitored=False,
                        is_active=True
                    )

                    # Apply classification metadata when available (avoid overwriting manual)
                    if device and (classification_confidence or confidence_score is not None or classification_details):
                        if (device.classification_confidence or '').strip().lower() != 'manual':
                            if classification_confidence:
                                device.classification_confidence = classification_confidence
                            if confidence_score is not None:
                                device.confidence_score = confidence_score
                            if classification_details is not None:
                                if not isinstance(classification_details, str):
                                    classification_details = json.dumps(classification_details)
                                device.classification_details = classification_details

                    db.session.flush()

                    _upsert_device_snmp_config(
                        device=device,
                        monitoring_mode='snmp' if data.get('snmp_working') else (device.monitoring_mode or 'ping'),
                        is_monitored=bool(device.is_monitored),
                        snmp_version=data.get('snmp_version') or device.snmp_version or '2c',
                        snmp_port=data.get('snmp_port') or device.snmp_port or 161,
                        snmp_community=data.get('snmp_community') or device.snmp_community or 'public',
                        snmp_username='',
                        snmp_auth_proto='',
                        snmp_auth_password='',
                        snmp_priv_proto='',
                        snmp_priv_password='',
                    )

                if action == "created":
                    added_count += 1
                elif action == "updated":
                    updated_count += 1
                else:
                    skipped_count += 1
            except Exception as item_error:
                logger.warning("Bulk add failed for %s: %s", ip_address, item_error)
                errors.append(f"Error adding {ip_address}: {str(item_error)}")

        db.session.commit()
        
        # Audit logging
        from middleware.rbac import create_audit_log
        if added_count > 0 or updated_count > 0:
            create_audit_log(
                action='bulk_add',
                entity_type='device',
                description=f"Bulk device operation: {added_count} added, {updated_count} updated, {skipped_count} skipped"
            )
        
        return jsonify({
            'success': True,
            'added': added_count,
            'updated': updated_count,
            'skipped': skipped_count,
            'errors': errors
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/bulk_delete', methods=['POST'])
@require_permission('devices.edit')
def bulk_delete_devices():
    try:
        from models.device import Device
        from middleware.rbac import scoped_query
        
        data = request.get_json()
        if not data or 'device_ids' not in data:
             return jsonify({'error': 'Invalid data. Expected device_ids list.'}), 400
        
        device_ids = data['device_ids']
        if not isinstance(device_ids, list):
             return jsonify({'error': 'device_ids must be a list'}), 400
        try:
            batch_size = int(data.get('batch_size', 25) or 25)
        except (TypeError, ValueError):
            return jsonify({'error': 'batch_size must be an integer'}), 400
        batch_size = max(1, min(batch_size, 100))
        logger.info("Bulk delete requested: count=%s", len(device_ids))

        # Stop active scans so deleted devices are not immediately re-added by scan completion.
        stopped_scans = 0
        service = get_discovery_service()
        with service.active_scans_lock:
            active_scan_ids = [
                scan_id for scan_id, scan in service.active_scans.items()
                if scan.get('status') == service.STATUS_SCANNING
            ]
        for scan_id in active_scan_ids:
            stop_result = service.stop_scan(scan_id)
            if stop_result.get('ok') and stop_result.get('state') == service.STATUS_STOPPED:
                stopped_scans += 1
        if stopped_scans:
            logger.info("Bulk delete pre-stop scans: stopped=%s", stopped_scans)

        eligible_ids = [
            row[0]
            for row in scoped_query(Device)
            .with_entities(Device.device_id)
            .filter(Device.device_id.in_(device_ids))
            .all()
        ]

        if not eligible_ids:
            return jsonify({'error': 'No eligible devices found for deletion'}), 404

        job = _create_bulk_delete_job(
            requested_ids=device_ids,
            eligible_ids=eligible_ids,
            batch_size=batch_size,
            stopped_scans=stopped_scans,
            initiated_by=session.get('user_id'),
        )
        app_obj = current_app._get_current_object()
        # Capture a plain immutable dict — no Flask proxies, no lazy objects.
        # The background thread has no request context so anything that touches
        # flask.session or flask.request will raise RuntimeError there.
        audit_context = {
            'user_id':    session.get('user_id'),
            'username':   str(session.get('username') or 'unknown'),
            'role':       str(session.get('role') or 'unknown'),
            'ip_address': str(request.remote_addr or ''),
            'user_agent': str(request.headers.get('User-Agent', '') or '')[:200],
        }
        worker = threading.Thread(
            target=_run_bulk_delete_job,
            kwargs={
                'app': app_obj,
                'job_id': job['job_id'],
                'device_ids': eligible_ids,
                'batch_size': batch_size,
                'audit_context': audit_context,
            },
            daemon=True,
        )
        worker.start()

        logger.info(
            "Bulk delete queued: requested=%s eligible=%s stopped_scans=%s batch_size=%s job_id=%s",
            len(device_ids),
            len(eligible_ids),
            stopped_scans,
            batch_size,
            job['job_id'],
        )

        return jsonify({
            'success': True,
            'queued': True,
            'job_id': job['job_id'],
            'requested': len(device_ids),
            'eligible': len(eligible_ids),
            'stopped_scans': stopped_scans,
            'batch_size': batch_size,
        }), 202
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@devices_bp.route('/api/devices/bulk_delete/<job_id>', methods=['GET'])
@require_permission('devices.edit')
def bulk_delete_status(job_id):
    with _bulk_delete_jobs_lock:
        job = _bulk_delete_jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Bulk delete job not found'}), 404
        return jsonify(dict(job))

@devices_bp.route('/api/devices/<int:device_id>/update_type', methods=['POST', 'PATCH'])
@require_permission('devices.edit')
def update_device_type(device_id):
    try:
        data = request.get_json()
        new_type = data.get('device_type')
        
        if not new_type:
            return jsonify({'error': 'Missing device_type'}), 400
            
        from models.device import Device
        from middleware.rbac import scoped_query
        device = scoped_query(Device).get(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
            
        # Update device type
        device.device_type = new_type
        device.classification_confidence = 'Manual'
        device.confidence_score = 100
        
        db.session.commit()
        
        return jsonify({'success': True, 'device_type': new_type})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@devices_bp.route('/api/devices/reclassify_all', methods=['GET'])
def reclassify_all():
    # Auth handled by middleware


    from models.device import Device
    from services.device_classifier import DeviceClassifier, DeviceSignals, ConfidenceLevel
    from services.device_enrichment_service import DeviceEnrichmentService
    from flask import current_app
    import os

    classifier = DeviceClassifier()
    _enrich_svc = DeviceEnrichmentService()
    device_ids = [row[0] for row in db.session.query(Device.device_id).all()]
    updated_count = 0
    updated_devices = []
    force = request.args.get('force', 'false').lower() == 'true'
    auto_mode = request.args.get('auto', 'false').lower() == 'true'
    unknown_only = request.args.get('unknown_only', 'false').lower() == 'true'

    # Change 1: filter to unknowns only when requested
    if unknown_only:
        device_ids = [
            row[0]
            for row in db.session.query(Device.device_id)
            .filter(func.lower(func.coalesce(Device.device_type, "")) == "unknown")
            .all()
        ]

    # Resolution counters
    classifier_resolved = 0
    gemini_resolved = 0
    still_unknown = 0

    # Get shared scanner instance
    scanner = get_discovery_service().scanner

    logger.info(
        "[Reclassify] start devices=%d force=%s auto=%s unknown_only=%s",
        len(device_ids), force, auto_mode, unknown_only,
    )

    for device_id in device_ids:
        try:
            # ── Fetch phase: grab only what we need, then release the connection
            # immediately. All network I/O (ping, ARP, port scan, Gemini) happens
            # below with no open transaction, so idle_in_transaction_session_timeout
            # can never fire mid-loop.
            device = (
                Device.query.options(selectinload(Device.snmp_config))
                .filter(Device.device_id == device_id)
                .first()
            )
            if device is None:
                continue

            dtype = (device.device_type or "").strip().lower()
            conf = (device.classification_confidence or "").strip().lower()

            # Auto mode: only classify unknown / low-confidence, skip manual
            if not force:
                if conf == "manual":
                    continue
                if dtype not in ("", "unknown", "network device") and conf in ("medium", "high"):
                    continue
                if auto_mode and dtype not in ("", "unknown", "network device"):
                    continue

            # Snapshot the fields we need for network I/O before releasing the session.
            _device_ip   = device.device_ip
            _mac         = device.macaddress if device.macaddress and device.macaddress.strip().lower() not in ('n/a', 'na', 'unknown', '') else None
            _hostname    = device.hostname or ""
            _manufacturer = device.manufacturer or "Unknown"
            _device_name = device.device_name or ""

            # Release the DB connection — no transaction held during network work.
            db.session.remove()

            # ── Network I/O phase (no DB connection open) ─────────────────────
            if not _hostname or _hostname.strip().lower() in ("unknown", "n/a", "na") or _hostname.startswith("Device-"):
                if _device_name and _device_name.strip().lower() not in ("unknown", "n/a", "na") and not _device_name.startswith("Device-"):
                    _hostname = _device_name
                else:
                    _hostname = "Unknown"

            status, _latency, _packet_loss, ttl, *_ = asyncio.run(scanner.ping_device(_device_ip))

            if status == "Online":
                _scanned_mac = scanner.get_mac_address(_device_ip)
                if _scanned_mac and _scanned_mac.strip().lower() not in ('n/a', 'na', 'unknown', ''):
                    _mac = _scanned_mac
                _hostname = scanner.get_hostname(_device_ip) or _hostname

            if _manufacturer in ("Unknown", "N/A", "") and _mac not in ("", "N/A", None):
                try:
                    _manufacturer = asyncio.run(scanner.get_manufacturer(_mac))
                except Exception:
                    pass

            open_ports = asyncio.run(scanner.scan_ports(_device_ip))
            port_numbers = [p.get("port") for p in open_ports if isinstance(p, dict)]

            is_l2_reachable = bool(_mac and _mac not in ("N/A", "Unknown", ""))
            enriched = asyncio.run(_enrich_svc.enrich(_device_ip, port_numbers, is_l2_reachable))

            signals = DeviceSignals(
                ip_address=_device_ip,
                mac_address=_mac,
                hostname=_hostname,
                manufacturer=_manufacturer,
                open_ports=port_numbers,
                ttl=ttl,
                http_banner=enriched.get("http_banner"),
                ssh_banner=enriched.get("ssh_banner"),
                mdns_services=enriched.get("mdns_services", []),
                upnp_info=enriched.get("upnp_info"),
            )

            result = classifier.classify(signals)
            normalized_type = DeviceClassifier.normalize_device_type(result.device_type)

            if result.confidence != ConfidenceLevel.LOW:
                classifier_resolved += 1

            gemini_resolved_this = False
            if result.confidence == ConfidenceLevel.LOW:
                try:
                    from services.gemini_classifier import classify_device as gemini_classify
                    gemini_signals = {
                        "manufacturer": _manufacturer or "",
                        "mac_address": _mac or "",
                        "ttl": ttl,
                        "open_ports": port_numbers,
                        "http_banner": enriched.get("http_banner"),
                        "ssh_banner": enriched.get("ssh_banner"),
                        "mdns_services": enriched.get("mdns_services", []),
                        "upnp_info": enriched.get("upnp_info"),
                        "hostname": _hostname or "",
                    }
                    gemini_type = gemini_classify(gemini_signals)
                    if gemini_type and gemini_type != "unknown":
                        normalized_type = gemini_type
                        gemini_resolved_this = True
                except Exception:
                    pass

            if gemini_resolved_this:
                gemini_resolved += 1

            if (normalized_type or "").strip().lower() == "unknown":
                still_unknown += 1

            # ── Write phase: re-acquire a fresh connection and commit quickly ──
            device = (
                Device.query.options(selectinload(Device.snmp_config))
                .filter(Device.device_id == device_id)
                .first()
            )
            if device is None:
                continue  # deleted between fetch and write — skip cleanly

            device.device_type = normalized_type
            device.confidence_score = result.score
            device.classification_confidence = result.confidence.value
            device.classification_details = json.dumps(result.to_dict())
            device.manufacturer = _manufacturer

            if _mac and _mac != device.macaddress:
                with db.session.no_autoflush:
                    mac_conflict = Device.query.filter(
                        Device.macaddress == _mac,
                        Device.device_id != device.device_id,
                    ).first()
                if mac_conflict is not None:
                    logger.warning(
                        "[Reclassify] MAC %s already owned by device_id=%s; skipping MAC update for device_id=%s (%s)",
                        _mac, mac_conflict.device_id, device.device_id, _device_ip,
                    )
                else:
                    device.macaddress = _mac
            elif not _mac:
                device.macaddress = _mac
            if _hostname and _hostname != "Unknown" and not _hostname.startswith("Device-"):
                device.hostname = _hostname

            db.session.commit()

            updated_count += 1
            updated_devices.append({
                "device_id": device.device_id,
                "device_type": device.device_type,
                "classification_confidence": device.classification_confidence,
                "confidence_score": device.confidence_score,
            })

        except Exception as e:
            _ip = "<unknown>"
            try:
                _ip = _device_ip  # use the snapshot, not the expired ORM object
            except Exception:
                pass
            db.session.rollback()
            logger.error("[Reclassify] Failed for %s: %s", _ip, e)

    # Final commit is now a no-op (each device commits individually), but kept
    # as a safety net in case any stray session state needs flushing.
    try:
        db.session.commit()
    except Exception as _final_ce:
        db.session.rollback()
        logger.error("[Reclassify] Final commit failed: %s", _final_ce)

    return jsonify({
        'success': True,
        'message': f"Reclassified {updated_count} devices.",
        'updated_count': updated_count,
        'updated_devices': updated_devices,
        'db_uri': current_app.config.get('SQLALCHEMY_DATABASE_URI', 'unknown'),
        'classifier_resolved': classifier_resolved,
        'gemini_resolved': gemini_resolved,
        'still_unknown': still_unknown,
    })
@devices_bp.route('/api/devices/<int:device_id>', methods=['POST'])
@require_permission('devices.edit')
def update_device(device_id):
    from models.device import Device
    from middleware.rbac import scoped_query
    device = scoped_query(Device).get_or_404(device_id)
    data = request.json or {}
    
    if 'switch_brand' in data:
        device.switch_brand = data['switch_brand']
    if 'device_type' in data:
        device.device_type = data['device_type']
    if 'cos_tier' in data:
        device.cos_tier = data['cos_tier']
    if 'is_monitored' in data:
        device.is_monitored = bool(data['is_monitored'])
    if 'parent_switch_id' in data:
        device.parent_switch_id = data['parent_switch_id']
    if 'parent_port_id' in data:
        device.parent_port_id = data['parent_port_id']
    # When an operator explicitly sets a device_name, lock it so discovery
    # and upsert_device_from_identity never overwrite the human-assigned label.
    if 'device_name' in data and data['device_name']:
        device.device_name = data['device_name'].strip()
        device.name_locked = True
    if 'hostname' in data and data['hostname']:
        device.hostname = data['hostname'].strip()
        device.name_locked = True
    # Explicit unlock — operator wants discovery to manage the name again.
    if 'name_locked' in data:
        device.name_locked = bool(data['name_locked'])

    db.session.commit()
    return jsonify({"success": True, "device": device.to_dict()})


# ============================================================================
# PHASE 4: Agent Token Management Endpoints
# ============================================================================

@devices_bp.route('/devices/<int:device_id>/regenerate_token', methods=['POST'])
@require_permission('devices.edit')
def regenerate_agent_token(device_id):
    """
    Regenerate agent token for a device.
    
    Uses scoped_query to ensure users can only manage tokens for devices
    in their scope (site for managers, department for operators).
    """
    from models.device import Device
    from middleware.rbac import generate_agent_token, scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    device.agent_token = generate_agent_token()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'device_id': device.device_id,
        'agent_token': device.agent_token
    })


@devices_bp.route('/devices/<int:device_id>/get_token', methods=['GET'])
@require_permission('devices.edit')
def get_agent_token(device_id):
    """
    Get agent token for a device (for display/copy).
    
    Uses scoped_query to ensure users can only view tokens for devices
    in their scope (site for managers, department for operators).
    """
    from models.device import Device
    from middleware.rbac import scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    return jsonify({
        'device_id': device.device_id,
        'agent_token': device.agent_token or 'Not generated'
    })


# ============================================================================
# PHASE 5: Site Alignment Diagnostic Tools (Admin)
# ============================================================================

@devices_bp.route('/api/devices/site-alignment-check', methods=['GET'])
@require_permission('devices.view')
def check_site_alignment():
    """
    Identify devices whose site assignment doesn't match their subnet mapping.
    Returns list of devices that may need manual reassignment.
    
    This is a READ-ONLY diagnostic tool - no automatic changes are made.
    """
    from models.subnet import Subnet
    from models.device import Device
    from middleware.rbac import scoped_query
    
    misaligned_devices = []
    
    # Get all devices with IP addresses (respecting user scope)
    devices = scoped_query(Device).filter(Device.device_ip != None).all()
    
    for device in devices:
        best_subnet = Subnet.get_best_match(device.device_ip)
        
        if best_subnet:
            suggested_site_id = best_subnet.site_id
            current_site_id = device.site_id
            
            # Check for mismatch
            if current_site_id != suggested_site_id:
                misaligned_devices.append({
                    'device_id': device.device_id,
                    'device_name': device.device_name,
                    'device_ip': device.device_ip,
                    'device_type': device.device_type,
                    'current_site_id': current_site_id,
                    'suggested_site_id': suggested_site_id,
                    'subnet_cidr': best_subnet.cidr,
                    'reason': f'IP {device.device_ip} is in subnet {best_subnet.cidr} mapped to site {suggested_site_id}'
                })
        elif device.site_id is not None:
            # Device has site but IP is not in any mapped subnet
            misaligned_devices.append({
                'device_id': device.device_id,
                'device_name': device.device_name,
                'device_ip': device.device_ip,
                'device_type': device.device_type,
                'current_site_id': device.site_id,
                'suggested_site_id': None,
                'subnet_cidr': None,
                'reason': f'IP {device.device_ip} is not in any mapped subnet'
            })
    
    return jsonify({
        'status': 'ok',
        'total_devices': len(devices),
        'misaligned_count': len(misaligned_devices),
        'misaligned_devices': misaligned_devices
    })


@devices_bp.route('/api/devices/<int:device_id>/suggest-site', methods=['GET'])
@require_permission('devices.view')
def suggest_site_for_device(device_id):
    """
    Get site suggestion for a specific device based on subnet mapping.
    Returns current site, suggested site, and reasoning.
    """
    from models.subnet import Subnet
    from models.device import Device
    from middleware.rbac import scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    if not device.device_ip:
        return jsonify({
            'status': 'ok',
            'device_id': device_id,
            'current_site_id': device.site_id,
            'suggested_site_id': None,
            'reason': 'Device has no IP address',
            'action_needed': False
        })
    
    best_subnet = Subnet.get_best_match(device.device_ip)
    
    if not best_subnet:
        return jsonify({
            'status': 'ok',
            'device_id': device_id,
            'device_ip': device.device_ip,
            'current_site_id': device.site_id,
            'suggested_site_id': None,
            'reason': f'IP {device.device_ip} is not in any mapped subnet',
            'action_needed': device.site_id is not None,
            'recommendation': 'Map subnet to a site or manually verify site assignment'
        })
    
    suggested_site_id = best_subnet.site_id
    current_site_id = device.site_id
    
    if current_site_id == suggested_site_id:
        return jsonify({
            'status': 'ok',
            'device_id': device_id,
            'device_ip': device.device_ip,
            'current_site_id': current_site_id,
            'suggested_site_id': suggested_site_id,
            'subnet_cidr': best_subnet.cidr,
            'reason': 'Site assignment matches subnet mapping',
            'action_needed': False
        })
    
    return jsonify({
        'status': 'ok',
        'device_id': device_id,
        'device_ip': device.device_ip,
        'current_site_id': current_site_id,
        'suggested_site_id': suggested_site_id,
        'subnet_cidr': best_subnet.cidr,
        'reason': f'IP {device.device_ip} is in subnet {best_subnet.cidr} mapped to site {suggested_site_id}',
        'action_needed': True,
        'recommendation': f'Consider reassigning device to site {suggested_site_id}'
    })


@devices_bp.route('/api/devices/<int:device_id>/reassign-site', methods=['POST', 'PATCH'])
@require_permission('devices.edit')
def reassign_device_site(device_id):
    """
    Manually reassign a device to a different site.
    Requires explicit admin action - never automatic.
    """
    from models.device import Device
    from models.site import Site
    from middleware.rbac import create_audit_log, scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    data = request.get_json()
    
    new_site_id = data.get('site_id')
    reason = data.get('reason', '').strip()
    
    if new_site_id is None:
        return jsonify({'status': 'error', 'message': 'site_id is required'}), 400
    
    # Validate site exists
    new_site = Site.query.get(new_site_id)
    if not new_site:
        return jsonify({'status': 'error', 'message': f'Site {new_site_id} not found'}), 404
    
    old_site_id = device.site_id
    old_department_id = device.department_id
    
    # Update site (and clear department since it may not belong to new site)
    device.site_id = new_site_id
    device.department_id = None  # Clear department - admin must reassign
    
    db.session.commit()
    
    # Audit log
    create_audit_log(
        action='reassign_site',
        entity_type='device',
        entity_id=device_id,
        entity_name=device.device_name,
        description=f'Reassigned device from site {old_site_id} to site {new_site_id}',
        changes={
            'old_site_id': old_site_id,
            'new_site_id': new_site_id,
            'old_department_id': old_department_id,
            'new_department_id': None,
            'reason': reason
        }
    )
    
    return jsonify({
        'status': 'ok',
        'message': f'Device reassigned to site {new_site_id}',
        'device': device.to_dict(),
        'warning': 'Department assignment cleared - please reassign to appropriate department'
    })


@devices_bp.route('/api/devices/<int:device_id>/icmp-thresholds', methods=['GET'])
@require_login
def get_icmp_thresholds(device_id):
    """Return effective ICMP thresholds for a device with source annotation."""
    from models.device import Device
    from services.alert_manager import AlertManager

    device = Device.query.get_or_404(device_id)
    effective = AlertManager.get_icmp_thresholds(device)

    # Determine source for each field
    def _source(device_attr, profile_key, effective_val, default_val):
        if getattr(device, device_attr, None) is not None:
            return 'device_override'
        profile_id = getattr(device, 'compliance_profile_id', None)
        if profile_id:
            try:
                from models.compliance_profile import ComplianceProfile
                profile = ComplianceProfile.query.get(profile_id)
                if profile and isinstance(getattr(profile, 'rules_json', None), dict):
                    if profile.rules_json.get(profile_key) is not None:
                        return 'compliance_profile'
            except Exception:
                pass
        return 'global_default'

    source_latency_warn = _source('icmp_latency_warning_ms',       'latency_warning_ms',       effective['latency_warning_ms'],      AlertManager.LATENCY_THRESHOLD_MS)
    source_latency_crit = _source('icmp_latency_critical_ms',      'latency_critical_ms',      effective['latency_critical_ms'],     AlertManager.LATENCY_THRESHOLD_MS)
    source_loss_warn    = _source('icmp_packet_loss_warning_pct',  'packet_loss_warning_pct',  effective['packet_loss_warning_pct'], AlertManager.PACKET_LOSS_THRESHOLD_PCT)
    source_loss_crit    = _source('icmp_packet_loss_critical_pct', 'packet_loss_critical_pct', effective['packet_loss_critical_pct'], AlertManager.PACKET_LOSS_THRESHOLD_PCT)

    return jsonify({
        'latency_warning_ms':       effective['latency_warning_ms'],
        'latency_critical_ms':      effective['latency_critical_ms'],
        'packet_loss_warning_pct':  effective['packet_loss_warning_pct'],
        'packet_loss_critical_pct': effective['packet_loss_critical_pct'],
        'source': {
            'latency_warning_ms':       source_latency_warn,
            'latency_critical_ms':      source_latency_crit,
            'packet_loss_warning_pct':  source_loss_warn,
            'packet_loss_critical_pct': source_loss_crit,
        },
        'device_overrides': {
            'icmp_latency_warning_ms':       device.icmp_latency_warning_ms,
            'icmp_latency_critical_ms':      device.icmp_latency_critical_ms,
            'icmp_packet_loss_warning_pct':  device.icmp_packet_loss_warning_pct,
            'icmp_packet_loss_critical_pct': device.icmp_packet_loss_critical_pct,
        }
    })


@devices_bp.route('/api/devices/<int:device_id>/icmp-thresholds', methods=['PATCH'])
@require_login
def set_icmp_thresholds(device_id):
    """Set per-device ICMP threshold overrides. Pass null to clear an override."""
    from middleware.rbac import require_role
    from models.device import Device

    if session.get('role') not in ('admin', 'manager'):
        return jsonify({'error': 'Insufficient permissions'}), 403

    device = Device.query.get_or_404(device_id)
    data = request.get_json(silent=True) or {}

    _INT_FIELDS   = ('icmp_latency_warning_ms', 'icmp_latency_critical_ms')
    _FLOAT_FIELDS = ('icmp_packet_loss_warning_pct', 'icmp_packet_loss_critical_pct')

    errors = []
    for field in _INT_FIELDS:
        if field in data:
            val = data[field]
            if val is None:
                setattr(device, field, None)
            else:
                try:
                    v = int(val)
                    if not (1 <= v <= 60000):
                        errors.append(f'{field} must be 1–60000')
                    else:
                        setattr(device, field, v)
                except (TypeError, ValueError):
                    errors.append(f'{field} must be an integer')

    for field in _FLOAT_FIELDS:
        if field in data:
            val = data[field]
            if val is None:
                setattr(device, field, None)
            else:
                try:
                    v = float(val)
                    if not (0.1 <= v <= 99.9):
                        errors.append(f'{field} must be 0.1–99.9')
                    else:
                        setattr(device, field, v)
                except (TypeError, ValueError):
                    errors.append(f'{field} must be a number')

    if errors:
        return jsonify({'error': '; '.join(errors)}), 400

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[devices] ICMP threshold save failed device=%s: %s', device_id, exc)
        return jsonify({'error': 'Database error'}), 500

    logger.info('[devices] ICMP thresholds updated device=%s by user=%s', device_id, session.get('user_id'))
    return jsonify({'status': 'ok', 'message': 'ICMP thresholds saved'})


@devices_bp.route('/api/devices/<int:device_id>/compliance-profile', methods=['PATCH'])
@require_login
def set_device_compliance_profile(device_id):
    """Assign or unassign a compliance profile from a device."""
    from models.device import Device

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin required'}), 403

    device = Device.query.get_or_404(device_id)
    data = request.get_json(silent=True) or {}
    profile_id = data.get('compliance_profile_id')  # null to unassign

    if profile_id is not None:
        from models.compliance_profile import ComplianceProfile
        if not ComplianceProfile.query.get(profile_id):
            return jsonify({'error': 'Compliance profile not found'}), 404

    device.compliance_profile_id = profile_id
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[devices] Compliance profile assign failed device=%s: %s', device_id, exc)
        return jsonify({'error': 'Database error'}), 500

    logger.info('[devices] Compliance profile set device=%s profile=%s by user=%s', device_id, profile_id, session.get('user_id'))
    return jsonify({'status': 'ok', 'compliance_profile_id': profile_id})


@devices_bp.route('/api/devices/bulk-compliance-profile', methods=['PATCH'])
@require_login
def bulk_set_compliance_profile():
    """Assign or unassign a compliance profile on multiple devices at once.

    Body: { "device_ids": [1, 2, 3], "compliance_profile_id": 5 }
    Pass null for compliance_profile_id to unassign from all listed devices.
    """
    from models.device import Device

    if session.get('role') != 'admin':
        return jsonify({'error': 'Admin required'}), 403

    data = request.get_json(silent=True) or {}
    device_ids = data.get('device_ids')
    profile_id = data.get('compliance_profile_id')  # null → unassign

    if not device_ids or not isinstance(device_ids, list):
        return jsonify({'error': 'device_ids must be a non-empty list'}), 400

    device_ids = [int(d) for d in device_ids if str(d).isdigit()]
    if not device_ids:
        return jsonify({'error': 'No valid device IDs provided'}), 400

    if profile_id is not None:
        from models.compliance_profile import ComplianceProfile
        if not ComplianceProfile.query.get(int(profile_id)):
            return jsonify({'error': 'Compliance profile not found'}), 404
        profile_id = int(profile_id)

    try:
        updated = (
            Device.query
            .filter(Device.device_id.in_(device_ids))
            .update({'compliance_profile_id': profile_id}, synchronize_session=False)
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('[devices] Bulk compliance profile assign failed: %s', exc)
        return jsonify({'error': 'Database error'}), 500

    logger.info(
        '[devices] Bulk compliance profile set profile=%s on %s devices by user=%s',
        profile_id, updated, session.get('user_id')
    )
    return jsonify({'status': 'ok', 'compliance_profile_id': profile_id, 'updated_count': updated})


@devices_bp.route('/api/devices/<int:device_id>/live-snapshot', methods=['GET'])
@require_login
def api_device_live_snapshot(device_id):
    """Live snapshot for device details tabs: ping stats, agent metrics, SNMP metrics."""
    from models.device import Device
    from models.server_health import ServerHealthLog
    from models.scan_history import DeviceScanHistory
    from middleware.rbac import scoped_query
    from datetime import timedelta

    device = scoped_query(Device).get_or_404(device_id)

    latest_agent = (
        ServerHealthLog.query
        .filter(ServerHealthLog.device_id == device_id, ServerHealthLog.source == 'agent')
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    latest_snmp = (
        ServerHealthLog.query
        .filter(ServerHealthLog.device_id == device_id, ServerHealthLog.source == 'snmp')
        .order_by(ServerHealthLog.timestamp.desc())
        .first()
    )

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_scans = (
        DeviceScanHistory.query
        .filter(
            DeviceScanHistory.device_ip == device.device_ip,
            DeviceScanHistory.scan_timestamp >= seven_days_ago,
            _exclude_agent_push_scan(DeviceScanHistory),
        )
        .order_by(DeviceScanHistory.scan_timestamp.desc())
        .limit(100)
        .all()
    )

    latencies = [s.ping_time_ms for s in recent_scans if s.ping_time_ms and s.ping_time_ms > 0]
    online_count = sum(1 for s in recent_scans if (s.status or '').lower() == 'online')
    pkt_vals = [s.packet_loss for s in recent_scans if s.packet_loss is not None]
    avg_ms = round(sum(latencies) / len(latencies), 1) if latencies else None

    ping_stats = {
        'avg_ms': avg_ms,
        'min_ms': round(min(latencies), 1) if latencies else None,
        'max_ms': round(max(latencies), 1) if latencies else None,
        'online_count': online_count,
        'offline_count': len(recent_scans) - online_count,
        'total_scans': len(recent_scans),
        'avg_packet_loss': round(sum(pkt_vals) / len(pkt_vals), 1) if pkt_vals else None,
        'sparkline': [
            {
                't': s.scan_timestamp.strftime('%H:%M'),
                'ts': s.scan_timestamp.isoformat() + 'Z',
                'display_t': _format_ist(s.scan_timestamp, include_tz=False),
                'v': round(s.ping_time_ms, 1) if s.ping_time_ms else 0,
                'status': (s.status or '').lower(),
            }
            for s in reversed(recent_scans[:50])
        ],
    }

    agent_data = None
    if latest_agent:
        agent_data = latest_agent.to_dict()
        agent_data['network_in_bps'] = latest_agent.network_in_bps
        agent_data['network_out_bps'] = latest_agent.network_out_bps
    else:
        import json
        tracked_device = None
        from models.device_identity_link import DeviceIdentityLink
        active_link = DeviceIdentityLink.query.filter_by(device_id=device_id, is_active=True).first()
        if active_link and active_link.tracked_device_id:
            from models.tracked_device import TrackedDevice
            tracked_device = TrackedDevice.query.get(active_link.tracked_device_id)
        if not tracked_device and device.macaddress:
            from models.tracked_device import TrackedDevice
            tracked_device = TrackedDevice.query.filter_by(mac_address=device.macaddress).first()
        
        if tracked_device and tracked_device.tracking_data:
            try:
                wt_data = json.loads(tracked_device.tracking_data)
                stats = wt_data.get('current_stats', wt_data)
                sys_metrics = stats.get('system_metrics', {})
                dev_info = stats.get('device_info', {})
                # build a dictionary shaped exactly like agent_data from ServerHealthLog
                agent_data = {
                    'timestamp': wt_data.get('timestamp'),
                    'os_name': dev_info.get('system'),
                    'os_version': dev_info.get('os_version'),
                    'os_arch': dev_info.get('os_arch'),
                    'uptime': "N/A",
                    'process_count': sys_metrics.get('process_count'),
                    'cpu_usage': sys_metrics.get('cpu_percent'),
                    'load_avg_1min': None,
                    'memory_usage': sys_metrics.get('memory_percent'),
                    'memory_total_gb': sys_metrics.get('total_gb'),
                    'memory_used_gb': sys_metrics.get('used_gb'),
                    'disk_usage': sys_metrics.get('disk_usage'),
                    'disk_total_gb': sys_metrics.get('disk_total_gb'),
                    'disk_used_gb': sys_metrics.get('disk_used_gb'),
                    'swap_percent': sys_metrics.get('swap_percent'),
                    'swap_total_mb': sys_metrics.get('swap_total_mb'),
                    'swap_used_mb': sys_metrics.get('swap_used_mb'),
                    'top_processes': sys_metrics.get('top_processes') or [],
                    'network_in_bps': None,
                    'network_out_bps': None,
                }
            except Exception:
                pass

    return jsonify({
        'ping_stats': ping_stats,
        'agent_metrics': agent_data,
        'snmp_metrics': latest_snmp.to_dict() if latest_snmp else None,
        'updated_at': datetime.utcnow().isoformat() + 'Z',
    })
