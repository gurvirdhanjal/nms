"""
Print Jobs API Routes — Comprehensive print job audit trail with filtering and pagination.
"""
from flask import Blueprint, request, jsonify, render_template
from datetime import datetime
from extensions import db
from models.printer import PrintJobAudit
from models.device import Device
from sqlalchemy import func

print_jobs_bp = Blueprint('print_jobs', __name__)


@print_jobs_bp.route('/print-jobs', methods=['GET'])
def print_jobs_list_page():
    """
    Render the print jobs list page (UI route).
    """
    return render_template('print_jobs/list.html')


@print_jobs_bp.route('/api/print-jobs', methods=['GET'])
def list_print_jobs():
    """
    List print jobs with comprehensive filtering and pagination.
    
    Query Parameters:
    - start_date: ISO format datetime (e.g., 2024-01-01T00:00:00)
    - end_date: ISO format datetime
    - user: Filter by user account (partial match)
    - printer_id: Filter by device ID
    - site_id: Filter by site
    - department_id: Filter by department
    - page: Page number (default: 1)
    - page_size: Items per page (default: 100, max: 500)
    """
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('page_size', 100, type=int)
    page_size = min(page_size, 500)  # Cap at 500
    
    # Build base query
    query = PrintJobAudit.query
    
    # Date filtering
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time >= start_dt)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid start_date format'}), 400
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time <= end_dt)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid end_date format'}), 400
    
    # User filtering
    user_filter = request.args.get('user')
    if user_filter:
        query = query.filter(PrintJobAudit.user_account.ilike(f'%{user_filter}%'))
    
    # Printer filtering
    printer_id = request.args.get('printer_id', type=int)
    if printer_id:
        query = query.filter_by(device_id=printer_id)
    
    # Site filtering - join with Device table
    site_id = request.args.get('site_id', type=int)
    if site_id:
        query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.site_id == site_id)
    
    # Department filtering - join with Device table
    department_id = request.args.get('department_id', type=int)
    if department_id:
        if not site_id:  # Only join if we haven't already
            query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.department_id == department_id)
    
    # Calculate total page count for filtered results
    total_pages_query = query.with_entities(func.sum(PrintJobAudit.page_count))
    total_page_count = total_pages_query.scalar() or 0
    
    # Order and paginate
    query = query.order_by(PrintJobAudit.submission_time.desc())
    pagination = query.paginate(page=page, per_page=page_size, error_out=False)
    
    return jsonify({
        'status': 'ok',
        'data': [j.to_dict() for j in pagination.items],
        'meta': {
            'page': page,
            'page_size': page_size,
            'total': pagination.total,
            'total_pages': pagination.pages,
            'total_page_count': total_page_count
        }
    })


@print_jobs_bp.route('/api/print-jobs/export', methods=['GET'])
def export_print_jobs():
    """
    Export print jobs to CSV format with same filtering as list endpoint.
    
    Query Parameters: Same as list_print_jobs
    """
    import csv
    from io import StringIO
    
    # Build query with same filters as list endpoint
    query = PrintJobAudit.query
    
    # Date filtering
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time >= start_dt)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid start_date format'}), 400
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time <= end_dt)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Invalid end_date format'}), 400
    
    # User filtering
    user_filter = request.args.get('user')
    if user_filter:
        query = query.filter(PrintJobAudit.user_account.ilike(f'%{user_filter}%'))
    
    # Printer filtering
    printer_id = request.args.get('printer_id', type=int)
    if printer_id:
        query = query.filter_by(device_id=printer_id)
    
    # Site filtering
    site_id = request.args.get('site_id', type=int)
    if site_id:
        query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.site_id == site_id)
    
    # Department filtering
    department_id = request.args.get('department_id', type=int)
    if department_id:
        if not site_id:
            query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.department_id == department_id)
    
    # Order by submission time
    query = query.order_by(PrintJobAudit.submission_time.desc())
    
    # Limit to prevent excessive exports
    jobs = query.limit(10000).all()
    
    # Generate CSV
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Job ID', 'User', 'Document Name', 'Printer Name', 
        'Submission Time', 'Completion Time', 'Page Count', 
        'Size (Bytes)', 'Status', 'Source IP', 'Collection Source'
    ])
    
    # Write data
    for job in jobs:
        writer.writerow([
            job.job_id,
            job.user_account,
            job.document_name,
            job.printer_name,
            job.submission_time.isoformat() if job.submission_time else '',
            job.completion_time.isoformat() if job.completion_time else '',
            job.page_count or 0,
            job.size_bytes or 0,
            job.status or '',
            job.source_ip or '',
            job.collection_source or ''
        ])
    
    # Return CSV response
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=print_jobs_export.csv'}
    )


@print_jobs_bp.route('/api/print-jobs/stats', methods=['GET'])
def get_print_job_stats():
    """
    Get statistics for print jobs with optional filtering.
    
    Returns:
    - total_jobs: Total number of jobs
    - total_pages: Total pages printed
    - unique_users: Number of unique users
    - date_range: First and last job timestamps
    """
    query = PrintJobAudit.query
    
    # Apply same filters as list endpoint
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    user_filter = request.args.get('user')
    printer_id = request.args.get('printer_id', type=int)
    site_id = request.args.get('site_id', type=int)
    department_id = request.args.get('department_id', type=int)
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time >= start_dt)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            query = query.filter(PrintJobAudit.submission_time <= end_dt)
        except ValueError:
            pass
    
    if user_filter:
        query = query.filter(PrintJobAudit.user_account.ilike(f'%{user_filter}%'))
    
    if printer_id:
        query = query.filter_by(device_id=printer_id)
    
    if site_id:
        query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.site_id == site_id)
    
    if department_id:
        if not site_id:
            query = query.join(Device, PrintJobAudit.device_id == Device.device_id)
        query = query.filter(Device.department_id == department_id)
    
    # Calculate statistics
    total_jobs = query.count()
    total_pages = query.with_entities(func.sum(PrintJobAudit.page_count)).scalar() or 0
    unique_users = query.with_entities(func.count(func.distinct(PrintJobAudit.user_account))).scalar() or 0
    
    # Get date range
    first_job = query.order_by(PrintJobAudit.submission_time.asc()).first()
    last_job = query.order_by(PrintJobAudit.submission_time.desc()).first()
    
    return jsonify({
        'status': 'ok',
        'data': {
            'total_jobs': total_jobs,
            'total_pages': int(total_pages),
            'unique_users': unique_users,
            'date_range': {
                'first': first_job.submission_time.isoformat() if first_job else None,
                'last': last_job.submission_time.isoformat() if last_job else None
            }
        }
    })
