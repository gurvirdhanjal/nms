# Route Security Tier Classification

This document classifies all routes in the Network Monitoring System into security tiers for RBAC enforcement.

## Tier 1 - Admin Only
Routes requiring `@require_role('admin')` decorator.

### routes/user_management.py
- `save_user` - Create/update users
- `toggle_user_status` - Activate/deactivate users
- `user_management` - User management page

### routes/sites.py
- `create_site` - Create new site
- `update_site` - Update site details
- `delete_site` - Delete site
- `assign_devices_to_site` - Assign devices to site
- `unassign_devices_from_site` - Unassign devices from site

### routes/subnets.py
- `add_subnet` - Add subnet
- `delete_subnet` - Delete subnet
- `subnets` - Subnets management page

### routes/discovery_settings.py
- `update_settings` - Update discovery settings
- `discovery_settings` - Discovery settings page

### routes/snmp.py
- `save_snmp_config` - Save SNMP configuration

## Tier 2 - Operational Write
Routes requiring `@require_permission` decorator with specific permissions.

### routes/devices.py
- `save_device` → requires 'devices.edit'
- `toggle_device_monitoring` → requires 'devices.edit'
- `bulk_add_devices` → requires 'devices.edit'
- `bulk_delete_devices` → requires 'devices.edit'
- `update_device_type` → requires 'devices.edit'
- `update_device` → requires 'devices.edit'

### routes/scanning.py
- `scan_network` → requires 'scanning.run'
- `stop_scan` → requires 'scanning.run'
- `start_discovery` → requires 'scanning.run'

### routes/dashboard.py
- `acknowledge_alert` → requires 'devices.edit'
- `resolve_alert` → requires 'devices.edit'

### routes/departments.py
- `create_department` → requires 'manager'
- `update_department` → requires 'manager'
- `delete_department` → requires 'manager'
- `assign_devices_to_department` → requires 'devices.edit'

## Tier 3 - Read-only Scoped
Routes requiring `@require_login` + scoped_query for data filtering.

### routes/devices.py
- `device_management` - Device list page
- `api_devices` - Device API endpoint
- `api_device_detail` - Device detail API endpoint

### routes/dashboard.py
- `dashboard` - Main dashboard page

### routes/monitoring.py
- `monitoring` - Monitoring page

### routes/reports.py
- `reports` - Reports page (read operations)

## Tier 4 - Agent/Internal
Routes requiring agent token validation (X-Agent-Token header).

### routes/agent.py
- All agent endpoints (receive_metrics, etc.)

## Implementation Notes

- **Tier 1**: Admin-only operations that affect system-wide configuration or user management
- **Tier 2**: Write operations that require specific permissions based on user role
- **Tier 3**: Read operations that need data scoping based on user's site/department
- **Tier 4**: Agent endpoints that should use token-based authentication instead of session auth

## Validation Requirements

- Non-admin users accessing Tier 1 routes → 403 Forbidden
- Users without required permission accessing Tier 2 routes → 403 Forbidden
- Tier 3 routes must filter data based on user's role and scope
- Tier 4 routes must require X-Agent-Token header and reject session auth
