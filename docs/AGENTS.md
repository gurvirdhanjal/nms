# Repository Rulebook (`AGENTS.md`)

This file is the operational guide for contributors and AI agents in this repository.
Keep it aligned with the codebase as it changes.

## 1. Project Overview
- Name: `Device Monitoring Tactical`
- Type: Network and device monitoring system
- Core function: Real-time monitoring (SNMP, WMI, ping, agent metrics), dashboard visualization, alerting, reporting, and exports

## 2. Technology Stack

### Backend
- Language: Python 3.x
- Framework: Flask (modular blueprints)
- ORM: SQLAlchemy
- Database:
  - Default local/dev runtime: SQLite at `instance/device_monitoring.db` (`config.py`)
  - Production target: PostgreSQL (`psycopg2-binary`)
- Async/network model:
  - `asyncio` + `aioping` in monitoring/scanning paths
  - Background scheduling via `schedule` (not APScheduler)
- Key libraries used in repo:
  - `schedule`, `aioping`, `pysnmp`, `wmi` (Windows), `opencv-python`, `openpyxl`

### Frontend
- Core: Vanilla JavaScript (ES modules in dashboard paths)
- Templating: Jinja2
- UI libraries in use: Bootstrap 5, Chart.js

### Infrastructure
- Docker + Docker Compose (`Dockerfile`, `docker-compose.yml`)
- Compose stack includes `app` + PostgreSQL service

## 3. Runtime Entry Points and Structure

### Main runtime entry points
- `app.py`: Flask app factory + direct run path (port `5001`)
- `run_prod.py`: Waitress production runner
- `web_main.py`: alternate local runner (port `5000`)
- `run.py`: SQLite desktop viewer utility, not a Flask server entrypoint

### Key folders
- `routes/`: Flask blueprints and APIs
- `models/`: SQLAlchemy models (`server_health`, rollups, tracking, etc.)
- `services/`: schedulers, monitoring, reporting, maintenance, discovery
- `utils/`: helpers and DB migration helpers
- `static/`, `templates/`: frontend assets and pages
- `tests/`: verification and regression scripts

## 4. Coding Standards

### Python
- Follow PEP 8
- Prefer explicit, narrow exceptions over bare `except:`
- Use docstrings for non-trivial service logic and data-processing functions
- Keep imports grouped: stdlib, third-party, local
- Use `async`/`await` for network-bound scanning/monitoring paths where already adopted

### JavaScript
- Prefer ES modules in modular dashboard code
- Prefer `const`/`let` over `var`
- Use `fetch` with async patterns in new code

### Database and queries
- Prefer SQLAlchemy ORM for normal CRUD/report reads
- Raw SQL is acceptable for retention/rollup/index operations where performance or SQL constructs are required
- Schema changes must be reflected in model + migration path (`utils/db_migrations.py` and/or `run_migration.py`)

## 5. Migrations and Data Lifecycle
- There is no Alembic/Flask-Migrate workflow in this repo today.
- Runtime migration helpers live in `utils/db_migrations.py` and are invoked by app startup.
- Manual migration trigger script: `run_migration.py`.
- Server health retention/rollups are handled by `services/maintenance_service.py` and scheduled in `services/scheduler.py`.

## 6. Scheduler and Concurrency Model
- Scheduler implementation: `schedule` + background thread (`services/scheduler.py`)
- **CRITICAL: Scheduler must never perform network I/O.**
  - Scheduler role is EXCLUSIVELY to enqueue tasks into `poll_tasks` table.
  - All network polling (SNMP, WMI, scanning) must be executed via worker processes.
- Monitoring work mixes:
  - `snmp_worker.py`: Dedicated process for SNMP polling (health, interfaces, discovery).
  - `asyncio` execution for scanner/monitor tasks (legacy paths being migrated).

## 7. Security and Configuration Rules
- Do not commit new secrets or credentials.
- Prefer environment variables for runtime secrets and credentials.
- Protected business endpoints should require auth:
  - Session middleware for protected blueprints
  - Token auth for agent ingestion (`/api/agent/metrics`)
- If you touch auth-sensitive routes, verify they are protected consistently.

### RBAC baseline (current)
- Role model is currently two roles: `admin`, `user`.
- Central RBAC helpers live in `middleware/rbac.py`:
  - `require_login`
  - `require_role(...)`
  - `require_permission(...)` (for future expansion)
- Admin-only routes must use centralized RBAC decorators (avoid inline `session.get('role')` checks).

Note:
- The repository currently contains some hardcoded defaults/credentials in existing files.
- Do not introduce additional hardcoded secrets; prefer migration toward env-based configuration.

## 8. Reporting and Export Rules

