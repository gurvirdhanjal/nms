# Security Policy - RBAC Authorization Enforcement

**Document Classification**: Internal Security Policy  
**Version**: 1.0  
**Effective Date**: 2024  
**Review Cycle**: Annual  
**Owner**: Security Team  
**Approver**: Chief Information Security Officer (CISO)

---

## 1. Executive Summary

This document establishes the security policy for the Role-Based Access Control (RBAC) authorization system implemented in the Flask multi-tenant Network Monitoring System. The policy addresses a critical security vulnerability where authentication existed but authorization was not enforced, allowing any authenticated user to access and modify resources across all departments and sites.

The RBAC implementation enforces authorization at three levels:
- **Route-level**: Controls which routes users can access based on their role
- **Operation-level**: Controls which write operations users can perform based on permissions
- **Data-level**: Controls which data users can see based on their site/department scope

This policy applies to all users, administrators, and automated agents accessing the Network Monitoring System.

### 1.1 Policy Objectives

1. **Prevent Unauthorized Access**: Ensure users can only access resources within their assigned scope
2. **Enforce Least Privilege**: Grant minimum necessary permissions based on role
3. **Maintain Audit Trail**: Log all sensitive operations for compliance and investigation
4. **Protect Data Integrity**: Prevent unauthorized modification of system data
5. **Support Compliance**: Meet regulatory requirements for access control and audit logging

### 1.2 Scope

This policy covers:
- User authentication and authorization
- Role-based access control (RBAC)
- Row-level security and data scoping
- Agent token authentication
- Session security and validation
- Audit logging and retention
- Security monitoring and incident response


---

## 2. RBAC Implementation Policy

### 2.1 Role Hierarchy and Definitions

The system implements a four-tier role hierarchy with decreasing levels of access:

**Admin > Manager > Operator > Viewer**

#### 2.1.1 Admin Role
- **Scope**: Global (all sites, all departments)
- **Purpose**: System administration and configuration
- **Access Level**: Full system access without restrictions
- **Assignment**: Limited to IT administrators and system owners
- **Restrictions**: 
  - Only the first registered user automatically receives admin role
  - Subsequent admin assignments require existing admin approval
  - Admin accounts must use strong passwords (minimum 12 characters)
  - Admin sessions expire after 4 hours of inactivity

#### 2.1.2 Manager Role
- **Scope**: Site-level (assigned site and all departments within that site)
- **Purpose**: Site management and operational oversight
- **Access Level**: Full access within assigned site
- **Assignment**: Requires admin approval and valid site assignment
- **Restrictions**:
  - Must have site_id assigned in user profile
  - Cannot access other sites or system configuration
  - Cannot create or modify admin users
  - Sessions expire after 8 hours of inactivity

#### 2.1.3 Operator Role
- **Scope**: Department-level (assigned department only)
- **Purpose**: Day-to-day operational tasks within department
- **Access Level**: Read and write access within assigned department
- **Assignment**: Requires manager or admin approval and valid department assignment
- **Restrictions**:
  - Must have department_id assigned in user profile
  - Cannot access other departments or manage users
  - Cannot modify site or department configurations
  - Sessions expire after 12 hours of inactivity

#### 2.1.4 Viewer Role
- **Scope**: Department-level (assigned department only)
- **Purpose**: Read-only monitoring and reporting
- **Access Level**: Read-only access within assigned department
- **Assignment**: Default role for new user registrations
- **Restrictions**:
  - Must have department_id assigned in user profile
  - Cannot perform any write operations
  - Cannot access configuration or management interfaces
  - Sessions expire after 24 hours of inactivity

### 2.2 Permission System

#### 2.2.1 Permission Naming Convention
Permissions follow the format: `{resource}.{action}`

**Resources**: dashboard, reports, devices, monitoring, scanning, tracking, snmp, server_metrics, service_checks, file_transfer, maintenance, users

**Actions**: view, edit, create, delete, run, export

**Special Permissions**:
- `*` (wildcard): Admin-only, grants all permissions
- `admin`: Explicit admin-only permission
- `public`: No authentication required

#### 2.2.2 Role Permission Mappings

**Admin Permissions**: `{'*'}` (all permissions)

**Manager Permissions**:
```
dashboard.view, reports.view, reports.export, devices.view, devices.edit,
monitoring.view, scanning.view, scanning.run, tracking.view, snmp.view,
server_metrics.view, service_checks.view, file_transfer.view,
maintenance.view, maintenance.edit, users.view
```

**Operator Permissions**:
```
dashboard.view, reports.view, devices.view, devices.edit, monitoring.view,
scanning.view, scanning.run, tracking.view, snmp.view, server_metrics.view,
service_checks.view, file_transfer.view, maintenance.view
```

**Viewer Permissions**:
```
dashboard.view, reports.view, devices.view, monitoring.view, tracking.view,
snmp.view, server_metrics.view, service_checks.view
```

### 2.3 Route Classification and Protection

Routes are classified into four security tiers:

#### Tier 1 - Admin Only
- User management, site management, subnet management
- Discovery settings, system configuration
- **Protection**: `@require_role('admin')` decorator
- **Violation Response**: 403 Forbidden

#### Tier 2 - Operational Write
- Device management, scanning operations, alert management
- Department management (managers only)
- **Protection**: `@require_permission('{permission}')` decorator
- **Violation Response**: 403 Forbidden

#### Tier 3 - Read-only Scoped
- Dashboard, monitoring, reports (read operations)
- **Protection**: `@require_login` + scoped queries
- **Violation Response**: 403 Forbidden or filtered data

#### Tier 4 - Agent/Internal
- Agent metric submission endpoints
- **Protection**: `@require_agent_token` decorator
- **Violation Response**: 401 Unauthorized

### 2.4 Global Write Guard

All write operations (POST/PUT/PATCH/DELETE) are subject to global permission validation:

1. Request arrives with write method
2. System checks `ENDPOINT_PERMISSIONS` mapping
3. User's permissions validated against required permission
4. Request blocked with 403 if unauthorized
5. Request proceeds if authorized

