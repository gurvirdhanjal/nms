import copy
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import (
    Blueprint,
    current_app,
    g as _g,
    has_request_context,
    jsonify,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func, or_, text

from extensions import db, redis_client, is_redis_available
from models.restricted_site_policy import RestrictedSiteEvent
from models.tracked_device import TrackedDevice
from middleware.rbac import build_scope_context, require_login, require_permission, scoped_query
from services.device_monitor import DeviceMonitor
from services.report_export_job_service import (
    cleanup_export_jobs as _job_cleanup,
    count_running_export_jobs as _job_count_running,
    create_export_job as _job_create,
    get_export_job as _job_get,
    update_export_job as _job_update,
)
from services.report_meta import build_report_meta
from services.report_metrics_enricher import ReportMetricsEnricher
from services.settings_service import get_monitoring_interval, format_monitoring_interval_label

reports_bp = Blueprint('reports_bp', __name__, url_prefix='')
monitor = DeviceMonitor()
logger = logging.getLogger(__name__)


def _exclude_agent_push_scan(model):
    return or_(
        model.scan_type.is_(None),
        model.scan_type != 'agent_push',
    )


def _normalize_report_scan_status(status, ping_time_ms=None):
    normalized = str(status or '').strip().lower()
    if normalized == 'online':
        return 'online'
    if normalized in {'no_response', 'timeout'}:
        return 'no_response'
    if normalized == 'offline' and ping_time_ms is None:
        return 'no_response'
    if normalized == 'offline':
        return 'offline'
    return normalized or 'unknown'


@reports_bp.before_request
def _record_report_start():
    _g._report_start = time.monotonic()


@reports_bp.after_request
def _log_slow_report_response(response):
    start = getattr(_g, '_report_start', None)
    if start is not None:
        elapsed = time.monotonic() - start
        if elapsed > 0.5:
            logger.warning(
                "[Reports] Slow response: %s %.2fs",
                request.endpoint,
                elapsed,
            )
    return response


@reports_bp.before_request
@require_login
def _reports_auth_guard():
    return None


_report_cache = {}
_report_cache_lock = threading.Lock()

_rate_limit_hits = {}
_rate_limit_lock = threading.Lock()


def invalidate_short_ttl_report_cache(max_ttl_seconds: int = 180) -> int:
    """Drop all in-memory report cache entries whose original TTL is <= max_ttl_seconds.

    Called by DeviceMonitor after each scan cycle so that 24h-range reports
    (TTL=120s by default) are rebuilt from fresh DB data on the next request,
    rather than serving stale results for up to 2 minutes.
    Longer-range reports (7d/30d/executive) are left untouched.
    """
    removed = 0
    with _report_cache_lock:
        stale = [k for k, v in list(_report_cache.items()) if float(v.get('ttl', 9999)) <= max_ttl_seconds]
        for k in stale:
            _report_cache.pop(k, None)
            removed += 1
    return removed

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
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
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


def _current_session_snapshot():
    if not has_request_context():
        return {}
    keys = (
        'logged_in',
        'role',
        'username',
        'user_id',
        'site_id',
        'department_id',
        'last_activity',
    )
    return {key: session.get(key) for key in keys if key in session}


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

    # SCOPE: out-of-request context counts ALL devices (background jobs, exports).
    # In-request context counts only devices visible to the current user's site/dept scope.
    # Reports use this to size row limits — not as a user-visible total_devices figure.
    if not has_request_context():
        return max(Device.query.count(), 1)
    return max(scoped_query(Device).count(), 1)


def _estimate_report_rows(report_type, start_date, end_date, device_ids=None):
    """Estimate result size to prevent runaway queries.

    Summary-level reports return a fixed small estimate. Time-series reports
    use a sample-based approach: query actual row count for one device and
    extrapolate. This avoids the old formula (device_count × buckets) which
    vastly overestimated on sparse data.
    """
    # Summary-level reports — small fixed estimates
    # device-health and network return aggregated rows (not raw time-series),
    # so sample×device_count estimation massively over-estimates — use fixed value.
    _SUMMARY_TYPES = {
        'executive': 500,
        'operational': 500,
        'device-health': 5000,
        'network': 5000,
        'alerts': 2000,
        'maintenance-availability': 1500,
        'security-compliance': 1500,
        'inventory-assets': 2000,
        'tracking-operations': 2000,
        'printer-operations': 1500,
    }
    if report_type in _SUMMARY_TYPES:
        return _SUMMARY_TYPES[report_type]

    # Time-series reports — sample-based estimate
    device_count = _count_devices(device_ids)
    if device_count == 0:
        return 0

    sample = _sample_row_density(report_type, start_date, end_date)
    return sample * device_count


def _sample_row_density(report_type, start_date, end_date):
    """Query actual row count for one representative device, 1s timeout."""
    try:
        from models.server_health import ServerHealthLog
        from models.device import Device

        sample_device = Device.query.filter(Device.is_active.isnot(False)).first()
        if not sample_device:
            return 100

        if report_type in ('device-health', 'network'):
            count = (
                db.session.query(func.count(ServerHealthLog.id))
                .filter(
                    ServerHealthLog.device_id == sample_device.device_id,
                    ServerHealthLog.timestamp >= start_date,
                    ServerHealthLog.timestamp <= end_date,
                )
                .scalar()
            ) or 0
            return max(count, 10)

        if report_type == 'productivity':
            from models.tracked_device import TrackingSample, TrackedDevice
            sample_td = TrackedDevice.query.filter(TrackedDevice.is_archived.isnot(True)).first()
            if not sample_td:
                return 100
            count = (
                db.session.query(func.count(TrackingSample.id))
                .filter(
                    TrackingSample.device_id == sample_td.id,
                    TrackingSample.received_at >= start_date,
                    TrackingSample.received_at <= end_date,
                )
                .scalar()
            ) or 0
            return max(count, 10)
    except Exception:
        pass
    return 500  # Safe fallback


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
        return int(current_app.config.get('REPORT_CACHE_TTL_LONG_RANGE_SECONDS', 600))
    span = end_date - start_date
    if span <= timedelta(hours=24):
        return int(current_app.config.get('REPORT_CACHE_TTL_24H_SECONDS', 120))
    if span <= timedelta(days=30):
        return int(current_app.config.get('REPORT_CACHE_TTL_7D_30D_SECONDS', 600))
    return int(current_app.config.get('REPORT_CACHE_TTL_LONG_RANGE_SECONDS', 600))


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


# Redis keys for pre-warmed reports — scheduler refreshes these every 8 minutes.
# Keyed by (report_type, approximate_range_days); route checks on L1 miss.
_REDIS_PREWARM_KEYS = {
    ('executive', 30): 'nms:report:executive:30d',
    ('executive', 7):  'nms:report:executive:7d',
    ('operational', 1): 'nms:report:operational:24h',
}
_REDIS_PREWARM_TTL = 900  # 15 minutes


def _redis_prewarm_key(report_type, range_days):
    """Return the Redis pre-warm key for this report/range, or None if not pre-warmed."""
    for (rtype, days), key in _REDIS_PREWARM_KEYS.items():
        if rtype == report_type and abs(range_days - days) <= 2:
            return key
    return None


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


_ENTERPRISE_REPORT_TYPES = frozenset({'executive', 'tracking-operations'})


def _apply_statement_timeout(report_type=None):
    """Apply per-report-type PostgreSQL statement timeout.

    Enterprise reports (executive, tracking-operations) get a longer timeout
    because they join across multiple fleet tables with heavy aggregation.
    """
    if report_type and report_type in _ENTERPRISE_REPORT_TYPES:
        timeout_ms = int(current_app.config.get('REPORT_TIMEOUT_ENTERPRISE_MS', 20000))
    else:
        timeout_ms = int(current_app.config.get('REPORT_STATEMENT_TIMEOUT_MS', 15000))
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
    # ── Narrative synthesis (Master Spec: progressive disclosure) ─────
    try:
        from services.report_narrative_service import ReportNarrativeService
        enriched['narrative'] = ReportNarrativeService().generate_narrative(report_type, enriched)
    except Exception as exc:
        logger.warning('[REPORT] narrative generation failed for %s: %s', report_type, exc)
        enriched['narrative'] = None
    # ── Intelligence auto-annotations (Master Spec: 7 rules) ─────────
    try:
        from services.report_intelligence_rules import ReportIntelligenceRules
        enriched['intelligence_annotations'] = ReportIntelligenceRules().annotate(report_type, enriched)
    except Exception as exc:
        logger.warning('[REPORT] intelligence annotation failed for %s: %s', report_type, exc)
        enriched['intelligence_annotations'] = []
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
                0,
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

    # L2 — Redis pre-warm check (scheduler keeps these warm; survives restarts)
    if use_cache and cache_key and is_redis_available():
        try:
            range_days = max(1, int((end_date - start_date).total_seconds() / 86400))
            rk = _redis_prewarm_key(report_type, range_days)
            if rk:
                rb = redis_client.get(rk)
                if rb:
                    payload = json.loads(rb)
                    row_count = _count_report_rows(report_type, payload)
                    _set_cached_report(cache_key, payload, ttl)
                    return payload, row_count, 0.0, {
                        'cache_hit': True, 'cache_source': 'redis',
                        'cache_ttl_seconds': ttl,
                        'cache_age_seconds': 0.0,
                        'cache_key': cache_key,
                    }
        except Exception:
            pass  # Redis failure → fall through to DB

    # Row estimation is skipped on cache hits — only run on actual DB queries.
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

    _apply_statement_timeout(report_type)
    started = time.perf_counter()
    payload = generator()
    duration = time.perf_counter() - started
    # Return the connection to the pool immediately — all DB work is done.
    # Without this, the connection is held until Flask teardown, which can
    # exhaust the pool when multiple heavy reports run concurrently.
    db.session.remove()

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

    # Write-through to Redis so the next restart / TTL expiry is a cache hit
    if cache_key and is_redis_available():
        try:
            range_days = max(1, int((end_date - start_date).total_seconds() / 86400))
            rk = _redis_prewarm_key(report_type, range_days)
            if rk:
                redis_client.setex(rk, _REDIS_PREWARM_TTL, json.dumps(payload, default=str))
        except Exception:
            pass

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
    session_snapshot,
):
    with app.app_context():
        _update_export_job(job_id, status='running', started_at=time.time())
        try:
            with app.test_request_context('/api/reports/export-jobs/worker'):
                for key, value in (session_snapshot or {}).items():
                    session[key] = value

                payload, row_count, duration, cache_meta = _run_report(
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
                payload = _decorate_report_payload(
                    report_type,
                    payload,
                    start_date,
                    end_date,
                    row_count,
                    cache_meta,
                )

                from services.export_service import export_report_buffer

                timestamp = _utcnow().strftime('%Y%m%d_%H%M')
                filename = f'{report_type}_report_{timestamp}.pdf'
                export_dir = os.path.join(app.instance_path, 'report_exports')
                os.makedirs(export_dir, exist_ok=True)
                file_path = os.path.join(export_dir, f'{job_id}_{filename}')
                buf = export_report_buffer(payload, report_type)

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
    monitoring_interval_seconds = get_monitoring_interval()
    return render_template(
        'reports.html',
        productivity_report_enabled=_is_productivity_report_enabled(),
        monitoring_interval_seconds=monitoring_interval_seconds,
        monitoring_interval_label=format_monitoring_interval_label(monitoring_interval_seconds),
    )


def _build_device_stats(device_ip: str, hours: int):
    """RBAC-scoped device lookup + ICMP stats + agent telemetry.

    Returns (device, stats) on success.
    Returns (None, None) if device is out of scope (-> 403).
    Returns (device, None) if device is in scope but has no scan data (-> 404).
    """
    from models.device import Device
    from models.server_health import ServerHealthLog

    device = scoped_query(Device).filter_by(device_ip=device_ip).first()
    if not device:
        return None, None

    stats = monitor.get_device_statistics(device_ip, hours)
    if not stats:
        return device, None

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    agent_data = {'available': False}
    agent_logs = (
        ServerHealthLog.query
        .filter(
            ServerHealthLog.device_id == device.device_id,
            ServerHealthLog.source == 'agent',
            ServerHealthLog.timestamp >= cutoff,
        )
        .order_by(ServerHealthLog.timestamp.asc())
        .limit(300)
        .all()
    )
    if agent_logs:
        latest = agent_logs[-1]
        agent_data = {
            'available': True,
            'latest': {
                'cpu_percent': latest.cpu_usage,
                'memory_percent': latest.memory_usage,
                'disk_percent': latest.disk_usage,
                'uptime_seconds': float(latest.uptime) if latest.uptime is not None else None,
                'network_in_bps': latest.network_in_bps,
                'network_out_bps': latest.network_out_bps,
            },
            'time_series': [
                {
                    'timestamp': log.timestamp.isoformat(),
                    'cpu': log.cpu_usage,
                    'memory': log.memory_usage,
                    'disk': log.disk_usage,
                }
                for log in agent_logs
            ],
        }
    stats['agent_data'] = agent_data

    # Per-scan ICMP series — needed for Inspector PDF timeline / availability bar
    from models.scan_history import DeviceScanHistory as _DSH
    try:
        scan_records = list(reversed(
            _DSH.query
            .filter(
                _DSH.device_ip == device_ip,
                _DSH.scan_timestamp >= cutoff,
                _exclude_agent_push_scan(_DSH),
            )
            .order_by(_DSH.scan_timestamp.desc())
            .limit(500)
            .all()
        ))
        stats['scan_series'] = [
            {
                'ts': r.scan_timestamp.isoformat(),
                'status': _normalize_report_scan_status(r.status, r.ping_time_ms),
                'status_detail': getattr(r, 'status_detail', None),
                'ping_ms': r.ping_time_ms,
                'pkt_loss': r.packet_loss,
            }
            for r in scan_records
        ]
    except Exception:
        logger.warning("[DeviceStats] scan_series fetch failed for %s", device_ip)
        stats['scan_series'] = []

    # Website policy violations (tracked device only)
    stats['website_violations'] = []
    try:
        td = TrackedDevice.query.filter_by(ip_address=device_ip).first()
        if td:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            stats['website_violations'] = _device_violation_breakdown(td.id, cutoff)
    except Exception:
        logger.warning("[DeviceStats] website violation lookup failed for %s", device_ip)

    return device, stats


# ── Inspector device search-list (lightweight, Redis-cached) ──────────────────
_DEVICE_SEARCH_LIST_KEY = "reports:device_search_list"
_DEVICE_SEARCH_LIST_TTL = 60  # seconds — refreshed by scan cycle invalidation


@reports_bp.route('/api/reports/device-search-list')
def get_device_search_list():
    """Return a minimal name+IP list for the Device Inspector search dropdown.

    Cached in Redis (60s TTL) so the inspector tab opens instantly instead of
    running a full scan-status join on every page load.
    """
    from extensions import redis_client, is_redis_available
    from models.device import Device

    if is_redis_available():
        try:
            cached = redis_client.get(_DEVICE_SEARCH_LIST_KEY)
            if cached:
                return current_app.response_class(cached, mimetype='application/json')
        except Exception:
            pass

    devices = scoped_query(Device).order_by(Device.device_ip.asc()).all()
    result = [
        {'device_name': d.device_name or d.device_ip, 'device_ip': d.device_ip}
        for d in devices if d.device_ip
    ]
    payload = json.dumps(result)

    if is_redis_available():
        try:
            redis_client.setex(_DEVICE_SEARCH_LIST_KEY, _DEVICE_SEARCH_LIST_TTL, payload)
        except Exception:
            pass

    return current_app.response_class(payload, mimetype='application/json')


@reports_bp.route('/api/device_statistics')
def get_device_statistics():
    device_ip = request.args.get('device_ip')
    period = request.args.get('period', '24h')

    if not device_ip:
        return jsonify({'error': 'device_ip is required'}), 400

    hours = {'24h': 24, '7d': 168, '30d': 720}.get(period, 24)
    device, stats = _build_device_stats(device_ip, hours)

    if device is None:
        return jsonify({'error': 'Device not found'}), 403
    if stats is None:
        return jsonify({'error': 'No data available'}), 404

    return jsonify(stats)


@reports_bp.route('/api/device_statistics/pdf')
@require_permission('reports.export')
def get_device_statistics_pdf():
    device_ip = request.args.get('device_ip')
    period = request.args.get('period', '24h')

    if not device_ip:
        return jsonify({'error': 'device_ip is required'}), 400

    hours = {'24h': 24, '7d': 168, '30d': 720}.get(period, 24)
    period_label = {'24h': 'Last 24 Hours', '7d': 'Last 7 Days',
                    '30d': 'Last 30 Days'}.get(period, 'Last 24 Hours')

    device, stats = _build_device_stats(device_ip, hours)

    if device is None:
        return jsonify({'error': 'Device not found'}), 403
    if stats is None:
        return jsonify({'error': 'No scan data available for this period'}), 404

    stats['device_type'] = device.device_type or '—'

    from services.enterprise_pdf_service import generate_device_inspector_pdf
    try:
        buf = generate_device_inspector_pdf(
            stats, device.device_name, device_ip, period_label,
            period_hours=hours,
        )
    except Exception:
        logger.exception(
            "[DeviceInspectorPDF] Generation failed for %s (%s)", device_ip, period
        )
        return jsonify({'error': 'PDF generation failed'}), 500

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M')
    filename = f"device_inspector_{device_ip.replace('.', '_')}_{period}_{ts}.pdf"
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=filename)


