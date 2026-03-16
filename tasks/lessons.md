# Lessons Learned

Patterns and corrections captured from real sessions. Review at session start.

---

## UI / Frontend

### L-001: Empty dropdown = silent broken feature
**Context:** `device_live.html` MORE button had `<ul class="dropdown-menu dropdown-menu-dark"></ul>` — no items.
**Lesson:** An empty `<ul>` renders a Bootstrap dropdown that opens to nothing, appearing as a bug to the user.
**Rule:** Whenever adding a dropdown toggle, always populate at least the minimum set of relevant items in the same commit. Never ship an empty menu.

---

### L-002: Proposed architecture before checking existing routes
**Context:** Plan proposed creating `/reports/device/<id>/inspector` as the replacement for `workstation_monitor.html`. But `reports_bp` is read-only by rule (CLAUDE.md Rule 10), and `device_history` already covered the same data.
**Lesson:** Always grep for existing routes and data overlap before proposing new ones. Ask: "Does this data already exist under a different URL?"
**Rule:** Before creating a new page/route, verify the existing route map. Prefer a redirect (1 line) over a new page (4 files) when the destination covers the same data.

---

### L-003: KPI cards need interactivity by design
**Context:** All numeric KPI cards (CPU, RAM, Uptime, Latency, Security Score) were display-only with no hover or click behaviour.
**Lesson:** In an ops dashboard, operators want context: "Is this CPU reading normal? What's the trend?" Static numbers without drill-down reduce trust in the data.
**Rule:** Any KPI card that shows a live metric must have at minimum a hover affordance (cursor:pointer, teal glow) and a click action (tooltip or modal breakdown). Use `data-kpi-key` + `kpi-clickable` class + `handleKpiBreakdownClick()`.

---

### L-004: Remote view flicker = direct blob URL swap
**Context:** Screenshot refresh wrote new blob URL directly to `<img id="remoteViewImage" src="">`, causing a white-flash between frames because the browser briefly shows no image while decoding.
**Lesson:** Direct `img.src = newBlobUrl` always flickers when the previous image is in place.
**Rule:** Use double-buffering: preload into a hidden back-buffer `<img>` first. Swap to the front buffer only after `onload` fires. CSS: back-buffer at `position:absolute; opacity:0; z-index:0`. This gives zero-flicker frame transitions.

---

### L-005: Fullscreen modal requires flex cascade on parent chain
**Context:** Remote view fullscreen toggled `modal-fullscreen` on `.modal-dialog` but the frame didn't fill the screen because `.modal-content` lacked `display:flex; flex-direction:column` and `.modal-body` lacked `flex: 1 1 auto`.
**Lesson:** Bootstrap's `modal-fullscreen` class sets `height:100%` on `.modal-content` but doesn't force flex layout. Without `flex:1 1 auto` on `.modal-body`, the body doesn't expand to fill the content.
**Rule:** For fullscreen modals with a custom inner frame: `.modal-content { display:flex; flex-direction:column }` + `.modal-body { flex:1 1 auto; overflow:hidden; min-height:0 }` + frame `height:100% !important`.

---

## Backend / API

### L-006: In-memory cache dies on server restart; Redis survives
**Context:** `api_real_time_tracking()` had a 5s in-memory `real_time_data` dict. On server restart, the cache is empty and every page reload shows "Awaiting telemetry" for 5–10 seconds.
**Lesson:** In-memory caches are process-local and ephemeral. Redis caches survive restarts and are shared across workers.
**Rule:** For any frequently-polled endpoint that serves live telemetry, add a Redis SWR layer:
1. Check in-memory first (fastest, same process)
2. Then check Redis (fast, cross-process, survives restart)
3. Then do the live probe
4. After a successful live probe with real data, write to Redis with short TTL (5–10s)
Use key pattern `tracking:realtime:<mac_address>` / TTL 8s. Always wrap Redis ops in `try/except pass`.

---

### L-007: workstation_monitor was an orphan — no inbound links
**Context:** Before deletion, grepped all templates for links to `workstation_monitor`. Found zero. The page was only accessible by direct URL.
**Lesson:** Before deleting any page, always grep templates and JS for inbound links. If zero inbound → safe to redirect/delete without a sweep.
**Rule:** `grep -r "workstation_monitor" templates/` before any page removal. If found, update those links first. Then redirect/delete. Preserve RBAC entry if function stub remains.

---

## Planning / Process

### L-008: Over-engineering a fix with new files
**Context:** Proposed 4-file solution (new route, template, JS, CSS) for what was essentially a "restyle this deprecated page" task. The correct answer was a 1-line redirect.
**Lesson:** CLAUDE.md Rule: "Simplicity First. Make every change as simple as possible. Impact minimal code." When a redirect solves the problem, use it. Don't create new files to justify architectural purity.
**Rule:** Before proposing any new file, ask: "What is the minimal change that achieves the goal?" Count files touched. If > 2 files for a cosmetic/structural refactor, re-examine.

---

### L-009: Always check dropdown HTML before assuming JS bug
**Context:** User reported "dropdown is missing." Investigation showed the Bootstrap toggle button existed and was wired, but the `<ul>` was completely empty — no `<li>` items. This is a template-authoring gap, not a JS bug.
**Lesson:** Empty menu = template gap. Missing menu = JS/init gap. These require different fixes.
**Rule:** When debugging "X is missing" on a UI feature: (1) inspect the HTML first — is the element present but empty? (2) Then check JS bindings. (3) Then check backend data.

---

## CSS Patterns

### L-010: CSS transitions prevent "number jump" flicker on polling
**Context:** Live KPI values updating every 5s felt jarring — numbers just snapped to new values.
**Lesson:** `transition: color 0.25s ease` on value elements makes changes feel smooth rather than abrupt without any JS changes.
**Rule:** All elements that receive live-polled data updates must have `transition: color 0.25s ease` (minimum). For opacity-based transitions, add `.kpi-updating { opacity: 0.55 }` with JS adding/removing the class around render cycles.