**Policy Requirements**:
- All write endpoints must be mapped in `ENDPOINT_PERMISSIONS`
- Unmapped write endpoints default to admin-only
- Permission checks occur before route handler execution
- Failed permission checks are logged for security monitoring


---

## 3. Row-Level Security Policy

### 3.1 Data Scoping Requirements

Row-level security ensures users can only access data within their assigned scope:

#### 3.1.1 Admin Scoping
- **Scope**: None (global access)
- **Filter**: No filtering applied
- **Rationale**: Admins require global visibility for system management
- **Audit**: All admin data access logged

#### 3.1.2 Manager Scoping
- **Scope**: Site-level
- **Filter**: `site_id = user.site_id` OR `department.site_id = user.site_id`
- **Enforcement**: Applied to all queries via `scoped_query()` function
- **Validation**: Manager must have valid site_id assignment
- **Violation**: Users without site_id see no data

#### 3.1.3 Operator/Viewer Scoping
- **Scope**: Department-level
- **Filter**: `department_id = user.department_id`
- **Enforcement**: Applied to all queries via `scoped_query()` function
- **Validation**: User must have valid department_id assignment
- **Violation**: Users without department_id see no data

### 3.2 Scoped Models

The following data models have row-level security applied:

#### 3.2.1 Device Model
- **Fields**: `site_id`, `department_id`
- **Manager Access**: Devices in assigned site or departments within site
- **Operator/Viewer Access**: Devices in assigned department only
- **Admin Access**: All devices

#### 3.2.2 Department Model
- **Fields**: `site_id`
- **Manager Access**: Departments in assigned site
- **Operator/Viewer Access**: Only assigned department
- **Admin Access**: All departments

#### 3.2.3 Site Model
- **Manager Access**: Only assigned site
- **Operator/Viewer Access**: Only site their department belongs to
- **Admin Access**: All sites

#### 3.2.4 User Model
- **Fields**: `site_id`, `department_id`
- **Manager Access**: Users in assigned site or departments within site
- **Operator/Viewer Access**: Users in assigned department
- **Admin Access**: All users

#### 3.2.5 Related Models
- ServerHealthLog, DeviceInterface, Alert models
- **Scoping**: Applied via device relationship
- **Enforcement**: Automatic through device scoping

### 3.3 Scoping Implementation Requirements

**Mandatory Requirements**:
1. All data access queries MUST use `scoped_query(model)` function
2. Direct `Model.query` calls are PROHIBITED except for:
   - Admin-only routes with explicit `@require_role('admin')`
   - Configuration tables without scoping requirements
   - Public routes (login, register)
3. Scoping violations MUST be logged for security review
4. Cross-scope access attempts MUST trigger security alerts

**Code Review Requirements**:
- All new routes must be reviewed for proper scoping
- Pull requests must include scoping verification
- Automated tests must validate scoping enforcement

### 3.4 Data Isolation Policy

**Cross-Scope Isolation**:
- Manager A MUST NOT see Manager B's site data
- Operator A MUST NOT see Operator B's department data
- Data leakage between scopes is a CRITICAL security violation

**Enforcement Mechanisms**:
1. Database-level filtering via scoped queries
2. Application-level permission checks
3. Session validation for critical operations
4. Regular security audits and penetration testing

**Violation Response**:
- Immediate investigation of cross-scope access attempts
- User account suspension pending investigation
- Incident report to security team
- Review of access logs and audit trail


---

## 4. Agent Token Authentication Policy

### 4.1 Token-Based Authentication Requirements

Agent endpoints use token-based authentication instead of session-based authentication to prevent unauthorized access and user impersonation.

#### 4.1.1 Token Generation
- **Algorithm**: `secrets.token_urlsafe(32)` (cryptographically secure)
- **Length**: 32 bytes (256 bits of entropy)
- **Format**: URL-safe base64 encoded string
- **Uniqueness**: Each device has a unique token
- **Storage**: Stored in `device.agent_token` field (encrypted at rest)

#### 4.1.2 Token Distribution
- **Initial Generation**: Tokens generated during device creation or migration
- **Distribution Method**: 
  - API endpoint for authorized users (`GET /devices/{id}/get_token`)
  - Secure configuration management systems
  - Manual distribution for high-security environments
- **Access Control**: Only users with `devices.edit` permission can view tokens
- **Scoping**: Users can only access tokens for devices in their scope

#### 4.1.3 Token Usage
- **Header**: `X-Agent-Token: <token>`
- **Endpoints**: All `/api/agent/*` endpoints
- **Validation**: Token validated against database on each request
- **Rejection**: Invalid or missing tokens return 401 Unauthorized
- **Session Auth**: Session authentication MUST be rejected for agent endpoints

### 4.2 Token Security Requirements

#### 4.2.1 Token Rotation
- **Frequency**: Tokens SHOULD be rotated every 90 days
- **Trigger Events**: 
  - Suspected compromise
  - Device decommissioning
  - Security incident
  - User request
- **Process**: Use `POST /devices/{id}/regenerate_token` endpoint
- **Impact**: Old token immediately invalidated

#### 4.2.2 Token Storage and Transmission
- **Storage**: 
  - Database: Encrypted at rest
  - Agent configuration: File permissions 0600 (owner read/write only)
  - Never stored in version control or logs
- **Transmission**: 
  - HTTPS only (TLS 1.2 or higher)
  - Never transmitted in URL parameters
  - Never transmitted in response bodies except token management endpoints

#### 4.2.3 Token Compromise Response
1. **Detection**: Monitor for invalid token attempts (>10/device/hour)
2. **Response**: 
   - Immediately regenerate token
   - Investigate source of invalid attempts
   - Review audit logs for suspicious activity
   - Update agent configuration with new token
3. **Escalation**: Report to security team if compromise confirmed

### 4.3 Agent Authentication Monitoring

**Required Monitoring**:
- Invalid token attempts per device
- Token regeneration frequency
- Agent connectivity status
- Unusual agent activity patterns

**Alert Thresholds**:
- >10 invalid token attempts per device per hour
- >5 token regenerations per device per day
- Agent offline >15 minutes (connectivity issue)
- Agent submitting metrics from unexpected IP address


---

## 5. Session Security Policy

### 5.1 Session Management Requirements

