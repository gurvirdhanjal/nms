# RBAC Authorization Enforcement - Deployment Guide

## Overview

This document provides comprehensive deployment instructions for the RBAC Authorization Enforcement bugfix. The implementation consists of 7 phases that can be deployed incrementally to minimize risk and enable rapid rollback if issues occur.

**Total Phases**: 7  
**Deployment Strategy**: Incremental phase-by-phase deployment  
**Estimated Deployment Time**: 2-4 hours (all phases)  
**Rollback Capability**: Per-phase rollback supported

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Phase Deployment Strategy](#phase-deployment-strategy)
3. [Phase 1: Tier-Based Route Protection](#phase-1-tier-based-route-protection)
4. [Phase 2: Global Write Guard](#phase-2-global-write-guard)
5. [Phase 3: Universal Scoped Query Layer](#phase-3-universal-scoped-query-layer)
6. [Phase 4: Agent Token Authentication](#phase-4-agent-token-authentication)
7. [Phase 5: Session Hardening](#phase-5-session-hardening)
8. [Phase 6: Register Route Hardening](#phase-6-register-route-hardening)
9. [Phase 7: Audit Logging](#phase-7-audit-logging)
10. [Post-Deployment Verification](#post-deployment-verification)
11. [Monitoring and Alerts](#monitoring-and-alerts)
12. [Performance Considerations](#performance-considerations)
13. [Troubleshooting](#troubleshooting)

## Pre-Deployment Checklist

### Required Actions Before Deployment

- [ ] **Backup Database**: Create full database backup
- [ ] **Test Suite Verification**: All tests passing in staging environment
- [ ] **Admin Account Verification**: Confirm at least one admin account exists and credentials are known
- [ ] **Staging Deployment**: Deploy all phases to staging and verify functionality
- [ ] **User Communication**: Notify users of upcoming security enhancements
- [ ] **Rollback Plan**: Review rollback procedures for each phase
- [ ] **Monitoring Setup**: Ensure error tracking and performance monitoring are active
- [ ] **Agent Token Plan**: Prepare strategy for distributing agent tokens (Phase 4)


### Environment Requirements

- Python 3.8+
- Flask application with existing authentication
- PostgreSQL or SQLite database
- Access to application logs
- Ability to restart application server

### Testing Requirements

```bash
# Run full test suite before deployment
pytest tests/ -v

# Run specific RBAC tests
pytest tests/test_rbac_phase*.py -v
pytest tests/property_tests/test_rbac_*.py -v

# Verify all tests pass
pytest tests/ --tb=short
```

## Phase Deployment Strategy

### Incremental Deployment Approach

Each phase builds on the previous phase and can be deployed independently. This approach provides:

1. **Risk Mitigation**: Issues isolated to specific phase
2. **Rapid Rollback**: Revert single phase without affecting others
3. **Gradual Security Hardening**: Users adapt to changes incrementally
4. **Validation Points**: Verify each phase before proceeding

### Deployment Order

**CRITICAL**: Phases must be deployed in order (1 → 7). Do not skip phases.

| Phase | Component | Risk Level | Rollback Complexity | Estimated Time |
|-------|-----------|------------|---------------------|----------------|
| 1 | Route Protection | Low | Simple | 15 min |
| 2 | Write Guard | Medium | Simple | 20 min |
| 3 | Scoped Queries | High | Moderate | 30 min |
| 4 | Agent Tokens | Medium | Moderate | 30 min |
| 5 | Session Hardening | Low | Simple | 15 min |
| 6 | Register Hardening | Low | Simple | 10 min |
| 7 | Audit Logging | Low | Simple | 20 min |

### Deployment Timeline Options

**Option A: Single Deployment (Recommended for Staging)**
- Deploy all 7 phases in one maintenance window
- Duration: 2-4 hours
- Best for: Staging environments, small deployments

**Option B: Phased Deployment (Recommended for Production)**
- Deploy phases 1-3 in first window (core authorization)
- Deploy phases 4-5 in second window (authentication hardening)
- Deploy phases 6-7 in third window (registration and audit)
- Duration: 3 maintenance windows over 1-2 weeks
- Best for: Production environments, large user bases


## Phase 1: Tier-Based Route Protection

### Description

Applies role-based decorators to routes based on security tiers (Admin Only, Operational Write, Read-only Scoped, Agent/Internal).

### Files Modified

- `routes/user_management.py`
- `routes/sites.py`
- `routes/subnets.py`
- `routes/discovery_settings.py`
- `routes/snmp.py`
- `routes/devices.py`
- `routes/scanning.py`
- `routes/dashboard.py`
- `routes/departments.py`

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify Phase 1 changes are present
   grep -r "@require_role('admin')" routes/
   ```

2. **Restart Application**
   ```bash
   # Example for systemd
   sudo systemctl restart network-monitor
   
   # Example for Docker
   docker-compose restart web
   ```

3. **Verify Deployment**
   ```bash
   # Test admin route protection
   curl -X GET http://localhost:5000/user_management \
     -H "Cookie: session=<non-admin-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 403 Forbidden
   
   # Test admin access
   curl -X GET http://localhost:5000/user_management \
     -H "Cookie: session=<admin-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK
   ```

### Expected Impact

- **Non-admin users**: Cannot access admin routes (user management, sites, subnets, discovery settings)
- **Admin users**: No change, full access maintained
- **Performance**: Negligible (<1ms per request)

### Rollback Procedure

```bash
# Revert to previous version
git revert <phase1-commit-hash>
git push origin main

# Restart application
sudo systemctl restart network-monitor

# Verify rollback
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<non-admin-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected after rollback: 200 OK (vulnerability restored)
```

### Monitoring

Watch for:
- Increased 403 errors (expected for non-admin users)
- User complaints about lost access (verify role assignments)
- Admin users reporting access issues (critical - investigate immediately)


## Phase 2: Global Write Guard

### Description

Enforces permission checks on all write operations (POST/PUT/PATCH/DELETE) through a global before_request handler.

### Files Modified

- `app.py` (add before_request handler)
- `middleware/rbac.py` (extend ENDPOINT_PERMISSIONS, update has_permission_for_endpoint)

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify global write guard is present
   grep -A 10 "@app.before_request" app.py | grep "enforce_authorization"
   ```

2. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

3. **Verify Deployment**
   ```bash
   # Test viewer blocked from writes
   curl -X POST http://localhost:5000/devices/1/toggle_monitoring \
     -H "Cookie: session=<viewer-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 403 Forbidden
   
   # Test operator can write with permission
   curl -X POST http://localhost:5000/devices/1/toggle_monitoring \
     -H "Cookie: session=<operator-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK
   
   # Test GET requests unaffected
   curl -X GET http://localhost:5000/devices \
     -H "Cookie: session=<viewer-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK
   ```

### Expected Impact

- **Viewer users**: Cannot perform any write operations
- **Operator/Manager users**: Can write within their permissions
- **Admin users**: No change, full write access
- **Performance**: ~1-2ms per write request (permission check overhead)

### Rollback Procedure

```bash
# Option 1: Revert commit
git revert <phase2-commit-hash>
git push origin main
sudo systemctl restart network-monitor

# Option 2: Comment out before_request handler (emergency)
# Edit app.py and comment out @app.before_request enforce_authorization()
# Restart application
```

### Monitoring

Watch for:
- Spike in 403 errors on write endpoints (expected)
- Legitimate users unable to perform authorized writes (critical)
- Performance degradation on write operations (should be <5ms)


## Phase 3: Universal Scoped Query Layer

### Description

Implements row-level security by filtering all queries based on user's role and scope (site_id or department_id). **HIGHEST RISK PHASE** - test thoroughly.

### Files Modified

- `middleware/rbac.py` (add scoped_query function)
- `routes/devices.py`
- `routes/dashboard.py`
- `routes/departments.py`
- `routes/sites.py`
- `routes/monitoring.py`
- `routes/reports.py`

### Pre-Deployment Validation

**CRITICAL**: Test in staging with production-like data before deploying to production.

```bash
# Run scoped query tests
pytest tests/test_rbac_phase3_scoped_queries.py -v

# Verify test data setup
pytest tests/test_rbac_phase3_scoped_queries.py::test_manager_sees_only_site_devices -v
pytest tests/test_rbac_phase3_scoped_queries.py::test_operator_sees_only_department_devices -v
```

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify scoped_query function exists
   grep -A 5 "def scoped_query" middleware/rbac.py
   ```

2. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

3. **Verify Deployment** (CRITICAL - Test Each Role)

   **Test Manager Scoping**:
   ```bash
   # Manager should see only their site's devices
   curl -X GET http://localhost:5000/api/devices \
     -H "Cookie: session=<manager-site-a-session>" \
     | jq '.[] | .site_id' | sort -u
   # Expected: Only Site A's site_id
   ```

   **Test Operator Scoping**:
   ```bash
   # Operator should see only their department's devices
   curl -X GET http://localhost:5000/api/devices \
     -H "Cookie: session=<operator-dept-it-session>" \
     | jq '.[] | .department_id' | sort -u
   # Expected: Only IT department's department_id
   ```

   **Test Admin No Scoping**:
   ```bash
   # Admin should see all devices
   curl -X GET http://localhost:5000/api/devices \
     -H "Cookie: session=<admin-session>" \
     | jq 'length'
   # Expected: Total device count (no filtering)
   ```

### Expected Impact

- **Manager users**: See only devices/departments in their site
- **Operator/Viewer users**: See only devices in their department
- **Admin users**: No change, see all data
- **Performance**: 2-5ms per query (additional WHERE clause)
- **User Experience**: Significant - users will see less data

### Rollback Procedure

**CRITICAL**: This phase has the highest rollback complexity due to widespread query changes.

```bash
# Option 1: Full revert (recommended)
git revert <phase3-commit-hash>
git push origin main
sudo systemctl restart network-monitor

# Option 2: Emergency bypass (temporary)
# Edit middleware/rbac.py
# Modify scoped_query to always return unfiltered query:
#   def scoped_query(model):
#       return model.query  # EMERGENCY BYPASS
# Restart application
```

### Monitoring

Watch for:
- Users reporting "missing devices" (expected for non-admin)
- Empty dashboards for managers/operators (critical - check site_id/department_id assignments)
- Cross-scope data leakage (critical security issue)
- Query performance degradation (should be <10ms)
- Database connection pool exhaustion

### Common Issues

**Issue**: Manager sees no devices despite having site_id assigned
- **Cause**: Devices not assigned to site or department
- **Fix**: Verify device site_id/department_id assignments in database

**Issue**: Operator sees devices from other departments
- **Cause**: Scoped query not applied to specific route
- **Fix**: Check route implementation, ensure scoped_query(Device) is used


## Phase 4: Agent Token Authentication

### Description

Replaces session-based authentication for agent endpoints with token-based authentication using X-Agent-Token header.

### Files Modified

- `middleware/rbac.py` (add token generation/validation)
- `routes/agent.py` (replace @require_login with @require_agent_token)
- `routes/devices.py` (add token management endpoints)
- `migrations/generate_agent_tokens.py` (new migration)

### Pre-Deployment Requirements

1. **Generate Tokens for Existing Devices**
   ```bash
   # Run migration to generate tokens
   python migrations/generate_agent_tokens.py
   
   # Verify tokens generated
   python -c "from models.device import Device; from extensions import db; \
     print(f'Devices with tokens: {Device.query.filter(Device.agent_token != None).count()}')"
   ```

2. **Prepare Agent Update Strategy**
   - Identify all devices running agents
   - Plan token distribution method (API, manual, configuration management)
   - Prepare agent configuration update procedure

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify agent token decorator exists
   grep -A 5 "def require_agent_token" middleware/rbac.py
   ```

2. **Run Token Generation Migration**
   ```bash
   python migrations/generate_agent_tokens.py
   ```

3. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

4. **Distribute Agent Tokens**

   **Option A: API-based distribution** (recommended)
   ```bash
   # For each device, retrieve token via API
   curl -X GET http://localhost:5000/devices/1/get_token \
     -H "Cookie: session=<admin-session>" \
     | jq -r '.agent_token'
   ```

   **Option B: Database export**
   ```bash
   # Export tokens to CSV for bulk distribution
   python -c "from models.device import Device; import csv; \
     devices = Device.query.all(); \
     with open('agent_tokens.csv', 'w') as f: \
       writer = csv.writer(f); \
       writer.writerow(['device_id', 'device_name', 'agent_token']); \
       writer.writerows([[d.device_id, d.device_name, d.agent_token] for d in devices])"
   ```

5. **Update Agent Configurations**
   ```bash
   # Example agent configuration update
   # Update agent config file with token
   echo "AGENT_TOKEN=<token>" >> /etc/network-monitor-agent/config
   
   # Restart agent
   sudo systemctl restart network-monitor-agent
   ```

6. **Verify Agent Authentication**
   ```bash
   # Test agent endpoint with token
   curl -X POST http://localhost:5000/api/agent/metrics \
     -H "X-Agent-Token: <device-token>" \
     -H "Content-Type: application/json" \
     -d '{"cpu": 50, "memory": 60}' \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK
   
   # Test agent endpoint without token
   curl -X POST http://localhost:5000/api/agent/metrics \
     -H "Content-Type: application/json" \
     -d '{"cpu": 50, "memory": 60}' \
     -w "\nStatus: %{http_code}\n"
   # Expected: 401 Unauthorized
   ```

### Expected Impact

- **Agent endpoints**: Require X-Agent-Token header, reject session auth
- **Existing agents**: Will fail until updated with tokens
- **Performance**: Negligible (<1ms token validation)
- **Security**: Significantly improved (prevents user impersonation of agents)

### Rollback Procedure

```bash
# Option 1: Revert to session auth
git revert <phase4-commit-hash>
git push origin main
sudo systemctl restart network-monitor

# Option 2: Temporary dual authentication (emergency)
# Edit routes/agent.py
# Add fallback to session auth if token validation fails
# Restart application
```

### Monitoring

Watch for:
- Agent metric submission failures (401 errors)
- Devices showing as "offline" due to failed authentication
- Invalid token attempts (potential security issue)
- Token regeneration requests

### Agent Update Checklist

- [ ] All agents identified
- [ ] Tokens generated for all devices
- [ ] Tokens distributed to agents
- [ ] Agent configurations updated
- [ ] Agents restarted
- [ ] Metric submission verified for all agents


## Phase 5: Session Hardening

### Description

Validates session variables against database for critical write operations to prevent session manipulation attacks.

### Files Modified

- `routes/auth.py` (store site_id/department_id in session)
- `middleware/rbac.py` (add validate_session_for_write, require_validated_session decorator)
- `routes/user_management.py`
- `routes/sites.py`
- `routes/departments.py`
- `routes/devices.py`
- `routes/discovery_settings.py`

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify session validation function exists
   grep -A 5 "def validate_session_for_write" middleware/rbac.py
   ```

2. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

3. **Force User Re-login** (Optional but Recommended)
   ```bash
   # Clear all sessions to ensure site_id/department_id are set
   # Option A: Clear session storage (if file-based)
   rm -rf /var/lib/network-monitor/sessions/*
   
   # Option B: Clear Redis sessions (if Redis-based)
   redis-cli FLUSHDB
   
   # Option C: Restart application (sessions expire naturally)
   # Users will be prompted to re-login when session expires
   ```

4. **Verify Deployment**
   ```bash
   # Test session validation on critical operation
   # Login as admin to get fresh session
   curl -X POST http://localhost:5000/login \
     -d "username=admin&password=<password>" \
     -c cookies.txt
   
   # Perform critical operation
   curl -X POST http://localhost:5000/user_management/save_user \
     -b cookies.txt \
     -d "username=testuser&role=viewer" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK (valid session)
   ```

### Expected Impact

- **All users**: Must re-login to get site_id/department_id in session
- **Critical operations**: Additional DB query for session validation (~2-5ms)
- **Security**: Protection against session manipulation attacks
- **User Experience**: Minimal (only affects critical operations)

### Rollback Procedure

```bash
# Option 1: Revert commit
git revert <phase5-commit-hash>
git push origin main
sudo systemctl restart network-monitor

# Option 2: Disable validation (emergency)
# Edit middleware/rbac.py
# Modify validate_session_for_write to always return True:
#   def validate_session_for_write():
#       return True  # EMERGENCY BYPASS
# Restart application
```

### Monitoring

Watch for:
- Session validation failures (potential attack or session corruption)
- Users forced to re-login unexpectedly
- Performance impact on critical operations (should be <10ms)
- Increased database query load


## Phase 6: Register Route Hardening

### Description

Prevents privilege escalation by forcing all registrations after the first user to viewer role.

### Files Modified

- `routes/auth.py` (add is_first_user check, force role to viewer)

### Deployment Steps

1. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify registration hardening
   grep -A 10 "is_first_user" routes/auth.py
   ```

2. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

3. **Verify Deployment**
   ```bash
   # Test registration forced to viewer role
   curl -X POST http://localhost:5000/register \
     -d "username=testuser&password=pass123&email=test@example.com&role=admin" \
     -w "\nStatus: %{http_code}\n"
   
   # Verify user created with viewer role
   python -c "from models.user import User; \
     u = User.query.filter_by(username='testuser').first(); \
     print(f'Role: {u.role}')"
   # Expected: Role: viewer
   ```

### Expected Impact

- **New registrations**: Forced to viewer role (cannot self-assign admin)
- **First user registration**: Unchanged (still gets admin)
- **Existing users**: No impact
- **Performance**: Negligible
- **Security**: Prevents privilege escalation via registration

### Rollback Procedure

```bash
# Revert commit
git revert <phase6-commit-hash>
git push origin main
sudo systemctl restart network-monitor
```

### Monitoring

Watch for:
- Registration attempts with non-viewer roles (log warnings)
- First user registration issues (critical)
- User complaints about role assignment


## Phase 7: Audit Logging

### Description

Creates immutable audit trail for sensitive operations to support compliance and security investigation.

### Files Modified

- `models/audit_log.py` (new model)
- `migrations/create_audit_logs_table.py` (new migration)
- `middleware/rbac.py` (add audit helper functions)
- `routes/audit.py` (new audit viewing interface)
- `routes/devices.py` (add audit logging)
- `routes/user_management.py` (add audit logging)
- `routes/sites.py` (add audit logging)
- `routes/departments.py` (add audit logging)
- `routes/dashboard.py` (add audit logging)
- `routes/discovery_settings.py` (add audit logging)
- `routes/auth.py` (add audit logging)
- `app.py` (register audit blueprint)

### Pre-Deployment Requirements

1. **Database Migration**
   ```bash
   # Run audit logs table migration
   python migrations/create_audit_logs_table.py
   
   # Verify table created
   python -c "from models.audit_log import AuditLog; \
     print(f'AuditLog table exists: {AuditLog.__table__.exists()}')"
   ```

### Deployment Steps

1. **Run Database Migration**
   ```bash
   python migrations/create_audit_logs_table.py
   ```

2. **Deploy Code Changes**
   ```bash
   git pull origin main
   # Verify audit log model exists
   grep -A 5 "class AuditLog" models/audit_log.py
   ```

3. **Restart Application**
   ```bash
   sudo systemctl restart network-monitor
   ```

4. **Verify Deployment**
   ```bash
   # Perform audited operation (device deletion)
   curl -X DELETE http://localhost:5000/devices/1 \
     -H "Cookie: session=<admin-session>" \
     -w "\nStatus: %{http_code}\n"
   
   # Verify audit log created
   python -c "from models.audit_log import AuditLog; \
     log = AuditLog.query.filter_by(action='delete', entity_type='device').first(); \
     print(f'Audit log: {log.username} {log.action} {log.entity_type} {log.entity_id}')"
   
   # Test audit log viewing interface
   curl -X GET http://localhost:5000/audit/logs \
     -H "Cookie: session=<admin-session>" \
     -w "\nStatus: %{http_code}\n"
   # Expected: 200 OK
   ```

### Expected Impact

- **All sensitive operations**: Create audit log entries
- **Database**: Additional write per audited operation
- **Performance**: 1-3ms per audited operation
- **Storage**: ~500 bytes per audit log entry
- **Compliance**: Full audit trail for security investigation

### Rollback Procedure

```bash
# Option 1: Revert commit (keeps audit logs table)
git revert <phase7-commit-hash>
git push origin main
sudo systemctl restart network-monitor

# Option 2: Disable audit logging (emergency)
# Edit middleware/rbac.py
# Modify create_audit_log to return immediately:
#   def create_audit_log(*args, **kwargs):
#       return  # EMERGENCY BYPASS
# Restart application

# Option 3: Drop audit logs table (if needed)
# python -c "from models.audit_log import AuditLog; \
#   from extensions import db; \
#   AuditLog.__table__.drop(db.engine)"
```

### Monitoring

Watch for:
- Audit log creation failures (should not block operations)
- Rapid audit log growth (storage capacity)
- Performance impact on audited operations (should be <5ms)
- Suspicious audit patterns (security investigation)

### Audit Log Retention

**Recommended Retention Policy**:
- Keep audit logs for minimum 90 days
- Archive logs older than 1 year
- Never delete logs for compliance-critical operations

**Cleanup Script** (optional):
```python
# cleanup_old_audit_logs.py
from models.audit_log import AuditLog
from extensions import db
from datetime import datetime, timedelta

# Delete logs older than 1 year
cutoff_date = datetime.utcnow() - timedelta(days=365)
AuditLog.query.filter(AuditLog.timestamp < cutoff_date).delete()
db.session.commit()
```


## Post-Deployment Verification

### Comprehensive Verification Checklist

After deploying all phases, perform comprehensive verification:

#### 1. Role-Based Access Control

```bash
# Test Admin Access (should have full access)
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<admin-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected: 200 OK

# Test Manager Access (should be blocked from admin routes)
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<manager-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected: 403 Forbidden

# Test Viewer Write Block (should be blocked from writes)
curl -X POST http://localhost:5000/devices/1/toggle_monitoring \
  -H "Cookie: session=<viewer-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected: 403 Forbidden
```

#### 2. Data Scoping

```bash
# Test Manager Scoping (should see only their site)
curl -X GET http://localhost:5000/api/devices \
  -H "Cookie: session=<manager-site-a-session>" \
  | jq '[.[] | .site_id] | unique'
# Expected: Only Site A's site_id

# Test Operator Scoping (should see only their department)
curl -X GET http://localhost:5000/api/devices \
  -H "Cookie: session=<operator-dept-it-session>" \
  | jq '[.[] | .department_id] | unique'
# Expected: Only IT department's department_id

# Test Admin No Scoping (should see all)
curl -X GET http://localhost:5000/api/devices \
  -H "Cookie: session=<admin-session>" \
  | jq 'length'
# Expected: Total device count
```

#### 3. Agent Token Authentication

```bash
# Test Agent Endpoint with Valid Token
curl -X POST http://localhost:5000/api/agent/metrics \
  -H "X-Agent-Token: <valid-token>" \
  -H "Content-Type: application/json" \
  -d '{"cpu": 50}' \
  -w "\nStatus: %{http_code}\n"
# Expected: 200 OK

# Test Agent Endpoint without Token
curl -X POST http://localhost:5000/api/agent/metrics \
  -H "Content-Type: application/json" \
  -d '{"cpu": 50}' \
  -w "\nStatus: %{http_code}\n"
# Expected: 401 Unauthorized
```

#### 4. Session Hardening

```bash
# Test Critical Operation with Valid Session
curl -X POST http://localhost:5000/user_management/save_user \
  -H "Cookie: session=<admin-session>" \
  -d "username=testuser&role=viewer" \
  -w "\nStatus: %{http_code}\n"
# Expected: 200 OK
```

#### 5. Registration Hardening

```bash
# Test Registration Forced to Viewer
curl -X POST http://localhost:5000/register \
  -d "username=newuser&password=pass123&email=new@example.com&role=admin" \
  -w "\nStatus: %{http_code}\n"

# Verify role
python -c "from models.user import User; \
  u = User.query.filter_by(username='newuser').first(); \
  print(f'Role: {u.role}')"
# Expected: Role: viewer
```

#### 6. Audit Logging

```bash
# Perform audited operation
curl -X DELETE http://localhost:5000/devices/1 \
  -H "Cookie: session=<admin-session>"

# Verify audit log
python -c "from models.audit_log import AuditLog; \
  log = AuditLog.query.order_by(AuditLog.timestamp.desc()).first(); \
  print(f'{log.username} {log.action} {log.entity_type} {log.entity_id}')"
# Expected: admin delete device 1

# Test audit viewing interface
curl -X GET http://localhost:5000/audit/logs \
  -H "Cookie: session=<admin-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected: 200 OK
```

### Integration Test Suite

```bash
# Run full integration test suite
pytest tests/test_rbac_phase*.py -v
pytest tests/property_tests/test_rbac_*.py -v

# Run comprehensive security tests
pytest tests/test_rbac_comprehensive_security.py -v

# Run performance tests
pytest tests/test_rbac_performance.py -v
```

### User Acceptance Testing

1. **Admin User Testing**
   - [ ] Can access all routes
   - [ ] Can perform all operations
   - [ ] Can see all data
   - [ ] Can view audit logs

2. **Manager User Testing**
   - [ ] Cannot access admin routes
   - [ ] Can manage departments in their site
   - [ ] Can see only site devices
   - [ ] Can perform writes within permissions

3. **Operator User Testing**
   - [ ] Cannot access admin routes
   - [ ] Can edit devices in their department
   - [ ] Can see only department devices
   - [ ] Cannot delete sites/departments

4. **Viewer User Testing**
   - [ ] Cannot access admin routes
   - [ ] Cannot perform any writes
   - [ ] Can see only department data
   - [ ] Can view dashboards and reports


## Monitoring and Alerts

### Key Metrics to Monitor

#### 1. Authorization Metrics

**403 Forbidden Errors**
- **Metric**: Count of 403 responses per endpoint
- **Alert Threshold**: Sudden spike (>50% increase)
- **Action**: Investigate if legitimate users are blocked

**401 Unauthorized Errors**
- **Metric**: Count of 401 responses (agent endpoints)
- **Alert Threshold**: >10 per minute
- **Action**: Check agent token validity

**Session Validation Failures**
- **Metric**: Count of session validation failures
- **Alert Threshold**: >5 per hour
- **Action**: Potential session manipulation attack

#### 2. Performance Metrics

**Request Latency**
- **Metric**: P95 response time per endpoint
- **Baseline**: Measure before deployment
- **Alert Threshold**: >20% increase from baseline
- **Action**: Investigate query performance

**Database Query Time**
- **Metric**: Average query execution time
- **Alert Threshold**: >50ms for scoped queries
- **Action**: Add indexes, optimize queries

**Audit Log Write Time**
- **Metric**: Time to create audit log entry
- **Alert Threshold**: >10ms
- **Action**: Check database performance

#### 3. Security Metrics

**Failed Login Attempts**
- **Metric**: Count of failed logins per user
- **Alert Threshold**: >5 per user per hour
- **Action**: Potential brute force attack

**Invalid Agent Token Attempts**
- **Metric**: Count of invalid token attempts per device
- **Alert Threshold**: >10 per device per hour
- **Action**: Potential token compromise

**Cross-Scope Access Attempts**
- **Metric**: Count of 403 errors for scoped resources
- **Alert Threshold**: >20 per user per hour
- **Action**: Potential privilege escalation attempt

#### 4. Operational Metrics

**Audit Log Growth Rate**
- **Metric**: Audit log entries per hour
- **Alert Threshold**: >1000 per hour (adjust based on usage)
- **Action**: Check storage capacity

**Agent Connectivity**
- **Metric**: Devices with recent metrics (last 5 minutes)
- **Alert Threshold**: <90% of expected devices
- **Action**: Check agent token distribution

### Monitoring Implementation

#### Application Logging

```python
# Add to app.py or middleware/rbac.py
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('/var/log/network-monitor/rbac.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('rbac')

# Log authorization events
@app.before_request
def log_authorization():
    if request.method in ['POST', 'PUT', 'PATCH', 'DELETE']:
        logger.info(f"Authorization check: {request.endpoint} by {session.get('username')}")
```

#### Prometheus Metrics (Optional)

```python
# Add to app.py
from prometheus_client import Counter, Histogram

# Define metrics
authorization_failures = Counter(
    'rbac_authorization_failures_total',
    'Total authorization failures',
    ['endpoint', 'role']
)

request_duration = Histogram(
    'rbac_request_duration_seconds',
    'Request duration with RBAC',
    ['endpoint']
)

# Instrument code
@app.before_request
def track_authorization():
    if not has_permission_for_endpoint():
        authorization_failures.labels(
            endpoint=request.endpoint,
            role=session.get('role')
        ).inc()
```

#### Log Analysis Queries

```bash
# Count 403 errors by endpoint
grep "403" /var/log/network-monitor/access.log | \
  awk '{print $7}' | sort | uniq -c | sort -rn

# Count session validation failures
grep "Session validation failed" /var/log/network-monitor/rbac.log | wc -l

# Count invalid agent token attempts
grep "Invalid or missing agent token" /var/log/network-monitor/rbac.log | wc -l

# Monitor audit log growth
python -c "from models.audit_log import AuditLog; \
  from datetime import datetime, timedelta; \
  cutoff = datetime.utcnow() - timedelta(hours=1); \
  count = AuditLog.query.filter(AuditLog.timestamp > cutoff).count(); \
  print(f'Audit logs last hour: {count}')"
```

### Alert Configuration Examples

#### Nagios/Icinga

```ini
# /etc/nagios/conf.d/rbac_monitoring.cfg
define service {
    service_description     RBAC Authorization Failures
    check_command           check_log!/var/log/network-monitor/rbac.log!403!50
    max_check_attempts      3
    check_interval          5
}

define service {
    service_description     RBAC Performance
    check_command           check_http_response_time!5000!/api/devices
    max_check_attempts      3
    check_interval          5
}
```

#### Grafana Dashboard (JSON)

```json
{
  "dashboard": {
    "title": "RBAC Monitoring",
    "panels": [
      {
        "title": "Authorization Failures",
        "targets": [
          {
            "expr": "rate(rbac_authorization_failures_total[5m])"
          }
        ]
      },
      {
        "title": "Request Duration",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rbac_request_duration_seconds)"
          }
        ]
      }
    ]
  }
}
```


## Performance Considerations

### Expected Performance Impact

| Phase | Component | Overhead | Impact Level |
|-------|-----------|----------|--------------|
| 1 | Route Protection | <1ms | Negligible |
| 2 | Write Guard | 1-2ms | Low |
| 3 | Scoped Queries | 2-5ms | Medium |
| 4 | Agent Tokens | <1ms | Negligible |
| 5 | Session Validation | 2-5ms | Low |
| 6 | Registration | <1ms | Negligible |
| 7 | Audit Logging | 1-3ms | Low |

**Total Overhead**: 5-15ms per request (varies by operation type)

### Performance Optimization Strategies

#### 1. Database Indexing

**Critical Indexes for Scoped Queries**:
```sql
-- Device scoping indexes
CREATE INDEX idx_device_site_id ON device(site_id);
CREATE INDEX idx_device_department_id ON device(department_id);

-- Department scoping indexes
CREATE INDEX idx_department_site_id ON department(site_id);

-- User scoping indexes
CREATE INDEX idx_user_site_id ON user(site_id);
CREATE INDEX idx_user_department_id ON user(department_id);

-- Audit log indexes (already created in migration)
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_timestamp ON audit_logs(timestamp);
CREATE INDEX idx_audit_logs_action ON audit_logs(action);
CREATE INDEX idx_audit_logs_entity_type ON audit_logs(entity_type);
```

#### 2. Query Optimization

**Scoped Query Performance**:
```python
# GOOD: Single query with proper indexes
devices = scoped_query(Device).filter(Device.is_active == True).all()

# BAD: Multiple queries or N+1 problems
devices = Device.query.all()
filtered = [d for d in devices if d.site_id == user_site_id]  # Avoid!
```

**Eager Loading for Relationships**:
```python
# Load related data in single query
from sqlalchemy.orm import joinedload

devices = scoped_query(Device)\
    .options(joinedload(Device.department))\
    .options(joinedload(Device.site))\
    .all()
```

#### 3. Caching Strategies

**Session Data Caching**:
```python
# Cache user scope data in session to avoid DB lookups
@auth_bp.route('/login', methods=['POST'])
def login():
    # ... authentication ...
    
    session['user_id'] = user.id
    session['role'] = user.role
    session['site_id'] = user.site_id  # Cache for scoping
    session['department_id'] = user.department_id  # Cache for scoping
    session['permissions'] = get_user_permissions(user)  # Cache permissions
```

**Permission Caching**:
```python
# Cache permission checks for request duration
from functools import lru_cache

@lru_cache(maxsize=128)
def has_permission_cached(user_id, permission):
    return has_permission(permission)

# Clear cache after request
@app.after_request
def clear_permission_cache(response):
    has_permission_cached.cache_clear()
    return response
```

#### 4. Audit Log Optimization

**Async Audit Logging** (optional):
```python
# Use background task for audit logging to avoid blocking
from threading import Thread

def create_audit_log_async(*args, **kwargs):
    """Create audit log in background thread."""
    thread = Thread(target=create_audit_log, args=args, kwargs=kwargs)
    thread.daemon = True
    thread.start()

# Use in routes
create_audit_log_async('delete', 'device', device_id, device_name)
```

**Batch Audit Logging** (for high-volume operations):
```python
# Batch insert audit logs
audit_logs = []
for device in devices:
    audit_logs.append(AuditLog(
        user_id=user_id,
        action='bulk_delete',
        entity_type='device',
        entity_id=device.device_id
    ))

db.session.bulk_save_objects(audit_logs)
db.session.commit()
```

### Performance Testing

#### Load Testing Script

```python
# load_test_rbac.py
import requests
import time
from concurrent.futures import ThreadPoolExecutor

def test_scoped_query(session_cookie):
    """Test scoped query performance."""
    start = time.time()
    response = requests.get(
        'http://localhost:5000/api/devices',
        cookies={'session': session_cookie}
    )
    duration = time.time() - start
    return duration, response.status_code

def run_load_test(num_requests=100, concurrency=10):
    """Run load test with multiple concurrent requests."""
    session_cookie = 'your-session-cookie'
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(test_scoped_query, session_cookie)
            for _ in range(num_requests)
        ]
        
        results = [f.result() for f in futures]
    
    durations = [r[0] for r in results]
    print(f"Average: {sum(durations)/len(durations):.3f}s")
    print(f"P95: {sorted(durations)[int(len(durations)*0.95)]:.3f}s")
    print(f"Max: {max(durations):.3f}s")

if __name__ == '__main__':
    run_load_test()
```

#### Performance Benchmarks

**Baseline (Before RBAC)**:
```
Average response time: 50ms
P95 response time: 100ms
Throughput: 200 req/s
```

**Target (After RBAC)**:
```
Average response time: <60ms (<20% increase)
P95 response time: <120ms (<20% increase)
Throughput: >160 req/s (>80% of baseline)
```

### Performance Troubleshooting

#### Issue: Slow Scoped Queries

**Symptoms**:
- Response times >100ms for device list
- Database CPU usage high

**Diagnosis**:
```sql
-- Check query execution plan
EXPLAIN ANALYZE
SELECT * FROM device
WHERE site_id = 1;

-- Check index usage
SELECT * FROM pg_stat_user_indexes
WHERE relname = 'device';
```

**Solutions**:
1. Add missing indexes
2. Optimize query filters
3. Use query result caching
4. Consider database connection pooling

#### Issue: Audit Log Write Bottleneck

**Symptoms**:
- Slow write operations
- Database write queue buildup

**Diagnosis**:
```python
# Measure audit log write time
import time
start = time.time()
create_audit_log('test', 'device', 1)
print(f"Audit log write: {time.time() - start:.3f}s")
```

**Solutions**:
1. Use async audit logging
2. Batch audit log writes
3. Optimize audit log indexes
4. Consider separate audit database

#### Issue: Session Validation Overhead

**Symptoms**:
- Slow critical operations
- Increased database query load

**Diagnosis**:
```python
# Measure session validation time
import time
start = time.time()
validate_session_for_write()
print(f"Session validation: {time.time() - start:.3f}s")
```

**Solutions**:
1. Cache user data in session
2. Reduce validation frequency
3. Optimize user query
4. Use database connection pooling


## Troubleshooting

### Common Issues and Solutions

#### Issue 1: Manager Sees No Devices

**Symptoms**:
- Manager user logs in successfully
- Dashboard shows no devices
- Device list is empty

**Diagnosis**:
```python
# Check manager's site assignment
from models.user import User
manager = User.query.filter_by(username='manager_username').first()
print(f"Manager site_id: {manager.site_id}")

# Check devices in that site
from models.device import Device
devices = Device.query.filter_by(site_id=manager.site_id).all()
print(f"Devices in site: {len(devices)}")

# Check if devices have site_id assigned
unassigned = Device.query.filter(Device.site_id == None).count()
print(f"Unassigned devices: {unassigned}")
```

**Solutions**:
1. Assign site_id to manager user
2. Assign site_id to devices
3. Verify scoped_query is applied to route

#### Issue 2: Viewer Can Still Perform Writes

**Symptoms**:
- Viewer user can edit devices
- Write operations return 200 instead of 403

**Diagnosis**:
```python
# Check user role
from models.user import User
viewer = User.query.filter_by(username='viewer_username').first()
print(f"User role: {viewer.role}")

# Check endpoint permission mapping
from middleware.rbac import ENDPOINT_PERMISSIONS
print(f"Endpoint permissions: {ENDPOINT_PERMISSIONS.get('devices_bp.save_device')}")

# Check if global write guard is active
# Look for @app.before_request in app.py
```

**Solutions**:
1. Verify user role is 'viewer'
2. Check ENDPOINT_PERMISSIONS includes the endpoint
3. Verify global write guard is deployed
4. Check decorator order (require_permission should be after require_login)

#### Issue 3: Agent Metrics Not Submitting

**Symptoms**:
- Agents showing as offline
- 401 errors in agent logs
- No recent metrics in database

**Diagnosis**:
```bash
# Check agent token exists
python -c "from models.device import Device; \
  d = Device.query.get(1); \
  print(f'Agent token: {d.agent_token}')"

# Test agent endpoint manually
curl -X POST http://localhost:5000/api/agent/metrics \
  -H "X-Agent-Token: <token>" \
  -H "Content-Type: application/json" \
  -d '{"cpu": 50}' \
  -v

# Check agent configuration
cat /etc/network-monitor-agent/config | grep AGENT_TOKEN
```

**Solutions**:
1. Generate agent token if missing
2. Update agent configuration with token
3. Restart agent service
4. Verify token in X-Agent-Token header

#### Issue 4: Session Validation Failures

**Symptoms**:
- Users forced to re-login frequently
- "Session invalid" error messages
- Critical operations fail with 401

**Diagnosis**:
```python
# Check session data
from flask import session
print(f"Session user_id: {session.get('user_id')}")
print(f"Session role: {session.get('role')}")
print(f"Session site_id: {session.get('site_id')}")

# Check user data in database
from models.user import User
user = User.query.get(session.get('user_id'))
print(f"DB role: {user.role}")
print(f"DB site_id: {user.site_id}")

# Check for mismatch
if session.get('role') != user.role:
    print("MISMATCH: Session role != DB role")
```

**Solutions**:
1. Force user re-login to refresh session
2. Verify user data hasn't changed
3. Check session storage (file/Redis) is working
4. Verify session timeout settings

#### Issue 5: Audit Logs Not Created

**Symptoms**:
- Operations complete successfully
- No audit log entries in database
- Audit log viewing page is empty

**Diagnosis**:
```python
# Check audit log table exists
from models.audit_log import AuditLog
print(f"AuditLog table: {AuditLog.__table__.exists()}")

# Check recent audit logs
logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(10).all()
print(f"Recent logs: {len(logs)}")

# Test audit log creation manually
from middleware.rbac import create_audit_log
create_audit_log('test', 'device', 1, 'Test Device')

# Check for errors in logs
grep "Failed to create audit log" /var/log/network-monitor/rbac.log
```

**Solutions**:
1. Run audit logs migration if table missing
2. Check database permissions
3. Verify create_audit_log is called in routes
4. Check for exceptions in audit logging code

#### Issue 6: Performance Degradation

**Symptoms**:
- Slow page loads (>5 seconds)
- High database CPU usage
- Timeout errors

**Diagnosis**:
```python
# Check query performance
import time
from middleware.rbac import scoped_query
from models.device import Device

start = time.time()
devices = scoped_query(Device).all()
print(f"Scoped query time: {time.time() - start:.3f}s")

# Check database indexes
# Run in database console:
# SELECT * FROM pg_stat_user_indexes WHERE relname = 'device';

# Check audit log growth
from models.audit_log import AuditLog
count = AuditLog.query.count()
print(f"Total audit logs: {count}")
```

**Solutions**:
1. Add missing database indexes
2. Optimize scoped queries
3. Enable query result caching
4. Archive old audit logs
5. Increase database connection pool

#### Issue 7: Cross-Scope Data Leakage

**Symptoms**:
- Manager sees devices from other sites
- Operator sees devices from other departments
- Security violation

**Diagnosis**:
```python
# Test scoped query manually
from middleware.rbac import scoped_query
from models.device import Device
from flask import session

# Simulate manager session
session['role'] = 'manager'
session['site_id'] = 1

devices = scoped_query(Device).all()
site_ids = set(d.site_id for d in devices)
print(f"Site IDs returned: {site_ids}")
# Should only contain site_id = 1

# Check if scoped_query is bypassed
# Search for Device.query.all() in routes (should use scoped_query instead)
```

**Solutions**:
1. Verify scoped_query is used in all routes
2. Check for Device.query.all() bypassing scoping
3. Verify session site_id/department_id are set
4. Test with different user roles

### Emergency Procedures

#### Emergency Rollback (All Phases)

```bash
# 1. Identify last known good commit
git log --oneline | head -20

# 2. Revert to pre-RBAC version
git revert <rbac-start-commit>..<rbac-end-commit>
git push origin main

# 3. Restart application
sudo systemctl restart network-monitor

# 4. Verify rollback
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<non-admin-session>" \
  -w "\nStatus: %{http_code}\n"
# Expected: 200 OK (vulnerability restored)

# 5. Notify users
echo "RBAC deployment rolled back due to issues. Investigating." | \
  mail -s "System Alert" admin@example.com
```

#### Emergency Bypass (Temporary)

**WARNING**: Only use in critical situations. This disables all authorization.

```python
# Edit middleware/rbac.py
# Add at top of file:
EMERGENCY_BYPASS = True  # REMOVE AFTER ISSUE RESOLVED

# Modify functions:
def has_permission_for_endpoint():
    if EMERGENCY_BYPASS:
        return True
    # ... rest of function ...

def scoped_query(model):
    if EMERGENCY_BYPASS:
        return model.query
    # ... rest of function ...

def validate_session_for_write():
    if EMERGENCY_BYPASS:
        return True
    # ... rest of function ...
```

```bash
# Restart application
sudo systemctl restart network-monitor

# CRITICAL: Remove bypass after issue resolved
# Set EMERGENCY_BYPASS = False and restart
```

### Support Contacts

**Deployment Issues**:
- DevOps Team: devops@example.com
- On-call: +1-555-0100

**Security Issues**:
- Security Team: security@example.com
- Emergency: +1-555-0911

**Database Issues**:
- DBA Team: dba@example.com
- On-call: +1-555-0200


## Appendix

### A. Quick Reference Commands

#### Deployment Commands

```bash
# Pull latest code
git pull origin main

# Run migrations
python migrations/create_audit_logs_table.py
python migrations/generate_agent_tokens.py

# Restart application
sudo systemctl restart network-monitor

# Check application status
sudo systemctl status network-monitor

# View logs
tail -f /var/log/network-monitor/app.log
tail -f /var/log/network-monitor/rbac.log
```

#### Testing Commands

```bash
# Run all RBAC tests
pytest tests/test_rbac_*.py -v

# Run specific phase tests
pytest tests/test_rbac_phase1_routes.py -v
pytest tests/test_rbac_phase3_scoped_queries.py -v

# Run property-based tests
pytest tests/property_tests/test_rbac_*.py -v

# Run performance tests
pytest tests/test_rbac_performance.py -v
```

#### Verification Commands

```bash
# Test admin access
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<admin-session>" -w "\nStatus: %{http_code}\n"

# Test non-admin blocked
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<manager-session>" -w "\nStatus: %{http_code}\n"

# Test scoped query
curl -X GET http://localhost:5000/api/devices \
  -H "Cookie: session=<manager-session>" | jq '[.[] | .site_id] | unique'

# Test agent token
curl -X POST http://localhost:5000/api/agent/metrics \
  -H "X-Agent-Token: <token>" -H "Content-Type: application/json" \
  -d '{"cpu": 50}' -w "\nStatus: %{http_code}\n"
```

#### Database Commands

```python
# Check user roles
from models.user import User
users = User.query.all()
for u in users:
    print(f"{u.username}: {u.role} (site={u.site_id}, dept={u.department_id})")

# Check device assignments
from models.device import Device
devices = Device.query.all()
for d in devices:
    print(f"{d.device_name}: site={d.site_id}, dept={d.department_id}")

# Check audit logs
from models.audit_log import AuditLog
logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(10).all()
for log in logs:
    print(f"{log.timestamp}: {log.username} {log.action} {log.entity_type}")

# Check agent tokens
from models.device import Device
devices_with_tokens = Device.query.filter(Device.agent_token != None).count()
total_devices = Device.query.count()
print(f"Devices with tokens: {devices_with_tokens}/{total_devices}")
```

### B. Phase Dependency Matrix

| Phase | Depends On | Required For | Can Deploy Independently |
|-------|------------|--------------|--------------------------|
| 1 | None | 2, 3 | Yes |
| 2 | 1 | 3 | No (needs route protection) |
| 3 | 1, 2 | None | No (needs auth foundation) |
| 4 | None | None | Yes |
| 5 | 1 | None | No (needs route protection) |
| 6 | None | None | Yes |
| 7 | None | None | Yes |

**Recommended Deployment Groups**:
- **Group A** (Core Authorization): Phases 1, 2, 3
- **Group B** (Authentication): Phases 4, 5
- **Group C** (Compliance): Phases 6, 7

### C. Configuration Checklist

#### Pre-Deployment Configuration

- [ ] Database backup completed
- [ ] Admin account verified
- [ ] User roles assigned correctly
- [ ] Site/department assignments complete
- [ ] Device site/department assignments complete
- [ ] Staging environment tested
- [ ] Rollback plan documented
- [ ] User communication sent

#### Post-Deployment Configuration

- [ ] Agent tokens generated
- [ ] Agent tokens distributed
- [ ] Agents updated and restarted
- [ ] Session storage cleared (optional)
- [ ] Monitoring alerts configured
- [ ] Performance baseline established
- [ ] Audit log retention policy set
- [ ] Documentation updated

### D. Security Hardening Checklist

#### Application Security

- [ ] All routes have appropriate decorators
- [ ] Global write guard is active
- [ ] Scoped queries applied to all data access
- [ ] Agent endpoints require tokens
- [ ] Session validation on critical operations
- [ ] Registration hardening active
- [ ] Audit logging enabled

#### Infrastructure Security

- [ ] HTTPS enabled (TLS 1.2+)
- [ ] Session cookies marked secure and httponly
- [ ] CSRF protection enabled
- [ ] Rate limiting configured
- [ ] Database access restricted
- [ ] Application logs secured
- [ ] Audit logs backed up

#### Operational Security

- [ ] Regular security audits scheduled
- [ ] Audit log review process established
- [ ] Incident response plan documented
- [ ] User access review process
- [ ] Token rotation policy
- [ ] Session timeout configured
- [ ] Failed login monitoring

### E. Rollback Decision Matrix

| Severity | Symptoms | Action | Rollback Phase |
|----------|----------|--------|----------------|
| **Critical** | Admin locked out | Immediate rollback | All phases |
| **Critical** | Data loss/corruption | Immediate rollback | All phases |
| **Critical** | Complete service outage | Immediate rollback | All phases |
| **High** | Cross-scope data leakage | Rollback Phase 3 | Phase 3 only |
| **High** | All agents offline | Rollback Phase 4 | Phase 4 only |
| **Medium** | Performance degradation >50% | Investigate, consider rollback | Specific phase |
| **Medium** | Legitimate users blocked | Fix user assignments | No rollback |
| **Low** | Increased 403 errors | Expected behavior | No rollback |
| **Low** | Audit logs not created | Fix audit logging | No rollback |

### F. Success Criteria

#### Deployment Success

- [ ] All phases deployed without errors
- [ ] All tests passing
- [ ] No critical issues reported
- [ ] Performance within acceptable range (<20% degradation)
- [ ] All user roles functioning correctly
- [ ] Agent metrics submitting successfully
- [ ] Audit logs being created

#### Security Success

- [ ] Non-admin users cannot access admin routes
- [ ] Viewers cannot perform write operations
- [ ] Managers see only their site data
- [ ] Operators see only their department data
- [ ] Agent endpoints require valid tokens
- [ ] Session manipulation attempts blocked
- [ ] Registration privilege escalation prevented
- [ ] All sensitive operations audited

#### Operational Success

- [ ] No increase in support tickets
- [ ] User satisfaction maintained
- [ ] System stability maintained
- [ ] Performance targets met
- [ ] Monitoring and alerts working
- [ ] Documentation complete
- [ ] Team trained on new system

---

## Document Version

**Version**: 1.0  
**Last Updated**: 2024  
**Author**: Network Monitoring System Team  
**Status**: Production Ready

## Change Log

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2024 | Initial deployment guide | System Team |

---

**END OF DEPLOYMENT GUIDE**
