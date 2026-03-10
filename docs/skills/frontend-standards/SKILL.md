---
name: frontend-standards
description: Apply frontend engineering standards for this repository’s Jinja + Vanilla JS UI. Use when changing `templates/**/*.html`, `static/js/**/*.js`, or dashboard/tracking UI behavior involving API parsing, client state management, and UI consistency.
---

# Frontend Standards

## Overview

Use this skill to make frontend changes safe under live monitoring conditions and consistent across pages. Prioritize strict API parsing, predictable state updates, and shared UI behavior patterns.

## Workflow

1. Identify the surface:
- dashboard modules (`static/js/dashboard/*`)
- page-level scripts in templates
- tracking scripts (`static/js/tracking/*`)
2. Load only the relevant reference file(s) in `references/`.
3. Apply standards for parsing, state, and UI behavior.
4. Verify:
- API failures render controlled UI errors
- bulk selection and modal behavior stay deterministic
- live updates avoid destructive full re-render churn

## Reference Map

- API parsing and transport rules: `references/api-parsing-rules.md`
- Client state and render lifecycle: `references/state-management.md`
- Visual and interaction consistency: `references/ui-consistency.md`

## Hard Rules

1. Include `credentials: 'same-origin'` on same-site fetches.
2. Verify `content-type` before `response.json()` for new fetch wrappers.
3. Handle HTTP non-OK statuses with user-visible messages, not silent failures.
4. Avoid browser `alert()` in new code; use toasts/banner patterns.
5. Keep one source-of-truth state object/array per page feature and render from it.
6. For live tables, patch rows by key instead of replacing full `tbody` each refresh.

## Completion Checklist

1. Confirm fetch paths provide deterministic success and error handling.
2. Confirm modal open/close lifecycle resets temporary selection state.
3. Confirm bulk actions update both UI state and backend state coherently.
4. Confirm style tokens and dashboard hierarchy follow `docs/FRONTEND.md`.
5. Confirm keyboard/mouse interactions are preserved (row-click select, disabled state guards).
