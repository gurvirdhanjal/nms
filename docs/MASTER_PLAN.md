# Device Monitoring Tactical — Master Plan & Status Document

> **Purpose:** Single source of truth for current state, critical gaps, hardening roadmap, and
> cross-cutting decisions. All other docs remain authoritative for their scope; this doc
> summarises and links them.
>
> **Last updated:** 2026-03-12

---

## 1. App Snapshot — What We Actually Have

### 1.1 Live Database State (as of March 2026)

| Table | Rows | Status |
|---|---:|---|
| `device` | 239 | ✅ Core inventory populated |
| `device_scan_history` | 92,155 | ✅ Availability history populated |
| `dashboard_events` | 1,275 | ✅ Alert history populated |
| `server_health_logs` | 905 | ✅ Raw server telemetry present |
| `tracking_samples` | 1,221 | ✅ Tracking telemetry present |
| `device_application_logs` | 819 | ✅ Productivity data present |
| `device_activity_logs` | 533 | ✅ Activity data present |
| `tracked_device_availability_events` | 1,835 | ✅ Tracking availability present |
| `audit_logs` | 614 | ✅ Audit trail present |
| `restricted_site_events` | 51 | ✅ Policy violations present |
| `tracked_devices` | 3 | ⚠️ Small sample |
| `maintenance_window` | 2 | ⚠️ Small sample |
| `server_health_hourly_rollups` | 1 | 🔴 Barely seeded — rollup scheduler not running |
| `server_health_daily_rollups` | 0 | 🔴 Missing — rollup scheduler not running |
| `daily_device_stats` | 0 | 🔴 Missing — daily uptime stats not backfilled |
| `tracking_hourly_rollups` | 0 | 🔴 Rollup missing |
| `tracking_daily_rollups` | 0 | 🔴 Rollup missing |
| `device_interfaces` | 0 | 🔴 SNMP interface collection not active |
| `interface_traffic_history` | 0 | 🔴 Bandwidth history not collected |
| `printer_metrics` | 0 | 🔴 Printer MIB polling not active |
| `print_job_audit` | 0 | 🔴 Print audit missing |
| `sites` | 0 | 🔴 Site dimension not populated |
| `departments` | 0 | 🔴 Department dimension not populated |
| `subnets` | 0 | 🔴 Subnet dimension not populated |

**Root cause of most empty rollup tables:** The maintenance/rollup scheduler jobs
(`services/maintenance_service.py`) are likely not running continuously in production.
Run `/api/maintenance/backfill-rollups` to seed historical data and verify the scheduler
is alive.

---

### 1.2 Monitoring Coverage

| Capability | State | Notes |
|---|---|---|
| SNMP v1/v2c/v3 polling | ✅ Active | Worker-based; GETBULK + fallback |
| ICMP / ping monitoring | ✅ Active | 3-strike system, latency + loss |
| Agent-based endpoint metrics | ✅ Active | 50+ metrics, token auth, auto-register |
| Agent reachability fallback (ICMP) | ✅ Implemented | Distinguishes agent-down vs host-offline |
| HTTP/TCP service checks | ✅ Active | Response time tracking |
| SNMP interface/traffic counters | 🔴 Not running | No data in `device_interfaces` |
| Printer MIB (toner, page counts) | 🔴 Not running | No data in `printer_metrics` |
| SNMP config backup (SSH/Telnet) | 🔴 Not implemented | See §4.3 |
| Windows WMI monitoring | ⚠️ Partial | In place but coverage incomplete |
| LDAP/AD authentication | ✅ Active | Local lab available (`docker-compose.ldap-lab.yml`) |

---

### 1.3 Enterprise Readiness Score: **7.5 / 10**

**Strengths:**
- SNMP/ICMP/agent infrastructure monitoring stack — solid foundation
- Worker-based task queue with `SELECT FOR UPDATE SKIP LOCKED` — horizontally scalable today
- Alert 3-strike + resolve-strike system — low false positive rate
- TimescaleDB continuous aggregate integration — partially active
- RBAC middleware + session auth — architecture correct, coverage incomplete
- Audit logging present