# ── Shared helper: single-device violation breakdown ──────────────────────

def _device_violation_breakdown(device_id: int, cutoff: datetime) -> list:
    """Group RestrictedSiteEvent by domain for a single tracked device.

    Returns [{domain, count, last_seen}] ordered by count DESC.
    Caller is responsible for try/except.
    """
    rows = (
        db.session.query(
            RestrictedSiteEvent.domain,
            func.count(RestrictedSiteEvent.id).label("count"),
            func.max(RestrictedSiteEvent.observed_at_utc).label("last_seen"),
        )
        .filter(
            RestrictedSiteEvent.device_id == device_id,
            RestrictedSiteEvent.observed_at_utc >= cutoff,
        )
        .group_by(RestrictedSiteEvent.domain)
        .order_by(func.count(RestrictedSiteEvent.id).desc())
        .all()
    )
    return [
        {
            "domain": r.domain,
            "count": int(r.count),
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
        }
        for r in rows
    ]


@reports_bp.route('/api/reports/device-violations/<int:device_id>')
def get_device_violations(device_id):
    """Per-device website violation breakdown for the Workstation Monitoring tab."""
    range_str = request.args.get('range', '30d')
    days = {'7d': 7, '30d': 30, '90d': 90}.get(range_str, 30)
    cutoff = datetime.utcnow() - timedelta(days=days)

    td = TrackedDevice.query.get(device_id)
    if not td:
        return jsonify({'error': 'Device not found'}), 404

    try:
        violations = _device_violation_breakdown(td.id, cutoff)
    except Exception:
        logger.warning("[DeviceViolations] query failed for device_id=%s", device_id)
        violations = []

    return jsonify({
        'device_id': device_id,
        'device_name': td.device_name or td.hostname or '\u2014',
        'employee_name': td.employee_name or '\u2014',
        'violations': violations,
    })


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
    try:
        hours = int(request.args.get('hours', 24))
        if hours < 1 or hours > 8760:
            raise ValueError
    except (ValueError, TypeError):
        return _json_error('hours must be an integer between 1 and 8760')

    cutoff_time = _utcnow_naive() - timedelta(hours=hours)

    scans = DeviceScanHistory.query.filter(
        DeviceScanHistory.device_ip == device_ip,
        DeviceScanHistory.scan_timestamp >= cutoff_time,
        _exclude_agent_push_scan(DeviceScanHistory),
    ).order_by(DeviceScanHistory.scan_timestamp).all()

    history_data = [
        {
            'timestamp': scan.scan_timestamp.isoformat(),
            'status': scan.status,
            'status_detail': getattr(scan, 'status_detail', None),
            'latency': scan.ping_time_ms,
            'packet_loss': scan.packet_loss,
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
    finally:
        # Ensure the connection is always returned even on error paths
        # (_run_report already does this on success, but exception paths skip it).
        try:
            db.session.remove()
        except Exception:
            pass


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


@reports_bp.route('/api/reports/devices', methods=['GET'])
def get_devices_report():
    """Unified device list using canonical 18-field row contract.

    Query params:
      range / start+end  — date range (default 7d, max 90d)
      fleet              — server | workstation | (omit for both)
      device_ids         — comma-separated TrackedDevice IDs (workstation filter)
    """
    try:
        start_date, end_date = _parse_date_range(max_days=90)
        fleet = _parse_fleet_param()
        device_ids = _parse_device_ids()
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)

    try:
        # ── 60-second cache ──────────────────────────────────────────────
        _range_key = request.args.get('range', '7d')
        _cache_key  = f"devices_report_{_range_key}"
        _cached     = _get_cached_report(_cache_key)
        if _cached:
            return jsonify(_cached['payload'])

        from services.core_metrics_service import (
            get_server_metrics_bulk, get_workstation_metrics_bulk,
        )
        from models.device import Device

        period_hours = (end_date - start_date).total_seconds() / 3600.0
        rows = []

        if fleet in (None, "server"):
            try:
                infra_types = [t.lower() for t in current_app.config.get(
                    'INFRASTRUCTURE_DEVICE_TYPES',
                    ['server', 'switch', 'access_point', 'router', 'firewall'],
                )]
            except RuntimeError:
                infra_types = ['server', 'switch', 'access_point', 'router', 'firewall']
            inv_devices = (
                Device.query
                .filter(
                    Device.is_active.isnot(False),
                    func.replace(func.lower(Device.device_type), ' ', '_').in_(infra_types),
                )
                .order_by(Device.device_name.asc())
                .all()
            )
            rows.extend(get_server_metrics_bulk(inv_devices, start_date, end_date, period_hours))

        if fleet in (None, "workstation"):
            td_query = (
                TrackedDevice.query
                .filter(TrackedDevice.is_archived.isnot(True))
                .order_by(TrackedDevice.device_name.asc())
            )
            if device_ids:
                td_query = td_query.filter(TrackedDevice.id.in_(device_ids))
            rows.extend(get_workstation_metrics_bulk(
                td_query.all(), start_date, end_date, period_hours,
            ))

        uptime_vals = [r["uptime_pct"] for r in rows if r["uptime_pct"] is not None]
        fleet_avg = round(sum(uptime_vals) / len(uptime_vals), 2) if uptime_vals else None

        # Total all active devices (across all types) for the header counter
        total_all_active = Device.query.filter(Device.is_active.isnot(False)).count()

        _payload = {
            "period": {
                "start": start_date.isoformat(),
                "end":   end_date.isoformat(),
                "days":  (end_date - start_date).days,
            },
            "row_count": len(rows),
            "devices":   rows,
            "summary": {
                "total":            len(rows),
                "total_all":        total_all_active,
                "anomaly_count":    sum(1 for r in rows if r["anomaly_flag"]),
                "violation_count":  sum(r["violation_count"] for r in rows),
                "fleet_avg_uptime": fleet_avg,
            },
        }
        _set_cached_report(_cache_key, _payload, ttl=60)
        return jsonify(_payload)
    except Exception as exc:
        logger.exception("[DevicesReport] build failed: %s", exc)
        return _json_error("Failed to build devices report.", 500)
    finally:
        try:
            db.session.remove()
        except Exception:
            pass


_PREVIEW_COLUMNS = {
    "executive": ["Device", "Type", "IP", "Uptime %", "Status"],
    "alerts": ["Timestamp", "Device", "IP", "Severity", "Type", "Message", "Resolved"],
    "inventory-assets": ["Device", "IP", "Type", "Site", "Department", "Status"],
    "device-health": ["Device", "Avg CPU %", "Avg Mem %", "Avg Disk %", "Points"],
    "network": ["Device", "Avg Uptime %", "Avg Latency (ms)", "Avg Packet Loss %"],
    "operational": ["Timestamp", "Action", "Details"],
    "maintenance-availability": ["Device", "IP", "Availability %", "Status"],
    "security-compliance": ["Timestamp", "Device", "Severity", "Type", "Detail"],
    "tracking-operations": ["Device", "Coverage %", "Freshness", "Sample Count"],
    "printer-operations": ["Device", "Status", "Page Count", "Timestamp"],
    "productivity": ["Device", "Application", "Category", "Usage (s)"],
}

_PREVIEW_STATUS_FIELD = "Status"


def _build_preview_rows(report_type, payload):
    """Reshape a report payload into flat row dicts keyed by _PREVIEW_COLUMNS."""
    if report_type == "executive":
        rows = []
        for item in (payload.get("top_problematic") or []):
            uptime = item.get("uptime") or 0
            status = "CRITICAL" if uptime < 90 else ("WARN" if uptime < 95 else "OK")
            rows.append({
                "Device": item.get("name", ""),
                "Type": item.get("type", ""),
                "IP": item.get("ip", ""),
                "Uptime %": uptime,
                "Status": status,
            })
        return rows

    if report_type == "alerts":
        rows = []
        for alert in (payload.get("alerts") or []):
            rows.append({
                "Timestamp": alert.get("timestamp", ""),
                "Device": alert.get("device_name", ""),
                "IP": alert.get("device_ip", ""),
                "Severity": alert.get("severity", ""),
                "Type": alert.get("event_type", ""),
                "Message": (alert.get("message") or "")[:120],
                "Resolved": "Yes" if alert.get("resolved") else "No",
            })
        return rows

    if report_type == "inventory-assets":
        rows = []
        for device in (payload.get("inventory_devices") or []):
            rows.append({
                "Device": device.get("name", ""),
                "IP": device.get("ip", ""),
                "Type": device.get("device_type", ""),
                "Site": device.get("site_name", "") or "",
                "Department": device.get("department_name", "") or "",
                "Status": device.get("status", ""),
            })
        return rows

    if report_type == "device-health":
        rows = []
        for dev_key, dev_data in (payload.get("time_series") or {}).items():
            points = dev_data.get("points") or []
            if not points:
                continue
            cpu_vals = [p.get("cpu") for p in points if p.get("cpu") is not None]
            mem_vals = [p.get("memory") for p in points if p.get("memory") is not None]
            disk_vals = [p.get("disk") for p in points if p.get("disk") is not None]
            rows.append({
                "Device": dev_data.get("device_name") or dev_key,
                "Avg CPU %": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else "",
                "Avg Mem %": round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else "",
                "Avg Disk %": round(sum(disk_vals) / len(disk_vals), 1) if disk_vals else "",
                "Points": len(points),
            })
        return rows

    if report_type == "network":
        rows = []
        for item in (payload.get("uptime_summary") or []):
            rows.append({
                "Device": item.get("device_name", ""),
                "Avg Uptime %": item.get("avg_uptime", ""),
                "Avg Latency (ms)": item.get("avg_latency_ms", ""),
                "Avg Packet Loss %": item.get("avg_packet_loss", ""),
            })
        return rows

    if report_type == "operational":
        rows = []
        for entry in (payload.get("audit_log") or []):
            rows.append({
                "Timestamp": entry.get("timestamp", ""),
                "Action": entry.get("action", ""),
                "Details": (entry.get("description") or "")[:120],
            })
        return rows

    if report_type == "maintenance-availability":
        rows = []
        for item in (payload.get("downtime_leaders") or []):
            avail = item.get("availability_pct")
            status = "CRITICAL" if (avail or 100) < 90 else ("WARN" if (avail or 100) < 99 else "OK")
            rows.append({
                "Device": item.get("device_name", ""),
                "IP": item.get("device_ip", ""),
                "Availability %": avail,
                "Status": status,
            })
        return rows

    if report_type == "security-compliance":
        rows = []
        for event in (payload.get("recent_alerts") or []):
            rows.append({
                "Timestamp": event.get("timestamp", ""),
                "Device": event.get("device_name", ""),
                "Severity": event.get("severity", ""),
                "Type": event.get("event_type", ""),
                "Detail": (event.get("message") or "")[:120],
            })
        return rows

    if report_type == "tracking-operations":
        rows = []
        for item in (payload.get("device_freshness") or []):
            rows.append({
                "Device": item.get("device_name", ""),
                "Coverage %": item.get("coverage_pct", ""),
                "Freshness": item.get("freshness_state", ""),
                "Sample Count": item.get("sample_count", ""),
            })
        return rows

    if report_type == "printer-operations":
        rows = []
        for item in (payload.get("printer_status") or []):
            rows.append({
                "Device": item.get("device_name", ""),
                "Status": item.get("status", ""),
                "Page Count": item.get("page_count_total", ""),
                "Timestamp": item.get("timestamp", ""),
            })
        return rows

    if report_type == "productivity":
        rows = []
        for dev_key, dev_data in (payload.get("app_breakdown") or {}).items():
            dev_name = dev_data.get("device_name") or dev_key
            for app in (dev_data.get("apps") or []):
                rows.append({
                    "Device": dev_name,
                    "Application": app.get("application_name", ""),
                    "Category": app.get("category", ""),
                    "Usage (s)": app.get("total_seconds", ""),
                })
        return rows

    return []


@reports_bp.route('/api/reports/<report_type>/preview', methods=['GET'])
def preview_report(report_type):
    """Return a flat, preview-ready payload for the enterprise table view.
    Read-only — never writes to DB."""
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    try:
        _validate_report_type(report_type)
        start_date, end_date = _parse_date_range(max_days=_max_days_for_report(report_type))
        device_ids = _parse_device_ids()
        severity = _parse_severity()
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)

    try:
        payload, row_count, _, cache_meta = _run_report(
            report_type,
            start_date,
            end_date,
            device_ids=device_ids,
            severity=severity,
        )
        payload = _decorate_report_payload(report_type, payload, start_date, end_date, row_count, cache_meta)
        columns = _PREVIEW_COLUMNS.get(report_type, ["Key", "Value"])
        rows = _build_preview_rows(report_type, payload)
        return jsonify({
            "report_type": report_type,
            "columns": columns,
            "rows": rows,
            "meta": payload.get("meta", {}),
        })
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    except Exception as exc:
        return _handle_report_exception(report_type, exc)


