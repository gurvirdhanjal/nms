# RBAC Authorization Enforcement - Deployment Checklist

**Quick Reference**: Use this checklist to ensure all critical deployment steps are completed.  
**Detailed Guide**: See [DEPLOYMENT.md](./DEPLOYMENT.md) for comprehensive instructions.

---

## Pre-Deployment Checklist

### Critical Prerequisites
- [ ] **Database Backup**: Full backup completed and verified
- [ ] **Admin Account**: At least one admin account exists with known credentials
- [ ] **Test Suite**: All tests passing (`pytest tests/test_rbac_*.py -v`)
- [ ] **Staging Tests**: All 7 phases deployed and verified in staging environment
- [ ] **User Communication**: Users notified of upcoming security enhancements
- [ ] **Rollback Plan**: Team familiar with rollback procedures for each phase

### Environment Verification
- [ ] Python 3.8+ installed
- [ ] Database accessible (PostgreSQL/SQLite)
- [ ] Application logs configured and accessible
- [ ] Monitoring/alerting systems active
- [ ] Ability to restart application server confirmed

### Data Validation
- [ ] All users have roles assigned (admin/manager/operator/viewer)
- [ ] Managers have site_id assigned
- [ ] Operators/Viewers have department_id assigned
- [ ] Devices have site_id or department_id assigned where applicable
- [ ] At least one admin user exists for emergency access

---

## Deployment Execution

### Phase 1: Route Protection (15 min)
- [ ] Deploy code changes
- [ ] Restart application: `sudo systemctl restart network-monitor`
- [ ] Test admin route access (admin: 200 OK, non-admin: 403 Forbidden)
- [ ] Verify admin maintains full access
- [ ] Monitor for unexpected 403 errors

### Phase 2: Write Guard (20 min)
- [ ] Deploy code changes
- [ ] Restart application
- [ ] Test viewer blocked from writes (403 Forbidden)
- [ ] Test operator can write with permissions (200 OK)
- [ ] Test GET requests unaffected
- [ ] Monitor write operation performance (<5ms overhead)

