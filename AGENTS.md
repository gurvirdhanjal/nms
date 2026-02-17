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

Note:
- The repository currently contains some hardcoded defaults/credentials in existing files.
- Do not introduce additional hardcoded secrets; prefer migration toward env-based configuration.

## 8. Reporting and Export Rules

These rules apply to report services/routes under `routes/reports.py` and `services/reporting_service.py`.

### Rule 1 - Time-range source selection
- For device health report paths, use:
  - `<= 24h`: `server_health_logs` (raw)
  - `<= 30d`: `server_health_hourly_rollups`
  - `> 30d`: `server_health_daily_rollups`
- Current code status:
  - `get_device_health_report` follows this pattern.
  - `get_operational_report` still aggregates from `ServerHealthLog` directly. Do not copy that pattern for new large-range report queries.

### Rule 2 - Reports are read-only
- Report endpoints/services must not write to the DB.
- Keep report generation as pure read/query logic.

### Rule 3 - Report exports are server-side
- Report exports must be generated server-side via Flask:
  - Route: `/api/reports/<report_type>/export`
  - Implementation: `services/export_service.py` (`csv`, `openpyxl`) + `send_file`
- Do not add browser-generated exports for report endpoints.

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

### Rule 1 — Only WARNING and CRITICAL alerts are persisted
| Severity | Store in DB | Show in Reports | Real-Time Display |
|----------|-------------|-----------------|-------------------|
| CRITICAL | ✅ | ✅ | ✅ |
| WARNING  | ✅ | ✅ | ✅ |
| INFO     | ❌ | ❌ | ✅ (ephemeral) |

Informational alerts (transient ping spikes, minor packet loss) are **noise**.
They MUST NOT be written to `DashboardEvent`. They are shown in real-time SSE only.

### Rule 2 — ICMP alerts require consecutive-scan escalation
Single-scan threshold violations are noise, not alerts.

| Metric | Threshold | Consecutive Scans Required | Severity |
|--------|-----------|---------------------------|----------|
| Latency | ≥ 200ms | 3 | WARNING |
| Packet Loss | ≥ 10% | 3 | WARNING |
| Offline | unreachable | 3 | CRITICAL |

Strike counters: `device.latency_strikes`, `device.packet_loss_strikes`, `device.offline_strikes`

### Rule 3 — Recovery requires consecutive clear scans
To prevent alert flapping, resolving an escalated alert requires `RESOLVE_STRIKES_REQUIRED` (currently 2) consecutive normal scans before the alert is resolved.

### Rule 4 — Server health alerts use separate strikes
Server health (CPU/RAM/Disk) — whether from Agent or SNMP — uses `device.health_alert_strikes` with the same 3-strike pattern. These are independent from ICMP strikes.

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

---
Update this rulebook whenever repository behavior changes.