These rules apply to report services/routes under `routes/reports.py` and `services/reporting_service.py`.

### Rule 1 - Time-range source selection
- For device health report paths, use:
  - `<= 24h`: `server_health_logs` (raw)
  - `<= 30d`: `server_health_hourly_rollups` or `server_health_hourly_cagg` when TimescaleDB is enabled
  - `> 30d`: `server_health_daily_rollups` or `server_health_daily_cagg` when TimescaleDB is enabled
- Current code status:
  - `get_device_health_report` follows this pattern and prefers Timescale continuous aggregates for longer windows when available.
  - `get_operational_report` follows the same raw/hourly/daily source-selection pattern for server-health heatmaps. Do not add new large-range raw aggregation paths.

### Rule 2 - Reports are read-only
- Report endpoints/services must not write to the DB.
- Keep report generation as pure read/query logic.

### Rule 3 - Report exports are server-side
- Report exports must be generated server-side via Flask:
  - Routes: `/api/reports/<report_type>/export` and async `/api/reports/<report_type>/export-jobs`
  - Implementation: `services/export_service.py` (`csv`, `openpyxl`) + `send_file`
- Do not add browser-generated exports for report endpoints.
- Async export jobs must preserve the caller RBAC/session scope and generate the same decorated payload/meta contract as synchronous exports.

### Rule 4 - Report safety guards are mandatory
- Enforce hard API time-range caps:
  - Global: `MAX_REPORT_RANGE_DAYS` (default 90)
  - Per-report overrides (example: network/productivity 30)
- Enforce hard row limits:
  - API payloads: `MAX_REPORT_ROWS`
  - Exports: `MAX_EXPORT_ROWS` (reject oversize export with HTTP 413)
- Apply PostgreSQL query timeout per request using `SET LOCAL statement_timeout`.
- Apply per-user report rate limits:
  - Query: `REPORT_RATE_LIMIT_PER_MINUTE`
  - Export: `REPORT_EXPORT_RATE_LIMIT_PER_MINUTE`
- Cache report responses for repeated filters using short TTL (`REPORT_CACHE_TTL_SECONDS`).
- Emit structured report telemetry logs for query duration and row counts.

### Rule 5 - Rollup integrity is scheduled
- Daily rollup integrity validation/repair must run via scheduler:
  - `SERVER_HEALTH_ROLLUP_INTEGRITY_SCHEDULE`
  - `SERVER_HEALTH_ROLLUP_INTEGRITY_LOOKBACK_DAYS`
- Repair job must remain idempotent (`ON CONFLICT DO NOTHING`) and PostgreSQL-only.

Note:
- Some non-report tracking pages currently include client-side CSV export behavior. Treat that as legacy behavior outside the report export contract.

## 9. Change Control Checklist
- For model changes:
  - Update model class
  - Update migration helper(s)
  - Validate affected APIs/services
- For monitoring metric additions:
  - Add collection
  - Add ingestion parsing/validation
  - Add persistence columns
  - Add API exposure
  - Verify compile/runtime paths
- For reporting changes:
  - Validate data source by time range
  - Keep endpoints read-only
  - Ensure export compatibility

## 10. Alert Persistence Rules (IMMUTABLE)

### Rule 1 â€” Only WARNING and CRITICAL alerts are persisted
| Severity | Store in DB | Show in Reports | Real-Time Display |
|----------|-------------|-----------------|-------------------|
| CRITICAL | âœ… | âœ… | âœ… |
| WARNING  | âœ… | âœ… | âœ… |
| INFO     | âŒ | âŒ | âœ… (ephemeral) |

Informational alerts (transient ping spikes, minor packet loss) are **noise**.
They MUST NOT be written to `DashboardEvent`. They are shown in real-time SSE only.

### Rule 2 â€” ICMP alerts require consecutive-scan escalation
Single-scan threshold violations are noise, not alerts.

| Metric | Threshold | Consecutive Scans Required | Severity |
|--------|-----------|---------------------------|----------|
| Latency | â‰¥ 200ms | 3 | WARNING |
| Packet Loss | â‰¥ 10% | 3 | WARNING |
| Offline | unreachable | 3 | CRITICAL |

Strike counters: `device.latency_strikes`, `device.packet_loss_strikes`, `device.offline_strikes`

### Rule 3 â€” Recovery requires consecutive clear scans
To prevent alert flapping, resolving an escalated alert requires `RESOLVE_STRIKES_REQUIRED` (currently 2) consecutive normal scans before the alert is resolved.

### Rule 4 â€” Server health alerts use separate strikes
Server health (CPU/RAM/Disk) â€” whether from Agent or SNMP â€” uses `device.health_alert_strikes` with the same 3-strike pattern. These are independent from ICMP strikes.

