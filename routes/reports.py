import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    has_request_context,
    jsonify,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import text

from extensions import db
from middleware.rbac import build_scope_context, require_login
from services.device_monitor import DeviceMonitor
from services.report_export_job_service import (
    cleanup_export_jobs as _job_cleanup,
    count_running_export_jobs as _job_count_running,
    create_export_job as _job_create,
    get_export_job as _job_get,
    update_export_job as _job_update,
)
from services.report_meta import build_report_meta

reports_bp = Blueprint('reports_bp', __name__, url_prefix='')
monitor = DeviceMonitor()
logger = logging.getLogger(__name__)


@reports_bp.before_request
@require_login
def _reports_auth_guard():
    return None


_report_cache = {}
_report_cache_lock = threading.Lock()

_rate_limit_hits = {}
_rate_limit_lock = threading.Lock()

_NAIVE_ISO_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$')


class ReportValidationError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _json_error(message, status_code=400, **extra):
    payload = {'error': message}
    payload.update(extra)
    return jsonify(payload), status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_naive() -> datetime:
    return _utcnow().replace(tzinfo=None)


def _request_id() -> str:
    if has_request_context():
        return str(request.headers.get('X-Request-ID') or '-')
    return '-'


def _to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace('+00:00', 'Z')


def _normalize_report_timestamps(value):
    if isinstance(value, datetime):
        return _to_utc_iso(value)
    if isinstance(value, dict):
        return {key: _normalize_report_timestamps(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_report_timestamps(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_report_timestamps(item) for item in value]
    if isinstance(value, str) and _NAIVE_ISO_DATETIME_RE.match(value):
        return f'{value}Z'
    return value


def _param(name, default=None, params=None):
    if isinstance(params, dict) and name in params:
        return params.get(name)
    return request.args.get(name, default)


def _normalize_dt(value):
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _max_days_for_report(report_type):
    global_cap = int(current_app.config.get('MAX_REPORT_RANGE_DAYS', 90))
    if report_type == 'network':
        net_cap = int(current_app.config.get('MAX_NETWORK_REPORT_RANGE_DAYS', 30))
        return min(global_cap, net_cap)
    if report_type == 'productivity':
        prod_cap = int(current_app.config.get('MAX_PRODUCTIVITY_REPORT_RANGE_DAYS', 30))
        return min(global_cap, prod_cap)
    return global_cap


def _parse_date_range(max_days=None, params=None):
    range_type = (_param('range', '24h', params) or '24h').strip().lower()
    end_date = _utcnow_naive()

    custom_start = _param('start', None, params)
    custom_end = _param('end', None, params)

    if custom_start and custom_end:
        try:
            start_date = _normalize_dt(datetime.fromisoformat(str(custom_start)))
            end_date = _normalize_dt(datetime.fromisoformat(str(custom_end)))
        except ValueError as exc:
            raise ReportValidationError(
                'Invalid custom date range. Use ISO 8601 for start/end.'
            ) from exc
    elif custom_start or custom_end:
        raise ReportValidationError('Both start and end must be provided for custom ranges.')
    else:
        ranges = {
            '24h': timedelta(hours=24),
            '7d': timedelta(days=7),
            '30d': timedelta(days=30),
            '90d': timedelta(days=90),
        }
        if range_type == 'mtd':
            start_date = end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = end_date - ranges.get(range_type, timedelta(hours=24))

    if start_date >= end_date:
        raise ReportValidationError('Invalid date range: start must be before end.')

    max_allowed_days = int(max_days if max_days is not None else current_app.config.get('MAX_REPORT_RANGE_DAYS', 90))
    span = end_date - start_date
    if span > timedelta(days=max_allowed_days):
        raise ReportValidationError(
            f'Range exceeds maximum allowed window ({max_allowed_days} days).'
        )

    return start_date, end_date


def _parse_device_ids(params=None):
    raw = _param('device_ids', '', params)
    if not raw:
        return None
    try:
        return [int(x.strip()) for x in str(raw).split(',') if x.strip()]
    except ValueError as exc:
        raise ReportValidationError('Invalid device_ids query parameter.') from exc


def _parse_severity(params=None):
    severity = _param('severity', None, params)
    if not severity:
        return None
    return str(severity).strip().upper()


def _get_service():
    from services.reporting_service import ReportingService

    return ReportingService()


def _is_productivity_report_enabled():
    return bool(current_app.config.get('ENABLE_PRODUCTIVITY_REPORT', False))


def _productivity_disabled_response():
    return _json_error(
        'Productivity report is disabled',
        403,
        feature='productivity_report',
        enabled=False,
    )


def _current_user_key():
    if has_request_context():
        return str(
            session.get('user_id')
            or session.get('username')
            or request.remote_addr
            or 'anonymous'
        )
    return 'system'


def _current_scope_details():
    scope = build_scope_context()
    scope_id = None
    if scope.get('scope_type') == 'site':
        scope_id = scope.get('site_id')
    elif scope.get('scope_type') == 'department':
        scope_id = scope.get('department_id')
    return scope, scope_id


def _count_devices(device_ids):
    if device_ids:
        return max(len(device_ids), 1)
    from models.device import Device
    from middleware.rbac import scoped_query

    if not has_request_context():
        return max(Device.query.count(), 1)
    return max(scoped_query(Device).count(), 1)


def _estimate_report_rows(report_type, start_date, end_date, device_ids=None):
    span_seconds = max((end_date - start_date).total_seconds(), 1)
    minute_buckets = int(span_seconds // 60) + 1
    hourly_buckets = int(span_seconds // 3600) + 1
    daily_buckets = int(span_seconds // 86400) + 1
    device_count = _count_devices(device_ids)

    if report_type == 'executive':
        return 200
    if report_type == 'operational':
        return 7 * 24 + 70
    if report_type == 'alerts':
        return 200
    if report_type == 'device-health':
        span = end_date - start_date
        if span <= timedelta(hours=24):
            return device_count * min(minute_buckets, 24 * 60)
        if span <= timedelta(days=30):
            return device_count * hourly_buckets
        return device_count * daily_buckets
    if report_type == 'network':
        iface_factor = int(current_app.config.get('REPORT_ESTIMATED_INTERFACES_PER_DEVICE', 4))
        return device_count * hourly_buckets * max(iface_factor, 1)
    if report_type == 'productivity':
        return device_count * hourly_buckets
    if report_type in ('maintenance-availability', 'security-compliance'):
        return 1000
    if report_type in ('inventory-assets', 'tracking-operations', 'printer-operations'):
        return 1500

    return 5000


def _count_report_rows(report_type, payload):
    if not isinstance(payload, dict):
        return len(payload) if isinstance(payload, list) else 0

    if report_type == 'device-health':
        ts = payload.get('time_series') or {}
        return sum(len((dev_data or {}).get('points') or []) for dev_data in ts.values())

    if report_type == 'network':
        bandwidth = payload.get('bandwidth') or {}
        bw_rows = sum(len((iface_data or {}).get('points') or []) for iface_data in bandwidth.values())
        uptime_rows = len(payload.get('uptime_summary') or [])
        return bw_rows + uptime_rows

    if report_type == 'alerts':
        return len(payload.get('alerts') or []) + len(payload.get('top_alerted_devices') or [])

    if report_type == 'operational':
        return (
            len(payload.get('heatmap') or [])
            + len(payload.get('audit_log') or [])
            + len(payload.get('new_devices') or [])
        )

    if report_type == 'executive':
        return len(payload.get('top_problematic') or []) + 4

    if report_type == 'productivity':
        app_breakdown = payload.get('app_breakdown') or {}
        app_rows = sum(len((dev or {}).get('apps') or []) for dev in app_breakdown.values())
        return app_rows + len(payload.get('category_totals') or {}) + len(payload.get('activity_summary') or {})

    if report_type == 'maintenance-availability':
        return (
            len(payload.get('scheduled_windows') or [])
            + len(payload.get('maintenance_devices') or [])
            + len(payload.get('downtime_leaders') or [])
            + len(payload.get('tracked_instability') or [])
        )

    if report_type == 'security-compliance':
        return (
            len(payload.get('recent_alerts') or [])
            + len(payload.get('recent_audit_log') or [])
            + len(payload.get('restricted_site_violations') or [])
            + len(payload.get('threshold_breaches') or [])
            + len(payload.get('integrity_breakdown') or {})
        )

    if report_type == 'inventory-assets':
        return (
            len(payload.get('inventory_devices') or [])
            + len(payload.get('tracked_devices') or [])
            + len(payload.get('active_links') or [])
            + len(payload.get('pending_candidates') or [])
        )

    if report_type == 'tracking-operations':
        return (
            len(payload.get('device_freshness') or [])
            + len(payload.get('top_applications') or [])
            + len(payload.get('activity_totals') or [])
            + len(payload.get('availability_breakdown') or [])
            + len(payload.get('integrity_breakdown') or {})
        )

    if report_type == 'printer-operations':
        return len(payload.get('printer_status') or []) + len(payload.get('print_volume') or [])

    return len(payload)


def _build_cache_key(report_type, start_date, end_date, device_ids=None, extras=None):
    scope, _ = _current_scope_details() if has_request_context() else ({'scope_type': 'background'}, None)
    payload = {
        'report_type': report_type,
        'start': start_date.isoformat(),
        'end': end_date.isoformat(),
        'device_ids': sorted(device_ids or []),
        'extras': extras or {},
        'user': _current_user_key(),
        'scope': scope.get('scope_type'),
        'scope_key': scope.get('scope_key'),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f'report:{digest}'


def _get_cached_report(cache_key):
    now = time.time()
    with _report_cache_lock:
        entry = _report_cache.get(cache_key)
        if entry and now < float(entry.get('expires_at', 0)):
            return dict(entry)
        _report_cache.pop(cache_key, None)
    return None


def _cache_ttl_seconds(report_type, start_date, end_date):
    if report_type in ('executive', 'operational'):
        return int(current_app.config.get('REPORT_CACHE_TTL_LONG_RANGE_SECONDS', 300))
    span = end_date - start_date
    if span <= timedelta(hours=24):
        return int(current_app.config.get('REPORT_CACHE_TTL_24H_SECONDS', 60))
    if span <= timedelta(days=30):
        return int(current_app.config.get('REPORT_CACHE_TTL_7D_30D_SECONDS', 180))
    return int(current_app.config.get('REPORT_CACHE_TTL_LONG_RANGE_SECONDS', 300))


def _set_cached_report(cache_key, payload, ttl):
    if ttl <= 0:
        return
    with _report_cache_lock:
        now = time.time()
        _report_cache[cache_key] = {
            'payload': copy.deepcopy(payload),
            'created_at': now,
            'expires_at': now + ttl,
            'ttl': ttl,
        }
        max_entries = int(current_app.config.get('MAX_REPORT_CACHE_ENTRIES', 500))
        if max_entries > 0 and len(_report_cache) > max_entries:
            oldest_keys = [
                key
                for key, _ in sorted(
                    _report_cache.items(),
                    key=lambda item: float(item[1].get('created_at', 0)),
                )[:-max_entries]
            ]
            for key in oldest_keys:
                _report_cache.pop(key, None)


def _enforce_rate_limit(report_type, is_export=False):
    limit = int(
        current_app.config.get(
            'REPORT_EXPORT_RATE_LIMIT_PER_MINUTE' if is_export else 'REPORT_RATE_LIMIT_PER_MINUTE',
            3 if is_export else 5,
        )
    )
    if limit <= 0:
        return

    # Rate-limit per user and report type so normal tab switching does not exhaust a shared bucket.
    key = f"{_current_user_key()}:{'export' if is_export else 'query'}:{report_type}"
    now = time.time()
    window_sec = 60

    with _rate_limit_lock:
        recent = [ts for ts in _rate_limit_hits.get(key, []) if now - ts < window_sec]
        if len(recent) >= limit:
            raise ReportValidationError('Rate limit exceeded. Please wait before running more reports.', 429)
        recent.append(now)
        _rate_limit_hits[key] = recent


def _apply_statement_timeout():
    timeout_ms = int(current_app.config.get('REPORT_STATEMENT_TIMEOUT_MS', 5000))
    if timeout_ms <= 0:
        return

    backend = db.engine.url.get_backend_name()
    if backend == 'postgresql':
        db.session.execute(
            text('SET LOCAL statement_timeout = :timeout_ms'),
            {'timeout_ms': timeout_ms},
        )


def _max_rows_limit(is_export=False):
    if is_export:
        return int(current_app.config.get('MAX_EXPORT_ROWS', 50000))
    return int(current_app.config.get('MAX_REPORT_ROWS', 50000))


def _build_report_generator(service, report_type, start_date, end_date, device_ids=None, severity=None):
    generators = {
        'device-health': lambda: service.get_device_health_report(device_ids, start_date, end_date),
        'productivity': lambda: service.get_productivity_report(device_ids, start_date, end_date),
        'network': lambda: service.get_network_performance_report(device_ids, start_date, end_date),
        'alerts': lambda: service.get_alert_history_report(start_date, end_date, severity, device_ids),
        'executive': lambda: service.get_executive_fleet_health(start_date, end_date),
        'operational': lambda: service.get_operational_report(start_date, end_date),
        'maintenance-availability': lambda: service.get_maintenance_availability_report(start_date, end_date),
        'security-compliance': lambda: service.get_security_compliance_report(start_date, end_date),
        'inventory-assets': lambda: service.get_inventory_assets_report(start_date, end_date),
        'tracking-operations': lambda: service.get_tracking_operations_report(start_date, end_date),
        'printer-operations': lambda: service.get_printer_operations_report(start_date, end_date),
    }
    return generators.get(report_type)


def _validate_report_type(report_type):
    probe_end = _utcnow_naive()
    probe_start = probe_end - timedelta(seconds=1)
    if _build_report_generator(_get_service(), report_type, probe_start, probe_end) is None:
        raise ReportValidationError(f'Unknown report type: {report_type}', 404)


def _log_report(report_type, start_date, end_date, estimated_rows, row_count, duration_s, cached=False, is_export=False, granularity='n/a'):
    range_days = round((end_date - start_date).total_seconds() / 86400, 3)
    logger.info(
        '[REPORT] type=%s export=%s range_days=%s granularity=%s est_rows=%s rows=%s duration=%.3fs cached=%s user=%s request_id=%s',
        report_type,
        str(is_export).lower(),
        range_days,
        granularity,
        estimated_rows,
        row_count,
        duration_s,
        str(cached).lower(),
        _current_user_key(),
        _request_id(),
    )


def _decorate_report_payload(report_type, payload, start_date, end_date, row_count, cache_meta):
    if not isinstance(payload, dict):
        return payload
    enriched = _normalize_report_timestamps(copy.deepcopy(payload))
    enriched['meta'] = _normalize_report_timestamps(build_report_meta(
        report_type,
        enriched,
        start_date=start_date,
        end_date=end_date,
        row_count=row_count,
        cache_hit=bool(cache_meta.get('cache_hit')),
        cache_ttl_seconds=int(cache_meta.get('cache_ttl_seconds', 0) or 0),
        cache_age_seconds=float(cache_meta.get('cache_age_seconds', 0.0) or 0.0),
    ))
    return enriched


def _run_report(
    report_type,
    start_date,
    end_date,
    device_ids=None,
    severity=None,
    is_export=False,
    use_cache=True,
    enforce_rate_limit=True,
    cache_key_override=None,
):
    max_rows = _max_rows_limit(is_export=is_export)
    estimated_rows = _estimate_report_rows(report_type, start_date, end_date, device_ids)

    if estimated_rows > max_rows:
        if is_export:
            raise ReportValidationError(
                'Export exceeds allowed size. Please reduce time range or filter devices.',
                413,
            )
        raise ReportValidationError(
            'Projected result exceeds allowed size. Please reduce time range or filter devices.'
        )

    ttl = _cache_ttl_seconds(report_type, start_date, end_date)
    cache_key = None
    if use_cache:
        cache_key = cache_key_override or _build_cache_key(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            extras={'severity': severity},
        )
        cached_entry = _get_cached_report(cache_key)
        if cached_entry is not None:
            cached_payload = copy.deepcopy(cached_entry.get('payload'))
            row_count = _count_report_rows(report_type, cached_payload)
            granularity = (
                (cached_payload.get('granularity') if isinstance(cached_payload, dict) else None)
                or (cached_payload.get('heatmap_granularity') if isinstance(cached_payload, dict) else None)
                or 'cache'
            )
            _log_report(
                report_type,
                start_date,
                end_date,
                estimated_rows,
                row_count,
                0.0,
                cached=True,
                is_export=is_export,
                granularity=granularity,
            )
            return cached_payload, row_count, 0.0, {
                'cache_hit': True,
                'cache_ttl_seconds': int(cached_entry.get('ttl') or ttl),
                'cache_age_seconds': max(0.0, time.time() - float(cached_entry.get('created_at') or time.time())),
                'cache_key': cache_key,
            }

    if enforce_rate_limit:
        _enforce_rate_limit(report_type, is_export=is_export)

    service = _get_service()
    generator = _build_report_generator(
        service,
        report_type,
        start_date,
        end_date,
        device_ids=device_ids,
        severity=severity,
    )
    if generator is None:
        raise ReportValidationError(f'Unknown report type: {report_type}', 404)

    _apply_statement_timeout()
    started = time.perf_counter()
    payload = generator()
    duration = time.perf_counter() - started

    row_count = _count_report_rows(report_type, payload)
    if row_count > max_rows:
        if is_export:
            raise ReportValidationError(
                'Export exceeds allowed size. Please reduce time range or filter devices.',
                413,
            )
        raise ReportValidationError(
            'Report exceeds allowed size. Please reduce time range or filter devices.'
        )

    granularity = 'n/a'
    if isinstance(payload, dict):
        granularity = payload.get('granularity') or payload.get('heatmap_granularity') or 'n/a'

    _log_report(
        report_type,
        start_date,
        end_date,
        estimated_rows,
        row_count,
        duration,
        cached=False,
        is_export=is_export,
        granularity=granularity,
    )

    if cache_key:
        _set_cached_report(cache_key, payload, ttl)

    return payload, row_count, duration, {
        'cache_hit': False,
        'cache_ttl_seconds': int(ttl or 0),
        'cache_age_seconds': 0.0,
        'cache_key': cache_key,
    }


def _handle_report_exception(report_type, exc):
    db.session.rollback()
    message = str(exc).lower()
    if 'statement timeout' in message or 'canceling statement due to statement timeout' in message:
        logger.warning('[REPORT] type=%s timeout=%s request_id=%s', report_type, exc, _request_id())
        return _json_error('Report query timed out. Please reduce time range or filters.', 504)

    logger.exception('Report request failed: type=%s error=%s request_id=%s', report_type, exc, _request_id())
    return _json_error('Failed to generate report.', 500)


def _collect_params_from_request():
    params = dict(request.args)
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        params.update(body)
    return params


def _cleanup_export_jobs():
    _job_cleanup()


def _create_export_job(owner, report_type, export_format, start_date, end_date, params=None, payload_cache_key=None):
    scope, scope_id = _current_scope_details()
    return _job_create(
        owner_key=owner,
        scope_type=scope.get('scope_type') or 'global',
        scope_id=scope_id,
        report_type=report_type,
        export_format=export_format,
        params=params
        or {
            'start': start_date.isoformat(),
            'end': end_date.isoformat(),
            'report_type': report_type,
        },
        payload_cache_key=payload_cache_key,
    )


def _update_export_job(job_id, **updates):
    for field_name in ('finished_at', 'started_at'):
        if field_name in updates and isinstance(updates[field_name], (int, float)):
            updates[field_name] = datetime.fromtimestamp(
                updates[field_name],
                tz=timezone.utc,
            ).replace(tzinfo=None)
    _job_update(job_id, **updates)


def _get_export_job(job_id, owner):
    return _job_get(job_id, owner_key=owner)


def _count_running_export_jobs():
    return _job_count_running()


def _run_export_job_worker(
    app,
    job_id,
    report_type,
    export_format,
    start_date,
    end_date,
    device_ids,
    severity,
    payload_cache_key,
):
    with app.app_context():
        _update_export_job(job_id, status='running', started_at=time.time())
        try:
            payload, row_count, duration, _ = _run_report(
                report_type,
                start_date,
                end_date,
                device_ids=device_ids,
                severity=severity,
                is_export=True,
                use_cache=True,
                enforce_rate_limit=False,
                cache_key_override=payload_cache_key,
            )
            payload = _normalize_report_timestamps(copy.deepcopy(payload))

            from services.export_service import export_report_buffer

            timestamp = _utcnow().strftime('%Y%m%d_%H%M')
            filename = f'{report_type}_report_{timestamp}.{export_format}'
            export_dir = os.path.join(app.instance_path, 'report_exports')
            os.makedirs(export_dir, exist_ok=True)
            file_path = os.path.join(export_dir, f'{job_id}_{filename}')
            buf = export_report_buffer(payload, report_type, export_format)

            with open(file_path, 'wb') as handle:
                handle.write(buf.getvalue())

            _update_export_job(
                job_id,
                status='completed',
                row_count=row_count,
                filename=filename,
                file_path=file_path,
                duration_seconds=duration,
                finished_at=time.time(),
            )
        except ReportValidationError as exc:
            _update_export_job(
                job_id,
                status='failed',
                error=str(exc),
                finished_at=time.time(),
            )
        except Exception as exc:
            logger.exception(
                'Async export job failed: job_id=%s error=%s request_id=%s',
                job_id,
                exc,
                _request_id(),
            )
            _update_export_job(
                job_id,
                status='failed',
                error='Export job failed unexpectedly.',
                finished_at=time.time(),
            )
        finally:
            db.session.remove()


@reports_bp.route('/reports')
def reports_page():
    return render_template(
        'reports.html',
        productivity_report_enabled=_is_productivity_report_enabled(),
    )


@reports_bp.route('/api/device_statistics')
def get_device_statistics():
    device_ip = request.args.get('device_ip')
    period = request.args.get('period', '24h')

    if period == '24h':
        hours = 24
    elif period == '7d':
        hours = 24 * 7
    elif period == '30d':
        hours = 24 * 30
    else:
        hours = 24

    stats = monitor.get_device_statistics(device_ip, hours)
    return jsonify(stats if stats else {'error': 'No data available'})


@reports_bp.route('/api/daily_report')
def get_daily_report():
    date_str = request.args.get('date')
    if date_str:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        date = None

    report = monitor.get_daily_report(date)
    return jsonify(report)


@reports_bp.route('/api/device_history')
def get_device_history():
    from models.scan_history import DeviceScanHistory

    device_ip = request.args.get('device_ip')
    hours = int(request.args.get('hours', 24))

    cutoff_time = _utcnow_naive() - timedelta(hours=hours)

    scans = DeviceScanHistory.query.filter(
        DeviceScanHistory.device_ip == device_ip,
        DeviceScanHistory.scan_timestamp >= cutoff_time,
    ).order_by(DeviceScanHistory.scan_timestamp).all()

    history_data = [
        {
            'timestamp': scan.scan_timestamp.isoformat(),
            'status': scan.status,
            'latency': scan.ping_time_ms,
            'scan_type': scan.scan_type,
        }
        for scan in scans
    ]

    return jsonify(history_data)


def _run_report_endpoint(report_type, include_severity=False):
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    try:
        _validate_report_type(report_type)
        start_date, end_date = _parse_date_range(max_days=_max_days_for_report(report_type))
        device_ids = _parse_device_ids()
        severity = _parse_severity() if include_severity else None

        payload, row_count, _, cache_meta = _run_report(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            severity=severity,
            is_export=False,
            use_cache=True,
            enforce_rate_limit=True,
        )
        return jsonify(_decorate_report_payload(report_type, payload, start_date, end_date, row_count, cache_meta))
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    except Exception as exc:
        return _handle_report_exception(report_type, exc)


@reports_bp.route('/api/reports/executive')
def get_executive_report():
    return _run_report_endpoint('executive')


@reports_bp.route('/api/reports/operational')
def get_operational_report():
    return _run_report_endpoint('operational')


@reports_bp.route('/api/reports/device-health')
def get_device_health_report():
    return _run_report_endpoint('device-health')


@reports_bp.route('/api/reports/productivity')
def get_productivity_report():
    return _run_report_endpoint('productivity')


@reports_bp.route('/api/reports/network')
def get_network_report():
    return _run_report_endpoint('network')


@reports_bp.route('/api/reports/alerts')
def get_alerts_report():
    return _run_report_endpoint('alerts', include_severity=True)


@reports_bp.route('/api/reports/maintenance-availability')
def get_maintenance_availability_report():
    return _run_report_endpoint('maintenance-availability')


@reports_bp.route('/api/reports/security-compliance')
def get_security_compliance_report():
    return _run_report_endpoint('security-compliance')


@reports_bp.route('/api/reports/inventory-assets')
def get_inventory_assets_report():
    return _run_report_endpoint('inventory-assets')


@reports_bp.route('/api/reports/tracking-operations')
def get_tracking_operations_report():
    return _run_report_endpoint('tracking-operations')


@reports_bp.route('/api/reports/printer-operations')
def get_printer_operations_report():
    return _run_report_endpoint('printer-operations')


@reports_bp.route('/api/reports/<report_type>/export')
def export_report(report_type):
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    export_format = (request.args.get('format', 'csv') or 'csv').lower()
    if export_format not in ('csv', 'xlsx', 'pdf'):
        return _json_error('format must be csv, xlsx, or pdf')

    try:
        _validate_report_type(report_type)
        start_date, end_date = _parse_date_range(max_days=_max_days_for_report(report_type))
        device_ids = _parse_device_ids()
        severity = _parse_severity()

        payload, row_count, _, cache_meta = _run_report(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            severity=severity,
            is_export=True,
            use_cache=False,
            enforce_rate_limit=True,
        )
        payload = _decorate_report_payload(report_type, payload, start_date, end_date, row_count, cache_meta)

        from services.export_service import export_report_buffer

        timestamp = _utcnow().strftime('%Y%m%d_%H%M')
        filename = f'{report_type}_report_{timestamp}'
        buf = export_report_buffer(payload, report_type, export_format)
        if export_format == 'csv':
            mimetype = 'text/csv'
        elif export_format == 'xlsx':
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        else:
            mimetype = 'application/pdf'
        return send_file(
            buf,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f'{filename}.{export_format}',
        )
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    except Exception as exc:
        return _handle_report_exception(report_type, exc)


@reports_bp.route('/api/reports/<report_type>/export-jobs', methods=['POST'])
def create_export_job(report_type):
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    export_params = _collect_params_from_request()
    export_format = str(export_params.get('format', 'xlsx')).lower()
    if export_format not in ('csv', 'xlsx', 'pdf'):
        return _json_error('format must be csv, xlsx, or pdf')

    try:
        _validate_report_type(report_type)
        _cleanup_export_jobs()
        max_concurrent = int(current_app.config.get('REPORT_MAX_CONCURRENT_EXPORT_JOBS', 2))
        if _count_running_export_jobs() >= max_concurrent:
            return _json_error('Too many export jobs are running. Please try again shortly.', 429)

        start_date, end_date = _parse_date_range(
            max_days=_max_days_for_report(report_type),
            params=export_params,
        )
        device_ids = _parse_device_ids(params=export_params)
        severity = _parse_severity(params=export_params)

        _enforce_rate_limit(report_type, is_export=True)
        max_export_rows = _max_rows_limit(is_export=True)
        estimated_rows = _estimate_report_rows(report_type, start_date, end_date, device_ids)
        if estimated_rows > max_export_rows:
            return _json_error(
                'Export exceeds allowed size. Please reduce time range or filter devices.',
                413,
            )

        payload_cache_key = _build_cache_key(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            extras={'severity': severity},
        )
        _run_report(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            severity=severity,
            is_export=False,
            use_cache=True,
            enforce_rate_limit=False,
            cache_key_override=payload_cache_key,
        )

        owner = _current_user_key()
        job_id = _create_export_job(
            owner,
            report_type,
            export_format,
            start_date,
            end_date,
            params=export_params,
            payload_cache_key=payload_cache_key,
        )

        app_obj = current_app._get_current_object()
        worker = threading.Thread(
            target=_run_export_job_worker,
            args=(
                app_obj,
                job_id,
                report_type,
                export_format,
                start_date,
                end_date,
                device_ids,
                severity,
                payload_cache_key,
            ),
            daemon=True,
        )
        worker.start()

        return (
            jsonify(
                {
                    'job_id': job_id,
                    'status': 'pending',
                    'status_url': url_for('reports_bp.get_export_job_status', job_id=job_id),
                    'download_url': url_for('reports_bp.download_export_job', job_id=job_id),
                }
            ),
            202,
        )
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    except Exception as exc:
        return _handle_report_exception(report_type, exc)


@reports_bp.route('/api/reports/export-jobs/<job_id>', methods=['GET'])
def get_export_job_status(job_id):
    _cleanup_export_jobs()
    owner = _current_user_key()
    job = _get_export_job(job_id, owner)
    if not job:
        return _json_error('Export job not found.', 404)

    duration_seconds = job.get('duration_seconds')
    if duration_seconds is None and job.get('started_at') and job.get('finished_at'):
        try:
            started_at = datetime.fromisoformat(str(job.get('started_at')))
            finished_at = datetime.fromisoformat(str(job.get('finished_at')))
            duration_seconds = round((finished_at - started_at).total_seconds(), 3)
        except Exception:
            duration_seconds = None

    payload = {
        'job_id': job['job_id'],
        'report_type': job['report_type'],
        'format': job['format'],
        'status': job['status'],
        'error': job['error'],
        'row_count': job['row_count'],
        'duration_seconds': duration_seconds,
        'created_at': job.get('created_at'),
        'updated_at': job.get('updated_at'),
    }
    if job.get('status') == 'completed':
        payload['download_url'] = url_for('reports_bp.download_export_job', job_id=job_id)
    return jsonify(payload)


@reports_bp.route('/api/reports/export-jobs/<job_id>/download', methods=['GET'])
def download_export_job(job_id):
    _cleanup_export_jobs()
    owner = _current_user_key()
    job = _get_export_job(job_id, owner)
    if not job:
        return _json_error('Export job not found.', 404)

    if job.get('status') != 'completed':
        return _json_error('Export job is not complete yet.', 409)

    file_path = job.get('file_path')
    filename = job.get('filename')
    if not file_path or not os.path.exists(file_path):
        return _json_error('Export file is no longer available.', 404)

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename or f'report_{job_id}.dat',
    )
