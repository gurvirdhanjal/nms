# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 1. What This App Is

On-premise Network Monitoring System (NMS) for IT ops: SNMP/ICMP/agent-based monitoring,
real-time alerting, employee tracking, reporting, and exports.
Stack: Python 3 + Flask (blueprints) + SQLAlchemy + PostgreSQL 16 + TimescaleDB 2.25.2
(port 5433, Docker). Jinja2 + Bootstrap 5 + Chart.js frontend.
Scale: 239 managed devices, 92K scan history rows, 1,275 alerts. Single-node on-prem.

---

## 2. Repo Map

### Entry Points
- `app.py` — Flask factory (`create_app`), port 5001
- `run_prod.py` — Waitress production runner
- `web_main.py` — alternate local runner (port 5000)
- `config.py` — all config + env-var bindings
- `extensions.py` — Flask extensions (db, limiter, compress, etc.)

### Backend
- `routes/` — Flask blueprints (one file = one domain)
  - `auth.py` — login/logout/register/OTP/reset
  - `devices.py` — device CRUD, bulk ops, type updates
  - `dashboard.py` — summary, alerts, trends, inventory
  - `tracking.py` — employee device tracking, live status, history
  - `reports.py` — all report endpoints + async export jobs
  - `server_metrics.py` — server telemetry modal/page
  - `monitoring.py` — device status, statistics, events
  - `snmp.py` — SNMP poll, config, interfaces, counters
  - `maintenance.py` — cleanup, aggregation, rollup backfill
  - `scanning.py` — network scan, SNMP discovery
  - `user_management.py` — user CRUD, role toggle
  - `sites.py`, `departments.py`, `subnets.py` — org dimensions
  - `agent.py` — agent metrics ingestion (token auth)
  - `file_transfer.py` — remote file ops
  - `print_jobs.py`, `printer.py` — printer MIB + audit
  - `device_console.py` — website policy, alerts, console API
  - `audit.py`, `sse.py`, `service_checks.py` — audit log, SSE stream, TCP/HTTP checks
  - `switch_discovery.py`, `discovery_settings.py` — switch CAM/LLDP discovery

- `services/` — business logic (no direct route logic here)
  - `scheduler.py` — background job scheduler (`schedule` lib, background thread)
  - `maintenance_service.py` — rollup jobs, retention, backfill
  - `reporting_service.py` — all report query logic
  - `alert_manager.py` — 3-strike alert evaluation
  - `timescaledb_service.py` — TimescaleDB continuous aggregate queries
  - `tracking_sync_core_service.py` — tracking device sync
  - `export_service.py` — CSV/XLSX/PDF server-side export
  - `notification_service.py` — email stubs (not yet wired)
  - `ssh_service.py`, `snmp_service.py`, `snmp_discovery_service.py`
  - `device_identity.py` — identity resolution (UUID > MAC > hostname > IP)

- `workers/` — standalone background processes
  - `snmp_worker.py` — SNMP polling (`SELECT FOR UPDATE SKIP LOCKED`)
  - `alert_fanout_worker.py` — alert channel dispatch
  - `tracking_sync_worker.py` — tracking data sync
  - `violation_ingest_worker.py` — policy violation ingestion
  - `policy_rebuild_worker.py` — policy cache rebuild

- `models/` — SQLAlchemy models (one file = one domain)
  - `device.py`, `user.py`, `site.py`, `department.py`, `subnet.py`
  - `server_health.py`, `server_health_rollups.py`
  - `tracked_device.py`, `scan_history.py`, `dashboard.py`
  - `poll_task.py` — task queue for workers
  - `audit_log.py`, `report_export_job.py`, `printer.py`, `interfaces.py`

- `middleware/`
  - `rbac.py` — `require_login`, `require_role`, `require_permission`
  - `session_middleware.py` — session timeout enforcement

- `utils/`
  - `db_migrations.py` — startup-invoked schema migrations (no Alembic)
  - `server_health.py`, `helpers.py`, `network_tools.py`

### Frontend
- `templates/` — Jinja2 templates
- `static/js/` — ES module JS per page
- `static/css/tactical.css` + per-page CSS — NMS Design System v5.0 tokens

### Infrastructure
- `docker-compose.timescaledb.yml` — TimescaleDB container (port 5433)
- `docker-compose.ldap-lab.yml` — local LDAP/AD test lab

---

