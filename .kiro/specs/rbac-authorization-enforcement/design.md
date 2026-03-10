# RBAC Authorization Enforcement Bugfix Design

## Overview

This design addresses a critical security vulnerability in the Flask multi-tenant Network Monitoring System where authentication exists but authorization is not enforced. The system currently allows any authenticated user to access and modify resources across all departments and sites, regardless of their assigned role (Admin, Manager, Operator, Viewer).

The fix implements comprehensive authorization enforcement through a 7-phase incremental approach:
1. Tier-based route protection using existing decorators
2. Global write guard for all POST/PUT/PATCH/DELETE operations
3. Universal scoped query layer for row-level security
4. Agent token authentication replacing session-based auth
5. Session hardening with DB validation for critical writes
6. Register route hardening to prevent privilege escalation
7. Audit logging for sensitive operations

This approach maintains backward compatibility, avoids rewrites, and provides incremental security improvements that can be deployed phase-by-phase.

## Glossary

- **Bug_Condition (C)**: The condition where authorization is not enforced - any authenticated user can access/modify resources outside their scope
- **Property (P)**: The desired behavior where users can only access/modify resources within their role and scope permissions
- **Preservation**: Existing authentication, session management, and template rendering that must remain unchanged
- **RBAC**: Role-Based Access Control - permission system based on user roles (Admin, Manager, Operator, Viewer)
- **Row-Level Security**: Data scoping that filters query results based on user's site_id or department_id
- **Scoped Query**: A query filtered to return only records visible to the current user based on their role and assignments
- **Agent Token**: Per-device authentication token for agent endpoints (X-Agent-Token header)
- **Tier Classification**: Grouping routes by required permission level (Admin Only, Operational Write, Read-only Scoped, Agent/Internal)
- **Global Write Guard**: Before-request handler that validates permissions for all write operations
- **Audit Log**: Immutable record of sensitive operations for compliance and security investigation

## Bug Details

### Fault Condition


The bug manifests when any authenticated user accesses routes or performs operations that should be restricted based on their role and scope. The system has authentication (login required) but lacks authorization enforcement at three critical levels: route access control, write operation permissions, and data scoping.

**Formal Specification:**
```
FUNCTION isBugCondition(request, user)
  INPUT: request of type HTTPRequest, user of type User
  OUTPUT: boolean
  
  RETURN (
    // Route-level: Non-admin accessing admin routes
    (user.role != 'admin' AND request.endpoint IN ADMIN_ONLY_ROUTES)
    OR
    // Operation-level: Write without permission check
    (request.method IN ['POST', 'PUT', 'PATCH', 'DELETE'] AND NOT has_permission_checked(request))
    OR
    // Data-level: Query returns out-of-scope records
    (user.role IN ['manager', 'operator', 'viewer'] AND query_returns_unscoped_data(request))
    OR
    // Agent-level: Agent endpoint using session auth
    (request.endpoint STARTS_WITH 'agent_bp.' AND authenticated_via_session(request))
    OR
    // Registration: Non-first user can register as admin
    (request.endpoint == 'auth_bp.register' AND user_count > 0 AND submitted_role == 'admin')
  )
END FUNCTION
```

### Examples

**Example 1: Manager Accessing Admin Routes**
- User: Manager role, assigned to Site A
- Action: Navigate to /user_management
- Expected: 403 Forbidden
- Actual: Page loads, shows all users across all sites

**Example 2: Viewer Performing Write Operations**
- User: Viewer role, assigned to Department IT
- Action: POST /devices/123/toggle_monitoring
- Expected: 403 Forbidden (viewers are read-only)
- Actual: Device monitoring toggled successfully

**Example 3: Operator Seeing Cross-Department Data**
- User: Operator role, assigned to Department IT
- Action: GET /api/devices
- Expected: Returns only devices in Department IT
- Actual: Returns all devices across all departments

**Example 4: Agent Using Session Authentication**
- Agent: Device agent on server-01
- Action: POST /api/agent/metrics with session cookie
- Expected: Require X-Agent-Token header
- Actual: Accepts session cookie, allows any authenticated user to impersonate agents

**Example 5: Privilege Escalation via Registration**
- User: Second user registering
- Action: POST /register with role='admin'
- Expected: Force role to 'viewer'
- Actual: User created with admin role

**Example 6: No Audit Trail**
- User: Admin deletes critical device
- Action: DELETE /devices/123
- Expected: Audit log entry created
- Actual: No record of who deleted the device or when


## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Admin users must continue to have full access to all routes and data without scoping restrictions
- Existing authentication flow (login, session creation, logout) must remain unchanged
- Template rendering and UI display must continue to work without breaking changes
- Database queries for models without scoping requirements (e.g., configuration tables) must return all records as before
- First user registration must continue to allow admin role creation
- Existing decorators (@require_login, @require_role, @require_permission) must continue to function
- API responses and error handling patterns must remain consistent

**Scope:**
All inputs that do NOT involve authorization checks should be completely unaffected by this fix. This includes:
- Public routes (login, register, password reset)
- Routes that already have correct authorization (if any)
- Admin user operations (they bypass all scoping)
- Read operations by users within their proper scope
- Database operations on non-scoped models (SNMP configs, discovery settings, etc.)

## Hypothesized Root Cause

Based on the bug description and codebase analysis, the root causes are:

1. **Incomplete Route Protection**: While `middleware/rbac.py` defines decorators (@require_role, @require_permission), many routes don't apply them. The existing ENDPOINT_PERMISSIONS map is incomplete and not enforced globally.

2. **Missing Global Write Guard**: There's a `has_permission_for_endpoint()` function and `enforce_write_permission()` stub in rbac.py, but no `@app.before_request` handler actually calls it. Write operations proceed without permission validation.

3. **Partial Scoping Implementation**: The `apply_department_scope()` function exists but is only applied to department-level filtering. There's no universal `scoped_query()` function that handles both site-level (Manager) and department-level (Operator) scoping across all models.

4. **Agent Authentication Gap**: Agent endpoints in `routes/agent.py` use `@require_login` which accepts session cookies. There's no X-Agent-Token validation mechanism or per-device token storage in the Device model.

5. **Session Trust Without Validation**: Session variables (role, department_id, site_id) are set on login but never re-validated. A compromised session or manual session manipulation could bypass authorization.

6. **Unprotected Registration**: The register route doesn't check if users already exist or force subsequent registrations to non-admin roles.

7. **No Audit Infrastructure**: There's no AuditLog model or audit logging functions. Sensitive operations have no tracking mechanism.


## Correctness Properties

Property 1: Fault Condition - Authorization Enforcement

_For any_ HTTP request where the bug condition holds (user accessing out-of-scope routes, performing unauthorized writes, querying unscoped data, or using improper authentication), the fixed system SHALL enforce authorization by returning 403 Forbidden for route/permission violations, filtering query results to user's scope, requiring agent tokens for agent endpoints, and forcing non-admin roles for subsequent registrations.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8**

Property 2: Preservation - Authorized Access

_For any_ HTTP request where the bug condition does NOT hold (admin users, properly scoped operations, public routes, first user registration), the fixed system SHALL produce exactly the same behavior as the original system, preserving full admin access, existing authentication flows, template rendering, and database operations on non-scoped models.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

## Fix Implementation

### PHASE 1: Tier-Based Route Protection

**Goal**: Classify all routes into security tiers and apply appropriate decorators.

**File**: `middleware/rbac.py` (extend), all route files in `routes/`

**Route Classification**:

