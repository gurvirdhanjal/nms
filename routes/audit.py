"""Audit log viewing routes for administrators."""

from flask import Blueprint, render_template, request, jsonify
from middleware.rbac import require_role
from models.audit_log import AuditLog
from extensions import db

audit_bp = Blueprint('audit', __name__)


@audit_bp.route('/audit_logs')
@require_role('admin')
def audit_logs():
    """
    Display audit logs with filtering and pagination.
    
    Query parameters:
        - action: Filter by action type (e.g., 'create', 'update', 'delete')
        - entity_type: Filter by entity type (e.g., 'device', 'user', 'site')
        - username: Filter by username
        - page: Page number for pagination (default: 1)
    """
    # Get filter parameters
    action_filter = request.args.get('action', '').strip()
    entity_type_filter = request.args.get('entity_type', '').strip()
    username_filter = request.args.get('username', '').strip()
    page = request.args.get('page', 1, type=int)
    
    # Build query with filters
    query = AuditLog.query
    
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)
    
    if entity_type_filter:
        query = query.filter(AuditLog.entity_type == entity_type_filter)
    
    if username_filter:
        query = query.filter(AuditLog.username.ilike(f'%{username_filter}%'))
    
    # Order by most recent first
    query = query.order_by(AuditLog.timestamp.desc())
    
    # Paginate results (50 per page)
    pagination = query.paginate(page=page, per_page=50, error_out=False)
    logs = pagination.items
    
    # Get unique values for filter dropdowns
    unique_actions = db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    unique_actions = [a[0] for a in unique_actions if a[0]]
    
    unique_entity_types = db.session.query(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type).all()
    unique_entity_types = [e[0] for e in unique_entity_types if e[0]]
    
    return render_template(
        'audit_logs.html',
        logs=logs,
        pagination=pagination,
        unique_actions=unique_actions,
        unique_entity_types=unique_entity_types,
        current_action=action_filter,
        current_entity_type=entity_type_filter,
        current_username=username_filter
    )


@audit_bp.route('/api/audit_logs')
@require_role('admin')
def api_audit_logs():
    """
    API endpoint for audit logs with filtering and pagination.
    
    Query parameters:
        - action: Filter by action type
        - entity_type: Filter by entity type
        - username: Filter by username
        - page: Page number (default: 1)
        - per_page: Results per page (default: 50, max: 100)
    
    Returns:
        JSON response with logs and pagination info
    """
    # Get filter parameters
    action_filter = request.args.get('action', '').strip()
    entity_type_filter = request.args.get('entity_type', '').strip()
    username_filter = request.args.get('username', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 100)  # Max 100 per page
    
    # Build query with filters
    query = AuditLog.query
    
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)
    
    if entity_type_filter:
        query = query.filter(AuditLog.entity_type == entity_type_filter)
    
    if username_filter:
        query = query.filter(AuditLog.username.ilike(f'%{username_filter}%'))
    
    # Order by most recent first
    query = query.order_by(AuditLog.timestamp.desc())
    
    # Paginate results
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items
    
    return jsonify({
        'success': True,
        'logs': [log.to_dict() for log in logs],
        'pagination': {
            'page': pagination.page,
            'per_page': pagination.per_page,
            'total': pagination.total,
            'pages': pagination.pages,
            'has_prev': pagination.has_prev,
            'has_next': pagination.has_next,
            'prev_num': pagination.prev_num,
            'next_num': pagination.next_num
        }
    })
