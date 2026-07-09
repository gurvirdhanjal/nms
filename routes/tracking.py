from flask import Blueprint, render_template, jsonify, make_response, request, session, redirect, url_for, Response, stream_with_context
from middleware.rbac import require_login, require_permission, require_role, create_audit_log, current_scope_cache_fragment
from extensions import db, redis_client, is_redis_available
from models.tracked_device import (
    TrackedDevice,
    TrackedDeviceIpHistory,
    RemoteDeviceScanHistory,
    DeviceActivityLog,
    DeviceResourceLog,
    DeviceApplicationLog,
    TrackingSample,
    TrackingHistoryIntegrityAudit,
    TrackedDeviceAvailabilityEvent,
    TrackingHourlyRollup,
    TrackingDailyRollup,
)
from models.dashboard import DashboardEvent
from models.audit_log import AuditLog
from models.device import Device
from models.device_effective_policy_cache import DeviceEffectivePolicyCache
from models.device_identity_link import DeviceIdentityLink
from models.device_identity_link_candidate import DeviceIdentityLinkCandidate
from models.restricted_site_policy import (
    RestrictedSiteAlertState,
    RestrictedSiteDomainMeta,
    RestrictedSiteEvent,
    RestrictedSitePolicy,
    TrackingAgentKeyBinding,
    build_policy_version,
    normalize_domain,
)
from models.alert_fanout_task import AlertFanoutTask
from models.policy_rebuild_task import PolicyRebuildTask
from models.tracking_sync_envelope import TrackingSyncEnvelope
from models.typed_text_policy_alert import TypedTextPolicyAlert
from models.user import User
from models.scan_history import DeviceScanHistory
from services.operational_error_handling import summarize_exception
from sqlalchemy.exc import OperationalError as SAOperationalError
from datetime import datetime, timedelta, timezone
import requests
import json
import os
import socket
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
import platform
import subprocess
import psutil
import ipaddress
import time
import logging
import re
import hmac
import hashlib
import secrets
from urllib.parse import urlparse
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import io
from werkzeug.exceptions import HTTPException
from services.tracking_reconcile import (
    normalize_mac,
    run_reconciliation,
    is_reconciliation_locked,
)
from services.tracking_history import (
    build_history_envelope,
    ingest_tracking_sample,
    parse_history_window_strict,
    parse_workstation_window,
    query_history_summary,
    query_history_dashboard,
    query_activity_page,
    query_resource_page,
    query_application_page,
    query_integrity_page,
    run_tracking_integrity_checks,
    run_tracking_retention,
)
from services.tracking_workstation import (
    calculate_daily_uptime_snapshot,
    get_scoped_tracked_device_or_404,
    persist_availability_event,
    query_availability_events_page,
    query_workstation_anomalies,
    query_workstation_overview,
    query_workstation_reports,
    scoped_tracked_device_query,
)
from services.tracked_device_ip_change import apply_tracked_device_ip_change, TrackedDeviceIpSyncError
from services.tracking_agent_ports import (
    preferred_tracking_agent_port,
    remember_tracking_agent_port,
    resolve_tracking_agent_ports,
)
from services.tracking_discovery_cache import (
    get_cached_tracking_probe,
    remember_tracking_probe,
)
from services.tracking_freshness import (
    build_controls_contract,
    build_live_freshness,
)
from services.notification_service import NotificationService
from services.sse_broadcaster import broadcast_event
from services.device_link_service import DeviceLinkService
from services.effective_policy_service import (
    EffectivePolicyUnavailable,
    enqueue_policy_rebuild,
    enqueue_policy_rebuild_for_all_tracked_devices,
    get_effective_policy,
)
from services.restricted_site_ingest_service import (
    RESTRICTED_CONFIDENCE_HIGH,
    RESTRICTED_CONFIDENCE_LOW,
    RESTRICTED_CONFIDENCE_MEDIUM,
    RESTRICTED_SOURCE_DNS,
    RESTRICTED_SOURCE_WINDOW,
    apply_restricted_site_ingest,
    build_restricted_alert_message as service_build_restricted_alert_message,
    coerce_restricted_events as service_coerce_restricted_events,
    match_restricted_domain as service_match_restricted_domain,
    maybe_uplift_confidence as service_maybe_uplift_confidence,
    parse_observed_datetime as service_parse_observed_datetime,
    plan_restricted_site_ingest,
)
from services.tracking_sync_core_service import (
    extract_current_stats_payload,
    normalize_current_stats_payload,
    plan_sync_core_mutations,
)
from services.tracking_identity_resolution_service import (
    build_actor_context,
    build_identity_input,
    preview_reconciliation_for_tracked_device,
    reconcile_tracking_identity,
    resolve_scan_device_identity,
)
from services.tracking_sync_intake_service import (
    build_sync_policy_payload,
    current_sync_mode,
)

tracking_bp = Blueprint('tracking_bp', __name__)
_tracking_local_cache = {}
_tracking_local_cache_ttl = {}
_tracking_local_locks = {}
_tracking_local_registry_lock = threading.Lock()
_TRACKING_CACHE_NAMESPACE = 'tracking'
_TRACKING_CACHE_VERSION = 'v1'
_TRACKING_ELIGIBLE_INVENTORY_KEY_PREFIX = 'eligible-inventory'


def _parse_limit(default: int = 100, max_val: int = 500) -> int:
    """Parse and cap the ?limit= query parameter."""
    return min(max(1, request.args.get('limit', default, type=int)), max_val)


@tracking_bp.before_request
def _tracking_auth_guard():
    # Only enforce tracking blueprint specific auth on specific endpoints.
    # The application-wide auth is handled by standard require_login decorators.
    pass

# Use centralized config for API key
from config import Config
SHARED_API_KEY = Config.API_KEY
logger = logging.getLogger(__name__)
PURGE_TOKEN_TTL_SECONDS = 300
_TRACKING_PURGE_TOKENS = {}
_TRACKING_PURGE_TOKEN_LOCK = threading.Lock()
RESTRICTED_CORROBORATION_WINDOW_SECONDS = 120
HOSTNAME_CANDIDATE_RE = re.compile(r'(?<!@)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b', re.IGNORECASE)
SYNC_IP_REASON_PAYLOAD = 'SYNC_PAYLOAD_UPDATE'
SYNC_IP_REASON_MANUAL = 'MANUAL_EDIT'
SYNC_IP_REASON_RECONCILE = 'RECONCILE_RELOCATION'
_TRACKING_HARDWARE_SPEC_KEYS = {
    'cpu_model',
    'cpu_physical_cores',
    'cpu_logical_cores',
    'memory_total_gb',
    'disk_total_gb',
    'architecture',
}


def _extract_tracking_hardware_specs(payload):
    if not isinstance(payload, dict):
        return None

    raw_specs = payload.get('hardware_specs') if isinstance(payload.get('hardware_specs'), dict) else {}
    current_stats = extract_current_stats_payload(payload) or {}
    device_info = current_stats.get('device_info') if isinstance(current_stats.get('device_info'), dict) else {}
    system_metrics = current_stats.get('system_metrics') if isinstance(current_stats.get('system_metrics'), dict) else {}

    derived_specs = {
        'cpu_model': raw_specs.get('cpu_model') or device_info.get('processor'),
        'cpu_physical_cores': raw_specs.get('cpu_physical_cores'),
        'cpu_logical_cores': raw_specs.get('cpu_logical_cores'),
        'memory_total_gb': raw_specs.get('memory_total_gb') if raw_specs.get('memory_total_gb') is not None else system_metrics.get('total_gb'),
        'disk_total_gb': raw_specs.get('disk_total_gb') if raw_specs.get('disk_total_gb') is not None else system_metrics.get('disk_total_gb'),
        'architecture': raw_specs.get('architecture') or device_info.get('os_arch'),
    }

    specs = {}
    for key in _TRACKING_HARDWARE_SPEC_KEYS:
        value = derived_specs.get(key)
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            specs[key] = value
    return specs or None


def _inventory_sync_error_message(reason_code):
    reason = str(reason_code or 'SYNC_FAILED').strip().upper()
    if reason == 'IP_COLLISION':
        return 'Linked inventory device already uses a different device record for this IP.'
    if reason == 'MAC_MISMATCH':
        return 'Linked inventory sync blocked because the active link MAC does not match the tracked device MAC.'
    if reason == 'LINK_CONFLICT':
        return 'Linked inventory sync blocked because more than one active identity link exists for this tracked device.'
    return 'Linked inventory sync failed.'


def _purge_tracked_device(device):
    """Delete a tracked device and its dependent rows in FK-safe order."""
    device_id = int(device.id)
    normalized_mac = normalize_mac(device.mac_address)

    delete_specs = (
        (AlertFanoutTask, AlertFanoutTask.tracked_device_id),
        (PolicyRebuildTask, PolicyRebuildTask.tracked_device_id),
        (DeviceEffectivePolicyCache, DeviceEffectivePolicyCache.tracked_device_id),
        (DeviceIdentityLinkCandidate, DeviceIdentityLinkCandidate.tracked_device_id),
        (DeviceIdentityLink, DeviceIdentityLink.tracked_device_id),
        (TrackingAgentKeyBinding, TrackingAgentKeyBinding.tracked_device_id),
        (RestrictedSiteAlertState, RestrictedSiteAlertState.device_id),
        (RestrictedSiteDomainMeta, RestrictedSiteDomainMeta.device_id),
        (RestrictedSiteEvent, RestrictedSiteEvent.device_id),
        (TrackedDeviceAvailabilityEvent, TrackedDeviceAvailabilityEvent.device_id),
        (TrackingHistoryIntegrityAudit, TrackingHistoryIntegrityAudit.device_id),
        (TrackingHourlyRollup, TrackingHourlyRollup.device_id),
        (TrackingDailyRollup, TrackingDailyRollup.device_id),
        (DeviceActivityLog, DeviceActivityLog.device_id),
        (DeviceResourceLog, DeviceResourceLog.device_id),
        (DeviceApplicationLog, DeviceApplicationLog.device_id),
        (TrackedDeviceIpHistory, TrackedDeviceIpHistory.device_id),
    )

    for model, column in delete_specs:
        model.query.filter(column == device_id).delete(synchronize_session=False)

    TrackingSample.query.filter(TrackingSample.device_id == device_id).delete(synchronize_session=False)
    TrackingSyncEnvelope.query.filter(TrackingSyncEnvelope.tracked_device_id == device_id).update(
        {TrackingSyncEnvelope.tracked_device_id: None},
        synchronize_session=False,
    )

    if normalized_mac:
        RemoteDeviceScanHistory.query.filter(
            db.func.replace(db.func.upper(RemoteDeviceScanHistory.mac_address), '-', ':') == normalized_mac
        ).delete(synchronize_session=False)

    db.session.delete(device)


def _coerce_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def _tracking_cache_key(key: str) -> str:
    return f"{_TRACKING_CACHE_NAMESPACE}:{str(key).strip()}:{_TRACKING_CACHE_VERSION}"


def _tracking_scope_suffix() -> str:
    return current_scope_cache_fragment().replace(':', '__')


def _eligible_inventory_cache_key(scope_suffix: str | None = None) -> str:
    return _tracking_cache_key(f"{_TRACKING_ELIGIBLE_INVENTORY_KEY_PREFIX}:{scope_suffix or _tracking_scope_suffix()}")


def _get_tracking_local_lock(lock_key: str) -> threading.Lock:
    with _tracking_local_registry_lock:
        if lock_key not in _tracking_local_locks:
            _tracking_local_locks[lock_key] = threading.Lock()
        return _tracking_local_locks[lock_key]


def _get_cached_tracking_value(cache_key: str):
    if is_redis_available() and not getattr(Config, 'TESTING', False):
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as exc:
            logger.debug('[TrackingCache] Redis read failed key=%s err=%s', cache_key, exc)

    if cache_key in _tracking_local_cache:
        if datetime.utcnow() < _tracking_local_cache_ttl.get(cache_key, datetime.min):
            return _tracking_local_cache[cache_key]
    return None


def _set_cached_tracking_value(cache_key: str, value, ttl_seconds: int):
    ttl_seconds = max(15, int(ttl_seconds or 90))
    if is_redis_available() and not getattr(Config, 'TESTING', False):
        try:
            redis_client.setex(cache_key, ttl_seconds, json.dumps(value))
            return
        except Exception as exc:
            logger.debug('[TrackingCache] Redis write failed key=%s err=%s', cache_key, exc)

    _tracking_local_cache[cache_key] = value
    _tracking_local_cache_ttl[cache_key] = datetime.utcnow() + timedelta(seconds=ttl_seconds)


def _acquire_tracking_cache_lock(lock_key: str, ttl_seconds: int = 15) -> bool:
    versioned_lock_key = _tracking_cache_key(f'lock:{lock_key}')
    if is_redis_available() and not getattr(Config, 'TESTING', False):
        try:
            return bool(redis_client.set(versioned_lock_key, '1', nx=True, ex=max(5, int(ttl_seconds or 15))))
        except Exception as exc:
            logger.debug('[TrackingCache] Redis lock failed key=%s err=%s', versioned_lock_key, exc)

    return _get_tracking_local_lock(lock_key).acquire(blocking=False)


def _release_tracking_cache_lock(lock_key: str):
    versioned_lock_key = _tracking_cache_key(f'lock:{lock_key}')
    if is_redis_available() and not getattr(Config, 'TESTING', False):
        try:
            redis_client.delete(versioned_lock_key)
        except Exception as exc:
            logger.debug('[TrackingCache] Redis unlock failed key=%s err=%s', versioned_lock_key, exc)

    local_lock = _tracking_local_locks.get(lock_key)
    if local_lock is not None:
        try:
            local_lock.release()
        except RuntimeError:
            pass


def _invalidate_tracking_inventory_candidates_cache():
    prefix = _tracking_cache_key(f'{_TRACKING_ELIGIBLE_INVENTORY_KEY_PREFIX}:')
    local_keys = [key for key in list(_tracking_local_cache.keys()) if key.startswith(prefix)]
    for key in local_keys:
        _tracking_local_cache.pop(key, None)
        _tracking_local_cache_ttl.pop(key, None)

    if is_redis_available() and not getattr(Config, 'TESTING', False):
        try:
            redis_keys = list(redis_client.scan_iter(match=f'{prefix}*', count=100))
            if redis_keys:
                redis_client.delete(*redis_keys)
        except Exception as exc:
            logger.debug('[TrackingCache] Redis invalidation failed prefix=%s err=%s', prefix, exc)


def _query_tracking_inventory_candidates():
    from middleware.rbac import scoped_query

    inventory_devices = scoped_query(Device).order_by(Device.device_name.asc()).all()
    scanner = NetworkScanner()

    _cutoff_48h = datetime.utcnow() - timedelta(hours=48)
    latest_scan_subq = (
        db.session.query(
            DeviceScanHistory.device_ip.label('device_ip'),
            db.func.max(DeviceScanHistory.scan_id).label('max_scan_id'),
        )
        .filter(
            DeviceScanHistory.device_ip.in_([device.device_ip for device in inventory_devices if device.device_ip]),
            DeviceScanHistory.scan_timestamp >= _cutoff_48h,
        )
        .group_by(DeviceScanHistory.device_ip)
        .subquery()
    )
    latest_scan_rows = (
        db.session.query(
            DeviceScanHistory.device_ip,
            DeviceScanHistory.status,
        )
        .join(
            latest_scan_subq,
            (DeviceScanHistory.device_ip == latest_scan_subq.c.device_ip)
            & (DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id),
        )
        .all()
    )
    latest_status_by_ip = {
        row.device_ip: ('Online' if str(row.status or '').strip().lower() in ('online', 'up') else 'Offline')
        for row in latest_scan_rows
    }

    tracked_devices = scoped_tracked_device_query(
        include_archived=False,
        include_unscoped_for_admin=True,
    ).all()
    tracked_macs = {
        normalize_mac(device.mac_address)
        for device in tracked_devices
        if getattr(device, 'mac_address', None)
    }
    tracked_macs.discard(None)

    active_linked_inventory_ids = {
        int(device_id)
        for device_id, in db.session.query(DeviceIdentityLink.device_id)
        .join(TrackedDevice, TrackedDevice.id == DeviceIdentityLink.tracked_device_id)
        .filter(
            DeviceIdentityLink.is_active.is_(True),
            db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None)),
        )
        .all()
        if device_id
    }

    candidates = []
    for device in inventory_devices:
        normalized_mac = normalize_mac(getattr(device, 'macaddress', None))
        if int(device.device_id) in active_linked_inventory_ids:
            continue
        if normalized_mac and normalized_mac in tracked_macs:
            continue

        device_ip = (getattr(device, 'device_ip', None) or '').strip()
        if not device_ip:
            continue
        service_info = scanner.check_tracking_service(device_ip, profile='scan') or {}
        tracking_status = str(service_info.get('tracking_status') or service_info.get('status') or '').strip().lower()
        if tracking_status != 'tracking_active':
            continue

        latest_agent_log_at = None
        if service_info.get('data') and isinstance(service_info.get('data'), dict):
            latest_agent_log_at = (
                service_info['data'].get('last_agent_sync_at')
                or service_info['data'].get('timestamp')
            )

        candidates.append({
            'device_id': int(device.device_id),
            'device_name': device.device_name or '',
            'device_type': device.device_type or '',
            'device_ip': device_ip,
            'macaddress': device.macaddress or '',
            'hostname': device.hostname or '',
            'manufacturer': device.manufacturer or '',
            'status': 'Maintenance' if getattr(device, 'maintenance_mode', False) else latest_status_by_ip.get(device.device_ip, 'Offline'),
            'agent_recent': True,
            'agent_configured': True,
            'last_agent_log_at': latest_agent_log_at,
        })

    return candidates


def _build_tracking_inventory_candidates(force_refresh=False):
    cache_ttl_seconds = max(
        30,
        int(getattr(Config, 'TRACKING_ELIGIBLE_INVENTORY_CACHE_TTL_SECONDS', 90) or 90),
    )
    scope_suffix = _tracking_scope_suffix()
    cache_key = _eligible_inventory_cache_key(scope_suffix)
    lock_key = f'{_TRACKING_ELIGIBLE_INVENTORY_KEY_PREFIX}:{scope_suffix}'

    if not force_refresh:
        cached = _get_cached_tracking_value(cache_key)
        if isinstance(cached, list):
            return cached

    acquired = _acquire_tracking_cache_lock(lock_key, ttl_seconds=15)
    if not acquired:
        cached = _get_cached_tracking_value(cache_key)
        if isinstance(cached, list):
            return cached
        return _query_tracking_inventory_candidates()

    try:
        candidates = _query_tracking_inventory_candidates()
        _set_cached_tracking_value(cache_key, candidates, ttl_seconds=cache_ttl_seconds)
        return candidates
    finally:
        _release_tracking_cache_lock(lock_key)


def _super_admin_allowlist():
    raw = getattr(Config, "SUPER_ADMIN_USERNAMES", "")
    return {entry.strip().lower() for entry in str(raw).split(",") if entry.strip()}


def _can_purge_tracking_history():
    username = str(session.get('username') or '').strip().lower()
    role = str(session.get('role') or '').strip().lower()
    if role != 'admin':
        return False
    allowlist = _super_admin_allowlist()
    if not allowlist:
        return False
    return username in allowlist


def _scope_defaults_for_new_tracked_device() -> dict[str, int | None]:
    """Apply RBAC scope defaults when creating tracked devices from UI actions."""
    role = str(session.get('role') or '').strip().lower()
    if role == 'admin':
        return {}

    site_id = session.get('site_id')
    department_id = session.get('department_id')
    user_id = session.get('user_id')
    if (site_id is None or department_id is None) and user_id:
        user = User.query.get(user_id)
        if user:
            site_id = user.site_id
            department_id = user.department_id

    scoped = {}
    if site_id is not None:
        scoped['site_id'] = int(site_id)
    if department_id is not None:
        scoped['department_id'] = int(department_id)
    return scoped


def _issue_purge_token(payload: dict):
    token = str(time.time_ns()) + "-" + str(abs(hash(json.dumps(payload, sort_keys=True))))
    expires_at = time.time() + PURGE_TOKEN_TTL_SECONDS
    with _TRACKING_PURGE_TOKEN_LOCK:
        _TRACKING_PURGE_TOKENS[token] = {
            "payload": payload,
            "expires_at": expires_at,
        }
    return token, expires_at


def _consume_purge_token(token: str):
    now_ts = time.time()
    with _TRACKING_PURGE_TOKEN_LOCK:
        # Cleanup expired tokens opportunistically.
        expired = [key for key, value in _TRACKING_PURGE_TOKENS.items() if value.get("expires_at", 0) <= now_ts]
        for key in expired:
            _TRACKING_PURGE_TOKENS.pop(key, None)
        stored = _TRACKING_PURGE_TOKENS.pop(token, None)
    if not stored:
        return None
    if stored.get("expires_at", 0) <= now_ts:
        return None
    return stored.get("payload")