@reports_bp.route('/api/reports/<report_type>/export')
@require_permission('reports.export')
def export_report(report_type):
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    export_format = request.args.get('format', 'pdf').lower()
    _VALID_EXPORT_FORMATS = {'pdf', 'csv', 'xlsx'}
    if export_format not in _VALID_EXPORT_FORMATS:
        export_format = 'pdf'

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
        buf = export_report_buffer(payload, report_type, export_format=export_format)
        if export_format == 'csv':
            mimetype = 'text/csv'
            ext = 'csv'
        elif export_format == 'xlsx':
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            ext = 'xlsx'
        else:
            mimetype = 'application/pdf'
            ext = 'pdf'
        return send_file(
            buf,
            mimetype=mimetype,
            as_attachment=True,
            download_name=f'{filename}.{ext}',
        )
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    except Exception as exc:
        return _handle_report_exception(report_type, exc)


@reports_bp.route('/api/reports/<report_type>/export-jobs', methods=['POST'])
@require_permission('reports.export')
def create_export_job(report_type):
    if report_type == 'productivity' and not _is_productivity_report_enabled():
        return _productivity_disabled_response()

    export_params = _collect_params_from_request()
    export_format = 'pdf'  # PDF-only export

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
        session_snapshot = _current_session_snapshot()
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
                session_snapshot,
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