**Critical Deficiencies:**
- **Authorization:** 60+ endpoints lack `require_role`/`require_login` guards
- **Data leakage:** 20+ endpoints return unscoped data across departments
- **TLS:** `SESSION_COOKIE_SECURE=False` — cookies transmitted in clear text
- **Rate limiting:** Missing on most write endpoints (brute-force vector)
- **Encryption at rest:** SNMP strings, passwords, API keys stored plaintext
- **Rollup data:** Near-zero — most "Partial" reports fall back to raw queries

---

## 2. Architecture Constraints (Current Reality)

> These constraints are real and inform the roadmap prioritisation.

### 2.1 Concurrency

| Dimension | Current | Gap vs Enterprise |
|---|---|---|
| Task queue model | `poll_tasks` DB table + `SELECT FOR UPDATE SKIP LOCKED` | Sound pattern — already safe |
| Worker concurrency | 20 concurrent SNMP polls per worker process | Bottleneck at 200+ devices with fast poll intervals |
| Background scheduler | Python `schedule` + single background thread | GIL-limited; long tasks block scheduler thread |
| Async model | `asyncio` + `aioping` in scan paths | Mixed sync/async — some legacy paths not yet migrated |

**Practical limit today:** The DB task-queue model is correct and safe. The ceiling is
single-process worker throughput. With 239 devices, this is manageable. At 500+ devices
with 30s poll intervals, add a second `snmp_worker.py` process — the architecture already
supports it. Celery/Redis is a later-phase option, not an immediate need.

### 2.2 Horizontal Scaling

| Component | Current | Upgrade Path |
|---|---|---|
| Flask app | Single Waitress instance | Add Nginx + multiple Waitress workers |
| SNMP worker | Single process | Run N additional `snmp_worker.py` processes — safe today |
| Scheduler | Single thread in app process | OK at current scale; leader election needed only for multi-node |
| Database | Single PostgreSQL | Sufficient for < 1000 devices; TimescaleDB adds time-series efficiency |

### 2.3 Database

| Layer | Status |
|---|---|
| PostgreSQL primary | Active (production) |
| TimescaleDB extension | Partially integrated — `server_health_*_cagg` tables exist |
| TimescaleDB continuous aggregates | Policy may not be running; verify with `\d+ server_health_hourly_cagg` |
| Daily rollup jobs | Scheduled but data is zero — scheduler likely not sustained |
| Alembic/migrations | None — startup-invoked `utils/db_migrations.py` only |

---

## 3. Reporting Status

See `REPORTING_GAP_MATRIX.md` for the full domain matrix. Summary:

| Report | Readiness | Blocker |
|---|---|---|
| Executive Health | Partial | `daily_device_stats` empty |
| Operational | Partial | Rollup lag / policy |
| Device Health | Partial | Same |
| Productivity | Partial | Long-range still raw-query backed |
| **Network** | **BLOCKED** | `device_interfaces` + `interface_traffic_history` empty |
| Alerts | Ready | Minor SLA semantics needed |
| Maintenance & Availability | Partial | `daily_device_stats` empty |
| Security & Compliance | Partial | Coverage sparse |
| Inventory & Asset | Partial | `sites`, `departments`, `subnets` empty |
| Tracking Operations | Partial | Long-range app/activity still raw-query backed |
| **Printer Operations** | **BLOCKED** | `printer_metrics` + `print_job_audit` empty |

**Immediate fix:** Run the backfill endpoint and ensure rollup scheduler is alive.

---

## 4. Hardening Roadmap

Priority order is based on blast radius and production risk. Given the **network-constrained
environment**, each phase is scoped to be self-contained and deployable independently.

---

### Phase 1 — Critical Security (1–2 weeks) 🔴

These must be done before any wider rollout. They are purely server-side changes.

#### 4.1.1 Authorization Coverage (HIGHEST RISK)

**Problem:** 60+ endpoints are unprotected write surfaces; 20+ leak cross-department data.

**Action plan:**
1. Audit all blueprints in `routes/` for missing `@require_login` / `@require_role('admin')`
2. Apply `@require_login` to every non-public route
3. Apply `@require_role('admin')` to all write/destructive/config endpoints
4. Apply department/site scoping to list endpoints that return device data
5. Replace all inline `session.get('role')` checks with `middleware/rbac.py` decorators