## 3. Current Phase & Status

**Active Phase: Phase 4 — Notifications** 🟡

### Phase 1 — Critical Security ✅ Complete

| Item | Status |
|---|---|
| Auth decorator audit (60+ endpoints) | ✅ Done — `@require_login` / `@require_role` applied across `user_management`, `tracking`, `departments`, `sites`, `subnets` |
| `SESSION_COOKIE_SECURE=True` | ✅ Done — default changed in `config.py` line 132 |
| Flask-Limiter rate limiting | ✅ Done — login route limited to 5/min per IP; custom 429 GUI page with countdown |
| Fernet encryption for sensitive fields | ✅ Done — `utils/encryption.py`; `Device.snmp_community` encrypted via property/setter |
| Rollup backfill (`/api/maintenance/backfill-rollups`) | ⏭ Deferred to Phase 2 |
| Sites / departments created in DB | ⏭ Deferred to Phase 2 |

### Phase 2 — Data Foundation ✅ Complete (SNMP interfaces paused)

| Item | Status |
|---|---|
| Run `/api/maintenance/backfill-rollups` | ✅ Done |
| Verify scheduler jobs are running continuously | ✅ Done — `_JOB_REGISTRY` added; `GET /api/maintenance/scheduler/status` endpoint live |
| Create at least 1 Site + 1 Department in DB | ✅ Done |
| Enable SNMP interface polling | ⏸ Paused — awaiting managed switch access |
| Fix SCOPING FIXMEs in `departments.py` and `subnets.py` | ✅ Done |

### Phase 3 — Config & Compliance ✅ Complete

| Item | Status |
|---|---|
| `DeviceConfigSnapshot` model + SSH config capture | ✅ Done — `models/config_snapshot.py`; `services/config_backup_service.py` (`capture_config()`); migration in `utils/db_migrations.py` |
| Config diff API (`difflib`) | ✅ Done — `routes/config_backup.py`; `GET /api/devices/<id>/config-history`, `POST /api/devices/<id>/config-backup`, `GET /api/devices/<id>/config-diff` |
| `ComplianceProfile` thresholds model | ✅ Done — `models/compliance_profile.py`; `compliance_profile_id` FK on `Device`; migration in `utils/db_migrations.py` |
| Compliance threshold override in AlertManager | ✅ Done — `AlertManager._get_thresholds(device)` applies `rules_json` overrides; `check_server_health()` uses per-device thresholds |
| Daily config backup scheduler job | ✅ Done — `MonitoringScheduler.enqueue_config_backup_tasks()` at 02:00; worker handler in `snmp_worker._execute_config_backup()` |

### Phase 4 — Notifications 🟡 Not Started

| Item | Status |
|---|---|
| Wire `notification_service.py` for real SMTP email | Not started |
| `AlertChannel` model (email, Slack, Teams) | Not started |
| Webhook dispatch (Slack/Teams) | Not started |
| MFA / TOTP (`pyotp`) | Not started |

_Update this section after completing each item._

---

## BANNED COMMANDS — Never Run These, Ever

A subagent previously ran `git reset --hard HEAD` and permanently destroyed uncommitted working
tree changes that could not be recovered (SNMP edits, device tab optimizations, and dozens of
other modified files). **This must never happen again.**

The following commands are **absolutely banned** for Claude and all subagents in this project:

```
git reset --hard (any variant)
git checkout -- . or git checkout -- <file>
git restore . or git restore <file>  (working tree only — staging-only is fine)
git clean -f / -fd / -fdx
```

If you think you need one of these, **STOP and ask the user first.** No exceptions.

---

## 4. Hard Rules (Follow Every Session)

1. **One file at a time.** Do not touch unrelated files even if they look wrong.
2. **No refactoring outside task scope.** Note issues in a `# FIXME:` comment and move on.
3. **No new dependencies** unless the task explicitly requires one. If adding: update `requirements.txt`.
4. **Additive-only changes.** Preserve existing behaviour. If removing something, ask first.
5. **After every change, state:** what changed, why, and what the user should manually test.
6. **Auth decorators go on routes, not services.** Use `middleware/rbac.py` decorators — never inline `session.get('role')`.
7. **Scheduler never does network I/O.** It enqueues to `poll_tasks` only. Workers execute.
8. **Device site assignment: NEVER auto-reassign existing devices.** Auto-assign only for new devices. Preserve `site_id` on existing ones. Log all manual reassignments.
9. **IP is never the sole device identifier.** Identity hierarchy: UUID > MAC > hostname > IP.
10. **Reports are read-only.** Report endpoints must never write to DB.

