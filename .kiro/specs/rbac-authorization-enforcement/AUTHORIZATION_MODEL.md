# RBAC Authorization Model Documentation

## Overview

This document describes the Role-Based Access Control (RBAC) authorization model implemented in the Flask multi-tenant Network Monitoring System. The system enforces authorization at three levels:

1. **Route-level**: Controls which routes users can access based on their role
2. **Operation-level**: Controls which write operations users can perform based on permissions
3. **Data-level**: Controls which data users can see based on their site/department scope

## Role Hierarchy

The system implements a four-tier role hierarchy with decreasing levels of access:

```
Admin > Manager > Operator > Viewer
```

### Role Definitions

#### Admin
- **Scope**: Global (all sites, all departments)
- **Access Level**: Full system access
- **Capabilities**:
  - View and modify all data across all sites and departments
  - Manage users, sites, departments, and system configuration
  - Access all administrative routes
  - Perform all write operations
  - No data scoping restrictions

#### Manager
- **Scope**: Site-level (assigned site and all departments within that site)
- **Access Level**: Site management and operational access
- **Capabilities**:
  - View and modify devices within their assigned site
  - View and modify all departments within their assigned site
  - Create, update, and delete departments within their site
  - View users within their site
  - Run scans and manage monitoring within their site
  - Export reports for their site
  - Cannot access user management, system configuration, or other sites

#### Operator
- **Scope**: Department-level (assigned department only)
- **Access Level**: Operational access within department
- **Capabilities**:
  - View and modify devices within their assigned department
  - Run scans within their department
  - Manage device monitoring and maintenance
  - View reports for their department
  - Cannot manage users, departments, or access other departments

#### Viewer
- **Scope**: Department-level (assigned department only)
- **Access Level**: Read-only access within department
- **Capabilities**:
  - View devices within their assigned department
  - View dashboards, reports, and monitoring data
  - View SNMP data and server metrics
  - Cannot perform any write operations
  - Cannot modify devices, run scans, or change configurations

### Legacy Role

#### User (Legacy)
- **Mapping**: Treated as Operator role
- **Purpose**: Backward compatibility with older system versions
- **Recommendation**: Migrate to explicit Operator role

## Permission System

### Permission Naming Convention

Permissions follow the format: `{resource}.{action}`

- **Resources**: dashboard, reports, devices, monitoring, scanning, tracking, snmp, server_metrics, service_checks, file_transfer, maintenance, users
- **Actions**: view, edit, create, delete, run, export
- **Special**: `*` (wildcard for admin), `admin` (admin-only), `public` (no auth required)

### Role Permission Mappings

#### Admin Permissions
```python
{'*'}  # Wildcard - all permissions
```

#### Manager Permissions
```python
{
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
    'users.view',  # Can view department users
}
```

#### Operator Permissions
```python
{
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
}
```

#### Viewer Permissions
```python
{
    'dashboard.view',
    'reports.view',
    'devices.view',
    'monitoring.view',
    'tracking.view',
    'snmp.view',
    'server_metrics.view',
    'service_checks.view',
}
```

### Endpoint Permission Mappings

The system maps each endpoint to a required permission. Key mappings include:

#### Public Endpoints (No Authentication)
- `auth_bp.login`, `auth_bp.register`, `auth_bp.forgot_password`
- `agent_bp.receive_metrics` (uses agent token authentication)

#### Device Operations
- **View**: `devices.view` (all authenticated users)
- **Edit**: `devices.edit` (admin, manager, operator)
  - `save_device`, `toggle_device_monitoring`
  - `bulk_add_devices`, `bulk_delete_devices`
  - `update_device_type`, `update_device`

#### Scanning Operations
- **Run**: `scanning.run` (admin, manager, operator)
  - `scan_network`, `stop_scan`, `start_discovery`
  - `ping_device`, `scan_ports`, `add_to_inventory`

#### Administrative Operations (Admin Only)
- **User Management**: `admin`
  - `save_user`, `toggle_user_status`