class AgentHttpError(Exception):
    def __init__(self, code, message, original=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.original = original


_agent_http_session = None
_proxy_bypass_logged = False


def _get_agent_http_session():
    global _agent_http_session, _proxy_bypass_logged
    if _agent_http_session is None:
        session = requests.Session()
        # Critical for monitoring: never inherit host proxy env for LAN agent calls.
        session.trust_env = False
        _agent_http_session = session
        if not _proxy_bypass_logged:
            logger.info("[AgentHTTP] proxy-bypass enabled (trust_env=False) for service.py polling")
            _proxy_bypass_logged = True
    return _agent_http_session


def _map_agent_request_error(exc):
    if isinstance(exc, requests.exceptions.ProxyError):
        return "AGENT_PROXY_BLOCKED", "Agent request blocked by proxy settings"
    if isinstance(exc, requests.exceptions.Timeout):
        return "AGENT_TIMEOUT", "Agent request timed out"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "AGENT_UNREACHABLE", "Could not connect to agent endpoint"
    return "AGENT_REQUEST_FAILED", "Agent request failed"


def _agent_http_request(
    method,
    url,
    timeout=2.0,
    headers=None,
    stream=False,
    silent=False,
    params=None,
    json_data=None,
    data=None,
    files=None,
):
    parsed = urlparse(url)
    started = time.monotonic()
    session = _get_agent_http_session()
    try:
        request_kwargs = {
            'method': method,
            'url': url,
            'timeout': timeout,
            'headers': headers,
            'stream': stream,
        }
        if params is not None:
            request_kwargs['params'] = params
        if json_data is not None:
            request_kwargs['json'] = json_data
        if data is not None:
            request_kwargs['data'] = data
        if files is not None:
            request_kwargs['files'] = files

        response = session.request(
            **request_kwargs,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if not silent:
            logger.info(
                "[AgentHTTP] method=%s host=%s path=%s result=ok status=%s latency_ms=%s",
                method.upper(),
                parsed.hostname,
                parsed.path,
                response.status_code,
                latency_ms,
            )
        return response
    except requests.exceptions.RequestException as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        code, message = _map_agent_request_error(exc)
        if not silent:
            logger.warning(
                "[AgentHTTP] method=%s host=%s path=%s result=%s latency_ms=%s error=%s",
                method.upper(),
                parsed.hostname,
                parsed.path,
                code.lower(),
                latency_ms,
                exc,
            )
        raise AgentHttpError(code, message, original=exc) from exc


def _agent_http_get(url, timeout=2.0, headers=None, stream=False, silent=False, params=None):
    return _agent_http_request(
        "GET",
        url,
        timeout=timeout,
        headers=headers,
        stream=stream,
        silent=silent,
        params=params,
    )


def _agent_http_post(url, timeout=2.0, headers=None, json_data=None, stream=False, silent=False, data=None, files=None):
    return _agent_http_request(
        "POST",
        url,
        timeout=timeout,
        headers=headers,
        stream=stream,
        silent=silent,
        json_data=json_data,
        data=data,
        files=files,
    )


def _agent_error_response(error, status=503):
    return jsonify({
        'success': False,
        'error_code': error.code,
        'error': error.message,
    }), status


def _json_error(error_code, message, status=400):
    return jsonify({
        'success': False,
        'error_code': error_code,
        'error': message,
    }), status


def _json_exception(error_code, message, exc=None, status=500):
    if exc is not None:
        logger.exception("[TrackingAPI] %s (%s): %s", message, error_code, exc)
    else:
        logger.error("[TrackingAPI] %s (%s)", message, error_code)
    return _json_error(error_code, message, status)


def _normalize_tracking_snapshot_dict(raw_payload):
    if not isinstance(raw_payload, dict):
        return {}

    candidate = raw_payload.get('tracking_data')
    if isinstance(candidate, dict):
        normalized_candidate = normalize_current_stats_payload(
            candidate,
            hostname=str(raw_payload.get('hostname') or '').strip() or None,
        )
        if isinstance(normalized_candidate, dict):
            return normalized_candidate

    normalized_payload = extract_current_stats_payload(raw_payload)
    return normalized_payload if isinstance(normalized_payload, dict) else {}


def _loads_tracking_snapshot(raw_text):
    try:
        decoded = json.loads(raw_text)
    except Exception:
        return {}
    return _normalize_tracking_snapshot_dict(decoded)


def _remember_sync_discovery_state(ip_candidates, *, current_stats, availability_status, metrics_available, probe_error_code, agent_port):
    if not isinstance(current_stats, dict):
        return

    payload = {
        'status': 'tracking_active',
        'tracking_status': 'tracking_active',
        'availability_status': availability_status,
        'metrics_available': bool(metrics_available),
        'probe_error_code': probe_error_code,
        'probe_method': 'sync',
        'agent_port': agent_port,
        'data': current_stats,
    }

    seen = set()
    for candidate_ip in ip_candidates or []:
        normalized_ip = str(candidate_ip or '').strip()
        if not normalized_ip or normalized_ip in seen:
            continue
        seen.add(normalized_ip)
        if agent_port:
            remember_tracking_agent_port(normalized_ip, agent_port)
        remember_tracking_probe(normalized_ip, payload)


def _resolve_tracked_agent_ip(device):
    sync_ip = str(getattr(device, 'last_agent_sync_ip', None) or '').strip()
    primary_ip = str(getattr(device, 'ip_address', None) or '').strip()

    for candidate in (sync_ip, primary_ip):
        if candidate and not candidate.startswith('127.'):
            return candidate
    return sync_ip or primary_ip or None


def _tracked_agent_headers():
    api_key = str(SHARED_API_KEY or '').strip()
    if not api_key:
        raise ValueError('TRACKING_API_KEY is not configured for tracked agent file transfer.')
    return {'X-API-Key': api_key}


def _tracked_agent_base_url(device):
    agent_ip = _resolve_tracked_agent_ip(device)
    if not agent_ip:
        raise ValueError('Tracked device does not have a reachable agent address.')
    agent_port = preferred_tracking_agent_port(agent_ip)
    return f"http://{agent_ip}:{agent_port}"


def _tracked_agent_request(
    device,
    method,
    path,
    *,
    timeout=2.0,
    headers=None,
    stream=False,
    silent=False,
    params=None,
    json_data=None,
    data=None,
    files=None,
):
    agent_ip = _resolve_tracked_agent_ip(device)
    if not agent_ip:
        raise ValueError('Tracked device does not have a reachable agent address.')

    last_error = None
    for agent_port in resolve_tracking_agent_ports(agent_ip):
        url = f"http://{agent_ip}:{agent_port}{path}"
        try:
            response = _agent_http_request(
                method,
                url,
                timeout=timeout,
                headers=headers,
                stream=stream,
                silent=silent,
                params=params,
                json_data=json_data,
                data=data,
                files=files,
            )
            remember_tracking_agent_port(agent_ip, agent_port)
            return response
        except AgentHttpError as exc:
            last_error = exc
            if exc.code in ('AGENT_UNREACHABLE', 'AGENT_TIMEOUT'):
                continue
            raise

    if last_error:
        raise last_error
    raise AgentHttpError('AGENT_UNREACHABLE', 'Could not connect to agent endpoint')


def _file_proxy_error_status(response):
    if response.status_code in {400, 403, 404, 409}:
        return response.status_code
    return 502


def _file_proxy_error(error_code, fallback_message, response):
    message = fallback_message
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = str(payload.get('error') or payload.get('message') or fallback_message)
    except Exception:
        pass
    return _json_error(error_code, message, _file_proxy_error_status(response))


def _agent_response_content_type(response):
    headers = getattr(response, 'headers', {}) or {}
    return str(headers.get('content-type') or headers.get('Content-Type') or '').lower()


def _agent_response_json(response):
    if not _agent_response_content_type(response).startswith('application/json'):
        return {}
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_tracking_api_key():
    api_key = (request.headers.get('X-API-Key') or '').strip()
    if api_key:
        return api_key
    payload = request.get_json(silent=True) or {}
    return str(payload.get('api_key') or '').strip()


def _require_tracking_api_key():
    provided_key = _extract_tracking_api_key()
    if not provided_key or provided_key != SHARED_API_KEY:
        return _json_error('SESSION_EXPIRED', 'Unauthorized agent sync request.', 401)
    return None


def _allow_shared_agent_key_bootstrap():
    return bool(getattr(Config, 'TRACKING_ALLOW_SHARED_AGENT_KEY_BOOTSTRAP', True))


def _extract_agent_binding_headers():
    key_id = (request.headers.get('X-Agent-Key-Id') or '').strip()
    key_secret = (request.headers.get('X-Agent-Key') or '').strip()
    return key_id, key_secret


def _hash_agent_secret(secret):
    return hashlib.sha256(str(secret or '').encode('utf-8')).hexdigest()


def _verify_agent_secret(secret, expected_hash):
    return hmac.compare_digest(_hash_agent_secret(secret), str(expected_hash or ''))


def _create_agent_key_binding(tracked_device_id):
    key_id = secrets.token_hex(16)
    key_secret = secrets.token_urlsafe(48)
    binding = TrackingAgentKeyBinding(
        key_id=key_id,
        key_hash=_hash_agent_secret(key_secret),
        tracked_device_id=int(tracked_device_id),
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.session.add(binding)
    db.session.flush()
    return binding, key_secret


def _get_active_binding_for_device(device_id):
    return TrackingAgentKeyBinding.query.filter_by(
        tracked_device_id=int(device_id),
        is_active=True,
    ).order_by(TrackingAgentKeyBinding.id.desc()).first()


def _touch_agent_key(binding):
    if not binding:
        return
    binding.last_used_at = datetime.utcnow()
    binding.last_used_ip = request.remote_addr


def _authorize_agent_request(expected_device_id=None, require_bound=False, allow_bootstrap=True):
    key_id, key_secret = _extract_agent_binding_headers()
    if key_id and key_secret:
        binding = TrackingAgentKeyBinding.query.filter_by(key_id=key_id, is_active=True).first()
        if not binding or not _verify_agent_secret(key_secret, binding.key_hash):
            return None, _json_error('INVALID_AGENT_KEY', 'Invalid agent key binding.', 401)
        if expected_device_id is not None and int(binding.tracked_device_id) != int(expected_device_id):
            create_audit_log(
                action='reject',
                entity_type='agent_key_binding',
                entity_id=binding.id,
                entity_name=binding.key_id,
                description=(
                    f'Agent key/device mismatch. expected_device_id={expected_device_id} '
                    f'bound_device_id={binding.tracked_device_id} remote_ip={request.remote_addr}'
                ),
                changes={'agent_key_id': binding.key_id, 'expected_device_id': expected_device_id},
            )
            return None, _json_error('AGENT_KEY_DEVICE_MISMATCH', 'Agent key is not bound to this device.', 403)
        _touch_agent_key(binding)
        return {'binding': binding, 'bootstrap': False}, None

    if require_bound:
        return None, _json_error('AGENT_KEY_REQUIRED', 'Bound agent key is required.', 401)

    if allow_bootstrap and _allow_shared_agent_key_bootstrap():
        auth_error = _require_tracking_api_key()
        if auth_error is None:
            return {'binding': None, 'bootstrap': True}, None
    return None, _json_error('SESSION_EXPIRED', 'Unauthorized agent sync request.', 401)


def _is_admin_session():
    return bool(session.get('logged_in')) and str(session.get('role') or '').strip().lower() == 'admin'


def _coerce_positive_int(value, default_value, min_value=1, max_value=86400):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default_value)
    return max(min_value, min(max_value, parsed))


def _normalize_restricted_domain_list(values):
    if isinstance(values, str):
        candidates = [item.strip() for item in values.replace('\r', '\n').split('\n')]
    elif isinstance(values, list):
        candidates = values
    else:
        candidates = []
    normalized = sorted({domain for domain in (normalize_domain(item) for item in candidates) if domain})
    return normalized


def _upsert_restricted_policy_from_payload(policy, payload):
    blocked_domains = _normalize_restricted_domain_list(payload.get('blocked_domains', []))
    policy.enabled = bool(payload.get('enabled', policy.enabled))
    policy.cooldown_seconds = _coerce_positive_int(payload.get('cooldown_seconds', policy.cooldown_seconds), policy.cooldown_seconds, min_value=60, max_value=86400)
    policy.dns_poll_seconds = _coerce_positive_int(payload.get('dns_poll_seconds', policy.dns_poll_seconds), policy.dns_poll_seconds, min_value=15, max_value=3600)
    policy.window_poll_seconds = _coerce_positive_int(payload.get('window_poll_seconds', policy.window_poll_seconds), policy.window_poll_seconds, min_value=5, max_value=600)
    policy.dns_seen_ttl_seconds = _coerce_positive_int(payload.get('dns_seen_ttl_seconds', policy.dns_seen_ttl_seconds), policy.dns_seen_ttl_seconds, min_value=60, max_value=86400)
    policy.apply_domains(blocked_domains)
    policy.recompute_version()
    return blocked_domains


def _parse_observed_datetime(value):
    return service_parse_observed_datetime(value)


def _match_restricted_domain(observed_domain, blocked_domains):
    return service_match_restricted_domain(observed_domain, blocked_domains)


def _build_restricted_alert_message(domain, source, confidence, hit_count):
    return service_build_restricted_alert_message(domain, source, confidence, hit_count)


def _coerce_restricted_events(raw_value):
    return service_coerce_restricted_events(raw_value)


def _ingest_restricted_site_events_internal(device, events, binding_key_id=None, policy=None, now_utc=None):
    plan = plan_restricted_site_ingest(
        device=device,
        events=events,
        binding_key_id=binding_key_id,
        policy=policy,
        now_utc=now_utc,
    )
    return apply_restricted_site_ingest(plan, fanout_mode='queued').to_dict()


def _ingest_typed_text_alerts(device_id, alerts):
    """Persist typed-text policy alerts from agent.

    Commits per-alert so a concurrent-duplicate IntegrityError on one alert cannot
    cause a batch rollback that silently discards all earlier inserts in the same loop.
    The UniqueConstraint(device_id, evidence_hash, detected_at) prevents duplicates at
    the DB level; the filter_by pre-check is a fast-path optimisation only.
    """
    for alert in alerts:
        evidence_hash = alert.get('evidence_hash')
        detected_at_raw = alert.get('detected_at')
        if not evidence_hash or not detected_at_raw:
            continue
        try:
            if isinstance(detected_at_raw, str):
                detected_at = datetime.fromisoformat(detected_at_raw.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                detected_at = detected_at_raw
        except (ValueError, TypeError):
            continue
        try:
            existing = TypedTextPolicyAlert.query.filter_by(
                device_id=device_id,
                evidence_hash=evidence_hash,
                detected_at=detected_at,
            ).first()
            if not existing:
                db.session.add(TypedTextPolicyAlert(
                    device_id=device_id,
                    pattern_type=alert.get('pattern_type'),
                    severity=alert.get('severity'),
                    evidence_hash=evidence_hash,
                    ai_risk_level=alert.get('ai_risk_level'),
                    ai_category=alert.get('ai_category'),
                    detected_at=detected_at,
                ))
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.debug(
                "Skipped typed_text_alert for device %s (hash=%s): likely duplicate",
                device_id, evidence_hash,
            )


def _ingest_location_samples(tracked_device_id, samples, *, idempotent=False):
    """Persist GPS/location samples and update TrackedDevice cache columns.

    When idempotent=True (relay path) each sample must carry a sample_uuid;
    the insert uses ON CONFLICT (sample_uuid) DO NOTHING so relay redeliveries
    after a visibility-timeout expiry never produce duplicate rows.
    Returns the number of rows actually inserted.
    """
    from models.device_location_log import DeviceLocationLog
    from models.tracked_device import TrackedDevice
    from datetime import datetime as _dt

    def _parse_dt(s):
        if not s:
            return None
        return _dt.fromisoformat(str(s).strip().replace('Z', '+00:00'))
    latest_recorded = None
    latest_entry = None
    inserted = 0
    for sample in samples:
        try:
            lat = float(sample.get('latitude') or 0)
            lng = float(sample.get('longitude') or 0)
        except (TypeError, ValueError):
            continue
        if lat == 0 and lng == 0:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            continue
        recorded_raw = sample.get('recorded_at')
        try:
            recorded_at = _parse_dt(recorded_raw) if recorded_raw else None
        except Exception:
            recorded_at = None
        acc = sample.get('accuracy_meters')
        try:
            acc = float(acc) if acc is not None else None
        except (TypeError, ValueError):
            acc = None
        if acc is not None and acc <= 0:
            acc = None
        sample_uuid = str(sample.get('sample_uuid') or '').strip() or None

        if idempotent and sample_uuid:
            # Skip cheaply without a full INSERT round-trip if already stored.
            exists = db.session.query(
                db.session.query(DeviceLocationLog)
                .filter_by(sample_uuid=sample_uuid)
                .exists()
            ).scalar()
            if exists:
                continue

        entry = DeviceLocationLog(
            tracked_device_id=tracked_device_id,
            sample_uuid=sample_uuid,
            latitude=lat,
            longitude=lng,
            accuracy_meters=acc,
            source=sample.get('source'),
            recorded_at=recorded_at,
        )
        db.session.add(entry)
        inserted += 1
        if recorded_at and (latest_recorded is None or recorded_at > latest_recorded):
            latest_recorded = recorded_at
            latest_entry = entry

    if latest_entry:
        device = TrackedDevice.query.get(tracked_device_id)
        if device:
            device.last_lat = latest_entry.latitude
            device.last_lng = latest_entry.longitude
            device.last_location_seen_at = latest_entry.recorded_at
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.debug("_ingest_location_samples rollback for device %s", tracked_device_id)
        return 0
    return inserted


def _ingest_patch_status(tracked_device_id, patches):
    """Replace patch records per package-manager.

    Managers like Winget/Homebrew/apt only report packages that need updates,
    not their full installed inventory. A pure upsert would leave stale
    'pending' rows forever once a package is patched. The fix: group by manager,
    delete all rows for that (device, manager) pair, then insert fresh. This
    means a patch disappearing from the list (i.e. it was applied) is correctly
    reflected as gone from the DB.
    """
    from sqlalchemy import text as _text
    from collections import defaultdict

    # Group patches by manager so we can replace per-manager atomically
    by_manager = defaultdict(list)
    for patch in patches:
        pkg_name = (patch.get('package_name') or '').strip()
        if not pkg_name:
            continue
        pkg_mgr = (patch.get('package_manager') or '').strip().lower() or None
        by_manager[pkg_mgr].append(patch)

    for pkg_mgr, manager_patches in by_manager.items():
        try:
            # Delete all existing rows for this device+manager pair
            db.session.execute(
                _text("""
                    DELETE FROM device_patch_logs
                    WHERE tracked_device_id = :device_id
                      AND (package_manager = :pkg_mgr OR (:pkg_mgr IS NULL AND package_manager IS NULL))
                """),
                {'device_id': tracked_device_id, 'pkg_mgr': pkg_mgr},
            )
            # Insert fresh snapshot
            for patch in manager_patches:
                pkg_name = (patch.get('package_name') or '').strip()
                db.session.execute(
                    _text("""
                        INSERT INTO device_patch_logs
                            (tracked_device_id, package_manager, package_name,
                             installed_version, available_version, is_pending_update,
                             last_checked_at, created_at, updated_at)
                        VALUES
                            (:device_id, :pkg_mgr, :pkg_name,
                             :installed, :available, :pending,
                             :checked_at, NOW(), NOW())
                    """),
                    {
                        'device_id': tracked_device_id,
                        'pkg_mgr': pkg_mgr,
                        'pkg_name': pkg_name,
                        'installed': patch.get('installed_version'),
                        'available': patch.get('available_version'),
                        'pending': bool(patch.get('is_pending_update', True)),
                        'checked_at': patch.get('last_checked_at'),
                    },
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.debug(
                "_ingest_patch_status rollback device=%s manager=%s", tracked_device_id, pkg_mgr
            )


def _maybe_uplift_confidence(device_id, domain, source, observed_at):
    return service_maybe_uplift_confidence(device_id, domain, source, observed_at)


def _restricted_severity_rank(value):
    normalized = str(value or 'LOW').strip().upper()
    if normalized == 'HIGH':
        return 3
    if normalized == 'MEDIUM':
        return 2
    if normalized == 'LOW':
        return 1
    return 0


def _restricted_severity_from_confidence(confidence):
    normalized = str(confidence or '').strip().upper()
    if normalized == RESTRICTED_CONFIDENCE_HIGH:
        return 'HIGH'
    if normalized in (RESTRICTED_CONFIDENCE_MEDIUM, RESTRICTED_CONFIDENCE_LOW):
        return 'MEDIUM'
    return 'MEDIUM'


def _extract_restricted_confidence(message):
    match = re.search(r'confidence\s*=\s*([a-z]+)', str(message or ''), re.IGNORECASE)
    if not match:
        return None
    return str(match.group(1)).strip().upper()


def _build_active_violation_summary(device_ids):
    normalized_ids = []
    seen = set()
    for raw_id in (device_ids or []):
        try:
            parsed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed_id <= 0 or parsed_id in seen:
            continue
        seen.add(parsed_id)
        normalized_ids.append(parsed_id)

    if not normalized_ids:
        return {}

    states = RestrictedSiteAlertState.query.filter(
        RestrictedSiteAlertState.device_id.in_(normalized_ids),
        RestrictedSiteAlertState.active_dashboard_event_id.isnot(None),
    ).all()
    if not states:
        return {}

    active_event_ids = sorted(
        {
            str(state.active_dashboard_event_id).strip()
            for state in states
            if state.active_dashboard_event_id
        }
    )
    active_events = {}
    if active_event_ids:
        rows = DashboardEvent.query.filter(
            DashboardEvent.event_id.in_(active_event_ids),
            DashboardEvent.resolved.is_(False),
        ).all()
        active_events = {str(row.event_id): row for row in rows}

    summary_map = {}
    for state in states:
        key = str(state.active_dashboard_event_id or '').strip()
        if not key:
            continue
        dashboard_event = active_events.get(key)
        if not dashboard_event:
            continue

        device_summary = summary_map.setdefault(
            int(state.device_id),
            {
                'active_violation_count': 0,
                'highest_violation_severity': 'LOW',
                'latest_violation_timestamp': None,
                '_latest_observed_dt': None,
            },
        )
        device_summary['active_violation_count'] = int(device_summary['active_violation_count'] or 0) + 1

        observed_at = state.last_seen_at or dashboard_event.timestamp
        latest_observed = device_summary.get('_latest_observed_dt')
        if observed_at and (latest_observed is None or observed_at > latest_observed):
            device_summary['_latest_observed_dt'] = observed_at

        severity = _restricted_severity_from_confidence(_extract_restricted_confidence(dashboard_event.message))
        if _restricted_severity_rank(severity) > _restricted_severity_rank(device_summary.get('highest_violation_severity')):
            device_summary['highest_violation_severity'] = severity

    for device_summary in summary_map.values():
        latest_observed_dt = device_summary.pop('_latest_observed_dt', None)
        device_summary['latest_violation_timestamp'] = latest_observed_dt.isoformat() if latest_observed_dt else None

    return summary_map


def _safe_build_active_violation_summary(device_ids):
    try:
        return _build_active_violation_summary(device_ids)
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning("[TrackingPolicy] violation summary unavailable: %s", exc)
        return {}


def _apply_violation_summary(payload, summary):
    data = payload if isinstance(payload, dict) else {}
    summary_data = summary if isinstance(summary, dict) else {}
    active_count = int(summary_data.get('active_violation_count') or 0)
    highest_severity = str(summary_data.get('highest_violation_severity') or 'LOW').strip().upper()
    latest_timestamp = summary_data.get('latest_violation_timestamp')

    if active_count <= 0:
        highest_severity = 'LOW'

    data['active_violation_count'] = active_count
    data['highest_violation_severity'] = highest_severity
    data['latest_violation_timestamp'] = latest_timestamp
    return data


def generate_placeholder_jpeg_bytes(text="No Feed"):
    """Generate placeholder JPEG bytes."""
    img = Image.new('RGB', (640, 480), color=(73, 109, 137))
    d = ImageDraw.Draw(img)
    
    # Try to use a font, fallback to default if not available
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except:
        font = ImageFont.load_default()
    
    # Get text size and center it
    bbox = d.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    position = ((640 - text_width) / 2, (480 - text_height) / 2)
    d.text(position, text, fill=(255, 255, 255), font=font)
    
    # Convert to JPEG bytes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG', quality=80)
    img_byte_arr.seek(0)
    return img_byte_arr.read()


def generate_placeholder_image(text="No Feed"):
    """Generate a multipart MJPEG placeholder image frame."""
    return (
        b'--frame\r\n'
        b'Content-Type: image/jpeg\r\n\r\n' +
        generate_placeholder_jpeg_bytes(text) +
        b'\r\n'
    )


def _extract_first_jpeg_frame(response, max_buffer_bytes=4 * 1024 * 1024):
    """Extract the first JPEG frame from an MJPEG multipart response."""
    buffer = bytearray()

    for chunk in response.iter_content(chunk_size=4096):
        if not chunk:
            continue

        buffer.extend(chunk)
        start = buffer.find(b'\xff\xd8')
        if start == -1:
            if len(buffer) > max_buffer_bytes:
                del buffer[:-2]
            continue

        end = buffer.find(b'\xff\xd9', start + 2)
        if end != -1:
            return bytes(buffer[start:end + 2])

        if start > 0:
            del buffer[:start]
        if len(buffer) > max_buffer_bytes:
            del buffer[max_buffer_bytes:]

    return None

def _wav_header(sample_rate=16000, bits_per_sample=16, channels=1, data_size=0x7FFFFFFF):
    """Create a WAV header for streaming PCM audio."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    riff_size = data_size + 36
    return (
        b'RIFF' +
        riff_size.to_bytes(4, 'little') +
        b'WAVE' +
        b'fmt ' +
        (16).to_bytes(4, 'little') +
        (1).to_bytes(2, 'little') +
        channels.to_bytes(2, 'little') +
        sample_rate.to_bytes(4, 'little') +
        byte_rate.to_bytes(4, 'little') +
        block_align.to_bytes(2, 'little') +
        bits_per_sample.to_bytes(2, 'little') +
        b'data' +
        data_size.to_bytes(4, 'little')
    )

def _ping_host(ip_address, timeout=2.0):
    """
    Perform a simple ICMP ping to check if a host is reachable.
    Returns True if the host responds, False otherwise.
    """
    try:
        if platform.system().lower() == "windows":
            # -n 1: 1 packet
            # -w timeout*1000: timeout in milliseconds
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip_address]
            creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            # Use subprocess.run for cleaner handling in newer Python
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                timeout=timeout + 1.0,
            )
            return result.returncode == 0
        else:
            # -c 1: 1 packet
            # -W timeout: timeout in seconds
            cmd = ["ping", "-c", "1", "-W", str(timeout), ip_address]
            result = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout + 1.0,
            )
            return result.returncode == 0
    except Exception:
        return False

class NetworkScanner:
    def __init__(self):
        self.timeout = 2.0  # Increased to 2.0s for reliability
        try:
            configured_workers = int(os.environ.get('TRACKING_SCAN_MAX_WORKERS', '24') or '24')
        except (TypeError, ValueError):
            configured_workers = 24
        self.max_workers = max(4, min(configured_workers, 64))
        self.require_private_scan_targets = _coerce_bool(
            os.environ.get('TRACKING_SCAN_REQUIRE_PRIVATE'),
            default=True,
        )
        self.require_private_agent_probes = _coerce_bool(
            os.environ.get('TRACKING_AGENT_PROBE_REQUIRE_PRIVATE'),
            default=True,
        )
        self.include_link_local = _coerce_bool(
            os.environ.get('TRACKING_SCAN_INCLUDE_LINK_LOCAL'),
            default=False,
        )
        self.preferred_subnet = (os.environ.get('TRACKING_SCAN_SUBNET') or '').strip() or None
        try:
            self.max_scan_hosts = max(32, int(os.environ.get('TRACKING_SCAN_MAX_HOSTS', '1024') or '1024'))
        except (TypeError, ValueError):
            self.max_scan_hosts = 1024

    def _is_link_local_ip(self, ip_value):
        try:
            return ipaddress.ip_address(str(ip_value)).is_link_local
        except ValueError:
            return False

    def _is_scan_candidate_ip(self, ip_value):
        """Filter out addresses that should not be part of bulk subnet scans."""
        try:
            ip_obj = ipaddress.ip_address(str(ip_value))
        except ValueError:
            return False

        if ip_obj.version != 4:
            return False
        if ip_obj.is_loopback or ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
        if ip_obj.is_link_local and not self.include_link_local:
            return False
        if self.require_private_scan_targets and not ip_obj.is_private:
            return False
        return True

    def _resolve_probe_profile(self, profile):
        if profile == 'interactive':
            return {
                'identity_timeout': max(self.timeout, 2.5),
                'stats_timeout': max(self.timeout, 3.0),
                'health_timeout': max(self.timeout, 2.0),
                'return_offline': True,
            }
        return {
            'identity_timeout': self.timeout,
            'stats_timeout': self.timeout,
            'health_timeout': self.timeout,
            'return_offline': False,
        }

    def _build_probe_result(
        self,
        availability_status,
        tracking_status,
        data=None,
        metrics_available=False,
        probe_error_code=None,
        probe_method=None,
        identity=None,
        agent_port=None,
    ):
        payload = data if isinstance(data, dict) else {}
        identity_payload = identity if isinstance(identity, dict) else None
        return {
            'status': tracking_status,
            'tracking_status': tracking_status,
            'availability_status': availability_status,
            'metrics_available': bool(metrics_available),
            'probe_error_code': probe_error_code,
            'probe_method': probe_method,
            'data': payload,
            'identity': identity_payload,
            'agent_port': agent_port,
            'last_probe_at': datetime.utcnow().isoformat(),
        }
    
    def get_mac_address(self, ip_address):
        """Get MAC address for an IP"""
        try:
            startupinfo = None
            creationflags = 0
            if platform.system().lower() == "windows":
                cmd = ["arp", "-a", ip_address]
                # Stop terminal window from popping up
                if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                     creationflags = subprocess.CREATE_NO_WINDOW
                else:
                     # Fallback for older python or non-standard envs
                     startupinfo = subprocess.STARTUPINFO()
                     startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            else:
                cmd = ["arp", "-n", ip_address]
            
            # Safe subprocess call with suppression flags
            arp_output = subprocess.check_output(
                cmd,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
                timeout=3,
            ).decode('utf-8', errors='ignore')
            
            for line in arp_output.splitlines():
                if ip_address in line:
                    parts = line.split()
                    for part in parts:
                        if ':' in part or '-' in part:
                            return part.upper().replace('-', ':')
        except:
            pass
        return "N/A"
    
    def check_port_open(self, ip, port=None):
        """Check if port is open"""
        try:
            target_port = int(port or preferred_tracking_agent_port(ip))
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((ip, target_port))
            sock.close()
            return result == 0
        except:
            return False
    
    def check_tracking_service(self, ip, port=None, profile='scan'):
        """Check if tracking service is running and classify availability."""
        logger.debug("[TrackingProbe] START ip=%s profile=%s", ip, profile)
        probe_cfg = self._resolve_probe_profile(profile)
        use_discovery_cache = profile == 'scan'
        ip_obj = None
        try:
            ip_obj = ipaddress.ip_address(str(ip))
        except ValueError:
            pass

        if use_discovery_cache:
            cached_probe = get_cached_tracking_probe(ip)
            if isinstance(cached_probe, dict):
                cached_port = cached_probe.get('agent_port')
                if cached_port:
                    remember_tracking_agent_port(ip, cached_port)
                logger.debug("[TrackingProbe] cache_hit ip=%s port=%s", ip, cached_port)
                return cached_probe

        def finalize_probe(result):
            if not isinstance(result, dict):
                return result
            agent_port = result.get('agent_port')
            if agent_port:
                remember_tracking_agent_port(ip, agent_port)
            if use_discovery_cache:
                remember_tracking_probe(ip, result)
            return result

        if self._is_link_local_ip(ip) and not self.include_link_local:
            if probe_cfg.get('return_offline'):
                return finalize_probe(self._build_probe_result(
                    availability_status='offline',
                    tracking_status='offline',
                    data={},
                    metrics_available=False,
                    probe_error_code='AGENT_LINK_LOCAL_SKIPPED',
                    probe_method='none',
                    identity=None,
                ))
            return None
        if ip_obj is not None and self.require_private_agent_probes and not ip_obj.is_private:
            if probe_cfg.get('return_offline'):
                return finalize_probe(self._build_probe_result(
                    availability_status='offline',
                    tracking_status='offline',
                    data={},
                    metrics_available=False,
                    probe_error_code='AGENT_PUBLIC_IP_SKIPPED',
                    probe_method='none',
                    identity=None,
                ))
            return None

        candidate_ports = resolve_tracking_agent_ports(ip_address=ip, explicit_port=port)
        last_probe_error_code = None
        last_identity_data = {}

        for candidate_port in candidate_ports:
            base_url = f"http://{ip}:{candidate_port}"
            identity_data = {}
            probe_error_code = None
            agent_up = True

            try:
                is_scan = (profile == 'scan')

                try:
                    identity_response = _agent_http_get(
                        f"{base_url}/api/identity",
                        timeout=probe_cfg['identity_timeout'],
                        silent=is_scan
                    )
                    if identity_response.status_code == 200:
                        identity_payload = identity_response.json()
                        identity_data = identity_payload if isinstance(identity_payload, dict) else {}
                    else:
                        probe_error_code = f"IDENTITY_HTTP_{identity_response.status_code}"
                except AgentHttpError as error:
                    probe_error_code = error.code
                    agent_up = False
                    if error.code in ('AGENT_UNREACHABLE', 'AGENT_TIMEOUT'):
                        raise error
                except Exception as error:
                    logger.debug("[TrackingProbe] identity parse failure ip=%s port=%s err=%s", ip, candidate_port, error)

                try:
                    stats_response = _agent_http_get(
                        f"{base_url}/api/secure/stats",
                        timeout=probe_cfg['stats_timeout'],
                        headers={'X-API-Key': SHARED_API_KEY},
                        silent=is_scan
                    )
                    if stats_response.status_code == 200:
                        stats_payload = stats_response.json()
                        stats_data = stats_payload if isinstance(stats_payload, dict) else {}
                        if identity_data:
                            device_info = stats_data.get('device_info')
                            if isinstance(device_info, dict):
                                for key, value in identity_data.items():
                                    device_info.setdefault(key, value)
                            else:
                                stats_data['device_info'] = identity_data
                        return finalize_probe(self._build_probe_result(
                            availability_status='online',
                            tracking_status='tracking_active',
                            data=stats_data,
                            metrics_available=True,
                            probe_error_code=None,
                            probe_method='stats',
                            identity=identity_data,
                            agent_port=candidate_port,
                        ))
                    if not probe_error_code:
                        probe_error_code = f"STATS_HTTP_{stats_response.status_code}"
                except AgentHttpError as error:
                    probe_error_code = error.code
                    agent_up = False
                    if error.code in ('AGENT_UNREACHABLE', 'AGENT_TIMEOUT'):
                        raise error
                except Exception as error:
                    logger.debug("[TrackingProbe] stats parse failure ip=%s port=%s err=%s", ip, candidate_port, error)

                if identity_data:
                    return finalize_probe(self._build_probe_result(
                        availability_status='degraded',
                        tracking_status='tracking_active',
                        data={'device_info': identity_data},
                        metrics_available=False,
                        probe_error_code=probe_error_code,
                        probe_method='identity',
                        identity=identity_data,
                        agent_port=candidate_port,
                    ))

                try:
                    health_response = _agent_http_get(
                        f"{base_url}/api/health",
                        timeout=probe_cfg['health_timeout'],
                        silent=is_scan
                    )
                    if health_response.status_code == 200:
                        return finalize_probe(self._build_probe_result(
                            availability_status='degraded',
                            tracking_status='tracking_active',
                            data={'device_info': identity_data} if identity_data else {},
                            metrics_available=False,
                            probe_error_code=probe_error_code,
                            probe_method='health',
                            identity=identity_data,
                            agent_port=candidate_port,
                        ))
                    if not probe_error_code:
                        probe_error_code = f"HEALTH_HTTP_{health_response.status_code}"
                except AgentHttpError as error:
                    probe_error_code = error.code
                    agent_up = False
                    if error.code in ('AGENT_UNREACHABLE', 'AGENT_TIMEOUT'):
                        raise error
                except Exception as error:
                    logger.debug("[TrackingProbe] health parse failure ip=%s port=%s err=%s", ip, candidate_port, error)

                if self.check_port_open(ip, candidate_port):
                    return finalize_probe(self._build_probe_result(
                        availability_status='degraded',
                        tracking_status='port_open_no_service',
                        data={},
                        metrics_available=False,
                        probe_error_code=probe_error_code or 'AGENT_SERVICE_NOT_IDENTIFIED',
                        probe_method='port',
                        identity=identity_data,
                        agent_port=candidate_port,
                    ))

                agent_up = False
            except AgentHttpError as error:
                probe_error_code = error.code
                agent_up = False
            except Exception as error:
                logger.warning("[TrackingProbe] ip=%s port=%s unexpected_error=%s", ip, candidate_port, error)
                probe_error_code = 'AGENT_REQUEST_FAILED'
                agent_up = False

            last_probe_error_code = probe_error_code or last_probe_error_code
            last_identity_data = identity_data or last_identity_data
            if agent_up:
                break

        host_alive = _ping_host(ip, timeout=1.0)
        if host_alive:
            availability_status = 'degraded'
            tracking_status = 'agent_missing_on_host'
            probe_error_code = last_probe_error_code or 'AGENT_UNREACHABLE'
            logger.info("[TrackingProbe] Host %s is UP but Agent is DOWN/MISSING (code=%s)", ip, probe_error_code)
        else:
            availability_status = 'offline'
            tracking_status = 'offline'
            probe_error_code = last_probe_error_code or 'HOST_UNREACHABLE'

        offline_result = finalize_probe(self._build_probe_result(
            availability_status=availability_status,
            tracking_status=tracking_status,
            data={},
            metrics_available=False,
            probe_error_code=probe_error_code,
            probe_method='none',
            identity=last_identity_data,
        ))
        logger.debug("[TrackingProbe] END ip=%s agent_up=False return_offline=%s", ip, probe_cfg.get('return_offline'))
        if probe_cfg.get('return_offline'):
            return offline_result
        return None
    
    def scan_single_ip(self, ip):
        """Scan a single IP"""
        try:
            service_info = self.check_tracking_service(ip)
            if not service_info:
                return None

            tracking_status = service_info.get('tracking_status') or service_info.get('status', 'unknown')
            availability_status = service_info.get('availability_status', 'offline')

            # After a successful HTTP/port check, ARP cache is warm—MAC lookup is more reliable.
            mac = self.get_mac_address(ip)

            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except:
                hostname = "Unknown"

            device_info = {
                'ip': ip,
                'port': service_info.get('agent_port') or preferred_tracking_agent_port(ip),
                'status': tracking_status,
                'availability_status': availability_status,
                'mac_address': mac,
                'unique_client_id': None,
                'hostname': hostname,
                'system': 'Unknown',
                'tracking_data': service_info.get('data'),
                'metrics_available': bool(service_info.get('metrics_available')),
                'probe_error_code': service_info.get('probe_error_code'),
                'probe_method': service_info.get('probe_method'),
            }

            if tracking_status == 'tracking_active' and service_info.get('data'):
                device_data = service_info['data'].get('device_info', {})
                # Extract identity info from the agent response
                agent_mac = device_data.get('mac_address')
                agent_client_id = device_data.get('unique_client_id')
                
                device_info.update({
                    'hostname': device_data.get('hostname', hostname),
                    'system': device_data.get('system', device_data.get('os', 'Unknown')),
                    'mac_address': agent_mac if agent_mac else mac,
                    'unique_client_id': agent_client_id
                })
            
            return device_info
        except Exception as e:
            logger.error("Error scanning IP %s: %s", ip, e)
            return None
    
    def get_local_network_ranges(self):
        """Get local network range"""
        if self.preferred_subnet:
            try:
                preferred_network = ipaddress.IPv4Network(self.preferred_subnet, strict=False)
                logger.info("[TrackingScan] Using TRACKING_SCAN_SUBNET=%s", preferred_network)
                return str(preferred_network)
            except Exception:
                logger.warning(
                    "[TrackingScan] Invalid TRACKING_SCAN_SUBNET=%s; falling back to interface detection",
                    self.preferred_subnet,
                )

        candidates = []
        try:
            interfaces = psutil.net_if_addrs()
            for interface_name, addrs in interfaces.items():
                for addr in addrs:
                    if addr.family.name == 'AF_INET':
                        ip = (addr.address or '').strip()
                        netmask = (addr.netmask or '').strip()
                        if not ip or not netmask:
                            continue
                        try:
                            ip_obj = ipaddress.ip_address(ip)
                        except ValueError:
                            continue
                        if ip_obj.version != 4:
                            continue
                        if ip_obj.is_loopback or ip_obj.is_unspecified or ip_obj.is_multicast:
                            continue
                        if ip_obj.is_link_local and not self.include_link_local:
                            continue
                        if self.require_private_scan_targets and not ip_obj.is_private:
                            continue
                        try:
                            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
                            # Prefer private, non-link-local interfaces.
                            score = 0
                            if ip_obj.is_link_local:
                                score += 100
                            if not ip_obj.is_private:
                                score += 10
                            candidates.append((score, -int(network.prefixlen), str(network), ip, interface_name))
                        except:
                            continue
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1], item[4]))
                selected = candidates[0]
                selected_network = selected[2]
                logger.info(
                    "[TrackingScan] Selected interface=%s ip=%s network=%s include_link_local=%s",
                    selected[4],
                    selected[3],
                    selected_network,
                    self.include_link_local,
                )
                return selected_network
        except:
            pass
        return "192.168.1.0/24"
    
    def scan_for_trackable_devices(self):
        """Scan network for devices"""
        logger.info("[TrackingScan] Starting scan")
        local_network = self.get_local_network_ranges()
        logger.info("[TrackingScan] Scanning network=%s", local_network)
        
        all_ips = []
        try:
            network = ipaddress.IPv4Network(local_network, strict=False)
            all_ips = [
                str(ip)
                for ip in network.hosts()
                if self._is_scan_candidate_ip(ip)
            ]
            if len(all_ips) > self.max_scan_hosts:
                logger.info(
                    "[TrackingScan] limiting host scan from %s to %s",
                    len(all_ips),
                    self.max_scan_hosts,
                )
                all_ips = all_ips[:self.max_scan_hosts]
        except Exception as e:
            logger.warning("[TrackingScan] failed to generate IP list: %s", summarize_exception(e))

        # ALWAYS ADD LOCALHOST FOR TESTING
        if "127.0.0.1" not in all_ips:
            all_ips.append("127.0.0.1")
        
        # Add typical local IPs if not present
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            if local_ip not in all_ips and self._is_scan_candidate_ip(local_ip):
                all_ips.append(local_ip)
        except Exception:
            pass

        logger.info("[TrackingScan] Candidate hosts=%s first5=%s", len(all_ips), all_ips[:5])
        
        devices_found = []
        # Use simple loop for debugging if needed, but keeping threads for now
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            results = list(executor.map(self.scan_single_ip, all_ips))
            
        # Process DB updates synchronously
        for result in results:
            if result:
                logger.debug("[TrackingScan] found device ip=%s status=%s", result['ip'], result['status'])
                devices_found.append(result)
                
                # --- AUTO-UPDATE IP LOGIC ---
                target_mac = result.get('mac_address')
                target_client_id = result.get('unique_client_id')
                availability_status = result.get('availability_status', 'offline')
                ip = result['ip']
                
                if target_mac and target_mac != "N/A" and availability_status in ('online', 'degraded'):
                    # 1. Try finding by Unique Client ID first (Most robust)
                    device = None
                    if target_client_id:
                        device = TrackedDevice.query.filter_by(unique_client_id=target_client_id).first()
                    
                    # 2. Fallback to MAC address
                    if not device:
                        device = TrackedDevice.query.filter_by(mac_address=target_mac).first()

                    # 3. Update IP if changed
                    if device:
                        try:
                            changed = False
                            if target_client_id and not device.unique_client_id:
                                device.unique_client_id = target_client_id
                                changed = True
                                logger.info("[TrackingScan] auto-repair linked unique_client_id=%s device=%s", target_client_id, device.device_name)

                            if device.ip_address != ip:
                                logger.info(
                                    "[TrackingScan] auto-repair ip change device=%s old_ip=%s new_ip=%s",
                                    device.device_name,
                                    device.ip_address,
                                    ip,
                                )
                                apply_tracked_device_ip_change(
                                    tracked_device=device,
                                    new_ip=ip,
                                    resolved_hostname=(result.get('hostname') or '').strip() or None,
                                    now_utc=datetime.utcnow(),
                                    payload_ip=ip,
                                    payload_candidates=[ip],
                                    transport_remote_ip=None,
                                    transport_forwarded_for=None,
                                    agent_key_id=None,
                                    reason='SCAN_AUTO_REPAIR',
                                    ip_source='scan_auto_repair',
                                    network_signature=None,
                                    update_last_seen=True,
                                    update_updated_at=True,
                                    sync_reason='SCAN_AUTO_REPAIR',
                                )
                                changed = True

                            if changed:
                                db.session.commit()
                        except TrackedDeviceIpSyncError as exc:
                            db.session.rollback()
                            logger.warning(
                                "[TrackingScan] skipped ip sync device=%s reason=%s",
                                getattr(device, 'device_name', None),
                                exc.reason_code,
                            )
                        except Exception:
                            db.session.rollback()
                            logger.exception(
                                "[TrackingScan] failed auto-repair device=%s",
                                getattr(device, 'device_name', None),
                            )
        
        logger.info("[TrackingScan] Completed scan found=%s", len(devices_found))
        return devices_found

    def scan_candidate_ips(self, candidate_ips):
        """Probe a known set of IPs for the tracking agent without sweeping the subnet."""
        normalized_candidates = []
        seen = set()

        for raw_ip in candidate_ips or []:
            ip_text = str(raw_ip or '').strip()
            if not ip_text or ip_text in seen:
                continue
            if not self._is_scan_candidate_ip(ip_text):
                continue
            seen.add(ip_text)
            normalized_candidates.append(ip_text)

        if len(normalized_candidates) > self.max_scan_hosts:
            logger.info(
                "[TrackingScan] limiting known-host probe from %s to %s",
                len(normalized_candidates),
                self.max_scan_hosts,
            )
            normalized_candidates = normalized_candidates[:self.max_scan_hosts]

        logger.info(
            "[TrackingScan] Probing known hosts=%s first5=%s",
            len(normalized_candidates),
            normalized_candidates[:5],
        )

        if not normalized_candidates:
            return []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            results = list(executor.map(self.scan_single_ip, normalized_candidates))

        devices_found = [result for result in results if result]
        logger.info("[TrackingScan] Known-host probe found=%s", len(devices_found))
        return devices_found

# Real-time tracking storage
real_time_data = {}
metrics_refresh_state = {'last_run': 0}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def check_device_status(device):
    """Return service-truth availability status for template rendering."""
    availability_status = str(getattr(device, 'availability_status', '') or '').strip().lower()
    if availability_status in ('online', 'degraded', 'offline'):
        return availability_status
    return "offline"

def device_to_dict(device):
    """Convert device to a JSON-serializable dictionary"""
    if not device:
        return {}
    identity_source = 'legacy_confirmed'
    try:
        if DeviceLinkService.resolve_link_for_tracked_device(int(device.id)) is not None:
            identity_source = 'scanner_inventory'
    except Exception:
        identity_source = 'legacy_confirmed'
    
    return {
        'id': device.id,
        'device_name': device.device_name,
        'employee_name': device.employee_name,
        'hostname': device.hostname,
        'ip_address': device.ip_address,
        'mac_address': device.mac_address,
        'unique_client_id': device.unique_client_id,
        'department': device.department,
        'notes': device.notes,
        'maintenance_mode': device.maintenance_mode,
        'is_archived': bool(getattr(device, 'is_archived', False)),
        'archived_at': device.archived_at.isoformat() if getattr(device, 'archived_at', None) else None,
        'archived_reason': getattr(device, 'archived_reason', None),
        'archived_by': getattr(device, 'archived_by', None),
        'created_at': device.created_at.isoformat() if device.created_at else None,
        'updated_at': device.updated_at.isoformat() if device.updated_at else None,
        'last_seen': device.last_seen.isoformat() if device.last_seen else None,
        'last_agent_sync_at': device.last_agent_sync_at.isoformat() if getattr(device, 'last_agent_sync_at', None) else None,
        'last_agent_sync_ip': getattr(device, 'last_agent_sync_ip', None),
        'status': check_device_status(device),
        'identity_confirmed': True,
        'identity_source': identity_source,
        'last_lat': getattr(device, 'last_lat', None),
        'last_lng': getattr(device, 'last_lng', None),
        'last_location_seen_at': device.last_location_seen_at.isoformat() if getattr(device, 'last_location_seen_at', None) else None,
        'agent_version': getattr(device, 'agent_version', None),
        'last_policy_sync_at': device.last_policy_sync_at.isoformat() if getattr(device, 'last_policy_sync_at', None) else None,
    }


def _build_tracking_scan_candidates():
    """Return tracking scan candidates from known online inventory and tracked endpoints."""
    _cutoff_48h = datetime.utcnow() - timedelta(hours=48)
    latest_scan_subq = (
        db.session.query(
            DeviceScanHistory.device_ip.label('device_ip'),
            db.func.max(DeviceScanHistory.scan_id).label('max_scan_id'),
        )
        .filter(DeviceScanHistory.scan_timestamp >= _cutoff_48h)
        .group_by(DeviceScanHistory.device_ip)
        .subquery()
    )

    inventory_rows = (
        db.session.query(Device.device_ip)
        .join(latest_scan_subq, Device.device_ip == latest_scan_subq.c.device_ip)
        .join(DeviceScanHistory, DeviceScanHistory.scan_id == latest_scan_subq.c.max_scan_id)
        .filter(
            Device.is_active.is_(True),
            Device.device_ip.isnot(None),
            Device.device_ip != '',
            db.func.lower(DeviceScanHistory.status) == 'online',
        )
        .all()
    )

    inventory_ips = {
        str(row.device_ip).strip()
        for row in inventory_rows
        if getattr(row, 'device_ip', None)
    }

    tracked_rows = (
        scoped_tracked_device_query(
            include_archived=False,
            include_unscoped_for_admin=True,
        )
        .with_entities(TrackedDevice.ip_address)
        .filter(TrackedDevice.ip_address.isnot(None), TrackedDevice.ip_address != '')
        .all()
    )
    tracked_ips = {
        str(row.ip_address).strip()
        for row in tracked_rows
        if getattr(row, 'ip_address', None)
    }

    candidates = sorted(inventory_ips | tracked_ips)
    return {
        'candidate_ips': candidates,
        'inventory_hosts': len(inventory_ips),
        'tracked_hosts': len(tracked_ips),
        'candidate_hosts': len(candidates),
    }


def _format_hm_duration(total_seconds):
    try:
        seconds = max(0, int(float(total_seconds)))
    except (TypeError, ValueError):
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _attach_daily_uptime_payload(device_payload, device_id, now_utc=None):
    payload = dict(device_payload or {})
    try:
        summary = calculate_daily_uptime_snapshot(int(device_id), now_utc=now_utc)
    except Exception:
        reference = now_utc or datetime.utcnow()
        if isinstance(reference, datetime) and reference.tzinfo:
            reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
        day_start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed_seconds = max(0, int((reference - day_start).total_seconds()))
        heartbeat_seconds = max(60, int(getattr(Config, 'TRACKING_HEARTBEAT_INTERVAL_SECONDS', 300) or 300))
        expected_heartbeats = (
            int((elapsed_seconds + heartbeat_seconds - 1) // heartbeat_seconds)
            if elapsed_seconds > 0
            else 0
        )
        summary = {
            'uptime_percent': 0.0,
            'online_seconds': 0,
            'downtime_seconds': elapsed_seconds,
            'elapsed_seconds': elapsed_seconds,
            'heartbeat_interval_seconds': heartbeat_seconds,
            'received_heartbeats': 0,
            'expected_heartbeats': expected_heartbeats,
            'sample_coverage_percent': 0.0 if expected_heartbeats > 0 else None,
            'window_start': day_start.isoformat(),
            'window_end': reference.isoformat(),
        }

    uptime_percent = summary.get('uptime_percent')
    online_seconds = summary.get('online_seconds')
    downtime_seconds = summary.get('downtime_seconds')

    uptime_display = "N/A"
    try:
        if uptime_percent is not None:
            uptime_display = f"{float(uptime_percent):.1f}%"
    except (TypeError, ValueError):
        uptime_display = "N/A"

    payload['daily_uptime'] = {
        'uptime_percent': uptime_percent,
        'online_seconds': online_seconds,
        'downtime_seconds': downtime_seconds,
        'elapsed_seconds': summary.get('elapsed_seconds'),
        'heartbeat_interval_seconds': summary.get('heartbeat_interval_seconds'),
        'received_heartbeats': summary.get('received_heartbeats'),
        'expected_heartbeats': summary.get('expected_heartbeats'),
        'sample_coverage_percent': summary.get('sample_coverage_percent'),
        'window_start': summary.get('window_start'),
        'window_end': summary.get('window_end'),
        'uptime_display': uptime_display,
        'online_display': _format_hm_duration(online_seconds),
        'downtime_display': _format_hm_duration(downtime_seconds),
    }
    return payload


def _extract_identity_from_service_info(service_info):
    if not isinstance(service_info, dict):
        return {}
    data = service_info.get('data') if isinstance(service_info.get('data'), dict) else {}
    device_info = data.get('device_info') if isinstance(data.get('device_info'), dict) else {}
    identity = service_info.get('identity') if isinstance(service_info.get('identity'), dict) else {}

    raw_mac = (
        device_info.get('mac_address')
        or identity.get('mac_address')
        or data.get('mac_address')
    )
    return {
        'mac_address': normalize_mac(raw_mac),
        'hostname': (
            device_info.get('hostname')
            or identity.get('hostname')
            or data.get('hostname')
        ),
        'unique_client_id': (
            device_info.get('unique_client_id')
            or identity.get('unique_client_id')
            or data.get('unique_client_id')
        ),
    }


def _find_tracked_device(mac_address=None, unique_client_id=None):
    if unique_client_id:
        existing = TrackedDevice.query.filter_by(unique_client_id=unique_client_id).first()
        if existing:
            return existing
    if mac_address:
        return TrackedDevice.query.filter_by(mac_address=mac_address).first()
    return None


def _identity_source_map_for_tracked_devices(device_ids):
    normalized_ids = sorted({int(device_id) for device_id in (device_ids or []) if device_id})
    if not normalized_ids:
        return {}

    linked_ids = {
        int(tracked_device_id)
        for tracked_device_id, in db.session.query(DeviceIdentityLink.tracked_device_id)
        .filter(
            DeviceIdentityLink.is_active.is_(True),
            DeviceIdentityLink.tracked_device_id.in_(normalized_ids),
        )
        .all()
    }
    return {
        device_id: ('scanner_inventory' if device_id in linked_ids else 'legacy_confirmed')
        for device_id in normalized_ids
    }


def _upsert_scanner_inventory_link(*, inventory_device_id, tracked_device_id, normalized_mac, resolution_reason):
    if inventory_device_id is None or tracked_device_id is None or not normalized_mac:
        return None

    now_utc = datetime.utcnow()
    DeviceIdentityLink.query.filter(
        DeviceIdentityLink.is_active.is_(True),
        db.or_(
            DeviceIdentityLink.device_id == int(inventory_device_id),
            DeviceIdentityLink.tracked_device_id == int(tracked_device_id),
        ),
    ).update(
        {
            'is_active': False,
            'updated_at': now_utc,
        },
        synchronize_session=False,
    )

    link = DeviceIdentityLink.query.filter_by(
        device_id=int(inventory_device_id),
        tracked_device_id=int(tracked_device_id),
    ).first()
    if link is None:
        link = DeviceIdentityLink(
            device_id=int(inventory_device_id),
            tracked_device_id=int(tracked_device_id),
            normalized_mac=normalized_mac,
            link_source='scanner_inventory_scan',
            confidence=100,
            is_active=True,
            resolved_by=str(session.get('username') or 'system'),
            resolution_reason=resolution_reason,
        )
        db.session.add(link)
        return link

    link.normalized_mac = normalized_mac
    link.link_source = 'scanner_inventory_scan'
    link.confidence = max(int(link.confidence or 0), 100)
    link.is_active = True
    link.resolved_by = str(session.get('username') or 'system')
    link.resolution_reason = resolution_reason
    link.updated_at = now_utc
    return link


def _cleanup_stale_tracked_devices(days=30, dry_run=False, limit=200):
    cutoff = datetime.utcnow() - timedelta(days=max(1, int(days or 30)))
    scoped_query = scoped_tracked_device_query(
        include_archived=False,
        include_unscoped_for_admin=True,
    )
    stale_query = scoped_query.filter(
        db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None)),
        db.func.lower(db.func.coalesce(TrackedDevice.availability_status, 'offline')) == 'offline',
        db.or_(
            db.and_(
                TrackedDevice.last_agent_sync_at.isnot(None),
                TrackedDevice.last_agent_sync_at < cutoff,
            ),
            db.and_(
                TrackedDevice.last_agent_sync_at.is_(None),
                TrackedDevice.last_seen.isnot(None),
                TrackedDevice.last_seen < cutoff,
                TrackedDevice.created_at.isnot(None),
                TrackedDevice.created_at < cutoff,
            ),
        ),
    ).order_by(TrackedDevice.last_seen.asc().nullsfirst(), TrackedDevice.id.asc())

    stale_devices = stale_query.limit(max(1, min(int(limit or 200), 2000))).all()
    archived = []
    now_utc = datetime.utcnow()

    for device in stale_devices:
        archived.append({
            'device_id': device.id,
            'device_name': device.device_name,
            'mac_address': device.mac_address,
            'ip_address': device.ip_address,
            'last_seen': device.last_seen.isoformat() if device.last_seen else None,
            'last_agent_sync_at': device.last_agent_sync_at.isoformat() if getattr(device, 'last_agent_sync_at', None) else None,
        })
        if dry_run:
            continue
        device.is_archived = True
        device.archived_at = now_utc
        device.archived_reason = f'stale_cleanup_{max(1, int(days or 30))}d'
        device.archived_by = str(session.get('username') or 'system')
        device.updated_at = now_utc

    return {
        'cutoff_utc': cutoff.isoformat(),
        'candidate_count': len(stale_devices),
        'archived_count': 0 if dry_run else len(stale_devices),
        'devices': archived,
    }


def _normalize_sync_ipv4(value, require_private=True):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return None

    if parsed.version != 4:
        return None
    if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified or parsed.is_multicast:
        return None
    if require_private and not parsed.is_private:
        return None
    return str(parsed)


def _parse_payload_ip_candidates(payload, require_private=True):
    raw_candidates = payload.get('ip_candidates') if isinstance(payload, dict) else None
    if not isinstance(raw_candidates, list):
        return []
    normalized = []
    seen = set()
    for candidate in raw_candidates:
        parsed = _normalize_sync_ipv4(candidate, require_private=require_private)
        if not parsed or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def _resolve_device_ip_from_payload(payload):
    data = payload if isinstance(payload, dict) else {}
    require_private = bool(getattr(Config, 'TRACKING_AGENT_IP_REQUIRE_PRIVATE', True))
    normalized_payload_ip = _normalize_sync_ipv4(data.get('ip_address'), require_private=require_private)
    normalized_candidates = _parse_payload_ip_candidates(data, require_private=require_private)

    if normalized_candidates:
        if normalized_payload_ip and normalized_payload_ip in normalized_candidates:
            return normalized_payload_ip, 'payload_ip', normalized_payload_ip, normalized_candidates, None
        if len(normalized_candidates) == 1:
            return normalized_candidates[0], 'single_candidate', normalized_payload_ip, normalized_candidates, None
        return None, 'unchanged_unresolved', normalized_payload_ip, normalized_candidates, 'SYNC_IP_UNRESOLVED'

    if normalized_payload_ip:
        return normalized_payload_ip, 'payload_ip', normalized_payload_ip, normalized_candidates, None

    return None, 'unchanged_unresolved', normalized_payload_ip, normalized_candidates, 'SYNC_IP_UNRESOLVED'


def _transport_forwarded_for_header():
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return str(forwarded).strip()
    forwarded = request.headers.get('X-Real-IP')
    if forwarded:
        return str(forwarded).strip()
    return None


def _record_tracked_device_ip_history(
    *,
    device_id,
    old_ip,
    new_ip,
    resolved_ip,
    payload_ip,
    payload_candidates,
    transport_remote_ip,
    transport_forwarded_for,
    agent_key_id,
    reason,
    ip_source,
    network_signature,
):
    if not device_id or str(old_ip or '').strip() == str(new_ip or '').strip():
        return None

    row = TrackedDeviceIpHistory(
        device_id=int(device_id),
        old_ip=(str(old_ip).strip() or None) if old_ip is not None else None,
        new_ip=(str(new_ip).strip() or None) if new_ip is not None else None,
        resolved_ip=(str(resolved_ip).strip() or None) if resolved_ip is not None else None,
        payload_ip=(str(payload_ip).strip() or None) if payload_ip is not None else None,
        payload_candidates_json=json.dumps(payload_candidates or [], ensure_ascii=True),
        transport_remote_ip=(str(transport_remote_ip).strip() or None) if transport_remote_ip is not None else None,
        transport_forwarded_for=(str(transport_forwarded_for).strip() or None) if transport_forwarded_for is not None else None,
        agent_key_id=(str(agent_key_id).strip() or None) if agent_key_id is not None else None,
        reason=(str(reason).strip() or SYNC_IP_REASON_PAYLOAD),
        ip_source=(str(ip_source).strip() or None) if ip_source is not None else None,
        network_signature=(str(network_signature).strip() or None) if network_signature is not None else None,
        changed_at_utc=datetime.utcnow(),
        received_at_utc=datetime.utcnow(),
    )
    db.session.add(row)
    return row

PRODUCTIVE_KEYWORDS = [
    'code', 'studio', 'pycharm', 'intellij', 'eclipse', 'vim', 'emacs',
    'excel', 'word', 'powerpoint', 'slack', 'jira', 'teams', 'outlook',
    'terminal', 'powershell', 'cmd', 'notepad', 'confluence'
]
DISTRACTING_KEYWORDS = [
    'youtube', 'facebook', 'instagram', 'tiktok', 'steam', 'game', 'netflix',
    'spotify', 'twitch', 'reddit'
]

def classify_app(app_name):
    """Classify applications into productive, distracting, or neutral."""
    name = (app_name or '').lower()
    if any(keyword in name for keyword in PRODUCTIVE_KEYWORDS):
        return 'productive'
    if any(keyword in name for keyword in DISTRACTING_KEYWORDS):
        return 'distracting'
    return 'neutral'

def calculate_focus_score(app_logs):
    """Calculate focus score and time breakdown from app logs."""
    productive_time = 0
    distracting_time = 0
    neutral_time = 0

    for log in app_logs:
        duration = log.duration or 0
        category = classify_app(log.application_name)
        if category == 'productive':
            productive_time += duration
        elif category == 'distracting':
            distracting_time += duration
        else:
            neutral_time += duration

    total_time = productive_time + distracting_time + neutral_time
    focus_score = int((productive_time / total_time) * 100) if total_time > 0 else 0

    return focus_score, productive_time, distracting_time, neutral_time, total_time

def calculate_longest_idle_seconds(activity_logs):
    """Find the longest idle duration recorded in activity logs."""
    longest_idle = 0
    for log in activity_logs:
        try:
            details = json.loads(log.details) if log.details else {}
        except Exception:
            details = {}
        idle_seconds = details.get('idle_seconds', 0) or 0
        if idle_seconds > longest_idle:
            longest_idle = idle_seconds
    return longest_idle

def build_work_sessions(activity_logs, idle_threshold=300, gap_threshold=300):
    """Build work session blocks based on activity logs and idle time."""
    sessions = []
    logs_by_device = {}

    for log in activity_logs:
        logs_by_device.setdefault(log.device_id, []).append(log)

    if not logs_by_device:
        return sessions

    device_ids = list(logs_by_device.keys())
    devices = TrackedDevice.query.filter(TrackedDevice.id.in_(device_ids)).all()
    device_lookup = {device.id: device.device_name for device in devices}

    for device_id, logs in logs_by_device.items():
        logs.sort(key=lambda entry: entry.timestamp)
        session_start = None
        last_timestamp = None

        for log in logs:
            try:
                details = json.loads(log.details) if log.details else {}
            except Exception:
                details = {}
            idle_seconds = details.get('idle_seconds', 0) or 0
            is_active = idle_seconds <= idle_threshold

            if not is_active:
                if session_start:
                    end_time = last_timestamp or log.timestamp
                    duration = (end_time - session_start).total_seconds()
                    sessions.append({
                        'device_id': device_id,
                        'device_name': device_lookup.get(device_id, 'Unknown'),
                        'start': session_start.isoformat(),
                        'end': end_time.isoformat(),
                        'duration_seconds': int(duration)
                    })
                    session_start = None
                last_timestamp = log.timestamp
                continue

            if session_start is None:
                session_start = log.timestamp
            elif last_timestamp and (log.timestamp - last_timestamp).total_seconds() > gap_threshold:
                end_time = last_timestamp
                duration = (end_time - session_start).total_seconds()
                sessions.append({
                    'device_id': device_id,
                    'device_name': device_lookup.get(device_id, 'Unknown'),
                    'start': session_start.isoformat(),
                    'end': end_time.isoformat(),
                    'duration_seconds': int(duration)
                })
                session_start = log.timestamp

            last_timestamp = log.timestamp

        if session_start:
            end_time = last_timestamp or session_start
            duration = (end_time - session_start).total_seconds()
            sessions.append({
                'device_id': device_id,
                'device_name': device_lookup.get(device_id, 'Unknown'),
                'start': session_start.isoformat(),
                'end': end_time.isoformat(),
                'duration_seconds': int(duration)
            })

    sessions.sort(key=lambda entry: entry['duration_seconds'], reverse=True)
    return sessions[:20]

def _calc_interval_seconds(log, last_ts_by_device, default_interval=60, max_interval=300):
    """Estimate sample interval per device for converting KB/s to KB."""
    last_ts = last_ts_by_device.get(log.device_id)
    if last_ts:
        delta = (log.timestamp - last_ts).total_seconds()
        if delta <= 0:
            delta = default_interval
    else:
        delta = default_interval
    if delta > max_interval:
        delta = max_interval
    last_ts_by_device[log.device_id] = log.timestamp
    return delta

def log_device_data(device_id, tracking_data):
    """Persist a canonical tracking sample + child logs."""
    try:
        ingest_tracking_sample(
            device_id=device_id,
            payload=tracking_data if isinstance(tracking_data, dict) else {},
            source='probe',
            received_at=datetime.utcnow(),
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error("Error logging device data: %s", e)

def refresh_tracking_snapshot(force=False, min_interval_seconds=15, force_log=False):
    """Refresh device snapshots from agents to keep metrics accurate."""
    if not force:
        return 0

    now = time.time()
    last_run = metrics_refresh_state.get('last_run', 0)
    if now - last_run < min_interval_seconds:
        return 0

    metrics_refresh_state['last_run'] = now
    refreshed = 0

    try:
        devices = TrackedDevice.query.filter(
            db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None))
        ).all()
        if not devices:
            return 0

        scanner = NetworkScanner()
        scanner.timeout = 2.5
        touched_devices = False

        for device in devices:
            if not device.ip_address:
                continue

            service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
            if not service_info:
                continue

            availability_status = service_info.get('availability_status', 'offline')
            if availability_status == 'offline':
                continue

            tracking_data = service_info.get('data') or {}
            has_metrics = (
                tracking_data.get('system_metrics') or
                tracking_data.get('today_stats') or
                tracking_data.get('current_activity')
            )

            # Reachable (online or degraded) updates last_seen for stable list status.
            device.last_seen = datetime.utcnow()
            touched_devices = True
            cache_entry = real_time_data.get(device.mac_address, {})
            last_log_time = cache_entry.get('last_log_time', 0)
            fallback_data = cache_entry.get('data') if isinstance(cache_entry.get('data'), dict) else {}
            metrics_stale = False
            cached_tracking_data = tracking_data
            if not has_metrics and fallback_data:
                cached_tracking_data = fallback_data
                metrics_stale = True

            real_time_data[device.mac_address] = {
                'data': cached_tracking_data,
                'status': availability_status,
                'availability_status': availability_status,
                'device_info': device_to_dict(device),
                'timestamp': time.time(),
                'last_log_time': last_log_time,
                'metrics_available': bool(has_metrics),
                'metrics_stale': metrics_stale,
                'probe_method': service_info.get('probe_method'),
                'probe_error_code': service_info.get('probe_error_code'),
            }

            # Throttled DB logging (force_log allows on-demand freshness)
            if has_metrics and (force_log or time.time() - last_log_time > 60):
                log_device_data(device.id, tracking_data)
                real_time_data[device.mac_address]['last_log_time'] = time.time()

            refreshed += 1

        if refreshed or touched_devices:
            db.session.commit()

    except Exception as exc:
        db.session.rollback()
        logger.warning("Metrics refresh: %s", exc)

    return refreshed

def get_device_statistics(device_id):
    """Get comprehensive statistics for device"""
    try:
        # Get today's date
        today = datetime.utcnow().date()
        
        # Activity statistics
        activity_logs = DeviceActivityLog.query.filter(
            DeviceActivityLog.device_id == device_id,
            db.func.date(DeviceActivityLog.timestamp) == today
        ).all()
        
        # Resource statistics
        resource_logs = DeviceResourceLog.query.filter(
            DeviceResourceLog.device_id == device_id,
            db.func.date(DeviceResourceLog.timestamp) == today
        ).all()
        
        # Application statistics
        app_logs = DeviceApplicationLog.query.filter(
            DeviceApplicationLog.device_id == device_id,
            db.func.date(DeviceApplicationLog.timestamp) == today
        ).all()
        
        stats = {
            'total_activity_time': len(activity_logs) * 60,  # Approximate seconds
            'keyboard_events': sum(log.event_count for log in activity_logs if 'keyboard' in log.activity_type),
            'mouse_events': sum(log.event_count for log in activity_logs if 'mouse' in log.activity_type),
            'unique_applications': len(set(log.application_name for log in app_logs)),
            'avg_cpu_usage': np.mean([log.cpu_usage for log in resource_logs if log.cpu_usage]) if resource_logs else 0,
            'avg_memory_usage': np.mean([log.memory_usage for log in resource_logs if log.memory_usage]) if resource_logs else 0,
        }
        
        return stats
        
    except Exception as e:
        logger.error("Error getting device statistics: %s", e)
        return {}

# ============================================================
# CONTEXT PROCESSOR
# ============================================================

@tracking_bp.context_processor
def utility_processor():
    """Make helper functions available in templates"""
    return dict(check_device_status=check_device_status)

# ============================================================
# ROUTES
# ============================================================

@tracking_bp.route('/tracking')
@require_login
def device_tracking():
    """Main device tracking page"""
    # Template values are best-effort; JS refresh from /api/tracking/live-summary is source-of-truth.
    saved_devices = scoped_tracked_device_query(
        include_archived=False,
        include_unscoped_for_admin=True,
    ).order_by(TrackedDevice.device_name).all()

    online_count = 0
    degraded_count = 0
    offline_count = 0
    for device in saved_devices:
        status = str(device.availability_status or 'offline').strip().lower()
        if status == 'online':
            online_count += 1
        elif status == 'degraded':
            degraded_count += 1
        else:
            offline_count += 1

    # 24h Activity aggregate query scoped to visible devices.
    yesterday = datetime.utcnow() - timedelta(hours=24)
    visible_device_ids = [device.id for device in saved_devices]
    if visible_device_ids:
        last_24h_activity = (
            db.session.query(db.func.count(db.distinct(DeviceActivityLog.device_id)))
            .filter(
                DeviceActivityLog.device_id.in_(visible_device_ids),
                DeviceActivityLog.timestamp >= yesterday,
            )
            .scalar()
            or 0
        )
    else:
        last_24h_activity = 0

    reachable_count = online_count + degraded_count
    checkin_window_seconds = max(30, int(getattr(Config, 'TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180) or 180))
    agent_cutoff = datetime.utcnow() - timedelta(seconds=checkin_window_seconds)
    active_agent_checkins = sum(
        1
        for device in saved_devices
        if getattr(device, 'last_agent_sync_at', None) and device.last_agent_sync_at >= agent_cutoff
    )
    identity_sources = _identity_source_map_for_tracked_devices([device.id for device in saved_devices])
    return render_template('tracking/device_tracking.html', 
                         saved_devices=saved_devices,
                         identity_sources=identity_sources,
                         eligible_inventory_devices=[],
                         online_count=online_count,
                         degraded_count=degraded_count,
                         reachable_count=reachable_count,
                         offline_count=offline_count,
                         active_count=reachable_count,
                         last_24h_activity=last_24h_activity,
                         active_agent_checkins=active_agent_checkins,
                         agent_sync_window_seconds=checkin_window_seconds,
                         workstation_ui_v2=bool(getattr(Config, 'TRACKING_WORKSTATION_UI_V2', False)))


@tracking_bp.route('/api/tracking/eligible-inventory-devices')
@require_login
def api_tracking_eligible_inventory_devices():
    try:
        return jsonify({
            'success': True,
            'devices': _build_tracking_inventory_candidates(),
        })
    except Exception as exc:
        return _json_exception(
            'TRACKING_ELIGIBLE_INVENTORY_FAILED',
            'Failed to load eligible inventory devices.',
            exc,
        )


@tracking_bp.route('/api/tracking/prewarm-eligible-inventory-devices')
@require_login
def api_tracking_prewarm_eligible_inventory_devices():
    try:
        devices = _build_tracking_inventory_candidates(force_refresh=_coerce_bool(request.args.get('force'), False))
        return jsonify({
            'success': True,
            'warmed': True,
            'count': len(devices),
        })
    except Exception as exc:
        return _json_exception(
            'TRACKING_PREWARM_ELIGIBLE_INVENTORY_FAILED',
            'Failed to prewarm eligible inventory devices.',
            exc,
        )

@tracking_bp.route('/tracking/history/<int:device_id>')
@require_permission('tracking.history.view')
def device_history(device_id):
    """Device history page"""
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    days = max(1, min(int(request.args.get('days', 7, type=int) or 7), 30))
    start_date, end_date = parse_history_window_strict(None, None, default_days=days, max_days=30)
    summary = query_history_summary(device_id, start_date, end_date)

    # Resolve linked inventory device_id for config backup endpoints
    link = DeviceIdentityLink.query.filter_by(
        tracked_device_id=device.id, is_active=True
    ).order_by(DeviceIdentityLink.id.desc()).first()
    linked_device_id = link.device_id if link else None
    is_admin = str(session.get('role') or '').strip().lower() == 'admin'

    return render_template('tracking/device_history.html',
                           device=device,
                           days=days,
                           summary=summary,
                           linked_device_id=linked_device_id,
                           is_admin=is_admin)


@tracking_bp.route('/tracking/history')
@require_permission('tracking.history.view')
def device_history_index():
    """Fallback route for history root; redirect to tracking list."""
    return redirect(url_for('tracking_bp.device_tracking'))


@tracking_bp.route('/tracking/workstation/<int:device_id>')
@require_permission('tracking.history.view')
def workstation_monitor(device_id):
    """Deprecated — redirects to device_history which covers the same data."""
    return redirect(url_for('tracking_bp.device_history', device_id=device_id))

# ============================================================
# API ENDPOINTS - REAL TIME TRACKING
# ============================================================

# Global cache for real-time data
real_time_data = {}


def _build_realtime_response_payload(
    *,
    device,
    now_utc,
    tracking_data,
    device_info,
    availability_status,
    metrics_available,
    metrics_stale,
    probe_method,
    probe_error_code,
    data_source,
    probe_failed,
    persisted_fallback_eligible,
    metrics_missing,
    cached=False,
    bootstrap_cache=False,
    sync_recent_fallback=False,
    probe_latency_ms=None,
    error_message=None,
):
    checkin_window_seconds = max(30, int(getattr(Config, 'TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180) or 180))
    stale_minutes = max(1, int(getattr(Config, 'TRACKING_WORKSTATION_STALE_MINUTES', 15) or 15))
    freshness = build_live_freshness(
        device,
        {
            'probe_failed': probe_failed,
            'persisted_fallback_eligible': persisted_fallback_eligible,
            'metrics_missing': metrics_missing,
            'reason_code': probe_error_code,
            'data_source': data_source,
            'probe_latency_ms': probe_latency_ms,
        },
        now_utc,
        checkin_window_seconds,
        stale_minutes,
    )
    controls = build_controls_contract(freshness.get('telemetry_state'), freshness.get('reason_code'))
    payload = {
        'success': freshness.get('telemetry_state') != 'offline-empty',
        'tracking_data': tracking_data,
        'device_info': device_info,
        'timestamp': now_utc.isoformat(),
        'availability_status': availability_status,
        'metrics_available': bool(metrics_available),
        'metrics_stale': bool(metrics_stale),
        'probe': {
            'method': probe_method,
            'error_code': probe_error_code,
        },
        'freshness': freshness,
        'controls': controls,
    }
    if cached:
        payload['cached'] = True
    if bootstrap_cache:
        payload['bootstrap_cache'] = True
    if sync_recent_fallback:
        payload['sync_recent_fallback'] = True
    if freshness.get('telemetry_state') == 'offline-empty':
        payload['error_code'] = probe_error_code or 'AGENT_UNREACHABLE'
        payload['error'] = error_message or 'Device not responding'
    return payload


def _build_cached_realtime_snapshot_response(
    *,
    device,
    mac_address,
    now_utc,
    device_violation_summary,
    prefer_bootstrap_label=False,
):
    fallback_data = {}
    if device.tracking_data:
        fallback_data = _loads_tracking_snapshot(device.tracking_data)

    cached_entry = real_time_data.get(mac_address, {})
    cached_data = _normalize_tracking_snapshot_dict(cached_entry.get('data'))
    if not fallback_data and cached_data:
        fallback_data = cached_data

    if not fallback_data:
        return None

    metrics_available = bool(
        device.metrics_available or
        fallback_data.get('system_metrics') or
        fallback_data.get('today_stats') or
        fallback_data.get('current_activity')
    )

    checkin_window_seconds = max(30, int(getattr(Config, 'TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180) or 180))
    sync_recent = bool(
        device.last_agent_sync_at and
        (now_utc - device.last_agent_sync_at).total_seconds() <= checkin_window_seconds
    )

    fallback_status = str(device.availability_status or '').strip().lower()
    if fallback_status not in ('online', 'degraded', 'offline'):
        fallback_status = 'degraded' if metrics_available else 'offline'

    data_source = 'bootstrap_cache' if (prefer_bootstrap_label and sync_recent) else 'db_snapshot'
    metrics_stale = not metrics_available or not sync_recent
    probe_method = device.probe_method or ('bootstrap-cache' if sync_recent else 'db-snapshot')
    probe_error_code = device.probe_error_code
    device_info_payload = _apply_violation_summary(device_to_dict(device), device_violation_summary)
    device_info_payload = _attach_daily_uptime_payload(device_info_payload, device.id, now_utc=now_utc)
    response_payload = _build_realtime_response_payload(
        device=device,
        now_utc=now_utc,
        tracking_data=fallback_data,
        device_info=device_info_payload,
        availability_status=fallback_status,
        metrics_available=metrics_available,
        metrics_stale=metrics_stale,
        probe_method=probe_method,
        probe_error_code=probe_error_code,
        data_source=data_source,
        probe_failed=False,
        persisted_fallback_eligible=bool(fallback_data),
        metrics_missing=not metrics_available,
        cached=True,
        bootstrap_cache=bool(prefer_bootstrap_label and sync_recent),
        sync_recent_fallback=bool(not sync_recent and fallback_data and fallback_status == 'offline'),
    )

    real_time_data[mac_address] = {
        'data': fallback_data,
        'status': fallback_status,
        'availability_status': fallback_status,
        'device_info': device_info_payload,
        'timestamp': time.time(),
        'probe_error_code': probe_error_code,
        'probe_method': probe_method,
        'metrics_available': metrics_available,
        'metrics_stale': metrics_stale,
        'response_body': response_payload,
        'response_status': 200,
    }
    return response_payload

@tracking_bp.route('/api/tracking/real-time/<mac_address>')
@require_login
def api_real_time_tracking(mac_address):
    """Real-time tracking data for device"""
    try:
        force_refresh = request.args.get('force') == '1'
        prefer_cache = request.args.get('prefer_cache') == '1'

        # Check in-memory cache first (fast path, same process)
        if not force_refresh and mac_address in real_time_data:
            cached = real_time_data[mac_address]
            if time.time() - cached['timestamp'] < 5:
                cached_payload = cached.get('response_body') if isinstance(cached.get('response_body'), dict) else None
                cached_status = int(cached.get('response_status') or 200)
                if cached_payload is not None:
                    return jsonify(cached_payload), cached_status

        # Redis cache fallback — survives server restarts, shared across workers
        _redis_key = f'tracking:realtime:{mac_address}'
        if not force_refresh:
            try:
                from extensions import redis_client, is_redis_available
                if is_redis_available():
                    _cached_raw = redis_client.get(_redis_key)
                    if _cached_raw:
                        _cached_payload = json.loads(_cached_raw)
                        return jsonify(_cached_payload)
            except Exception:
                pass  # Redis unavailable — fall through to live probe

        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
        device_violation_summary = _safe_build_active_violation_summary([device.id]).get(device.id, {})

        if prefer_cache and not force_refresh:
            bootstrap_now_utc = datetime.utcnow()
            response_payload = _build_cached_realtime_snapshot_response(
                device=device,
                mac_address=mac_address,
                now_utc=bootstrap_now_utc,
                device_violation_summary=device_violation_summary,
                prefer_bootstrap_label=True,
            )
            if response_payload is not None:
                return jsonify(response_payload)

        # Get live data from device using interactive probe profile.
        scanner = NetworkScanner()
        scanner.timeout = 2.5
        probe_started = time.perf_counter()
        service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
        probe_latency_ms = round((time.perf_counter() - probe_started) * 1000.0, 2)
        availability_status = service_info.get('availability_status', 'offline') if isinstance(service_info, dict) else 'offline'

        if service_info and availability_status in ('online', 'degraded'):
            raw_tracking_data = _normalize_tracking_snapshot_dict(service_info.get('data') or {})
            has_metrics = bool(
                raw_tracking_data.get('system_metrics') or
                raw_tracking_data.get('today_stats') or
                raw_tracking_data.get('current_activity')
            )

            cached_entry = real_time_data.get(mac_address, {})
            last_log_time = cached_entry.get('last_log_time', 0)
            fallback_data = {}
            if device.tracking_data:
                fallback_data = _loads_tracking_snapshot(device.tracking_data)
            if not fallback_data:
                cached_data = _normalize_tracking_snapshot_dict(cached_entry.get('data'))
                if cached_data:
                    fallback_data = cached_data
            tracking_data = raw_tracking_data
            metrics_stale = not has_metrics
            data_source = 'live_probe'
            if not has_metrics and fallback_data:
                tracking_data = fallback_data
                data_source = 'db_snapshot'

            now_utc = datetime.utcnow()
            should_commit_last_seen = (
                not device.last_seen or
                (now_utc - device.last_seen).total_seconds() >= 30
            )
            device.last_seen = now_utc
            device_info_payload = _apply_violation_summary(device_to_dict(device), device_violation_summary)
            device_info_payload = _attach_daily_uptime_payload(device_info_payload, device.id, now_utc=now_utc)

            real_time_data[mac_address] = {
                'data': tracking_data,
                'status': availability_status,
                'availability_status': availability_status,
                'device_info': device_info_payload,
                'timestamp': time.time(),
                'last_log_time': last_log_time,
                'metrics_available': bool(has_metrics),
                'metrics_stale': metrics_stale,
                'probe_method': service_info.get('probe_method'),
                'probe_error_code': service_info.get('probe_error_code'),
            }

            response_payload = _build_realtime_response_payload(
                device=device,
                now_utc=now_utc,
                tracking_data=tracking_data,
                device_info=device_info_payload,
                availability_status=availability_status,
                metrics_available=bool(has_metrics),
                metrics_stale=metrics_stale,
                probe_method=service_info.get('probe_method'),
                probe_error_code=service_info.get('probe_error_code'),
                data_source=data_source,
                probe_failed=False,
                persisted_fallback_eligible=bool(fallback_data),
                metrics_missing=not has_metrics,
                probe_latency_ms=probe_latency_ms,
            )
            real_time_data[mac_address]['response_body'] = response_payload
            real_time_data[mac_address]['response_status'] = 200

            # Persist to Redis so page reloads serve instant data (anti-flicker)
            if has_metrics:
                try:
                    from extensions import redis_client, is_redis_available
                    if is_redis_available():
                        redis_client.setex(_redis_key, 8, json.dumps(response_payload))
                except Exception:
                    pass  # Best-effort — never block the response

            if has_metrics and time.time() - last_log_time > 60:
                log_device_data(device.id, tracking_data)
                real_time_data[mac_address]['last_log_time'] = time.time()
            elif should_commit_last_seen:
                db.session.commit()

            return jsonify(response_payload)

        probe_error_code = service_info.get('probe_error_code') if isinstance(service_info, dict) else None
        probe_method = service_info.get('probe_method') if isinstance(service_info, dict) else None
        now_utc = datetime.utcnow()
        checkin_window_seconds = max(30, int(getattr(Config, 'TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180) or 180))
        sync_recent = False
        if device.last_agent_sync_at:
            sync_recent = (now_utc - device.last_agent_sync_at).total_seconds() <= checkin_window_seconds

        if sync_recent:
            fallback_data = {}
            if device.tracking_data:
                fallback_data = _loads_tracking_snapshot(device.tracking_data)
            cached_entry = real_time_data.get(mac_address, {})
            cached_data = _normalize_tracking_snapshot_dict(cached_entry.get('data'))
            if not fallback_data and cached_data:
                fallback_data = cached_data

            fallback_status = str(device.availability_status or 'degraded').strip().lower()
            if fallback_status not in ('online', 'degraded'):
                fallback_status = 'degraded'
            metrics_available = bool(
                device.metrics_available or
                fallback_data.get('system_metrics') or
                fallback_data.get('today_stats') or
                fallback_data.get('current_activity')
            )
            device_info_payload = _apply_violation_summary(device_to_dict(device), device_violation_summary)
            device_info_payload = _attach_daily_uptime_payload(device_info_payload, device.id, now_utc=now_utc)
            response_payload = _build_realtime_response_payload(
                device=device,
                now_utc=now_utc,
                tracking_data=fallback_data,
                device_info=device_info_payload,
                availability_status=fallback_status,
                metrics_available=metrics_available,
                metrics_stale=True,
                probe_method=probe_method or 'interactive',
                probe_error_code=probe_error_code or 'AGENT_UNREACHABLE',
                data_source='sync_recent_fallback',
                probe_failed=True,
                persisted_fallback_eligible=True,
                metrics_missing=True,
                sync_recent_fallback=True,
                probe_latency_ms=probe_latency_ms,
            )

            real_time_data[mac_address] = {
                'data': fallback_data,
                'status': fallback_status,
                'availability_status': fallback_status,
                'device_info': device_info_payload,
                'timestamp': time.time(),
                'probe_error_code': probe_error_code or 'AGENT_UNREACHABLE',
                'probe_method': probe_method or 'interactive',
                'metrics_available': metrics_available,
                'metrics_stale': True,
                'response_body': response_payload,
                'response_status': 200,
            }

            return jsonify(response_payload)

        device_info_payload = _apply_violation_summary(device_to_dict(device), device_violation_summary)
        device_info_payload = _attach_daily_uptime_payload(device_info_payload, device.id, now_utc=now_utc)
        response_payload = _build_realtime_response_payload(
            device=device,
            now_utc=now_utc,
            tracking_data={},
            device_info=device_info_payload,
            availability_status='offline',
            metrics_available=False,
            metrics_stale=False,
            probe_method=probe_method,
            probe_error_code=probe_error_code or 'AGENT_UNREACHABLE',
            data_source='none',
            probe_failed=True,
            persisted_fallback_eligible=False,
            metrics_missing=False,
            probe_latency_ms=probe_latency_ms,
            error_message='Device not responding',
        )
        real_time_data[mac_address] = {
            'data': None,
            'status': 'offline',
            'availability_status': 'offline',
            'device_info': device_info_payload,
            'timestamp': time.time(),
            'probe_error_code': probe_error_code or 'AGENT_UNREACHABLE',
            'probe_method': probe_method,
            'metrics_available': False,
            'metrics_stale': False,
            'response_body': response_payload,
            'response_status': 503,
        }

        return jsonify(response_payload), 503
    except Exception as e:
        return _json_exception(
            'REAL_TIME_TRACKING_FAILED',
            'Failed to fetch real-time tracking data.',
            e,
        )

def _history_window_from_request(default_days=7, max_days=30):
    return parse_history_window_strict(
        request.args.get('from'),
        request.args.get('to'),
        default_days=default_days,
        max_days=max_days,
    )


def _workstation_window_from_request(default_days=7):
    max_days = max(1, int(getattr(Config, 'TRACKING_REPORT_MAX_DAYS', 90) or 90))
    return parse_workstation_window(
        request.args.get('from'),
        request.args.get('to'),
        default_days=default_days,
        max_days=max_days,
    )


@tracking_bp.route('/api/tracking/history/<int:device_id>/summary')
@require_permission('tracking.history.view')
def api_history_summary_v2(device_id):
    """V2 summary endpoint for history shell page."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        summary = query_history_summary(device_id, start_date, end_date)
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            'tz': (request.args.get('tz') or 'UTC'),
            'data': summary,
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_SUMMARY_FAILED',
            'Failed to load history summary.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/activity')
@require_permission('tracking.history.view')
def api_history_activity_v2(device_id):
    """V2 activity endpoint with deterministic cursor pagination."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        cursor = request.args.get('cursor')
        rows, next_cursor = query_activity_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=limit,
            cursor=cursor,
        )
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'data': rows,
            'next_cursor': next_cursor,
            'has_more': bool(next_cursor),
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_ACTIVITY_FAILED',
            'Failed to load activity history.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/resources')
@require_permission('tracking.history.view')
def api_history_resources_v2(device_id):
    """V2 resource endpoint with cursor pagination and optional bucket rollups."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        cursor = request.args.get('cursor')
        bucket = (request.args.get('bucket') or 'raw').strip().lower()
        rows, next_cursor = query_resource_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=limit,
            cursor=cursor,
            bucket=bucket,
        )
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'data': rows,
            'bucket': bucket,
            'next_cursor': next_cursor,
            'has_more': bool(next_cursor),
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_RESOURCE_FAILED',
            'Failed to load resource history.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/applications')
@require_permission('tracking.history.view')
def api_history_applications_v2(device_id):
    """V2 application endpoint with cursor pagination and grouped view."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        cursor = request.args.get('cursor')
        group_by = request.args.get('group_by')
        rows, next_cursor = query_application_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=limit,
            cursor=cursor,
            group_by=group_by,
        )
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'data': rows,
            'group_by': group_by or 'raw',
            'next_cursor': next_cursor,
            'has_more': bool(next_cursor),
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_APPLICATION_FAILED',
            'Failed to load application history.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/integrity')
@require_permission('tracking.history.view')
def api_history_integrity_v2(device_id):
    """V2 integrity endpoint showing sample quality records."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        cursor = request.args.get('cursor')
        rows, next_cursor = query_integrity_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=limit,
            cursor=cursor,
        )
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'data': rows,
            'next_cursor': next_cursor,
            'has_more': bool(next_cursor),
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_INTEGRITY_FAILED',
            'Failed to load integrity history.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/dashboard')