### 4.1 Frontend / Tracking Guardrails

- Tracking screenshots showing **Stored Employee Devices** map to `templates/tracking/device_tracking.html`,
  not `templates/tracking/live_tracking.html`.
- Route-scoped UI primitives stay additive during migration. Prefer
  `templates/components/ui_primitives_assets.html` on touched surfaces instead of loading new UI helpers
  globally.
- On the tracking device management page, the visible destructive action is **Delete**. The UI should not be
  renamed back to **Archive** unless the backend contract is changed intentionally.
- Tracking delete currently uses `POST /api/tracking/delete-device` with `purge=true` from the page JS.
  Backend cleanup is handled in `routes/tracking.py::_purge_tracked_device()` and must clear dependent
  tracking/history rows before deleting `tracked_devices`.
- Premium/dense polish for dashboard, sites, departments, and tracking pages is frontend-only by default.
  Do not introduce backend changes just to support spacing, color, alignment, or modal copy updates.

### 4.2 Module Boundaries

**Tracking Module**
- **Purpose:** real-time operational monitoring.
- **Contains:** `/tracking`, `/tracking/live/<id>`, `/tracking/history/<id>`, scan modal, device status, policy state, availability.
- **Focus:** current state, operational actions, tactical monitoring.
- **UI Design Rule:** Show current state, live telemetry, and device actions. Never mix analytical/trending views here.

**Reports Module**
- **Purpose:** analytical and historical reporting.
- **Contains:** Device Inspector, usage reports, SLA reporting, trend analysis, compliance views.
- **Focus:** lifecycle analysis, long-range telemetry, aggregation and visualization.
- **UI Design Rule:** Show trend graphs, time aggregation, long-range history, and analysis. Never mix tactical/live monitoring here.

### 4.3 Device Live View Patterns (`/devices/<id>`)

- **MORE dropdown** must always have menu items. Empty `<ul class="dropdown-menu">` is a bug.
  Current items: Device Details, View History, Run Integrity, Archive Device.
- **KPI cards and header stats** must be interactive. All `.kpi-clickable` and `.kpi-stat-wrap`
  elements use `data-kpi-key` to open `#kpiBreakdownModal` via `handleKpiBreakdownClick()`.
  IP stat uses clipboard copy instead of modal.
- **Anti-flicker rule**: Never replace `.stat-value` text directly without CSS `transition`.
  Smooth value changes via `transition: color 0.25s ease` are required on all live-updating elements.
  Mark elements `.kpi-updating` (sets `opacity: 0.55`) during updates, remove class after render.
- **Remote view double-buffering**: Load new screenshot blob into `#remoteViewImageBack` first.
  Swap to `#remoteViewImage` only after `onload` fires on the back buffer. Never assign a new blob
  URL directly to the front buffer (causes white-flash flicker).
- **Remote view fullscreen**: The `.modal-content` needs `display: flex; flex-direction: column`
  and `.modal-body` needs `flex: 1 1 auto; overflow: hidden` for the frame to fill 100vh correctly.
  Override `height: 100% !important` on `.remote-view-frame` in the fullscreen context.

### 4.4 Redis Caching Conventions

- `tracking:realtime:<mac_address>` — 8s TTL, written on successful live probe with metrics.
  Provides instant data on page reload (anti-flicker / stale-while-revalidate).
- `tracking:discovery-probe:ip:<ip>` — 120s TTL (tracking probe results).
- `tracking:agent-port:ip:<ip>` — 43200s TTL (12h, agent port mapping).
- `dashboard:*` — namespace invalidated via `dashboard_cache_service.invalidate_dashboard_namespace()`.
- **Rule**: Redis writes are always best-effort (`try/except pass`). Never let Redis failure block a
  response. Check `is_redis_available()` before any Redis operation in request context.

---

## 5. TimescaleDB Quick Reference

DB runs in Docker container `monitoring_timescaledb` on port **5433**.

```bash
# Start
docker start monitoring_timescaledb

# Connect
docker exec -it monitoring_timescaledb psql -U monitoring_man -d monitoring_db

# Check job health
SELECT * FROM v_timescaledb_job_health;

# Check continuous aggregate freshness
SELECT * FROM v_hypertable_stats;
```

