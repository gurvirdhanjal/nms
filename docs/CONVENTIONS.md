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