**Files to audit:** `routes/devices.py`, `routes/reports.py`, `routes/user_management.py`,
`routes/tracking.py`, `routes/server_metrics.py`, `routes/sites.py`, `routes/departments.py`

**Test:** `middleware/rbac.py` already has the decorators — wiring them is the work.

#### 4.1.2 TLS / Secure Cookies

**Problem:** `SESSION_COOKIE_SECURE=False` — session hijack risk on any network.

**Action:**
```python
# config.py / env
SESSION_COOKIE_SECURE = True       # HTTPS only
SESSION_COOKIE_HTTPONLY = True      # No JS access
SESSION_COOKIE_SAMESITE = 'Lax'    # CSRF protection
```

Also configure HTTPS termination at Nginx or the host level (self-signed cert is acceptable
for internal-only deployment).

#### 4.1.3 Rate Limiting

**Problem:** Login endpoint and write APIs have no brute-force protection.

**Action:** Add `Flask-Limiter` to `extensions.py` and apply:
- Login: 5 attempts / minute per IP
- API write endpoints: 60 req / minute per user
- Agent ingestion: 120 req / minute per token

#### 4.1.4 Encrypt Sensitive Fields at Rest

**Problem:** SNMP community strings, device passwords, API keys stored in plaintext.

**Action:** Use `cryptography.fernet` (already available or trivial to add):
- Wrap read/write on `Device.snmp_community`, `SshProfile.password`, API key fields
- Store `FERNET_KEY` in environment variable — never in code

---

### Phase 2 — Data Foundation (1–2 weeks) 🟡

Fix the empty rollup tables so reports become meaningful.

#### 4.2.1 Run Rollup Backfill

```bash
# One-time: seed historical rollups
POST /api/maintenance/backfill-rollups
```

#### 4.2.2 Verify Rollup Scheduler is Alive

Check `services/scheduler.py` jobs:
- `SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE` — hourly
- `daily_device_stats` rollup — end-of-day
- Tracking hourly/daily rollups

Add a health-check endpoint that reports scheduler last-run timestamps.

#### 4.2.3 Populate Organisational Dimensions

Sites, departments, and subnets are all empty. Without them:
- RBAC scoping is nonfunctional for manager/viewer roles
- Inventory & Asset report is partial
- Subnet breakdown on dashboard is blank

**Action:** Add a setup wizard or admin UI to create at least one site and assign devices.
The code exists — the data does not.

#### 4.2.4 Enable SNMP Interface Polling

`device_interfaces` and `interface_traffic_history` are empty, blocking the Network report.

**Action:** Verify SNMP worker is configured to collect interface counters (IF-MIB walk).
The worker (`workers/snmp_worker.py`) has the capability — confirm task types are being
enqueued by the scheduler.

---

### Phase 3 — Config & Compliance (2–4 weeks) 🟡

User-identified gap: no config backup, diff, rollback, or compliance checks.

#### 4.3.1 Network Device Config Backup (via SSH)

**New capability required.** Design:

```
services/config_backup_service.py
  - connect via SSH (Paramiko / existing ssh_profiles)
  - run `show running-config` (Cisco IOS) or device-specific variant
  - store output in new table: device_config_snapshots(device_id, captured_at, config_text, hash)
  - schedule via scheduler.py: daily or on-demand

models/config_snapshot.py
  - DeviceConfigSnapshot(id, device_id, captured_at, config_text, config_hash, source)

routes/config_backup.py (new blueprint)
  - GET  /api/devices/<id>/config-history
  - POST /api/devices/<id>/config-backup  (on-demand)
  - GET  /api/devices/<id>/config-diff?from=<id>&to=<id>
```

**Config diff:** Use Python `difflib.unified_diff` on stored `config_text` strings.
No external tools required.

**Config rollback:** Show diff, require admin confirmation, push back via SSH (later phase).

#### 4.3.2 Compliance Check Templates

Currently: manual thresholds only. Gap vs OpManager:

| Feature | OpManager | Current | Plan |
|---|---|---|---|
| Vendor templates | 500+ | 0 | Build 5–10 for in-use devices |
| Compliance profiles | CIS/HIPAA | None | Define threshold sets in DB |
| Template auto-apply | Yes | No | On discovery |

