"""Central RBAC helpers for route authorization.

Current role model:
    - admin: full access
    - manager: department-scoped read/write + user management within dept
    - operator: department-scoped read/write (no user management)
    - viewer: department-scoped read-only
    - user: legacy standard user (maps to operator)
"""

from functools import wraps

from flask import current_app, flash, jsonify, redirect, request, session, url_for


ROLE_PERMISSIONS = {
    'admin': {'*'},
    'manager': {
        'dashboard.view',
        'reports.view', 'reports.export',
        'devices.view', 'devices.edit',
        'monitoring.view',
        'scanning.view', 'scanning.run',
        'tracking.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
        'file_transfer.view',
        'maintenance.view', 'maintenance.edit',
        'users.view',  # can view dept users
    },
    'operator': {
        'dashboard.view',
        'reports.view',
        'devices.view', 'devices.edit',
        'monitoring.view',
        'scanning.view', 'scanning.run',
        'tracking.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
        'file_transfer.view',
        'maintenance.view',
    },
    'viewer': {
        'dashboard.view',
        'reports.view',
        'devices.view',
        'monitoring.view',
        'tracking.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
    },
    # Legacy role — treated as operator
    'user': {
        'dashboard.view',
        'reports.view',
        'devices.view', 'devices.edit',
        'monitoring.view',
        'scanning.view',
        'tracking.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
        'file_transfer.view',
    },
}


def _is_api_request():
    if request.path.startswith('/api/'):
        return True
    accept_header = (request.headers.get('Accept') or '').lower()
    return 'application/json' in accept_header


def _unauthorized_response():
    if _is_api_request():
        return jsonify({'error': 'Unauthorized'}), 401
    return redirect(url_for('auth_bp.login'))


def _forbidden_response(message='Forbidden'):
    if _is_api_request():
        return jsonify({'error': message}), 403
    flash(message, 'danger')
    return redirect(url_for('monitoring_bp.dashboard'))


def current_role():
    return str(session.get('role') or '').strip().lower()


def _has_valid_api_key():
    provided_key = request.headers.get('X-API-Key')
    expected_key = current_app.config.get('MOBILE_API_KEY')
    return bool(provided_key and expected_key and provided_key == expected_key)


def has_permission(permission, role=None):
    role = (role or current_role()).lower()
    allowed = ROLE_PERMISSIONS.get(role, set())
    return '*' in allowed or permission in allowed


def require_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in') and not _has_valid_api_key():
            return _unauthorized_response()
        return func(*args, **kwargs)

    return wrapper


def require_role(*allowed_roles):
    normalized = {str(role).strip().lower() for role in allowed_roles}

    def decorator(func):
        @wraps(func)
        @require_login
        def wrapper(*args, **kwargs):
            if current_role() not in normalized:
                allowed_display = ', '.join(sorted(normalized))
                return _forbidden_response(f'Access denied. Allowed roles: {allowed_display}.')
            return func(*args, **kwargs)

        return wrapper

    return decorator


def require_permission(permission):
    def decorator(func):
        @wraps(func)
        @require_login
        def wrapper(*args, **kwargs):
            if not has_permission(permission):
                return _forbidden_response('Insufficient permissions.')
            return func(*args, **kwargs)

        return wrapper

    return decorator


def apply_department_scope(query, model=None):
    """
    Filter a SQLAlchemy query to only return records visible to the current user.

    Rules:
        admin  → sees everything (no filter)
        others → sees only records matching their department_id
    """
    from flask import session as flask_session

    role = current_role()
    if role == 'admin':
        return query

    dept_id = flask_session.get('department_id')
    if dept_id is None:
        # Fallback: try to load from DB if session doesn't have it
        user_id = flask_session.get('user_id')
        if user_id:
            from models.user import User
            user = User.query.get(user_id)
            dept_id = getattr(user, 'department_id', None) if user else None

    if dept_id is None:
        # No department assigned — show nothing for safety
        return query.filter(False)

    if model and hasattr(model, 'department_id'):
        return query.filter(model.department_id == dept_id)

    return query
