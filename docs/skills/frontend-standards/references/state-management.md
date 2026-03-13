# State Management

## Goal

Keep UI state deterministic, especially for polling views and bulk-selection modals.

## Core Pattern

1. Maintain explicit source-of-truth state in JS:
- arrays/maps for entities (`sitesData`, `departmentsData`, device lists)
- `Set` for selected IDs
2. Render DOM from state.
3. Update state first on successful API mutation, then re-render.

## Modal Lifecycle Rules

1. On modal open:
- load required datasets (lazy-load allowed)
- initialize current context (`currentAssignSiteId`, `currentAssignDeptId`)
2. On modal close:
- clear transient selection state
- reset local filters/search fields

## Bulk Selection Rules

1. Checkbox remains source of truth for selection.
2. Row click toggles checkbox unless click target is interactive (`input`, `button`, `a`, `label`).
3. Respect disabled rules (locked rows cannot be selected).
4. Keep selection summary synced (`selected / visible`).

## Live Update Rules

1. Avoid full-table replacement during frequent updates when possible.
2. Prefer keyed patching for high-frequency tables.
3. Preserve scroll/focus where feasible.
4. Discard stale responses that complete after a newer request on migrated polling surfaces.
5. If a modal is open during scheduled refresh, defer DOM mutation until the modal closes unless the surface is explicitly exempt.
6. After first hydration, keep visible data mounted while background refresh runs.

## Existing Reference Surfaces

- Bulk assign modal patterns:
  - `templates/departments/list.html`
  - `templates/sites/list.html`
- Dashboard error and render guards:
  - `static/js/dashboard/dashboard.js`

## Checklist

1. Is state held in one clear place per feature?
2. Do render functions read from state only?
3. Do modal open/close handlers reset transient state?
4. Do bulk actions keep backend and UI state in sync?

## Device Console Global State Contract

The device console must derive indicators from a shared canonical object:
```json
{
  "connectivity": "online|degraded|offline",
  "telemetry": "healthy|partial|stale|degraded|critical|offline",
  "policy": "compliant|violations",
  "risk": "low|medium|high"
}
```

## Mutation Locks

Use lock keys for mutating actions to avoid duplicate submissions:
- `policy:add`
- `policy:remove`
- `alert:ack:<event_id>`

Buttons tied to active locks must stay disabled until unlock.

## Cache Keys and Invalidation

Cache keys must be device-scoped:
- `website-policy:<device_id>`
- `alerts:<device_id>`
- `device-summary:<device_id>`
- `risk-score:<device_id>`
- `policy-counter:<device_id>`

Policy mutations must invalidate all keys above.

## Dashboard RBAC Client State (2026-03-05)

- Global RBAC context source of truth:
  - `window.__RBAC_CONTEXT__ = { role, scope_key, scope_label, capabilities }`
- Full snapshot responses echo matching meta fields.
- Client guard (`rbacGuard.js`) enforces role/scope consistency on each snapshot fetch and can trigger one forced refresh when mismatched.
- Scope summary UI line is rendered from RBAC context via `scopeSummary.js`.
