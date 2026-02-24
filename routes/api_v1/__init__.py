"""
REST API v1 Blueprint — Phase 1 MVP

Versioned API namespace at /api/v1/ with API key authentication.
Provides consistent JSON responses with pagination support.
"""
from flask import Blueprint, request, jsonify, current_app
from functools import wraps
from datetime import datetime, timedelta
from extensions import db
from models.device import Device
from models.site import Site
from models.printer import PrinterMetrics, PrintJobAudit
from models.server_health import ServerHealthLog

api_v1_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# ─────────────────────────────────────────────
# API Key Authentication
# ─────────────────────────────────────────────

def require_api_key(f):
    """Decorator to require X-API-Key header for all v1 endpoints."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        expected_key = current_app.config.get('MOBILE_API_KEY')

        if not expected_key:
            return _error('API key not configured on server. Set MOBILE_API_KEY in .env', 500)
        if not api_key or api_key != expected_key:
            return _error('Invalid or missing API key', 401)

        return f(*args, **kwargs)
    return wrapper


# Apply auth to all endpoints in this blueprint
@api_v1_bp.before_request
def check_api_key():
    api_key = request.headers.get('X-API-Key')
    expected_key = current_app.config.get('MOBILE_API_KEY')
    if not expected_key:
        return _error('API key not configured on server', 500)
    if not api_key or api_key != expected_key:
        return _error('Invalid or missing API key', 401)


# ─────────────────────────────────────────────
# Response Helpers
# ─────────────────────────────────────────────

def _ok(data, meta=None, status=200):
    resp = {'status': 'ok', 'data': data}
    if meta:
        resp['meta'] = meta
    return jsonify(resp), status


def _error(message, status=400):
    return jsonify({'status': 'error', 'message': message}), status


def _paginate(query, schema_fn=None):
    """Apply pagination to a query and return paginated response."""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = pagination.items

    data = [schema_fn(i) if schema_fn else i.to_dict() for i in items]
    meta = {
        'page': page,
        'per_page': per_page,
        'total': pagination.total,
        'pages': pagination.pages,
    }
    return _ok(data, meta)


# ─────────────────────────────────────────────
# DEVICES
# ─────────────────────────────────────────────

@api_v1_bp.route('/devices', methods=['GET'])
def list_devices():
    """List all devices with optional filtering."""
    query = Device.query

    # Filters
    site_id = request.args.get('site_id', type=int)
    device_type = request.args.get('type')
    is_monitored = request.args.get('monitored')

    if site_id:
        query = query.filter_by(site_id=site_id)
    if device_type:
        query = query.filter(Device.device_type.ilike(f'%{device_type}%'))
    if is_monitored is not None:
        query = query.filter_by(is_monitored=is_monitored.lower() == 'true')

    query = query.order_by(Device.device_name)
    return _paginate(query)


@api_v1_bp.route('/devices/<int:device_id>', methods=['GET'])
def get_device(device_id):
    """Get a single device by ID."""
    device = Device.query.get_or_404(device_id)
    return _ok(device.to_dict())


@api_v1_bp.route('/devices/<int:device_id>/metrics', methods=['GET'])
def get_device_metrics(device_id):
    """Get recent server health metrics for a device."""
    Device.query.get_or_404(device_id)
    hours = request.args.get('hours', 24, type=int)
    since = datetime.utcnow() - timedelta(hours=hours)

    metrics = (
        ServerHealthLog.query
        .filter_by(device_id=device_id)
        .filter(ServerHealthLog.timestamp >= since)
        .order_by(ServerHealthLog.timestamp.desc())
        .limit(500)
        .all()
    )
    return _ok(
        [m.to_dict() for m in metrics],
        {'device_id': device_id, 'hours': hours, 'count': len(metrics)}
    )


@api_v1_bp.route('/devices/<int:device_id>/maintenance', methods=['POST'])
def set_maintenance(device_id):
    """Toggle maintenance mode for a device."""
    device = Device.query.get_or_404(device_id)
    data = request.get_json() or {}
    mode = data.get('maintenance_mode', True)
    device.maintenance_mode = bool(mode)
    db.session.commit()
    return _ok({'device_id': device_id, 'maintenance_mode': device.maintenance_mode})


# ─────────────────────────────────────────────
# SITES
# ─────────────────────────────────────────────

@api_v1_bp.route('/sites', methods=['GET'])
def list_sites():
    """List all sites."""
    sites = Site.query.order_by(Site.site_name).all()
    return _ok([s.to_dict() for s in sites])


@api_v1_bp.route('/sites/<int:site_id>', methods=['GET'])
def get_site(site_id):
    """Get a single site with device count."""
    site = Site.query.get_or_404(site_id)
    return _ok(site.to_dict())


# ─────────────────────────────────────────────
# PRINTER METRICS
# ─────────────────────────────────────────────

@api_v1_bp.route('/printers/<int:device_id>/latest', methods=['GET'])
def get_printer_latest(device_id):
    """Get the most recent printer metrics snapshot."""
    Device.query.get_or_404(device_id)
    latest = (
        PrinterMetrics.query
        .filter_by(device_id=device_id)
        .order_by(PrinterMetrics.timestamp.desc())
        .first()
    )
    return _ok(latest.to_dict() if latest else None)


@api_v1_bp.route('/printers/<int:device_id>/jobs', methods=['GET'])
def get_printer_jobs(device_id):
    """Get paginated print job audit for a device."""
    Device.query.get_or_404(device_id)
    query = PrintJobAudit.query.filter_by(device_id=device_id).order_by(PrintJobAudit.submission_time.desc())
    return _paginate(query)


# ─────────────────────────────────────────────
# HEALTH / STATUS
# ─────────────────────────────────────────────

@api_v1_bp.route('/status', methods=['GET'])
def api_status():
    """Health check endpoint."""
    return _ok({
        'version': 'v1',
        'timestamp': datetime.utcnow().isoformat(),
        'status': 'healthy',
    })
