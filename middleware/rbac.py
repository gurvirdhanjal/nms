"""Central RBAC helpers for route authorization.

Current role model:
    - admin: full access
    - manager: department-scoped read/write + user management within dept
    - operator: department-scoped read/write (no user management)
    - viewer: department-scoped read-only
    - user: legacy standard user (maps to operator)
"""

import secrets
import threading
import time
import logging
from functools import wraps

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for
from extensions import db

logger = logging.getLogger(__name__)

ROLE_PERMISSIONS = {
    'admin': {'*'},
    'manager': {
        'dashboard.view',
        'reports.view', 'reports.export',
        'devices.view', 'devices.edit',
        'monitoring.view',
        'scanning.view', 'scanning.run',
        'tracking.view',
        'tracking.history.view',
        'tracking.device.archive',
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
        'tracking.history.view',
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
        'tracking.history.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
    },
    'user': {
        'dashboard.view',
        'reports.view',
        'devices.view', 'devices.edit',
        'monitoring.view',
        'scanning.view',
        'tracking.view',
        'tracking.history.view',
        'snmp.view',
        'server_metrics.view',
        'service_checks.view',
        'file_transfer.view',
    },
}

ENDPOINT_PERMISSIONS = {
    # Public endpoints (no authentication required)
    "auth_bp.login": "public",
    "auth_bp.register": "public",
    "auth_bp.forgot_password": "public",
    "auth_bp.validate_otp": "public",
    "auth_bp.reset_password": "public",
    "agent_bp.receive_metrics": "public",  # Handled by agent token checks
    
    # Device endpoints (write operations require devices.edit)
    "devices_bp.check_connectivity": "scanning.run",
    "devices_bp.save_device": "devices.edit",
    "devices_bp.toggle_device_monitoring": "devices.edit",
    "devices_bp.bulk_add_devices": "devices.edit",
    "devices_bp.bulk_delete_devices": "devices.edit",
    "devices_bp.update_device_type": "devices.edit",
    "devices_bp.update_device": "devices.edit",
    
    # Scanning endpoints (require scanning.run)
    "scanning_bp.scan_network": "scanning.run",
    "scanning_bp.stop_scan": "scanning.run",
    "scanning_bp.ping_device": "scanning.run",
    "scanning_bp.scan_ports": "scanning.run",
    "scanning_bp.add_to_inventory": "scanning.run",
    "scanning_bp.start_discovery": "scanning.run",
    
    # Reports endpoints
    "reports_bp.create_export_job": "reports.export",
    
    # User Management endpoints (admin only)
    "user_management_bp.save_user": "admin",
    "user_management_bp.toggle_user_status": "admin",
    "user_management_bp.bulk_delete_users": "admin",
    
    # Tracking endpoints
    "tracking_bp.api_toggle_mic": "tracking.view",
    "tracking_bp.api_toggle_camera": "tracking.view",
    "tracking_bp.api_stop_camera": "tracking.view",
    "tracking_bp.api_scan_devices": "scanning.run",
    "tracking_bp.api_save_device": "devices.edit",
    "tracking_bp.api_delete_device": "tracking.device.archive",
    "tracking_bp.api_archive_device": "tracking.device.archive",
    "tracking_bp.api_cleanup_stale_devices": "tracking.device.archive",
    "tracking_bp.api_sync_ips": "devices.edit",
    "tracking_bp.api_tracking_sync": "public",
    "tracking_bp.api_ingest_restricted_site_events": "public",
    "tracking_bp.api_update_restricted_sites_policy": "admin",
    "tracking_bp.api_toggle_device_maintenance": "maintenance.edit",
    "tracking_bp.device_history": "tracking.history.view",
    "tracking_bp.api_history_summary_v2": "tracking.history.view",
    "tracking_bp.api_history_activity_v2": "tracking.history.view",
    "tracking_bp.api_history_resources_v2": "tracking.history.view",
    "tracking_bp.api_history_applications_v2": "tracking.history.view",
    "tracking_bp.api_history_integrity_v2": "tracking.history.view",
    "tracking_bp.api_device_restricted_alerts": "tracking.history.view",
    "tracking_bp.workstation_monitor": "tracking.history.view",
    "tracking_bp.api_workstation_overview": "tracking.history.view",
    "tracking_bp.api_workstation_reports": "tracking.history.view",
    "tracking_bp.api_workstation_availability": "tracking.history.view",
    "tracking_bp.api_workstation_anomalies": "tracking.history.view",
    "tracking_bp.api_activity_history": "tracking.history.view",
    "tracking_bp.api_resource_history": "tracking.history.view",
    "tracking_bp.api_application_history": "tracking.history.view",
    "tracking_bp.api_tracking_history_purge_request": "tracking.history.purge",
    "tracking_bp.api_tracking_history_purge_confirm": "tracking.history.purge",

    # Device Console endpoints
    "device_console_bp.get_device_website_policy": "tracking.history.view",
    "device_console_bp.add_device_website_policy": "devices.edit",
    "device_console_bp.remove_device_website_policy": "devices.edit",
    "device_console_bp.get_device_alerts": "tracking.history.view",
    "device_console_bp.acknowledge_device_alert": "devices.edit",
    "device_console_bp.device_policy_history_redirect": "tracking.history.view",
    
    # File Transfer endpoints
    "file_transfer_bp.connect_to_client": "file_transfer.view",
    "file_transfer_bp.disconnect_client": "file_transfer.view",
    "file_transfer_bp.list_client_files": "file_transfer.view",
    "file_transfer_bp.download_from_client": "file_transfer.view",
    "file_transfer_bp.upload_to_client": "admin",
    "file_transfer_bp.create_client_folder": "admin",
    "file_transfer_bp.delete_client_file": "admin",
    "file_transfer_bp.list_local_files": "file_transfer.view",
    "file_transfer_bp.download_local_file": "file_transfer.view",
    "file_transfer_bp.upload_local_file": "admin",
    "file_transfer_bp.transfer_between_systems": "admin",
    
    # Dashboard alert endpoints (require devices.edit)
    "dashboard_bp.acknowledge_alert": "devices.edit",
    "dashboard_bp.resolve_alert": "devices.edit",
    
    # SNMP endpoints
    "snmp_bp.save_snmp_config": "devices.edit",
    "snmp_bp.poll_interface_counters": "snmp.view",
    "snmp_bp.poll_all_devices": "snmp.view",
    
    # Service Checks endpoints
    "service_checks_bp.check_batch": "service_checks.view",
    
    # Maintenance endpoints (require maintenance.edit)
    "maintenance_bp.run_cleanup": "maintenance.edit",
    "maintenance_bp.run_aggregation": "maintenance.edit",
    "maintenance_bp.run_all_maintenance": "maintenance.edit",
    "maintenance_bp.toggle_maintenance": "maintenance.edit",
    
    # Switch Discovery endpoints
    "switch_discovery_bp.discover_switches": "scanning.run",
    
    # Discovery Settings endpoints (admin only)
    "discovery_settings_bp.update_settings": "admin",
    "discovery_settings_bp.trigger_heavy": "scanning.run",
    
    # Site endpoints (admin only)
    "sites.create_site": "admin",
    "sites.update_site": "admin",
    "sites.delete_site": "admin",
    "sites.assign_devices_to_site": "admin",
    "sites.unassign_devices_from_site": "admin",
    
    # Printer endpoints
    "printer.poll_printer_metrics": "snmp.view",
    
    # Department endpoints (manager or devices.edit)
    "departments.create_department": "manager",
    "departments.update_department": "manager",
    "departments.delete_department": "manager",
    "departments.assign_devices_to_department": "devices.edit",
    "departments.unassign_devices_from_department": "devices.edit",
    
    # Subnet endpoints (admin only)
    "subnets.add_subnet": "admin",
    "subnets.delete_subnet": "admin",
    
    # API v1 endpoints
    "api_v1.set_maintenance": "maintenance.edit"
}

