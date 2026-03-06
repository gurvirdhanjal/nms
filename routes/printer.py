"""
Printer Audit Routes — API endpoints for printer metrics and print job audit trail.
"""
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from datetime import datetime, timedelta
from extensions import db
from models.printer import PrinterMetrics, PrintJobAudit
from models.device import Device
from models.tracked_device import TrackedDevice
from services.snmp_service import snmp_service
from services.tracking_reconcile import normalize_mac

printer_bp = Blueprint('printer', __name__)


# ============================================================================
# UI Routes
# ============================================================================

@printer_bp.route('/printers', methods=['GET'])
def printers_list_page():
    """Render the printer list page."""
    return render_template('printers/list.html')


@printer_bp.route('/printer/<int:device_id>', methods=['GET'])
def printer_detail_page(device_id):
    """Render the printer detail page with live-tracking fallback safety."""
    device = Device.query.get(device_id)
    if device is None:
        tracked = TrackedDevice.query.get(device_id)
        if tracked is None:
            abort(404)

        tracked_mac = normalize_mac(getattr(tracked, 'mac_address', None))
        if tracked_mac:
            return redirect(url_for('tracking_bp.live_tracking', mac=tracked_mac))
        return redirect(url_for('tracking_bp.tracked_device_live', device_id=tracked.id, warn='no_mac'))

    normalized_mac = normalize_mac(getattr(device, 'macaddress', None))
    tracked_live_url = None
    warning_code = None
    if normalized_mac:
        tracked = TrackedDevice.query.filter_by(mac_address=normalized_mac).first()
        if tracked:
            tracked_live_url = url_for('tracking_bp.live_tracking', mac=normalized_mac)
        else:
            warning_code = 'no_live_device'
    else:
        warning_code = 'no_mac'

    return render_template(
        'printers/detail.html',
        device=device,
        tracked_live_url=tracked_live_url,
        warning_code=warning_code,
    )


# ============================================================================
# Printer Management
# ============================================================================

@printer_bp.route('/api/printers', methods=['GET'])
def list_printers():
    """List all printers with optional filtering by site and department."""
    site_id = request.args.get('site_id', type=int)
    department_id = request.args.get('department_id', type=int)
    
    query = Device.query.filter(Device.device_type.in_(['printer', 'print_server']))
    
    if site_id:
        query = query.filter_by(site_id=site_id)
    if department_id:
        query = query.filter_by(department_id=department_id)
    
    printers = query.order_by(Device.device_name).all()
    
    return jsonify({
        'status': 'ok',
        'data': [p.to_dict() for p in printers]
    })


@printer_bp.route('/api/printers/<int:device_id>', methods=['GET'])
def get_printer_details(device_id):
    """Get detailed information about a specific printer."""
    device = Device.query.get_or_404(device_id)
    
    if device.device_type not in ['printer', 'print_server']:
        return jsonify({'status': 'error', 'message': 'Device is not a printer'}), 400
    
    # Get latest metrics
    latest_metrics = (
        PrinterMetrics.query
        .filter_by(device_id=device_id)
        .order_by(PrinterMetrics.timestamp.desc())
        .first()
    )
    
    result = device.to_dict()
    result['latest_metrics'] = latest_metrics.to_dict() if latest_metrics else None
    
    return jsonify({'status': 'ok', 'data': result})


# ============================================================================
# SNMP-Based Printer Metrics
# ============================================================================

