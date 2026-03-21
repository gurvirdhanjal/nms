# TODOS — Deferred Work

Items from the Behavioral Intelligence plan review (SCOPE EXPANSION mode, 2026-03-16).
Each item was explicitly deferred; "Build Now" items are already implemented.

---

## P2 — Code Quality

### TODO-7: DRY up APP_CATEGORIES dict
**What:** Remove the duplicate `APP_CATEGORIES` dict from `services/reporting_service.py`
(or wherever else it exists) and import from `services/app_classifier.py` instead.

**Why:** Two sources of truth for the same hardcoded mapping — they will drift.
`app_classifier.py` is now the canonical source; `reporting_service.py` should import from it.

**Where to start:**
- `services/app_classifier.py` — APP_CATEGORIES dict (canonical)
- Search for `APP_CATEGORIES` in `services/reporting_service.py` and any other file
- Replace with `from services.app_classifier import APP_CATEGORIES`

**Effort:** S
**Priority:** P2
**Depends on:** Nothing — pure cleanup

---

## P2 — Observability / Admin UX

### DELIGHT-1: Fleet productivity trend sparklines in Workstation Health tab
**What:** Add a 7-day sparkline next to each device's Productivity Score in the workstation
health table (reports.html). Show trend direction (up/down) alongside the current score.

**Why:** A single score number hides whether the device is trending better or worse.
A 3-column sparkline (3 data points = 3 day buckets) makes the trend legible at a glance
without adding a modal.

**Where to start:**
- `services/enterprise_report_service.py` — `_workstation_behavioral_metrics()`: add
  `productivity_trend: list[float]` (last 7 daily buckets) from `TrackingDailyRollup`
- `templates/reports.html` — render sparkline SVG inline in the Prod. column

**Effort:** M
**Priority:** P2
**Depends on:** Behavioral metrics (Phase 1-2) already shipped

---

### DELIGHT-2: Productivity score tooltip explaining score calculation
**What:** On hover over the productivity or focus score values (both in reports table and
device live view), show a tooltip explaining the formula: which apps were counted, their
categories and weights, and the resulting calculation.