@require_permission('tracking.history.view')
def api_history_dashboard(device_id):
    """Polished history dashboard payload with deterministic health metrics."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        payload = query_history_dashboard(device, start_date, end_date)
        envelope = build_history_envelope(request, start_date, end_date)
        return jsonify({
            'success': True,
            'data': payload,
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
            'tz': (request.args.get('tz') or 'UTC'),
            **envelope,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'HISTORY_DASHBOARD_FAILED',
            'Failed to load history dashboard.',
            e,
        )


@tracking_bp.route('/api/tracking/history/<int:device_id>/domains')
@require_permission('tracking.history.view')
def api_history_domains(device_id):
    """Return domain visit history for a tracked device."""
    try:
        from models.device_domain_log import DeviceDomainLog
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 500))
        rows = (
            DeviceDomainLog.query
            .filter(
                DeviceDomainLog.tracked_device_id == device_id,
                DeviceDomainLog.last_seen_at >= start_date,
                DeviceDomainLog.last_seen_at <= end_date,
            )
            .order_by(DeviceDomainLog.last_seen_at.desc())
            .limit(limit)
            .all()
        )
        return jsonify({
            'success': True,
            'data': [r.to_dict() for r in rows],
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception('HISTORY_DOMAINS_FAILED', 'Failed to load domain history.', e)


@tracking_bp.route('/api/tracking/history/<int:device_id>/location')
@require_permission('tracking.history.view')
def api_history_location(device_id):
    """Return GPS/location log for a tracked device."""
    try:
        from models.device_location_log import DeviceLocationLog
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_date, end_date = _history_window_from_request(default_days=7, max_days=30)
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 500))
        rows = (
            DeviceLocationLog.query
            .filter(
                DeviceLocationLog.tracked_device_id == device_id,
                DeviceLocationLog.recorded_at >= start_date,
                DeviceLocationLog.recorded_at <= end_date,
            )
            .order_by(DeviceLocationLog.recorded_at.desc())
            .limit(limit)
            .all()
        )
        return jsonify({
            'success': True,
            'data': [r.to_dict() for r in rows],
            'from': start_date.isoformat(),
            'to': end_date.isoformat(),
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception('HISTORY_LOCATION_FAILED', 'Failed to load location history.', e)


@tracking_bp.route('/api/tracking/history/<int:device_id>/patches')
@require_permission('tracking.history.view')
def api_history_patches(device_id):
    """Return current patch/software inventory for a tracked device."""
    try:
        from models.device_patch_log import DevicePatchLog
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        only_pending = request.args.get('pending_only', 'false').lower() == 'true'
        q = DevicePatchLog.query.filter(DevicePatchLog.tracked_device_id == device_id)
        if only_pending:
            q = q.filter(DevicePatchLog.is_pending_update.is_(True))
        rows = q.order_by(
            DevicePatchLog.is_pending_update.desc(),
            DevicePatchLog.package_manager,
            DevicePatchLog.package_name,
        ).all()
        pending_count = sum(1 for r in rows if r.is_pending_update)
        return jsonify({
            'success': True,
            'data': [r.to_dict() for r in rows],
            'pending_count': pending_count,
            'total_count': len(rows),
        })
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception('HISTORY_PATCHES_FAILED', 'Failed to load patch status.', e)


# ── Patch commands (server → agent update dispatch) ───────────────────────────

@tracking_bp.route('/api/tracking/<int:device_id>/patch-commands', methods=['GET'])
@require_permission('tracking.history.view')
def api_list_patch_commands(device_id):
    """List patch commands for a device."""
    try:
        from models.patch_command import PatchCommand
        get_scoped_tracked_device_or_404(device_id)
        status_filter = request.args.get('status', '').strip().lower() or None
        q = PatchCommand.query.filter_by(tracked_device_id=device_id)
        if status_filter:
            q = q.filter_by(status=status_filter)
        rows = q.order_by(PatchCommand.created_at.desc()).limit(200).all()
        return jsonify({'success': True, 'data': [r.to_dict() for r in rows]})
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception('LIST_PATCH_COMMANDS_FAILED', 'Failed to list patch commands.', e)


@tracking_bp.route('/api/tracking/<int:device_id>/patch-commands', methods=['POST'])
@require_role('admin')
def api_queue_patch_command(device_id):
    """Queue an update command to be delivered to the agent on next sync."""
    try:
        from models.patch_command import PatchCommand, ALLOWED_MANAGERS
        from models.device_patch_log import DevicePatchLog
        from flask_login import current_user

        device = get_scoped_tracked_device_or_404(device_id)
        body = request.get_json(silent=True) or {}
        pkg_mgr = str(body.get('package_manager') or '').strip().lower()
        pkg_name = str(body.get('package_name') or '').strip()
        target_ver = str(body.get('target_version') or '').strip() or None

        if not pkg_mgr or not pkg_name:
            return _json_error('MISSING_FIELDS', 'package_manager and package_name are required.', 400)
        if pkg_mgr not in ALLOWED_MANAGERS:
            return _json_error('INVALID_MANAGER', f'Unsupported package manager: {pkg_mgr}', 400)

        # Verify the package exists in patch_status for this device
        known = DevicePatchLog.query.filter_by(
            tracked_device_id=device_id,
            package_manager=pkg_mgr,
            package_name=pkg_name,
        ).first()
        if not known:
            return _json_error(
                'PACKAGE_NOT_FOUND',
                'Package not found in this device\'s patch inventory. '
                'Wait for the next agent sync before queuing an update.',
                404,
            )

        # Only one queued/sent command per package at a time
        existing = PatchCommand.query.filter(
            PatchCommand.tracked_device_id == device_id,
            PatchCommand.package_manager == pkg_mgr,
            PatchCommand.package_name == pkg_name,
            PatchCommand.status.in_(['queued', 'sent']),
        ).first()
        if existing:
            return _json_error(
                'ALREADY_QUEUED',
                f'A command for {pkg_name} is already {existing.status}.',
                409,
            )

        cmd = PatchCommand(
            tracked_device_id=device_id,
            package_manager=pkg_mgr,
            package_name=pkg_name,
            target_version=target_ver or known.available_version,
            status='queued',
            created_by=getattr(current_user, 'username', None),
        )
        db.session.add(cmd)
        db.session.commit()

        create_audit_log(
            action='patch_command_queued',
            entity_type='tracked_device',
            entity_id=device.id,
            entity_name=device.device_name,
            description=f'Queued {pkg_mgr} update: {pkg_name} → {cmd.target_version}',
            changes={'package_manager': pkg_mgr, 'package_name': pkg_name,
                     'target_version': cmd.target_version},
        )
        return jsonify({'success': True, 'command_id': cmd.id, 'data': cmd.to_dict()}), 201

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        return _json_exception('QUEUE_PATCH_COMMAND_FAILED', 'Failed to queue patch command.', e)


@tracking_bp.route('/api/tracking/<int:device_id>/patch-commands/<int:cmd_id>/cancel', methods=['POST'])
@require_role('admin')
def api_cancel_patch_command(device_id, cmd_id):
    """Cancel a queued command before it is delivered to the agent."""
    try:
        from models.patch_command import PatchCommand
        get_scoped_tracked_device_or_404(device_id)
        cmd = PatchCommand.query.filter_by(id=cmd_id, tracked_device_id=device_id).first()
        if not cmd:
            return _json_error('NOT_FOUND', 'Command not found.', 404)
        if cmd.status not in ('queued',):
            return _json_error('NOT_CANCELLABLE', f'Cannot cancel a command in status "{cmd.status}".', 409)
        cmd.status = 'cancelled'
        db.session.commit()
        return jsonify({'success': True, 'data': cmd.to_dict()})
    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        return _json_exception('CANCEL_PATCH_COMMAND_FAILED', 'Failed to cancel patch command.', e)


@tracking_bp.route('/api/tracking/patch-commands/result', methods=['POST'])
def api_patch_command_result():
    """Agent reports the outcome of an executed patch command.

    Called by the agent after executing a command received in the sync response.
    Auth: shared API key (same as all agent endpoints).
    """
    try:
        from models.patch_command import PatchCommand
        auth_err = _require_tracking_api_key()
        if auth_err:
            return auth_err

        body = request.get_json(silent=True) or {}
        cmd_id = body.get('command_id')
        if not cmd_id:
            return _json_error('MISSING_COMMAND_ID', 'command_id is required.', 400)

        cmd = PatchCommand.query.get(int(cmd_id))
        if not cmd:
            return _json_error('NOT_FOUND', 'Command not found.', 404)
        if cmd.status not in ('sent', 'queued'):
            # Already resolved — idempotent accept
            return jsonify({'success': True})

        success = bool(body.get('success', False))
        cmd.status = 'success' if success else 'failed'
        cmd.result_success = success
        cmd.result_output = str(body.get('output') or '')[:4096]
        cmd.result_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True})

    except HTTPException:
        raise
    except Exception as e:
        db.session.rollback()
        return _json_exception('PATCH_RESULT_FAILED', 'Failed to record patch result.', e)


@tracking_bp.route('/api/tracking/<int:device_id>/log-action', methods=['POST'])
@require_permission('tracking.view')
def api_log_device_action(device_id):
    """Record a client-initiated sensitive action for the audit trail."""
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    body = request.get_json(silent=True) or {}
    action = str(body.get('action') or '').strip().lower()
    _ALLOWED_ACTIONS = {
        'force_sync', 'remote_view', 'isolate', 'restart_agent',
        'capture_snapshot', 'start_surveillance', 'stop_surveillance',
    }
    if action not in _ALLOWED_ACTIONS:
        return jsonify({'error': 'Unknown action'}), 400
    _changes = {'success': body.get('success', True)}
    if body.get('error_detail'):
        _changes['error_detail'] = body['error_detail']
    create_audit_log(
        action=action,
        entity_type='tracked_device',
        entity_id=device.id,
        entity_name=device.device_name,
        description=body.get('description') or f'{action} on {device.device_name}',
        changes=_changes,
    )
    return jsonify({'success': True})


@tracking_bp.route('/api/tracking/<int:device_id>/location-config-status')
@require_permission('tracking.view')
def api_location_config_status(device_id):
    """Return whether on-site / off-site classification is configured."""
    get_scoped_tracked_device_or_404(device_id, include_archived=True)
    from config import Config
    from models.subnet import Subnet
    has_sigs    = bool(getattr(Config, 'PLANT_NETWORK_SIGNATURES', None))
    has_subnets = bool(getattr(Config, 'PLANT_NETWORK_SUBNET_PREFIXES', None))
    has_db_subnets = False
    if not has_sigs and not has_subnets:
        try:
            has_db_subnets = db.session.query(Subnet.id).limit(1).count() > 0
        except Exception:
            pass
    return jsonify({
        'on_site_detection': has_sigs or has_subnets or has_db_subnets,
        'has_signatures':    has_sigs,
        'has_subnets':       has_subnets or has_db_subnets,
    })


@tracking_bp.route('/api/tracking/history/<int:device_id>/run-integrity', methods=['POST'])
@require_permission('tracking.history.view')
def api_history_run_integrity(device_id):
    """Trigger a scoped integrity check cycle from history page action."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        payload = request.get_json(silent=True) or {}
        requested_days = payload.get('days', request.args.get('days', 7))
        try:
            lookback_days = int(requested_days)
        except (TypeError, ValueError):
            lookback_days = 7
        lookback_days = max(1, min(lookback_days, 30))
        result = run_tracking_integrity_checks(lookback_days=lookback_days)
        return jsonify({
            'success': True,
            'message': 'Integrity check completed.',
            'data': result,
        })
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception(
            'HISTORY_RUN_INTEGRITY_FAILED',
            'Failed to run integrity checks.',
            e,
        )