**Pragmatic scope for now:**
- Add `device_compliance_profile` FK to `Device`
- Create `ComplianceProfile(id, name, rules_json)` model
- `rules_json` encodes threshold overrides: `{"cpu_critical": 90, "disk_warning": 75}`
- Apply profile thresholds in `AlertManager.check_server_health()` before global defaults

---

### Phase 4 — Functionality & Notifications (2–4 weeks) 🟢

#### 4.4.1 Email Alerting

Currently stubs only. Wire real SMTP:
```python
# services/notification_service.py
# Use smtplib or Flask-Mail
# Config: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD (env vars)
```

Alert types to wire: device offline (CRITICAL), server health threshold breach, policy violation.

#### 4.4.2 Webhook / Slack / Teams Integration

Add `AlertChannel` model with `type` (email/slack/teams/webhook) + `config_json`.
On alert persist in `DashboardEvent`, fan out to configured channels asynchronously
(enqueue as low-priority task in `poll_tasks`).

#### 4.4.3 MFA / TOTP

Add `pyotp` library. Store `totp_secret` on `User` model (encrypted via Fernet).
Enforce on login for admin accounts first.

#### 4.4.4 Devices Tab Phase C/D (from PRD §14.4)

- Reduce full-page reload jitter from auto-submit search interactions
- Progressive loading indicators
- Lightweight client telemetry for render/API latency
- Validate with 500+ device datasets

---

### Phase 5 — Observability & Hardening (1–2 months) 🟢

#### 4.5.1 Scheduler Health API

```
GET /api/admin/scheduler/status
→ { "jobs": [{ "name": "rollup_hourly", "last_run": "...", "next_run": "...", "status": "ok|late|failed" }] }
```

Expose on dashboard admin panel. Alert if any job is overdue by > 2× its interval.

#### 4.5.2 Monitoring Load Visibility (FRONTEND.md §1.4)

Global Status Strip must show:
- Poll interval + last poll duration
- Task queue backlog (`SELECT COUNT(*) FROM poll_tasks WHERE status='pending'`)
- Sync timestamp

#### 4.5.3 Structured Logging + Audit Trail

Ensure all admin actions go through `audit_logs`. Currently 614 rows — verify coverage for:
- User CRUD
- Device CRUD
- Maintenance toggle
- SNMP credential changes
- Config backup triggers

#### 4.5.4 Quality Gate Baseline

Run `python scripts/run_quality_gate.py` and capture a baseline. Target before wider rollout:
- Backend unit + integration: `≥ 80%` coverage (gate is `≥ 95%` for device console modules)
- JS coverage: `≥ 95%` (already enforced in `vitest.config.mjs`)
- 0 integration test failures

---

## 5. RBAC Current Reality vs Target

| Role | Current | Target (docs) | Gap |
|---|---|---|---|
| `admin` | Full access | Full access | None (if decorators applied) |
| `user` | Partial — inconsistent | Standard operator | Apply `require_login` everywhere |
| `manager` | Partially modelled | Site-scoped | Scoping logic present, data missing (sites=0) |
| `operator` / `viewer` | Modelled in code | Dept-scoped | Same — departments=0 |

**Immediately actionable:** Apply Phase 1 auth guards. Role expansion to manager/viewer
becomes useful only after sites and departments are populated (Phase 2).

---

## 6. Issues Catalogue (User-Identified + Doc-Derived)