## 11. SNMP Architecture (Phase 1 & 2)

### Task Queue Model
- **DB Table**: `poll_tasks` stores all pending/running/history of SNMP operations.
- **Workers**: `workers/snmp_worker.py` runs as a standalone process.
- **Concurrency**: Workers use `SELECT FOR UPDATE SKIP LOCKED` to safely claim tasks without duplication.
- **Scaling**: Safe to run multiple worker instances (horizontal scaling).

### Polling Mechanics
- **BulkCmd**: all table walks (interfaces, health) use `bulkCmd` (GETBULK) with fallback to `nextCmd` for v1.
- **Error Types**: Typed errors (`SnmpTimeoutError`, `SnmpAuthError`) are classified and stored in `error_code`.
- **Retry Logic**: Exponential backoff (2^n seconds) for up to 3 retries.

### Monitoring & Alerts
- **Thresholds**: SNMP-polled CPU/RAM/Disk metrics feed into `AlertManager.check_server_health()`.
- **Consistency**: Exact same thresholds/logic as Agent-based monitoring.

## 12. Device Identity Principles (IMMUTABLE)

### Rule 1 â€” Identity Hierarchy
Devices are identified by the following keys, in priority order:

| Priority | Key | Stability | Example |
|----------|-----|-----------|---------|
| 1 | `unique_client_id` (UUID) | Permanent | Agent-assigned UUID |
| 2 | `macaddress` (MAC) | Very High | `aa:bb:cc:dd:ee:ff` |
| 3 | `hostname` (unique, non-generic) | High | `srv-dc-01`, `fw-main` |
| 4 | `device_ip` (IP) | **Mutable** | `10.0.1.50` |

### Rule 2 â€” IP is NEVER the sole identifier
IP addresses change (DHCP, re-addressing, failover). A scan discovering a known MAC or unique hostname at a **new IP** must:
1. Update the existing device record's `device_ip` field.
2. **NOT** create a duplicate device.
3. Log the IP change: `[Identity] Device {id} IP changed: {old} â†’ {new}`.

### Rule 3 â€” Merge, never duplicate
If a scan produces candidates from multiple identity keys (IP match + MAC match pointing to different rows), the system must **merge** into the highest-priority record (monitored > recent > oldest ID) and delete duplicates.

### Rule 4 â€” Hostname matching constraints
Hostname-based matching is only valid when **all** of these are true:
- MAC is missing or invalid (`N/A`, `unknown`, empty)
- Hostname is **not** generic (reject: `Unknown`, `localhost`, `DESKTOP-*`, `WIN-*`, `iPhone`, `android-*`)
- Hostname matches **exactly one** device in the DB (uniqueness check)

### Rule 5 â€” Maintenance mode survives identity changes
`maintenance_mode`, `device_type`, `cos_tier`, and `classification_confidence = "Manual"` must **never** be overwritten by automated scans. These are operator-set fields.

### Rule 6 â€” Global consistency
When a device's IP changes, **all** related records must update:
- `device.device_ip` (primary record)
- `DeviceScanHistory` entries referencing the old IP (update `device_ip` column)
- Active alerts referencing the old IP

---
Update this rulebook whenever repository behavior changes.

## 14. Device Console API + State Machine Contract

### Device Console API Surface (additive)
- `GET /api/devices/<id>/website-policy`
- `POST /api/devices/<id>/website-policy`
- `DELETE /api/devices/<id>/website-policy`
- `GET /api/devices/<id>/alerts`
- `POST /api/devices/<id>/alerts/<event_id>/acknowledge`
- `GET /devices/<id>/policy-history` (redirect to `/tracking/history/<id>?focus=policy`)

### Website Policy Mutation Contract
- Required field: `domain`
- Optional fields: `category`, `reason`
- Response style for this surface: action-style JSON (`success` + payload or `success=false` + `error`)

### Frontend Global Device State
All console indicators must derive from a single canonical object:
```json
{
  "connectivity": "online|degraded|offline",
  "telemetry": "healthy|partial|stale|degraded|critical|offline",
  "policy": "compliant|violations",
  "risk": "low|medium|high"
}
```

### Device Console Quality Gates
- Python coverage on touched backend modules: `>=95%`
- JS coverage on touched/new console modules: `>=95%`
- Unit + integration suites: `100% pass`
- Performance scenarios: `>=95% SLA compliance` and `<=5% error rate`
- Gate runner: `scripts/run_quality_gate.py`

## 13. Subnet Awareness Rules

