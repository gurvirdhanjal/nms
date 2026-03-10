# UI Consistency

## Goal

Keep monitoring UI behavior and visuals coherent across pages, especially under live updates.

## Source Of Truth

Follow `docs/FRONTEND.md` for dashboard-level design and interaction contracts.

## Interaction Rules

1. Use consistent toast/alert presentation for feedback.
2. Use shared modal behaviors:
- deterministic open/close state reset
- predictable button labels/actions
3. Keep table interactions consistent:
- hover + row-click selection for bulk workflows
- disabled rows remain visibly disabled and non-interactive

## Error Surface Rules

1. Route API failures to explicit UI error containers/toasts.
2. Avoid browser `alert(...)` in new/edited operational flows.
3. Keep message tone concise and operational.

## Dashboard-Specific Rules

1. Respect enterprise token hierarchy and status semantics from `docs/FRONTEND.md`.
2. Keep healthy states visually quiet.
3. Avoid disruptive animation on data refresh.
4. Preserve stable layout during polling.

## Accessibility and Usability Baseline

1. Preserve keyboard focus behavior in modals/forms.
2. Ensure buttons with icons retain titles/labels for intent clarity.
3. Keep numeric/status values easy to scan (alignment and formatting consistency).

## Checklist

1. Does this change match existing pattern on sibling pages?
2. Are user feedback and errors shown in a consistent way?
3. Are bulk actions discoverable and reversible?
4. Does the UI remain stable during polling/refresh?

## Device Console Badge + Empty/Error Standards

1. Tab badges (`Processes`, `Website Policy`, `Alerts`) update only from normalized, device-scoped data.
2. Required empty states:
- Policy: `No restricted domains configured`
- Alerts: `No alerts detected`
- Files: `No transfer activity yet`
3. Required error states:
- Policy panel: `Policy data unavailable` + Retry
- Alerts panel: `Alerts failed to load` + Retry
4. Required loading behavior:
- lazy-load tab data on first open
- keep card layout stable while loading/retrying

## RBAC Dashboard UI Consistency (2026-03-05)

- Dashboard header must always show one scope summary line:
  - `Scope: Global`
  - `Scope: Site — <name>`
  - `Scope: Department — <name>`
- Sidebar links are capability-driven; hide unavailable destinations instead of rendering dead links.
- Files navigation is removed from global sidebar.
- Device live console no longer includes Files tab/panel controls.