_VALID_FLEET_PARAMS = frozenset(("server", "workstation"))


def _parse_fleet_param() -> str | None:
    """
    Parse and validate the optional ?fleet= query param.
    Returns None (both fleets), "server", or "workstation".
    Raises ReportValidationError on invalid value.
    """
    fleet = request.args.get('fleet') or None
    if fleet is not None and fleet not in _VALID_FLEET_PARAMS:
        raise ReportValidationError(
            f"Invalid fleet value '{fleet}'. Must be 'server' or 'workstation'.", 400
        )
    return fleet


@reports_bp.route('/api/reports/enterprise-uptime', methods=['GET'])
def enterprise_uptime_report():
    """
    JSON summary of enterprise uptime/downtime data.  Read-only.
    Optional: ?fleet=server|workstation  filters to a single fleet.
    Optional: ?device_ids=1,2,3          filters to specific device IDs.
    """
    try:
        start_date, end_date = _parse_date_range(max_days=365)
        fleet = _parse_fleet_param()
        device_ids = _parse_device_ids()
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    try:
        from services.enterprise_report_service import build_enterprise_uptime_report
        data = build_enterprise_uptime_report(start_date=start_date, end_date=end_date, fleet=fleet, device_ids=device_ids)
        # Add narrative + intelligence annotations (Master Spec)
        try:
            from services.report_narrative_service import ReportNarrativeService
            svc = ReportNarrativeService()
            exec_n   = svc.generate_narrative('executive', data)
            srv_n    = svc.generate_narrative('server-fleet', data)
            ws_n     = svc.generate_narrative('tracked-fleet', data)
            data['narratives'] = {
                'executive':     exec_n,
                'server_fleet':  srv_n,
                'tracked_fleet': ws_n,
            }
            # Cross-report synthesis — fleet-level RAG status
            try:
                # Pull alert + security narratives from their individual reports if available
                alerts_n   = data.get('alerts_narrative')   # populated if pre-fetched
                security_n = data.get('security_narrative') # populated if pre-fetched
                data['cross_report'] = svc.synthesize_cross_report(
                    exec_n, alerts_n, security_n,
                    report_summary=data.get('summary'),
                )
            except Exception as exc:
                logger.debug("[EnterpriseUptime] cross_report synthesis skipped: %s", exc)
                data['cross_report'] = None
        except Exception as exc:
            logger.warning("[EnterpriseUptime] narrative failed: %s", exc)
            data['narratives'] = {}
            data['cross_report'] = None
        try:
            from services.report_intelligence_rules import ReportIntelligenceRules
            data['intelligence_annotations'] = ReportIntelligenceRules().annotate('enterprise', data)
        except Exception as exc:
            logger.warning("[EnterpriseUptime] intelligence annotation failed: %s", exc)
            data['intelligence_annotations'] = []
        return jsonify(data)
    except Exception as exc:
        logger.exception("[EnterpriseUptime] JSON build failed: %s", exc)
        return _json_error("Failed to build enterprise uptime report.", 500)
    finally:
        try:
            db.session.remove()
        except Exception:
            pass


