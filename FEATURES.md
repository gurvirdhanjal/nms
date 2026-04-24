# Device Monitoring Tactical - Feature Documentation

## Executive Summary

**Enterprise Readiness Score: 7.5/10**

Device Monitoring Tactical is a comprehensive network and endpoint monitoring platform built with Flask, SQLAlchemy, and distributed worker architecture. The system provides robust infrastructure monitoring (SNMP, ICMP, service checks), agent-based endpoint monitoring, and specialized capabilities for printer management, employee tracking, and network topology discovery.

### Key Strengths
- **Excellent Infrastructure Monitoring**: SNMP v1/v2c/v3 with bulk polling, ICMP ping monitoring, service checks (HTTP/DNS/TCP)
- **Comprehensive Endpoint Monitoring**: Cross-platform agent (Windows/Linux) collecting 50+ metrics
- **Sophisticated RBAC Infrastructure**: Role-based access control with department/site isolation
- **Multi-Site & Multi-Tenancy**: Site and department-based data scoping
- **Distributed Architecture**: Worker-based task queue for horizontal scaling
- **Time-Series Data Management**: Automated rollups (hourly/daily) with retention policies
- **LDAP/AD Integration**: Full Active Directory authentication and group mapping

### Critical Gaps
- **Authorization Enforcement**: 60+ high-risk endpoints allow writes without proper role restrictions (see AUTHORIZATION_COVERAGE_MATRIX.md)
- **Security Hardening**: Missing encryption at rest, TLS enforcement, rate limiting, MFA, SSO/SAML
- **Data Scoping**: Inconsistent application of department filters across endpoints
- **Audit Logging**: Incomplete coverage of sensitive operations

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Infrastructure Monitoring](#infrastructure-monitoring)
3. [Endpoint Monitoring](#endpoint-monitoring)
4. [Device Management](#device-management)
5. [Data Collection & Storage](#data-collection--storage)
6. [Alerting & Notifications](#alerting--notifications)
7. [Reporting & Analytics](#reporting--analytics)
8. [Security & Access Control](#security--access-control)
9. [Enterprise Features](#enterprise-features)
10. [Specialized Monitoring](#specialized-monitoring)
11. [Integration & Extensibility](#integration--extensibility)
12. [What's Missing](#whats-missing)
13. [Recommendations](#recommendations)

---

## 1. Architecture Overview

### Technology Stack
- **Backend**: Flask 2.x (Python web framework)
- **Database**: PostgreSQL (production) / SQLite (development)
  - SQLAlchemy ORM with connection pooling
  - WAL mode for SQLite concurrency
  - Optimized indexes for time-series queries
- **Task Queue**: Database-backed poll task queue with `SELECT FOR UPDATE SKIP LOCKED`
- **Workers**: Standalone Python processes for distributed SNMP polling
- **Real-time**: Server-Sent Events (SSE) for live dashboard updates
- **Authentication**: Session-based + API key + Agent token
- **Compression**: Flask-Compress with gzip (6:1 ratio)

### Application Structure
```
app.py                  # Application factory, blueprint registration
config.py               # Configuration management (env-based)
extensions.py           # Shared extensions (db, bcrypt, event_manager)

routes/                 # 20+ blueprints for modular routing
├── auth.py            # Login, LDAP, session management
├── devices.py         # Device CRUD, bulk operations
├── monitoring.py      # Dashboard, real-time metrics
├── scanning.py        # Network discovery, port scanning
├── snmp.py            # SNMP polling, interface counters
├── agent.py           # Agent metric ingestion
├── reports.py         # Report generation, exports
├── tracking.py        # Employee monitoring
├── printer.py         # Printer-specific monitoring
└── ...

services/              # 30+ service modules (business logic)
├── device_monitor.py  # Orchestrates ICMP monitoring
├── snmp_service.py    # SNMP polling implementation
├── alert_manager.py   # Alert generation with strike system
├── scheduler.py       # Scheduled task management
├── ldap_service.py    # LDAP authentication
└── ...

workers/               # Background workers
└── snmp_worker.py     # Distributed SNMP task executor

models/                # 25+ SQLAlchemy models
├── device.py          # Core device model (50+ fields)
├── server_health.py   # Time-series health metrics
├── user.py            # User accounts with RBAC
└── ...

middleware/            # Request/response middleware
└── rbac.py            # Authorization, scoping, audit logging
```

### Database Design

**Core Entities**:
- `device` (50+ columns): IP, type, monitoring config, SNMP/WMI/agent settings, site/department FK
- `server_health_logs`: Raw metrics (CPU, RAM, disk, network, processes)
- `server_health_hourly_rollup`: Aggregated hourly stats
- `server_health_daily_rollup`: Aggregated daily stats
- `device_scan_history`: ICMP ping results with latency/packet loss
- `device_interfaces`: Interface inventory with ifIndex mapping
- `interface_traffic_history`: Counter snapshots for bandwidth calculation

**Multi-Tenancy**:
- `sites`: Physical locations
- `departments`: Organizational units within sites
- `users`: Role-based access with site/department FK

**Monitoring**:
- `dashboard_events`: Active alerts with severity, strike counters
- `poll_tasks`: Task queue for SNMP workers (status: pending/running/done/failed)
- `device_snmp_config`: Per-device SNMP credentials and polling config

**Specialized**:
- `printer_metrics`: Toner levels, page counts, tray status
- `print_job_audit`: Print job tracking with user/document details
- `tracked_devices`: Employee monitoring targets
- `switch_topology`: Network topology from LLDP/CDP

---

## 2. Infrastructure Monitoring

### SNMP Monitoring

**Protocols Supported**:
- SNMP v1, v2c, v3 (with USM authentication)
- GETBULK optimization (10x fewer round-trips vs GETNEXT)
- Configurable timeout/retries per device

**Capabilities**:
- **System Information**: sysDescr, sysName, sysUpTime, sysLocation, sysContact
- **Interface Discovery**: Automatic interface enumeration with ifIndex mapping
- **Interface Counters**: In/out octets, errors, admin/oper status
- **High-Speed Interfaces**: 64-bit counters (ifHCInOctets/ifHCOutOctets)
- **Server Health**: CPU load (per-core), RAM usage, disk usage via HOST-RESOURCES-MIB
- **Printer Metrics**: Toner levels, page counts, tray status via Printer-MIB (RFC 3805)

**Error Classification**:
- Typed exceptions: `SnmpTimeoutError`, `SnmpAuthError`, `SnmpOidNotFoundError`, `SnmpVersionMismatchError`
- Structured error returns with `error_code` field for programmatic handling

**Worker Architecture**:
- Standalone `snmp_worker.py` process (can run multiple instances)
- Database-backed task queue with `SELECT FOR UPDATE SKIP LOCKED` (PostgreSQL)
- Priority-based scheduling (Critical > Standard > Low based on device CoS tier)
- Automatic stale task reclamation (15-minute timeout)
- Graceful shutdown with SIGTERM/SIGINT handling
- Thread pool executor (20 concurrent SNMP polls)

**Polling Intervals**:
- Health checks: Every 5 minutes (configurable)
- Interface counters: Every 30 seconds (configurable via `INTERFACE_POLL_INTERVAL`)
- Duplicate task protection (skips devices with pending tasks)

### ICMP Ping Monitoring

**Features**:
- Asynchronous ping using `asyncio` and `ping3` library
- Metrics collected: Latency (ms), packet loss (%), online/offline status
- Scheduled monitoring every 5 minutes
- Per-device strike counters to prevent alert spam

**Storage**:
- `device_scan_history` table with indexed timestamps
- Retention: Configurable (default: 90 days for raw data)

### Service Checks

**Supported Protocols**:
- **HTTP/HTTPS**: Status code validation, response time, SSL certificate expiry
- **TCP**: Port connectivity checks with timeout
- **DNS**: Query resolution with response time

**Batch Operations**:
- `/check/batch` endpoint for parallel service checks
- Returns structured results with success/failure status

### Network Topology Discovery

**Methods**:
- **LLDP** (Link Layer Discovery Protocol): Neighbor discovery on managed switches
- **CDP** (Cisco Discovery Protocol): Cisco-specific neighbor discovery
- **SSH CAM Table**: MAC address table parsing for downstream device mapping

**Data Model**:
- `switch_topology` table: parent_switch_id, parent_port_id, discovery_method
- `device.parent_switch_id` and `device.parent_port_id` for hierarchical relationships
- `device.if_index_map` (JSON): Maps ifIndex to canonical interface names

---

## 3. Endpoint Monitoring

### Agent-Based Monitoring

**Platform Support**:
- Windows (psutil-based)
- Linux (psutil-based)
- Cross-platform Python agent (`client_modules/`)

**Metrics Collected** (50+ metrics):

**CPU**:
- Overall CPU usage (%)
- Per-core load average (1/5/15 min on Linux)
- CPU I/O wait (%)
- CPU steal time (%) for virtualized environments
- Context switches per second

**Memory**:
- RAM usage (%, GB used/total)
- Swap usage (%, MB used/total)
- Page faults per second

**Disk**:
- Disk usage (%, GB used/free/total)
- Disk I/O: read/write bytes, read/write count
- Disk latency: read/write latency (ms)
- Disk busy percentage

**Network**:
- Upload/download speed (KB/s) with delta-based calculation
- Network I/O per interface (JSON)
- TCP retransmits (delta)
- Active connections: total, established, unique IPs
- Top 20 remote IPs by connection count

**Processes**:
- Total process count
- Zombie process count
- Top 5 processes by memory usage
- Top 5 processes by CPU usage
- Open file descriptors (count, limit, %)

**System**:
- OS name, version, architecture
- Uptime (seconds)
- System alerts (JSON array)

**Agent Configuration**:
- Token-based authentication (`X-Agent-Token` header)
- Configurable reporting interval (default: 300 seconds)
- Automatic device registration on first metric submission
- Hardware specs stored in `device.hardware_specs` (JSON)

**Data Ingestion**:
- Endpoint: `POST /api/agent/metrics`
- Validation: Token must match `device.agent_token`
- Storage: `server_health_logs` table with `source='agent'`
- Fallback: SNMP polling disabled for servers with active agent (5-minute freshness check)

### WMI Monitoring (Configured, Not Actively Polled)

**Configuration Fields**:
- `device.wmi_username`, `device.wmi_password`, `device.wmi_domain`
- Intended for Windows-specific metrics (services, event logs, registry)

**Status**: Infrastructure exists but not wired to active polling

---

## 4. Device Management

### Device Discovery

**Automated Discovery**:
- **Heavy Scan**: Full subnet scan with port probing, SNMP enumeration, service detection
- **Light Scan**: ICMP-only sweep for quick availability checks
- **Configurable Intervals**: Heavy scan (default: 1440 min), light scan (default: 60 min)
- **Subnet Management**: Define target subnets via `subnets` table
- **Auto-Classification**: Device type inference based on SNMP sysDescr, open ports, MAC OUI

**Manual Discovery**:
- Network scanner UI (`/scanner`)
- CIDR range input with progress tracking
- Port scanning (common ports: 22, 80, 443, 3389, etc.)
- Add to inventory with one-click

**Device Classification**:
- **Types**: Server, Switch, Router, Printer, Workstation, IoT, Unknown
- **Confidence Scoring**: 0-100 based on classification signals
- **Classification Details**: JSON field storing reasoning (e.g., "SNMP sysDescr contains 'Cisco IOS'")
- **Manufacturer Detection**: Parsed from SNMP sysDescr or MAC OUI lookup

### Device Inventory

**Core Fields** (50+ total):
- **Identity**: device_name, device_ip, hostname, macaddress, manufacturer
- **Classification**: device_type, confidence_score, classification_confidence, classification_details
- **Monitoring**: is_monitored, monitoring_mode (ping/snmp/agent/wmi)
- **Network**: subnet_cidr, port, rstplink (RSTP link status)
- **Topology**: parent_switch_id, parent_port_id, last_discovery_method
- **Multi-Tenancy**: site_id, department_id
- **Maintenance**: maintenance_mode, health_alert_strikes, offline_strikes, latency_strikes, packet_loss_strikes
- **CoS**: cos_tier (Critical/Standard/Low) for priority-based polling

**SNMP Configuration** (per-device):
- Version: v1, v2c, v3
- Community string (v2c) or USM credentials (v3)
- Auth protocol: SHA, MD5
- Privacy protocol: AES, DES
- Timeout, retries, port

**Agent Configuration**:
- agent_token (32-byte URL-safe token)
- agent_interval (reporting frequency)
- agent_os_type (windows/linux)
- hardware_specs (JSON)

**WMI Configuration**:
- wmi_username, wmi_password, wmi_domain

**Device Credentials**:
- device_username, device_password_hash (pbkdf2) for SSH/API access

### Bulk Operations

**Supported**:
- Bulk add devices (CSV import or JSON API)
- Bulk delete devices
- Bulk assign to site/department
- Bulk enable/disable monitoring
- Bulk reclassify (re-run classification logic)

**Endpoints**:
- `POST /api/devices/bulk_add`
- `POST /api/devices/bulk_delete`
- `POST /api/sites/<site_id>/assign`
- `POST /api/departments/<dept_id>/assign`

---

## 5. Data Collection & Storage

### Time-Series Data Management

**Raw Metrics**:
- Table: `server_health_logs`
- Retention: 7 days (configurable via `SERVER_HEALTH_RAW_RETENTION_DAYS`)
- Indexes: Composite indexes on (device_id, source, timestamp) for fast queries
- Sources: `agent`, `snmp`, `icmp`

**Hourly Rollups**:
- Table: `server_health_hourly_rollup`
- Aggregation: AVG, MIN, MAX, STDDEV for CPU/RAM/disk
- Retention: 30 days (configurable via `SERVER_HEALTH_HOURLY_RETENTION_DAYS`)
- Scheduled: Daily at 02:00 (configurable via `SERVER_HEALTH_RETENTION_SCHEDULE`)

**Daily Rollups**:
- Table: `server_health_daily_rollup`
- Aggregation: AVG, MIN, MAX, STDDEV for CPU/RAM/disk
- Retention: 365 days (configurable via `SERVER_HEALTH_DAILY_RETENTION_DAYS`)
- Scheduled: Daily at 02:00

**Rollup Integrity**:
- Validation job: Daily at 03:00 (configurable via `SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE`)
- Lookback: 45 days (configurable via `SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS`)
- Auto-repair: Fills missing hourly/daily buckets from raw data

**Rollup State Tracking**:
- Table: `server_health_rollup_state`
- Tracks last successful rollup timestamp per device/granularity
- Prevents duplicate rollup processing

### ICMP Scan History

**Storage**:
- Table: `device_scan_history`
- Fields: device_ip, device_name, ping_time_ms, packet_loss, status, scan_type, scan_timestamp
- Retention: Configurable (default: 90 days)
- Indexes: (device_ip, scan_timestamp) for time-range queries

### Interface Traffic History

**Storage**:
- Table: `interface_traffic_history`
- Fields: interface_id, in_octets, out_octets, in_errors, out_errors, timestamp
- Delta calculation: Bandwidth (bps) = (current_octets - previous_octets) / time_delta
- Counter wrap handling: 32-bit and 64-bit counter overflow detection

### Maintenance Service

**Automated Tasks**:
- **Retention Cleanup**: Deletes old raw metrics, hourly rollups, daily rollups
- **Rollup Generation**: Creates hourly/daily aggregates from raw data
- **Integrity Validation**: Detects and repairs missing rollup buckets
- **Scheduled Execution**: Via `MonitoringScheduler` (schedule library)

**Manual Triggers**:
- `POST /maintenance/cleanup` (admin only)
- `POST /maintenance/aggregate` (admin only)
- `POST /maintenance/run-all` (admin only)

---

## 6. Alerting & Notifications

### Alert Generation

**Alert Manager** (`services/alert_manager.py`):
- **3-Strike System**: Requires N consecutive threshold breaches before firing alert (prevents false positives)
- **Recovery Strikes**: Requires N consecutive normal readings before resolving alert (prevents flapping)
- **Maintenance Mode**: Devices with `maintenance_mode=True` are silently skipped

**Alert Types**:

1. **Server Health Alerts** (PRIMARY - triggers email):
   - **RAM**: Warning ≥60%, Critical ≥85%
   - **CPU**: Warning ≥80%, Critical ≥90%
   - **Disk**: Warning ≥90%, Critical ≥95%
   - Strikes required: 3 consecutive breaches
   - Recovery strikes: 2 consecutive normal readings

2. **Server Availability Alerts** (CRITICAL):
   - Offline status for servers only (3-strike rule)
   - Non-servers: Visual status only, no alerts

3. **ICMP Performance Alerts** (WARNING):
   - **Latency**: ≥200ms for 3 consecutive scans
   - **Packet Loss**: ≥10% for 3 consecutive scans
   - Informational only (no email by default)

**Alert Storage**:
- Table: `dashboard_events`
- Fields: event_id (UUID), device_id, event_type, severity, metric_name, message, value, timestamp, resolved, resolved_at
- Severity levels: INFO, WARNING, CRITICAL
- Resolved flag: Automatically set when metric returns to normal

**Strike Counters** (stored in `device` table):
- `health_alert_strikes`: Consecutive health threshold breaches
- `offline_strikes`: Consecutive offline checks
- `latency_strikes`: Consecutive high-latency scans
- `packet_loss_strikes`: Consecutive high packet-loss scans

### Notification Channels

**Email Notifications**:
- Service: `NotificationService` (mock implementation)
- Triggered for: WARNING and CRITICAL alerts
- SMTP configuration: `config.py` (SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD)
- Status: Infrastructure exists but not fully wired

**Real-Time Updates**:
- Server-Sent Events (SSE) via `/sse/stream`
- Event types: `device_update_batch`, `alert_fired`, `alert_resolved`
- Batch updates for performance (accumulates troubled devices)

### Alert Management

**User Actions**:
- **Acknowledge**: Mark alert as seen (POST `/alerts/<event_id>/acknowledge`)
- **Resolve**: Manually resolve alert (POST `/alerts/<event_id>/resolve`)

**Dashboard Integration**:
- Active alerts widget with severity filtering
- Top problems view (most critical devices)
- Alert history with time-range filtering

---

## 7. Reporting & Analytics

### Report Types

**1. Device Health Report**:
- Metrics: CPU, RAM, disk usage over time
- Aggregation: Hourly/daily rollups
- Filters: Device, date range, metric type
- Export: CSV, JSON

**2. Network Performance Report**:
- Metrics: Latency, packet loss, uptime percentage
- Source: `device_scan_history` table
- Filters: Device, subnet, date range
- Export: CSV, JSON

**3. Operational Report**:
- Device inventory summary
- Alert statistics (count by severity)
- Monitoring coverage (monitored vs total devices)
- Export: CSV, JSON

**4. Executive Report**:
- High-level KPIs: Total devices, uptime %, critical alerts
- Trend analysis: Week-over-week comparisons
- Top problems: Devices with most alerts
- Export: CSV, JSON

**5. Alerts Report**:
- Alert history with filters (severity, device, date range)
- Resolution time analysis
- Export: CSV, JSON

**6. Productivity Report** (Feature Flag):
- Employee activity tracking (if `ENABLE_PRODUCTIVITY_REPORT=true`)
- Application usage, idle time, active hours
- Export: CSV, JSON

### Report Generation

**Synchronous Reports**:
- Endpoint: `GET /api/reports/<report_type>`
- Query parameters: start_date, end_date, device_id, etc.
- Response: JSON with data array
- Timeout: 5 seconds (configurable via `REPORT_STATEMENT_TIMEOUT_MS`)

**Asynchronous Export Jobs**:
- Endpoint: `POST /api/reports/<report_type>/export-jobs`
- Background processing for large datasets
- Job status tracking: `GET /api/reports/export-jobs/<job_id>`
- Download: `GET /api/reports/export-jobs/<job_id>/download`
- TTL: 1 hour (configurable via `REPORT_ASYNC_JOB_TTL_SECONDS`)
- Concurrency limit: 2 jobs (configurable via `REPORT_MAX_CONCURRENT_EXPORT_JOBS`)

### Report Safety Controls

**Range Limits**:
- General reports: 90 days (configurable via `MAX_REPORT_RANGE_DAYS`)
- Network reports: 30 days (configurable via `MAX_NETWORK_REPORT_RANGE_DAYS`)
- Productivity reports: 30 days (configurable via `MAX_PRODUCTIVITY_REPORT_RANGE_DAYS`)

**Row Limits**:
- Query results: 50,000 rows (configurable via `MAX_REPORT_ROWS`)
- Export results: 50,000 rows (configurable via `MAX_EXPORT_ROWS`)

**Rate Limiting**:
- Report queries: 5 per minute (configurable via `REPORT_RATE_LIMIT_PER_MINUTE`)
- Export jobs: 3 per minute (configurable via `REPORT_EXPORT_RATE_LIMIT_PER_MINUTE`)

**Caching**:
- Cache TTL: 180 seconds (configurable via `REPORT_CACHE_TTL_SECONDS`)
- Cache key: Report type + parameters hash

### Daily Report

**Automated Generation**:
- Scheduled: Daily at 23:59
- Content: Device statistics for the day (uptime %, avg latency, packet loss)
- Storage: In-memory (can be extended to email/file)

---

## 8. Security & Access Control

### Authentication

**Local Authentication**:
- Username/password with bcrypt hashing
- Session-based (Flask sessions with secure cookies)
- Session lifetime: 5 minutes (configurable via `PERMANENT_SESSION_LIFETIME`)
- Session refresh on each request
- Cookie flags: HttpOnly, SameSite=Lax, Secure (if HTTPS)

**LDAP/Active Directory Integration**:
- Full LDAP authentication support
- Configuration: `config.py` (LDAP_SERVER, LDAP_BASE_DN, LDAP_BIND_DN, etc.)
- TLS support: LDAPS and STARTTLS
- Certificate validation: CERT_REQUIRED or CERT_NONE
- Attribute mapping: email, displayName, objectGUID
- Group-based role mapping: LDAP_ADMIN_GROUP
- User provisioning: Auto-create local user on first LDAP login
- Sync: External ID tracking for LDAP users

**API Key Authentication**:
- Header: `X-API-Key`
- Configuration: `MOBILE_API_KEY` in config
- Used for: Mobile/external client access to API v1 endpoints

**Agent Token Authentication**:
- Header: `X-Agent-Token`
- Per-device token (32-byte URL-safe random)
- Generation: `generate_agent_token()` in `middleware/rbac.py`
- Validation: `validate_agent_token()` checks against `device.agent_token`
- Used for: Agent metric submission (`POST /api/agent/metrics`)

### Authorization (RBAC)

**Roles**:
- **admin**: Full access to all resources and operations
- **manager**: Department-scoped read/write + user management within department
- **operator**: Department-scoped read/write (no user management)
- **viewer**: Department-scoped read-only
- **user**: Legacy role (maps to operator)

**Permission Model**:
- Permission strings: `<resource>.<action>` (e.g., `devices.edit`, `reports.export`)
- Role-permission mapping: `ROLE_PERMISSIONS` dict in `middleware/rbac.py`
- Wildcard: `admin` role has `*` permission

**Endpoint Authorization**:
- Mapping: `ENDPOINT_PERMISSIONS` dict (endpoint → required permission)
- Global guard: `enforce_write_permission()` in `@app.before_request`
- Decorators: `@require_login`, `@require_role(...)`, `@require_permission(...)`

**Data Scoping**:
- **Admin**: Sees all data (no filtering)
- **Manager**: Sees all data in their site (site_id filter)
- **Operator/Viewer**: Sees only data in their department (department_id filter)
- Helper: `scoped_query(model)` returns filtered query
- Request-level caching: Department IDs cached in Flask `g` object

**Session Hardening**:
- Validation: `validate_session_for_write()` checks session vs database
- Prevents: Session manipulation attacks (role escalation, scope bypass)
- Decorator: `@require_validated_session` for critical operations
- Request-level caching: Validation result cached in Flask `g` object

### Audit Logging

**Audit Log Model**:
- Table: `audit_log`
- Fields: user_id, username, user_role, action, entity_type, entity_id, entity_name, description, changes (JSON), ip_address, user_agent, timestamp

**Logged Actions**:
- User login/logout
- Device create/update/delete
- Site/department create/update/delete
- User create/update/delete
- Permission changes
- Bulk operations

**Logging Functions**:
- `create_audit_log()`: Synchronous (for critical operations)
- `create_audit_log_async()`: Asynchronous (for high-volume operations)
- `create_audit_logs_bulk()`: Batch insert (for bulk operations)

**Resilience**:
- Audit failures do not block operations
- Errors logged but operation proceeds
- Automatic rollback of audit transaction on failure

### Security Gaps (Critical)

**Authorization Enforcement**:
- **60+ high-risk endpoints** allow writes without role restrictions (see `AUTHORIZATION_COVERAGE_MATRIX.md`)
- Examples: `/api/devices/bulk_delete`, `/api/sites/<id>` (DELETE), `/api/departments/<id>` (DELETE)
- Impact: Any authenticated user (including `viewer`) can modify/delete data

**Data Scoping**:
- **20+ endpoints** return unscoped data (cross-department leakage)
- Examples: `/api/devices/<id>`, `/api/sites`, `/alerts`
- Impact: Users can view data outside their department/site

**Missing Security Features**:
- No encryption at rest for sensitive fields (passwords, SNMP community strings)
- No TLS enforcement (SESSION_COOKIE_SECURE defaults to False)
- No rate limiting (except for reports)
- No MFA/2FA support
- No SSO/SAML integration
- No IP whitelisting
- No brute-force protection

---

## 9. Enterprise Features

### Multi-Site Support

**Site Model**:
- Table: `sites`
- Fields: id, name, location, description, created_at
- Relationships: Devices, departments, users

**Capabilities**:
- Create/update/delete sites (admin only)
- Assign devices to sites (bulk or individual)
- Site-specific dashboards (`/sites/<site_id>/dashboard`)
- Manager role: Scoped to single site

**Endpoints**:
- `GET /api/sites` - List all sites
- `POST /api/sites` - Create site
- `PUT /api/sites/<id>` - Update site
- `DELETE /api/sites/<id>` - Delete site
- `POST /api/sites/<id>/assign` - Assign devices to site

### Department Isolation

**Department Model**:
- Table: `departments`
- Fields: id, name, site_id (FK), description, created_at
- Relationships: Devices, users

**Capabilities**:
- Create/update/delete departments (manager or admin)
- Assign devices to departments (bulk or individual)
- Department-scoped data access for operator/viewer roles
- Manager role: Sees all departments in their site

**Endpoints**:
- `GET /api/departments` - List departments
- `POST /api/departments` - Create department
- `PUT /api/departments/<id>` - Update department
- `DELETE /api/departments/<id>` - Delete department
- `POST /api/departments/<id>/assign` - Assign devices to department

### Maintenance Windows

**Configuration**:
- Per-device flag: `device.maintenance_mode` (boolean)
- Effect: Suppresses all alerts for the device
- Use case: Planned maintenance, testing, decommissioning

**Management**:
- Toggle via: `POST /api/tracking/maintenance/<mac_address>`
- UI: Maintenance mode indicator in device list
- API v1: `POST /api/v1/devices/<id>/maintenance` (set start/end times)

### Scalability

**Horizontal Scaling**:
- Multiple SNMP worker instances (shared task queue)
- Stateless Flask app (can run behind load balancer)
- Database connection pooling (SQLAlchemy)

**Performance Optimizations**:
- GETBULK for SNMP table walks (10x faster)
- Composite indexes on time-series tables
- Request-level caching (department IDs, session validation)
- Batch SSE updates (accumulates troubled devices)
- Async audit logging (non-blocking)
- Bulk insert for audit logs

**Resource Management**:
- Thread pool for SNMP workers (20 concurrent polls)
- Task queue with priority scheduling
- Stale task reclamation (prevents queue buildup)
- Rollup integrity validation (auto-repair missing buckets)

### Configuration Management

**Environment-Based Config**:
- `.env` file support (dotenv)
- Config class: `config.py`
- Runtime environment: `APP_ENV` (development/production)
- Feature flags: `ENABLE_PRODUCTIVITY_REPORT`, `REQUIRE_POSTGRES`, etc.

**Database Flexibility**:
- PostgreSQL (production): Full feature support
- SQLite (development): WAL mode, busy timeout, check_same_thread=False
- Automatic backend detection and optimization

**Compression**:
- Flask-Compress with gzip (6:1 ratio)
- Configurable level (default: 6)
- Min size: 512 bytes
- Mimetypes: HTML, CSS, JS, JSON, XML, SVG

**Static Asset Caching**:
- Versioned assets: `immutable` cache (1 year)
- Unversioned assets: `public` cache (1 hour)
- Query parameter versioning: `?v=<hash>`

---

## 10. Specialized Monitoring

### Printer Monitoring

**SNMP Printer-MIB (RFC 3805)**:
- **Status**: hrPrinterStatus (idle, printing, warmup, error)
- **Consumables**: Toner/ink levels (black, cyan, magenta, yellow) as percentage
- **Page Counts**: Total pages, color pages, B&W pages (prtMarkerLifeCount)
- **Paper Trays**: Tray status, capacity, current level (prtInputStatus)

**Storage**:
- Table: `printer_metrics`
- Fields: device_id, status, status_code, toner_black/cyan/magenta/yellow, page_count_total/color/bw, paper_tray_status (JSON), job_queue_length, polled_at

**Endpoints**:
- `GET /api/printers` - List all printers
- `GET /api/printers/<device_id>` - Get printer details
- `POST /api/printer/<device_id>/poll` - Trigger SNMP poll
- `GET /api/printer/<device_id>/metrics` - Get latest metrics
- `GET /api/printer/<device_id>/jobs` - Get print job history

**Print Job Auditing**:
- Table: `print_job_audit`
- Fields: device_id, job_id, user_name, document_name, pages, copies, status, submitted_at, completed_at
- Source: SNMP traps or periodic polling (implementation pending)

**UI**:
- Printer dashboard (`/printers`)
- Toner level gauges with color-coded alerts
- Page count trends
- Print job history with user/document filters

### Employee Tracking

**Tracked Devices**:
- Table: `tracked_devices`
- Fields: mac_address, device_name, employee_name, department, is_active, last_seen, tracking_enabled

**Capabilities**:
- Real-time device status (`/api/tracking/live-status/<mac_address>`)
- Live summary dashboard (`/api/tracking/live-summary`)
- Activity history (`/api/tracking/history/activity/<device_id>`)
- Application usage tracking (`/api/tracking/history/applications/<device_id>`)
- Resource usage tracking (`/api/tracking/history/resources/<device_id>`)

**Remote Control** (Requires Agent):
- Toggle microphone: `POST /api/tracking/toggle-mic/<mac_address>`
- Toggle camera: `POST /api/tracking/toggle-camera/<mac_address>`
- Stop camera: `POST /api/tracking/stop-camera/<mac_address>`
- Live camera stream: `GET /api/tracking/stream/camera/<mac_address>`
- Live audio stream: `GET /api/tracking/stream/audio/<mac_address>`
- Screenshot capture: `GET /api/tracking/stream/screenshot/<mac_address>`

**Metrics**:
- Performance metrics: CPU, RAM, disk, network
- Productivity metrics: Active time, idle time, application usage
- Security metrics: Failed login attempts, unauthorized access

**UI**:
- Live tracking dashboard (`/tracking/live`)
- Historical analysis (`/tracking/history/<device_id>`)
- Alert configuration (idle time, resource usage)

### File Transfer

**Capabilities**:
- Connect to remote client: `POST /api/clients/connect`
- List remote files: `POST /api/files/client/list`
- Download from client: `POST /api/files/client/download`
- Upload to client: `POST /api/files/client/upload` (admin only)
- Create folder on client: `POST /api/files/client/create_folder` (admin only)
- Delete file on client: `POST /api/files/client/delete` (admin only)
- Transfer between systems: `POST /api/files/transfer_between` (admin only)

**Local File Management**:
- List local files: `POST /api/files/local/list`
- Download local file: `POST /api/files/local/download`
- Upload local file: `POST /api/files/local/upload`

**Client Discovery**:
- Auto-discover clients: `GET /api/clients/discover`
- Current client info: `GET /api/clients/current`
- System info: `GET /api/files/client/system_info`

**UI**:
- File transfer interface (`/file_transfer`) - admin only
- Dual-pane file browser (local + remote)
- Drag-and-drop upload
- Progress tracking

### Network Topology Mapping

**Discovery Methods**:
- **LLDP**: Link Layer Discovery Protocol (IEEE 802.1AB)
- **CDP**: Cisco Discovery Protocol
- **SSH CAM Table**: MAC address table parsing

**Data Model**:
- Table: `switch_topology`
- Fields: device_id, neighbor_device_id, local_port, remote_port, discovery_method, last_seen

**Visualization**:
- Hierarchical tree view (parent-child relationships)
- Network diagram (nodes + edges)
- Port-level connectivity

**Endpoints**:
- `POST /api/switches/discover` - Trigger topology discovery
- `GET /api/devices/<id>/connections` - Get device connections

---

## 11. Integration & Extensibility

### API Endpoints

**API v1** (`/api/v1/`):
- RESTful API with API key authentication
- Endpoints: Device management, maintenance windows, metrics retrieval
- Authentication: `X-API-Key` header
- Response format: JSON

**Agent API** (`/api/agent/`):
- Metric submission: `POST /api/agent/metrics`
- Authentication: `X-Agent-Token` header (per-device token)
- Payload: JSON with 50+ metrics
- Auto-registration: Creates device on first submission

**Public API Endpoints** (No Auth):
- `/api/agent/metrics` - Agent metric submission (token-based)
- `/auth/login`, `/auth/register` - Authentication

### Agent Protocol

**Communication**:
- Protocol: HTTPS (recommended) or HTTP
- Method: POST
- Endpoint: `/api/agent/metrics`
- Headers: `X-Agent-Token: <token>`, `Content-Type: application/json`

**Payload Structure**:
```json
{
  "cpu_percent": 45.2,
  "memory_percent": 62.1,
  "memory_used_gb": 7.8,
  "memory_total_gb": 16.0,
  "disk_usage": 78.5,
  "disk_used_gb": 235.6,
  "disk_free_gb": 64.4,
  "disk_total_gb": 300.0,
  "network_in_bps": 1024000,
  "network_out_bps": 512000,
  "uptime": "345600",
  "os_name": "Windows",
  "os_version": "10.0.19044",
  "os_arch": "AMD64",
  "load_avg_1min": 1.5,
  "load_avg_5min": 1.2,
  "load_avg_15min": 1.0,
  "swap_total_mb": 4096,
  "swap_used_mb": 512,
  "swap_percent": 12.5,
  "process_count": 156,
  "zombie_count": 0,
  "top_processes": [...],
  "top_processes_cpu": [...],
  "network_connections_total": 45,
  "network_connections_established": 23,
  "network_top_remote_ips": [...]
}
```

**Response**:
- Success: `200 OK` with `{"status": "success"}`
- Error: `401 Unauthorized` (invalid token), `400 Bad Request` (invalid payload)

### Supported Protocols

**Monitoring Protocols**:
- SNMP v1, v2c, v3 (UDP port 161)
- ICMP (ping)
- HTTP/HTTPS (service checks)
- TCP (port connectivity)
- DNS (query resolution)

**Discovery Protocols**:
- LLDP (Link Layer Discovery Protocol)
- CDP (Cisco Discovery Protocol)
- ARP (Address Resolution Protocol)

**Authentication Protocols**:
- LDAP/LDAPS (TCP port 389/636)
- STARTTLS (LDAP upgrade)

### Extensibility Points

**Custom Monitoring Modules**:
- Add new service modules in `services/`
- Register blueprints in `app.py`
- Define models in `models/`

**Custom Alert Rules**:
- Extend `AlertManager` class
- Add threshold rules in `thresholds/rules.py`
- Define custom evaluators in `thresholds/evaluator.py`

**Custom Reports**:
- Add report type in `routes/reports.py`
- Define query logic in service layer
- Register in report type enum

**Custom Integrations**:
- Webhook support (infrastructure exists, not wired)
- Syslog forwarding (infrastructure exists, not wired)
- SNMP trap receiver (infrastructure exists, not wired)

---

## 12. What's Missing

### Critical Security Gaps

**Authorization**:
- ❌ 60+ endpoints allow writes without role restrictions (any authenticated user can modify/delete data)
- ❌ 20+ endpoints return unscoped data (cross-department leakage)
- ❌ No session validation for write operations (session manipulation attacks possible)
- ❌ Inconsistent application of department scoping filters

**Encryption & Transport Security**:
- ❌ No encryption at rest for sensitive fields (passwords, SNMP community strings, API keys)
- ❌ No TLS enforcement (SESSION_COOKIE_SECURE defaults to False)
- ❌ No certificate pinning for LDAP connections

**Authentication Hardening**:
- ❌ No MFA/2FA support
- ❌ No SSO/SAML integration
- ❌ No brute-force protection (account lockout, CAPTCHA)
- ❌ No password complexity requirements
- ❌ No password expiration policy

**Access Control**:
- ❌ No IP whitelisting
- ❌ No rate limiting (except for reports)
- ❌ No API request throttling
- ❌ No concurrent session limits

### Monitoring Gaps

**Infrastructure Monitoring**:
- ❌ WMI monitoring configured but not actively polled
- ❌ No Application Performance Monitoring (APM)
- ❌ No log aggregation (beyond print jobs)
- ❌ No distributed tracing
- ❌ No synthetic monitoring (uptime checks from multiple locations)
- ❌ No container monitoring (Docker, Kubernetes)
- ❌ No cloud monitoring (AWS, Azure, GCP)

**Alerting**:
- ❌ Email notifications not fully wired (mock implementation)
- ❌ No SMS/push notifications
- ❌ No webhook integrations (Slack, Teams, PagerDuty)
- ❌ No alert escalation policies
- ❌ No on-call scheduling
- ❌ No alert grouping/deduplication

**Reporting**:
- ❌ No scheduled report delivery (email, FTP)
- ❌ No report templates
- ❌ No custom report builder
- ❌ No data visualization (charts, graphs)
- ❌ No SLA tracking
- ❌ No capacity planning reports

### Enterprise Features

**High Availability**:
- ❌ No active-active clustering
- ❌ No automatic failover
- ❌ No database replication
- ❌ No backup/restore automation

**Compliance**:
- ❌ No GDPR compliance features (data retention, right to erasure)
- ❌ No HIPAA compliance features (audit logging, encryption)
- ❌ No SOC 2 compliance features (access reviews, change management)

**Integration**:
- ❌ No ITSM integration (ServiceNow, Jira)
- ❌ No CMDB integration
- ❌ No ticketing system integration
- ❌ No ChatOps integration

### Operational Gaps

**Deployment**:
- ❌ No containerization (Docker, Kubernetes)
- ❌ No CI/CD pipeline
- ❌ No infrastructure as code (Terraform, Ansible)
- ❌ No automated testing (unit, integration, E2E)

**Observability**:
- ❌ No application metrics (Prometheus, Grafana)
- ❌ No distributed tracing (Jaeger, Zipkin)
- ❌ No centralized logging (ELK, Splunk)
- ❌ No health check endpoints

**Documentation**:
- ❌ No API documentation (Swagger, OpenAPI)
- ❌ No user manual
- ❌ No admin guide
- ❌ No troubleshooting guide

---

## 13. Recommendations

### Immediate Priority (Security Critical)

**1. Fix Authorization Enforcement** (CRITICAL - 1-2 weeks)
- Implement `ENDPOINT_PERMISSIONS` mapping for all 60+ high-risk endpoints
- Add `@require_permission(...)` decorators to write endpoints
- Apply `scoped_query()` to all data retrieval endpoints
- Add session validation for critical operations
- **Impact**: Prevents unauthorized data modification/deletion
- **Effort**: Medium (requires systematic review of all routes)

**2. Enable TLS/HTTPS** (CRITICAL - 1 day)
- Set `SESSION_COOKIE_SECURE=True` in production config
- Enforce HTTPS redirects in Flask app
- Configure reverse proxy (nginx/Apache) with SSL certificates
- **Impact**: Prevents session hijacking, credential theft
- **Effort**: Low (configuration change)

**3. Implement Rate Limiting** (HIGH - 3-5 days)
- Add Flask-Limiter for global rate limiting
- Apply per-endpoint limits (e.g., 100 req/min for API, 10 req/min for auth)
- Add IP-based blocking for repeated violations
- **Impact**: Prevents brute-force attacks, DoS
- **Effort**: Low (library integration)

**4. Encrypt Sensitive Fields** (HIGH - 1 week)
- Use Fernet (symmetric encryption) for SNMP community strings, passwords, API keys
- Store encryption key in environment variable (not in code)
- Add migration script to encrypt existing data
- **Impact**: Protects credentials if database is compromised
- **Effort**: Medium (requires data migration)

### Short-Term (1-3 Months)

**5. Complete Email Notification Wiring** (MEDIUM - 1 week)
- Replace mock `NotificationService` with real SMTP implementation
- Add email templates (HTML + plain text)
- Add user notification preferences (email, frequency)
- Test with Gmail, Office 365, SendGrid
- **Impact**: Enables proactive alerting
- **Effort**: Low (infrastructure exists)

**6. Implement MFA/2FA** (HIGH - 2 weeks)
- Add TOTP support (Google Authenticator, Authy)
- Add backup codes for account recovery
- Make MFA optional but recommended
- **Impact**: Significantly reduces account compromise risk
- **Effort**: Medium (requires UI changes)

**7. Add Audit Log UI** (MEDIUM - 1 week)
- Create audit log viewer (`/audit`)
- Add filters (user, action, entity type, date range)
- Add export to CSV
- Restrict access to admin role
- **Impact**: Enables compliance audits, forensics
- **Effort**: Low (data already collected)

**8. Implement WMI Monitoring** (MEDIUM - 2 weeks)
- Wire WMI polling to scheduler
- Add Windows-specific metrics (services, event logs, registry)
- Add WMI error handling and retry logic
- **Impact**: Completes Windows monitoring coverage
- **Effort**: Medium (infrastructure exists)

### Medium-Term (3-6 Months)

**9. Add APM & Distributed Tracing** (MEDIUM - 3 weeks)
- Integrate OpenTelemetry for tracing
- Add Prometheus metrics export
- Set up Grafana dashboards
- Add Jaeger for trace visualization
- **Impact**: Improves troubleshooting, performance optimization
- **Effort**: Medium (requires instrumentation)

**10. Implement SSO/SAML** (HIGH - 4 weeks)
- Add python3-saml library
- Support SAML 2.0 IdPs (Okta, Azure AD, OneLogin)
- Add SAML metadata endpoint
- Add JIT (Just-In-Time) user provisioning
- **Impact**: Simplifies enterprise authentication
- **Effort**: High (complex protocol)

**11. Add Webhook Integrations** (MEDIUM - 2 weeks)
- Add webhook configuration UI
- Support Slack, Microsoft Teams, Discord
- Add PagerDuty integration for on-call
- Add retry logic with exponential backoff
- **Impact**: Enables ChatOps, incident management
- **Effort**: Medium (requires external API integration)

**12. Implement Container Monitoring** (MEDIUM - 3 weeks)
- Add Docker API integration
- Add Kubernetes API integration
- Collect container metrics (CPU, RAM, network, disk I/O)
- Add container lifecycle events (start, stop, restart)
- **Impact**: Extends monitoring to containerized workloads
- **Effort**: Medium (requires new data models)

### Long-Term (6-12 Months)

**13. Build High Availability** (HIGH - 8 weeks)
- Implement active-active clustering
- Add database replication (PostgreSQL streaming replication)
- Add load balancer health checks
- Add automatic failover with keepalived/HAProxy
- **Impact**: Eliminates single point of failure
- **Effort**: High (requires infrastructure changes)

**14. Add Cloud Monitoring** (MEDIUM - 6 weeks)
- Integrate AWS CloudWatch
- Integrate Azure Monitor
- Integrate GCP Cloud Monitoring
- Add cloud cost tracking
- **Impact**: Extends monitoring to cloud resources
- **Effort**: High (requires multiple API integrations)

**15. Implement CMDB Integration** (MEDIUM - 4 weeks)
- Add ServiceNow CMDB sync
- Add Jira Asset Management sync
- Add bi-directional sync (push/pull)
- Add conflict resolution
- **Impact**: Maintains single source of truth for assets
- **Effort**: High (requires external API integration)

**16. Build Custom Report Builder** (MEDIUM - 6 weeks)
- Add drag-and-drop report designer
- Add custom metric selection
- Add chart/graph builder
- Add scheduled report delivery
- **Impact**: Enables self-service reporting
- **Effort**: High (requires UI framework)

---

## Conclusion

Device Monitoring Tactical is a feature-rich monitoring platform with excellent infrastructure monitoring capabilities and a solid foundation for enterprise deployment. The system demonstrates sophisticated architecture with distributed workers, time-series data management, and multi-tenancy support.

However, **critical security gaps in authorization enforcement** must be addressed immediately before production deployment. The 60+ high-risk endpoints without proper role restrictions represent a significant vulnerability that could lead to unauthorized data modification or deletion.

With focused effort on security hardening (1-2 months), the platform can achieve enterprise-grade security and be ready for production use. The roadmap above prioritizes security fixes, followed by operational improvements and feature enhancements.

**Recommended Next Steps**:
1. Review and implement authorization fixes from `AUTHORIZATION_COVERAGE_MATRIX.md`
2. Enable TLS/HTTPS and rate limiting
3. Encrypt sensitive database fields
4. Complete email notification wiring
5. Add MFA/2FA support
6. Conduct security audit and penetration testing

---

**Document Version**: 1.0  
**Last Updated**: 2026-02-26  
**Maintained By**: Gurvir H. Dhanjal
