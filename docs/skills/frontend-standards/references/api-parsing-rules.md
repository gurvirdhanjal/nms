# API Parsing Rules

## Goal

Prevent frontend crashes from non-JSON responses and make API failures user-actionable.

## Transport Rules

1. Use `credentials: 'same-origin'` for same-site authenticated requests.
2. Set `Content-Type: application/json` only when sending JSON bodies.
3. Use centralized request helper(s) per page/module.

## Parsing Rules

1. Read `content-type` before calling `response.json()`.
2. If response is not JSON, parse as text and raise a controlled error with a short snippet.
3. Treat `!response.ok` as failure even if JSON parse succeeds.
4. Normalize error messages from mixed backend contracts:
- `payload.error`
- `payload.message`
- `payload.error.message`

## Existing Good Patterns

- `requestJson(...)` in `static/js/tracking/device_tracking.js`
- `fetchJsonSafe(...)` in `templates/tracking/live_tracking.html`

## Existing Risk Patterns To Avoid In New Code

- direct `response.json()` without content-type check
- `alert(...)` for transport errors
- silent catch blocks that hide API failures

## Error UX Rules

1. Show user-visible error state (banner/toast/inline status), not console-only failure.
2. Keep error wording short and actionable.
3. Map common statuses:
- `401`: session/auth expired
- `403`: permission denied
- `404`: resource missing/deleted
- `409`: conflict
- `5xx`: backend unavailable or failed

## Checklist

1. Are all fetches in this feature using a safe helper?
2. Are non-JSON responses handled gracefully?
3. Are HTTP error statuses translated into useful UI feedback?

## Device Console Retry Pattern

For policy and alerts tabs:
1. On transport/API failure, render an inline defensive card:
- `Policy data unavailable`
- `Alerts failed to load`
2. Card includes a retry button that re-runs the same endpoint call.
3. Do not leave the panel blank on failure.

## Normalization Rules For `/api/devices/*`

1. Normalize website policy payload to:
- `mode`
- `restrictedDomains[]` with optional metadata
- `violationsToday`
- `recentViolations[]`
2. Normalize alerts payload to:
- `alerts[]` cards
- `activeAlertCount`
- `riskScore`, `riskLevel`
3. Badge counters must derive from normalized values, not raw transport fields.

## Dashboard Snapshot Parsing Rule (2026-03-05)

- Parse and validate `snapshot.meta` against `window.__RBAC_CONTEXT__` before applying snapshot sections.
- On mismatch:
  - perform exactly one forced reload (session-guarded)
  - skip state updates for the mismatched payload.
- Do not silently render stale role/scope data.