@reports_bp.route('/api/reports/enterprise-uptime/pdf', methods=['GET'])
@require_permission('reports.export')
def enterprise_uptime_pdf():
    """
    Download a colour-formatted enterprise uptime/downtime PDF report.
    Optional: ?fleet=server|workstation  scopes PDF to a single fleet.
    Optional: ?device_ids=1,2,3          filters to specific device IDs.
    """
    try:
        start_date, end_date = _parse_date_range(max_days=365)
        fleet = _parse_fleet_param()
        device_ids = _parse_device_ids()
    except ReportValidationError as exc:
        return _json_error(str(exc), exc.status_code)
    try:
        from services.enterprise_report_service import build_enterprise_uptime_report
        from services.enterprise_pdf_service import generate_enterprise_pdf
        data = build_enterprise_uptime_report(start_date=start_date, end_date=end_date, fleet=fleet, device_ids=device_ids)
        # Enrich canonical rows with 9 new fields for 3-table PDF layout
        _interval = get_monitoring_interval()
        _enricher = ReportMetricsEnricher(_interval, start_date, end_date)
        if data.get("server_rows"):
            data["server_rows"] = _enricher.enrich(data["server_rows"])
        if data.get("tracked_rows"):
            data["tracked_rows"] = _enricher.enrich(data["tracked_rows"])
        buf = generate_enterprise_pdf(data, fleet=fleet or "all")
        timestamp = _utcnow().strftime('%Y%m%d_%H%M')
        label = fleet or "enterprise"
        filename = f'{label}_uptime_report_{timestamp}.pdf'
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as exc:
        logger.exception("[EnterpriseUptime] PDF generation failed: %s", exc)
        return _json_error("Failed to generate enterprise uptime PDF.", 500)
    finally:
        try:
            db.session.remove()
        except Exception:
            pass


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