**Tier 1 - Admin Only** (require @require_role('admin')):
- `routes/user_management.py`: All endpoints (save_user, toggle_user_status, user_management page)
- `routes/sites.py`: All endpoints (create_site, update_site, delete_site, assign/unassign devices)
- `routes/subnets.py`: All endpoints (add_subnet, delete_subnet, subnets page)
- `routes/discovery_settings.py`: All endpoints (update_settings, discovery_settings page)
- `routes/snmp.py`: save_snmp_config endpoint

**Tier 2 - Operational Write** (require @require_permission with specific permission):
- `routes/devices.py`: 
  - save_device → 'devices.edit'
  - toggle_device_monitoring → 'devices.edit'
  - bulk_add_devices → 'devices.edit'
  - bulk_delete_devices → 'devices.edit'
  - update_device_type → 'devices.edit'
  - update_device → 'devices.edit'
- `routes/scanning.py`:
  - scan_network → 'scanning.run'
  - stop_scan → 'scanning.run'
  - start_discovery → 'scanning.run'
- `routes/dashboard.py`:
  - acknowledge_alert → 'devices.edit'
  - resolve_alert → 'devices.edit'
- `routes/departments.py`:
  - create_department → 'manager' (managers can create depts in their site)
  - update_department → 'manager'
  - delete_department → 'manager'
  - assign_devices_to_department → 'devices.edit'

**Tier 3 - Read-only Scoped** (require @require_login + scoped_query):
- `routes/devices.py`: device_management, api_devices, api_device_detail
- `routes/dashboard.py`: dashboard page
- `routes/monitoring.py`: monitoring page
- `routes/reports.py`: reports page (read operations)

**Tier 4 - Agent/Internal** (require agent token validation):
- `routes/agent.py`: All endpoints (receive_metrics, etc.)