#### 5.1.1 Session Creation
Upon successful authentication, the following session variables are stored:

```python
session['logged_in'] = True
session['user_id'] = user.id
session['username'] = user.username
session['role'] = user.role
session['site_id'] = user.site_id
session['department_id'] = user.department_id
session['auth_source'] = user.auth_source
```

**Requirements**:
- All session variables MUST be set during login
- Session cookies MUST be HTTP-only (not accessible via JavaScript)
- Session cookies MUST be Secure (HTTPS only)
- Session cookies MUST have SameSite=Lax or Strict

#### 5.1.2 Session Timeout
- **Admin**: 4 hours of inactivity
- **Manager**: 8 hours of inactivity
- **Operator**: 12 hours of inactivity
- **Viewer**: 24 hours of inactivity
- **Absolute Maximum**: 7 days (regardless of activity)

**Enforcement**:
- Timeout enforced at application level
- Expired sessions automatically cleared
- Users redirected to login page on timeout

#### 5.1.3 Session Storage
- **Backend**: Server-side session storage (Redis or database)
- **Client**: Encrypted session cookie with session ID only
- **Encryption**: AES-256 encryption for session data
- **Integrity**: HMAC signature to prevent tampering

### 5.2 Session Validation Policy

#### 5.2.1 Critical Operations Requiring Validation

The following operations require database validation of session variables:

1. **User Management**: Creating, editing, or deactivating users
2. **Site Management**: Creating, updating, or deleting sites
3. **Department Management**: Creating, updating, or deleting departments
4. **Bulk Device Operations**: Mass deletion or modification
5. **System Configuration**: Discovery settings, SNMP configuration

**Validation Process**:
1. Extract `user_id` from session
2. Load user from database
3. Validate `session['role']` matches `user.role`
4. Validate `session['site_id']` matches `user.site_id`
5. Validate `session['department_id']` matches `user.department_id`
6. Log warning if mismatch detected
7. Return 401 and force re-login if validation fails

#### 5.2.2 Session Manipulation Detection

**Indicators of Session Manipulation**:
- Role mismatch (session says admin, database says viewer)
- Site/department ID mismatch
- Session variables modified without re-authentication
- Multiple concurrent sessions with different roles

**Response to Detected Manipulation**:
1. Immediately invalidate session
2. Force user re-login
3. Log security event with details
4. Alert security team if repeated attempts
5. Temporarily suspend account if confirmed attack

### 5.3 Session Security Best Practices

#### 5.3.1 For Users
- Log out when finished using the system
- Do not share session cookies or credentials
- Report suspicious session behavior immediately
- Use strong, unique passwords
- Enable multi-factor authentication if available

#### 5.3.2 For Administrators
- Regularly review active sessions
- Implement session monitoring and alerting
- Enforce password complexity requirements
- Implement account lockout after failed login attempts
- Review session logs for suspicious patterns

#### 5.3.3 For Developers
- Always use `@require_validated_session` for critical operations
- Never trust session data without validation
- Implement proper session timeout handling
- Use secure session configuration
- Test session security in code reviews


---

## 6. Audit Logging and Retention Policy

### 6.1 Audit Logging Requirements

#### 6.1.1 Purpose
Audit logging provides an immutable trail of sensitive operations for:
- Security incident investigation
- Compliance reporting
- User activity monitoring
- Forensic analysis
- Change tracking

#### 6.1.2 Audit Log Data Model

Each audit log entry contains:

**Who Performed the Action**:
- `user_id`: Database ID of user (nullable if user deleted)
- `username`: Username at time of action (denormalized for immutability)
- `user_role`: Role at time of action (denormalized)

**What Action Was Performed**:
- `action`: Action type (create, update, delete, login, etc.)
- `entity_type`: Type of entity affected (device, user, site, etc.)
- `entity_id`: ID of affected entity (nullable)
- `entity_name`: Name of affected entity (denormalized)

**When and Where**:
- `timestamp`: UTC timestamp of action
- `ip_address`: Client IP address
- `user_agent`: Browser/client information

**Additional Context**:
- `description`: Human-readable description
- `changes`: JSON object with before/after values for updates

### 6.2 Audited Operations

#### 6.2.1 Device Operations
- **Create**: Device creation with initial configuration
- **Update**: Device configuration changes
- **Delete**: Device deletion (includes device name and IP)
- **Bulk Operations**: Mass device operations (includes count)
- **Monitoring Toggle**: Enabling/disabling device monitoring

#### 6.2.2 User Management Operations
- **Create**: User account creation (includes assigned role)
- **Update**: User profile changes (includes role changes with before/after)
- **Deactivate**: User account deactivation
- **Role Change**: User role modifications (CRITICAL - includes before/after)
- **Password Reset**: Password reset requests and completions

#### 6.2.3 Site and Department Operations
- **Create**: Site/department creation
- **Update**: Site/department configuration changes
- **Delete**: Site/department deletion
- **Device Assignment**: Assigning devices to sites/departments
- **User Assignment**: Assigning users to sites/departments

#### 6.2.4 Alert Operations
- **Acknowledge**: Alert acknowledgment
- **Resolve**: Alert resolution (includes resolution notes)
- **Escalate**: Alert escalation

#### 6.2.5 Configuration Changes
- **Discovery Settings**: System-wide discovery configuration changes
- **SNMP Configuration**: SNMP credential and configuration updates
- **Subnet Management**: Network subnet additions and deletions
- **Maintenance Windows**: Maintenance window creation and modification

#### 6.2.6 Authentication Events
- **Login Success**: Successful user login
- **Login Failure**: Failed login attempts (includes attempted username)
- **Logout**: User logout
- **Session Timeout**: Automatic session expiration
- **Password Change**: User password changes

### 6.3 Audit Log Retention Policy

#### 6.3.1 Retention Periods

**Standard Retention**:
- **Active Logs**: 90 days in primary database
- **Archived Logs**: 7 years in secure archive storage
- **Compliance Logs**: Indefinite retention for compliance-critical operations

**Compliance-Critical Operations** (indefinite retention):
- User role changes (especially to admin)
- Site/department deletions
- Bulk device deletions
- System configuration changes
- Security incident related logs

#### 6.3.2 Archive Process