- **Site Management**: `admin`
  - `create_site`, `update_site`, `delete_site`
  - `assign_devices_to_site`, `unassign_devices_from_site`
- **Subnet Management**: `admin`
  - `add_subnet`, `delete_subnet`
- **Discovery Settings**: `admin`
  - `update_settings`

#### Department Operations
- **Create/Update/Delete**: `manager` (managers only)
  - `create_department`, `update_department`, `delete_department`
- **Assign Devices**: `devices.edit` (admin, manager, operator)
  - `assign_devices_to_department`, `unassign_devices_from_department`

#### Alert Operations
- **Manage**: `devices.edit` (admin, manager, operator)
  - `acknowledge_alert`, `resolve_alert`

#### Report Operations
- **View**: `reports.view` (all except viewer)
- **Export**: `reports.export` (admin, manager)
  - `create_export_job`

#### Maintenance Operations
- **Edit**: `maintenance.edit` (admin, manager)
  - `run_cleanup`, `run_aggregation`, `toggle_maintenance`

## Data Scoping Rules

Data scoping implements row-level security by filtering query results based on the user's role and assigned scope.

### Scoping by Role

#### Admin
- **Scope**: None (sees all data)
- **Filter**: No filtering applied
- **Rationale**: Admins need global visibility for system management

#### Manager
- **Scope**: Site-level
- **Filter**: `site_id = user.site_id` OR `department.site_id = user.site_id`
- **Behavior**:
  - Sees all devices in their assigned site
  - Sees all devices in departments within their site
  - Sees all departments within their site
  - Sees only their assigned site
  - Sees users within their site or departments

#### Operator
- **Scope**: Department-level
- **Filter**: `department_id = user.department_id`
- **Behavior**:
  - Sees only devices in their assigned department
  - Sees only their assigned department
  - Sees only the site their department belongs to
  - Sees only users in their department

#### Viewer
- **Scope**: Department-level (same as Operator)
- **Filter**: `department_id = user.department_id`
- **Behavior**: Identical to Operator for data visibility (difference is write permissions)

### Scoped Models

The following models have scoping applied:

#### Device
- **Fields**: `site_id`, `department_id`
- **Manager Scope**: Devices where `site_id = user.site_id` OR `department_id IN (departments in user's site)`
- **Operator/Viewer Scope**: Devices where `department_id = user.department_id`

#### Department
- **Fields**: `site_id`
- **Manager Scope**: Departments where `site_id = user.site_id`
- **Operator/Viewer Scope**: Only their assigned department (`id = user.department_id`)

#### Site
- **Manager Scope**: Only their assigned site (`id = user.site_id`)
- **Operator/Viewer Scope**: Only the site their department belongs to

#### User
- **Fields**: `site_id`, `department_id`
- **Manager Scope**: Users where `site_id = user.site_id` OR `department_id IN (departments in user's site)`
- **Operator/Viewer Scope**: Users where `department_id = user.department_id`

#### Related Models (via Device relationship)
- ServerHealthLog
- DeviceInterface
- Alert/Dashboard models

### Scoping Implementation

The `scoped_query(model)` function in `middleware/rbac.py` automatically applies appropriate filters:

```python
from middleware.rbac import scoped_query

# Instead of: devices = Device.query.all()
devices = scoped_query(Device).all()  # Automatically filtered by user's scope
```

### Edge Cases

- **No Site Assignment**: Manager with `site_id = None` sees no data
- **No Department Assignment**: Operator/Viewer with `department_id = None` sees no data
- **Unscoped Models**: Configuration tables (SNMP configs, discovery settings) are not scoped

## Agent Token Authentication

Agent endpoints use token-based authentication instead of session-based authentication to prevent unauthorized access.

### Token Storage

Each device has an `agent_token` field (nullable string, max 100 characters) that stores a unique authentication token.

### Token Generation

Tokens are generated using `secrets.token_urlsafe(32)` which produces cryptographically secure random tokens.

```python
from middleware.rbac import generate_agent_token

device.agent_token = generate_agent_token()
db.session.commit()
```