| # | Issue | Severity | Phase | Status |
|---|---|---|---|---|
| 1 | 60+ endpoints unprotected | 🔴 Critical | 1 | Not started |
| 2 | SESSION_COOKIE_SECURE=False | 🔴 Critical | 1 | Not started |
| 3 | No rate limiting on auth/write | 🔴 Critical | 1 | Not started |
| 4 | Sensitive fields stored plaintext | 🔴 High | 1 | Not started |
| 5 | Rollup tables empty (reports degraded) | 🟡 High | 2 | Needs backfill |
| 6 | Sites/departments/subnets empty | 🟡 High | 2 | Data entry needed |
| 7 | SNMP interface polling not active | 🟡 High | 2 | Network report blocked |
| 8 | Config backup not implemented | 🟡 Medium | 3 | Architecture designed here |
| 9 | Config diff / rollback | 🟡 Medium | 3 | After backup |
| 10 | Compliance profile templates | 🟡 Medium | 3 | Architecture designed here |
| 11 | No email alerting (stubs only) | 🟡 Medium | 4 | Wire SMTP |
| 12 | No MFA/2FA | 🟡 Medium | 4 | pyotp approach |
| 13 | Printer MIB polling inactive | ⚪ Low | 4 | Printer report blocked |
| 14 | No Slack/Teams webhooks | ⚪ Low | 4 | Channel model needed |
| 15 | Scheduler health not visible | ⚪ Low | 5 | Admin API |
| 16 | No APM / OpenTelemetry | ⚪ Low | 5 | Post-stabilisation |
| 17 | Single Waitress instance | ⚪ Low | 5 | Nginx + multi-worker |
| 18 | GIL-limited concurrency ceiling | ⚪ Low | 5 | Add worker processes first |
| 19 | No Alembic migrations | ⚪ Low | 5 | Tech debt |

---

## 7. What NOT to Do (Scope Guard)

These are out of scope for the current hardening phase — do not introduce them:

- ❌ Celery/Redis task queue (DB queue is sufficient for < 500 devices)
- ❌ Leader election / distributed scheduler (single node is fine)
- ❌ Cloud monitoring (AWS/Azure/GCP)
- ❌ CMDB sync (ServiceNow, Jira)
- ❌ Custom report drag-and-drop builder
- ❌ Replacing Flask/Jinja stack
- ❌ Mobile app

---

## 8. Document Index

| Document | Scope |
|---|---|
| `docs/PRD.md` | Product requirements, timeline, acceptance criteria |
| `docs/AGENTS.md` | Repository rulebook — immutable rules, SNMP arch, identity, alerting |
| `docs/CONVENTIONS.md` | Coding standards, test layout, quality gate |
| `docs/FRONTEND.md` | NMS Design System v5.0 — UI rules, tokens, component specs |
| `docs/RBAC_PLAN.md` | RBAC role model, enforcement strategy |
| `docs/REPORTING_GAP_MATRIX.md` | Live data state, report domain readiness |
| `docs/FEATURE_SUMMARY.md` | Enterprise readiness summary, critical recommendations |
| `docs/SERVER_CONNECTIONS_TEST_MATRIX.md` | Server connection testing |
| `docs/SERVER_MONITORING_UI_GUIDE.md` | Server monitoring UI patterns |
| `docs/LDAP_TEST_LAB.md` | Local LDAP/AD test setup |
| `docs/skills/security-standards/references/auth-rules.md` | Auth enforcement checklist |
| `docs/skills/backend-standards/references/scaling-guidelines.md` | Scaling + concurrency rules |
| `docs/skills/backend-standards/references/database-performance.md` | DB query patterns |
| `docs/skills/backend-standards/references/api-contracts.md` | API response contracts |
| `docs/skills/backend-standards/references/agent-reachability.md` | Agent fallback rules |
| `docs/skills/frontend-standards/references/ui-consistency.md` | UI component rules |
| `docs/skills/frontend-standards/references/state-management.md` | Frontend state patterns |
| **`docs/MASTER_PLAN.md`** | **← This file — central hub** |

---

## 9. Recommended Next Actions (This Week)

1. **Run backfill** — `POST /api/maintenance/backfill-rollups` — takes minutes, unblocks reports
2. **Verify scheduler** — confirm `services/scheduler.py` rollup jobs are executing; add log output
3. **Start Phase 1 auth audit** — pick the 10 highest-risk write endpoints in `routes/devices.py`
   and `routes/user_management.py` and add `@require_login` / `@require_role`
4. **Set `SESSION_COOKIE_SECURE=True`** — one config line, zero code change
5. **Create one Site + one Department** — unlock RBAC scoping and unblock Inventory report

These five actions can be done within 2 days and materially reduce risk and improve report quality.

---

*This document should be updated whenever a phase milestone is completed or a new issue is identified.*
