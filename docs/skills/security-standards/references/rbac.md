# RBAC

## Goal

Enforce least privilege through centralized role and permission checks.

## Authority Source

- Central RBAC helper module: `middleware/rbac.py`
- Role-to-permission mapping: `ROLE_PERMISSIONS`
- Department scoping helper: `apply_department_scope(...)`

## Role Model In Code

- `admin`: full access (`'*'`)
- `manager`: department-scoped read/write + user-management visibility
- `operator`: department-scoped operational read/write
- `viewer`: department-scoped read-only
- `user`: legacy role mapped to operator-like permissions

## Enforcement Rules

1. Use decorators (`require_role`, `require_permission`) on protected routes.
2. Avoid scattered inline `session.get('role')` checks in handlers.
3. Apply department scope to queries for non-admin roles.
4. Fail closed when department context is missing for scoped roles.

## Route Patterns

- Admin-only user management routes in `routes/user_management.py` use `@require_role('admin')`.
- Device views apply department scoping with `apply_department_scope(...)` in `routes/devices.py`.

## Adding New Endpoint Rules

1. Decide required permission/role before implementation.
2. Apply decorator first, then add business logic.
3. If endpoint returns data collections, apply `apply_department_scope(...)` unless intentionally global.
4. Verify behavior for `admin`, scoped roles, and unauthenticated callers.

## Checklist

1. Does endpoint use centralized RBAC guard?
2. Is department-scoped filtering applied for non-admins?
3. Are unauthorized/forbidden responses consistent with API/page context?

## Device Console Permission Map

Endpoint-to-permission bindings in `middleware/rbac.py`:
- `device_console_bp.get_device_website_policy` -> `tracking.history.view`
- `device_console_bp.add_device_website_policy` -> `devices.edit`
- `device_console_bp.remove_device_website_policy` -> `devices.edit`
- `device_console_bp.get_device_alerts` -> `tracking.history.view`
- `device_console_bp.acknowledge_device_alert` -> `devices.edit`
- `device_console_bp.device_policy_history_redirect` -> `tracking.history.view`

Rule:
- Read operations are available to tracking-read roles.
- Mutations require write-capable roles.

## UI RBAC Contract Updates (2026-03-05)

- UI receives server-derived RBAC context each request.
- `full_snapshot.meta` must echo role/scope so clients can detect stale cached HTML across login role changes.
- Dashboard/server-metrics endpoints are scope-filtered to prevent out-of-scope leakage.
- Sidebar rendering uses capability map; hidden links reduce accidental unauthorized navigation paths.
- File-transfer backend auth remains unchanged; only dashboard/device-live UI entry points were removed.