_UI_DEFAULT_CAPABILITIES = {
    'dashboard': False,
    'devices': False,
    'sites': False,
    'departments': False,
    'subnets': False,
    'scanner': False,
    'reports': False,
    'tracking': False,
    'maintenance': False,
    'discovery': False,
    'users': False,
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


def _safe_int(value):
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_scope_ids():
    site_id = _safe_int(session.get('site_id'))
    department_id = _safe_int(session.get('department_id'))
    user_id = _safe_int(session.get('user_id'))

    if (site_id is None or department_id is None) and user_id is not None:
        from models.user import User
        user = User.query.get(user_id)
        if user is not None:
            if site_id is None:
                site_id = _safe_int(getattr(user, 'site_id', None))
            if department_id is None:
                department_id = _safe_int(getattr(user, 'department_id', None))
    return site_id, department_id


def build_scope_context(role=None):
    normalized_role = str(role or current_role() or 'guest').strip().lower() or 'guest'
    site_id, department_id = _resolve_scope_ids()

    if normalized_role in {'', 'guest', 'admin'}:
        return {
            'role': normalized_role or 'guest',
            'scope_type': 'global',
            'scope_key': 'global',
            'scope_label': 'Global',
            'site_id': None,
            'department_id': None,
        }

    if normalized_role == 'manager':
        scope_name = 'Unassigned'
        if site_id is not None:
            from models.site import Site
            site = Site.query.get(site_id)
            scope_name = (site.site_name if site else f'Site {site_id}') or 'Unassigned'
        return {
            'role': normalized_role,
            'scope_type': 'site',
            'scope_key': f"site:{site_id if site_id is not None else 'none'}",
            'scope_label': f'Site — {scope_name}',
            'site_id': site_id,
            'department_id': department_id,
        }

    # Default non-admin operator/viewer/user scope to department.
    scope_name = 'Unassigned'
    if department_id is not None:
        from models.department import Department
        department = Department.query.get(department_id)
        scope_name = (department.name if department else f'Department {department_id}') or 'Unassigned'
    return {
        'role': normalized_role,
        'scope_type': 'department',
        'scope_key': f"department:{department_id if department_id is not None else 'none'}",
        'scope_label': f'Department — {scope_name}',
        'site_id': site_id,
        'department_id': department_id,
    }


def build_ui_capabilities(role=None):
    normalized_role = str(role or current_role() or 'guest').strip().lower() or 'guest'
    capabilities = dict(_UI_DEFAULT_CAPABILITIES)

    capabilities['dashboard'] = has_permission('dashboard.view', normalized_role)
    capabilities['devices'] = has_permission('devices.view', normalized_role)
    capabilities['scanner'] = has_permission('scanning.view', normalized_role)
    capabilities['reports'] = has_permission('reports.view', normalized_role)
    capabilities['tracking'] = has_permission('tracking.view', normalized_role)
    capabilities['maintenance'] = has_permission('maintenance.view', normalized_role)
    capabilities['users'] = normalized_role == 'admin' or has_permission('users.view', normalized_role)

    # Route-level RBAC for these pages is currently role-based in practice.
    capabilities['sites'] = normalized_role in {'admin', 'manager'}
    capabilities['departments'] = normalized_role in {'admin', 'manager'}
    capabilities['subnets'] = normalized_role in {'admin', 'manager'}
    capabilities['discovery'] = normalized_role in {'admin', 'manager'}
    return capabilities


def get_ui_rbac_context():
    if hasattr(request, '_ui_rbac_context'):
        return request._ui_rbac_context
    if hasattr(g, '_ui_rbac_context'):
        return g._ui_rbac_context

    role = str(current_role() or 'guest').strip().lower() or 'guest'
    scope = build_scope_context(role)
    context = {
        'role': role,
        'scope_key': scope['scope_key'],
        'scope_label': scope['scope_label'],
        'scope_type': scope['scope_type'],
        'capabilities': build_ui_capabilities(role),
    }
    request._ui_rbac_context = context
    g._ui_rbac_context = context
    return context


def current_scope_cache_fragment():
    scope = get_ui_rbac_context()
    return f"{scope.get('role', 'guest')}:{scope.get('scope_key', 'global')}"


def require_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug(
            "[RBAC] require_login logged_in=%s has_api_key=%s endpoint=%s",
            session.get('logged_in'),
            _has_valid_api_key(),
            request.endpoint,
        )
        if not session.get('logged_in') and not _has_valid_api_key():
            logger.debug("[RBAC] require_login unauthorized endpoint=%s", request.endpoint)
            return _unauthorized_response()
        logger.debug("[RBAC] require_login passed endpoint=%s", request.endpoint)
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


def has_permission_for_endpoint():
    """
    Check if current user has permission for the requested endpoint.
    
    Returns:
        True if user has permission, False otherwise
    """
    endpoint = request.endpoint
    if not endpoint:
        return True
    
    # Public endpoints (no authentication required)
    req_perm = ENDPOINT_PERMISSIONS.get(endpoint)
    if req_perm == "public":
        return True
    
    # API endpoints with API key
    if endpoint.startswith('api_v1.'):
        if _has_valid_api_key():
            return True
        req_perm = req_perm or 'devices.edit'
    
    # Default unmapped write endpoints to admin-only for security
    if not req_perm and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        req_perm = "admin"
    
    # No permission required for GET requests to unmapped endpoints
    if not req_perm:
        return True
    
    # Check admin role for admin-only endpoints
    if req_perm == "admin":
        return current_role() == "admin"
    
    # Check permission for other endpoints
    return has_permission(req_perm)

def enforce_write_permission():
    """
    Global guard: all write operations (POST, PUT, PATCH, DELETE) require mapped permission.
    
    This function is called by the @app.before_request handler in app.py.
    It checks if the current request is a write operation and if the user has permission.
    
    Raises:
        403 Forbidden if user lacks permission for write operation
    """
    # Skip for static files and public routes
    if request.endpoint and (
        request.endpoint.startswith('static') or
        request.endpoint in ['auth_bp.login', 'auth_bp.register', 'auth_bp.forgot_password', 
                            'auth_bp.validate_otp', 'auth_bp.reset_password']
    ):
        return
    
    # Enforce write permission for all write operations
    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        if not has_permission_for_endpoint():
            # Return JSON error for API requests
            if _is_api_request():
                from flask import abort
                abort(403)
            # Flash message and redirect for web requests
            flash('You do not have permission to perform this action.', 'danger')
            from flask import redirect, url_for
            return redirect(url_for('monitoring_bp.dashboard'))


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


# ============================================================================
# OPTIMIZATION: Request-level caching helper for department IDs
# ============================================================================

def _get_department_ids_for_site(site_id):
    """
    Get department IDs for a site with request-level caching.
    
    This avoids repeated queries for department IDs within the same request.
    Cache is stored in Flask's g object which is request-scoped.
    
    Args:
        site_id: The site ID to get departments for
    
    Returns:
        List of department IDs
    """
    from models.department import Department
    
    cache_key = f'_dept_ids_{site_id}'
    
    # Check if already cached in this request
    if hasattr(g, cache_key):
        return getattr(g, cache_key)
    
    # Query and cache the result
    dept_ids = [d[0] for d in db.session.query(Department.id).filter(Department.site_id == site_id).all()]
    setattr(g, cache_key, dept_ids)
    
    return dept_ids


def scoped_query(model):
    """
    Return a SQLAlchemy query filtered by the current user's scope.
    
    OPTIMIZED: Uses request-level caching and optimized queries with subqueries
    instead of N+1 query patterns.
    
    Scoping Rules:
    - Admin: No filtering (sees everything)
    - Manager: Filter by site_id (sees all departments in their site)
    - Operator: Filter by department_id (sees only their department)
    - Viewer: Filter by department_id (sees only their department)
    
    Args:
        model: SQLAlchemy model class (Device, Department, Site, User, etc.)
    
    Returns:
        Filtered query object
    """
    from flask import session as flask_session
    from models.device import Device
    from models.department import Department
    from models.site import Site
    from models.user import User
    
    role = current_role()
    
    # Admin sees everything
    if role == 'admin':
        return model.query
    
    # Get user's scope from session
    user_id = flask_session.get('user_id')
    site_id = flask_session.get('site_id')
    department_id = flask_session.get('department_id')
    
    # Fallback: load from DB if session missing
    if (site_id is None or department_id is None) and user_id:
        user = User.query.get(user_id)
        if user:
            site_id = user.site_id
            department_id = user.department_id
    
    # Manager: scope by site_id
    if role == 'manager':
        if site_id is None:
            return model.query.filter(False)  # No site assigned, show nothing
        
        # OPTIMIZATION: Use cached department IDs to avoid repeated queries
        dept_ids = _get_department_ids_for_site(site_id)
        
        # For Device model: include devices in site OR in departments within site
        if model == Device:
            return model.query.filter(
                db.or_(
                    Device.site_id == site_id,
                    Device.department_id.in_(dept_ids) if dept_ids else False
                )
            )
        
        # For Department model: departments in their site
        if model == Department:
            return model.query.filter(Department.site_id == site_id)
        
        # For Site model: only their site
        if model == Site:
            return model.query.filter(Site.id == site_id)
        
        # For User model: users in their site or departments within site
        if model == User:
            return model.query.filter(
                db.or_(
                    User.site_id == site_id,
                    User.department_id.in_(dept_ids) if dept_ids else False
                )
            )
        
        # For other models with site_id
        if hasattr(model, 'site_id'):
            return model.query.filter(model.site_id == site_id)
        
        # For other models with department_id
        if hasattr(model, 'department_id'):
            return model.query.filter(model.department_id.in_(dept_ids)) if dept_ids else model.query.filter(False)
    
    # Operator/Viewer: scope by department_id
    if role in ['operator', 'viewer']:
        if department_id is None:
            return model.query.filter(False)  # No department assigned, show nothing
        
        # For models with department_id
        if hasattr(model, 'department_id'):
            return model.query.filter(model.department_id == department_id)
        
        # For Department model: only their department
        if model == Department:
            return model.query.filter(Department.id == department_id)
        
        # For Site model: only the site their department belongs to
        if model == Site:
            dept = Department.query.get(department_id)
            if dept and dept.site_id:
                return model.query.filter(Site.id == dept.site_id)
            return model.query.filter(False)
        
        # For User model: users in their department
        if model == User:
            return model.query.filter(User.department_id == department_id)
    
    # Default: show nothing for safety
    return model.query.filter(False)


# ============================================================================
# PHASE 4: Agent Token Authentication
# ============================================================================

def generate_agent_token():
    """
    Generate a secure random token for agent authentication.
    
    Returns:
        str: A URL-safe random token (32 bytes = ~43 characters)
    """
    return secrets.token_urlsafe(32)


def validate_agent_token(token):
    """
    Validate an agent token and return the associated device.
    
    Args:
        token: The token from X-Agent-Token header
    
    Returns:
        Device object if valid, None otherwise
    """
    from models.device import Device
    
    if not token:
        return None
    
    device = Device.query.filter(Device.agent_token == token).first()
    return device if device else None


def _normalize_agent_identity_value(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _resolve_agent_device_for_shared_bootstrap(payload):
    from models.device import Device

    if not isinstance(payload, dict):
        return None

    hostname = _normalize_agent_identity_value(payload.get('hostname'))
    payload_ip = _normalize_agent_identity_value(payload.get('ip_address') or payload.get('ip'))
    if payload_ip and payload_ip.startswith('127.'):
        payload_ip = None

    candidates = []
    if hostname:
        hostname_lc = hostname.lower()
        candidates = Device.query.filter(
            db.or_(
                db.func.lower(Device.hostname) == hostname_lc,
                db.func.lower(Device.device_name) == hostname_lc,
            )
        ).all()

    if not candidates and payload_ip:
        candidates = Device.query.filter(Device.device_ip == payload_ip).all()

    if not candidates:
        return None

    if payload_ip:
        ip_matches = [device for device in candidates if (device.device_ip or '').strip() == payload_ip]
        if len(ip_matches) == 1:
            return ip_matches[0]
        if len(ip_matches) > 1:
            return None

    if len(candidates) == 1:
        return candidates[0]

    agent_mode_matches = [
        device
        for device in candidates
        if (device.monitoring_mode or '').strip().lower() == 'agent'
    ]
    if len(agent_mode_matches) == 1:
        return agent_mode_matches[0]

    token_matches = [device for device in candidates if (device.agent_token or '').strip()]
    if len(token_matches) == 1:
        return token_matches[0]

    return None


def _try_shared_agent_bootstrap(token):
    if not token:
        return None

    allow_bootstrap = bool(current_app.config.get('AGENT_ALLOW_SHARED_TOKEN_BOOTSTRAP', False))
    shared_key = (current_app.config.get('API_KEY') or '').strip()
    if not allow_bootstrap or not shared_key or token != shared_key:
        return None

    payload = request.get_json(silent=True) or {}
    device = _resolve_agent_device_for_shared_bootstrap(payload)
    if not device:
        current_app.logger.warning(
            "Agent shared-token bootstrap rejected: unable to map payload identity "
            "(hostname=%s ip=%s remote_addr=%s)",
            payload.get('hostname'),
            payload.get('ip_address') or payload.get('ip'),
            request.remote_addr,
        )
        return None

    assigned_token = (device.agent_token or '').strip()
    if not assigned_token:
        assigned_token = generate_agent_token()
        device.agent_token = assigned_token
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "Failed assigning bootstrap agent token for device_id=%s",
                device.device_id,
            )
            return None

    request.agent_bootstrap_used = True
    request.agent_bootstrap_assigned_token = assigned_token
    request.agent_auth_mode = 'shared_bootstrap'

    current_app.logger.warning(
        "Accepted legacy shared agent token bootstrap for device_id=%s hostname=%s "
        "remote_addr=%s. Disable AGENT_ALLOW_SHARED_TOKEN_BOOTSTRAP after rollout.",
        device.device_id,
        payload.get('hostname'),
        request.remote_addr,
    )
    return device



def require_agent_token(func):
    """
    Decorator to require valid agent token for agent endpoints.
    
    Extracts X-Agent-Token header (preferred) or Authorization: Bearer
    token (legacy compatibility), validates it, and stores the device in
    request.agent_device for use in the endpoint.
    
    Returns 401 for missing or invalid tokens.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get('X-Agent-Token')
        if token and token.lower().startswith('bearer '):
            token = token[7:].strip()

        if not token:
            auth_header = request.headers.get('Authorization', '')
            if auth_header and auth_header.lower().startswith('bearer '):
                token = auth_header[7:].strip()

        device = validate_agent_token(token)
        if not device:
            device = _try_shared_agent_bootstrap(token)
        
        if not device:
            if _is_api_request():
                return jsonify({'error': 'Invalid or missing agent token'}), 401
            return _unauthorized_response()
        
        # Store device in request context for use in endpoint
        request.agent_device = device
        return func(*args, **kwargs)
    
    return wrapper


# ============================================================================
# PHASE 5: Session Hardening
# ============================================================================

def validate_session_for_write():
    """
    Validate that session variables match database for critical write operations.

    OPTIMIZED: Uses request-level caching to avoid repeated DB queries within
    the same request.

    This prevents session manipulation attacks where an attacker modifies
    session cookies to escalate privileges or access other scopes.

    Returns:
        True if session is valid, False otherwise
    """
    from flask import session as flask_session
    from models.user import User
    import logging

    logger = logging.getLogger(__name__)

    # OPTIMIZATION: Check if already validated in this request
    if hasattr(g, '_session_validated'):
        return g._session_validated

    user_id = flask_session.get('user_id')
    if not user_id:
        logger.warning("Session validation failed: no user_id in session")
        g._session_validated = False
        return False

    # Load user from database
    user = User.query.get(user_id)
    if not user:
        logger.warning(f"Session validation failed: user {user_id} not found in database")
        g._session_validated = False
        return False

    # Validate critical session variables
    session_role = flask_session.get('role')
    if session_role != user.role:
        logger.warning(
            f"Session role mismatch for user {user_id}: "
            f"session={session_role}, db={user.role}"
        )
        g._session_validated = False
        return False

    session_site_id = flask_session.get('site_id')
    if session_site_id != user.site_id:
        logger.warning(
            f"Session site_id mismatch for user {user_id}: "
            f"session={session_site_id}, db={user.site_id}"
        )
        g._session_validated = False
        return False

    session_department_id = flask_session.get('department_id')
    if session_department_id != user.department_id:
        logger.warning(
            f"Session department_id mismatch for user {user_id}: "
            f"session={session_department_id}, db={user.department_id}"
        )
        g._session_validated = False
        return False

    # Cache the validation result for this request
    g._session_validated = True
    return True


def require_validated_session(func):
    """
    Decorator to require validated session for critical write operations.
    
    This decorator ensures that session variables (role, site_id, department_id)
    match the database values before allowing critical operations to proceed.
    This prevents session manipulation attacks.
    
    Returns 401 for invalid sessions with appropriate error message.
    """
    @wraps(func)
    @require_login
    def wrapper(*args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        if not validate_session_for_write():
            logger.error(f"Session validation failed for {request.endpoint}")
            if _is_api_request():
                return jsonify({'error': 'Session invalid, please re-login'}), 401
            flash('Your session is invalid. Please log in again.', 'danger')
            return redirect(url_for('auth_bp.login'))
        
        return func(*args, **kwargs)
    
    return wrapper


# ============================================================================
# PHASE 7: Audit Logging
# ============================================================================

def create_audit_log(action, entity_type, entity_id=None, entity_name=None, 
                     description=None, changes=None):
    """
    Create an audit log entry for sensitive operations.
    
    This function is resilient - if audit logging fails, it logs the error
    but does not prevent the operation from completing.
    
    Args:
        action: Action performed (e.g., 'create', 'update', 'delete', 'login')
        entity_type: Type of entity affected (e.g., 'device', 'user', 'site')
        entity_id: ID of the affected entity (optional)
        entity_name: Name of the affected entity for readability (optional)
        description: Human-readable description of the action (optional)
        changes: Dict of before/after values for updates (optional)
    
    Returns:
        AuditLog object if successful, None if failed
    """
    import logging
    from flask import session as flask_session
    from models.audit_log import AuditLog
    
    logger = logging.getLogger(__name__)
    
    try:
        # Extract user info from session
        user_id = flask_session.get('user_id')
        username = flask_session.get('username', 'unknown')
        user_role = flask_session.get('role', 'unknown')
        
        # Extract request info
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')[:200]  # Truncate to fit column
        
        # Create audit log entry
        audit_entry = AuditLog(
            user_id=user_id,
            username=username,
            user_role=user_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            changes=changes,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        db.session.add(audit_entry)
        db.session.commit()
        
        logger.info(
            f"Audit log created: {username} ({user_role}) {action} {entity_type} "
            f"{entity_id or 'N/A'} from {ip_address}"
        )
        
        return audit_entry
        
    except Exception as e:
        # Log the error but don't fail the operation
        logger.error(f"Failed to create audit log: {e}", exc_info=True)
        
        # Rollback the audit log transaction to prevent affecting the main operation
        try:
            db.session.rollback()
        except Exception:
            pass
        
        return None


def create_audit_log_async(action, entity_type, entity_id=None, entity_name=None, 
                           description=None, changes=None):
    """
    Create an audit log entry asynchronously for high-volume operations.
    
    OPTIMIZATION: Uses threading to avoid blocking the main request thread.
    This is useful for non-critical audit logging where we don't need to wait
    for the database write to complete.
    
    Note: For critical operations (login, permission changes), use the synchronous
    create_audit_log() function instead.
    
    Args:
        action: Action performed (e.g., 'create', 'update', 'delete', 'login')
        entity_type: Type of entity affected (e.g., 'device', 'user', 'site')
        entity_id: ID of the affected entity (optional)
        entity_name: Name of the affected entity for readability (optional)
        description: Human-readable description of the action (optional)
        changes: Dict of before/after values for updates (optional)
    
    Returns:
        None (logs asynchronously)
    """
    import logging
    from flask import session as flask_session, copy_current_request_context
    from models.audit_log import AuditLog
    
    logger = logging.getLogger(__name__)
    
    # Capture context data before threading
    user_id = flask_session.get('user_id')
    username = flask_session.get('username', 'unknown')
    user_role = flask_session.get('role', 'unknown')
    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')[:200]
    
    @copy_current_request_context
    def _create_log():
        """Inner function that runs in a separate thread."""
        try:
            # Create new session for thread safety
            from extensions import db
            
            audit_entry = AuditLog(
                user_id=user_id,
                username=username,
                user_role=user_role,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                description=description,
                changes=changes,
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            db.session.add(audit_entry)
            db.session.commit()
            
            logger.info(
                f"Async audit log created: {username} ({user_role}) {action} {entity_type} "
                f"{entity_id or 'N/A'} from {ip_address}"
            )
            
        except Exception as e:
            logger.error(f"Failed to create async audit log: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
    
    # Start thread for async logging
    thread = threading.Thread(target=_create_log)
    thread.daemon = True  # Don't block app shutdown
    thread.start()


def create_audit_logs_bulk(audit_entries):
    """
    Create multiple audit log entries in a single batch operation.
    
    OPTIMIZATION: Uses bulk_save_objects() for better performance when
    creating many audit logs at once (e.g., bulk device operations).
    
    Args:
        audit_entries: List of dicts, each containing:
            - action: Action performed
            - entity_type: Type of entity
            - entity_id: ID of entity (optional)
            - entity_name: Name of entity (optional)
            - description: Description (optional)
            - changes: Changes dict (optional)
    
    Returns:
        Number of audit logs created, or 0 if failed
    """
    import logging
    from flask import session as flask_session
    from models.audit_log import AuditLog
    
    logger = logging.getLogger(__name__)
    
    if not audit_entries:
        return 0
    
    try:
        # Extract common user/request info once
        user_id = flask_session.get('user_id')
        username = flask_session.get('username', 'unknown')
        user_role = flask_session.get('role', 'unknown')
        ip_address = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')[:200]
        
        # Create audit log objects
        audit_objects = []
        for entry in audit_entries:
            audit_obj = AuditLog(
                user_id=user_id,
                username=username,
                user_role=user_role,
                action=entry.get('action'),
                entity_type=entry.get('entity_type'),
                entity_id=entry.get('entity_id'),
                entity_name=entry.get('entity_name'),
                description=entry.get('description'),
                changes=entry.get('changes'),
                ip_address=ip_address,
                user_agent=user_agent
            )
            audit_objects.append(audit_obj)
        
        # Bulk insert
        db.session.bulk_save_objects(audit_objects)
        db.session.commit()
        
        logger.info(
            f"Bulk audit logs created: {len(audit_objects)} entries by {username} ({user_role})"
        )
        
        return len(audit_objects)
        
    except Exception as e:
        logger.error(f"Failed to create bulk audit logs: {e}", exc_info=True)
        try:
            db.session.rollback()
        except Exception:
            pass
        return 0


def monitor_performance(threshold_ms=50):
    """
    Decorator to monitor and log slow operations.
    
    OPTIMIZATION: Helps identify performance bottlenecks by logging warnings
    when operations exceed the specified threshold.
    
    Args:
        threshold_ms: Threshold in milliseconds (default: 50ms)
    
    Example:
        @monitor_performance(threshold_ms=100)
        def slow_operation():
            # ... operation code ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import logging
            logger = logging.getLogger(__name__)
            
            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed_ms = (time.time() - start_time) * 1000
            
            if elapsed_ms > threshold_ms:
                logger.warning(
                    f"Slow operation detected: {func.__name__} took {elapsed_ms:.2f}ms "
                    f"(threshold: {threshold_ms}ms)"
                )
            
            return result
        
        return wrapper
    
    return decorator


def audit_action(action, entity_type, get_entity_id=None, get_entity_name=None, 
                 get_description=None, get_changes=None):
    """
    Decorator for automatic auditing of route operations.
    
    This decorator automatically creates audit log entries for decorated functions.
    It can extract entity information from function arguments or return values.
    
    Args:
        action: Action being performed (e.g., 'create', 'update', 'delete')
        entity_type: Type of entity (e.g., 'device', 'user', 'site')
        get_entity_id: Function to extract entity_id from args/kwargs/result (optional)
        get_entity_name: Function to extract entity_name from args/kwargs/result (optional)
        get_description: Function to generate description from args/kwargs/result (optional)
        get_changes: Function to extract changes dict from args/kwargs/result (optional)
    
    Example usage:
        @audit_action('delete', 'device', 
                     get_entity_id=lambda args, kwargs, result: kwargs.get('device_id'),
                     get_entity_name=lambda args, kwargs, result: result.get('device_name'))
        def delete_device(device_id):
            # ... delete logic ...
            return {'device_name': device.hostname}
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Execute the function first
            result = func(*args, **kwargs)
            
            # Extract audit information
            entity_id = None
            entity_name = None
            description = None
            changes = None
            
            try:
                if get_entity_id:
                    entity_id = get_entity_id(args, kwargs, result)
                if get_entity_name:
                    entity_name = get_entity_name(args, kwargs, result)
                if get_description:
                    description = get_description(args, kwargs, result)
                if get_changes:
                    changes = get_changes(args, kwargs, result)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to extract audit info: {e}")
            
            # Create audit log (resilient - won't fail if audit fails)
            create_audit_log(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                description=description,
                changes=changes
            )
            
            return result
        
        return wrapper
    
    return decorator