**5 Hypertables:** `server_health_logs`, `tracking_samples`, `device_resource_logs`,
`device_activity_logs`, `device_application_logs`

**4 Continuous Aggregates:** `server_health_hourly_cagg`, `server_health_daily_cagg`,
`server_health_daily_extended_cagg`, `tracking_hourly_cagg`

**Query routing rule:**
- `≤ 24h` → raw logs
- `≤ 30d` → `*_hourly_cagg`
- `> 30d` → `*_daily_cagg`

---

## 6. Phase Roadmap Summary

| Phase | Focus | Key Actions |
|---|---|---|
| **1** 🔴 Security | Auth + TLS + rate limiting + encryption | Decorators on 60+ routes, `SESSION_COOKIE_SECURE=True`, Flask-Limiter, Fernet |
| **2** 🟡 Data | Rollups + org dimensions + SNMP interfaces | Backfill endpoint, create Site+Dept, verify interface polling |
| **3** 🟡 Config & Compliance | SSH config backup, diff API, compliance profiles | `DeviceConfigSnapshot` model, `difflib` diff, `ComplianceProfile` thresholds |
| **4** 🟢 Notifications | SMTP email, webhooks, MFA/TOTP | Wire `notification_service.py`, `AlertChannel` model, `pyotp` |
| **5** 🟢 Observability | Scheduler health API, structured logging, quality gate | `/api/admin/scheduler/status`, audit coverage, quality gate baseline |

---

## 7. What NOT To Do (Ever)

- ❌ Celery/Redis task queue — DB queue (`poll_tasks`) is sufficient for < 500 devices
- ❌ Leader election / distributed scheduler — single node, not needed yet
- ❌ Cloud monitoring (AWS/Azure/GCP integration)
- ❌ CMDB sync (ServiceNow, Jira)
- ❌ Custom drag-and-drop report builder
- ❌ Replace Flask/Jinja stack
- ❌ Mobile app
- ❌ Auto-reassign existing device site/department without admin confirmation
- ❌ `session.get('role')` inline checks — always use `middleware/rbac.py` decorators
- ❌ Network I/O in the scheduler thread — enqueue only

---

## 8. Where We Left Off

> **Update this after every session.**

