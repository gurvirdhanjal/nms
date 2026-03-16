---
name: device-monitoring-tactical-patterns
description: Coding patterns extracted from device_monitoring_tactical — Flask NMS with blueprints, SQLAlchemy, TimescaleDB, and worker queues
version: 1.0.0
source: local-git-analysis
analyzed_commits: 38
---

# Device Monitoring Tactical — Coding Patterns

Patterns extracted from git history and codebase analysis. Covers architecture
conventions, workflow sequences, auth enforcement, logging, DB access, and
TimescaleDB query routing that appear consistently across this repository.

---

## Commit Conventions

This project uses **conventional commits** with optional scope:

```
feat:           New feature
feat(scope):    Scoped feature (e.g. feat(tracking):, feat(devices):)
fix:            Bug fix
chore:          Maintenance / sync
refactor:       Refactoring (may be capitalised: Refactor:)
docs:           Documentation
test:           Tests
perf:           Performance improvement
```

External AI agent commits use emoji prefixes — **always revert** if they break existing
behaviour (`⚡ Bolt:`, `🛡️ Sentinel:`, `🎨 Palette:` have been reverted multiple times).

---

## Code Architecture

```
routes/          # Flask blueprints — one file per domain (no business logic)
services/        # Business logic — no direct route decisions, no session access
models/          # SQLAlchemy models — one file per domain
workers/         # Standalone background processes (SELECT FOR UPDATE SKIP LOCKED)
middleware/      # Cross-cutting concerns: RBAC, session timeout
utils/           # Startup-invoked DB migrations, helpers
static/js/       # Per-page ES module JS (no bundler)
static/js/tracking/  # Tracking module JS
static/js/dashboard/ # Dashboard module JS
templates/       # Jinja2 templates, matched 1-to-1 with pages
```

---

## Workflows

### Adding a New Feature (full stack)

Files that must co-change:

1. `models/<domain>.py` — define the SQLAlchemy model
2. `utils/db_migrations.py` — add `_ensure_<table>_table()` or `_ensure_<table>_columns()`
3. `services/<domain>_service.py` — business logic, queries
4. `routes/<domain>.py` — blueprint endpoints (auth decorators, calls service)
5. `templates/<domain>.html` — Jinja2 template
6. `static/js/<domain>.js` — page JS (if interactive)
7. `static/css/<domain>.css` — scoped CSS (if needed)

### Adding a New Column to an Existing Table

1. Add column to `models/<domain>.py`
2. Add guard in `utils/db_migrations.py`:
   ```python
   def _ensure_<table>_columns(inspector=None):
       if inspector is None:
           inspector = inspect(db.engine)
       existing = {col['name'] for col in inspector.get_columns('<table>')}
       statements = []
       if 'new_col' not in existing:
           statements.append("ALTER TABLE <table> ADD COLUMN new_col TYPE")
       for stmt in statements:
           db.session.execute(text(stmt))
       db.session.commit()
   ```
3. Call the guard from `run_all_migrations()` in `db_migrations.py`

### Adding a Background Job (Scheduler → Worker)

1. Add method to `services/scheduler.py` that enqueues a `PollTask` — **no network I/O**
2. Register in `JOB_META` dict in `scheduler.py`
3. Add handler in `workers/snmp_worker.py` `_execute_task()` dispatch block
4. Worker runs: `SELECT FOR UPDATE SKIP LOCKED` via raw SQL

---

## Auth Enforcement

**Rule: Auth decorators go on routes, never in services.**

```python
from middleware.rbac import require_login, require_role, require_permission

# Read-only endpoints:
@require_login
def my_view():
    ...

# Admin-only:
@require_role('admin')
def admin_action():
    ...

# Permission-scoped (preferred):
@require_permission('devices.edit')
def edit_device():
    ...
```

**Never** use `session.get('role')` inline checks in route or service code.

Permission strings follow `domain.action` format — defined in `ROLE_PERMISSIONS`
in `middleware/rbac.py`. Current domains: `dashboard`, `reports`, `devices`,
`monitoring`, `scanning`, `tracking`, `snmp`, `server_metrics`, `service_checks`,
`file_transfer`, `maintenance`, `users`, `sites`, `departments`, `subnets`.

---

## Logging

**Rule: Never use `print()` in routes or services. Always use `logging`.**

```python
import logging
logger = logging.getLogger(__name__)   # module-level, always

# Usage:
logger.debug("detail for developers")
logger.info("Device %s added", device_id)
logger.warning("Threshold exceeded: %s", value)
logger.error("Failed to process: %s", exc)
logger.exception("Unhandled error in route")   # includes traceback
```

---

## Database Access Patterns

### Standard SQLAlchemy session usage

```python
try:
    device = db.session.query(Device).filter_by(device_id=device_id).first()
    db.session.add(new_obj)
    db.session.commit()
except Exception as e:
    db.session.rollback()
    logger.exception("DB error: %s", e)
    return jsonify({'error': str(e)}), 500
```

### JSON error response shape (API routes)

```python
def _json_error_response(*, code, message, status):
    return jsonify({
        'error': {'code': code, 'message': message},
        'meta': {},
    }), status
```