### Phase 3: Scoped Queries (30 min) ⚠️ HIGHEST RISK
- [ ] Deploy code changes
- [ ] Restart application
- [ ] **CRITICAL**: Test manager sees only their site devices
- [ ] **CRITICAL**: Test operator sees only their department devices
- [ ] **CRITICAL**: Test admin sees all devices (no filtering)
- [ ] Test cross-scope isolation (Manager A cannot see Manager B's data)
- [ ] Monitor query performance (<10ms overhead)
- [ ] Verify no empty dashboards (check site/department assignments)

### Phase 4: Agent Tokens (30 min)
- [ ] Run migration: `python migrations/generate_agent_tokens.py`
- [ ] Verify tokens generated for all devices
- [ ] Deploy code changes
- [ ] Restart application
- [ ] Distribute agent tokens to all devices
- [ ] Update agent configurations with tokens
- [ ] Restart all agents
- [ ] Test agent endpoint with valid token (200 OK)
- [ ] Test agent endpoint without token (401 Unauthorized)
- [ ] Verify all agents submitting metrics

### Phase 5: Session Hardening (15 min)
- [ ] Deploy code changes
- [ ] Restart application
- [ ] *Optional*: Clear session storage to force re-login
- [ ] Test critical operations with valid session (200 OK)
- [ ] Monitor session validation performance (<5ms overhead)
- [ ] Watch for unexpected session validation failures

### Phase 6: Registration Hardening (10 min)
- [ ] Deploy code changes
- [ ] Restart application
- [ ] Test first user registration gets admin role
- [ ] Test subsequent registration forced to viewer role
- [ ] Verify privilege escalation blocked (submitted admin → viewer)

### Phase 7: Audit Logging (20 min)
- [ ] Run migration: `python migrations/create_audit_logs_table.py`
- [ ] Verify audit_logs table created
- [ ] Deploy code changes
- [ ] Restart application
- [ ] Perform test operation (device edit/delete)
- [ ] Verify audit log entry created
- [ ] Test audit log viewing interface (admin only)
- [ ] Configure audit log retention policy

---

## Post-Deployment Verification

### Functional Testing
- [ ] **Admin Access**: Can access all routes and see all data
- [ ] **Manager Access**: Blocked from admin routes, sees only site data
- [ ] **Operator Access**: Can write with permissions, sees only department data
- [ ] **Viewer Access**: Blocked from all writes, sees only department data
- [ ] **Agent Authentication**: All agents submitting metrics with tokens
- [ ] **Audit Logging**: All sensitive operations creating audit entries

### Security Validation
- [ ] Non-admin users get 403 on admin routes
- [ ] Viewers get 403 on all write operations
- [ ] Cross-scope data isolation verified (no data leakage)
- [ ] Agent endpoints reject session authentication
- [ ] Registration privilege escalation blocked
- [ ] Session manipulation attempts blocked

### Performance Validation
- [ ] Response times within acceptable range (<20% increase)
- [ ] Database query performance acceptable (<50ms for scoped queries)
- [ ] No database connection pool exhaustion
- [ ] Audit log writes not blocking operations (<5ms)

### Integration Testing
- [ ] Run full test suite: `pytest tests/test_rbac_*.py -v`
- [ ] Run property-based tests: `pytest tests/property_tests/test_rbac_*.py -v`
- [ ] Run security tests: `pytest tests/test_rbac_comprehensive_security.py -v`
- [ ] Run performance tests: `pytest tests/test_rbac_performance.py -v`

---

## Post-Deployment Monitoring

### First 24 Hours - Watch For:

#### Authorization Metrics
- [ ] **403 Errors**: Spike expected for non-admin users (verify legitimate)
- [ ] **401 Errors**: Agent token issues (check token distribution)
- [ ] **Session Validation Failures**: >5/hour indicates potential attack

#### Performance Metrics
- [ ] **Response Time**: P95 <20% increase from baseline
- [ ] **Database Query Time**: Scoped queries <50ms
- [ ] **Audit Log Write Time**: <10ms per operation

#### Security Metrics
- [ ] **Failed Login Attempts**: >5/user/hour indicates brute force
- [ ] **Invalid Agent Tokens**: >10/device/hour indicates compromise
- [ ] **Cross-Scope Access Attempts**: >20/user/hour indicates escalation attempt

#### Operational Metrics
- [ ] **Agent Connectivity**: >90% of devices reporting metrics
- [ ] **Audit Log Growth**: Monitor storage capacity
- [ ] **User Support Tickets**: No significant increase

### Monitoring Commands
```bash
# Count 403 errors by endpoint
grep "403" /var/log/network-monitor/access.log | awk '{print $7}' | sort | uniq -c

# Check session validation failures
grep "Session validation failed" /var/log/network-monitor/rbac.log | wc -l

# Monitor audit log growth (last hour)
python -c "from models.audit_log import AuditLog; from datetime import datetime, timedelta; \
  print(AuditLog.query.filter(AuditLog.timestamp > datetime.utcnow() - timedelta(hours=1)).count())"

# Check agent connectivity
python -c "from models.device import Device; from datetime import datetime, timedelta; \
  recent = Device.query.filter(Device.last_seen > datetime.utcnow() - timedelta(minutes=5)).count(); \
  total = Device.query.count(); print(f'{recent}/{total} devices online')"
```

---

## Rollback Procedures

### Emergency Rollback (All Phases)
```bash
# 1. Revert to pre-RBAC version
git revert <rbac-start-commit>..<rbac-end-commit>
git push origin main

# 2. Restart application
sudo systemctl restart network-monitor

# 3. Verify rollback (non-admin should access admin routes)
curl -X GET http://localhost:5000/user_management \
  -H "Cookie: session=<non-admin-session>" -w "\nStatus: %{http_code}\n"
# Expected: 200 OK (vulnerability restored)
```

### Phase-Specific Rollback
- **Phase 1-2**: Simple revert, restart application
- **Phase 3**: Revert commit or emergency bypass in `scoped_query()`
- **Phase 4**: Revert commit or add session auth fallback
- **Phase 5**: Revert commit or disable validation
- **Phase 6-7**: Simple revert, restart application

### Rollback Decision Matrix
| Severity | Symptoms | Action |
|----------|----------|--------|
| **Critical** | Admin locked out, data loss, service outage | Immediate full rollback |
| **High** | Cross-scope data leakage, all agents offline | Rollback specific phase |
| **Medium** | Performance degradation >50% | Investigate, consider rollback |
| **Low** | Expected 403 errors, audit logs not created | Fix without rollback |

---

## Success Criteria

### Deployment Complete When:
- [ ] All 7 phases deployed without errors
- [ ] All tests passing (unit, integration, property-based, security)
- [ ] No critical issues reported within 24 hours
- [ ] Performance within acceptable range (<20% degradation)
- [ ] All user roles functioning correctly
- [ ] All agents submitting metrics successfully
- [ ] Audit logs being created for all sensitive operations
- [ ] No increase in support tickets
- [ ] Monitoring and alerts operational

---

## Quick Reference

### Test Commands
```bash
# Admin access (should succeed)
curl -X GET http://localhost:5000/user_management -H "Cookie: session=<admin-session>"

# Non-admin blocked (should fail with 403)
curl -X GET http://localhost:5000/user_management -H "Cookie: session=<manager-session>"

# Scoped query (manager sees only their site)
curl -X GET http://localhost:5000/api/devices -H "Cookie: session=<manager-session>" | jq '[.[] | .site_id] | unique'

# Agent token (should succeed)
curl -X POST http://localhost:5000/api/agent/metrics -H "X-Agent-Token: <token>" -d '{"cpu": 50}'
```

### Support Contacts
- **Deployment Issues**: DevOps Team (devops@example.com)
- **Security Issues**: Security Team (security@example.com)
- **Database Issues**: DBA Team (dba@example.com)

---

**Document Version**: 1.0  
**Last Updated**: 2024  
**Status**: Production Ready

For detailed instructions, troubleshooting, and performance optimization, see [DEPLOYMENT.md](./DEPLOYMENT.md).
