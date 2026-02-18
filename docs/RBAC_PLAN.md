# RBAC Plan (Admin + User)

This plan keeps a 2-role model now, while staying compatible with future role expansion.

## 1) Role Model

### `admin`
- Full access to all pages, APIs, and configuration operations.
- Can manage users, maintenance, credentials/config, and destructive actions.

### `user`
- Non-admin role.
- Can view dashboards/reports and run non-admin workflows allowed by current product policy.
- Cannot access admin-only operations.

## 2) Permission Contract (Phase 1)

### Admin-only operations
- User management (`/user_management`, save, toggle status)
- Maintenance write actions (`/api/maintenance/cleanup`, `/aggregate`, `/run-all`, `/toggle`)
- Admin-only pages/endpoints using `require_role('admin')`

### Logged-in user operations
- Dashboard and reports views
- Read-only/standard device and monitoring workflows
- Maintenance read/status views if currently intended for all logged-in users

## 3) Enforcement Strategy

1. Central middleware in `middleware/rbac.py`:
   - `require_login`
   - `require_role(*roles)`
   - `require_permission(permission)` for future expansion
2. Replace scattered inline `session.get('role')` checks with decorators.
3. Keep session auth source from login flow (`session['role']`) as runtime role authority.

## 4) AD/LDAP Mapping

- LDAP/AD-authenticated users are upserted into local `User` with `auth_source='ldap'`.
- If user belongs to configured admin group (`LDAP_ADMIN_GROUP`) -> role `admin`.
- Otherwise role defaults to `LDAP_DEFAULT_ROLE` (currently `user`).

## 5) Verification Checklist

1. Local admin user can access all admin endpoints.
2. Local user gets `403` on admin-only endpoints.
3. LDAP admin-group user receives `admin` role.
4. LDAP non-admin user receives `user` role.
5. Unauthenticated requests receive `401` (API) or redirect (page).

## 6) OpManager Alignment (Simplified)

- Mirrors the common enterprise pattern:
  - Administrator (full control)
  - Operator/user (limited control and visibility)
- Keeps role checks centralized and auditable, which is required before introducing finer-grained scopes later.