**Why:** Scores feel like black boxes. A brief breakdown ("chrome.exe: Browser×0.5 = 30min
→ 25 pts") builds trust with managers reviewing the data.

**Where to start:**
- `routes/tracking.py` — `/behavioral-summary` endpoint: add `score_breakdown` list to response
  (each item: `{app, category, weight, duration_s, weighted_pts}`)
- `static/js/tracking/device_live.js` — render breakdown in a popover or tooltip on the score card
- `templates/reports.html` — same for the table column tooltip

**Effort:** M
**Priority:** P2
**Depends on:** `_workstation_behavioral_metrics()` already exposes app_rows logic

---

### DELIGHT-3: Zero-state empty panel for violations (no events message)
**What:** When `recent_violations` is empty in the device live view, show a styled
"No policy violations in the last 24h" panel with a green shield icon rather than
a blank card.

**Why:** Blank cards look like loading failures. A positive confirmation ("all clear")
reduces admin anxiety and makes the monitoring feel deliberate.

**Where to start:**
- `static/js/tracking/device_live.js` — `_renderViolationsPanel()`: add empty-state branch
- `templates/tracking/device_live.html` — violations panel HTML: add `#violationsEmpty` element

**Effort:** S
**Priority:** P2
**Depends on:** Violations panel (Phase 5C) already shipped

---

### DELIGHT-4: "App usage today" export button on device live view
**What:** Add a small CSV download button to the "App Usage Today" card on the device live
view. Exports: app_name, category, duration_minutes, pct_of_active_time for today.

**Why:** Managers reviewing a specific device often want to share the app breakdown
in a report or email without navigating to the Reports tab and running a full export.

**Where to start:**
- `routes/tracking.py` — add `GET /api/tracking/workstation/<id>/app-usage-csv` that
  queries `DeviceApplicationLog` for today and streams a CSV response
- `templates/tracking/device_live.html` — add download button to app donut card header
- `static/js/tracking/device_live.js` — wire button to trigger the download URL

**Effort:** S
**Priority:** P3
**Depends on:** `/behavioral-summary` endpoint already ships top_apps data

---

### DELIGHT-5: Admin UI for app category overrides
**What:** Add an admin page (or section of the existing settings/discovery_settings page)
where admins can see the `app_category_cache` table and override individual app categories.

**Why:** The Claude API will occasionally misclassify niche apps. Admins need a way to
correct `spotify.exe → Entertainment` to `spotify.exe → Utility` without touching the DB
directly.

**Where to start:**
- New route in `routes/` (or extend `device_console.py`) for `GET/POST /admin/app-categories`
- Template: table of all rows in `AppCategoryCache` with inline edit for `category` column
- `AppCategoryCache.source` should be updated to `'admin_override'` on manual edit

**Effort:** M
**Priority:** P2
**Depends on:** `AppCategoryCache` model and `services/app_classifier.py` already exist

---

## P3 — Future Investigation

### DELIGHT-2b: Peer comparison / team productivity benchmarks
**What:** Show how a device's productivity score compares to the department average.
Example: "72 / 100 — 8 pts above dept avg".

**Why:** Absolute scores are hard to interpret without context. Relative scores enable
managers to spot outliers in either direction.

**Note:** Requires baseline data across multiple devices over multiple days before the
comparison is meaningful. Deferred until sufficient data has accumulated.

**Effort:** M
**Priority:** P3
**Depends on:** Behavioral metrics shipped + 2+ weeks of production data

---

### TODO-8: Add "Hours Opened" column to website violation tables
**What:** Add estimated viewing duration column to the violation tables in both
Workstation Monitoring tab and Device Inspector. Use agent's window_title events
× window_poll_seconds for estimation.

**Why:** Currently only violation count + website name are shown. Duration data
tells admins how much time was actually spent on restricted sites.

**Where to start:**
- `services/enterprise_report_service.py` — `_fleet_violation_summary()`: add
  `sum(case(source='window_title', 1, else_=0))` column, multiply by
  `RestrictedSitePolicy.window_poll_seconds / 3600`
- `routes/reports.py` — `_device_violation_breakdown()`: same pattern for single device
- `templates/reports.html` — add "Hours Opened" column to both tables
- Consider: show "—" when only DNS events exist (no duration data)

**Effort:** S
**Priority:** P3
**Depends on:** Website violations in reports (shipped 2026-03-21), optionally
agent-side enhancement to track explicit foreground duration per domain

---

## P2 — Security

### TODO-10: GET export endpoint lacks role-based permission ✅ DONE
**Status:** Fixed in `feat/reports-module-refactor` (2026-03-16).
`@require_permission('reports.export')` applied to `export_report()`, `create_export_job()`,
and `enterprise_uptime_pdf()` in `routes/reports.py`. Maps to admin + manager via `ROLE_PERMISSIONS`.

---

## P3 — Code Cleanup

### TODO-11: Remove dead CSV/XLSX export code from export_service.py
**What:** PDF-only decision made 2026-03-16 for the reports flow. CSV/XLSX generation
functions in `services/export_service.py` are now unreachable from the reports route
(all exports go through `enterprise_pdf_service.py` or the PDF builder pipeline).

**Why:** ~400 LOC of dead code (openpyxl-based XLSX builders, CSV stream functions)
that will rot without test coverage since reports are PDF-only. Either remove entirely
or mark as internal-only utility if other code paths still use them.

**Where to start:**
- `services/export_service.py` — identify which functions are still called from anywhere
  (grep for `export_to_xlsx`, `export_to_csv` across the codebase)
- Remove unreachable functions and their openpyxl imports
- Run `pytest tests/` to verify nothing breaks

**Effort:** S
**Priority:** P3
**Depends on:** Nothing — pure dead code cleanup

---

## P3 — Frontend Migration

### TODO-12: Migrate remaining --mo-* inline styles in device_live.html
**What:** Move 13+ inline `style="..."` attributes (lines 57-137) from `device_live.html` to CSS
classes in `device_live.css`, replacing `--mo-*` tokens with `--e-*` enterprise tokens.

**Why:** `--mo-*` tokens are legacy (minimal ops system). They resolve today via `tactical.css:117`
but should migrate to `--e-*` per v5.0 spec. Inline styles also prevent CSP `style-src` tightening.

**Where to start:**
- `templates/tracking/device_live.html` — lines 57, 83, 87, 96, 98, 100, 106, 115, 126, 128, 130, 133, 135, 137
- `static/css/tracking/device_live.css` — add class rules mapping to `--e-*` tokens
- `static/css/tactical.css:117-124` — `--mo-*` definitions (can be removed after full migration)

**Effort:** M
**Priority:** P3
**Depends on:** Visual refinement PR (ships Fix 6 as partial progress)

---

### TODO-13: Jinja2 macro for repeated KPI card structure in dashboard.html
**What:** `dashboard.html` repeats the KPI card HTML pattern 5 times (lines ~2238–2280). Convert
to a `{% macro kpi_card(id, label, icon) %}` macro.

**Why:** Reduce template size by ~120 lines. Any future KPI card change requires editing 5 places today.

**Where to start:**
- `templates/dashboard.html` — KPI card blocks (5× near line 2238)
- Extract to `templates/macros/dashboard_macros.html` (new)

**Effort:** S | **Priority:** P4

---

### TODO-14: Bootstrap grid vs custom CSS grid unification on #device-kpi-row
**What:** `dashboard.html` applies both Bootstrap `row-cols-lg-5` and a custom CSS Grid override
(`repeat(6, minmax(0, 1fr))`) to `#device-kpi-row`. Currently works but fragile.

**Why:** Two layout systems on the same element will conflict if Bootstrap updates or a new KPI card is added.

**Where to start:**
- `templates/dashboard.html` — `#device-kpi-row` (line ~2237)
- `templates/dashboard.html` `{% block extra_css %}` — `#device-kpi-row` grid rule

**Effort:** M | **Priority:** P4

---

### TODO-15: Move dashboard.html extra_css <style> block to static/css/dashboard.css
**What:** The 2100+ line `<style>` block inside `{% block extra_css %}` in `dashboard.html`
should become a cached external CSS file `static/css/dashboard.css`.

**Why:** Browser cannot cache inline styles. Large inline style block adds ~38KB to HTML response
on every page load.

**Where to start:**
- `templates/dashboard.html` — lines 7–2170 (`{% block extra_css %}`)
- Create `static/css/dashboard.css`, link it via `{% block extra_css %}` `<link>` tag

**Effort:** M | **Priority:** P3

---

---

## P2 — API / Safety

### TODO-API-1: Fix api_live_alerts() — live network scans in route handler
**What:** `api_live_alerts()` in `routes/tracking.py` (~line 5862) calls
`TrackedDevice.query.filter(...).all()` (no limit) then fires a synchronous
ICMP/TCP scan for every device IP in the request thread.

**Why:** Synchronous multi-device network I/O in a Flask request handler blocks
the WSGI worker thread for seconds. At 239 devices with a 2.5s scan timeout,
this request can take up to 10 minutes and exhaust the Waitress thread pool.
This is worse than the live-summary OOM risk addressed in the API Optimization sprint.

**How to fix:**
1. Move scan logic to a background task (enqueue via `poll_tasks`).
2. Return cached/last-known alert state from Redis or DB instead of live-probing.
3. Alternatively, gate with a strict `?device_id=` single-device filter (no batch scanning).

**Where to start:** `routes/tracking.py` line ~5862 — `api_live_alerts()` function.

**Effort:** M | **Priority:** P2
**Depends on:** Nothing

---

### TODO-API-2: Remove POST from toggle routes after PATCH migration
**What:** Six toggle routes currently accept both `POST` and `PATCH`
(`['POST', 'PATCH']` methods list). Once all callers are confirmed to use PATCH,
remove `POST` from the methods list.

**Why:** Keeping POST indefinitely defeats the REST correctness improvement. The
transition period exists to avoid breaking clients — it should not become permanent.

**Routes to update (remove POST):**
- `POST /api/devices/<id>/toggle_monitoring` (`routes/devices.py`)
- `POST /api/devices/<id>/update_type` (`routes/devices.py`)
- `POST /api/devices/<id>/reassign-site` (`routes/devices.py`)
- `POST /api/tracking/toggle-mic/<mac>` (`routes/tracking.py`)
- `POST /api/tracking/toggle-camera/<mac>` (`routes/tracking.py`)

**Frontend callers already migrated to PATCH:**
- `static/js/tracking/device_live.js` (mic/camera)
- `static/js/dashboard/tables/inventoryTable.js` (toggle_monitoring)
- `templates/devices.html` (update_type)

**Effort:** S | **Priority:** P3
**Depends on:** Confirm no other callers still use POST (grep codebase first)

---

*Updated: 2026-03-20*
