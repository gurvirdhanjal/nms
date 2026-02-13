from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from extensions import db
from services.device_monitor import DeviceMonitor
from datetime import datetime, timedelta

reports_bp = Blueprint('reports_bp', __name__, url_prefix='')
monitor = DeviceMonitor()

@reports_bp.route('/reports')
def reports_page():
    if 'logged_in' not in session:
        return redirect(url_for('auth_bp.login'))
    return render_template('reports.html')

@reports_bp.route('/api/device_statistics')
def get_device_statistics():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    device_ip = request.args.get('device_ip')
    period = request.args.get('period', '24h')  # 24h, 7d, 30d
    
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
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    date_str = request.args.get('date')
    if date_str:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        date = None
    
    report = monitor.get_daily_report(date)
    return jsonify(report)

@reports_bp.route('/api/device_history')
def get_device_history():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
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

@reports_bp.route('/api/reports/executive')
def get_executive_report():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    from services.reporting_service import ReportingService
    service = ReportingService()
    
    range_type = request.args.get('range', '30d')
    end_date = datetime.utcnow()
    
    if range_type == '24h':
        start_date = end_date - timedelta(hours=24)
    elif range_type == '7d':
        start_date = end_date - timedelta(days=7)
    elif range_type == '30d':
        start_date = end_date - timedelta(days=30)
    elif range_type == 'mtd':
        start_date = end_date.replace(day=1)
    else:
        # Default 30d
        start_date = end_date - timedelta(days=30)
        
    report = service.get_executive_fleet_health(start_date, end_date)
    return jsonify(report)

@reports_bp.route('/api/reports/operational')
def get_operational_report():
    if 'logged_in' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    from services.reporting_service import ReportingService
    service = ReportingService()
    
    # Same date logic as executive for now, can be factorized
    range_type = request.args.get('range', '30d')
    end_date = datetime.utcnow()
    
    if range_type == '24h':
        start_date = end_date - timedelta(hours=24)
    elif range_type == '7d':
        start_date = end_date - timedelta(days=7)
    elif range_type == '30d':
        start_date = end_date - timedelta(days=30)
    elif range_type == 'mtd':
        start_date = end_date.replace(day=1)
    else:
        start_date = end_date - timedelta(days=30)
        
    report = service.get_operational_report(start_date, end_date)
    return jsonify(report)