**Monthly Archive Process**:
1. Export logs older than 90 days to archive format (JSON or CSV)
2. Compress archived logs (gzip or similar)
3. Store in secure, immutable storage (S3 with versioning, tape backup)
4. Verify archive integrity (checksums)
5. Delete archived logs from primary database
6. Document archive location and retrieval process

**Archive Storage Requirements**:
- Encrypted at rest (AES-256)
- Immutable (write-once, read-many)
- Geographically redundant
- Access controlled (admin only)
- Integrity verified (checksums)

#### 6.3.3 Log Deletion Policy

**Prohibited Deletions**:
- Audit logs MUST NOT be deleted or modified
- User deletion sets `user_id` to NULL but preserves username
- Entity deletion preserves entity_name in audit log
- No manual deletion of audit logs permitted

**Permitted Deletions**:
- Automated deletion of logs older than retention period
- Deletion must be logged in separate audit trail
- Deletion requires approval from security team
- Deletion must comply with legal hold requirements

### 6.4 Audit Log Access and Review

#### 6.4.1 Access Control
- **View Access**: Admin role only
- **Export Access**: Admin role with approval
- **API Access**: Admin role with rate limiting
- **Archive Access**: Security team with documented justification

#### 6.4.2 Regular Review Requirements

**Daily Review** (automated):
- Failed login attempts (>5 per user)
- Invalid agent token attempts (>10 per device)
- Session validation failures (>5 per hour)
- Cross-scope access attempts (>20 per user)

**Weekly Review** (manual):
- User role changes
- Bulk operations (device deletions, etc.)
- System configuration changes
- Unusual activity patterns

**Monthly Review** (compliance):
- All admin actions
- All user management operations
- All site/department changes
- Compliance report generation

**Quarterly Review** (security audit):
- Comprehensive security audit
- Access pattern analysis
- Anomaly detection
- Policy compliance verification

### 6.5 Audit Log Monitoring and Alerting

#### 6.5.1 Real-Time Alerts

**Critical Alerts** (immediate notification):
- Admin role assignment to user
- Bulk device deletion (>10 devices)
- System configuration changes outside maintenance window
- Multiple failed login attempts (>5 in 5 minutes)
- Session validation failures (>5 per hour)

**High Priority Alerts** (15-minute delay):
- User role changes
- Site/department deletions
- Agent token regeneration (>5 per day)
- Cross-scope access attempts (>20 per hour)

**Medium Priority Alerts** (1-hour delay):
- Device deletions
- Alert resolutions
- Configuration changes
- Unusual activity patterns

#### 6.5.2 Alert Recipients
- **Critical**: Security team, CISO, on-call engineer
- **High Priority**: Security team, system administrators
- **Medium Priority**: System administrators


---

## 7. Security Monitoring and Incident Response

### 7.1 Security Monitoring Requirements

#### 7.1.1 Authorization Metrics

**403 Forbidden Errors**:
- **Metric**: Count of 403 responses per endpoint per user
- **Baseline**: Establish baseline during first week post-deployment
- **Alert Threshold**: >50% increase from baseline
- **Investigation**: Verify if legitimate users are blocked

**401 Unauthorized Errors**:
- **Metric**: Count of 401 responses (agent endpoints)
- **Alert Threshold**: >10 per device per hour
- **Investigation**: Check agent token validity and distribution

**Session Validation Failures**:
- **Metric**: Count of session validation failures per user
- **Alert Threshold**: >5 per user per hour
- **Investigation**: Potential session manipulation attack

**Cross-Scope Access Attempts**:
- **Metric**: Attempts to access out-of-scope data
- **Alert Threshold**: >20 per user per hour
- **Investigation**: Potential privilege escalation attempt

#### 7.1.2 Performance Metrics

**Request Latency**:
- **Metric**: P95 response time per endpoint
- **Baseline**: Measure before RBAC deployment
- **Alert Threshold**: >20% increase from baseline
- **Investigation**: Query performance, database indexes

**Database Query Time**:
- **Metric**: Average query execution time for scoped queries
- **Alert Threshold**: >50ms for scoped queries
- **Investigation**: Missing indexes, query optimization

**Audit Log Write Time**:
- **Metric**: Time to write audit log entry
- **Alert Threshold**: >10ms per operation
- **Investigation**: Database performance, storage capacity

#### 7.1.3 Security Metrics

**Failed Login Attempts**:
- **Metric**: Failed login attempts per user per hour
- **Alert Threshold**: >5 per user per hour
- **Investigation**: Brute force attack, credential stuffing

**Invalid Agent Tokens**:
- **Metric**: Invalid token attempts per device per hour
- **Alert Threshold**: >10 per device per hour
- **Investigation**: Token compromise, misconfiguration

**Privilege Escalation Attempts**:
- **Metric**: Registration attempts with admin role (non-first user)
- **Alert Threshold**: >1 per day
- **Investigation**: Potential attack, user education needed

### 7.2 Incident Response Procedures

#### 7.2.1 Incident Classification

**Critical Incidents** (immediate response):
- Cross-scope data leakage confirmed
- Admin account compromise
- Mass unauthorized data access
- System-wide authorization bypass
- Audit log tampering or deletion

**High Priority Incidents** (1-hour response):
- Multiple failed authorization attempts
- Session manipulation detected
- Agent token compromise
- Privilege escalation attempt
- Unusual admin activity

**Medium Priority Incidents** (4-hour response):
- Repeated 403 errors for legitimate users
- Performance degradation >50%
- Audit log gaps or inconsistencies
- Configuration drift

**Low Priority Incidents** (24-hour response):
- Expected 403 errors (user education)
- Minor performance issues
- Non-critical configuration issues

#### 7.2.2 Incident Response Steps

**Step 1: Detection and Triage** (0-15 minutes)
1. Alert received via monitoring system
2. On-call engineer reviews alert details
3. Classify incident severity
4. Escalate to security team if critical/high priority
5. Document initial findings

**Step 2: Containment** (15-60 minutes)
1. Identify affected users, devices, or data
2. Suspend compromised accounts if necessary
3. Regenerate compromised tokens
4. Block suspicious IP addresses
5. Preserve evidence (logs, database snapshots)

