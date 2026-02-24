# Product Requirements Document (PRD)

## 1. Document Control
- Project: Device Monitoring Tactical
- Date: February 20, 2026
- Version: v1.1 (Draft for implementation + Devices UX performance addendum)

## 2. Product Summary
Device Monitoring Tactical is an on-premise monitoring platform for company IT operations. It provides unified visibility across network devices, servers, and employee network usage to reduce outages, improve response speed, and enforce policy compliance.

## 3. Problem Statement
Network administrators need a single system to:
- Detect device/server/network failures before or as they happen.
- Notify operations quickly enough to prevent prolonged downtime.
- Track employee network activity and productivity signals.
- Detect access to blacklisted sites and trigger immediate action.
- Audit network usage events (for example, who initiated printer commands and from which IP).

Without this, monitoring is fragmented, incidents are detected late, and compliance/investigation workflows are slow.

## 4. Target Users
- Primary: Network Administrators / IT Administrators.
- Secondary: Managers (consume weekly productivity/compliance reports).
- Read-only stakeholders: Operations/compliance viewers.

## 5. Goals and Success Metrics (First 6 Months)
- Alert accuracy: `> 90%`.
- False-positive rate: `< 10%`.
- Failure notification latency: `< 2 minutes`.
- Device coverage at launch: `100%` of managed network devices.
- Reporting cadence: Weekly productivity reports for managers.
- Operations visibility: Real-time dashboard for admins.
- Policy enforcement: Blacklisted site access triggers immediate alert, with optional block action.
- Data retention/search: Minimum 90-day logs, searchable by IP, user, and device.

## 6. Scope

### 6.1 MVP (Day-One Release)
- Employee network activity monitoring.
- Network monitoring across managed infrastructure.
- Device classification using scanner-based classification logic.
- Server health monitoring (core resource and status tracking).
- Switch monitoring (SNMP-based).
- Monitoring coverage for each managed device.
- Real-time alerting for failures and policy violations.
- Weekly manager reports and admin real-time dashboards.
- Searchable logs (IP/user/device) with 90-day retention minimum.

### 6.2 Later Phase (Post-MVP)
- Camera streaming ingestion.
- AI-based frame/pattern change detection for camera feeds.

## 7. Explicitly Out of Scope (v1)
- Building full feature parity with OpManager.
- SNMP trap reception and event correlation engine.
- Multi-site / distributed polling architecture.

## 8. Functional Requirements

### 8.1 Monitoring
- Monitor servers, switches, and network-reachable managed devices.
- Support SNMP v2/v3 devices.
- Provide per-device status, health, and classification context.

### 8.2 Alerting
- Generate alerts for device/server/network failures.
- Deliver alerts within 2 minutes of detectable failure conditions.
- Generate immediate alerts for blacklisted site access.
- Support optional block workflow for blacklisted sites (implementation mechanism defined during design).

### 8.3 Employee Activity and Productivity
- Collect and expose employee network usage insights.
- Provide manager-facing weekly productivity reports.
- Provide admin-facing real-time operational dashboard.

### 8.4 Audit and Forensics
- Persist searchable logs by IP/user/device.
- Include network action traceability (for example, command source IP for printer/network usage events where available).

### 8.5 Access Control and Identity
- Integrate with Active Directory / LDAP.
- Enforce RBAC roles: `Admin`, `Manager`, `Read-only`.

## 9. Non-Functional Requirements
- Deployment: On-premise only (no public-cloud dependency).
- Security in transit: TLS.
- Security at rest: AES-256.
- Retention: 90 days default, configurable up to 1 year.
- Web UX: Browser-based dashboard only (no mobile app in v1).
- Compliance: Employee monitoring disclosure required at onboarding.
- OS compatibility target:
  - Windows 10/11
  - Windows Server
  - Linux (Ubuntu/CentOS)

## 10. Technical Constraints and Existing Stack

### 10.1 Backend
- Python Flask using application factory (`app.py`, `create_app`).
- Modular Flask blueprints in `routes/`.
- Service-layer business logic in `services/`.
- SQLAlchemy models in `models/`.

### 10.2 Frontend
- Jinja2 templates in `templates/`.
- Static assets in `static/`.
- Bootstrap 5 for styling.
- Chart.js for visualizations.