```
Phase 1 completed (2026-03-12):
  - Auth decorators applied: user_management.py, tracking.py, departments.py, sites.py, subnets.py
  - SESSION_COOKIE_SECURE=True set in config.py
  - Flask-Limiter 4.1.1 installed; login route limited to 5/min; 429 GUI page with countdown timer
  - Fernet encryption wired: utils/encryption.py created; Device.snmp_community encrypted via
    property/setter; _widen_snmp_community_column() migration added to db_migrations.py
  - SCOPING FIXMEs fixed: departments.py:department_profile, subnets.py:get_subnets

Phase 2 completed (2026-03-12):
  - Rollup backfill run via /api/maintenance/backfill-rollups
  - Scheduler health endpoint live: GET /api/maintenance/scheduler/status (@require_role admin)
    _JOB_REGISTRY tracks last_run + status for all 9 rollup jobs
  - 1 Site + 1 Department created in DB; RBAC scoping unblocked
  - SNMP pilot batch: 5 devices enabled in device_snmp_config (is_enabled=True, community='public')
    scripts/enable_snmp_pilot.py written for future re-runs
  - InterfacePoller.poll_device_interfaces(device_id) implemented in services/interface_poller.py
    Walks IF-MIB via snmp_service, upserts device_interfaces, writes interface_traffic_history
  ⚠ SNMP interface polling PAUSED — pilot devices (172.16.1.x workstations) do not run SNMP agents
    Resume when a managed switch or SNMP-enabled device is accessible

Phase 3 completed (2026-03-12):
  - models/config_snapshot.py: DeviceConfigSnapshot (device_id, captured_at, config_text,
    config_hash [SHA-256 auto-computed via @validates], source, captured_by_user_id)
    Table: device_config_snapshots; composite index (device_id, captured_at DESC)
  - utils/db_migrations.py: _ensure_device_config_snapshot_table() + _ensure_compliance_profile_tables()
  - services/config_backup_service.py: capture_config(device_id, source, user_id)
    DEVICE_TYPE_COMMANDS map (13 entries); SSH via SSHService
  - routes/config_backup.py: 3 endpoints under /api/devices
    GET /config-history, POST /config-backup (@require_role admin), GET /config-diff
  - services/scheduler.py: enqueue_config_backup_tasks() daily at 02:00; backup_device_configs in JOB_META
  - workers/snmp_worker.py: _execute_config_backup() handler for task_type='config_backup'
  - models/compliance_profile.py: ComplianceProfile(id, name, description, rules_json, created_at, updated_at)
  - models/device.py: compliance_profile_id FK column added
  - services/alert_manager.py: _RULES_JSON_MAP + _get_thresholds(device) classmethod
    check_server_health() now uses per-device compliance profile thresholds
  - app.py: ComplianceProfile imported for db.create_all() FK resolution on fresh installs

Phase 4 starting — Notifications:
  Session 1 next tasks:
    - Wire notification_service.py for real SMTP email (AlertChannel model or config-based)
    - Webhook dispatch stubs for Slack/Teams
    - Review pyotp integration for MFA/TOTP

Tracking / UI session updates (2026-03-13):
  - `templates/tracking/device_tracking.html` densified and aligned closer to dashboard tone
    using page-scoped CSS changes in `static/css/tracking/device_tracking.css`
  - Top Add Device button on the tracking device page wired to the existing modal flow
  - Delete modal copy changed from Archive -> Delete on the tracking device page
  - `static/js/tracking/device_tracking.js` now uses delete wording in notifications while preserving
    the existing delete endpoint
  - Hard-delete failure fixed in `routes/tracking.py`:
    `_purge_tracked_device()` now clears dependent rows
    (`tracked_device_availability_events`, rollups, logs, identity/policy rows, IP history, remote scan
    history, etc.) before deleting the tracked device
  - Purge smoke-tested with a temporary tracked device plus dependent availability event:
    delete returned 200 and both parent/dependent rows were removed successfully

Elegance + hardening sprint (2026-03-13):
  - app.py: added _apply_security_headers() after_request hook (X-Frame-Options, X-Content-Type-Options,
    X-XSS-Protection, Referrer-Policy); added _wants_json() + errorhandlers for 404/403/500
  - templates/errors/: created 404.html, 403.html, 500.html (styled to match 429.html brand)
  - routes/monitoring.py: replaced 20 print() calls with logger.*; bare excepts → logger.exception
  - routes/tracking.py: replaced 19 print() calls with logger.*
  - routes/devices.py: replaced 8 print() calls with logger.*; removed DEBUG DB URI block
  - routes/dashboard.py: added logger; replaced 8 print() calls + 6 str(e) exposures
  - routes/maintenance.py: added logger; all bare `except Exception as e: str(e)` → logger.exception
  - static/css/tactical.css: added .card-stale CSS rule (completes JS-only half-implementation)
  - templates/partials/server_details_modal.html: moved 10+ inline styles to scoped <style> block
  - templates/reports.html: updated setTableStatusRow() to preserve rows during refresh;
    patchTableRows() removes .table-refreshing overlay; .table-refreshing CSS added
  - Deleted orphaned templates: server_dashboard_new.html, file_transfer_temp.html

Device Live View sprint (2026-03-13):
  - templates/tracking/device_live.html: MORE dropdown populated (was empty <ul>)
    Items: Device Details, View History, Run Integrity Check, Archive Device
  - templates/tracking/device_live.html: All header stats wrapped in .kpi-stat-wrap with
    data-kpi-key attributes (ip, latency, uptime, agent, security) — cursor pointer + hover teal glow
  - templates/tracking/device_live.html: Overview KPI cards (.kpi-clickable) + data-kpi-key
    attributes on all 6 cards (cpu, ram, disk, upload, download, idle) — expand icon on hover
  - templates/tracking/device_live.html: Remote view double-buffer added (#remoteViewImageBack)
    eliminates white-flash flicker on screenshot refresh
  - templates/tracking/device_live.html: #kpiBreakdownModal added for rich KPI breakdowns
  - static/css/tracking/device_live.css: CSS transitions on .stat-value (color 0.25s ease)
    .kpi-updating class for update dimming; .kpi-stat-wrap + .kpi-clickable hover styles
    Remote view fullscreen height cascade fix (display:flex on .modal-content)
    Back-buffer CSS (position:absolute, opacity:0, z-index:0)
  - static/js/tracking/device_live.js: handleKpiBreakdownClick() + renderKpiBreakdownContent()
    Uptime breakdown fetches /api/tracking/workstation/<id>/overview
    Series breakdown (cpu/ram/disk/upload/download) shows current/avg/min/max from state.series
    Agent breakdown shows version/health/last-seen; security shows score/risk/violations
    handleRunIntegrityFromDropdown() wired to POST /api/tracking/history/<id>/run-integrity
    #dropdownArchiveDevice wired to existing handleIsolateAction()
    state.modals.kpiBreakdown registered alongside other modals
  - routes/tracking.py: Redis SWR cache added to api_real_time_tracking()
    Key: tracking:realtime:<mac>, TTL: 8s, written on successful live probe with has_metrics=True
    Redis read inserted between in-memory cache miss and live probe (anti-flicker on page reload)
  - workstation_monitor removed: route now redirects to device_history; template + JS deleted

Discovered gaps (still open):
  - 72 print() calls remain in services/ — de-prioritised, not customer-visible
  - No CSRF protection on any POST routes — needs Flask-WTF (Phase 5)
  - SESSION_COOKIE_SECURE=False in app.py line 63 — keep until HTTPS cert in place
  - Remote view double-buffer swap logic not yet wired in device_live.js refreshRemoteViewSnapshot()
    (back-buffer HTML added; JS swap logic is additive enhancement for next session)

Blockers (still open):
  - FERNET_KEY must be set in .env before restarting the app — generate with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  - SESSION_COOKIE_SECURE=True will break login on plain HTTP — set SESSION_COOKIE_SECURE=false
    in .env until HTTPS/self-signed cert is in place
```