**Step 3: Investigation** (1-4 hours)
1. Review audit logs for suspicious activity
2. Analyze access patterns and anomalies
3. Identify root cause and attack vector
4. Determine scope of compromise
5. Document findings and timeline

**Step 4: Remediation** (4-24 hours)
1. Fix identified vulnerabilities
2. Restore affected data from backups if needed
3. Update security controls
4. Deploy patches or configuration changes
5. Verify fix effectiveness

**Step 5: Recovery** (24-48 hours)
1. Restore normal operations
2. Re-enable suspended accounts (if appropriate)
3. Communicate with affected users
4. Update documentation
5. Schedule post-incident review

**Step 6: Post-Incident Review** (within 1 week)
1. Conduct lessons learned session
2. Update incident response procedures
3. Implement preventive measures
4. Update security policies
5. Provide training if needed

#### 7.2.3 Escalation Procedures

**Escalation Path**:
1. On-call engineer (initial response)
2. Security team lead (critical/high priority)
3. CISO (critical incidents, data breach)
4. Legal team (compliance violations, data breach)
5. Executive management (major incidents)

**Escalation Criteria**:
- Data breach or suspected breach
- Compromise of admin accounts
- System-wide security failure
- Compliance violations
- Media attention or public disclosure

### 7.3 Security Testing and Validation

#### 7.3.1 Regular Security Testing

**Weekly Testing**:
- Automated security scans
- Vulnerability scanning
- Dependency updates and patching

**Monthly Testing**:
- Manual security testing
- Authorization boundary testing
- Session security testing
- Agent token security testing

**Quarterly Testing**:
- Penetration testing by external firm
- Security audit and compliance review
- Disaster recovery testing
- Incident response drill

**Annual Testing**:
- Comprehensive security assessment
- Third-party security audit
- Compliance certification (if required)
- Policy review and update

#### 7.3.2 Test Scenarios

**Authorization Testing**:
- Non-admin accessing admin routes (expect 403)
- Viewer performing write operations (expect 403)
- Cross-scope data access attempts (expect filtered data)
- Session manipulation attempts (expect 401)
- Privilege escalation via registration (expect forced viewer role)

**Performance Testing**:
- Load testing with scoped queries
- Stress testing with concurrent users
- Performance regression testing
- Database query optimization validation

**Audit Testing**:
- Verify all sensitive operations logged
- Test audit log integrity
- Validate retention policy enforcement
- Test archive and retrieval process


---

## 8. Compliance and Regulatory Requirements

### 8.1 Access Control Compliance

#### 8.1.1 Principle of Least Privilege
**Requirement**: Users must be granted the minimum level of access necessary to perform their job functions.

**Implementation**:
- Default role for new users is Viewer (read-only)
- Role upgrades require manager or admin approval
- Permissions explicitly mapped to roles
- Regular access reviews (quarterly)

**Validation**:
- Audit user role assignments quarterly
- Review permission mappings annually
- Document justification for elevated privileges
- Remove access when no longer needed

#### 8.1.2 Separation of Duties
**Requirement**: Critical operations should require multiple approvals or roles.

**Implementation**:
- User creation and role assignment separated
- Admin role assignment requires existing admin approval
- Critical operations require session validation
- Audit logs reviewed by separate security team

**Validation**:
- Review admin actions monthly
- Verify separation of duties in critical workflows
- Test for single-user privilege escalation paths

#### 8.1.3 Access Review and Recertification
**Requirement**: User access must be reviewed and recertified periodically.

**Schedule**:
- **Quarterly**: Review all user role assignments
- **Semi-Annual**: Review admin and manager accounts
- **Annual**: Comprehensive access recertification

**Process**:
1. Generate user access report from database
2. Send to managers for review and approval
3. Identify and remove unnecessary access
4. Document review results
5. Update user roles as needed

### 8.2 Audit and Logging Compliance

#### 8.2.1 Audit Trail Requirements
**Requirement**: System must maintain comprehensive audit trail of all security-relevant events.

**Implementation**:
- All sensitive operations logged to audit_logs table
- Logs include who, what, when, where, and why
- Logs are immutable (no updates or deletes)
- Logs retained per retention policy (90 days active, 7 years archive)

**Validation**:
- Verify audit log coverage (all required operations)
- Test audit log integrity (no tampering)
- Validate retention policy enforcement
- Test archive and retrieval process

#### 8.2.2 Log Protection
**Requirement**: Audit logs must be protected from unauthorized access, modification, or deletion.

**Implementation**:
- Audit log access restricted to admin role
- Database-level constraints prevent updates/deletes
- Logs archived to immutable storage
- Access to logs is itself audited

**Validation**:
- Test unauthorized access attempts (expect 403)
- Verify logs cannot be modified or deleted
- Validate archive integrity (checksums)
- Review audit log access logs

#### 8.2.3 Log Monitoring and Review
**Requirement**: Audit logs must be regularly reviewed for security incidents and policy violations.

**Implementation**:
- Automated daily review for critical events
- Manual weekly review by security team
- Monthly compliance reporting
- Quarterly security audit

**Validation**:
- Verify review schedule adherence
- Document review findings
- Track remediation of identified issues
- Report to management quarterly

### 8.3 Data Protection Compliance

#### 8.3.1 Data Scoping and Isolation
**Requirement**: Multi-tenant systems must ensure data isolation between tenants.

**Implementation**:
- Row-level security via scoped queries
- Site-level isolation for managers
- Department-level isolation for operators/viewers
- Cross-scope access attempts logged and blocked

**Validation**:
- Test cross-scope access attempts (expect filtered data)
- Verify data isolation between sites/departments
- Penetration testing for data leakage
- Regular security audits

#### 8.3.2 Data Access Logging
**Requirement**: Access to sensitive data must be logged for audit purposes.

**Implementation**:
- All data access via scoped queries (enforced)
- Admin data access logged in audit trail
- Bulk data exports logged
- API access logged with user and scope

**Validation**:
- Verify data access logging coverage
- Review data access patterns monthly
- Investigate unusual access patterns
- Report to compliance team quarterly

### 8.4 Authentication and Session Management Compliance

