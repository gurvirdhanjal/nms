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

## P2 — Security

### TODO-10: GET export endpoint lacks role-based permission
**What:** `/api/reports/<type>/export` (GET) has no permission check beyond `@require_login`.
Any logged-in viewer can download any report PDF.

**Why:** Report exports may contain sensitive operational data (device health, alerts,
network performance). A viewer role should not have unrestricted export access.

**Where to start:**
- `middleware/rbac.py` — add `reports_bp.export_report` to `ENDPOINT_PERMISSIONS`
  with `reports.export` permission
- Consider which roles should have export access (admin + manager, not viewer)

**Effort:** S
**Priority:** P2
**Depends on:** Nothing — pure security hardening

---

*Updated: 2026-03-16*