### 10.3 Data Layer
- SQLAlchemy ORM as primary DB access method.
- Development DB: SQLite (`instance/device_monitoring.db`).
- Production DB: PostgreSQL (required by configuration).
- Time-series metrics: InfluxDB.
- Migrations: `utils/db_migrations.py` (startup-invoked), no Alembic.

## 11. Timeline and Rollout Plan
Assumption: Planning starts on **February 20, 2026**.

- Weeks 1-2:
  - Finalize requirements, data model updates, and environment setup.
  - Confirm AD/LDAP integration approach and security controls.
- Weeks 3-5:
  - Implement MVP core monitoring, device classification, alerting, and dashboards.
  - Add blacklisted-site detection/alert workflow.
- Weeks 6-7:
  - Reporting, retention/search hardening, RBAC validation, and QA.
  - UAT with network admin and manager personas.
- Week 8:
  - Production readiness checks and on-prem rollout.
  - Full production go-live by approximately **April 20, 2026** (2 months total).

## 12. Risks and Dependencies
- SNMP credential completeness and device coverage may delay 100% onboarding.
- AD/LDAP integration complexity can impact timeline.
- Blacklisted-site blocking method depends on network control points available.
- Compliance/legal policy for employee monitoring must be approved before production use.

## 13. Release Acceptance Criteria (MVP)
- Alert accuracy and false-positive metrics meet target thresholds.
- Failure alerts verified to arrive within 2 minutes in test scenarios.
- 100% managed network devices visible and monitored at go-live.
- Weekly manager report workflow is operational.
- Real-time admin dashboard is operational.
- Blacklisted-site alerting works end-to-end (with optional block path validated where enabled).
- Log retention/search works for at least 90 days and supports IP/user/device queries.

## 14. Devices Tab Performance Hardening (Incremental Plan)

### 14.1 Objective
Make the Devices tab smooth and enterprise-ready at scale without breaking existing workflows (add/edit/delete/toggle monitoring/bulk actions).

### 14.2 Current Pain Points
- Jitter during status refresh cycles.
- Heavy table operations causing delayed UI response.
- Inconsistent behavior when selecting filtered vs total inventory sets.
- High backend load from status queries when inventory grows.

### 14.3 Scope
- In-scope:
  - Devices table rendering, filtering, pagination, status polling, and bulk selection behavior.
  - Backend status/query efficiency for Devices-tab API usage.
- Out-of-scope:
  - Rewriting monitoring architecture.
  - Replacing Flask/Jinja stack.
  - Mobile app experience.

### 14.4 Incremental Delivery Strategy
- Phase A (Completed):
  - Server-side pagination and persistent filters.
  - Cross-page filtered selection endpoint and UI flow.
  - Select-all behavior constrained to currently filtered/visible rows.
  - Batch status polling support with device ID scoping.
- Phase B (Completed):
  - Status endpoint latest-scan query scoped to requested devices only.
  - Keyed DOM row updates for status patches (no full-table traversal for every response).
  - Re-filtering triggered only when status actually changes.
- Phase C (Next):
  - Reduce full-page reload jitter from auto-submit search interactions.
  - Add progressive loading indicators for long-running filtered selections.
  - Add lightweight telemetry for client render/update timings and API latency.
- Phase D (Next):
  - Validate with larger datasets (500+ devices).
  - Tune polling cadence and batch size defaults for stability under load.
  - Final QA sign-off for "enterprise-ready" behavior.

### 14.5 Performance Targets (Devices Tab)
- Filter interaction response (UI action to visible update): p95 under 300 ms on standard admin workstation.
- Status refresh UI patch time (per poll cycle): p95 under 200 ms for a 200-row page.
- No full table DOM rebuild during live status updates.
- Bulk select correctness: 100% alignment with active filters (no hidden/unfiltered device inclusion).
- Devices-tab status endpoint: scoped query path for requested device IDs, not full-inventory aggregation.

### 14.6 Acceptance Criteria (Devices Tab)
- With active filters, "Select All" only selects matching visible records on page.
- "Select Filtered (All Pages)" selects only server-filter-matched records and respects hard cap safety limits.
- No missing device rows caused by client-side status/filter race conditions.
- Polling remains stable during modal open/close, tab visibility changes, and batch refresh operations.
- All existing CRUD and monitoring toggle flows remain functional after optimization.