#### 8.4.1 Strong Authentication
**Requirement**: System must enforce strong authentication mechanisms.

**Implementation**:
- Password complexity requirements (minimum 8 characters)
- Account lockout after failed attempts (5 attempts)
- Session timeout based on role (4-24 hours)
- Session validation for critical operations

**Validation**:
- Test password complexity enforcement
- Verify account lockout functionality
- Test session timeout enforcement
- Validate session security controls

#### 8.4.2 Session Security
**Requirement**: Sessions must be protected from hijacking and manipulation.

**Implementation**:
- HTTP-only, secure session cookies
- Session validation for critical operations
- Session manipulation detection and response
- Automatic session expiration

**Validation**:
- Test session cookie security flags
- Attempt session manipulation (expect 401)
- Verify session timeout enforcement
- Test session validation on critical operations

### 8.5 Compliance Reporting

#### 8.5.1 Monthly Compliance Report
**Contents**:
- User access review summary
- Audit log statistics
- Security incidents and response
- Policy violations and remediation
- Performance metrics

**Recipients**: Security team, compliance officer, management

#### 8.5.2 Quarterly Security Report
**Contents**:
- Comprehensive security audit results
- Access recertification results
- Security testing results
- Compliance status
- Recommendations for improvement

**Recipients**: CISO, compliance officer, executive management

#### 8.5.3 Annual Compliance Certification
**Contents**:
- Full compliance assessment
- Third-party audit results
- Policy review and updates
- Training completion status
- Certification of compliance

**Recipients**: Board of directors, regulatory bodies (if required)


---

## 9. User Responsibilities and Training

### 9.1 User Responsibilities

#### 9.1.1 All Users
**Required Actions**:
- Protect login credentials (never share passwords)
- Log out when finished using the system
- Report suspicious activity immediately
- Follow security policies and procedures
- Complete required security training

**Prohibited Actions**:
- Sharing credentials with other users
- Attempting to access unauthorized resources
- Circumventing security controls
- Modifying session data or cookies
- Using automated tools without authorization

#### 9.1.2 Admin Users
**Additional Responsibilities**:
- Review user access requests promptly
- Conduct regular access reviews
- Monitor audit logs for suspicious activity
- Respond to security incidents
- Maintain admin account security (strong passwords, MFA)
- Document administrative actions
- Follow change management procedures

**Prohibited Actions**:
- Granting unnecessary elevated privileges
- Bypassing security controls
- Sharing admin credentials
- Making undocumented system changes
- Disabling security features without approval

#### 9.1.3 Manager Users
**Additional Responsibilities**:
- Review access for users in their site
- Approve role upgrade requests
- Monitor activity within their site
- Report security concerns to admin
- Ensure proper device and user assignments

**Prohibited Actions**:
- Attempting to access other sites
- Granting access outside their site
- Bypassing scoping restrictions

#### 9.1.4 Developer Responsibilities
**Required Actions**:
- Use `scoped_query()` for all data access
- Apply appropriate decorators to routes
- Create audit logs for sensitive operations
- Follow secure coding practices
- Test authorization in code reviews
- Document security-relevant changes

**Prohibited Actions**:
- Direct `Model.query` calls without scoping
- Bypassing permission checks
- Disabling security features
- Hardcoding credentials or tokens
- Committing sensitive data to version control

### 9.2 Security Training Requirements

#### 9.2.1 Initial Training (All Users)
**Topics**:
- RBAC overview and role hierarchy
- Password security and best practices
- Recognizing phishing and social engineering
- Reporting security incidents
- Data handling and classification

**Duration**: 1 hour  
**Delivery**: Online training module  
**Completion**: Required before system access granted  
**Assessment**: Pass 80% quiz to complete

#### 9.2.2 Role-Specific Training

**Admin Training** (4 hours):
- Advanced RBAC administration
- User access management
- Audit log review and analysis
- Incident response procedures
- Security monitoring and alerting

**Manager Training** (2 hours):
- Site-level access management
- Data scoping and isolation
- Access request approval process
- Security best practices for managers

**Developer Training** (3 hours):
- Secure coding practices
- RBAC implementation details
- Scoped query usage
- Audit logging implementation
- Security testing and validation

#### 9.2.3 Annual Refresher Training
**Topics**:
- Policy updates and changes
- Recent security incidents and lessons learned
- New threats and attack vectors
- Best practices review

**Duration**: 30 minutes  
**Delivery**: Online training module  
**Completion**: Required annually for all users  
**Assessment**: Pass 80% quiz to complete

#### 9.2.4 Training Records
**Requirements**:
- Training completion tracked in database
- Records retained for 3 years
- Compliance reports generated quarterly
- Non-compliance escalated to management

### 9.3 Acceptable Use Policy

#### 9.3.1 Authorized Use
The Network Monitoring System is provided for legitimate business purposes only:
- Monitoring network devices and infrastructure
- Generating reports and analytics
- Managing alerts and incidents
- Configuring system settings (authorized users only)

#### 9.3.2 Prohibited Use
The following activities are strictly prohibited:
- Accessing data outside assigned scope
- Attempting to bypass security controls
- Using the system for personal purposes
- Sharing credentials with others
- Automated scanning or testing without authorization
- Attempting to compromise system security
- Accessing the system from unauthorized locations

#### 9.3.3 Consequences of Policy Violations
**First Violation**:
- Written warning
- Mandatory security training
- Increased monitoring of account

**Second Violation**:
- Account suspension (1-7 days)
- Meeting with management
- Performance improvement plan

**Third Violation or Serious Violation**:
- Account termination
- Employment termination (if employee)
- Legal action (if criminal activity)
- Report to law enforcement (if required)


---

## 10. Policy Governance and Maintenance

### 10.1 Policy Ownership

#### 10.1.1 Policy Owner
**Role**: Chief Information Security Officer (CISO)  
**Responsibilities**:
- Overall policy ownership and approval
- Annual policy review and updates
- Compliance oversight
- Escalation point for security incidents
- Budget approval for security initiatives

#### 10.1.2 Policy Administrator
**Role**: Security Team Lead  
**Responsibilities**:
- Day-to-day policy administration
- Security monitoring and incident response
- User access reviews
- Audit log reviews
- Security training coordination