---

## 9. Development Commands

```bash
# Dev server — starts scheduler + interface poller + browser auto-open (port 5000)
python web_main.py

# Production server (Waitress, port 5001, 6 threads)
python run_prod.py

# NOTE: `python app.py` is NOT a valid run command — app.py is a Flask factory with no __main__ block

# Python tests
pytest tests/                           # all tests
pytest tests/ -m unit                   # fast isolated tests only
pytest tests/ -m integration            # DB/Flask integration tests only
pytest tests/unit/services/test_X.py   # single test file

# JS tests (Vitest — covers static/js/tracking and dashboard modules)
npm run test:js                         # all JS unit tests
npm run test:js:coverage                # with v8 coverage report
```

---

## 10. Key Doc Index

| Doc | What It Contains |
|---|---|
| `docs/MASTER_PLAN.md` | Full status, issues catalogue, architecture constraints |
| `docs/AGENTS.md` | Immutable rules: SNMP arch, device identity, alert persistence |
| `docs/FRONTEND.md` | NMS Design System v5.0 — CSS tokens, component contracts |
| `docs/RBAC_PLAN.md` | Role model, enforcement strategy, LDAP mapping |
| `docs/REPORTING_GAP_MATRIX.md` | Report readiness per domain |
| `docs/FEATURE_SUMMARY.md` | Enterprise readiness score + critical recommendations |
| `docs/CONVENTIONS.md` | Coding standards, test layout, quality gate |
| `docs/PRD.md` | Product requirements, timeline, acceptance criteria |
| `docs/skills/security-standards/references/auth-rules.md` | Auth enforcement checklist |
| `docs/skills/backend-standards/references/scaling-guidelines.md` | Scaling + concurrency rules |
| `docs/skills/backend-standards/references/agent-reachability.md` | Agent reachability rules |

---

## 11. GStack Skills

This project uses `gstack` for advanced AI assistance. Follow these rules for web-enabled tasks:

- **Web Browsing**: Always use the `/browse` skill from `gstack` for all web browsing tasks.
- **Tool Restriction**: Never use `mcp__claude-in-chrome__*` tools.
- **Available Skills**:
  - `/plan-ceo-review` — Request a high-level executive summary and review of plans.
  - `/plan-eng-review` — Request a technical engineering review of implementation plans.
  - `/review` — Perform an automated code review of current changes.
  - `/ship` — Automate the shipping process (tests, sync, PR).
  - `/browse` — Immersive browser automation for web research and verification.
  - `/qa` — Run automated quality assurance tests.
  - `/setup-browser-cookies` — Configure browser cookies for authenticated browsing.
  - `/retro` — Conduct an engineering retrospective on completed work.
