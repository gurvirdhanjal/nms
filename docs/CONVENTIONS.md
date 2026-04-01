# Conventions and Contribution Guidelines

This document outlines the coding standards, architectural patterns, and operational rules for the Device Monitoring Tactical project. It is based on the `AGENTS.md` file and existing codebase practices.

## 1. Coding Standards

### Python
-   **Style Guide:** Follow [PEP 8](https://peps.python.org/pep-0008/).
-   **Imports:** Group imports: Standard library, Third-party, Local application.
-   **Type Hints:** Use type hints for function arguments and return values where possible.
-   **Docstrings:** Provide docstrings for all modules, classes, and public methods.
-   **Async/Await:** Use `asyncio` and `async/await` for network-bound operations (e.g., scanning, monitoring).

### JavaScript
-   **Style:** Use modern JavaScript (ES6+).
-   **Modules:** Prefer ES modules (`import`/`export`) for dashboard code.
-   **Variables:** Use `const` and `let` instead of `var`.
-   **Async:** Use `async/await` with `fetch` for API calls.

## 2. Architecture

### Backend (Flask)
-   **Application Factory:** The app is created using the factory pattern in `app.py` (`create_app`).
-   **Blueprints:** Functionality is modularized using Flask Blueprints in the `routes/` directory.
-   **Services:** Business logic should reside in `services/`, not in route handlers.
-   **Models:** Database models are defined in `models/` using SQLAlchemy.

### Frontend
-   **Templates:** Jinja2 templates are used for server-side rendering (`templates/`).
-   **Static Assets:** CSS, JS, and images are stored in `static/`.
-   **Framework:** Bootstrap 5 is used for styling.
-   **Charts:** Chart.js is used for data visualization.

### Database
-   **ORM:** SQLAlchemy is the primary method for database interaction.
-   **Development:** SQLite (`instance/device_monitoring.db`).
-   **Production:** PostgreSQL (enforced by `REQUIRE_POSTGRES` config).
-   **Migrations:** Database schema changes are handled via `utils/db_migrations.py` and invoked at startup. There is no Alembic integration currently.

## 3. Operational Rules

### Scheduler & Concurrency
-   **Scheduler:** The scheduler (`services/scheduler.py`) runs in a background thread using the `schedule` library.
-   **Crucial Rule:** The scheduler **must never perform network I/O** directly. Its role is to enqueue tasks.
-   **Workers:** Network polling (SNMP, WMI) must be executed by separate worker processes (e.g., `workers/snmp_worker.py`).
-   **Concurrency:** Workers use mechanisms like `SELECT FOR UPDATE SKIP LOCKED` (Postgres) to claim tasks safely.

### Reporting
-   **Read-Only:** Report endpoints must be read-only and never modify database state.
-   **Server-Side Export:** CSV/Excel exports are generated server-side (`services/export_service.py`), not in the browser.
-   **Performance:**
    -   Enforce time-range caps (e.g., max 90 days).
    -   Enforce row limits.
    -   Use appropriate rollups (Hourly/Daily) for long time ranges.

### Alerting
-   **Persistence:** Only **WARNING** and **CRITICAL** alerts are persisted to the database. Info-level events are ephemeral (SSE only).
-   **Escalation:** Alerts typically require consecutive failures (e.g., 3 strikes) to trigger, preventing flapping.
-   **Resolution:** Recovery also requires consecutive successes.

### Device Identity
-   **Hierarchy:** Devices are identified by the following priority:
    1.  `unique_client_id` (UUID) - Most stable.
    2.  `macaddress` (MAC) - Very high stability.
    3.  `hostname` (Unique, non-generic) - High stability.
    4.  `device_ip` (IP) - **Mutable**, least stable.
-   **Rule:** IP address is never the sole identifier. If an IP changes for a known MAC/UUID, update the existing record; do not create a duplicate.

## 4. Security

-   **Secrets:** Do not commit secrets (API keys, passwords) to the repository. Use environment variables.
-   **Authentication:** Protected routes must be decorated with `login_required` or role-based checks (`require_role`).
-   **Agent Auth:** Agent ingestion endpoints (`/api/agent/metrics`) require a valid token.

## 5. Git Workflow

-   **Branching:** Use descriptive branch names (e.g., `feature/add-snmp-v3`, `bugfix/fix-login-error`).
-   **Commits:** Write clear, concise commit messages.
-   **Review:** All changes should be reviewed before merging.

## 6. Test Program and Quality Gate

### Test Layout
- Python unit tests: `tests/unit/**`
- Python integration tests: `tests/integration/**`
- Python performance tests: `tests/performance/**`
- JS tests: `static/js/tracking/console/__tests__/**`

### Pytest Markers
- `unit`
- `integration`
- `performance`

### JS Test Rules
- Use Vitest + jsdom for console modules.
- Keep logic in testable files under `static/js/tracking/console/`.
- Frontend coverage thresholds are enforced in `vitest.config.mjs` at `>=95%`.

### Quality Gate Workflow
Run from repository root:
```bash
python scripts/run_quality_gate.py
```

The gate runs:
1. `pytest -m \"unit or integration\"` with backend module coverage
2. `pytest -m performance`
3. `npm run test:js:coverage`
4. `npm run test:js:perf`

Output summary:
- `artifacts/quality_gate_summary.json`

## RBAC Dashboard Testing Conventions (2026-03-05)

- New Python suites:
  - `tests/unit/rbac/test_ui_capabilities.py`
  - `tests/unit/services/test_scope_resolution.py`
  - `tests/unit/services/test_snapshot_meta_builder.py`
  - `tests/integration/dashboard/*`
  - `tests/integration/templates/*`
  - `tests/integration/routes/test_file_transfer_backend_unchanged.py`
  - `tests/performance/test_dashboard_scope_perf.py`
- New JS suites:
  - `static/js/dashboard/__tests__/rbacGuard.unit.test.js`
  - `static/js/dashboard/__tests__/scopeSummary.dom.integration.test.js`
  - `static/js/tracking/__tests__/deviceLive.filesRemoved.dom.test.js`
- `vitest.config.mjs` includes dashboard and tracking RBAC/removal suites.
- Quality gate runner executes:
  - Python unit+integration coverage run
  - Python performance marker run
  - JS unit run (`npm run test:js`)
  - JS coverage run (`npm run test:js:coverage`)
  - JS perf run (`npm run test:js:perf`)

## 7. Logging Standards

Every Python module must define a module-level logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Use the appropriate level:

| Level | When to use |
|---|---|
| `logger.debug()` | Internal state transitions, polling cycle details, loop progress |
| `logger.info()` | Business events: device added, report generated, alert triggered, scan completed |
| `logger.warning()` | Recoverable anomalies: Redis miss, stale data served, partial SNMP result |
| `logger.error()` | Failed operations: DB write failure, export crash, connection refused |
| `logger.exception()` | Unexpected exceptions in `except` blocks — includes full stack trace |

Rules:
- **Never use `print()` in production code.** Use `logger.*` instead.
- **Never use bare `except:`**.  Use `except Exception:` (or a specific exception type) and log with `logger.exception()` for unexpected failures.
- For expected, silent failures (e.g., network probe misses), `except Exception: pass` is acceptable when the caller handles the None/False return.
- Use `%s` style format strings in logger calls (`logger.info("msg %s", val)`) — not f-strings — so the string is only formatted if the level is active.