#### 10.1.3 Technical Owner
**Role**: Lead Developer / DevOps Lead  
**Responsibilities**:
- RBAC implementation and maintenance
- Security control deployment
- Performance monitoring
- Technical documentation
- Developer training and support

### 10.2 Policy Review and Updates

#### 10.2.1 Regular Review Schedule
**Annual Review** (comprehensive):
- Full policy review and update
- Compliance assessment
- Threat landscape analysis
- Technology updates
- Stakeholder feedback incorporation

**Quarterly Review** (targeted):
- Security metrics review
- Incident analysis
- Performance assessment
- Minor policy updates

**Ad-Hoc Review** (as needed):
- After major security incidents
- After significant system changes
- After regulatory changes
- After technology updates

#### 10.2.2 Update Process
1. **Proposal**: Identify need for policy update
2. **Draft**: Create updated policy draft
3. **Review**: Stakeholder review and feedback
4. **Approval**: CISO approval required
5. **Communication**: Notify all affected users
6. **Training**: Update training materials if needed
7. **Implementation**: Deploy policy changes
8. **Verification**: Verify compliance with new policy

#### 10.2.3 Version Control
- Policy version number: Major.Minor (e.g., 1.0, 1.1, 2.0)
- Major version: Significant policy changes
- Minor version: Clarifications or minor updates
- All versions archived with approval date
- Change log maintained in policy document

### 10.3 Exception Management

#### 10.3.1 Exception Request Process
**When Exceptions May Be Granted**:
- Technical limitations prevent policy compliance
- Business requirements conflict with policy
- Temporary exception needed during transition
- Alternative controls provide equivalent security

**Exception Request Requirements**:
1. Written justification for exception
2. Risk assessment and mitigation plan
3. Proposed alternative controls
4. Duration of exception (temporary only)
5. Approval from CISO

**Exception Approval Criteria**:
- Business justification is valid
- Risk is acceptable and mitigated
- Alternative controls are adequate
- Exception is time-limited
- Compensating controls are in place

#### 10.3.2 Exception Tracking
- All exceptions documented in exception register
- Exceptions reviewed quarterly
- Expired exceptions automatically revoked
- Exception compliance monitored
- Annual exception report to management

### 10.4 Policy Communication

#### 10.4.1 Initial Communication
**New Policy Rollout**:
- Email announcement to all users
- Policy posted on internal portal
- Training sessions scheduled
- Q&A sessions for stakeholders
- Documentation updated

#### 10.4.2 Ongoing Communication
**Regular Updates**:
- Quarterly security newsletter
- Policy reminders in system login page
- Security awareness campaigns
- Incident lessons learned sharing
- Best practices sharing

#### 10.4.3 New User Onboarding
**Required Steps**:
1. Provide policy document during onboarding
2. Complete security training before access granted
3. Acknowledge policy acceptance
4. Assign appropriate role based on job function
5. Document training completion

### 10.5 Policy Compliance Monitoring

#### 10.5.1 Automated Compliance Checks
**Daily Checks**:
- User role assignments match job functions
- Session timeouts enforced
- Audit logs being created
- Security controls operational

**Weekly Checks**:
- Access review compliance
- Training completion status
- Exception expiration tracking
- Security metric thresholds

**Monthly Checks**:
- Comprehensive compliance report
- Policy violation tracking
- Remediation status
- Trend analysis

#### 10.5.2 Manual Compliance Audits
**Quarterly Audits**:
- User access review
- Audit log review
- Security control testing
- Policy compliance assessment

**Annual Audits**:
- Comprehensive security audit
- Third-party assessment (if required)
- Compliance certification
- Policy effectiveness review

#### 10.5.3 Non-Compliance Response
**Minor Non-Compliance**:
- Document issue
- Notify responsible party
- Set remediation deadline (30 days)
- Track remediation progress
- Verify resolution

**Major Non-Compliance**:
- Immediate escalation to CISO
- Suspend affected access if necessary
- Investigate root cause
- Implement corrective actions
- Report to management
- Update policy or controls as needed

### 10.6 Related Policies and Standards

#### 10.6.1 Internal Policies
- Information Security Policy
- Access Control Policy
- Password Policy
- Incident Response Policy
- Data Classification Policy
- Acceptable Use Policy

#### 10.6.2 External Standards and Regulations
- ISO 27001 (Information Security Management)
- NIST Cybersecurity Framework
- SOC 2 (Security and Availability)
- GDPR (if applicable - data protection)
- HIPAA (if applicable - healthcare data)
- PCI DSS (if applicable - payment data)

#### 10.6.3 Technical Standards
- OWASP Top 10 (Web Application Security)
- CWE Top 25 (Software Weaknesses)
- SANS Top 20 Critical Security Controls
- NIST SP 800-53 (Security Controls)


---

## 11. Appendices

### Appendix A: Glossary of Terms

**Access Control**: Security mechanism that determines who can access specific resources and what actions they can perform.

**Admin**: Highest privilege role with global access to all system resources and configuration.

**Agent Token**: Cryptographically secure token used to authenticate device agents submitting metrics.

**Audit Log**: Immutable record of security-relevant events including who, what, when, where, and why.

**Authentication**: Process of verifying the identity of a user or system.

**Authorization**: Process of determining what actions an authenticated user is permitted to perform.

**Cross-Scope Access**: Attempt by a user to access data outside their assigned site or department scope.

**Data Scoping**: Row-level security mechanism that filters query results based on user's role and assignments.

**Department**: Organizational unit within a site; operators and viewers are scoped to departments.

**Manager**: Role with site-level access to manage devices and departments within assigned site.

**Operator**: Role with department-level access to manage devices within assigned department.

**Permission**: Specific capability granted to a role (e.g., devices.edit, reports.export).

**RBAC**: Role-Based Access Control - security model that assigns permissions based on user roles.

**Role**: Set of permissions assigned to a user (Admin, Manager, Operator, Viewer).

**Row-Level Security**: Database security mechanism that filters query results based on user attributes.

**Scoped Query**: Database query automatically filtered based on user's role and scope assignments.

**Session**: Authenticated user session maintained by the application after successful login.