**Implementation Strategy**:
1. Add decorators to unprotected routes incrementally
2. Test each tier before moving to next
3. Keep existing decorators intact (don't remove working protection)
4. Document any routes intentionally left public


### PHASE 2: Global Write Guard

**Goal**: Enforce permission checks on all write operations (POST/PUT/PATCH/DELETE) before they execute.

**File**: `app.py` (add before_request handler), `middleware/rbac.py` (extend ENDPOINT_PERMISSIONS)

**Implementation**:

**Step 1: Complete ENDPOINT_PERMISSION_MAP**

Extend the existing `ENDPOINT_PERMISSIONS` dict in `middleware/rbac.py` to cover ALL write endpoints:

```python
ENDPOINT_PERMISSIONS = {
    # ... existing entries ...
    
    # Devices (write operations)
    "devices_bp.save_device": "devices.edit",
    "devices_bp.toggle_device_monitoring": "devices.edit",
    "devices_bp.bulk_add_devices": "devices.edit",
    "devices_bp.bulk_delete_devices": "devices.edit",
    "devices_bp.update_device_type": "devices.edit",
    "devices_bp.update_device": "devices.edit",
    
    # Sites (admin only)
    "sites.create_site": "admin",
    "sites.update_site": "admin",
    "sites.delete_site": "admin",
    "sites.assign_devices_to_site": "admin",
    "sites.unassign_devices_from_site": "admin",
    
    # Departments (manager or admin)
    "departments.create_department": "manager",
    "departments.update_department": "manager",
    "departments.delete_department": "manager",
    "departments.assign_devices_to_department": "devices.edit",
    "departments.unassign_devices_from_department": "devices.edit",
    
    # Subnets (admin only)
    "subnets.add_subnet": "admin",
    "subnets.delete_subnet": "admin",
    
    # User Management (admin only)
    "user_management_bp.save_user": "admin",
    "user_management_bp.toggle_user_status": "admin",
    
    # Discovery Settings (admin only)
    "discovery_settings_bp.update_settings": "admin",
    
    # Dashboard alerts
    "dashboard_bp.acknowledge_alert": "devices.edit",
    "dashboard_bp.resolve_alert": "devices.edit",
    
    # Scanning
    "scanning_bp.scan_network": "scanning.run",
    "scanning_bp.stop_scan": "scanning.run",
    "scanning_bp.start_discovery": "scanning.run",
    
    # SNMP
    "snmp_bp.save_snmp_config": "devices.edit",
    
    # Maintenance
    "maintenance_bp.toggle_maintenance": "maintenance.edit",
    
    # Reports (export requires permission)
    "reports_bp.create_export_job": "reports.export",
}
```

**Step 2: Implement has_permission_for_endpoint()**

Update the existing function in `middleware/rbac.py`:

```python
def has_permission_for_endpoint():
    """Check if current user has permission for the requested endpoint."""
    endpoint = request.endpoint
    if not endpoint:
        return True
    
    # Public endpoints
    req_perm = ENDPOINT_PERMISSIONS.get(endpoint)
    if req_perm == "public":
        return True
    
    # API endpoints with API key
    if endpoint.startswith('api_v1.'):
        if _has_valid_api_key():
            return True
        req_perm = req_perm or 'devices.edit'
    
    # Default to admin-only for unmapped write endpoints
    if not req_perm and request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        req_perm = "admin"
    
    # No permission required for GET requests to unmapped endpoints
    if not req_perm:
        return True
    
    # Check admin role
    if req_perm == "admin":
        return current_role() == "admin"
    
    # Check permission
    return has_permission(req_perm)
```

**Step 3: Add Global Write Guard to app.py**

```python
@app.before_request
def enforce_authorization():
    """Global authorization guard for all requests."""
    # Skip for static files and public routes
    if request.endpoint and (
        request.endpoint.startswith('static') or
        request.endpoint in ['auth_bp.login', 'auth_bp.register', 'auth_bp.forgot_password']
    ):
        return None
    
    # Enforce write permission for all write operations
    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        from middleware.rbac import has_permission_for_endpoint
        if not has_permission_for_endpoint():
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            flash('You do not have permission to perform this action.', 'danger')
            return redirect(url_for('monitoring_bp.dashboard'))
    
    return None
```

**Permission Naming Convention**:
- Format: `{resource}.{action}`
- Resources: devices, sites, departments, users, scanning, reports, maintenance, snmp
- Actions: view, edit, create, delete, run, export
- Special: "admin" for admin-only, "public" for no auth required


### PHASE 3: Universal Scoped Query Layer

**Goal**: Implement row-level security by filtering all queries based on user's role and scope (site_id or department_id).

**File**: `middleware/rbac.py` (add scoped_query function), all route files (refactor queries)

**Scoped Query Function**:

```python
def scoped_query(model):
    """
    Return a SQLAlchemy query filtered by the current user's scope.
    
    Scoping Rules:
    - Admin: No filtering (sees everything)
    - Manager: Filter by site_id (sees all departments in their site)
    - Operator: Filter by department_id (sees only their department)
    - Viewer: Filter by department_id (sees only their department)
    
    Args:
        model: SQLAlchemy model class (Device, Department, Site, etc.)
    
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
        
        # For Device model: include devices in site OR in departments within site
        if model == Device:
            dept_ids = db.session.query(Department.id).filter(Department.site_id == site_id).all()
            dept_ids = [d[0] for d in dept_ids]
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
            dept_ids = db.session.query(Department.id).filter(Department.site_id == site_id).all()
            dept_ids = [d[0] for d in dept_ids]
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
            dept_ids = db.session.query(Department.id).filter(Department.site_id == site_id).all()
            dept_ids = [d[0] for d in dept_ids]
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
```

**Models Requiring Scoping**:
- Device (has site_id and department_id)
- Department (has site_id)
- Site (direct access control)
- User (has site_id and department_id)
- ServerHealthLog (via device relationship)
- DeviceInterface (via device relationship)
- Alert/Dashboard models (via device relationship)

**Refactoring Strategy**:

Replace direct `Model.query` calls with `scoped_query(Model)`:

**Before**:
```python
devices = Device.query.filter(Device.is_active == True).all()
```

**After**:
```python
from middleware.rbac import scoped_query
devices = scoped_query(Device).filter(Device.is_active == True).all()
```

**Example Refactors** (3 representative routes):

**1. routes/devices.py - device_management()**:
```python
# Line ~276
@devices_bp.route('/devices', methods=['GET'])
@require_login
def device_management():
    from middleware.rbac import scoped_query
    
    # ... existing code ...
    
    # OLD: query = Device.query
    query = scoped_query(Device)
    
    # ... rest of function unchanged ...
```

**2. routes/dashboard.py - dashboard()**:
```python
@dashboard_bp.route('/dashboard')
@require_login
def dashboard():
    from middleware.rbac import scoped_query
    
    # OLD: devices = Device.query.filter(Device.is_monitored == True).all()
    devices = scoped_query(Device).filter(Device.is_monitored == True).all()
    
    # ... rest of function unchanged ...
```

**3. routes/departments.py - list_departments()**:
```python
@departments.route('/departments')
@require_login
def list_departments():
    from middleware.rbac import scoped_query
    
    # OLD: departments = Department.query.all()
    departments = scoped_query(Department).all()
    
    # ... rest of function unchanged ...
```


### PHASE 4: Agent Token Authentication

**Goal**: Replace session-based authentication for agent endpoints with token-based authentication using X-Agent-Token header.

**Files**: `models/device.py` (add agent_token field - already exists), `routes/agent.py` (add token validation), `middleware/rbac.py` (add token helpers)

**Token Storage**:

The Device model already has `agent_token` field:
```python
# models/device.py (existing)
agent_token = db.Column(db.String(100), nullable=True)
```

**Token Generation Helper** (add to `middleware/rbac.py`):

```python
import secrets

def generate_agent_token():
    """Generate a secure random token for agent authentication."""
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
```

**Agent Route Decorator** (add to `middleware/rbac.py`):

```python
def require_agent_token(func):
    """Decorator to require valid agent token for agent endpoints."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get('X-Agent-Token')
        device = validate_agent_token(token)
        
        if not device:
            if _is_api_request():
                return jsonify({'error': 'Invalid or missing agent token'}), 401
            return _unauthorized_response()
        
        # Store device in request context for use in endpoint
        request.agent_device = device
        return func(*args, **kwargs)
    
    return wrapper
```

**Agent Endpoints Conversion** (routes/agent.py):

**Before**:
```python
@agent_bp.route('/api/agent/metrics', methods=['POST'])
@require_login
def receive_metrics():
    # ... existing code ...
```

**After**:
```python
@agent_bp.route('/api/agent/metrics', methods=['POST'])
@require_agent_token
def receive_metrics():
    device = request.agent_device  # Set by decorator
    # ... existing code, use device instead of session ...
```

**Token Management Endpoints** (add to routes/devices.py):

```python
@devices_bp.route('/devices/<int:device_id>/regenerate_token', methods=['POST'])
@require_permission('devices.edit')
def regenerate_agent_token(device_id):
    """Regenerate agent token for a device."""
    from middleware.rbac import generate_agent_token, scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    device.agent_token = generate_agent_token()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'device_id': device.device_id,
        'agent_token': device.agent_token
    })

@devices_bp.route('/devices/<int:device_id>/get_token', methods=['GET'])
@require_permission('devices.edit')
def get_agent_token(device_id):
    """Get agent token for a device (for display/copy)."""
    from middleware.rbac import scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    return jsonify({
        'device_id': device.device_id,
        'agent_token': device.agent_token or 'Not generated'
    })
```

**Agent Endpoints to Convert**:
- `routes/agent.py`: All endpoints (receive_metrics, etc.)
- Any other endpoints that accept agent data

**Migration Strategy**:
1. Add token generation to device creation/edit flows
2. Deploy token validation alongside existing session auth (accept both)
3. Update agent clients to use X-Agent-Token header
4. Remove session auth fallback after migration complete


### PHASE 5: Session Hardening

**Goal**: Validate session variables against database for critical write operations to prevent session manipulation attacks.

**File**: `middleware/rbac.py` (add validation function), `routes/auth.py` (enhance login)

**Session Variable Storage on Login** (routes/auth.py):

**Current login flow** (enhance existing):
```python
@auth_bp.route('/login', methods=['POST'])
def login():
    # ... existing authentication logic ...
    
    if user_authenticated:
        session['logged_in'] = True
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        session['site_id'] = user.site_id  # ADD THIS
        session['department_id'] = user.department_id  # ADD THIS
        session['auth_source'] = user.auth_source
        
        # ... rest of login logic ...
```

**Session Validation Function** (add to middleware/rbac.py):

```python
def validate_session_for_write():
    """
    Validate that session variables match database for critical write operations.
    
    This prevents session manipulation attacks where an attacker modifies
    session cookies to escalate privileges or access other scopes.
    
    Returns:
        True if session is valid, False otherwise
    """
    from flask import session as flask_session
    from models.user import User
    
    user_id = flask_session.get('user_id')
    if not user_id:
        return False
    
    # Load user from database
    user = User.query.get(user_id)
    if not user:
        return False
    
    # Validate critical session variables
    if flask_session.get('role') != user.role:
        logger.warning(f"Session role mismatch for user {user_id}: session={flask_session.get('role')}, db={user.role}")
        return False
    
    if flask_session.get('site_id') != user.site_id:
        logger.warning(f"Session site_id mismatch for user {user_id}")
        return False
    
    if flask_session.get('department_id') != user.department_id:
        logger.warning(f"Session department_id mismatch for user {user_id}")
        return False
    
    return True

def require_validated_session(func):
    """Decorator to require validated session for critical write operations."""
    @wraps(func)
    @require_login
    def wrapper(*args, **kwargs):
        if not validate_session_for_write():
            logger.error(f"Session validation failed for {request.endpoint}")
            if _is_api_request():
                return jsonify({'error': 'Session invalid, please re-login'}), 401
            flash('Your session is invalid. Please log in again.', 'danger')
            return redirect(url_for('auth_bp.login'))
        return func(*args, **kwargs)
    
    return wrapper
```

**Operations Requiring DB Validation**:

Apply `@require_validated_session` to these critical operations:

1. **User Management** (routes/user_management.py):
   - save_user (creating/editing users)
   - toggle_user_status (activating/deactivating users)

2. **Site Management** (routes/sites.py):
   - create_site, update_site, delete_site
   - assign_devices_to_site, unassign_devices_from_site

3. **Department Management** (routes/departments.py):
   - create_department, update_department, delete_department

4. **Device Deletion** (routes/devices.py):
   - bulk_delete_devices (mass deletion)

5. **Discovery Settings** (routes/discovery_settings.py):
   - update_settings (system-wide configuration)

**Example Application**:

```python
@user_management_bp.route('/save_user', methods=['POST'])
@require_validated_session  # ADD THIS
@require_role('admin')
def save_user():
    # ... existing code ...
```

**Validation Strategy**:
- Validate on critical writes only (not every request for performance)
- Log validation failures for security monitoring
- Force re-login on validation failure
- Consider adding session version/nonce for additional security


### PHASE 6: Register Route Hardening

**Goal**: Prevent privilege escalation by forcing all registrations after the first user to non-admin roles.

**File**: `routes/auth.py` (modify register endpoint)

**First-User Detection Logic**:

```python
def is_first_user():
    """Check if this is the first user registration (no users exist)."""
    from models.user import User
    return User.query.count() == 0
```

**Register Route Modification** (routes/auth.py):

**Before**:
```python
@auth_bp.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    email = request.form.get('email')
    role = request.form.get('role', 'viewer')  # VULNERABLE: accepts any role
    
    # ... validation ...
    
    user = User(
        username=username,
        password=generate_password_hash(password),
        email=email,
        role=role  # VULNERABLE: uses submitted role
    )
    
    db.session.add(user)
    db.session.commit()
    
    # ... rest of registration ...
```

**After**:
```python
@auth_bp.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    email = request.form.get('email')
    submitted_role = request.form.get('role', 'viewer')
    
    # ... validation ...
    
    # SECURITY: Force role based on user count
    if is_first_user():
        # First user gets admin role
        role = 'admin'
        logger.info(f"First user registration: {username} assigned admin role")
    else:
        # All subsequent users forced to viewer role
        role = 'viewer'
        if submitted_role != 'viewer':
            logger.warning(f"Registration attempt with role '{submitted_role}' by {username}, forced to viewer")
    
    user = User(
        username=username,
        password=generate_password_hash(password),
        email=email,
        role=role  # SECURE: uses forced role
    )
    
    db.session.add(user)
    db.session.commit()
    
    flash(f'Account created successfully with {role} role. An administrator can upgrade your permissions.', 'success')
    
    # ... rest of registration ...
```

**Backward Compatibility**:
- First user registration continues to work exactly as before (gets admin)
- Existing users are unaffected
- Only new registrations after first user are forced to viewer
- Admins can still upgrade user roles via user management interface

**UI Considerations**:
- Update registration form to show role will be set to viewer
- Add message explaining admin can upgrade permissions
- Consider hiding role selector on registration form (since it's ignored)

**Alternative Approach** (if registration should be disabled after first user):

```python
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # Disable registration after first user
    if not is_first_user():
        flash('Registration is disabled. Please contact an administrator.', 'warning')
        return redirect(url_for('auth_bp.login'))
    
    # ... rest of registration for first user only ...
```


### PHASE 7: Audit Log Model

**Goal**: Create immutable audit trail for sensitive operations to support compliance and security investigation.

**Files**: `models/audit_log.py` (new), `middleware/rbac.py` (add audit helpers), various routes (add audit calls)

**AuditLog Database Model**:

```python
# models/audit_log.py (NEW FILE)
from extensions import db
from datetime import datetime

class AuditLog(db.Model):
    """Immutable audit trail for sensitive operations."""
    __tablename__ = 'audit_logs'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    
    # Who performed the action
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='SET NULL'), nullable=True, index=True)
    username = db.Column(db.String(80), nullable=False)  # Denormalized for immutability
    user_role = db.Column(db.String(20), nullable=False)  # Role at time of action
    
    # What action was performed
    action = db.Column(db.String(50), nullable=False, index=True)  # create, update, delete, login, etc.
    entity_type = db.Column(db.String(50), nullable=False, index=True)  # device, user, site, department, etc.
    entity_id = db.Column(db.Integer, nullable=True, index=True)  # ID of affected entity
    entity_name = db.Column(db.String(200), nullable=True)  # Denormalized name for readability
    
    # Additional context
    description = db.Column(db.Text, nullable=True)  # Human-readable description
    changes = db.Column(db.JSON, nullable=True)  # Before/after values for updates
    ip_address = db.Column(db.String(50), nullable=True)  # Client IP
    user_agent = db.Column(db.String(200), nullable=True)  # Browser/client info
    
    # When it happened
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('audit_logs', lazy='dynamic'))
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'user_role': self.user_role,
            'action': self.action,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'entity_name': self.entity_name,
            'description': self.description,
            'changes': self.changes,
            'ip_address': self.ip_address,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
        }
    
    def __repr__(self):
        return f'<AuditLog {self.username} {self.action} {self.entity_type} {self.entity_id}>'
```

**Audit Helper Functions** (add to middleware/rbac.py):

```python
def create_audit_log(action, entity_type, entity_id=None, entity_name=None, description=None, changes=None):
    """
    Create an audit log entry for a sensitive operation.
    
    Args:
        action: Action performed (create, update, delete, login, etc.)
        entity_type: Type of entity affected (device, user, site, etc.)
        entity_id: ID of affected entity (optional)
        entity_name: Name of affected entity for readability (optional)
        description: Human-readable description (optional)
        changes: Dict of before/after values for updates (optional)
    """
    from models.audit_log import AuditLog
    from flask import session as flask_session
    
    try:
        log = AuditLog(
            user_id=flask_session.get('user_id'),
            username=flask_session.get('username', 'unknown'),
            user_role=flask_session.get('role', 'unknown'),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            changes=changes,
            ip_address=request.remote_addr,
            user_agent=request.headers.get('User-Agent', '')[:200]
        )
        
        db.session.add(log)
        db.session.commit()
        
        logger.info(f"Audit: {log.username} ({log.user_role}) {action} {entity_type} {entity_id}")
        
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        # Don't fail the operation if audit logging fails
        db.session.rollback()

def audit_decorator(entity_type, action=None):
    """
    Decorator to automatically audit an operation.
    
    Usage:
        @audit_decorator('device', 'delete')
        def delete_device(device_id):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Execute the function
            result = func(*args, **kwargs)
            
            # Determine action from function name if not provided
            determined_action = action or func.__name__.replace('_', ' ')
            
            # Try to extract entity_id from args/kwargs
            entity_id = kwargs.get('device_id') or kwargs.get('user_id') or kwargs.get('site_id') or kwargs.get('id')
            if not entity_id and args:
                entity_id = args[0] if isinstance(args[0], int) else None
            
            # Create audit log
            create_audit_log(
                action=determined_action,
                entity_type=entity_type,
                entity_id=entity_id
            )
            
            return result
        
        return wrapper
    return decorator
```

**Operations Triggering Audit Logs**:

1. **Device Operations**:
   - Device deletion: `create_audit_log('delete', 'device', device.device_id, device.device_name)`
   - Device creation: `create_audit_log('create', 'device', device.device_id, device.device_name)`
   - Bulk operations: `create_audit_log('bulk_delete', 'device', description=f'Deleted {count} devices')`

2. **User Management**:
   - User creation: `create_audit_log('create', 'user', user.id, user.username)`
   - User role change: `create_audit_log('update', 'user', user.id, user.username, changes={'role': {'old': old_role, 'new': new_role}})`
   - User deactivation: `create_audit_log('deactivate', 'user', user.id, user.username)`

3. **Site/Department Management**:
   - Site creation/deletion: `create_audit_log('create', 'site', site.id, site.site_name)`
   - Department creation/deletion: `create_audit_log('create', 'department', dept.id, dept.name)`

4. **Alert Operations**:
   - Alert resolution: `create_audit_log('resolve', 'alert', alert.id, description=f'Resolved alert for {device.device_name}')`

5. **Configuration Changes**:
   - Discovery settings: `create_audit_log('update', 'discovery_config', description='Updated discovery settings')`
   - SNMP config: `create_audit_log('update', 'snmp_config', device.device_id, device.device_name)`

6. **Authentication Events**:
   - Login: `create_audit_log('login', 'user', user.id, user.username)`
   - Failed login: `create_audit_log('login_failed', 'user', description=f'Failed login attempt for {username}')`
   - Logout: `create_audit_log('logout', 'user', user.id, user.username)`

**Example Integration** (routes/devices.py):

```python
@devices_bp.route('/devices/<int:device_id>/delete', methods=['DELETE'])
@require_permission('devices.edit')
def delete_device(device_id):
    from middleware.rbac import create_audit_log, scoped_query
    
    device = scoped_query(Device).filter(Device.device_id == device_id).first()
    if not device:
        return jsonify({'error': 'Device not found'}), 404
    
    device_name = device.device_name
    
    # Delete device
    _delete_device_with_dependencies(device)
    db.session.commit()
    
    # Audit log
    create_audit_log('delete', 'device', device_id, device_name, 
                    description=f'Deleted device {device_name} ({device.device_ip})')
    
    return jsonify({'success': True})
```

**Audit Log Query Interface** (add route for viewing logs):

```python
# routes/audit.py (NEW FILE)
from flask import Blueprint, render_template, request, jsonify
from middleware.rbac import require_role, scoped_query
from models.audit_log import AuditLog

audit_bp = Blueprint('audit_bp', __name__, url_prefix='/audit')

@audit_bp.route('/logs')
@require_role('admin')
def audit_logs():
    """View audit logs (admin only)."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    
    # Filters
    action = request.args.get('action')
    entity_type = request.args.get('entity_type')
    username = request.args.get('username')
    
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if username:
        query = query.filter(AuditLog.username.ilike(f'%{username}%'))
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('audit_logs.html', logs=pagination.items, pagination=pagination)

@audit_bp.route('/api/logs')
@require_role('admin')
def api_audit_logs():
    """API endpoint for audit logs."""
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()
    return jsonify([log.to_dict() for log in logs])
```

**Database Migration**:

```python
# Migration script to add audit_logs table
def upgrade():
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(80), nullable=False),
        sa.Column('user_role', sa.String(20), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('entity_name', sa.String(200), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('user_agent', sa.String(200), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'])
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])
    op.create_index('ix_audit_logs_entity_type', 'audit_logs', ['entity_type'])
    op.create_index('ix_audit_logs_entity_id', 'audit_logs', ['entity_id'])
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'])
```


## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the authorization vulnerabilities on unfixed code, then verify the fix works correctly and preserves existing authorized access patterns.

Testing is organized by phase to match the incremental implementation approach, with comprehensive integration tests validating the complete authorization system.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the authorization bugs BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that attempt unauthorized operations on the UNFIXED code. These tests should FAIL (return 200/success) on unfixed code, demonstrating the vulnerability. After the fix, these same tests should PASS (return 403/filtered results).

**Test Cases**:

1. **Route Access Violation** (will succeed on unfixed code, should fail after fix):
   - Manager user navigates to /user_management → expects 200, should get 403
   - Viewer user navigates to /sites → expects 200, should get 403
   - Operator user navigates to /discovery_settings → expects 200, should get 403

2. **Write Permission Violation** (will succeed on unfixed code, should fail after fix):
   - Viewer user POST /devices/123/toggle_monitoring → expects 200, should get 403
   - Operator user DELETE /sites/1 → expects 200, should get 403
   - Manager user POST /discovery_settings/update → expects 200, should get 403

3. **Data Scoping Violation** (will return all data on unfixed code, should filter after fix):
   - Manager (Site A) GET /api/devices → expects devices from all sites, should get only Site A
   - Operator (Dept IT) GET /api/devices → expects devices from all depts, should get only Dept IT
   - Viewer (Dept HR) GET /departments → expects all departments, should get only Dept HR

4. **Agent Authentication Bypass** (will succeed on unfixed code, should fail after fix):
   - POST /api/agent/metrics with session cookie, no token → expects 200, should get 401
   - POST /api/agent/metrics with invalid token → expects 200, should get 401

5. **Registration Privilege Escalation** (will succeed on unfixed code, should fail after fix):
   - POST /register with role='admin' (when users exist) → expects admin role, should get viewer role

**Expected Counterexamples**:
- Unauthorized routes return 200 instead of 403
- Write operations succeed without permission checks
- Queries return unscoped data across all sites/departments
- Agent endpoints accept session authentication
- Registration accepts admin role for non-first users

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed system produces the expected behavior (403 errors, filtered data, token requirements).

**Pseudocode:**
```
FOR ALL request WHERE isBugCondition(request, user) DO
  response := handle_request_fixed(request, user)
  ASSERT expectedAuthorizationBehavior(response, request, user)
END FOR
```

**Test Implementation**:

**Test Fixtures** (tests/conftest.py):

```python
@pytest.fixture
def test_data(db_session):
    """Create test data: 2 sites, 2 departments per site, 4 devices, 4 users."""
    # Sites
    site_a = Site(site_name='Site A', site_code='SITE_A')
    site_b = Site(site_name='Site B', site_code='SITE_B')
    db_session.add_all([site_a, site_b])
    db_session.flush()
    
    # Departments
    dept_it_a = Department(name='IT', site_id=site_a.id)
    dept_hr_a = Department(name='HR', site_id=site_a.id)
    dept_it_b = Department(name='IT', site_id=site_b.id)
    dept_hr_b = Department(name='HR', site_id=site_b.id)
    db_session.add_all([dept_it_a, dept_hr_a, dept_it_b, dept_hr_b])
    db_session.flush()
    
    # Devices
    device_a1 = Device(device_name='Device A1', device_ip='10.0.1.1', 
                       site_id=site_a.id, department_id=dept_it_a.id)
    device_a2 = Device(device_name='Device A2', device_ip='10.0.1.2',
                       site_id=site_a.id, department_id=dept_hr_a.id)
    device_b1 = Device(device_name='Device B1', device_ip='10.0.2.1',
                       site_id=site_b.id, department_id=dept_it_b.id)
    device_b2 = Device(device_name='Device B2', device_ip='10.0.2.2',
                       site_id=site_b.id, department_id=dept_hr_b.id)
    db_session.add_all([device_a1, device_a2, device_b1, device_b2])
    db_session.flush()
    
    # Users
    admin = User(username='admin', role='admin', 
                password=generate_password_hash('admin123'))
    manager_a = User(username='manager_a', role='manager', site_id=site_a.id,
                    password=generate_password_hash('pass123'))
    operator_it_a = User(username='operator_it_a', role='operator', 
                        department_id=dept_it_a.id,
                        password=generate_password_hash('pass123'))
    viewer_hr_a = User(username='viewer_hr_a', role='viewer',
                      department_id=dept_hr_a.id,
                      password=generate_password_hash('pass123'))
    db_session.add_all([admin, manager_a, operator_it_a, viewer_hr_a])
    db_session.commit()
    
    return {
        'sites': {'a': site_a, 'b': site_b},
        'departments': {
            'it_a': dept_it_a, 'hr_a': dept_hr_a,
            'it_b': dept_it_b, 'hr_b': dept_hr_b
        },
        'devices': {
            'a1': device_a1, 'a2': device_a2,
            'b1': device_b1, 'b2': device_b2
        },
        'users': {
            'admin': admin,
            'manager_a': manager_a,
            'operator_it_a': operator_it_a,
            'viewer_hr_a': viewer_hr_a
        }
    }
```

**Phase 1 Tests - Route Protection**:

```python
# tests/test_rbac_phase1_routes.py
def test_admin_routes_blocked_for_non_admin(client, test_data):
    """Non-admin users should get 403 on admin-only routes."""
    manager = test_data['users']['manager_a']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = manager.id
        sess['role'] = 'manager'
    
    # Test admin-only routes
    response = client.get('/user_management')
    assert response.status_code == 403
    
    response = client.get('/discovery_settings')
    assert response.status_code == 403
    
    response = client.post('/subnets/add', data={'subnet': '10.0.0.0/24'})
    assert response.status_code == 403

def test_admin_has_full_access(client, test_data):
    """Admin users should access all routes."""
    admin = test_data['users']['admin']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['role'] = 'admin'
    
    response = client.get('/user_management')
    assert response.status_code == 200
    
    response = client.get('/sites')
    assert response.status_code == 200
```

**Phase 2 Tests - Global Write Guard**:

```python
# tests/test_rbac_phase2_write_guard.py
def test_viewer_cannot_write(client, test_data):
    """Viewer role should be blocked from all write operations."""
    viewer = test_data['users']['viewer_hr_a']
    device = test_data['devices']['a2']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = viewer.id
        sess['role'] = 'viewer'
        sess['department_id'] = viewer.department_id
    
    # Test write operations
    response = client.post(f'/devices/{device.device_id}/toggle_monitoring')
    assert response.status_code == 403
    
    response = client.delete(f'/devices/{device.device_id}')
    assert response.status_code == 403
    
    response = client.post('/devices/save', data={'device_name': 'Test'})
    assert response.status_code == 403

def test_operator_can_write_with_permission(client, test_data):
    """Operator role should perform writes within their scope."""
    operator = test_data['users']['operator_it_a']
    device = test_data['devices']['a1']  # Same department
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = operator.id
        sess['role'] = 'operator'
        sess['department_id'] = operator.department_id
    
    response = client.post(f'/devices/{device.device_id}/toggle_monitoring')
    assert response.status_code == 200
```

**Phase 3 Tests - Scoped Queries**:

```python
# tests/test_rbac_phase3_scoping.py
def test_manager_sees_only_site_devices(client, test_data):
    """Manager should see only devices in their site."""
    manager = test_data['users']['manager_a']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = manager.id
        sess['role'] = 'manager'
        sess['site_id'] = manager.site_id
    
    response = client.get('/api/devices')
    assert response.status_code == 200
    
    data = response.get_json()
    device_ips = [d['device_ip'] for d in data]
    
    # Should see Site A devices
    assert '10.0.1.1' in device_ips
    assert '10.0.1.2' in device_ips
    
    # Should NOT see Site B devices
    assert '10.0.2.1' not in device_ips
    assert '10.0.2.2' not in device_ips

def test_operator_sees_only_department_devices(client, test_data):
    """Operator should see only devices in their department."""
    operator = test_data['users']['operator_it_a']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = operator.id
        sess['role'] = 'operator'
        sess['department_id'] = operator.department_id
    
    response = client.get('/api/devices')
    assert response.status_code == 200
    
    data = response.get_json()
    device_ips = [d['device_ip'] for d in data]
    
    # Should see only IT dept devices
    assert '10.0.1.1' in device_ips
    
    # Should NOT see HR dept devices
    assert '10.0.1.2' not in device_ips

def test_admin_sees_all_devices(client, test_data):
    """Admin should see all devices without scoping."""
    admin = test_data['users']['admin']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['role'] = 'admin'
    
    response = client.get('/api/devices')
    assert response.status_code == 200
    
    data = response.get_json()
    assert len(data) == 4  # All devices
```

**Phase 4 Tests - Agent Token Authentication**:

```python
# tests/test_rbac_phase4_agent_tokens.py
def test_agent_endpoint_requires_token(client, test_data):
    """Agent endpoints should require X-Agent-Token header."""
    # No token
    response = client.post('/api/agent/metrics', json={'cpu': 50})
    assert response.status_code == 401
    
    # Invalid token
    response = client.post('/api/agent/metrics',
                          headers={'X-Agent-Token': 'invalid'},
                          json={'cpu': 50})
    assert response.status_code == 401

def test_agent_endpoint_accepts_valid_token(client, test_data):
    """Agent endpoints should accept valid tokens."""
    device = test_data['devices']['a1']
    device.agent_token = 'valid_token_123'
    db.session.commit()
    
    response = client.post('/api/agent/metrics',
                          headers={'X-Agent-Token': 'valid_token_123'},
                          json={'cpu': 50})
    assert response.status_code == 200

def test_agent_endpoint_rejects_session_auth(client, test_data):
    """Agent endpoints should NOT accept session authentication."""
    admin = test_data['users']['admin']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['role'] = 'admin'
    
    # Session auth should be rejected
    response = client.post('/api/agent/metrics', json={'cpu': 50})
    assert response.status_code == 401
```

**Phase 5 Tests - Session Hardening**:

```python
# tests/test_rbac_phase5_session_validation.py
def test_session_validation_detects_role_mismatch(client, test_data):
    """Session validation should detect manipulated role."""
    viewer = test_data['users']['viewer_hr_a']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = viewer.id
        sess['role'] = 'admin'  # MANIPULATED: user is viewer, session says admin
    
    # Critical operation should validate and reject
    response = client.post('/user_management/save_user', data={
        'username': 'hacker',
        'role': 'admin'
    })
    assert response.status_code == 401  # Session invalid

def test_session_validation_allows_valid_session(client, test_data):
    """Session validation should allow valid sessions."""
    admin = test_data['users']['admin']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['role'] = 'admin'  # Matches DB
    
    response = client.post('/user_management/save_user', data={
        'username': 'newuser',
        'role': 'viewer'
    })
    assert response.status_code == 200
```

**Phase 6 Tests - Register Route Hardening**:

```python
# tests/test_rbac_phase6_registration.py
def test_first_user_gets_admin_role(client, db_session):
    """First user registration should get admin role."""
    # Ensure no users exist
    User.query.delete()
    db_session.commit()
    
    response = client.post('/register', data={
        'username': 'firstuser',
        'password': 'pass123',
        'email': 'first@example.com',
        'role': 'admin'
    })
    
    user = User.query.filter_by(username='firstuser').first()
    assert user.role == 'admin'

def test_subsequent_users_forced_to_viewer(client, test_data):
    """Subsequent registrations should be forced to viewer role."""
    # Users already exist from test_data
    
    response = client.post('/register', data={
        'username': 'hacker',
        'password': 'pass123',
        'email': 'hacker@example.com',
        'role': 'admin'  # Attempt privilege escalation
    })
    
    user = User.query.filter_by(username='hacker').first()
    assert user.role == 'viewer'  # Forced to viewer
```

**Phase 7 Tests - Audit Logging**:

```python
# tests/test_rbac_phase7_audit_logs.py
def test_device_deletion_creates_audit_log(client, test_data):
    """Device deletion should create audit log entry."""
    admin = test_data['users']['admin']
    device = test_data['devices']['a1']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['username'] = admin.username
        sess['role'] = 'admin'
    
    response = client.delete(f'/devices/{device.device_id}')
    assert response.status_code == 200
    
    # Check audit log
    log = AuditLog.query.filter_by(
        action='delete',
        entity_type='device',
        entity_id=device.device_id
    ).first()
    
    assert log is not None
    assert log.username == 'admin'
    assert log.user_role == 'admin'

def test_user_role_change_creates_audit_log(client, test_data):
    """User role changes should be audited."""
    admin = test_data['users']['admin']
    viewer = test_data['users']['viewer_hr_a']
    
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['user_id'] = admin.id
        sess['username'] = admin.username
        sess['role'] = 'admin'
    
    response = client.post('/user_management/save_user', data={
        'user_id': viewer.id,
        'role': 'operator'  # Upgrade from viewer
    })
    
    log = AuditLog.query.filter_by(
        action='update',
        entity_type='user',
        entity_id=viewer.id
    ).first()
    
    assert log is not None
    assert log.changes['role']['old'] == 'viewer'
    assert log.changes['role']['new'] == 'operator'
```


### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed system produces the same result as the original system.

**Pseudocode:**
```
FOR ALL request WHERE NOT isBugCondition(request, user) DO
  ASSERT handle_request_original(request, user) = handle_request_fixed(request, user)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for authorized operations

**Test Plan**: Observe behavior on UNFIXED code first for authorized operations, then write property-based tests capturing that behavior.

**Test Cases**:

1. **Admin Full Access Preservation**: Observe that admin can access all routes and data on unfixed code, then verify this continues after fix
   ```python
   @given(st.sampled_from(['GET', 'POST', 'PUT', 'DELETE']),
          st.sampled_from(['/devices', '/sites', '/users', '/departments']))
   def test_admin_access_preserved(method, route):
       """Admin should maintain full access to all routes."""
       # Test that admin access works identically before and after fix
   ```

2. **Authorized Write Operations Preservation**: Observe that users with proper permissions can write on unfixed code, then verify this continues
   ```python
   def test_operator_authorized_writes_preserved(client, test_data):
       """Operator writes within scope should work identically."""
       operator = test_data['users']['operator_it_a']
       device = test_data['devices']['a1']  # In operator's department
       
       # Test that authorized operations work the same
       response = client.post(f'/devices/{device.device_id}/toggle_monitoring')
       assert response.status_code == 200
   ```

3. **Public Route Preservation**: Observe that public routes work on unfixed code, then verify they continue to work
   ```python
   def test_public_routes_preserved(client):
       """Public routes should remain accessible."""
       response = client.get('/login')
       assert response.status_code == 200
       
       response = client.post('/login', data={'username': 'test', 'password': 'test'})
       # Should process login attempt (may fail auth, but route accessible)
   ```

4. **Template Rendering Preservation**: Observe that templates render correctly on unfixed code, then verify no breaking changes
   ```python
   def test_template_rendering_preserved(client, test_data):
       """Templates should render without breaking changes."""
       admin = test_data['users']['admin']
       
       with client.session_transaction() as sess:
           sess['logged_in'] = True
           sess['user_id'] = admin.id
           sess['role'] = 'admin'
       
       response = client.get('/devices')
       assert response.status_code == 200
       assert b'<!DOCTYPE html>' in response.data  # HTML rendered
   ```

5. **First User Registration Preservation**: Observe that first user gets admin on unfixed code, then verify this continues
   ```python
   def test_first_user_registration_preserved(client, db_session):
       """First user should still get admin role."""
       User.query.delete()
       db_session.commit()
       
       response = client.post('/register', data={
           'username': 'firstadmin',
           'password': 'pass123',
           'role': 'admin'
       })
       
       user = User.query.filter_by(username='firstadmin').first()
       assert user.role == 'admin'
   ```

### Unit Tests

**Route-Level Tests**:
- Test each tier of routes with appropriate user roles
- Test decorator application (@require_role, @require_permission)
- Test error responses (403 Forbidden, 401 Unauthorized)

**Permission Tests**:
- Test has_permission() function with all role/permission combinations
- Test has_permission_for_endpoint() with all endpoint mappings
- Test permission inheritance (admin has all permissions)

**Scoping Tests**:
- Test scoped_query() with each model and role combination
- Test edge cases (no site_id, no department_id, null assignments)
- Test multi-level scoping (Manager seeing departments in their site)

**Agent Token Tests**:
- Test token generation (uniqueness, length, randomness)
- Test token validation (valid, invalid, missing, expired)
- Test token decorator application

**Session Validation Tests**:
- Test validate_session_for_write() with matching/mismatching data
- Test session validation on critical operations
- Test fallback to DB when session missing data

**Registration Tests**:
- Test is_first_user() with 0, 1, many users
- Test role forcing logic
- Test backward compatibility

**Audit Log Tests**:
- Test create_audit_log() with various parameters
- Test audit log model creation and storage
- Test audit log querying and filtering

### Property-Based Tests

**Authorization Properties**:
```python
from hypothesis import given, strategies as st

@given(st.sampled_from(['manager', 'operator', 'viewer']),
       st.sampled_from(['/user_management', '/sites', '/discovery_settings']))
def test_non_admin_blocked_from_admin_routes(role, route):
    """Property: Non-admin users should always get 403 on admin routes."""
    # Generate test cases across all role/route combinations
```

**Scoping Properties**:
```python
@given(st.integers(min_value=1, max_value=100),  # site_id
       st.integers(min_value=1, max_value=100))  # department_id
def test_manager_scoping_property(site_id, department_id):
    """Property: Managers should only see devices in their site."""
    # Generate test cases with various site/department combinations
```

**Permission Properties**:
```python
@given(st.sampled_from(['admin', 'manager', 'operator', 'viewer']),
       st.sampled_from(list(ROLE_PERMISSIONS.keys())))
def test_permission_consistency(role, permission):
    """Property: Permission checks should be consistent with ROLE_PERMISSIONS."""
    expected = permission in ROLE_PERMISSIONS.get(role, set()) or '*' in ROLE_PERMISSIONS.get(role, set())
    actual = has_permission(permission, role)
    assert actual == expected
```

**Preservation Properties**:
```python
@given(st.sampled_from(['/devices', '/dashboard', '/monitoring']))
def test_admin_access_preserved_property(route):
    """Property: Admin should access all routes before and after fix."""
    # Verify admin access is identical
```

### Integration Tests

**End-to-End Authorization Flow**:
- Test complete user journey from login to authorized operation
- Test complete user journey from login to unauthorized operation (403)
- Test role-based workflows (Manager managing their site, Operator editing devices)

**Multi-Phase Integration**:
- Test route protection + write guard + scoping together
- Test agent token + audit logging together
- Test session validation + write guard together

**Cross-Scope Isolation**:
- Test that Manager A cannot see Manager B's site
- Test that Operator in Dept IT cannot see Dept HR
- Test that device operations respect scoping

**Audit Trail Verification**:
- Test that sensitive operations create audit logs
- Test that audit logs contain correct information
- Test that audit logs are queryable

**Template Integration**:
- Test that templates render correctly with scoped data
- Test that UI elements respect permissions (hide buttons for viewers)
- Test that error pages display correctly for 403/401

### Test Suite Structure

```
tests/
├── conftest.py                          # Fixtures (test_data, client, db_session)
├── test_rbac_phase1_routes.py          # Route protection tests
├── test_rbac_phase2_write_guard.py     # Global write guard tests
├── test_rbac_phase3_scoping.py         # Scoped query tests
├── test_rbac_phase4_agent_tokens.py    # Agent token authentication tests
├── test_rbac_phase5_session_validation.py  # Session hardening tests
├── test_rbac_phase6_registration.py    # Register route hardening tests
├── test_rbac_phase7_audit_logs.py      # Audit logging tests
├── test_rbac_integration.py            # End-to-end integration tests
├── test_rbac_preservation.py           # Preservation checking tests
└── property_tests/
    ├── test_authorization_properties.py  # PBT for authorization
    ├── test_scoping_properties.py        # PBT for data scoping
    └── test_permission_properties.py     # PBT for permission logic
```


## Database Migration Requirements

### New Tables

**audit_logs table**:
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username VARCHAR(80) NOT NULL,
    user_role VARCHAR(20) NOT NULL,
    action VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id INTEGER,
    entity_name VARCHAR(200),
    description TEXT,
    changes JSON,
    ip_address VARCHAR(50),
    user_agent VARCHAR(200),
    timestamp DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX ix_audit_logs_action ON audit_logs(action);
CREATE INDEX ix_audit_logs_entity_type ON audit_logs(entity_type);
CREATE INDEX ix_audit_logs_entity_id ON audit_logs(entity_id);
CREATE INDEX ix_audit_logs_timestamp ON audit_logs(timestamp);
```

### Schema Modifications

**No modifications required** - existing schema already supports:
- User.site_id and User.department_id (for scoping)
- Device.agent_token (for agent authentication)
- Device.site_id and Device.department_id (for scoping)

### Data Migration

**No data migration required** - this is a pure authorization enforcement fix that doesn't change existing data structures.

**Optional**: Generate agent tokens for existing devices:
```python
# Migration script: generate_agent_tokens.py
from models.device import Device
from middleware.rbac import generate_agent_token
from extensions import db

devices = Device.query.filter(Device.agent_token.is_(None)).all()
for device in devices:
    device.agent_token = generate_agent_token()
    print(f"Generated token for {device.device_name}")

db.session.commit()
print(f"Generated tokens for {len(devices)} devices")
```

## Deployment and Rollout Notes

### Deployment Strategy

**Incremental Phase Deployment** (recommended):

1. **Phase 1 - Route Protection** (Low Risk):
   - Deploy decorator additions to routes
   - Monitor for 403 errors in logs
   - Verify admin access unaffected
   - Rollback: Remove decorators

2. **Phase 2 - Global Write Guard** (Medium Risk):
   - Deploy before_request handler
   - Monitor write operation success rates
   - Verify legitimate writes still work
   - Rollback: Comment out before_request handler

3. **Phase 3 - Scoped Queries** (High Risk):
   - Deploy scoped_query() function
   - Refactor routes incrementally (start with read-only routes)
   - Monitor data visibility per role
   - Rollback: Revert to direct Model.query calls

4. **Phase 4 - Agent Tokens** (Medium Risk):
   - Deploy token validation alongside session auth (accept both)
   - Generate tokens for existing devices
   - Update agent clients
   - Remove session auth fallback
   - Rollback: Re-enable session auth

5. **Phase 5 - Session Hardening** (Low Risk):
   - Deploy session validation on critical operations
   - Monitor validation failures
   - Rollback: Remove @require_validated_session decorators

6. **Phase 6 - Registration Hardening** (Low Risk):
   - Deploy role forcing logic
   - Test registration flow
   - Rollback: Remove is_first_user() check

7. **Phase 7 - Audit Logging** (Low Risk):
   - Create audit_logs table
   - Deploy audit logging calls
   - Monitor audit log creation
   - Rollback: Remove audit log calls (table remains)

**All-at-Once Deployment** (not recommended):
- Higher risk of breaking changes
- Harder to isolate issues
- Use only if incremental deployment not feasible

### Pre-Deployment Checklist

- [ ] Run full test suite (unit, integration, property-based)
- [ ] Verify test coverage >80% for authorization code
- [ ] Review ENDPOINT_PERMISSIONS map for completeness
- [ ] Test with real user accounts in staging environment
- [ ] Verify admin account exists and is accessible
- [ ] Backup database before deployment
- [ ] Document rollback procedures
- [ ] Prepare monitoring dashboards for 403/401 errors

### Post-Deployment Monitoring

**Metrics to Monitor**:
- 403 Forbidden error rate (should increase initially, then stabilize)
- 401 Unauthorized error rate (should remain low)
- Login success/failure rates (should remain unchanged)
- Query performance (scoped queries may be slower)
- Audit log growth rate (should be steady)

**Alerts to Configure**:
- Spike in 403 errors (may indicate overly restrictive permissions)
- Admin unable to access routes (critical issue)
- Session validation failures (may indicate session manipulation attempts)
- Agent token validation failures (may indicate misconfigured agents)

**Logs to Review**:
- Authorization failures (who, what, when)
- Session validation failures
- Audit log entries for sensitive operations
- Performance of scoped queries

### Rollback Procedures

**Phase 1 Rollback**:
```python
# Remove decorators from routes
# Example: routes/user_management.py
@user_management_bp.route('/save_user', methods=['POST'])
# @require_role('admin')  # COMMENT OUT
def save_user():
    ...
```

**Phase 2 Rollback**:
```python
# app.py
# @app.before_request  # COMMENT OUT
# def enforce_authorization():
#     ...
```

**Phase 3 Rollback**:
```python
# Revert scoped_query() calls to Model.query
# Example: routes/devices.py
# devices = scoped_query(Device).all()  # REMOVE
devices = Device.query.all()  # RESTORE
```

**Phase 4 Rollback**:
```python
# routes/agent.py
@agent_bp.route('/api/agent/metrics', methods=['POST'])
# @require_agent_token  # COMMENT OUT
@require_login  # RESTORE
def receive_metrics():
    ...
```

**Phase 5 Rollback**:
```python
# Remove @require_validated_session decorators
@user_management_bp.route('/save_user', methods=['POST'])
# @require_validated_session  # COMMENT OUT
@require_role('admin')
def save_user():
    ...
```

**Phase 6 Rollback**:
```python
# routes/auth.py - restore original registration
role = request.form.get('role', 'viewer')  # Accept submitted role
# if is_first_user():  # COMMENT OUT
#     role = 'admin'
# else:
#     role = 'viewer'
```

**Phase 7 Rollback**:
```python
# Remove audit log calls (table remains for historical data)
# create_audit_log(...)  # COMMENT OUT
```

### Performance Considerations

**Scoped Query Performance**:
- Scoped queries add WHERE clauses to every query
- Impact: 5-10% slower for large datasets
- Mitigation: Add indexes on site_id and department_id (already exist)
- Consider caching for frequently accessed data

**Session Validation Performance**:
- DB lookup on every critical write operation
- Impact: 10-20ms per validated request
- Mitigation: Only validate critical operations, not all writes
- Consider session caching with TTL

**Audit Log Performance**:
- INSERT on every audited operation
- Impact: 5-10ms per audited operation
- Mitigation: Async audit logging (queue-based)
- Regular audit log archival/cleanup

**Global Write Guard Performance**:
- Permission check on every write request
- Impact: <1ms per request (in-memory check)
- Mitigation: None needed, negligible impact

### Security Considerations

**Session Security**:
- Use secure session cookies (httponly, secure, samesite)
- Implement session timeout (30 minutes recommended)
- Consider session versioning to invalidate old sessions

**Token Security**:
- Agent tokens should be 32+ bytes, cryptographically random
- Store tokens hashed in database (optional, adds complexity)
- Implement token rotation policy (90 days recommended)
- Revoke tokens on device deletion

**Audit Log Security**:
- Audit logs should be immutable (no UPDATE/DELETE)
- Restrict audit log access to admin only
- Consider separate audit log database for compliance
- Implement audit log retention policy (1 year recommended)

**Password Security**:
- Existing password hashing (werkzeug) is adequate
- Consider implementing password complexity requirements
- Implement account lockout after failed login attempts
- Consider 2FA for admin accounts

### Compliance Considerations

**GDPR/Privacy**:
- Audit logs contain user actions (may be personal data)
- Implement data retention policy
- Provide audit log export for data subject requests
- Document data processing in privacy policy

**SOC 2/ISO 27001**:
- Audit logging satisfies access control monitoring requirements
- Document authorization model in security policies
- Implement regular access reviews (quarterly recommended)
- Test authorization controls in security audits

**HIPAA/PCI-DSS** (if applicable):
- Audit logging satisfies audit trail requirements
- Implement role-based access control (RBAC) - satisfied by this fix
- Document authorization policies and procedures
- Implement regular security assessments