@tracking_bp.route('/api/tracking/workstation/<int:device_id>/overview')
@require_permission('tracking.history.view')
def api_workstation_overview(device_id):
    """Scoped workstation overview API."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        return jsonify({
            'success': True,
            'data': query_workstation_overview(device),
        })
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception(
            'WORKSTATION_OVERVIEW_FAILED',
            'Failed to load workstation overview.',
            e,
        )


@tracking_bp.route('/api/tracking/workstation/<int:device_id>/reports')
@require_permission('tracking.history.view')
def api_workstation_reports(device_id):
    """Scoped workstation report metrics API."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_utc, end_utc = _workstation_window_from_request(default_days=7)
        return jsonify({
            'success': True,
            'from': start_utc.isoformat(),
            'to': end_utc.isoformat(),
            'tz': (request.args.get('tz') or 'UTC'),
            'data': query_workstation_reports(device.id, start_utc, end_utc),
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'WORKSTATION_REPORTS_FAILED',
            'Failed to load workstation reports.',
            e,
        )


@tracking_bp.route('/api/tracking/workstation/<int:device_id>/behavioral-summary')
@require_permission('tracking.history.view')
def api_workstation_behavioral_summary(device_id):
    """
    Behavioral summary for the device live view Activity tab.
    Returns hourly keyboard/mouse/active data, top apps, and recent violations.

    Query params:
      range=24h (default) | 7d
    """
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=False)

        now = datetime.utcnow()
        _raw_range = request.args.get('range', '24h')
        range_param = '7d' if _raw_range == '7d' else '24h'  # normalise before echo
        if range_param == '7d':
            since = now - timedelta(days=7)
        else:
            since = now - timedelta(hours=24)

        # ── Hourly activity (TrackingHourlyRollup) — bounded range ─────────
        hourly_rows = (
            db.session.query(
                TrackingHourlyRollup.bucket_hour,
                db.func.coalesce(TrackingHourlyRollup.keyboard_events, 0).label("kb"),
                db.func.coalesce(TrackingHourlyRollup.mouse_events, 0).label("ms"),
                db.func.coalesce(TrackingHourlyRollup.active_seconds, 0).label("active_s"),
            )
            .filter(
                TrackingHourlyRollup.device_id == device.id,
                TrackingHourlyRollup.bucket_hour >= since,
                TrackingHourlyRollup.bucket_hour <= now,
            )
            .order_by(TrackingHourlyRollup.bucket_hour.asc())
            .all()
        )
        hourly_activity = [
            {
                "hour": r.bucket_hour.strftime("%H:%M"),
                "keyboard": int(r.kb),
                "mouse": int(r.ms),
                "active_s": int(r.active_s),
            }
            for r in hourly_rows
        ]

        # ── Top 5 apps by duration today ────────────────────────────────────
        app_rows = (
            db.session.query(
                DeviceApplicationLog.application_name,
                db.func.coalesce(db.func.sum(DeviceApplicationLog.duration), 0).label("total_s"),
            )
            .filter(
                DeviceApplicationLog.device_id == device.id,
                DeviceApplicationLog.timestamp >= since,
                DeviceApplicationLog.timestamp <= now,
            )
            .group_by(DeviceApplicationLog.application_name)
            .order_by(db.func.sum(DeviceApplicationLog.duration).desc())
            .limit(6)
            .all()
        )
        grand_total = sum(r.total_s for r in app_rows) or 1
        top_5 = app_rows[:5]
        other_s = sum(r.total_s for r in app_rows[5:])
        top_apps = [
            {
                "app": r.application_name,
                "duration_s": int(r.total_s),
                "pct": round(r.total_s / grand_total * 100, 1),
            }
            for r in top_5
        ]
        if other_s > 0:
            top_apps.append({"app": "Other", "duration_s": int(other_s),
                             "pct": round(other_s / grand_total * 100, 1)})

        # ── Recent violations via unique_client_id → agent_key_id ────────────
        recent_violations = []
        uid = getattr(device, "unique_client_id", None)
        if uid:
            viol_rows = (
                RestrictedSiteEvent.query
                .filter(
                    RestrictedSiteEvent.agent_key_id == uid,
                    RestrictedSiteEvent.observed_at_utc >= since,
                    RestrictedSiteEvent.observed_at_utc <= now,
                )
                .order_by(RestrictedSiteEvent.observed_at_utc.desc())
                .limit(10)
                .all()
            )
            recent_violations = [
                {
                    "domain":     v.domain,
                    "confidence": v.confidence,
                    "source":     v.source,
                    "process":    v.process_name,
                    "at":         v.observed_at_utc.isoformat() if v.observed_at_utc else None,
                }
                for v in viol_rows
            ]

        # ── Productivity & Focus scores (from AppCategoryCache + DeviceActivityLog) ─
        productivity_score = None
        focus_score = None
        try:
            from services.enterprise_report_service import _workstation_behavioral_metrics
            from models.app_category_cache import AppCategoryCache
            _cat_cache = {r.app_name: r.category for r in AppCategoryCache.query.all()}
            beh = _workstation_behavioral_metrics(device.id, since, now, _cat_cache)
            productivity_score = beh.get("productivity_score")
            focus_score = beh.get("focus_score")
        except Exception:
            pass

        return jsonify({
            "success": True,
            "device_id": device.id,
            "range": range_param,
            "from": since.isoformat(),
            "to": now.isoformat(),
            "hourly_activity": hourly_activity,
            "top_apps": top_apps,
            "recent_violations": recent_violations,
            "productivity_score": productivity_score,
            "focus_score": focus_score,
        })
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception(
            'BEHAVIORAL_SUMMARY_FAILED',
            'Failed to load behavioral summary.',
            e,
        )