@printer_bp.route('/api/printer/<int:device_id>/poll', methods=['POST'])
def poll_printer_metrics(device_id):
    """Trigger an on-demand SNMP poll of printer metrics and store the result."""
    device = Device.query.get_or_404(device_id)

    community = device.snmp_community or 'public'
    version = device.snmp_version or 'v2c'
    port = device.snmp_port or 161

    # Normalize version string
    ver = version.replace('v', '') if version.startswith('v') else version

    raw = snmp_service.get_printer_metrics(device.device_ip, community, ver, port)

    if 'error' in raw:
        return jsonify({'status': 'error', 'message': raw['error'], 'error_code': raw.get('error_code')}), 502

    metric = PrinterMetrics(
        device_id=device_id,
        timestamp=datetime.utcnow(),
        status=raw.get('status'),
        status_code=raw.get('status_code'),
        toner_black=raw.get('toner_black'),
        toner_cyan=raw.get('toner_cyan'),
        toner_magenta=raw.get('toner_magenta'),
        toner_yellow=raw.get('toner_yellow'),
        paper_tray_status=raw.get('paper_tray_status'),
        page_count_total=raw.get('page_count_total'),
        page_count_color=raw.get('page_count_color'),
        page_count_bw=raw.get('page_count_bw'),
        job_queue_length=raw.get('job_queue_length'),
    )
    db.session.add(metric)
    db.session.commit()

    return jsonify({'status': 'ok', 'data': metric.to_dict()}), 201


@printer_bp.route('/api/printer/<int:device_id>/metrics', methods=['GET'])
def get_printer_metrics(device_id):
    """Get historical printer metrics for a device."""
    Device.query.get_or_404(device_id)

    hours = request.args.get('hours', 24, type=int)
    since = datetime.utcnow() - timedelta(hours=hours)

    metrics = (
        PrinterMetrics.query
        .filter_by(device_id=device_id)
        .filter(PrinterMetrics.timestamp >= since)
        .order_by(PrinterMetrics.timestamp.desc())
        .limit(500)
        .all()
    )

    return jsonify({
        'status': 'ok',
        'data': [m.to_dict() for m in metrics],
        'meta': {'device_id': device_id, 'hours': hours, 'count': len(metrics)}
    })


@printer_bp.route('/api/printer/<int:device_id>/latest', methods=['GET'])
def get_printer_latest(device_id):
    """Get the most recent printer metrics snapshot."""
    Device.query.get_or_404(device_id)

    latest = (
        PrinterMetrics.query
        .filter_by(device_id=device_id)
        .order_by(PrinterMetrics.timestamp.desc())
        .first()
    )

    if not latest:
        return jsonify({'status': 'ok', 'data': None, 'message': 'No metrics recorded yet'})

    return jsonify({'status': 'ok', 'data': latest.to_dict()})


# ============================================================================
# Print Job Audit Trail
# ============================================================================

@printer_bp.route('/api/printer/<int:device_id>/jobs', methods=['GET'])
def get_print_jobs(device_id):
    """Get print job history for a specific printer."""
    Device.query.get_or_404(device_id)

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 200)  # Cap at 200

    user_filter = request.args.get('user')
    ip_filter = request.args.get('source_ip')

    query = PrintJobAudit.query.filter_by(device_id=device_id)

    if user_filter:
        query = query.filter(PrintJobAudit.user_account.ilike(f'%{user_filter}%'))
    if ip_filter:
        query = query.filter(PrintJobAudit.source_ip == ip_filter)

    query = query.order_by(PrintJobAudit.submission_time.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'status': 'ok',
        'data': [j.to_dict() for j in pagination.items],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages,
        }
    })


@printer_bp.route('/api/printer/jobs/search', methods=['GET'])
def search_print_jobs():
    """Search print jobs across all printers (by user, IP, or printer name)."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    per_page = min(per_page, 200)

    query = PrintJobAudit.query

    user_filter = request.args.get('user')
    ip_filter = request.args.get('source_ip')
    printer_filter = request.args.get('printer_name')
    hours = request.args.get('hours', type=int)

    if user_filter:
        query = query.filter(PrintJobAudit.user_account.ilike(f'%{user_filter}%'))
    if ip_filter:
        query = query.filter(PrintJobAudit.source_ip == ip_filter)
    if printer_filter:
        query = query.filter(PrintJobAudit.printer_name.ilike(f'%{printer_filter}%'))
    if hours:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = query.filter(PrintJobAudit.submission_time >= since)

    query = query.order_by(PrintJobAudit.submission_time.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        'status': 'ok',
        'data': [j.to_dict() for j in pagination.items],
        'meta': {
            'page': page,
            'per_page': per_page,
            'total': pagination.total,
            'pages': pagination.pages,
        }
    })
