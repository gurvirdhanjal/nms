# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Authorization Enforcement Violations
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate authorization vulnerabilities exist
  - **Scoped PBT Approach**: Test concrete failing cases across all authorization violation types
  - Test implementation details from Fault Condition in design:
    - Non-admin accessing admin routes (user_management, sites, subnets, discovery_settings)
    - Write operations without permission checks (viewer editing devices, operator deleting sites)
    - Queries returning unscoped data (manager seeing all sites, operator seeing all departments)
    - Agent endpoints accepting session auth instead of tokens
    - Non-first user registering as admin
  - The test assertions should match the Expected Behavior Properties from design (403 responses, filtered data, token requirements)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Authorized Access Patterns
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for authorized operations:
    - Admin accessing all routes and data
    - Users with proper permissions performing writes within scope
    - Public routes (login, register) remaining accessible
    - Template rendering working correctly
    - First user registration getting admin role
    - Existing authentication flow creating sessions
  - Write property-based tests capturing observed behavior patterns from Preservation Requirements
  - Property-based testing generates many test cases for stronger guarantees
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 3. PHASE 1: Tier-Based Route Protection

  - [x] 3.1 Classify routes into security tiers
    - Document Tier 1 (Admin Only): user_management, sites, subnets, discovery_settings, snmp
    - Document Tier 2 (Operational Write): devices (save/toggle/bulk), scanning, dashboard alerts, departments
    - Document Tier 3 (Read-only Scoped): device_management, dashboard, monitoring, reports
    - Document Tier 4 (Agent/Internal): agent endpoints
    - Create route classification reference document
    - _Requirements: 2.1_

  - [x] 3.2 Apply decorators to Tier 1 routes (Admin Only)
    - Add @require_role('admin') to routes/user_management.py (save_user, toggle_user_status, user_management page)
    - Add @require_role('admin') to routes/sites.py (create_site, update_site, delete_site, assign/unassign devices)
    - Add @require_role('admin') to routes/subnets.py (add_subnet, delete_subnet, subnets page)
    - Add @require_role('admin') to routes/discovery_settings.py (update_settings, discovery_settings page)
    - Add @require_role('admin') to routes/snmp.py (save_snmp_config)
    - _Requirements: 2.1_

  - [x] 3.3 Apply decorators to Tier 2 routes (Operational Write)
    - Add @require_permission('devices.edit') to routes/devices.py write operations
    - Add @require_permission('scanning.run') to routes/scanning.py operations
    - Add @require_permission('devices.edit') to routes/dashboard.py alert operations
    - Add @require_permission('manager') to routes/departments.py management operations
    - _Requirements: 2.2, 2.5_

  - [x] 3.4 Verify Tier 3 routes have @require_login
    - Confirm routes/devices.py device_management has @require_login
    - Confirm routes/dashboard.py dashboard has @require_login
    - Confirm routes/monitoring.py monitoring has @require_login
    - Confirm routes/reports.py reports has @require_login
    - _Requirements: 3.3_

  - [x] 3.5 Test Phase 1 route protection
    - Test non-admin users get 403 on admin routes
    - Test viewers get 403 on write operations
    - Test admin maintains full access
    - Test authenticated users can access read-only routes
    - _Requirements: 2.1, 2.2, 2.5, 3.1, 3.3_

- [x] 4. PHASE 2: Global Write Guard

  - [x] 4.1 Complete ENDPOINT_PERMISSION_MAP in middleware/rbac.py
    - Add all device write endpoints (save_device, toggle_device_monitoring, bulk operations, update_device)
    - Add all site endpoints (create_site, update_site, delete_site, assign/unassign devices)
    - Add all department endpoints (create, update, delete, assign/unassign devices)
    - Add all subnet endpoints (add_subnet, delete_subnet)
    - Add user management endpoints (save_user, toggle_user_status)
    - Add discovery settings endpoints (update_settings)
    - Add dashboard alert endpoints (acknowledge_alert, resolve_alert)
    - Add scanning endpoints (scan_network, stop_scan, start_discovery)
    - Add SNMP endpoints (save_snmp_config)
    - Add maintenance endpoints (toggle_maintenance)
    - Add report endpoints (create_export_job)
    - _Requirements: 2.2_

  - [x] 4.2 Implement has_permission_for_endpoint() function
    - Update existing function in middleware/rbac.py
    - Handle public endpoints
    - Handle API endpoints with API key
    - Default unmapped write endpoints to admin-only
    - Check admin role for admin-only endpoints
    - Check permissions for other endpoints
    - _Requirements: 2.2_

  - [x] 4.3 Add global write guard to app.py
    - Add @app.before_request handler enforce_authorization()
    - Skip static files and public routes (login, register, forgot_password)
    - Enforce permission check for POST/PUT/PATCH/DELETE methods
    - Return 403 JSON for API requests
    - Flash message and redirect for web requests
    - _Requirements: 2.2, 2.5_

  - [x] 4.4 Test Phase 2 write guard
    - Test viewer blocked from all write operations
    - Test operator can write with proper permissions
    - Test unmapped write endpoints default to admin-only
    - Test GET requests not affected by write guard
    - _Requirements: 2.2, 2.5, 3.2_