**Session Validation**: Process of verifying session variables against database for critical operations.

**Site**: Top-level organizational unit; managers are scoped to sites.

**Viewer**: Role with read-only access to data within assigned department.

### Appendix B: Quick Reference Tables

#### B.1 Role Comparison Matrix

| Feature | Admin | Manager | Operator | Viewer |
|---------|-------|---------|----------|--------|
| **Scope** | Global | Site | Department | Department |
| **User Management** | ✓ | View only | ✗ | ✗ |
| **Site Management** | ✓ | ✗ | ✗ | ✗ |
| **Department Management** | ✓ | ✓ (own site) | ✗ | ✗ |
| **Device Edit** | ✓ | ✓ (own site) | ✓ (own dept) | ✗ |
| **Device View** | ✓ (all) | ✓ (own site) | ✓ (own dept) | ✓ (own dept) |
| **Run Scans** | ✓ | ✓ (own site) | ✓ (own dept) | ✗ |
| **View Reports** | ✓ (all) | ✓ (own site) | ✓ (own dept) | ✓ (own dept) |
| **Export Reports** | ✓ | ✓ | ✗ | ✗ |
| **System Config** | ✓ | ✗ | ✗ | ✗ |
| **View Audit Logs** | ✓ | ✗ | ✗ | ✗ |
| **Session Timeout** | 4 hours | 8 hours | 12 hours | 24 hours |

#### B.2 Audited Operations Reference

| Operation Category | Actions Logged | Retention |
|-------------------|----------------|-----------|
| **Device Operations** | Create, Update, Delete, Bulk Operations | 90 days / 7 years |
| **User Management** | Create, Update, Deactivate, Role Change | Indefinite |
| **Site/Department** | Create, Update, Delete, Assignments | Indefinite |
| **Alerts** | Acknowledge, Resolve, Escalate | 90 days / 7 years |
| **Configuration** | Discovery, SNMP, Subnets, Maintenance | Indefinite |
| **Authentication** | Login, Logout, Failed Attempts | 90 days / 7 years |

#### B.3 Security Alert Thresholds

| Alert Type | Threshold | Priority | Response Time |
|------------|-----------|----------|---------------|
| Failed Logins | >5 per user per hour | High | 1 hour |
| Invalid Agent Tokens | >10 per device per hour | High | 1 hour |
| Session Validation Failures | >5 per user per hour | Critical | Immediate |
| Cross-Scope Access | >20 per user per hour | High | 1 hour |
| Admin Role Assignment | Any occurrence | Critical | Immediate |
| Bulk Device Deletion | >10 devices | Critical | Immediate |
| 403 Errors | >50% increase | Medium | 4 hours |
| Performance Degradation | >20% increase | Medium | 4 hours |

### Appendix C: Security Checklist for Administrators

#### C.1 Daily Tasks
- [ ] Review critical security alerts
- [ ] Check failed login attempts
- [ ] Verify agent connectivity status
- [ ] Monitor system performance metrics

#### C.2 Weekly Tasks
- [ ] Review audit logs for unusual activity
- [ ] Check user role assignments
- [ ] Review 403/401 error patterns
- [ ] Verify backup completion
- [ ] Review security incident reports

#### C.3 Monthly Tasks
- [ ] Conduct user access review
- [ ] Review and approve access requests
- [ ] Generate compliance report
- [ ] Review audit log retention
- [ ] Update security documentation
- [ ] Review agent token status

#### C.4 Quarterly Tasks
- [ ] Comprehensive access recertification
- [ ] Security control testing
- [ ] Policy compliance audit
- [ ] Review and renew exceptions
- [ ] Security training verification
- [ ] Penetration testing coordination

#### C.5 Annual Tasks
- [ ] Full security audit
- [ ] Policy review and update
- [ ] Third-party assessment
- [ ] Compliance certification
- [ ] Disaster recovery testing
- [ ] Security awareness campaign

### Appendix D: Incident Response Contact Information

#### D.1 Internal Contacts

**Security Team**:
- Email: security@example.com
- Phone: (555) 123-4567
- On-Call: (555) 123-4568
- Escalation: CISO

**IT Operations**:
- Email: itops@example.com
- Phone: (555) 123-4569
- On-Call: (555) 123-4570
- Escalation: IT Director

**Management**:
- CISO: ciso@example.com
- IT Director: itdirector@example.com
- Legal: legal@example.com

#### D.2 External Contacts

**Incident Response Vendor**:
- Company: [Vendor Name]
- Contact: [Contact Name]
- Phone: [Phone Number]
- Email: [Email Address]

**Law Enforcement** (if required):
- Local: [Local Police Department]
- Federal: FBI Cyber Division
- Phone: [Phone Number]

### Appendix E: Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2024 | Security Team | Initial policy creation |
| | | | - RBAC implementation policy |
| | | | - Row-level security policy |
| | | | - Audit logging policy |
| | | | - Session security policy |
| | | | - Agent token authentication policy |

### Appendix F: Policy Acknowledgment Form

**I acknowledge that I have read, understood, and agree to comply with the RBAC Authorization Enforcement Security Policy.**

**User Information**:
- Name: _______________________________
- Username: _______________________________
- Role: _______________________________
- Department/Site: _______________________________

**Acknowledgment**:
- I understand my responsibilities under this policy
- I understand the consequences of policy violations
- I have completed required security training
- I agree to report security incidents immediately
- I agree to protect my credentials and not share them

**Signature**: _______________________________

**Date**: _______________________________

**Manager Approval** (if applicable):
- Manager Name: _______________________________
- Manager Signature: _______________________________
- Date: _______________________________

---

## 12. Policy Approval

**Policy Approved By**:

**Chief Information Security Officer (CISO)**:
- Name: _______________________________
- Signature: _______________________________
- Date: _______________________________

**IT Director**:
- Name: _______________________________
- Signature: _______________________________
- Date: _______________________________

**Compliance Officer** (if applicable):
- Name: _______________________________
- Signature: _______________________________
- Date: _______________________________

---

**End of Security Policy Document**

**Next Review Date**: [One year from effective date]  
**Policy Location**: Internal security portal, shared documentation repository  
**Questions**: Contact security@example.com