### Rule 1 â€” Stored, not computed at query time
Every `Device` must have a `subnet_cidr` column (VARCHAR, nullable, indexed). The subnet is stored at write time so dashboard queries group cheaply via `GROUP BY subnet_cidr`.

### Rule 2 â€” Derivation from IP
`subnet_cidr` is derived from `device_ip` using a `/24` prefix (`ipaddress.ip_network(ip/24, strict=False)`). The canonical helper is `compute_subnet_cidr()` in `services/device_identity.py`.

### Rule 3 â€” Write-time population
`subnet_cidr` must be set or updated whenever:
- A new device is created (via `upsert_device_from_identity` or manual save).
- A device's `device_ip` changes.
Do **not** override if a future "manual subnet assignment" mechanism is added.

### Rule 4 â€” Nullable and backward-safe
`subnet_cidr` is nullable. Existing code must tolerate `NULL` values. The dashboard treats `NULL` subnets as an **"Unassigned"** bucket. Never require `subnet_cidr` to be non-null for device creation.

### Rule 5 â€” Online/Offline consistency
Subnet health breakdowns must reuse the **exact same** online/offline logic as the main KPI cards. Do **not** introduce a separate status computation path for subnet grouping.

### Rule 6 â€” Migration idempotency
The migration script (`run_subnet_migration.py`) must be safe to run multiple times: check column existence before adding, only backfill rows where `subnet_cidr IS NULL`.

### Rule 7 â€” Future upgrade path
When upgrading to named subnets (Subnet table with name, CIDR, priority), add a `subnet_id` FK to `Device` (nullable). Assignment fallback: use `subnet_id` if set, else fall back to computed `subnet_cidr`.

## 14. Agent Service Optimization Principles

### Rule 1 â€” Single Connection for High-Frequency SQLite
- The agent tracker (`service.py`) operates at a high frequency (0.5s - 1s loops). Re-opening standard SQLite connections multiple times per loop induces heavy I/O overhead and lock contention. 
- Use a persistent thread-safe SQLite connection (`check_same_thread=False`) configured with `PRAGMA journal_mode=WAL` to ensure smooth metrics writes.

### Rule 2 â€” Strict Thread Pool and Timeout Limits
- Network discovery (`AutoDiscoveryService`) sweeping `/24` subnets can spawn 254+ threads simultaneously.
- Limit discovery thread execution to maximum 20 workers (`max_workers=20`) and set an aggressive socket timeout (`timeout=2`) so failing targets don't hang threads and CPU.

### Rule 3 â€” Caching Expensive OS Invocations
- WMI calls (`get_mac_address()`) to hardware interfaces and hostname DNS resolutions are extremely heavy on the CPU if hit millions of times.
- These static system properties MUST be resolved once during startup into module-level cached constants (`_CACHED_MAC`, `_CACHED_HOSTNAME`, `_CACHED_IP`). They are not expected to change during the execution frame of the service.

### Rule 4 â€” Loop Interval Isolation
- When executing metrics in a single continuous `while True` monitor loop, state variables (`next_net`, `next_core`, `next_window`) must be completely isolated and explicitly evaluated.
- Do not let one subsystem (like window title polling) hijack the interval wait conditions of another overlapping system, or the loops will collide or freeze.

### Rule 5 â€” Efficient Video Array Operations
- In real-time image capture layers (e.g., `ScreenCaptureManager`), perform destructive sizing operations (`cv2.resize`) **before** complex array calculations like color space mapping (`cv2.cvtColor`). Small matrices process colors significantly faster than native screen resolution blocks.

# device_monitoring

## Local LDAP / AD Test Lab

If you do not have Active Directory yet, use the local LDAP lab:
- Compose stack: `docker-compose.ldap-lab.yml`
- Guide: `docs/LDAP_TEST_LAB.md`

## RBAC Dashboard Scope Contract (2026-03-05)

- Dashboard templates receive `rbac_context` from `app.context_processor`.
- Browser global context is emitted in `base.html` as:
  - `window.__RBAC_CONTEXT__ = { role, scope_key, scope_label, capabilities }`
- `/api/dashboard/full_snapshot` now returns:
  - `meta.role`
  - `meta.scope_key`
  - `meta.scope_label`
  - `meta.generated_at_utc`
- Frontend guard (`static/js/dashboard/rbacGuard.js`) compares snapshot `meta` against `window.__RBAC_CONTEXT__` and triggers at most one forced reload on mismatch.
- Dashboard header scope line contract:
  - Admin: `Scope: Global`
  - Manager: `Scope: Site — <name>`
  - Viewer/Operator: `Scope: Department — <name>`
- Sidebar is rendered by capabilities; Files navigation entry is removed from base layout.
- Device live console Files tab/UI is removed; backend file-transfer routes remain intact.