@tracking_bp.route('/api/tracking/workstation/<int:device_id>/availability')
@require_permission('tracking.history.view')
def api_workstation_availability(device_id):
    """Scoped workstation availability timeline API."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_utc, end_utc = _workstation_window_from_request(default_days=7)
        limit = request.args.get('limit', 100, type=int)
        cursor = request.args.get('cursor')
        rows, next_cursor = query_availability_events_page(
            device_id=device.id,
            start_utc=start_utc,
            end_utc=end_utc,
            limit=limit,
            cursor=cursor,
        )
        return jsonify({
            'success': True,
            'from': start_utc.isoformat(),
            'to': end_utc.isoformat(),
            'data': rows,
            'next_cursor': next_cursor,
            'has_more': bool(next_cursor),
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'WORKSTATION_AVAILABILITY_FAILED',
            'Failed to load workstation availability timeline.',
            e,
        )


@tracking_bp.route('/api/tracking/workstation/<int:device_id>/anomalies')
@require_permission('tracking.history.view')
def api_workstation_anomalies(device_id):
    """Scoped workstation anomalies API."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        start_utc, end_utc = _workstation_window_from_request(default_days=7)
        anomalies = query_workstation_anomalies(device, start_utc, end_utc)
        return jsonify({
            'success': True,
            'from': start_utc.isoformat(),
            'to': end_utc.isoformat(),
            'data': anomalies,
        })
    except HTTPException:
        raise
    except ValueError as window_error:
        return _json_error('INVALID_TIME_RANGE', str(window_error), 400)
    except Exception as e:
        return _json_exception(
            'WORKSTATION_ANOMALIES_FAILED',
            'Failed to load workstation anomalies.',
            e,
        )


@tracking_bp.route('/api/tracking/history/activity/<int:device_id>')
@require_permission('tracking.history.view')
def api_activity_history(device_id):
    """Legacy wrapper -> v2 activity endpoint."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)
        end_date = datetime.utcnow()
        rows, _ = query_activity_page(device_id, start_date, end_date, limit=1000, cursor=None)
        rows.reverse()
        response = jsonify({'success': True, 'data': rows})
        response.headers['Warning'] = '299 - "Deprecated endpoint. Use /api/tracking/history/<device_id>/activity"'
        return response
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception('ACTIVITY_HISTORY_FAILED', 'Failed to load activity history.', e)


@tracking_bp.route('/api/tracking/history/resources/<int:device_id>')
@require_permission('tracking.history.view')
def api_resource_history(device_id):
    """Legacy wrapper -> v2 resources endpoint."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        hours = request.args.get('hours', 24, type=int)
        start_date = datetime.utcnow() - timedelta(hours=hours)
        end_date = datetime.utcnow()
        rows, _ = query_resource_page(device_id, start_date, end_date, limit=2000, cursor=None, bucket='raw')
        rows.reverse()
        response = jsonify({'success': True, 'data': rows})
        response.headers['Warning'] = '299 - "Deprecated endpoint. Use /api/tracking/history/<device_id>/resources"'
        return response
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception('RESOURCE_HISTORY_FAILED', 'Failed to load resource history.', e)


@tracking_bp.route('/api/tracking/history/applications/<int:device_id>')
@require_permission('tracking.history.view')
def api_application_history(device_id):
    """Legacy wrapper -> v2 application endpoint."""
    try:
        get_scoped_tracked_device_or_404(device_id, include_archived=True)
        days = request.args.get('days', 7, type=int)
        start_date = datetime.utcnow() - timedelta(days=days)
        end_date = datetime.utcnow()
        grouped, _ = query_application_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=500,
            cursor=None,
            group_by='application',
        )
        raw_rows, _ = query_application_page(
            device_id=device_id,
            start=start_date,
            end=end_date,
            limit=100,
            cursor=None,
            group_by=None,
        )
        legacy_grouped = [
            {
                'name': item.get('application_name'),
                'sessions': item.get('sessions', 0),
                'total_duration': item.get('total_duration', 0),
                'last_used': item.get('last_used'),
            }
            for item in grouped
        ]
        response = jsonify({'success': True, 'data': legacy_grouped, 'raw_data': raw_rows})
        response.headers['Warning'] = '299 - "Deprecated endpoint. Use /api/tracking/history/<device_id>/applications"'
        return response
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception('APPLICATION_HISTORY_FAILED', 'Failed to load application history.', e)



@tracking_bp.route('/api/tracking/stream/screenshot/<mac_address>')
@require_login
def api_stream_screenshot(mac_address):
    """Stream real-time screenshots"""
    # Auth handled by middleware
    single_frame = str(request.args.get('single') or '').strip().lower() in ('1', 'true', 'yes')
    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        if single_frame:
            return Response(
                generate_placeholder_jpeg_bytes("Device Not Found"),
                mimetype='image/jpeg',
                headers={'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache'},
            )
        return Response(
            generate_placeholder_image("Device Not Found"),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )

    if single_frame:
        response = None
        try:
            response = _tracked_agent_request(
                device,
                'GET',
                '/stream',
                timeout=5,
                headers={'X-API-Key': SHARED_API_KEY},
                stream=True,
            )
            if response.status_code != 200:
                return Response(
                    generate_placeholder_jpeg_bytes(f"Error {response.status_code}"),
                    mimetype='image/jpeg',
                    headers={'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache'},
                )

            frame_bytes = _extract_first_jpeg_frame(response)
            if not frame_bytes:
                frame_bytes = generate_placeholder_jpeg_bytes("No Frame")

            return Response(
                frame_bytes,
                mimetype='image/jpeg',
                headers={'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache'},
            )
        except AgentHttpError as e:
            return Response(
                generate_placeholder_jpeg_bytes(e.code),
                mimetype='image/jpeg',
                headers={'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache'},
            )
        except Exception as e:
            logger.error("Screenshot single-frame error for %s: %s", device.ip_address, e)
            return Response(
                generate_placeholder_jpeg_bytes("Stream Error"),
                mimetype='image/jpeg',
                headers={'Cache-Control': 'no-store, max-age=0', 'Pragma': 'no-cache'},
            )
        finally:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
    
    def generate():
        consecutive_errors = 0
        max_errors = 5
        
        while consecutive_errors < max_errors:
            try:
                # Make request with stream=True to get chunks
                response = _tracked_agent_request(
                    device,
                    'GET',
                    '/stream',
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True,
                )
                
                if response.status_code == 200:
                    consecutive_errors = 0  # Reset error counter
                    
                    # Read and forward the multipart stream chunks
                    for chunk in response.iter_content(chunk_size=4096):
                        if chunk:
                            yield chunk
                else:
                    consecutive_errors += 1
                    logger.error("Screenshot stream HTTP error %s for %s", response.status_code, device.ip_address)
                    yield generate_placeholder_image(f"Error {response.status_code}")
                    time.sleep(2)
            except AgentHttpError as e:
                consecutive_errors += 1
                logger.error("Screenshot stream agent error for %s: %s", device.ip_address, e.code)
                yield generate_placeholder_image(e.code)
                time.sleep(2)
                
            except Exception as e:
                consecutive_errors += 1
                logger.error("Screenshot stream error for %s: %s", device.ip_address, e)
                yield generate_placeholder_image("Stream Error")
                time.sleep(2)
        
        yield generate_placeholder_image("Stream Stopped")

    return Response(
        stream_with_context(generate()),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-store, max-age=0', 'X-Accel-Buffering': 'no'},
    )


@tracking_bp.route('/api/tracking/stream/camera/<mac_address>')
@require_login
def api_stream_camera(mac_address):
    """Stream real-time camera feed"""
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return Response(
            generate_placeholder_image("Device Not Found"),
            mimetype='multipart/x-mixed-replace; boundary=frame'
        )

    def generate():
        consecutive_errors = 0
        max_errors = 5

        while consecutive_errors < max_errors:
            try:
                with _tracked_agent_request(
                    device,
                    'GET',
                    '/start_camera',
                    timeout=5,
                    headers={'X-API-Key': SHARED_API_KEY},
                    stream=True,
                ) as response:
                    if response.status_code == 200:
                        consecutive_errors = 0
                        for chunk in response.iter_content(chunk_size=4096):
                            if chunk:
                                yield chunk
                    else:
                        consecutive_errors += 1
                        logger.error("Camera stream HTTP error %s for %s", response.status_code, device.ip_address)
                        yield generate_placeholder_image(f"Camera Error {response.status_code}")
                        time.sleep(2)
            except AgentHttpError as e:
                consecutive_errors += 1
                logger.error("Camera stream agent error for %s: %s", device.ip_address, e.code)
                yield generate_placeholder_image(e.code)
                time.sleep(2)
            except Exception as e:
                consecutive_errors += 1
                logger.error("Camera stream error for %s: %s", device.ip_address, e)
                yield generate_placeholder_image("Camera Error")
                time.sleep(2)

        yield generate_placeholder_image("Camera Stopped")

    return Response(
        stream_with_context(generate()),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-store, max-age=0', 'X-Accel-Buffering': 'no'},
    )


@tracking_bp.route('/api/tracking/stream/audio/<mac_address>')
@require_login
def api_stream_audio(mac_address):
    """Stream real-time audio"""
    # Auth handled by middleware

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
         return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    def generate():
        try:
            # Connect to the device's audio stream
            # stream=True is crucial here
            with _tracked_agent_request(
                device,
                'GET',
                '/audio_stream.wav',
                timeout=5,
                headers={'X-API-Key': SHARED_API_KEY},
                stream=True,
            ) as response:
                
                if response.status_code == 200:
                    # Send WAV header once so browsers can play raw PCM stream.
                    try:
                        sample_rate = int(response.headers.get('X-Audio-Sample-Rate', 16000))
                    except (TypeError, ValueError):
                        sample_rate = 16000
                    try:
                        channels = int(response.headers.get('X-Audio-Channels', 1))
                    except (TypeError, ValueError):
                        channels = 1
                    try:
                        bits_per_sample = int(response.headers.get('X-Audio-Bits', 16))
                    except (TypeError, ValueError):
                        bits_per_sample = 16

                    sample_rate = max(8000, sample_rate)
                    channels = max(1, channels)
                    bits_per_sample = 16 if bits_per_sample not in (8, 16, 24, 32) else bits_per_sample
                    yield _wav_header(
                        sample_rate=sample_rate,
                        bits_per_sample=bits_per_sample,
                        channels=channels,
                    )
                    # Forward chunks
                    # Use a smaller chunk size for audio to reduce latency
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            yield chunk
                else:
                    return
        except AgentHttpError as e:
            logger.error("Audio stream agent error for %s: %s", device.ip_address, e.code)
            return
        except Exception as e:
            logger.error("Audio stream error for %s: %s", device.ip_address, e)
            return

    return Response(
        stream_with_context(generate()),
        mimetype='audio/wav',
        headers={'Cache-Control': 'no-store, max-age=0', 'X-Accel-Buffering': 'no'},
    )


@tracking_bp.route('/api/tracking/toggle-mic/<mac_address>', methods=['POST', 'PATCH'])
@require_role('admin')
def api_toggle_mic(mac_address):
    """Toggle microphone state"""
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

    try:
        status_resp = _tracked_agent_request(
            device,
            'GET',
            '/mic_status',
            timeout=2,
            headers={'X-API-Key': SHARED_API_KEY},
        )

        if status_resp.status_code != 200:
            return _json_error('AGENT_MIC_STATUS_FAILED', 'Failed to check mic status', 502)

        is_active = bool(status_resp.json().get('active', False))
        if is_active:
            action_resp = _tracked_agent_request(
                device,
                'GET',
                '/stop_mic',
                timeout=2,
                headers={'X-API-Key': SHARED_API_KEY},
            )
            if action_resp.status_code != 200:
                return _json_error('AGENT_MIC_STOP_FAILED', 'Failed to stop microphone', 502)
            time.sleep(0.2)
            return jsonify({'success': True, 'action': 'stopped'})

        # Mic startup is handled when /audio_stream.wav is requested by the player.
        return jsonify({'success': True, 'action': 'ready'})

    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        return _json_exception(
            'TOGGLE_MIC_FAILED',
            'Failed to toggle microphone state.',
            e,
        )


@tracking_bp.route('/api/tracking/toggle-camera/<mac_address>', methods=['POST', 'PATCH'])
@require_role('admin')
def api_toggle_camera(mac_address):
    """Toggle camera state"""
    # Auth handled by middleware

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    try:
        response = _tracked_agent_request(
            device,
            'POST',
            '/toggle_camera',
            timeout=5,
            headers={'X-API-Key': SHARED_API_KEY},
        )
        
        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return _json_error('AGENT_CAMERA_TOGGLE_FAILED', f'Device returned {response.status_code}', 502)
    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        return _json_exception(
            'TOGGLE_CAMERA_FAILED',
            'Failed to toggle camera state.',
            e,
        )


@tracking_bp.route('/api/tracking/stop-camera/<mac_address>', methods=['POST'])
@require_role('admin')
def api_stop_camera(mac_address):
    """Stop camera stream on device"""
    
    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device or not device.ip_address:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
    
    try:
        response = _tracked_agent_request(
            device,
            'GET',
            '/stop_camera',
            timeout=3,
            headers={'X-API-Key': SHARED_API_KEY},
        )
        
        if response.status_code == 200:
            return jsonify({"success": True, "message": "Camera stopped"})
        else:
            return _json_error('AGENT_CAMERA_STOP_FAILED', 'Failed to stop camera', 502)
    except AgentHttpError as e:
        return _agent_error_response(e, status=503)
    except Exception as e:
        logger.error("Error stopping camera: %s", e)
        return _json_exception(
            'STOP_CAMERA_FAILED',
            'Failed to stop camera stream.',
            e,
        )




# ============================================================
# DEVICE MANAGEMENT ENDPOINTS
# ============================================================

