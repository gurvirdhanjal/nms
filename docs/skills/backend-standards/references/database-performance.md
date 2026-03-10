# Database Performance

## Goal

Keep monitoring/reporting queries fast under growth and prevent write contention.

## Existing Performance Building Blocks

- Engine tuning in `config.py` and `app.py`:
  - SQLite `check_same_thread=False`, timeout, WAL/NORMAL pragmas
  - `pool_pre_ping=True`
- Runtime migration/index helpers in `utils/db_migrations.py`
- Report safeguards in `routes/reports.py`:
  - max range caps
  - row limits
  - cache TTL
  - rate limits
  - statement timeout (Postgres)
- Rollup retention model for server health metrics.

## Query Rules

1. Filter first, aggregate second.
2. Use indexed columns in filters (`device_id`, `timestamp`, `site_id`, `department_id`, `subnet_cidr`).
3. Add server-side pagination for unbounded lists.
4. For historical metrics/reports, use rollup tables for longer windows.
5. Avoid N+1 loops over relationships in API endpoints.

## Write Path Rules

1. Batch writes where possible.
2. Keep one transaction boundary per request/job unit.
3. Roll back on any failure before reusing session.
4. For concurrent workers, use claim-and-lock patterns (`FOR UPDATE SKIP LOCKED`) where supported.

## Index and Schema Change Rules

1. Add DB schema changes via `utils/db_migrations.py` and model updates together.
2. Prefer `CREATE INDEX IF NOT EXISTS` style for idempotent migrations.
3. Backfill only what is needed; avoid full-table rewrites in request paths.
4. Keep nullable defaults when introducing new columns to avoid deploy-time breakage.

## SQLite and Postgres Notes

- SQLite is acceptable for local/dev but has write-lock constraints.
- High-ingest or worker-heavy workloads should run on PostgreSQL.
- Respect `REQUIRE_POSTGRES` / `REQUIRE_POSTGRES_ONLY` settings where configured.

## PR Checklist

1. Is every new query bounded by filters/time window/limit?
2. Is there an index for the dominant filter/sort path?
3. Is large-range report logic routed to rollups?
4. Is transaction + rollback behavior explicit?

## Device Console Performance Budgets

Performance scenarios are validated in `tests/performance/test_device_console_api_perf.py`.

- `GET /api/devices/<id>/website-policy`: SLA target `<=350ms` for 95% of successful requests.
- `GET /api/devices/<id>/alerts`: SLA target `<=350ms` for 95% of successful requests.
- Mixed `POST/GET/DELETE` policy workflow: SLA target `<=450ms` for 95% of successful requests.
- Error rate target per scenario: `<=5%`.

## Index Notes For Device Console Tables

1. `restricted_site_domain_meta` should remain indexed by:
- `(device_id, domain)` unique
- `(device_id, updated_at)` for recent policy reads
2. Existing restricted-site event/state indexes continue to serve alerts/risk derivation endpoints.

## Scope-Aware Cache and SLA Notes (2026-03-05)

- Dashboard cache keys must be scope-qualified using role + scope key (example: `summary:manager__site:1`).
- Snapshot lock/cache keys must include scope fragment to prevent cross-scope cache bleed.
- Added performance scenarios validate >=95% compliance and <=5% error rate for scoped dashboard endpoints:
  - `GET /api/dashboard/summary`
  - `GET /api/dashboard/full_snapshot`
  - `GET /api/dashboard/alerts`