### Reports are read-only

Report endpoints (under `routes/reports.py`) **must never write to DB**.
All export jobs are enqueued via `ReportExportJob` — they never block a request.

---

## Identity Resolution

**Device identity hierarchy: UUID > MAC > hostname > IP**

IP is **never** the sole device identifier. Always use:
```python
from services.device_identity import upsert_device_from_identity
```

When querying tracked devices, prefer MAC address or UUID. IP may change —
`tracked_device_ip_change.py` handles IP migration for tracked devices.

---

## Redis Caching Conventions

Redis writes are **always best-effort** — never let a Redis failure block a response:

```python
from extensions import redis_client

def _try_redis_write(key, value, ttl):
    try:
        if not is_redis_available():
            return
        redis_client.setex(key, ttl, value)
    except Exception:
        pass   # best-effort only
```

Key namespaces and TTLs:

| Key Pattern | TTL | Purpose |
|---|---|---|
| `tracking:realtime:<mac>` | 8s | Live probe result (SWR anti-flicker) |
| `tracking:discovery-probe:ip:<ip>` | 120s | Tracking probe results |
| `tracking:agent-port:ip:<ip>` | 43200s | Agent port mapping (12h) |
| `dashboard:*` | varies | Dashboard cache (invalidate via `dashboard_cache_service`) |

---

## TimescaleDB Query Routing

```
≤ 24h  → raw logs        (server_health_logs, tracking_samples, etc.)
≤ 30d  → *_hourly_cagg   (server_health_hourly_cagg, tracking_hourly_cagg)
> 30d  → *_daily_cagg    (server_health_daily_cagg, server_health_daily_extended_cagg)
```

**Hypertables:** `server_health_logs`, `tracking_samples`, `device_resource_logs`,
`device_activity_logs`, `device_application_logs`

**Continuous Aggregates:** `server_health_hourly_cagg`, `server_health_daily_cagg`,
`server_health_daily_extended_cagg`, `tracking_hourly_cagg`

Always check `DeviceScanHistory.status` with `func.lower()` — stored as `"Online"` (capital O):
```python
func.lower(DeviceScanHistory.status) == "online"
```

---

## Worker Concurrency Pattern

Workers use PostgreSQL's `SELECT FOR UPDATE SKIP LOCKED` — never SQLAlchemy ORM for this:

```python
# Raw SQL required — ORM doesn't expose SKIP LOCKED easily
result = db.session.execute(text("""
    SELECT id FROM poll_tasks
    WHERE status = 'pending'
    ORDER BY created_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED
"""))
```

Workers are **standalone processes** — they create their own minimal Flask app:
```python
def create_worker_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    return app
```

---

## Frontend Patterns

### JS module structure

Each page has a dedicated JS file (`static/js/<page>.js`).
No bundler — files are loaded via `<script src="...">` in templates.
Tracking module has the densest JS: `device_live.js`, `device_tracking.js`, `live_fleet.js`.

### Anti-flicker pattern for live data

```javascript
// Mark element as updating during in-flight requests
el.classList.add('kpi-updating');   // CSS: opacity 0.55
// ... fetch ...
el.classList.remove('kpi-updating');
```

### Double-buffer for screenshot refreshes

Load new image blob into back-buffer (`#remoteViewImageBack`), swap to front-buffer
(`#remoteViewImage`) only after `onload` fires — avoids white-flash flicker.

### KPI card interaction pattern

All `.kpi-clickable` and `.kpi-stat-wrap` elements use `data-kpi-key` to open
`#kpiBreakdownModal` via `handleKpiBreakdownClick()`. Never break this contract.

### `patchTableRows()` / `setTableStatusRow()` pattern

All tables use `patchTableRows()` for keyed DOM diffing (no full re-render on refresh).
Loading state uses `setTableStatusRow()` — if the table already has data rows, overlay
with `.table-refreshing` CSS class instead of wiping content.

---

## CSS Conventions

Base design system: `static/css/tactical.css` (NMS Design System v5.0 tokens).

Per-page CSS files: `static/css/tracking/device_live.css`, etc. — always scoped.

UI primitives: `templates/components/ui_primitives_assets.html` — additive only,
load on touched surfaces, not globally.

Dark theme always: background `#0a0f1a` / `#111827`, accent teal `#00ff88`.

---

## Testing

```bash
# Python — unit tests only (fast, no DB)
pytest tests/ -m unit

# Python — integration tests (requires DB)
pytest tests/ -m integration

# Python — all tests
pytest tests/

# JS — Vitest unit tests
npm run test:js
npm run test:js:coverage   # with v8 coverage

# Layout: tests/unit/<module>/test_*.py
#         tests/integration/<module>/test_*.py
```

---

## Security Checklist (every new route)

- [ ] `@require_login` or `@require_permission` on every non-public endpoint
- [ ] No `session.get('role')` inline
- [ ] Input validated at boundaries (user input, external APIs)
- [ ] No secrets in responses or logs
- [ ] Report endpoints are read-only (no DB writes)
- [ ] SNMP community string goes through `Device.snmp_community` property (Fernet encrypted)