@tracking_bp.route('/api/tracking/scan', methods=['POST'])
@require_role('admin')
def api_scan_devices():
    """Scan network for devices"""
    if is_reconciliation_locked():
        return _json_error(
            'TRACKING_RECONCILIATION_BUSY',
            'Tracking reconciliation is currently running. Try scan again shortly.',
            409,
        )
    
    try:
        scanner = NetworkScanner()
        candidate_plan = _build_tracking_scan_candidates()
        devices_found = scanner.scan_candidate_ips(candidate_plan['candidate_ips'])

        enhanced_devices = []
        updated_ips = []

        for device in devices_found:
            reported_mac = normalize_mac(device.get('mac_address'))
            status = device.get('status')
            unique_client_id = (device.get('unique_client_id') or '').strip() or None
            scanned_hostname = (device.get('hostname') or '').strip() or None
            identity = resolve_scan_device_identity(device, now_utc=datetime.utcnow())
            authoritative_mac = identity.get('authoritative_mac') or reported_mac
            saved_device = identity.get('matched_device')
            if saved_device:
                if (
                    identity.get('resolved_inventory_device_id')
                    and authoritative_mac
                    and identity.get('authoritative_mac_source') == 'scanner_inventory'
                ):
                    _upsert_scanner_inventory_link(
                        inventory_device_id=identity.get('resolved_inventory_device_id'),
                        tracked_device_id=saved_device.id,
                        normalized_mac=authoritative_mac,
                        resolution_reason=identity.get('resolution_path') or 'inventory_ip_match',
                    )
                if status == 'tracking_active' and getattr(saved_device, 'is_archived', False):
                    saved_device.is_archived = False
                    saved_device.archived_at = None
                    saved_device.archived_reason = None
                    saved_device.archived_by = None
                    saved_device.is_active = True
                if device.get('ip') and device.get('ip') != saved_device.ip_address:
                    old_ip = saved_device.ip_address
                    try:
                        apply_tracked_device_ip_change(
                            tracked_device=saved_device,
                            new_ip=device.get('ip'),
                            resolved_hostname=scanned_hostname,
                            now_utc=datetime.utcnow(),
                            payload_ip=device.get('ip'),
                            payload_candidates=[device.get('ip')],
                            transport_remote_ip=request.remote_addr,
                            transport_forwarded_for=_transport_forwarded_for_header(),
                            agent_key_id=None,
                            reason='DISCOVERY_SYNC',
                            ip_source='device_discovery',
                            network_signature=None,
                            update_last_seen=True,
                            update_updated_at=True,
                            sync_reason='DISCOVERY_SYNC',
                        )
                        updated_ips.append({
                            'device_name': saved_device.device_name,
                            'old_ip': old_ip,
                            'new_ip': device.get('ip')
                        })
                    except TrackedDeviceIpSyncError as exc:
                        db.session.rollback()
                        logger.warning("[TrackingDiscovery] skipped ip sync device=%s reason=%s", saved_device.device_name, exc.reason_code)

                if scanned_hostname and scanned_hostname != saved_device.hostname:
                    saved_device.hostname = scanned_hostname

                if unique_client_id and not saved_device.unique_client_id:
                    saved_device.unique_client_id = unique_client_id

            device_dict = {
                'ip': device.get('ip'),
                'port': device.get('port') or preferred_tracking_agent_port(device.get('ip')),
                'status': status,
                'availability_status': device.get('availability_status', 'offline'),
                'mac_address': authoritative_mac or 'N/A',
                'reported_agent_mac': reported_mac or 'N/A',
                'hostname': device.get('hostname', 'Unknown'),
                'system': device.get('system', 'Unknown'),
                'tracking_data': device.get('tracking_data'),
                'is_saved': bool(saved_device),
                'metrics_available': bool(device.get('metrics_available')),
                'probe_error_code': device.get('probe_error_code'),
                'probe_method': device.get('probe_method'),
                'authoritative_mac': authoritative_mac or 'N/A',
                'authoritative_mac_source': identity.get('authoritative_mac_source') or ('scanner_inventory' if identity.get('resolved_inventory_device_id') else 'agent_payload'),
                'matched_tracked_device_id': identity.get('matched_tracked_device_id'),
                'identity_confirmed': bool(identity.get('identity_confirmed')),
                'resolution_path': identity.get('resolution_path'),
            }

            if saved_device:
                device_dict['saved_info'] = device_to_dict(saved_device)

            enhanced_devices.append(device_dict)

        # Commit all changes (new devices + IP updates)
        db.session.commit()

        tracking_active = [d for d in enhanced_devices if d['status'] == 'tracking_active']
        port_only = [d for d in enhanced_devices if d['status'] == 'port_open_no_service']
        ready_to_add = [
            d for d in enhanced_devices
            if d['status'] == 'tracking_active' and not d['is_saved']
        ]

        return jsonify({
            'success': True,
            'devices_found': tracking_active,
            'total_found': len(tracking_active),
            'scanned_total': len(enhanced_devices),
            'tracking_active': len(tracking_active),
            'port_only': len(port_only),
            'new_devices': len(ready_to_add),
            'auto_saved_devices': [],
            'updated_ips': updated_ips,
            'candidate_hosts': candidate_plan['candidate_hosts'],
            'inventory_hosts': candidate_plan['inventory_hosts'],
            'tracked_hosts': candidate_plan['tracked_hosts'],
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'TRACKING_SCAN_FAILED',
            'Failed to complete tracking network scan.',
            e,
        )

@tracking_bp.route('/api/tracking/save-device', methods=['POST'])
@require_role('admin')
def api_save_device():
    """Save/update device"""
    
    try:
        data = request.json or {}
        inventory_device_id = data.get('inventory_device_id')
        inventory_device = None
        if inventory_device_id not in (None, ''):
            try:
                inventory_device_id = int(inventory_device_id)
            except (TypeError, ValueError):
                return _json_error(
                    'INVALID_INVENTORY_DEVICE_ID',
                    'Inventory device ID must be a valid integer.',
                    400,
                )
            if inventory_device_id <= 0:
                return _json_error(
                    'INVALID_INVENTORY_DEVICE_ID',
                    'Inventory device ID must be greater than zero.',
                    400,
                )
            inventory_device = Device.query.get(inventory_device_id)
            if inventory_device is None:
                return _json_error(
                    'INVENTORY_DEVICE_NOT_FOUND',
                    'Selected inventory device was not found.',
                    404,
                )

        ip_address = (data.get('ip_address') or '').strip() or None
        hostname = (data.get('hostname') or '').strip() or None
        unique_client_id = (data.get('unique_client_id') or '').strip() or None
        raw_mac = data.get('mac_address')
        if not raw_mac and inventory_device is not None:
            raw_mac = inventory_device.macaddress
        raw_mac_text = str(raw_mac or '').strip()
        mac_address = normalize_mac(raw_mac)
        if not ip_address and inventory_device is not None:
            ip_address = (inventory_device.device_ip or '').strip() or None
        if not hostname and inventory_device is not None:
            hostname = (inventory_device.hostname or '').strip() or None

        if raw_mac_text and not mac_address:
            return _json_error(
                'INVALID_MAC_ADDRESS',
                'Invalid MAC address format. Use 00:1A:2B:3C:4D:5E.',
                400,
            )

        if not ip_address and not mac_address:
            return _json_error(
                'DEVICE_IDENTITY_REQUIRED',
                'Provide at least IP address or MAC address to register a device.',
                400,
            )

        # If MAC is missing and IP is provided, resolve identity from service.py endpoint.
        if not mac_address and ip_address:
            scanner = NetworkScanner()
            service_info = scanner.check_tracking_service(ip_address)
            identity = _extract_identity_from_service_info(service_info)
            mac_address = identity.get('mac_address')
            hostname = hostname or identity.get('hostname')
            unique_client_id = unique_client_id or identity.get('unique_client_id')

        if not mac_address:
            return _json_error(
                'IDENTITY_RESOLUTION_FAILED',
                'Could not resolve MAC from service. Ensure service.py is running on the target IP and one of the configured tracking agent ports.',
                400,
            )

        device_name = (
            (data.get('device_name') or '').strip()
            or (getattr(inventory_device, 'device_name', '') or '').strip()
            or hostname
            or f"Device_{mac_address[-5:].replace(':', '')}"
        )
        employee_name = (data.get('employee_name') or '').strip() or None
        department = (data.get('department') or '').strip() or None
        notes = (data.get('notes') or '').strip() or None
        scope_defaults = _scope_defaults_for_new_tracked_device()

        device = _find_tracked_device(mac_address=mac_address, unique_client_id=unique_client_id)
        if device is not None:
            visible_device = scoped_tracked_device_query(
                include_archived=True,
                include_unscoped_for_admin=True,
            ).filter(TrackedDevice.id == device.id).first()
            if not visible_device:
                return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

        if device:
            previous_ip = (device.ip_address or '').strip() or None
            if getattr(device, 'is_archived', False):
                device.is_archived = False
                device.archived_at = None
                device.archived_reason = None
                device.archived_by = None
                device.is_active = True

            if device.mac_address != mac_address:
                mac_collision = TrackedDevice.query.filter(
                    TrackedDevice.mac_address == mac_address,
                    TrackedDevice.id != device.id
                ).first()
                if mac_collision:
                    return _json_error(
                        'MAC_ALREADY_EXISTS',
                        f'MAC {mac_address} is already assigned to another tracked device.',
                        409,
                    )
                device.mac_address = mac_address

            device.device_name = device_name
            device.employee_name = employee_name
            device.hostname = hostname
            device.unique_client_id = unique_client_id or device.unique_client_id
            device.department = department
            device.notes = notes
            if scope_defaults.get('site_id') is not None and device.site_id is None:
                device.site_id = scope_defaults.get('site_id')
            if scope_defaults.get('department_id') is not None and device.department_id is None:
                device.department_id = scope_defaults.get('department_id')
            device.updated_at = datetime.utcnow()
            next_ip = ip_address
            if previous_ip != next_ip and next_ip:
                apply_tracked_device_ip_change(
                    tracked_device=device,
                    new_ip=next_ip,
                    resolved_hostname=hostname,
                    now_utc=datetime.utcnow(),
                    payload_ip=ip_address,
                    payload_candidates=[next_ip],
                    transport_remote_ip=request.remote_addr,
                    transport_forwarded_for=_transport_forwarded_for_header(),
                    agent_key_id=None,
                    reason=SYNC_IP_REASON_MANUAL,
                    ip_source='manual_edit',
                    network_signature=None,
                    update_last_seen=False,
                    update_updated_at=True,
                    sync_reason=SYNC_IP_REASON_MANUAL,
                )
            else:
                device.ip_address = ip_address
        else:
            device = TrackedDevice(
                mac_address=mac_address,
                unique_client_id=unique_client_id,
                device_name=device_name,
                employee_name=employee_name,
                hostname=hostname,
                ip_address=None,
                department=department,
                notes=notes,
                is_archived=False,
                **scope_defaults,
            )
            db.session.add(device)
            db.session.flush()
            next_ip = (device.ip_address or '').strip() or None
            if ip_address:
                apply_tracked_device_ip_change(
                    tracked_device=device,
                    new_ip=ip_address,
                    resolved_hostname=hostname,
                    now_utc=datetime.utcnow(),
                    payload_ip=ip_address,
                    payload_candidates=[ip_address],
                    transport_remote_ip=request.remote_addr,
                    transport_forwarded_for=_transport_forwarded_for_header(),
                    agent_key_id=None,
                    reason=SYNC_IP_REASON_MANUAL,
                    ip_source='manual_edit',
                    network_signature=None,
                    update_last_seen=False,
                    update_updated_at=True,
                    sync_reason=SYNC_IP_REASON_MANUAL,
                )

        db.session.commit()
        _invalidate_tracking_inventory_candidates_cache()
        return jsonify({
            'success': True,
            'message': 'Device saved successfully',
            'device': device_to_dict(device)
        })
    except TrackedDeviceIpSyncError as e:
        db.session.rollback()
        return _json_error(
            'TRACKED_DEVICE_IP_SYNC_FAILED',
            _inventory_sync_error_message(e.reason_code),
            e.status_code,
        )
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'SAVE_DEVICE_FAILED',
            'Failed to save tracked device.',
            e,
        )

@tracking_bp.route('/api/tracking/delete-device', methods=['POST'])
@require_permission('tracking.device.archive')
def api_delete_device():
    """Delete endpoint with archive-by-default behavior and purge support."""
    try:
        payload = request.get_json(silent=True) or {}
        purge_requested = _coerce_bool(payload.get('purge'), False)
        device = None
        device_query = scoped_tracked_device_query(include_archived=True, include_unscoped_for_admin=True)
        device_id_provided = False

        raw_device_id = payload.get('device_id')
        if raw_device_id is not None and str(raw_device_id).strip() != '':
            device_id_provided = True
            try:
                parsed_device_id = int(raw_device_id)
            except (TypeError, ValueError):
                return _json_error('INVALID_DEVICE_ID', 'Device ID must be a valid integer.', 400)
            if parsed_device_id <= 0:
                return _json_error('INVALID_DEVICE_ID', 'Device ID must be greater than zero.', 400)
            device = device_query.filter(TrackedDevice.id == parsed_device_id).first()
            if not device:
                if purge_requested:
                    return jsonify({
                        'success': True,
                        'message': 'Device was already deleted.',
                        'purged': True,
                        'already_deleted': True,
                    })
                return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

        if device is None:
            raw_mac = payload.get('mac_address')
            raw_mac_text = str(raw_mac or '').strip()
            if not raw_mac_text:
                if device_id_provided:
                    return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
                return _json_error(
                    'DEVICE_IDENTITY_REQUIRED',
                    'Provide device_id or mac_address to delete a tracked device.',
                    400,
                )

            mac_address = normalize_mac(raw_mac)
            if not mac_address:
                return _json_error('INVALID_MAC_ADDRESS', 'MAC address format is invalid.', 400)

            # Match legacy rows that may have mixed separator/case in stored MAC.
            device = device_query.filter(
                db.func.replace(db.func.upper(TrackedDevice.mac_address), '-', ':') == mac_address
            ).first()

        if not device:
            if purge_requested:
                return jsonify({
                    'success': True,
                    'message': 'Device was already deleted.',
                    'purged': True,
                    'already_deleted': True,
                })
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

        if purge_requested:
            _purge_tracked_device(device)
            db.session.commit()
            _invalidate_tracking_inventory_candidates_cache()
            return jsonify({
                'success': True,
                'message': 'Device and tracking history permanently purged.',
                'purged': True,
            })

        now_utc = datetime.utcnow()
        if not device.is_archived:
            device.is_archived = True
            device.archived_at = now_utc
            device.archived_reason = (payload.get('reason') or 'manual_archive').strip() if payload.get('reason') else 'manual_archive'
            device.archived_by = str(session.get('username') or 'system')
        device.is_active = False
        device.updated_at = now_utc
        db.session.commit()
        _invalidate_tracking_inventory_candidates_cache()

        return jsonify({
            'success': True,
            'message': 'Device archived successfully.',
            'archived': True,
            'device': device_to_dict(device),
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception('DELETE_DEVICE_FAILED', 'Failed to delete tracked device.', e)


@tracking_bp.route('/api/tracking/device/archive', methods=['POST'])
@require_permission('tracking.device.archive')
def api_archive_device():
    """Explicit archive endpoint (alias of archive-first delete behavior)."""
    return api_delete_device()


@tracking_bp.route('/api/tracking/history/purge/request', methods=['POST'])
@require_permission('tracking.history.purge')
def api_tracking_history_purge_request():
    """Create a short-lived purge confirmation token with deletion preview."""
    if not _can_purge_tracking_history():
        return _json_error(
            'TRACKING_PURGE_FORBIDDEN',
            'Only allowlisted super-admin users can request purge.',
            403,
        )

    payload = request.get_json(silent=True) or {}
    target_device_id = payload.get('device_id')
    if target_device_id is not None:
        try:
            target_device_id = int(target_device_id)
        except (TypeError, ValueError):
            return _json_error('INVALID_DEVICE_ID', 'Device ID must be a valid integer.', 400)

    query = TrackedDevice.query
    if target_device_id:
        query = query.filter(TrackedDevice.id == target_device_id)

    devices = query.all()
    if not devices:
        return _json_error('DEVICE_NOT_FOUND', 'No tracked devices match purge scope.', 404)

    device_ids = [device.id for device in devices]
    preview = {
        'devices': len(device_ids),
        'activity_logs': DeviceActivityLog.query.filter(DeviceActivityLog.device_id.in_(device_ids)).count(),
        'resource_logs': DeviceResourceLog.query.filter(DeviceResourceLog.device_id.in_(device_ids)).count(),
        'application_logs': DeviceApplicationLog.query.filter(DeviceApplicationLog.device_id.in_(device_ids)).count(),
    }
    token_payload = {'device_id': target_device_id, 'preview': preview}
    token, expires_at = _issue_purge_token(token_payload)
    return jsonify({
        'success': True,
        'token': token,
        'expires_at': datetime.utcfromtimestamp(expires_at).isoformat(),
        'preview': preview,
    })


@tracking_bp.route('/api/tracking/history/purge/confirm', methods=['POST'])
@require_permission('tracking.history.purge')
def api_tracking_history_purge_confirm():
    """Confirm and execute permanent purge by token."""
    if not _can_purge_tracking_history():
        return _json_error(
            'TRACKING_PURGE_FORBIDDEN',
            'Only allowlisted super-admin users can confirm purge.',
            403,
        )

    payload = request.get_json(silent=True) or {}
    token = str(payload.get('token') or '').strip()
    if not token:
        return _json_error('PURGE_TOKEN_REQUIRED', 'Purge token is required.', 400)

    token_payload = _consume_purge_token(token)
    if not token_payload:
        return _json_error('PURGE_TOKEN_INVALID', 'Purge token is invalid or expired.', 400)

    target_device_id = token_payload.get('device_id')
    query = TrackedDevice.query
    if target_device_id:
        query = query.filter(TrackedDevice.id == target_device_id)

    devices = query.all()
    if not devices:
        return _json_error('DEVICE_NOT_FOUND', 'No tracked devices match purge scope.', 404)

    purged_devices = 0
    for device in devices:
        _purge_tracked_device(device)
        purged_devices += 1

    db.session.commit()
    return jsonify({
        'success': True,
        'purged_devices': purged_devices,
        'scope': {'device_id': target_device_id},
    })

@tracking_bp.route('/api/tracking/sync-ips', methods=['POST'])
@require_role('admin')
def api_sync_ips():
    """Sync IP addresses for all devices"""
    if is_reconciliation_locked():
        return _json_error(
            'TRACKING_RECONCILIATION_BUSY',
            'Tracking reconciliation is currently running. Try sync again shortly.',
            409,
        )
    
    try:
        scanner = NetworkScanner()
        devices_found = scanner.scan_for_trackable_devices()

        scope_defaults = _scope_defaults_for_new_tracked_device()

        updated_devices = []
        auto_saved_devices = []

        for device in devices_found:
            status = device.get('status')
            unique_client_id = (device.get('unique_client_id') or '').strip() or None
            scanned_hostname = (device.get('hostname') or '').strip() or None
            scanned_ip = device.get('ip')
            identity = resolve_scan_device_identity(device, now_utc=datetime.utcnow())
            authoritative_mac = identity.get('authoritative_mac')
            saved_device = identity.get('matched_device')

            if (
                status == 'tracking_active'
                and authoritative_mac
                and saved_device is None
                and identity.get('authoritative_mac_source') == 'scanner_inventory'
            ):
                new_device = TrackedDevice(
                    mac_address=authoritative_mac,
                    unique_client_id=unique_client_id,
                    device_name=scanned_hostname or f"Device_{authoritative_mac[-5:].replace(':', '')}",
                    employee_name="Auto-Discovered",
                    hostname=scanned_hostname,
                    ip_address=scanned_ip,
                    department="Unassigned",
                    notes="Auto-discovered during sync (scanner-confirmed)",
                    **scope_defaults,
                )
                db.session.add(new_device)
                db.session.flush()
                saved_device = new_device
                auto_saved_devices.append({
                    'device_name': new_device.device_name,
                    'mac_address': authoritative_mac,
                    'ip_address': scanned_ip
                })

            if saved_device:
                if (
                    identity.get('resolved_inventory_device_id')
                    and authoritative_mac
                    and identity.get('authoritative_mac_source') == 'scanner_inventory'
                ):
                    _upsert_scanner_inventory_link(
                        inventory_device_id=identity.get('resolved_inventory_device_id'),
                        tracked_device_id=saved_device.id,
                        normalized_mac=authoritative_mac,
                        resolution_reason=identity.get('resolution_path') or 'inventory_ip_match',
                    )
                if status == 'tracking_active' and getattr(saved_device, 'is_archived', False):
                    saved_device.is_archived = False
                    saved_device.archived_at = None
                    saved_device.archived_reason = None
                    saved_device.archived_by = None
                    saved_device.is_active = True
                if scanned_ip and scanned_ip != saved_device.ip_address:
                    old_ip = saved_device.ip_address
                    try:
                        apply_tracked_device_ip_change(
                            tracked_device=saved_device,
                            new_ip=scanned_ip,
                            resolved_hostname=scanned_hostname,
                            now_utc=datetime.utcnow(),
                            payload_ip=scanned_ip,
                            payload_candidates=[scanned_ip],
                            transport_remote_ip=request.remote_addr,
                            transport_forwarded_for=_transport_forwarded_for_header(),
                            agent_key_id=None,
                            reason='TRACKING_SYNC_IPS',
                            ip_source='tracking_sync_ips',
                            network_signature=None,
                            update_last_seen=True,
                            update_updated_at=True,
                            sync_reason='TRACKING_SYNC_IPS',
                        )
                        updated_devices.append({
                            'device_name': saved_device.device_name,
                            'old_ip': old_ip,
                            'new_ip': scanned_ip
                        })
                    except TrackedDeviceIpSyncError as exc:
                        db.session.rollback()
                        logger.warning("[TrackingSyncIps] skipped ip sync device=%s reason=%s", saved_device.device_name, exc.reason_code)

                if scanned_hostname and scanned_hostname != saved_device.hostname:
                    saved_device.hostname = scanned_hostname

                if unique_client_id and not saved_device.unique_client_id:
                    saved_device.unique_client_id = unique_client_id
        
        if updated_devices or auto_saved_devices:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'updated_devices': updated_devices,
            'auto_saved_devices': auto_saved_devices,
            'message': f'Updated {len(updated_devices)} device(s), auto-saved {len(auto_saved_devices)} new device(s)'
        })
        
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'SYNC_IPS_FAILED',
            'Failed to sync tracked device IPs.',
            e,
        )


@tracking_bp.route('/api/tracking/reconcile', methods=['POST'])
@require_role('admin')
def api_tracking_reconcile():
    """Run tracking reconciliation on demand."""
    payload = request.get_json(silent=True) or {}
    dry_run_raw = payload.get('dry_run', request.args.get('dry_run'))
    force_discovery_raw = payload.get('force_discovery', request.args.get('force_discovery'))

    dry_run = Config.TRACKING_RECONCILE_DRYRUN
    if dry_run_raw is not None:
        dry_run = str(dry_run_raw).strip().lower() in ('1', 'true', 'yes', 'on')

    force_discovery = False
    if force_discovery_raw is not None:
        force_discovery = str(force_discovery_raw).strip().lower() in ('1', 'true', 'yes', 'on')

    report = run_reconciliation(force_discovery=force_discovery, dry_run=dry_run)
    status_code = 200 if report.success else (409 if report.error_code == 'TRACKING_RECONCILIATION_BUSY' else 500)
    return jsonify(report.to_dict()), status_code


@tracking_bp.route('/api/tracking/cleanup-stale-devices', methods=['POST'])
@require_permission('tracking.device.archive')
def api_cleanup_stale_devices():
    payload = request.get_json(silent=True) or {}
    days_raw = payload.get('days', request.args.get('days', 30))
    dry_run = _coerce_bool(payload.get('dry_run', request.args.get('dry_run')), default=False)
    limit_raw = payload.get('limit', request.args.get('limit', 200))

    try:
        days = max(1, min(int(days_raw), 3650))
    except (TypeError, ValueError):
        days = 30
    try:
        limit = max(1, min(int(limit_raw), 2000))
    except (TypeError, ValueError):
        limit = 200

    result = _cleanup_stale_tracked_devices(days=days, dry_run=dry_run, limit=limit)
    if not dry_run and result['archived_count'] > 0:
        db.session.commit()
    elif not dry_run:
        db.session.rollback()

    return jsonify({
        'success': True,
        'dry_run': bool(dry_run),
        'days': days,
        'limit': limit,
        **result,
    })


@tracking_bp.route('/admin/restricted-sites-policy')
@require_role('admin')
def restricted_sites_policy_page():
    return render_template('admin/restricted_sites_policy.html')


@tracking_bp.route('/api/tracking/restricted-sites/policy', methods=['GET'])
@require_login
def api_restricted_sites_policy():
    current_version = str(request.args.get('current_version') or '').strip()
    policy = RestrictedSitePolicy.get_singleton()

    if _is_admin_session():
        if current_version and current_version == policy.policy_version:
            return ('', 304)
        return jsonify({'success': True, 'policy': policy.to_dict(), 'policy_version': policy.policy_version})

    auth_ctx, auth_error = _authorize_agent_request(
        expected_device_id=None,
        require_bound=True,
        allow_bootstrap=False,
    )
    if auth_error:
        return auth_error
    if current_version and current_version == policy.policy_version:
        return ('', 304)

    return jsonify({
        'success': True,
        'policy': policy.to_dict(),
        'policy_version': policy.policy_version,
        'agent_key_id': auth_ctx['binding'].key_id if auth_ctx and auth_ctx.get('binding') else None,
    })


@tracking_bp.route('/api/tracking/restricted-sites/policy', methods=['POST'])
@require_role('admin')
def api_update_restricted_sites_policy():
    payload = request.get_json(silent=True) or {}
    policy = RestrictedSitePolicy.get_singleton()
    before = policy.to_dict()

    blocked_domains = _upsert_restricted_policy_from_payload(policy, payload)
    policy.updated_by = str(session.get('username') or 'admin')
    policy.updated_at = datetime.utcnow()
    enqueue_policy_rebuild_for_all_tracked_devices()
    db.session.commit()

    create_audit_log(
        action='update',
        entity_type='restricted_site_policy',
        entity_id=policy.id,
        entity_name='Restricted Site Policy',
        description='Updated restricted site policy settings.',
        changes={
            'before': before,
            'after': policy.to_dict(),
            'blocked_domains_count': len(blocked_domains),
        },
    )
    return jsonify({'success': True, 'policy': policy.to_dict(), 'policy_version': policy.policy_version})


@tracking_bp.route('/api/admin/restricted-sites-policy', methods=['GET'])
@require_role('admin')
def get_admin_restricted_sites_policy():
    policy = RestrictedSitePolicy.get_singleton()
    return jsonify({
        'success': True,
        'domains': policy.blocked_domains,
        'mode': 'blocking' if policy.enabled else 'monitoring'
    })


@tracking_bp.route('/api/admin/restricted-sites-policy/domains', methods=['POST'])
@require_role('admin')
def add_admin_restricted_sites_policy_domain():
    payload = request.get_json(silent=True) or {}
    domain = str(payload.get('domain') or '').strip().lower()
    if not domain:
        return jsonify({'success': False, 'error': 'Domain required'}), 400
    
    policy = RestrictedSitePolicy.get_singleton()
    before = policy.to_dict()
    current_domains = set(policy.blocked_domains)
    current_domains.add(domain)
    policy.apply_domains(list(current_domains))
    
    policy.updated_by = str(session.get('username') or 'admin')
    policy.updated_at = datetime.utcnow()
    enqueue_policy_rebuild_for_all_tracked_devices()
    db.session.commit()
    
    create_audit_log(
        action='update',
        entity_type='restricted_site_policy',
        entity_id=policy.id,
        entity_name='Restricted Site Policy',
        description=f'Added domain {domain} to global policy.',
        changes={'before': before, 'after': policy.to_dict()}
    )
    return jsonify({'success': True, 'domains': policy.blocked_domains, 'mode': 'blocking' if policy.enabled else 'monitoring'})


@tracking_bp.route('/api/admin/restricted-sites-policy/domains', methods=['DELETE'])
@require_role('admin')
def remove_admin_restricted_sites_policy_domains():
    payload = request.get_json(silent=True) or {}
    domains_to_remove = set(payload.get('domains', []))
    
    policy = RestrictedSitePolicy.get_singleton()
    before = policy.to_dict()
    
    current_domains = set(policy.blocked_domains)
    new_domains = current_domains - domains_to_remove
    policy.apply_domains(list(new_domains))
    
    policy.updated_by = str(session.get('username') or 'admin')
    policy.updated_at = datetime.utcnow()
    enqueue_policy_rebuild_for_all_tracked_devices()
    db.session.commit()
    
    create_audit_log(
        action='update',
        entity_type='restricted_site_policy',
        entity_id=policy.id,
        entity_name='Restricted Site Policy',
        description=f'Removed domains from global policy.',
        changes={'before': before, 'after': policy.to_dict()}
    )
    return jsonify({'success': True, 'domains': policy.blocked_domains, 'mode': 'blocking' if policy.enabled else 'monitoring'})