### Token Validation

Agent endpoints require the `X-Agent-Token` header:

```http
POST /api/agent/metrics HTTP/1.1
X-Agent-Token: <device_agent_token>
Content-Type: application/json

{"cpu": 50, "memory": 75}
```

The `@require_agent_token` decorator validates the token and stores the associated device in `request.agent_device`.

### Token Management

#### Regenerate Token
```http
POST /devices/<device_id>/regenerate_token
```
- **Permission**: `devices.edit`
- **Scope**: User can only regenerate tokens for devices in their scope
- **Returns**: New token for the device

#### Get Token
```http
GET /devices/<device_id>/get_token
```
- **Permission**: `devices.edit`
- **Scope**: User can only view tokens for devices in their scope
- **Returns**: Current token for the device

### Security Considerations

- Tokens are device-specific (one token per device)
- Session authentication is rejected for agent endpoints
- Invalid or missing tokens return 401 Unauthorized
- Tokens should be rotated periodically
- Tokens are transmitted over HTTPS only

## Session Security

### Session Variables

On successful login, the following variables are stored in the session:

```python
session['logged_in'] = True
session['user_id'] = user.id
session['username'] = user.username
session['role'] = user.role
session['site_id'] = user.site_id
session['department_id'] = user.department_id
session['auth_source'] = user.auth_source
```

### Session Validation

Critical write operations validate session variables against the database to prevent session manipulation attacks.

The `@require_validated_session` decorator:
1. Loads the user from the database using `session['user_id']`
2. Validates that `session['role']` matches `user.role`
3. Validates that `session['site_id']` matches `user.site_id`
4. Validates that `session['department_id']` matches `user.department_id`
5. Logs warnings for mismatches
6. Returns 401 and forces re-login if validation fails

### Operations Requiring Validation

Session validation is applied to these critical operations:

- **User Management**: Creating/editing users, toggling user status
- **Site Management**: Creating, updating, deleting sites
- **Department Management**: Creating, updating, deleting departments
- **Device Deletion**: Bulk device deletion
- **Discovery Settings**: System-wide configuration changes

### Session Security Best Practices

- Session cookies are HTTP-only and secure (HTTPS only)
- Sessions expire after inactivity
- Session validation prevents privilege escalation via cookie manipulation
- Failed validation attempts are logged for security monitoring

## Registration Security

### First User Registration

The first user to register receives the `admin` role automatically:

```python
if User.query.count() == 0:
    role = 'admin'
```

This allows initial system setup without requiring pre-existing admin accounts.

### Subsequent Registrations

All registrations after the first user are forced to the `viewer` role, regardless of submitted data:

```python
if User.query.count() > 0:
    role = 'viewer'  # Force viewer role
    if submitted_role != 'viewer':
        logger.warning(f"Registration attempt with role '{submitted_role}', forced to viewer")
```

This prevents privilege escalation via the registration endpoint.

### Role Upgrades

Users registered as viewers can be upgraded to higher roles by administrators through the user management interface.

## Audit Logging

The system maintains an immutable audit trail for sensitive operations to support compliance and security investigation.

### Audit Log Model

Each audit log entry contains:

- **Who**: `user_id`, `username`, `user_role` (at time of action)
- **What**: `action`, `entity_type`, `entity_id`, `entity_name`
- **When**: `timestamp`
- **Where**: `ip_address`, `user_agent`
- **Details**: `description`, `changes` (JSON before/after values)

### Audited Operations

#### Device Operations
- Device creation: `create`, `device`
- Device deletion: `delete`, `device`
- Bulk operations: `bulk_delete`, `device`
- Device updates: `update`, `device`

#### User Management
- User creation: `create`, `user`
- User role changes: `update`, `user` (with changes field)
- User deactivation: `deactivate`, `user`

#### Site/Department Management
- Site creation/deletion: `create`/`delete`, `site`
- Department creation/deletion: `create`/`delete`, `department`

