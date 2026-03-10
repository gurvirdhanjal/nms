# Error Handling

## Goal

Return actionable errors to callers while keeping DB/session state safe and logs debuggable.

## Patterns Already In Use

- JSON helpers:
  - `_json_error(...)` in `routes/reports.py`
  - `_json_error(...)`, `_json_exception(...)` in `routes/tracking.py`
  - `_json_error_response(...)` in `routes/devices.py`
- Transaction safety:
  - `db.session.rollback()` in exception paths across routes/services
- Concurrency-specific handling:
  - `ObjectDeletedError`, `StaleDataError` handling in `routes/agent.py`

## Required Practices

1. Differentiate expected validation/business errors from unexpected failures.
2. Return deterministic `4xx` payloads for expected client mistakes.
3. On write failures:
- call `db.session.rollback()`
- return controlled JSON error
- log context with route + resource identifiers
4. Use narrow exceptions where practical.
5. Do not swallow exceptions silently (`except: pass`) in request paths.

## API vs Page Error Behavior

- `/api/*`: always return JSON error payloads.
- page routes: redirect/flash is acceptable.
- if route serves both contexts, branch by request path/accept header and keep behavior explicit.

## Logging During Errors

1. Use `logger.exception(...)` when stack trace is useful.
2. Use `logger.warning(...)` for recoverable issues.
3. Never include secrets in log message arguments.
4. Include stable context keys (`device_id`, `site_id`, `scan_id`, `job_id`) for triage.

## Practical Do/Don’t

Do:
- Validate payload shape before business logic.
- Map known dependency failures to stable error codes for frontend handling.
- Use helper functions to keep envelope consistency.

Don’t:
- Return raw Python exception text to clients for internal failures.
- Mix HTML and JSON responses on the same API endpoint.
- Continue using a dirty SQLAlchemy session after an exception without rollback.

## Device Console Error Guarantees

1. Device console API endpoints under `/api/devices/*` always return JSON on failures.
2. Mutation endpoints (`POST/DELETE/ack`) must rollback DB session before returning `5xx`.
3. Frontend defensive retry contract expects:
- `success: false`
- `error` string in the response body
4. Policy and Alerts UI surfaces must support retry buttons that safely re-run the same request.

## Dashboard RBAC Error Handling (2026-03-05)

- Snapshot meta mismatch (`snapshot.meta` vs `window.__RBAC_CONTEXT__`) triggers one forced page reload guarded by session storage key `dashboard:rbac-refresh-once`.
- Guard must not loop indefinitely; second mismatch in same session does not reload again.
- Policy/alerts fetch failures in device console must render defensive error cards with retry actions, not blank panels.
- API failures must preserve JSON error shape (`error` or `success=false`) to support retry UI rendering.