@tracking_bp.route('/api/admin/restricted-sites-policy/mode', methods=['POST'])
@require_role('admin')
def set_admin_restricted_sites_policy_mode():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get('mode') or '').strip().lower()
    if mode not in ['monitoring', 'blocking']:
        return jsonify({'success': False, 'error': 'Invalid mode'}), 400
        
    policy = RestrictedSitePolicy.get_singleton()
    before = policy.to_dict()
    
    policy.enabled = True if mode == 'blocking' else False
    policy.updated_by = str(session.get('username') or 'admin')
    policy.updated_at = datetime.utcnow()
    enqueue_policy_rebuild_for_all_tracked_devices()
    db.session.commit()
    
    create_audit_log(
        action='update',
        entity_type='restricted_site_policy',
        entity_id=policy.id,
        entity_name='Restricted Site Policy',
        description=f'Updated global policy mode to {mode}.',
        changes={'before': before, 'after': policy.to_dict()}
    )
    return jsonify({'success': True, 'domains': policy.blocked_domains, 'mode': 'blocking' if policy.enabled else 'monitoring'})


@tracking_bp.route('/api/tracking/restricted-sites/events', methods=['POST'])
def api_ingest_restricted_site_events():
    payload = request.get_json(silent=True) or {}
    raw_mac = payload.get('mac_address')
    mac_address = normalize_mac(raw_mac)
    unique_client_id = (payload.get('unique_client_id') or '').strip() or None

    device = _find_tracked_device(mac_address=mac_address, unique_client_id=unique_client_id)
    if not device:
        return _json_error('TRACKED_DEVICE_NOT_FOUND', 'Tracked device not found for restricted-site event ingest.', 404)

    auth_ctx, auth_error = _authorize_agent_request(
        expected_device_id=device.id,
        require_bound=True,
        allow_bootstrap=False,
    )
    if auth_error:
        return auth_error
    binding = auth_ctx.get('binding') if auth_ctx else None

    policy = RestrictedSitePolicy.get_singleton()
    events = _coerce_restricted_events(payload.get('events'))
    if not events:
        return _json_error('EVENTS_REQUIRED', 'events[] payload is required.', 400)
    now_utc = datetime.utcnow()
    ingest_summary = _ingest_restricted_site_events_internal(
        device=device,
        events=events,
        binding_key_id=binding.key_id if binding else None,
        policy=policy,
        now_utc=now_utc,
    )

    db.session.commit()
    return jsonify(
        {
            'success': True,
            **ingest_summary,
            'received_at': now_utc.isoformat(),
        }
    )


@tracking_bp.route('/api/tracking/register', methods=['GET'])
def api_tracking_register():
    """Compatibility registration endpoint for service auto-discovery."""
    _auth_ctx, auth_error = _authorize_agent_request(
        expected_device_id=None,
        require_bound=False,
        allow_bootstrap=True,
    )
    if auth_error:
        return auth_error

    return jsonify({
        'success': True,
        'server_name': 'Device Monitoring Tactical',
        'status': 'active',
        'version': '1.0',
        'timestamp': datetime.utcnow().isoformat(),
    })


@tracking_bp.route('/api/tracking/sync', methods=['POST'])
def api_tracking_sync():
    """Compatibility sync endpoint for service agents."""
    try:
        payload = request.get_json(silent=True) or {}
        raw_mac = payload.get('mac_address')
        raw_mac_text = str(raw_mac or '').strip()
        mac_address = normalize_mac(raw_mac)
        if not raw_mac_text:
            return _json_error('MAC_ADDRESS_REQUIRED', 'MAC address is required for sync.', 400)
        if not mac_address:
            return _json_error('INVALID_MAC_ADDRESS', 'MAC address format is invalid for sync.', 400)

        hostname = (payload.get('hostname') or '').strip() or None
        unique_client_id = (payload.get('unique_client_id') or '').strip() or None
        now_utc = datetime.utcnow()
        resolved_ip, resolved_from, payload_ip, payload_ip_candidates, ip_resolution_code = _resolve_device_ip_from_payload(payload)
        network_signature = str(payload.get('network_signature') or '').strip() or None
        sync_agent_port = payload.get('agent_port')
        ip_changed = False
        sync_mode = current_sync_mode()
        client_policy_version = str(payload.get('restricted_sites_policy_version') or '').strip()
        actor = build_actor_context(username='agent-sync', role='service')
        identity_input = build_identity_input(
            normalized_payload_mac=mac_address,
            unique_client_id=unique_client_id,
            hostname=hostname,
            resolved_ip=resolved_ip,
            payload_ip=payload_ip,
            payload_ip_candidates=payload_ip_candidates,
            network_signature=network_signature,
            now_utc=now_utc,
            device_name_hint=hostname,
        )
        response_payload = {}
        with db.session.begin():
            auth_ctx, auth_error = _authorize_agent_request(
                expected_device_id=None,
                require_bound=False,
                allow_bootstrap=True,
            )
            if auth_error:
                return auth_error

            resolution = reconcile_tracking_identity(
                identity_input=identity_input,
                payload=payload,
                actor=actor,
                sync_mode=sync_mode,
                resolution_source='sync',
                allow_create=True,
            )

            device = resolution.device
            binding = auth_ctx.get('binding') if auth_ctx else None
            issued_binding_secret = None

            if device is not None:
                if binding is not None and int(binding.tracked_device_id) != int(device.id):
                    raise PermissionError('AGENT_KEY_DEVICE_MISMATCH')

                if binding is None:
                    existing_binding = _get_active_binding_for_device(device.id)
                    if existing_binding is None:
                        binding, issued_binding_secret = _create_agent_key_binding(device.id)
                    else:
                        binding = existing_binding
                if binding:
                    _touch_agent_key(binding)

                current_ip = (device.ip_address or '').strip() or None
                if resolved_ip and resolved_ip != current_ip:
                    ip_change = apply_tracked_device_ip_change(
                        tracked_device=device,
                        new_ip=resolved_ip,
                        resolved_hostname=hostname,
                        now_utc=now_utc,
                        payload_ip=payload_ip,
                        payload_candidates=payload_ip_candidates,
                        transport_remote_ip=request.remote_addr,
                        transport_forwarded_for=_transport_forwarded_for_header(),
                        agent_key_id=binding.key_id if binding else None,
                        reason=SYNC_IP_REASON_PAYLOAD,
                        ip_source=resolved_from,
                        network_signature=network_signature,
                        update_last_seen=False,
                        update_updated_at=True,
                        sync_reason=SYNC_IP_REASON_PAYLOAD,
                    )
                    ip_changed = bool(ip_change.get('changed'))
                    db.session.add(
                        AuditLog(
                            user_id=actor.user_id,
                            username=actor.username,
                            user_role=actor.role,
                            action='update',
                            entity_type='tracked_device',
                            entity_id=device.id,
                            entity_name=device.device_name,
                            description=f"Updated tracked device IP from {current_ip or 'N/A'} to {resolved_ip}.",
                            changes={
                                'old_ip': current_ip,
                                'new_ip': resolved_ip,
                                'resolved_from': resolved_from,
                                'agent_key_id': binding.key_id if binding else None,
                            },
                            ip_address=request.remote_addr,
                            user_agent=(request.headers.get('User-Agent') or '')[:200],
                        )
                    )

                device.last_agent_sync_at = now_utc
                device.last_policy_sync_at = now_utc
                if client_policy_version:
                    device.last_policy_version_seen = client_policy_version
                current_resolved_ip = (device.ip_address or '').strip() or None
                if current_resolved_ip:
                    device.last_agent_sync_ip = current_resolved_ip
            else:
                current_resolved_ip = resolved_ip or payload_ip

            discovery_ip_candidates = []
            for candidate_ip in payload_ip_candidates or []:
                normalized_ip = str(candidate_ip or '').strip()
                if normalized_ip:
                    discovery_ip_candidates.append(normalized_ip)
            for candidate_ip in (payload_ip, resolved_ip, current_resolved_ip):
                normalized_ip = str(candidate_ip or '').strip()
                if normalized_ip:
                    discovery_ip_candidates.append(normalized_ip)

            for candidate_ip in discovery_ip_candidates:
                remember_tracking_agent_port(candidate_ip, sync_agent_port)

            current_stats = extract_current_stats_payload(payload)
            current_stats_valid = isinstance(current_stats, dict)
            hardware_specs = _extract_tracking_hardware_specs(payload)
            integrity_error_code = None
            ingest_result = None

            has_metrics_cache = bool(current_stats_valid and (
                current_stats.get('system_metrics') or
                current_stats.get('today_stats') or
                current_stats.get('current_activity')
            ))
            if device is not None:
                inventory_device = None
                resolved_inventory_device_id = resolution.resolved_inventory_device_id
                if resolved_inventory_device_id:
                    inventory_device = Device.query.get(resolved_inventory_device_id)
                elif device is not None:
                    active_link = (
                        DeviceIdentityLink.query
                        .filter_by(tracked_device_id=device.id, is_active=True)
                        .order_by(DeviceIdentityLink.updated_at.desc(), DeviceIdentityLink.id.desc())
                        .first()
                    )
                    if active_link and active_link.device_id:
                        inventory_device = Device.query.get(active_link.device_id)

                if inventory_device is not None and hardware_specs:
                    merged_specs = dict(inventory_device.hardware_specs or {}) if isinstance(inventory_device.hardware_specs, dict) else {}
                    merged_specs.update(hardware_specs)
                    inventory_device.hardware_specs = merged_specs

                # Capture agent_version from payload meta block
                _agent_ver = None
                if current_stats_valid and isinstance(current_stats, dict):
                    _meta_block = current_stats.get('meta') or {}
                    if isinstance(_meta_block, dict):
                        _agent_ver = str(_meta_block.get('agent_version') or '').strip() or None
                if _agent_ver and device is not None:
                    device.agent_version = _agent_ver

                if current_stats is not None and not current_stats_valid:
                    integrity_error_code = 'INTEGRITY_PAYLOAD_INVALID'
                    device.availability_status = 'degraded'
                    device.metrics_available = False
                    device.probe_method = 'sync'
                    device.probe_error_code = integrity_error_code
                    device.last_probe_at = now_utc
                elif current_stats_valid:
                    ingest_ok = False
                    try:
                        with db.session.begin_nested():
                            ingest_result = ingest_tracking_sample(
                                device_id=device.id,
                                payload=current_stats,
                                source='sync',
                                received_at=now_utc,
                            )
                        ingest_ok = True
                    except Exception as ingest_exc:
                        integrity_error_code = 'INTEGRITY_INGEST_FAILED'
                        logger.warning("[TrackingSync] ingest failed mac=%s err=%s", mac_address, ingest_exc)

                    if ingest_ok:
                        device.availability_status = 'online' if has_metrics_cache else 'degraded'
                        device.metrics_available = bool(has_metrics_cache)
                        device.probe_method = 'sync'
                        device.probe_error_code = None
                        device.last_probe_at = now_utc
                        if has_metrics_cache:
                            device.tracking_data = json.dumps(current_stats, ensure_ascii=True)
                    else:
                        device.availability_status = 'degraded'
                        device.metrics_available = False
                        device.probe_method = 'sync'
                        device.probe_error_code = integrity_error_code
                        device.last_probe_at = now_utc

                _remember_sync_discovery_state(
                    discovery_ip_candidates,
                    current_stats=current_stats if current_stats_valid else None,
                    availability_status=device.availability_status,
                    metrics_available=bool(device.metrics_available),
                    probe_error_code=integrity_error_code,
                    agent_port=sync_agent_port,
                )

                persist_availability_event(
                    device=device,
                    probe_result={
                        'availability_status': device.availability_status,
                        'metrics_available': bool(device.metrics_available),
                        'probe_method': 'sync',
                        'probe_error_code': integrity_error_code or device.probe_error_code,
                        'sample_id': ingest_result.sample_id if ingest_result else None,
                        'observed_at': now_utc,
                    },
                    source='sync',
                    dry_run=False,
                )

                cache_key = resolution.authoritative_mac or mac_address
                cached_entry = real_time_data.get(cache_key, {})
                real_time_data[cache_key] = {
                    'data': current_stats if current_stats_valid else {},
                    'status': device.availability_status or ('online' if has_metrics_cache else 'degraded'),
                    'availability_status': device.availability_status or ('online' if has_metrics_cache else 'degraded'),
                    'device_info': device_to_dict(device),
                    'timestamp': time.time(),
                    'last_log_time': cached_entry.get('last_log_time', 0),
                    'metrics_available': bool(device.metrics_available),
                    'metrics_stale': False,
                    'probe_method': 'sync',
                    'probe_error_code': integrity_error_code,
                }
                if ingest_result and ingest_result.created:
                    real_time_data[cache_key]['last_log_time'] = time.time()
            else:
                pending_status = 'online' if has_metrics_cache else 'degraded'
                _remember_sync_discovery_state(
                    discovery_ip_candidates,
                    current_stats=current_stats if current_stats_valid else None,
                    availability_status=pending_status,
                    metrics_available=bool(has_metrics_cache),
                    probe_error_code='PENDING_CONFIRMATION',
                    agent_port=sync_agent_port,
                )

            response_payload = {
                'success': True,
                'message': 'Sync received' if device is not None else 'Sync accepted pending confirmation.',
                'device': device_to_dict(device) if device is not None else None,
                'sample': ingest_result.to_dict() if ingest_result else None,
                'integrity_error_code': integrity_error_code,
                'resolved_ip': resolved_ip,
                'resolved_from': resolved_from,
                'ip_changed': bool(ip_changed),
                'ip_resolution_code': ip_resolution_code,
                'synced_at': now_utc.isoformat(),
                'identity_status': resolution.identity_status,
                'visible_in_tracking': bool(resolution.visible_in_tracking),
                'identity_confirmed': bool(resolution.identity_confirmed),
                'authoritative_mac': resolution.authoritative_mac,
                'authoritative_mac_source': resolution.authoritative_mac_source,
                'resolution_path': resolution.resolution_path,
                'resolution_source': resolution.resolution_source,
                'resolved_inventory_device_id': resolution.resolved_inventory_device_id,
                'merged_duplicate_device_id': resolution.merged_duplicate_device_id,
            }

            if device is not None:
                response_payload.update(build_sync_policy_payload(device.id, client_policy_version))
                restricted_site_events = _coerce_restricted_events(payload.get('restricted_site_events'))
                if restricted_site_events:
                    policy = RestrictedSitePolicy.get_singleton()
                    response_payload['restricted_site_ingest'] = _ingest_restricted_site_events_internal(
                        device=device,
                        events=restricted_site_events,
                        binding_key_id=binding.key_id if binding else None,
                        policy=policy,
                        now_utc=now_utc,
                    )

                # Ingest typed-text policy alerts (hashes only — no raw text)
                typed_text_alerts = payload.get('typed_text_alerts')
                if isinstance(typed_text_alerts, list) and typed_text_alerts and device:
                    _ingest_typed_text_alerts(device.id, typed_text_alerts)

                # Ingest GPS/location samples inline (lightweight — no worker needed)
                location_samples = payload.get('location_samples')
                if isinstance(location_samples, list) and location_samples and device:
                    _ingest_location_samples(device.id, location_samples)

                # Ingest patch status inline (upsert — idempotent)
                patch_status = payload.get('patch_status')
                if isinstance(patch_status, list) and patch_status and device:
                    _ingest_patch_status(device.id, patch_status)

                if issued_binding_secret and binding:
                    response_payload['agent_binding'] = {
                        'key_id': binding.key_id,
                        'agent_key': issued_binding_secret,
                        'issued_at': now_utc.isoformat(),
                    }

            response_payload['sync_mode'] = sync_mode
            if resolution.envelope is not None:
                response_payload['queue_accepted'] = True
                response_payload['sync_envelope_id'] = resolution.envelope.id
                # Mark domain lane pending only when there is data (avoids no-op worker runs)
                domain_history = payload.get('domain_history')
                if isinstance(domain_history, list) and domain_history:
                    if resolution.envelope.domain_status not in ('pending', 'running'):
                        resolution.envelope.domain_status = 'pending'
                        resolution.envelope.domain_retry_count = 0
                else:
                    # No domain data — skip worker entirely
                    if resolution.envelope.domain_status not in ('running',):
                        resolution.envelope.domain_status = 'skipped'

        return jsonify(response_payload)
    except TrackedDeviceIpSyncError as e:
        db.session.rollback()
        return _json_error(
            'TRACKED_DEVICE_IP_SYNC_FAILED',
            _inventory_sync_error_message(e.reason_code),
            e.status_code,
        )
    except PermissionError as e:
        db.session.rollback()
        if str(e) == 'AGENT_KEY_DEVICE_MISMATCH':
            return _json_error('AGENT_KEY_DEVICE_MISMATCH', 'Agent key is not bound to this device.', 403)
        return _json_error('SYNC_PERMISSION_DENIED', 'Sync request was denied.', 403)
    except SAOperationalError as e:
        db.session.rollback()
        # _lock_inventory_device uses FOR UPDATE NOWAIT. LockNotAvailable means a concurrent
        # writer (SNMP worker, device monitor) holds the device row. Signal the agent to retry
        # after a short back-off rather than logging a hard 500 failure.
        orig = getattr(e, 'orig', None)
        orig_type = type(orig).__name__ if orig is not None else ''
        if orig_type == 'LockNotAvailable':
            response = make_response(jsonify({
                'success': False,
                'error_code': 'SYNC_LOCK_CONTENTION',
                'error': 'Device row temporarily locked by another process. Retry in 2 seconds.',
            }), 503)
            response.headers['Retry-After'] = '2'
            return response
        return _json_exception(
            'TRACKING_SYNC_FAILED',
            'Failed to process tracking sync payload.',
            e,
        )
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'TRACKING_SYNC_FAILED',
            'Failed to process tracking sync payload.',
            e,
        )

# ============================================================
# LIVE TRACKING ROUTES
# ============================================================

@tracking_bp.route('/tracking/live')
@require_login
def live_tracking():
    """Compatibility redirect for legacy fleet links and tracked-device deep links."""
    selected_device_id = request.args.get('device_id', type=int)
    selected_mac = normalize_mac(request.args.get('mac'))

    if selected_device_id:
        return redirect(url_for('tracking_bp.tracked_device_live', device_id=selected_device_id))
    if selected_mac:
        target = scoped_tracked_device_query(
            include_archived=True,
            include_unscoped_for_admin=True,
        ).filter(TrackedDevice.mac_address == selected_mac).first()
        if target:
            return redirect(url_for('tracking_bp.tracked_device_live', device_id=target.id))
    return redirect(url_for('tracking_bp.device_tracking', go_live=1))


@tracking_bp.route('/api/tracking/live-sync', methods=['POST'])
@require_permission('tracking.view')
def api_live_sync():
    """Force an immediate tracked-device snapshot refresh."""
    try:
        refreshed_devices = refresh_tracking_snapshot(force=True, min_interval_seconds=10, force_log=True)
        return jsonify({
            'success': True,
            'refreshed_devices': int(refreshed_devices or 0),
            'message': 'Live sync completed.',
        })
    except Exception as e:
        db.session.rollback()
        return _json_exception('TRACKING_LIVE_SYNC_FAILED', 'Failed to run live sync.', e)


@tracking_bp.route('/tracking/devices/<int:device_id>')
@tracking_bp.route('/devices/<int:device_id>')
@require_permission('tracking.history.view')
def tracked_device_live(device_id):
    """Canonical full-page live telemetry view for a tracked workstation."""
    # Dispatcher: if this device_id belongs to an inventory server (agent/snmp/wmi)
    # and there is no matching TrackedDevice row, redirect to server monitoring.
    _inv = Device.query.filter_by(device_id=device_id).first()
    if _inv is not None and str(getattr(_inv, 'monitoring_mode', '') or '').lower() in ('agent', 'snmp', 'wmi'):
        _td = TrackedDevice.query.get(device_id)
        if _td is None:
            return redirect(url_for('server_metrics_bp.server_monitoring_page', device_id=device_id))

    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    active_inventory_link = (
        DeviceIdentityLink.query
        .filter_by(tracked_device_id=device.id, is_active=True)
        .order_by(DeviceIdentityLink.updated_at.desc(), DeviceIdentityLink.id.desc())
        .first()
    )
    linked_inventory_device_id = active_inventory_link.device_id if active_inventory_link else None
    _role = str(session.get('role') or '').strip().lower()
    file_transfer_enabled = _role == 'admin'
    is_admin = _role == 'admin'
    identity_text = f"{device.device_name or ''} {device.hostname or ''}".strip().lower()
    if 'server' in identity_text:
        device_type_label = 'Server'
    elif 'printer' in identity_text or 'print' in identity_text:
        device_type_label = 'Printer'
    else:
        device_type_label = 'Workstation'

    primary_ip = (device.ip_address or '').strip()
    sync_ip = (getattr(device, 'last_agent_sync_ip', None) or '').strip()
    if primary_ip.startswith('127.') and sync_ip:
        display_ip = sync_ip
    else:
        display_ip = primary_ip or sync_ip or None

    policy_status = 'compliant'
    policy_domain = None
    latest_policy_state = (
        RestrictedSiteAlertState.query
        .filter(RestrictedSiteAlertState.device_id == device.id)
        .order_by(RestrictedSiteAlertState.last_seen_at.desc().nullslast(), RestrictedSiteAlertState.id.desc())
        .first()
    )
    if latest_policy_state and latest_policy_state.active_dashboard_event_id:
        active_event = DashboardEvent.query.filter(
            DashboardEvent.event_id == latest_policy_state.active_dashboard_event_id,
            DashboardEvent.resolved.is_(False),
        ).first()
        if active_event:
            policy_status = 'violating'
            policy_domain = latest_policy_state.domain

    initial_daily_uptime = _attach_daily_uptime_payload({}, device.id).get('daily_uptime', {})
    last_seen_candidates = [value for value in (device.last_agent_sync_at, device.last_seen) if value]
    initial_last_seen_utc = None
    if last_seen_candidates:
        latest_seen = max(last_seen_candidates)
        initial_last_seen_utc = f"{latest_seen.isoformat()}Z"

    return render_template(
        'tracking/device_live.html',
        device=device,
        device_type_label=device_type_label,
        display_ip=display_ip,
        policy_status=policy_status,
        policy_domain=policy_domain,
        initial_daily_uptime=initial_daily_uptime,
        initial_last_seen_utc=initial_last_seen_utc,
        file_transfer_enabled=file_transfer_enabled,
        is_admin=is_admin,
        linked_inventory_device_id=linked_inventory_device_id,
    )


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/system-info')
@require_role('admin')
def api_tracked_device_file_system_info(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)

    try:
        response = _agent_http_get(
            f"{_tracked_agent_base_url(device)}/api/files/system_info",
            timeout=10,
            headers=_tracked_agent_headers(),
        )
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_SYSTEM_INFO_FAILED',
            'Failed to load workstation file system info.',
            exc,
        )

    if response.status_code != 200:
        return _file_proxy_error(
            'TRACKED_DEVICE_FILE_SYSTEM_INFO_FAILED',
            'Workstation file system info is unavailable.',
            response,
        )

    payload = _agent_response_json(response)
    payload.setdefault('success', True)
    payload['device'] = {
        'id': device.id,
        'name': device.device_name,
        'hostname': device.hostname,
        'agent_ip': _resolve_tracked_agent_ip(device),
    }
    return jsonify(payload)


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/list', methods=['POST'])
@require_role('admin')
def api_tracked_device_file_list(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}
    requested_path = str(payload.get('path') or '').strip()

    try:
        response = _agent_http_get(
            f"{_tracked_agent_base_url(device)}/api/files/list",
            timeout=15,
            headers=_tracked_agent_headers(),
            params={'path': requested_path} if requested_path else None,
        )
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_LIST_FAILED',
            'Failed to load workstation files.',
            exc,
        )

    if response.status_code != 200:
        return _file_proxy_error(
            'TRACKED_DEVICE_FILE_LIST_FAILED',
            'Failed to load workstation files.',
            response,
        )

    body = _agent_response_json(response)
    body.setdefault('success', True)
    body['agent_ip'] = _resolve_tracked_agent_ip(device)
    body['device_id'] = device.id
    return jsonify(body)


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/create-folder', methods=['POST'])
@require_role('admin')
def api_tracked_device_file_create_folder(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}

    try:
        response = _agent_http_post(
            f"{_tracked_agent_base_url(device)}/api/files/create_folder",
            timeout=15,
            headers=_tracked_agent_headers(),
            json_data=payload,
        )
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_CREATE_FOLDER_FAILED',
            'Failed to create workstation folder.',
            exc,
        )

    if response.status_code != 200:
        return _file_proxy_error(
            'TRACKED_DEVICE_FILE_CREATE_FOLDER_FAILED',
            'Failed to create workstation folder.',
            response,
        )

    body = _agent_response_json(response)
    body.setdefault('success', True)
    return jsonify(body)


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/delete', methods=['POST'])
@require_role('admin')
def api_tracked_device_file_delete(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}
    raw_paths = payload.get('paths')
    if isinstance(raw_paths, list):
        paths = [str(item or '').strip() for item in raw_paths if str(item or '').strip()]
    else:
        single_path = str(payload.get('path') or '').strip()
        paths = [single_path] if single_path else []

    if not paths:
        return _json_error('PATH_REQUIRED', 'A file or folder path is required.', 400)

    deleted = []
    failed = []
    try:
        headers = _tracked_agent_headers()
        base_url = _tracked_agent_base_url(device)
        for target_path in paths:
            response = _agent_http_post(
                f"{base_url}/api/files/delete",
                timeout=15,
                headers=headers,
                json_data={'path': target_path},
            )
            if response.status_code == 200:
                deleted.append(target_path)
                continue

            error_message = 'Failed to delete workstation file.'
            try:
                response_payload = response.json()
                if isinstance(response_payload, dict):
                    error_message = str(response_payload.get('error') or response_payload.get('message') or error_message)
            except Exception:
                pass
            failed.append({'path': target_path, 'error': error_message, 'status': response.status_code})
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_DELETE_FAILED',
            'Failed to delete workstation file.',
            exc,
        )

    status = 200 if not failed else 207
    return jsonify({
        'success': not failed,
        'deleted': deleted,
        'deleted_count': len(deleted),
        'failed': failed,
        'failed_count': len(failed),
    }), status


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/upload', methods=['POST'])
@require_role('admin')
def api_tracked_device_file_upload(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    incoming_files = [file for file in request.files.getlist('file') if getattr(file, 'filename', '')]
    if not incoming_files:
        return _json_error('FILE_REQUIRED', 'At least one upload file is required.', 400)

    target_path = str(request.form.get('path') or '').strip()
    proxied_files = []
    for file_storage in incoming_files:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        proxied_files.append((
            'file',
            (
                file_storage.filename,
                file_storage.stream,
                file_storage.mimetype or 'application/octet-stream',
            ),
        ))

    try:
        response = _agent_http_post(
            f"{_tracked_agent_base_url(device)}/api/files/upload",
            timeout=60,
            headers=_tracked_agent_headers(),
            data={'path': target_path},
            files=proxied_files,
        )
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_UPLOAD_FAILED',
            'Failed to upload file to workstation.',
            exc,
        )

    if response.status_code != 200:
        return _file_proxy_error(
            'TRACKED_DEVICE_FILE_UPLOAD_FAILED',
            'Failed to upload file to workstation.',
            response,
        )

    body = _agent_response_json(response)
    body.setdefault('success', True)
    body['device_id'] = device.id
    return jsonify(body)


@tracking_bp.route('/api/tracking/devices/<int:device_id>/files/download', methods=['POST'])
@require_role('admin')
def api_tracked_device_file_download(device_id):
    device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
    payload = request.get_json(silent=True) or {}
    target_path = str(payload.get('path') or '').strip()
    if not target_path:
        return _json_error('PATH_REQUIRED', 'A file or folder path is required.', 400)

    try:
        response = _agent_http_get(
            f"{_tracked_agent_base_url(device)}/api/files/download",
            timeout=60,
            headers=_tracked_agent_headers(),
            stream=True,
            params={'path': target_path},
        )
    except ValueError as exc:
        return _json_error('TRACKING_FILE_TRANSFER_UNAVAILABLE', str(exc), 503)
    except AgentHttpError as exc:
        return _agent_error_response(exc, status=503)
    except Exception as exc:
        return _json_exception(
            'TRACKED_DEVICE_FILE_DOWNLOAD_FAILED',
            'Failed to download workstation file.',
            exc,
        )

    if response.status_code != 200:
        return _file_proxy_error(
            'TRACKED_DEVICE_FILE_DOWNLOAD_FAILED',
            'Failed to download workstation file.',
            response,
        )

    fallback_name = str(payload.get('name') or '').strip() or os.path.basename(target_path.rstrip('/\\')) or 'workstation-file'
    if payload.get('is_dir'):
        fallback_name = fallback_name if fallback_name.lower().endswith('.zip') else f"{fallback_name}.zip"

    def generate():
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass

    proxy_response = Response(
        stream_with_context(generate()),
        mimetype=response.headers.get('Content-Type', 'application/octet-stream'),
    )
    proxy_response.headers['Cache-Control'] = 'no-store, max-age=0'
    if response.headers.get('Content-Length'):
        proxy_response.headers['Content-Length'] = response.headers['Content-Length']
    if response.headers.get('Content-Disposition'):
        proxy_response.headers['Content-Disposition'] = response.headers['Content-Disposition']
    else:
        proxy_response.headers['Content-Disposition'] = f'attachment; filename="{fallback_name}"'
    return proxy_response

