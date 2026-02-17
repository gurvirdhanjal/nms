from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, send_file
from extensions import db
from services.device_monitor import DeviceMonitor
from datetime import datetime, timedelta

reports_bp = Blueprint('reports_bp', __name__, url_prefix='')
monitor = DeviceMonitor()


# ── Helpers ──────────────────────────────────────────────────────

def _auth_check():
    """Return error response if not logged in, else None."""
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None


def _parse_date_range():
    """Parse ?range=, ?start=, ?end= into (start_date, end_date)."""
    range_type = request.args.get('range', '24h')
    end_date = datetime.utcnow()

    custom_start = request.args.get('start')
    custom_end = request.args.get('end')
    if custom_start and custom_end:
        try:
            return datetime.fromisoformat(custom_start), datetime.fromisoformat(custom_end)
        except ValueError:
            pass

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

    return start_date, end_date


def _parse_device_ids():
    """Parse ?device_ids=1,2,3 into list of ints, or None."""
    raw = request.args.get('device_ids', '')
    if not raw:
        return None
    try:
        return [int(x.strip()) for x in raw.split(',') if x.strip()]
    except ValueError:
        return None


def _get_service():
    from services.reporting_service import ReportingService
    return ReportingService()


# ── Existing pages & APIs ────────────────────────────────────────

@reports_bp.route('/reports')
def reports_page():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    return render_template('reports.html')


@reports_bp.route('/api/device_statistics')
def get_device_statistics():
    err = _auth_check()
    if err:
        return err

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
    err = _auth_check()
    if err:
        return err

    date_str = request.args.get('date')
    if date_str:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        date = None

    report = monitor.get_daily_report(date)
    return jsonify(report)


@reports_bp.route('/api/device_history')
def get_device_history():
    err = _auth_check()
    if err:
        return err

    from models.scan_history import DeviceScanHistory

    device_ip = request.args.get('device_ip')
    hours = int(request.args.get('hours', 24))

    cutoff_time = datetime.utcnow() - timedelta(hours=hours)

    scans = DeviceScanHistory.query.filter(
        DeviceScanHistory.device_ip == device_ip,
        DeviceScanHistory.scan_timestamp >= cutoff_time
    ).order_by(DeviceScanHistory.scan_timestamp).all()

    history_data = [{
        'timestamp': scan.scan_timestamp.isoformat(),
        'status': scan.status,
        'latency': scan.ping_time_ms,
        'scan_type': scan.scan_type
    } for scan in scans]

    return jsonify(history_data)


# ── Existing report APIs (refactored) ────────────────────────────

@reports_bp.route('/api/reports/executive')
def get_executive_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    report = service.get_executive_fleet_health(start_date, end_date)
    return jsonify(report)


@reports_bp.route('/api/reports/operational')
def get_operational_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    report = service.get_operational_report(start_date, end_date)
    return jsonify(report)


# ── NEW: 4 report data endpoints ─────────────────────────────────

@reports_bp.route('/api/reports/device-health')
def get_device_health_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    device_ids = _parse_device_ids()
    report = service.get_device_health_report(device_ids, start_date, end_date)
    return jsonify(report)


@reports_bp.route('/api/reports/productivity')
def get_productivity_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    device_ids = _parse_device_ids()
    report = service.get_productivity_report(device_ids, start_date, end_date)
    return jsonify(report)


@reports_bp.route('/api/reports/network')
def get_network_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    device_ids = _parse_device_ids()
    report = service.get_network_performance_report(device_ids, start_date, end_date)
    return jsonify(report)


@reports_bp.route('/api/reports/alerts')
def get_alerts_report():
    err = _auth_check()
    if err:
        return err

    service = _get_service()
    start_date, end_date = _parse_date_range()
    device_ids = _parse_device_ids()
    severity = request.args.get('severity')
    report = service.get_alert_history_report(start_date, end_date, severity, device_ids)
    return jsonify(report)


# ── NEW: Universal export endpoint ───────────────────────────────

@reports_bp.route('/api/reports/<report_type>/export')
def export_report(report_type):
    """
    Export any report as CSV or Excel (Rule 3: server-side only).
    Usage: GET /api/reports/device-health/export?format=csv&range=24h
    """
    err = _auth_check()
    if err:
        return err

    export_format = request.args.get('format', 'csv').lower()
    if export_format not in ('csv', 'xlsx'):
        return jsonify({'error': 'format must be csv or xlsx'}), 400

    service = _get_service()
    start_date, end_date = _parse_date_range()
    device_ids = _parse_device_ids()
    severity = request.args.get('severity')

    # Generate the report data
    report_generators = {
        'device-health': lambda: service.get_device_health_report(device_ids, start_date, end_date),
        'productivity': lambda: service.get_productivity_report(device_ids, start_date, end_date),
        'network': lambda: service.get_network_performance_report(device_ids, start_date, end_date),
        'alerts': lambda: service.get_alert_history_report(start_date, end_date, severity, device_ids),
        'executive': lambda: service.get_executive_fleet_health(start_date, end_date),
        'operational': lambda: service.get_operational_report(start_date, end_date),
    }

    generator = report_generators.get(report_type)
    if not generator:
        return jsonify({'error': f'Unknown report type: {report_type}'}), 404

    report_data = generator()

    from services.export_service import export_to_csv, export_to_excel

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M')
    filename = f"{report_type}_report_{timestamp}"

    if export_format == 'csv':
        buf = export_to_csv(report_data, report_type)
        return send_file(
            buf,
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'{filename}.csv',
        )
    else:
        buf = export_to_excel(report_data, report_type)
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'{filename}.xlsx',
        )