#### Alert Operations
- Alert resolution: `resolve`, `alert`
- Alert acknowledgment: `acknowledge`, `alert`

#### Configuration Changes
- Discovery settings: `update`, `discovery_config`
- SNMP configuration: `update`, `snmp_config`

#### Authentication Events
- Successful login: `login`, `user`
- Failed login: `login_failed`, `user`
- Logout: `logout`, `user`

### Creating Audit Logs

```python
from middleware.rbac import create_audit_log

# Simple audit log
create_audit_log('delete', 'device', device.device_id, device.device_name)

# Audit log with description
create_audit_log('delete', 'device', device.device_id, device.device_name,
                description=f'Deleted device {device.device_name} ({device.device_ip})')

# Audit log with before/after changes
create_audit_log('update', 'user', user.id, user.username,
                changes={'role': {'old': 'viewer', 'new': 'manager'}})
```

### Viewing Audit Logs

#### Web Interface
```
GET /audit/logs
```
- **Permission**: Admin only
- **Features**: Filtering by action, entity_type, username; pagination (50 per page)

#### API Endpoint
```
GET /audit/api/logs
```
- **Permission**: Admin only
- **Returns**: JSON array of last 100 audit log entries

### Audit Log Retention

- Audit logs are immutable (no updates or deletes)
- User deletion sets `user_id` to NULL but preserves username
- Logs should be archived periodically for long-term retention
- Consider implementing log rotation and archival policies

## Authorization Flow

### Request Processing Flow

```
1. Request arrives
   ↓
2. @app.before_request: enforce_authorization()
   ├─ Skip static files and public routes
   ├─ For write operations (POST/PUT/PATCH/DELETE):
   │  └─ Check has_permission_for_endpoint()
   │     ├─ Lookup endpoint in ENDPOINT_PERMISSIONS
   │     ├─ Check user has required permission
   │     └─ Return 403 if unauthorized
   └─ Continue if authorized
   ↓
3. Route decorator checks (@require_role, @require_permission, @require_agent_token)
   ├─ @require_login: Verify session exists
   ├─ @require_role: Verify user has required role
   ├─ @require_permission: Verify user has required permission
   ├─ @require_agent_token: Verify X-Agent-Token header
   └─ @require_validated_session: Validate session against DB
   ↓
4. Route handler executes
   ├─ Use scoped_query() for data access
   └─ Create audit log for sensitive operations
   ↓
5. Response returned
```

### Permission Check Logic

```python
def has_permission(permission, role=None):
    """Check if user has a specific permission."""
    role = role or current_role()
    
    # Admin has all permissions
    if role == 'admin':
        return True
    
    # Check role's permission set
    role_perms = ROLE_PERMISSIONS.get(role, set())
    
    # Wildcard permission
    if '*' in role_perms:
        return True
    
    # Exact permission match
    return permission in role_perms
```

### Scoped Query Logic

```python
def scoped_query(model):
    """Return query filtered by user's scope."""
    role = current_role()
    
    # Admin sees everything
    if role == 'admin':
        return model.query
    
    # Manager: filter by site_id
    if role == 'manager':
        site_id = session.get('site_id')
        # Apply site-level filtering...
    
    # Operator/Viewer: filter by department_id
    if role in ['operator', 'viewer']:
        department_id = session.get('department_id')
        # Apply department-level filtering...
    
    # Default: show nothing
    return model.query.filter(False)
```

## Security Best Practices

### For Developers

1. **Always use scoped_query()** for data access in routes
   ```python
   # Bad: devices = Device.query.all()
   # Good: devices = scoped_query(Device).all()
   ```

2. **Apply appropriate decorators** to all routes
   ```python
   @require_login  # For authenticated routes
   @require_role('admin')  # For admin-only routes
   @require_permission('devices.edit')  # For permission-based routes
   @require_validated_session  # For critical write operations
   ```

3. **Create audit logs** for sensitive operations
   ```python
   create_audit_log('delete', 'device', device_id, device_name)
   ```