@tracking_bp.route('/api/tracking/list')
@require_login
def api_tracking_list():
    """Lightweight list of all non-archived tracked devices for search/select pickers."""
    try:
        devices = scoped_tracked_device_query(
            include_archived=False,
            include_unscoped_for_admin=True,
        ).order_by(TrackedDevice.device_name.asc()).all()
        return jsonify([{
            'id': d.id,
            'device_name': d.device_name or '',
            'employee_name': d.employee_name or '',
            'device_ip': d.ip_address or '',
            'mac_address': d.mac_address or '',
        } for d in devices])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@tracking_bp.route('/api/tracking/live-summary')
@require_login
def api_live_summary():
    """Get live summary data for all devices pulling from the background-synced DB cache"""
    
    try:
        from extensions import redis_client
        now_utc = datetime.utcnow()
        limit = _parse_limit(default=100, max_val=500)
        checkin_window_seconds = max(30, int(getattr(Config, 'TRACKING_AGENT_CHECKIN_WINDOW_SECONDS', 180) or 180))
        total_devices = scoped_tracked_device_query(
            include_archived=False,
            include_unscoped_for_admin=True,
        ).count()
        devices = scoped_tracked_device_query(
            include_archived=False,
            include_unscoped_for_admin=True,
        ).order_by(TrackedDevice.device_name.asc()).limit(limit).all()
        identity_sources = _identity_source_map_for_tracked_devices([device.id for device in devices])
        violation_summary_map = _safe_build_active_violation_summary([device.id for device in devices if device and device.id])
        summary_data = []
        
        # MGET High-Speed Cache
        redis_results = []
        if redis_client and devices:
            try:
                keys = [f"tracking:probe:{d.mac_address}" for d in devices]
                redis_results = redis_client.mget(keys)
            except Exception:
                redis_results = [None] * len(devices)
        else:
            redis_results = [None] * len(devices)

        for i, device in enumerate(devices):
            tracking_info = {}
            metrics_available = False
            availability_status = 'offline'
            probe_error_code = device.probe_error_code
            probe_method = device.probe_method
            last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None
            last_agent_sync_at = device.last_agent_sync_at
            last_agent_sync_at_iso = last_agent_sync_at.isoformat() if last_agent_sync_at else None
            agent_sync_age_seconds = None
            agent_sync_recent = False
            if last_agent_sync_at:
                age_seconds = max(0, int((now_utc - last_agent_sync_at).total_seconds()))
                agent_sync_age_seconds = age_seconds
                agent_sync_recent = age_seconds <= checkin_window_seconds
            is_from_redis = False

            # Try Redis first (High Speed Cache)
            if redis_results and i < len(redis_results) and redis_results[i]:
                try:
                    payload = json.loads(redis_results[i])
                    if isinstance(payload, dict):
                        candidate_tracking = payload.get('tracking_data')
                        if isinstance(candidate_tracking, dict):
                            tracking_info = _normalize_tracking_snapshot_dict(candidate_tracking)
                        elif any(key in payload for key in ('current_activity', 'today_stats', 'system_metrics', 'activity', 'system', 'network')):
                            # Backward-compatible support for older payload shape
                            tracking_info = _normalize_tracking_snapshot_dict(payload)

                        status_from_cache = str(
                            payload.get('availability_status') or payload.get('status') or ''
                        ).strip().lower()
                        if status_from_cache in ('online', 'degraded', 'offline'):
                            availability_status = status_from_cache
                        elif tracking_info:
                            availability_status = 'online'

                        metrics_available = bool(
                            payload.get('metrics_available', False) or
                            tracking_info.get('system_metrics') or
                            tracking_info.get('today_stats') or
                            tracking_info.get('current_activity')
                        )
                        probe_error_code = payload.get('probe_error_code')
                        probe_method = payload.get('probe_method') or 'redis'
                        last_probe_at = payload.get('last_probe_at') or datetime.utcnow().isoformat()
                        is_from_redis = True
                except Exception:
                    pass

            # DB Fallback (Durable State)
            if not is_from_redis:
                if device.tracking_data:
                    tracking_info = _loads_tracking_snapshot(device.tracking_data)
                availability_status = str(device.availability_status or 'offline').strip().lower()
                if availability_status not in ('online', 'degraded', 'offline'):
                    availability_status = 'offline'
                metrics_available = bool(device.metrics_available)
                probe_error_code = device.probe_error_code
                probe_method = device.probe_method
                last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None

            if not probe_error_code and availability_status == 'offline':
                probe_error_code = 'DEVICE_NO_IP' if not device.ip_address else 'AGENT_UNREACHABLE'
            
            device_data = {
                'id': device.id,
                'device_name': device.device_name,
                'employee_name': device.employee_name,
                'hostname': device.hostname,
                'mac_address': device.mac_address,
                'ip_address': device.ip_address,
                'status': availability_status,
                'availability_status': availability_status,
                'probe_error_code': probe_error_code,
                'probe_method': probe_method,
                'metrics_available': metrics_available,
                'last_probe_at': last_probe_at,
                'last_agent_sync_at': last_agent_sync_at_iso,
                'agent_sync_age_seconds': agent_sync_age_seconds,
                'agent_sync_recent': bool(agent_sync_recent),
                'agent_sync_window_seconds': checkin_window_seconds,
                'last_agent_sync_ip': getattr(device, 'last_agent_sync_ip', None),
                'tracking_data': tracking_info,
                'identity_confirmed': True,
                'identity_source': identity_sources.get(int(device.id), 'legacy_confirmed'),
            }
            _apply_violation_summary(
                device_data,
                violation_summary_map.get(int(device.id)),
            )
            summary_data.append(device_data)
        active_agent_checkins = len([d for d in summary_data if d.get('agent_sync_recent')])
        never_seen_count = len([d for d in summary_data if not d.get('last_agent_sync_at')])
        policy_violations_count = sum(
            int(d.get('active_violation_count') or 0) for d in summary_data
        )

        return jsonify({
            'success': True,
            'total': total_devices,
            'truncated': total_devices > limit,
            'total_devices': len(devices),
            'online_devices': len([d for d in summary_data if d['status'] == 'online']),
            'degraded_devices': len([d for d in summary_data if d['status'] == 'degraded']),
            'reachable_devices': len([d for d in summary_data if d['status'] in ('online', 'degraded')]),
            'offline_devices': len([d for d in summary_data if d['status'] == 'offline']),
            'never_seen_count': never_seen_count,
            'policy_violations_count': policy_violations_count,
            'active_agent_checkins': active_agent_checkins,
            'agent_sync_window_seconds': checkin_window_seconds,
            'devices': summary_data
        })
        
    except Exception as e:
        return _json_exception(
            'LIVE_SUMMARY_FAILED',
            'Failed to load live tracking summary.',
            e,
        )


@tracking_bp.route('/api/tracking/devices/<int:device_id>/alerts')
@require_permission('tracking.history.view')
def api_device_restricted_alerts(device_id):
    """Return restricted-site violation records for one tracked device."""
    try:
        device = get_scoped_tracked_device_or_404(device_id, include_archived=True)
        states = RestrictedSiteAlertState.query.filter(
            RestrictedSiteAlertState.device_id == device.id
        ).order_by(
            RestrictedSiteAlertState.last_seen_at.desc().nullslast(),
            RestrictedSiteAlertState.id.desc(),
        ).limit(100).all()

        if not states:
            return jsonify(
                {
                    'success': True,
                    'device_id': device.id,
                    'active_violation_count': 0,
                    'highest_violation_severity': 'LOW',
                    'latest_violation_timestamp': None,
                    'alerts': [],
                }
            )

        dashboard_event_ids = sorted(
            {
                str(state.active_dashboard_event_id).strip()
                for state in states
                if state.active_dashboard_event_id
            }
        )
        dashboard_events = {}
        if dashboard_event_ids:
            event_rows = DashboardEvent.query.filter(
                DashboardEvent.event_id.in_(dashboard_event_ids)
            ).all()
            dashboard_events = {str(event.event_id): event for event in event_rows}

        alerts = []
        active_violation_count = 0
        highest_violation_severity = 'LOW'
        latest_violation_dt = None

        for state in states:
            latest_event = RestrictedSiteEvent.query.filter(
                RestrictedSiteEvent.device_id == device.id,
                RestrictedSiteEvent.domain == state.domain,
            ).order_by(
                RestrictedSiteEvent.observed_at_utc.desc(),
                RestrictedSiteEvent.id.desc(),
            ).first()

            active_key = str(state.active_dashboard_event_id or '').strip()
            dashboard_event = dashboard_events.get(active_key) if active_key else None
            is_active = bool(dashboard_event and not dashboard_event.resolved)

            if is_active:
                active_violation_count += 1

            if is_active and dashboard_event and dashboard_event.is_acknowledged:
                status = 'Acknowledged'
            elif is_active:
                status = 'New'
            else:
                status = 'Resolved'

            confidence = (
                getattr(latest_event, 'confidence', None)
                or _extract_restricted_confidence(dashboard_event.message if dashboard_event else None)
                or RESTRICTED_CONFIDENCE_LOW
            )
            severity = _restricted_severity_from_confidence(confidence)
            if is_active and _restricted_severity_rank(severity) > _restricted_severity_rank(highest_violation_severity):
                highest_violation_severity = severity

            observed_at = (
                latest_event.observed_at_utc if latest_event else None
            ) or state.last_seen_at or (dashboard_event.timestamp if dashboard_event else None)
            if observed_at and (latest_violation_dt is None or observed_at > latest_violation_dt):
                latest_violation_dt = observed_at

            alerts.append(
                {
                    'domain': state.domain,
                    'site_visited': state.domain,
                    'matched_rule': latest_event.matched_rule if latest_event else state.domain,
                    'source': latest_event.source if latest_event else None,
                    'confidence': str(confidence).upper(),
                    'severity': severity,
                    'status': status,
                    'hit_count': int(state.hit_count or 0),
                    'timestamp': observed_at.isoformat() if observed_at else None,
                    'observed_at_utc': observed_at.isoformat() if observed_at else None,
                    'first_seen_at': state.first_seen_at.isoformat() if state.first_seen_at else None,
                    'last_seen_at': state.last_seen_at.isoformat() if state.last_seen_at else None,
                    'dashboard_event_id': dashboard_event.event_id if dashboard_event else None,
                    'is_acknowledged': bool(dashboard_event.is_acknowledged) if dashboard_event else False,
                    'resolved': bool(dashboard_event.resolved) if dashboard_event else True,
                }
            )

        alerts.sort(key=lambda item: item.get('timestamp') or '', reverse=True)

        return jsonify(
            {
                'success': True,
                'device_id': device.id,
                'active_violation_count': int(active_violation_count),
                'highest_violation_severity': highest_violation_severity if active_violation_count > 0 else 'LOW',
                'latest_violation_timestamp': latest_violation_dt.isoformat() if latest_violation_dt else None,
                'alerts': alerts,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        return _json_exception(
            'DEVICE_RESTRICTED_ALERTS_FAILED',
            'Failed to load restricted-site alerts.',
            e,
        )

@tracking_bp.route('/api/tracking/live-status/<mac_address>')
@require_login
def api_live_status(mac_address):
    """Get simplified live status for a device directly from DB cache"""
    
    try:
        from extensions import redis_client
        
        device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
        if not device or not device.ip_address:
            return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
        
        tracking_info = {}
        availability_status = 'offline'
        metrics_available = False
        probe_error_code = device.probe_error_code
        probe_method = device.probe_method
        last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None
        is_from_redis = False
        
        # Redis Primary Try
        if redis_client:
            try:
                val = redis_client.get(f"tracking:probe:{mac_address}")
                if val:
                    payload = json.loads(val)
                    if isinstance(payload, dict):
                        candidate_tracking = payload.get('tracking_data')
                        if isinstance(candidate_tracking, dict):
                            tracking_info = _normalize_tracking_snapshot_dict(candidate_tracking)
                        elif any(key in payload for key in ('current_activity', 'today_stats', 'system_metrics', 'activity', 'system', 'network')):
                            # Backward-compatible support for older payload shape
                            tracking_info = _normalize_tracking_snapshot_dict(payload)

                        status_from_cache = str(
                            payload.get('availability_status') or payload.get('status') or ''
                        ).strip().lower()
                        if status_from_cache in ('online', 'degraded', 'offline'):
                            availability_status = status_from_cache
                        elif tracking_info:
                            availability_status = 'online'

                        metrics_available = bool(
                            payload.get('metrics_available', False) or
                            tracking_info.get('system_metrics') or
                            tracking_info.get('today_stats') or
                            tracking_info.get('current_activity')
                        )
                        probe_error_code = payload.get('probe_error_code')
                        probe_method = payload.get('probe_method') or 'redis'
                        last_probe_at = payload.get('last_probe_at') or datetime.utcnow().isoformat()
                        is_from_redis = True
            except Exception:
                pass
                
        # DB Fallback
        if not is_from_redis:
            if device.tracking_data:
                tracking_info = _loads_tracking_snapshot(device.tracking_data)
            availability_status = str(device.availability_status or 'offline').strip().lower()
            if availability_status not in ('online', 'degraded', 'offline'):
                availability_status = 'offline'
            metrics_available = bool(device.metrics_available)
            probe_error_code = device.probe_error_code
            probe_method = device.probe_method
            last_probe_at = device.last_probe_at.isoformat() if device.last_probe_at else None

        if not probe_error_code and availability_status == 'offline':
            probe_error_code = 'DEVICE_NO_IP' if not device.ip_address else 'AGENT_UNREACHABLE'

        return jsonify({
            'success': True,
            'status': availability_status,
            'availability_status': availability_status,
            'device_name': device.device_name,
            'activity': tracking_info.get('current_activity', {}),
            'resources': tracking_info.get('system_metrics', {}),
            'metrics_available': metrics_available,
            'probe': {
                'method': probe_method,
                'error_code': probe_error_code,
            },
            'timestamp': datetime.utcnow().isoformat(),
            'last_probe_at': last_probe_at,
        })
            
    except Exception as e:
        return _json_exception(
            'LIVE_STATUS_FAILED',
            'Failed to load live status.',
            e,
        )

# ============================================================
# ALERT FUNCTIONS
# ============================================================

def check_live_alerts(tracking_data, device_info):
    """Check for live tracking alerts"""
    if device_info and device_info.get('maintenance_mode'):
        return []

    alerts = []
    
    # Check for high resource usage
    system_metrics = tracking_data.get('system_metrics', {})
    if system_metrics.get('cpu_percent', 0) > 90:
        alerts.append({
            'type': 'high_cpu',
            'message': f'High CPU usage: {system_metrics["cpu_percent"]}%',
            'severity': 'warning'
        })
    
    if system_metrics.get('memory_percent', 0) > 90:
        alerts.append({
            'type': 'high_memory',
            'message': f'High memory usage: {system_metrics["memory_percent"]}%',
            'severity': 'warning'
        })
    
    # Check for prolonged inactivity
    current_activity = tracking_data.get('current_activity', {})
    if current_activity.get('idle_seconds', 0) > 1800:  # 30 minutes
        alerts.append({
            'type': 'inactive',
            'message': f'Device inactive for {current_activity["idle_seconds"] // 60} minutes',
            'severity': 'info'
        })
    
    return alerts

@tracking_bp.route('/api/tracking/live-alerts')
@require_login
def api_live_alerts():
    """Get live alerts for tracked devices.

    If ?device_id= is provided: run a live probe for that single device only.
    If absent: return last-known alert state from DB without live probing (non-blocking).
    """
    device_id = request.args.get('device_id', type=int)

    try:
        live = False
        if device_id:
            device = TrackedDevice.query.get(device_id)
            if not device:
                return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)
            devices = [device]
        else:
            devices = TrackedDevice.query.filter(
                db.or_(TrackedDevice.is_archived.is_(False), TrackedDevice.is_archived.is_(None))
            ).all()

        all_alerts = []
        for device in devices:
            if device_id and device.ip_address:
                # Single-device live probe only (bounded, user-initiated)
                scanner = NetworkScanner()
                scanner.timeout = 2.5
                service_info = scanner.check_tracking_service(device.ip_address, profile='interactive')
                availability_status = service_info.get('availability_status', 'offline') if isinstance(service_info, dict) else 'offline'
                tracking_payload = (service_info.get('data') or {}) if isinstance(service_info, dict) else {}
                live = True
                if availability_status not in ('online', 'degraded'):
                    tracking_payload = {}
            else:
                # No live scan — return last-known state only
                tracking_payload = {}

            alerts = check_live_alerts(tracking_payload, device_to_dict(device))
            for alert in alerts:
                alert['device_name'] = device.device_name
                alert['device_id']   = device.id
                all_alerts.append(alert)

        return jsonify({
            'success': True,
            'alerts': all_alerts,
            'count':  len(all_alerts),
            'live':   live,
        })

    except Exception as e:
        return _json_exception(
            'LIVE_ALERTS_FAILED',
            'Failed to load live alerts.',
            e,
        )

@tracking_bp.route('/api/tracking/maintenance/<mac_address>', methods=['POST'])
@require_role('admin')
def api_toggle_device_maintenance(mac_address):
    """Toggle maintenance mode for a tracked device."""

    data = request.get_json(silent=True) or {}
    if 'enabled' not in data:
        return _json_error('MISSING_ENABLED_FLAG', 'Missing enabled flag', 400)

    enabled = data.get('enabled')
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ('true', '1', 'yes', 'on')
    else:
        enabled = bool(enabled)

    device = TrackedDevice.query.filter_by(mac_address=mac_address).first()
    if not device:
        return _json_error('DEVICE_NOT_FOUND', 'Device not found', 404)

    try:
        device.maintenance_mode = enabled
        device.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return _json_exception(
            'MAINTENANCE_UPDATE_FAILED',
            'Failed to update maintenance mode.',
            e,
        )

    return jsonify({
        'success': True,
        'mac_address': device.mac_address,
        'maintenance_mode': device.maintenance_mode
    })

# ============================================================
# PRODUCTIVITY & INTELLIGENCE METRICS
# ============================================================

@tracking_bp.route('/api/tracking/metrics/productivity')
@require_login
def api_productivity_metrics():
    """Get productivity metrics and work session blocks."""
        
    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()

        # Fetch all app logs for today
        app_logs = DeviceApplicationLog.query.filter(
            db.func.date(DeviceApplicationLog.timestamp) == today
        ).all()

        focus_score, productive_time, distracting_time, neutral_time, total_time = calculate_focus_score(app_logs)

        # Work session blocks and idle insights
        activity_logs = DeviceActivityLog.query.filter(
            db.func.date(DeviceActivityLog.timestamp) == today
        ).all()
        work_sessions = build_work_sessions(activity_logs)
        longest_idle_seconds = calculate_longest_idle_seconds(activity_logs)

        return jsonify({
            'success': True,
            'productivity': {
                'focus_score': focus_score,
                'productive_seconds': productive_time,
                'neutral_seconds': neutral_time,
                'distracting_seconds': distracting_time,
                'non_productive_seconds': distracting_time + neutral_time,
                'longest_idle_seconds': longest_idle_seconds,
                'total_tracked_seconds': total_time
            },
            'work_sessions': work_sessions,
            'timestamp': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        return _json_exception(
            'PRODUCTIVITY_METRICS_FAILED',
            'Failed to calculate productivity metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/security')
@require_login
def api_security_metrics():
    """Get security risk metrics and unusual activity alerts."""

    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        resource_logs = DeviceResourceLog.query.filter(
            db.func.date(DeviceResourceLog.timestamp) == today
        ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()

        device_stats = {}
        total_upload_kb = 0
        total_download_kb = 0
        high_cpu_events_total = 0

        last_ts_by_device = {}
        for log in resource_logs:
            stats = device_stats.setdefault(log.device_id, {
                'device_id': log.device_id,
                'high_cpu_events': 0,
                'high_mem_events': 0,
                'total_upload_kb': 0,
                'total_download_kb': 0,
                'risk_score': 0
            })

            if (log.cpu_usage or 0) > 90:
                stats['high_cpu_events'] += 1
                high_cpu_events_total += 1
            if (log.memory_usage or 0) > 90:
                stats['high_mem_events'] += 1

            interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
            upload_kb = (log.upload_kbps or 0) * interval_seconds
            download_kb = (log.download_kbps or 0) * interval_seconds
            stats['total_upload_kb'] += upload_kb
            stats['total_download_kb'] += download_kb
            total_upload_kb += upload_kb
            total_download_kb += download_kb

        device_ids = list(device_stats.keys())
        devices = TrackedDevice.query.filter(TrackedDevice.id.in_(device_ids)).all() if device_ids else []
        device_lookup = {device.id: device.device_name for device in devices}

        risk_devices = []
        for device_id, stats in device_stats.items():
            risk_score = 0
            if stats['high_cpu_events'] > 10:
                risk_score += 20
            if stats['total_upload_kb'] > 500 * 1024:
                risk_score += 30
            if stats['total_download_kb'] > 0 and stats['total_upload_kb'] > stats['total_download_kb'] * 1.5:
                risk_score += 40

            stats['risk_score'] = min(100, risk_score)
            stats['device_name'] = device_lookup.get(device_id, 'Unknown')
            stats['upload_mb'] = round(stats['total_upload_kb'] / 1024, 2)
            stats['download_mb'] = round(stats['total_download_kb'] / 1024, 2)
            risk_devices.append(stats)

        risk_devices.sort(key=lambda entry: entry['risk_score'], reverse=True)
        highest_risk_device = risk_devices[0] if risk_devices else None
        highest_risk_score = highest_risk_device['risk_score'] if highest_risk_device else 0
        high_risk_count = sum(1 for entry in risk_devices if entry['risk_score'] > 70)

        total_upload_mb = round(total_upload_kb / 1024, 2)
        total_download_mb = round(total_download_kb / 1024, 2)
        upload_download_ratio = round(total_upload_kb / total_download_kb, 2) if total_download_kb > 0 else 0

        alerts = []
        if highest_risk_device and highest_risk_score >= 70:
            alerts.append({
                'type': 'high_risk_device',
                'message': f"High risk device: {highest_risk_device['device_name']} (score {highest_risk_score})",
                'severity': 'warning'
            })
        if total_upload_mb > 500:
            alerts.append({
                'type': 'high_upload',
                'message': f"High total upload volume today: {total_upload_mb} MB",
                'severity': 'warning'
            })
        if upload_download_ratio > 1.5 and total_upload_mb > 50:
            alerts.append({
                'type': 'upload_ratio',
                'message': f"Upload-to-download ratio elevated: {upload_download_ratio}x",
                'severity': 'info'
            })
        if high_cpu_events_total > 25:
            alerts.append({
                'type': 'cpu_spikes',
                'message': f"High CPU spikes detected: {high_cpu_events_total} events",
                'severity': 'info'
            })

        return jsonify({
            'success': True,
            'security': {
                'highest_risk_score': highest_risk_score,
                'highest_risk_device': highest_risk_device,
                'high_risk_count': high_risk_count,
                'network_upload_mb': total_upload_mb,
                'network_download_mb': total_download_mb,
                'upload_download_ratio': upload_download_ratio,
                'unusual_activity_alerts': alerts
            },
            'top_risk_devices': risk_devices[:10],
            'timestamp': datetime.utcnow().isoformat()
        })

    except Exception as e:
        return _json_exception(
            'SECURITY_METRICS_FAILED',
            'Failed to calculate security metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/performance')
@require_login
def api_performance_metrics():
    """Get performance metrics (CPU heatmap data)."""

    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        resource_logs = DeviceResourceLog.query.filter(
            db.func.date(DeviceResourceLog.timestamp) == today
        ).all()

        hourly_samples = {hour: [] for hour in range(24)}
        for log in resource_logs:
            if log.cpu_usage is None:
                continue
            hourly_samples[log.timestamp.hour].append(log.cpu_usage)

        heatmap = []
        for hour in range(24):
            samples = hourly_samples.get(hour, [])
            avg_cpu = float(np.mean(samples)) if samples else 0.0
            heatmap.append({
                'hour': hour,
                'avg_cpu': round(avg_cpu, 2),
                'samples': len(samples)
            })

        return jsonify({
            'success': True,
            'cpu_heatmap': heatmap,
            'timestamp': datetime.utcnow().isoformat()
        })
    except Exception as e:
        return _json_exception(
            'PERFORMANCE_METRICS_FAILED',
            'Failed to calculate performance metrics.',
            e,
        )

@tracking_bp.route('/api/tracking/metrics/details/<metric_type>')
@require_login
def api_metric_details(metric_type):
    """Get detailed breakdown for a specific metric"""
        
    try:
        refresh_requested = request.args.get('refresh') == '1'
        refresh_tracking_snapshot(force=refresh_requested, force_log=refresh_requested)

        today = datetime.utcnow().date()
        details = []
        
        if metric_type == 'productivity':
            # Breakdown of applications by duration
            app_logs = DeviceApplicationLog.query.filter(
                db.func.date(DeviceApplicationLog.timestamp) == today
            ).all()
            
            app_usage = {}
            for log in app_logs:
                app = log.application_name
                dur = log.duration or 0
                app_usage[app] = app_usage.get(app, 0) + dur
            
            results = []
            for app, duration in app_usage.items():
                category = classify_app(app).title()
                
                results.append({
                    'name': app,
                    'duration_seconds': duration,
                    'category': category,
                    'duration_formatted': f"{duration // 3600}h {(duration % 3600) // 60}m"
                })
            
            # Sort by duration desc
            details = sorted(results, key=lambda x: x['duration_seconds'], reverse=True)[:20]
            
        elif metric_type == 'security':
            # List devices with high resource usage
            resource_logs = DeviceResourceLog.query.filter(
                db.func.date(DeviceResourceLog.timestamp) == today
            ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()
            
            device_risks = {}
            last_ts_by_device = {}
            for log in resource_logs:
                if log.device_id not in device_risks:
                    device = TrackedDevice.query.get(log.device_id)
                    device_risks[log.device_id] = {
                        'device_name': device.device_name if device else 'Unknown',
                        'high_cpu_events': 0,
                        'high_mem_events': 0,
                        'total_upload': 0,
                        'total_download': 0,
                        'risk_score': 0
                    }
                
                if (log.cpu_usage or 0) > 90: device_risks[log.device_id]['high_cpu_events'] += 1
                if (log.memory_usage or 0) > 90: device_risks[log.device_id]['high_mem_events'] += 1
                interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
                device_risks[log.device_id]['total_upload'] += (log.upload_kbps or 0) * interval_seconds
                device_risks[log.device_id]['total_download'] += (log.download_kbps or 0) * interval_seconds
            
            # Filter for "risky" ones (any high event or high upload)
            final_list = []
            for did, data in device_risks.items():
                risk_score = 0
                if data['high_cpu_events'] > 10:
                    risk_score += 20
                if data['total_upload'] > 500 * 1024:
                    risk_score += 30
                if data['total_download'] > 0 and data['total_upload'] > data['total_download'] * 1.5:
                    risk_score += 40
                data['risk_score'] = min(100, risk_score)

                if data['high_cpu_events'] > 0 or data['high_mem_events'] > 0 or data['total_upload'] > 102400: # 100MB
                     data['upload_mb'] = round(data['total_upload'] / 1024, 2)
                     data['download_mb'] = round(data['total_download'] / 1024, 2)
                     final_list.append(data)
            
            details = sorted(final_list, key=lambda x: x['risk_score'], reverse=True)

        elif metric_type == 'network':
             # Top network consumers
            resource_logs = DeviceResourceLog.query.filter(
                db.func.date(DeviceResourceLog.timestamp) == today
            ).order_by(DeviceResourceLog.device_id.asc(), DeviceResourceLog.timestamp.asc()).all()
            
            device_net = {}
            last_ts_by_device = {}
            for log in resource_logs:
                if log.device_id not in device_net:
                    device = TrackedDevice.query.get(log.device_id)
                    device_net[log.device_id] = {
                        'device_name': device.device_name if device else 'Unknown',
                        'upload_kb': 0,
                        'download_kb': 0
                    }
                interval_seconds = _calc_interval_seconds(log, last_ts_by_device)
                device_net[log.device_id]['upload_kb'] += (log.upload_kbps or 0) * interval_seconds
                device_net[log.device_id]['download_kb'] += (log.download_kbps or 0) * interval_seconds
            
            results = []
            for did, data in device_net.items():
                results.append({
                    'device_name': data['device_name'],
                    'upload_mb': round(data['upload_kb'] / 1024, 2),
                    'download_mb': round(data['download_kb'] / 1024, 2),
                    'total_mb': round((data['upload_kb'] + data['download_kb']) / 1024, 2)
                })
            
            details = sorted(results, key=lambda x: x['total_mb'], reverse=True)

        return jsonify({'success': True, 'type': metric_type, 'data': details})

    except Exception as e:
        return _json_exception(
            'METRIC_DETAILS_FAILED',
            'Failed to load metric details.',
            e,
        )