- [x] 5. PHASE 3: Universal Scoped Query Layer

  - [x] 5.1 Implement scoped_query() function in middleware/rbac.py
    - Admin: return unfiltered query (sees everything)
    - Manager: filter by site_id, include departments in site
    - Operator/Viewer: filter by department_id
    - Handle Device model with site_id and department_id
    - Handle Department model with site_id
    - Handle Site model (direct access control)
    - Handle User model with site_id and department_id
    - Handle models with relationships (ServerHealthLog, DeviceInterface via device)
    - Handle edge cases (no site_id, no department_id, null assignments)
    - Default to showing nothing for safety
    - _Requirements: 2.3, 2.4_

  - [x] 5.2 Refactor routes/devices.py to use scoped_query
    - Replace Device.query with scoped_query(Device) in device_management()
    - Replace Device.query with scoped_query(Device) in api_devices()
    - Replace Device.query with scoped_query(Device) in api_device_detail()
    - Replace Device.query with scoped_query(Device) in all other device queries
    - _Requirements: 2.3, 2.4_

  - [x] 5.3 Refactor routes/dashboard.py to use scoped_query
    - Replace Device.query with scoped_query(Device) in dashboard()
    - Replace alert queries with scoped queries
    - _Requirements: 2.3, 2.4_

  - [x] 5.4 Refactor routes/departments.py to use scoped_query
    - Replace Department.query with scoped_query(Department) in list_departments()
    - Replace Department.query with scoped_query(Department) in other department queries
    - _Requirements: 2.3, 2.4_

  - [x] 5.5 Refactor routes/sites.py to use scoped_query
    - Replace Site.query with scoped_query(Site) where appropriate
    - Keep admin-only routes unscoped (already protected by @require_role)
    - _Requirements: 2.3, 2.4_

  - [x] 5.6 Refactor routes/monitoring.py to use scoped_query
    - Replace Device.query with scoped_query(Device) in monitoring views
    - _Requirements: 2.3, 2.4_

  - [x] 5.7 Refactor routes/reports.py to use scoped_query
    - Replace Device.query with scoped_query(Device) in report generation
    - _Requirements: 2.3, 2.4_

  - [x] 5.8 Test Phase 3 scoped queries
    - Test manager sees only site devices
    - Test operator sees only department devices
    - Test viewer sees only department devices
    - Test admin sees all devices
    - Test edge cases (no site_id, no department_id)
    - Test cross-scope isolation (Manager A cannot see Manager B's site)
    - _Requirements: 2.3, 2.4, 3.1, 3.2_

- [x] 6. PHASE 4: Agent Token Authentication

  - [x] 6.1 Add token generation and validation helpers to middleware/rbac.py
    - Implement generate_agent_token() using secrets.token_urlsafe(32)
    - Implement validate_agent_token(token) to query Device by agent_token
    - _Requirements: 2.6_

  - [x] 6.2 Create require_agent_token decorator in middleware/rbac.py
    - Extract X-Agent-Token header from request
    - Validate token using validate_agent_token()
    - Return 401 for missing or invalid tokens
    - Store device in request.agent_device for use in endpoint
    - _Requirements: 2.6_

  - [x] 6.3 Convert agent endpoints to use token authentication
    - Replace @require_login with @require_agent_token in routes/agent.py
    - Update receive_metrics() to use request.agent_device
    - Update all other agent endpoints to use request.agent_device
    - _Requirements: 2.6_

  - [x] 6.4 Add token management endpoints to routes/devices.py
    - Add regenerate_agent_token(device_id) endpoint with @require_permission('devices.edit')
    - Add get_agent_token(device_id) endpoint with @require_permission('devices.edit')
    - Use scoped_query to ensure users can only manage tokens for devices in their scope
    - _Requirements: 2.6_

  - [x] 6.5 Generate tokens for existing devices
    - Create migration script to generate agent_token for devices with null tokens
    - Run migration script
    - _Requirements: 2.6_

  - [x] 6.6 Test Phase 4 agent token authentication
    - Test agent endpoints require X-Agent-Token header
    - Test agent endpoints reject invalid tokens
    - Test agent endpoints accept valid tokens
    - Test agent endpoints reject session authentication
    - Test token management endpoints work with scoping
    - _Requirements: 2.6, 3.2_

- [x] 7. PHASE 5: Session Hardening

  - [x] 7.1 Enhance login to store site_id and department_id in session
    - Update routes/auth.py login() to add session['site_id'] = user.site_id
    - Update routes/auth.py login() to add session['department_id'] = user.department_id
    - _Requirements: 3.6_

  - [x] 7.2 Implement session validation function in middleware/rbac.py
    - Implement validate_session_for_write() to check session vs DB
    - Validate role matches user.role
    - Validate site_id matches user.site_id
    - Validate department_id matches user.department_id
    - Log warnings for mismatches
    - Return True if valid, False otherwise
    - _Requirements: 3.6_

  - [x] 7.3 Create require_validated_session decorator in middleware/rbac.py
    - Call validate_session_for_write()
    - Return 401 for invalid sessions
    - Flash message and redirect to login for web requests
    - _Requirements: 3.6_

  - [x] 7.4 Apply session validation to critical operations
    - Add @require_validated_session to routes/user_management.py (save_user, toggle_user_status)
    - Add @require_validated_session to routes/sites.py (create_site, update_site, delete_site)
    - Add @require_validated_session to routes/departments.py (create_department, update_department, delete_department)
    - Add @require_validated_session to routes/devices.py (bulk_delete_devices)
    - Add @require_validated_session to routes/discovery_settings.py (update_settings)
    - _Requirements: 3.6_

  - [x] 7.5 Test Phase 5 session hardening
    - Test session validation detects role mismatch
    - Test session validation detects site_id mismatch
    - Test session validation detects department_id mismatch
    - Test session validation allows valid sessions
    - Test critical operations require validated sessions
    - _Requirements: 3.6_

- [x] 8. PHASE 6: Register Route Hardening

  - [x] 8.1 Implement is_first_user() helper in routes/auth.py
    - Query User.query.count() == 0
    - Return True if no users exist, False otherwise
    - _Requirements: 2.7, 3.5_

  - [x] 8.2 Modify register route to force role based on user count
    - Check is_first_user() in routes/auth.py register()
    - If first user: assign role = 'admin'
    - If not first user: force role = 'viewer' regardless of submitted data
    - Log warning if submitted role != 'viewer' for non-first users
    - Flash message explaining role assignment
    - _Requirements: 2.7, 3.5_

  - [x] 8.3 Test Phase 6 registration hardening
    - Test first user gets admin role
    - Test subsequent users forced to viewer role
    - Test privilege escalation attempt blocked (submitted admin role becomes viewer)
    - Test backward compatibility (first user flow unchanged)
    - _Requirements: 2.7, 3.5_

- [x] 9. PHASE 7: Audit Log Model

  - [x] 9.1 Create AuditLog model in models/audit_log.py
    - Create new file models/audit_log.py
    - Define AuditLog model with fields: id, user_id, username, user_role, action, entity_type, entity_id, entity_name, description, changes (JSON), ip_address, user_agent, timestamp
    - Add foreign key to User model (ondelete='SET NULL')
    - Add indexes on user_id, action, entity_type, entity_id, timestamp
    - Implement to_dict() method
    - _Requirements: 2.8_

  - [x] 9.2 Create database migration for audit_logs table
    - Create migration script to add audit_logs table
    - Add indexes for performance
    - Run migration
    - _Requirements: 2.8_

  - [x] 9.3 Implement audit helper functions in middleware/rbac.py
    - Implement create_audit_log(action, entity_type, entity_id, entity_name, description, changes)
    - Extract user info from session (user_id, username, role)
    - Extract request info (ip_address, user_agent)
    - Create AuditLog entry and commit
    - Handle exceptions gracefully (don't fail operation if audit fails)
    - Implement audit_decorator for automatic auditing
    - _Requirements: 2.8_

  - [x] 9.4 Add audit logging to device operations
    - Add create_audit_log to device deletion (routes/devices.py)
    - Add create_audit_log to device creation (routes/devices.py)
    - Add create_audit_log to bulk device operations (routes/devices.py)
    - _Requirements: 2.8_

  - [x] 9.5 Add audit logging to user management operations
    - Add create_audit_log to user creation (routes/user_management.py)
    - Add create_audit_log to user role changes (routes/user_management.py)
    - Add create_audit_log to user deactivation (routes/user_management.py)
    - _Requirements: 2.8_

  - [x] 9.6 Add audit logging to site/department operations
    - Add create_audit_log to site creation/deletion (routes/sites.py)
    - Add create_audit_log to department creation/deletion (routes/departments.py)
    - _Requirements: 2.8_

  - [x] 9.7 Add audit logging to alert operations
    - Add create_audit_log to alert resolution (routes/dashboard.py)
    - _Requirements: 2.8_

  - [x] 9.8 Add audit logging to configuration changes
    - Add create_audit_log to discovery settings updates (routes/discovery_settings.py)
    - Add create_audit_log to SNMP config updates (routes/snmp.py)
    - _Requirements: 2.8_

  - [x] 9.9 Add audit logging to authentication events
    - Add create_audit_log to successful login (routes/auth.py)
    - Add create_audit_log to failed login attempts (routes/auth.py)
    - Add create_audit_log to logout (routes/auth.py)
    - _Requirements: 2.8_

  - [x] 9.10 Create audit log viewing interface
    - Create new file routes/audit.py with audit_bp blueprint
    - Add audit_logs() route with @require_role('admin')
    - Add filtering by action, entity_type, username
    - Add pagination (50 per page)
    - Add api_audit_logs() API endpoint
    - Create template templates/audit_logs.html
    - Register blueprint in app.py
    - _Requirements: 2.8_

  - [x] 9.11 Test Phase 7 audit logging
    - Test device deletion creates audit log
    - Test user role change creates audit log with changes field
    - Test site/department operations create audit logs
    - Test alert resolution creates audit log
    - Test configuration changes create audit logs
    - Test authentication events create audit logs
    - Test audit log viewing interface (admin only)
    - Test audit log API endpoint
    - _Requirements: 2.8_

- [x] 10. Integration Testing and Verification

  - [x] 10.1 Run comprehensive integration tests
    - Run all phase-specific tests (Phase 1-7)
    - Run end-to-end authorization flow tests
    - Run multi-phase integration tests (route + write guard + scoping)
    - Run cross-scope isolation tests
    - Run template integration tests
    - _Requirements: All_

  - [x] 10.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Authorization Enforcement
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - Verify all authorization violations now return 403
    - Verify queries return scoped data
    - Verify agent endpoints require tokens
    - Verify registration forces viewer role
    - _Requirements: Expected Behavior Properties from design (2.1-2.8)_

  - [x] 10.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Authorized Access Patterns
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Verify admin maintains full access
    - Verify authorized operations work correctly
    - Verify public routes remain accessible
    - Verify templates render correctly
    - Verify first user registration unchanged
    - Verify authentication flow unchanged
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: Preservation Requirements from design (3.1-3.7)_

  - [x] 10.4 Performance testing
    - Measure scoped query performance impact
    - Measure session validation performance impact
    - Measure audit logging performance impact
    - Verify performance degradation <20%
    - _Requirements: All_

  - [x] 10.5 Security testing
    - Test session manipulation attempts
    - Test privilege escalation attempts
    - Test cross-scope access attempts
    - Test agent token security
    - Verify all security controls working
    - _Requirements: All_

- [x] 11. Documentation and Deployment Preparation

  - [x] 11.1 Update deployment documentation
    - Document incremental phase deployment strategy
    - Document rollback procedures for each phase
    - Document monitoring metrics and alerts
    - Document performance considerations
    - _Requirements: All_

  - [x] 11.2 Create deployment checklist
    - Pre-deployment checklist (test suite, coverage, staging tests, admin account, backup)
    - Post-deployment monitoring checklist (error rates, performance, logs)
    - _Requirements: All_

  - [x] 11.3 Document authorization model
    - Document role hierarchy (Admin > Manager > Operator > Viewer)
    - Document permission mappings (ROLE_PERMISSIONS, ENDPOINT_PERMISSIONS)
    - Document scoping rules (Admin: all, Manager: site, Operator/Viewer: department)
    - Document agent token authentication
    - Document audit logging
    - _Requirements: All_

  - [x] 11.4 Create security policy documentation
    - Document RBAC implementation
    - Document row-level security
    - Document audit logging and retention
    - Document session security
    - Document token security
    - _Requirements: All_

- [x] 12. Checkpoint - Ensure all tests pass
  - Ensure all unit tests pass
  - Ensure all integration tests pass
  - Ensure all property-based tests pass
  - Ensure bug condition exploration test passes (confirms fix works)
  - Ensure preservation tests pass (confirms no regressions)
  - Ask the user if questions arise