4. **Use agent tokens** for agent endpoints
   ```python
   @require_agent_token  # Instead of @require_login
   ```

5. **Never trust session data** for critical operations
   ```python
   @require_validated_session  # Validates session against DB
   ```

### For Administrators

1. **Follow principle of least privilege**: Assign the minimum role necessary
2. **Regularly review audit logs**: Monitor for suspicious activity
3. **Rotate agent tokens**: Regenerate tokens periodically
4. **Monitor failed authorization attempts**: Investigate 403 errors
5. **Review user role assignments**: Ensure users have appropriate access
6. **Archive audit logs**: Implement log retention policies
7. **Use strong session configuration**: HTTP-only, secure cookies, appropriate timeout

### For Security Auditors

1. **Verify route protection**: All routes should have appropriate decorators
2. **Check scoped queries**: All data access should use scoped_query()
3. **Review audit log coverage**: All sensitive operations should be logged
4. **Test authorization boundaries**: Verify users cannot access out-of-scope data
5. **Validate session security**: Test session manipulation attempts
6. **Check agent token security**: Verify tokens are required and validated
7. **Review permission mappings**: Ensure ENDPOINT_PERMISSIONS is complete

## Troubleshooting

### Common Issues

#### User Cannot Access Route (403 Forbidden)
- **Check**: User's role has required permission in ROLE_PERMISSIONS
- **Check**: Route has correct decorator (@require_role or @require_permission)
- **Check**: Endpoint is mapped in ENDPOINT_PERMISSIONS

#### User Cannot See Data
- **Check**: User has site_id or department_id assigned
- **Check**: Data belongs to user's scope (site or department)
- **Check**: Route uses scoped_query() instead of direct Model.query

#### Agent Endpoint Returns 401
- **Check**: Request includes X-Agent-Token header
- **Check**: Token matches device.agent_token in database
- **Check**: Endpoint uses @require_agent_token decorator

#### Session Invalid Error
- **Check**: User's role, site_id, department_id match database
- **Check**: Session hasn't been manually modified
- **Solution**: User should log out and log back in

#### First User Cannot Register as Admin
- **Check**: Database has no existing users (User.query.count() == 0)
- **Check**: Registration route has is_first_user() logic

### Debugging Authorization Issues

1. **Enable debug logging** in middleware/rbac.py
2. **Check session variables**: Print session contents
3. **Verify database state**: Check user's role, site_id, department_id
4. **Test with admin user**: Admins bypass most restrictions
5. **Review audit logs**: Check for authorization failures
6. **Use browser dev tools**: Inspect 403 responses for details

## Migration Guide

### Upgrading from Session-Based Agent Auth

1. Generate tokens for all devices:
   ```python
   from middleware.rbac import generate_agent_token
   for device in Device.query.all():
       if not device.agent_token:
           device.agent_token = generate_agent_token()
   db.session.commit()
   ```

2. Update agent clients to use X-Agent-Token header

3. Deploy new code with @require_agent_token decorators

4. Monitor for 401 errors from agents

5. Provide tokens to agent administrators

### Adding New Roles

1. Add role to ROLE_PERMISSIONS with appropriate permissions
2. Update has_permission() logic if needed
3. Update scoped_query() logic for new scoping rules
4. Add role to user management interface
5. Update documentation

### Adding New Permissions

1. Add permission to ROLE_PERMISSIONS for appropriate roles
2. Add endpoint mapping to ENDPOINT_PERMISSIONS
3. Apply @require_permission decorator to routes
4. Test with users of different roles
5. Update documentation

## References

- **Implementation**: `middleware/rbac.py`
- **Models**: `models/user.py`, `models/device.py`, `models/audit_log.py`
- **Routes**: All route files in `routes/`
- **Tests**: `tests/test_rbac_*.py`, `tests/property_tests/test_rbac_*.py`
- **Design Document**: `.kiro/specs/rbac-authorization-enforcement/design.md`
- **Bugfix Requirements**: `.kiro/specs/rbac-authorization-enforcement/bugfix.md`
