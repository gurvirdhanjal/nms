# Dashboard UX Polish — Scroll, Column Access, Tab Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix inaccessible table scroll (rows and columns), replace pill tabs with underline tabs, add ARIA, and add an expand toggle only on the Alerts table.

**Architecture:** Two files change — `templates/dashboard.html` for all CSS/HTML and `static/js/dashboard/dashboard.js` for JS. The primary scroll breakage is a single 9-line `dashboard-flow` CSS block that uses `!important` to kill every scroll container; deleting it is Task 1. Tasks 2–9 are independent polish items that can be verified individually.

**Tech Stack:** Jinja2 HTML template, vanilla CSS (inline `<style>` blocks inside the template), vanilla JS (module-style functions in dashboard.js)

---

### Task 1: Delete the `dashboard-flow` scroll-kill block

This is the root cause. A CSS block uses `!important` to set `overflow: visible` and `max-height: none` on every table container, preventing both vertical and horizontal scrollbars.

**Files:**
- Modify: `templates/dashboard.html` (~line 3115)

- [ ] **Step 1: Locate the block**

Open `templates/dashboard.html` and find these exact 9 lines (around line 3115):

```css
    .dashboard-flow #device-breakdown .breakdown-left .table-responsive,
    .dashboard-flow #device-breakdown .breakdown-right .table-responsive,
    .dashboard-flow #device-breakdown .service-impact-row .table-responsive,
    .dashboard-flow #server-health-detail .table-responsive,
    .dashboard-flow .network-segments-panel .subnet-table-scroll {
        max-height: none !important;
        overflow: visible !important;
        overscroll-behavior: auto;
    }
```

- [ ] **Step 2: Delete those 9 lines**

Remove the entire rule set. The surrounding rules (spacing, typography) are untouched.

- [ ] **Step 3: Verify in browser**

Start the server (`python service.py` or equivalent), open `http://127.0.0.1:5000/dashboard`, expand the Network Intelligence panel, hover over a table — scrollbars should now appear when content overflows.

- [ ] **Step 4: Commit**

```
git add templates/dashboard.html
git commit -m "fix(dashboard): remove dashboard-flow scroll-kill override"
```

---

### Task 2: Fix `#device-breakdown` secondary overflow clipping

Even with Task 1 done, the panel's collapse animation uses `overflow: hidden` and `.is-active` never overrides it. Also `transform: translateY(0)` triggers a Firefox quirk that demotes `overflow: visible` to `overflow: clip`. Fix: change the transform to `none` (visually identical) and use a JS `transitionend` listener to set overflow after animation.

**Files:**
- Modify: `templates/dashboard.html` (second `<style>` block, ~line 2456)
- Modify: `static/js/dashboard/dashboard.js` (functions at lines 822 and 846)

- [ ] **Step 1: Change `transform: translateY(0)` to `transform: none` in CSS**

Find `#device-breakdown.is-active` (~line 2456) in the second `<style>` block and change one line:

```css
/* BEFORE */
#device-breakdown.is-active {
    max-height: 2800px;
    opacity: 1;
    transform: translateY(0);
    margin-top: 0.65rem;
    pointer-events: auto;
}

/* AFTER */
#device-breakdown.is-active {
    max-height: 2800px;
    opacity: 1;
    transform: none;
    margin-top: 0.65rem;
    pointer-events: auto;
}
```

- [ ] **Step 2: Add transitionend listener in `openDeviceBreakdown`**

In `static/js/dashboard/dashboard.js`, find `openDeviceBreakdown()` at line 822. After `el.classList.add('is-active')` (line 827), add the listener immediately:

```js
function openDeviceBreakdown(sourceCard = null) {
    const el = document.getElementById('device-breakdown');
    if (!el) return;

    const wasActive = el.classList.contains('is-active');
    el.classList.add('is-active');

    // ADD: unclip inner scrollbars after expand animation completes
    el.addEventListener('transitionend', function onExpand(e) {
        if (e.propertyName === 'max-height' && el.classList.contains('is-active')) {
            el.style.overflow = 'visible';
        }
    }, { once: true });

    if (sourceCard) {
        // ... rest of function unchanged
```

- [ ] **Step 3: Reset overflow in `closeDeviceBreakdown` before animation**

Find `closeDeviceBreakdown()` at line 846. Add one line before `el.classList.remove('is-active')`:

```js
function closeDeviceBreakdown() {
    const el = document.getElementById('device-breakdown');
    if (!el) return;
    el.style.overflow = 'hidden';      // ADD: re-clip before collapse animation
    el.classList.remove('is-active');
    setBreakdownActiveCard(null);
}
```

- [ ] **Step 4: Verify in browser**

Expand the panel → wait for animation to finish → both row scroll and column scroll should work. Collapse → animation plays cleanly, content disappears. Re-expand → scroll still works.

- [ ] **Step 5: Commit**

```
git add templates/dashboard.html static/js/dashboard/dashboard.js
git commit -m "fix(dashboard): fix overflow clipping on device-breakdown expand/collapse"
```

---

### Task 3: Fix subnet table columns + add touch scroll to all containers

**Files:**
- Modify: `templates/dashboard.html` (~line 1028, ~line 2964)

- [ ] **Step 1: Fix subnet scroll — add x-axis and touch**

Find `.dashboard-enterprise .network-segments-panel .subnet-table-scroll` (~line 1028). Change `overflow-y: auto` to `overflow: auto` and add the touch line:

```css
/* BEFORE */
.dashboard-enterprise .network-segments-panel .subnet-table-scroll {
    max-height: 340px;
    overflow-y: auto;
    overscroll-behavior: contain;
}

/* AFTER */
.dashboard-enterprise .network-segments-panel .subnet-table-scroll {
    max-height: 340px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    overscroll-behavior: contain;
}
```

- [ ] **Step 2: Add touch support to breakdown table-responsive**

Find `#device-breakdown .table-responsive` in the second `<style>` block (~line 2964). Add one property:

```css
#device-breakdown .table-responsive {
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 8px;
    background: rgba(11, 15, 21, 0.5);
    overflow: auto;
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
    -webkit-overflow-scrolling: touch;    /* ADD */
}
```

- [ ] **Step 3: Verify in browser**

In Chrome DevTools, enable device emulation (any phone). Scroll the subnet table and the breakdown tables — scrolling should feel native with momentum.

- [ ] **Step 4: Commit**

```
git add templates/dashboard.html
git commit -m "fix(dashboard): restore subnet x-axis scroll and add touch momentum to tables"
```

---

### Task 4: Add max-height + scroll to Server Health table

The `.fleet-table-card .table-responsive` has no height constraint so its scrollbar never activates.

**Files:**
- Modify: `templates/dashboard.html` (find `.fleet-table-card` styles, ~line 404)

- [ ] **Step 1: Add the rule**

Find `.fleet-table-card .card-body` (~line 412). Add a new rule immediately after:

```css
.fleet-table-card .table-responsive {
    max-height: 260px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
}
```

- [ ] **Step 2: Verify in browser**

The Server Health table in the right column should now cap at 260px with a visible scrollbar when more than ~5 servers are listed. Horizontal columns (Ping, Loss %, Jitter) should also be reachable.

- [ ] **Step 3: Commit**

```
git add templates/dashboard.html
git commit -m "fix(dashboard): add max-height and scroll to server health table"
```

---

### Task 5: Replace pill tabs with underline tabs (dark + light theme)

**Files:**
- Modify: `templates/dashboard.html` (multiple locations — see exact rules below)

There are 6 tab-style rule groups to replace/slim. Work through them in order.

- [ ] **Step 1: Replace base `.tabs` and `.tabs button` rules (~lines 41–89)**

Find and replace these three rule blocks in the first `<style>` block:

```css
/* REMOVE THIS — old .tabs base */
.tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

/* REMOVE THIS — old .tabs button */
.tabs button {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: #8b949e;
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    transition: border-color 130ms ease, background 130ms ease;
}

/* REMOVE THIS — old .tabs button.active */
.tabs button.active {
    color: #e6edf5;
    border-color: rgba(148, 163, 184, 0.32);
    background: rgba(148, 163, 184, 0.18);
}

/* REMOVE THIS — old .tabs button:hover */
.tabs button:hover {
    color: #e6edf5;
    border-color: rgba(148, 163, 184, 0.32);
}
```

Replace with:

```css
/* NEW underline tab bar */
.tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
}

.tabs::-webkit-scrollbar {
    display: none;
}

.tabs button {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    padding: 0.32rem 0.75rem;
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-weight: 600;
    color: rgba(148, 163, 184, 0.6);
    cursor: pointer;
    white-space: nowrap;
    transition: color 160ms ease, border-color 160ms ease;
}

.tabs button.active,
.tabs button[aria-selected="true"] {
    color: #5eead4;
    border-bottom-color: #5eead4;
}

.tabs button:hover {
    color: var(--e-text-primary, #e6edf5);
    border-bottom-color: rgba(148, 163, 184, 0.35);
}
```

- [ ] **Step 2: Slim `.dashboard-enterprise .tabs` overrides (~lines 732–757)**

Find these rules and strip pill-specific properties, keeping only sizing:

```css
/* BEFORE */
.dashboard-enterprise .tabs {
    gap: 0.28rem;
}

.dashboard-enterprise .tabs button {
    padding: 0.18rem 0.44rem;
    font-size: 0.6rem;
    line-height: 1.2;
    letter-spacing: 0.08em;
    border-radius: 3px;
    color: rgba(148, 163, 184, 0.5);
    border: 1px solid rgba(148, 163, 184, 0.10);
    background: transparent;
    transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
}

.dashboard-enterprise .tabs button.active {
    color: var(--e-text-secondary);
    border-color: rgba(148, 163, 184, 0.26);
    background: rgba(148, 163, 184, 0.10);
}

.dashboard-enterprise .tabs button:hover {
    color: var(--e-text-secondary);
    border-color: rgba(148, 163, 184, 0.22);
}

/* AFTER — only keep sizing, remove pill appearance */
.dashboard-enterprise .tabs button {
    padding: 0.18rem 0.44rem;
    font-size: 0.6rem;
    letter-spacing: 0.08em;
}
```

(The `.dashboard-enterprise .tabs { gap: 0.28rem; }` rule conflicts with the base `gap: 0` — delete it entirely.)

- [ ] **Step 3: Replace light-theme tab overrides (~lines 2047–2057)**

Find:

```css
html[data-theme="light"] .dashboard-enterprise .tabs button {
    background: rgba(15, 23, 42, 0.04);
    color: #475569;
    border-color: rgba(15, 23, 42, 0.12);
}

html[data-theme="light"] .dashboard-enterprise .tabs button.active {
    background: rgba(15, 23, 42, 0.08);
    color: #0f172a;
    border-color: rgba(15, 23, 42, 0.24);
}
```

Replace with:

```css
html[data-theme="light"] .tabs {
    border-bottom-color: rgba(15, 23, 42, 0.15);
}

html[data-theme="light"] .tabs button {
    color: rgba(51, 65, 85, 0.6);
}

html[data-theme="light"] .tabs button.active,
html[data-theme="light"] .tabs button[aria-selected="true"] {
    color: #0d9488;
    border-bottom-color: #0d9488;
}

html[data-theme="light"] .tabs button:hover {
    color: #1e293b;
    border-bottom-color: rgba(15, 23, 42, 0.25);
}
```

- [ ] **Step 4: Slim `#device-breakdown .tabs button` size overrides (~line 4156)**

Find (inside a `@media (max-width: 992px)` block):

```css
#device-breakdown .tabs button {
    padding: 0.14rem 0.42rem;
    font-size: 0.56rem;
    letter-spacing: 0.09em;
}
```

Keep this rule as-is — it only overrides sizing at small screens, which is fine.

- [ ] **Step 5: Delete the `dashboard-flow` round-pill tab override (~line 4836)**

Find and delete entirely:

```css
.dashboard-flow .tabs button,
.dashboard-flow #device-breakdown .tabs button {
    position: relative;
    min-height: 30px;
    padding: 0.34rem 0.72rem;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: rgba(203, 213, 225, 0.62);
    font-family: 'Inter', 'IBM Plex Sans', system-ui, sans-serif;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0;
    text-transform: none;
    ...
}
```

(Delete through the closing `}` of this block.)

Also slim `.dashboard-flow #device-breakdown .tabs button` at ~line 3205 — keep only `padding` and `font-size`, delete `border`, `border-radius`, `background`:

```css
/* AFTER */
.dashboard-flow #device-breakdown .tabs button {
    padding: 0.26rem 0.54rem;
    font-size: 0.6rem;
}
```

- [ ] **Step 6: Verify tabs in browser**

All four tabs (Latency / Packet Loss / Inventory / Discovery) show an underline bar. Active tab has teal underline and text. Inactive tabs are dimmer. Toggle to light theme — active tab should be teal-600 (`#0d9488`), inactive tabs dark-slate.

- [ ] **Step 7: Commit**

```
git add templates/dashboard.html
git commit -m "feat(dashboard): replace pill tabs with underline tabs, add light-theme colors"
```

---

### Task 6: Add ARIA attributes to tabs (HTML + JS)

**Files:**
- Modify: `templates/dashboard.html` (~line 3139 — the tab buttons HTML)
- Modify: `static/js/dashboard/dashboard.js` (`setupTabs()` at line 698)

- [ ] **Step 1: Find the tab buttons in HTML**

Search for `data-target="tab-latency"` in `templates/dashboard.html`. You will find four buttons like:

```html
<button class="active" data-target="tab-latency">Latency</button>
<button data-target="tab-loss">Packet Loss</button>
<button data-target="tab-inventory-list">Inventory</button>
<button data-target="tab-discovery">Discovery</button>
```

- [ ] **Step 2: Add ARIA to tab buttons**

Replace those four buttons with:

```html
<button class="active" role="tab" aria-selected="true"
        aria-controls="tab-latency" id="tab-btn-latency"
        data-target="tab-latency">Latency</button>
<button role="tab" aria-selected="false"
        aria-controls="tab-loss" id="tab-btn-loss"
        data-target="tab-loss">Packet Loss</button>
<button role="tab" aria-selected="false"
        aria-controls="tab-inventory-list" id="tab-btn-inventory"
        data-target="tab-inventory-list">Inventory</button>
<button role="tab" aria-selected="false"
        aria-controls="tab-discovery" id="tab-btn-discovery"
        data-target="tab-discovery">Discovery</button>
```

- [ ] **Step 3: Add `role="tabpanel"` and `aria-labelledby` to the panel divs**

Find the four panel divs by their IDs. Each starts like `<div id="tab-latency" class="tab-content is-active">`. Add ARIA:

```html
<div id="tab-latency" class="tab-content is-active"
     role="tabpanel" aria-labelledby="tab-btn-latency">

<div id="tab-loss" class="tab-content"
     role="tabpanel" aria-labelledby="tab-btn-loss">

<div id="tab-inventory-list" class="tab-content"
     role="tabpanel" aria-labelledby="tab-btn-inventory">

<div id="tab-discovery" class="tab-content"
     role="tabpanel" aria-labelledby="tab-btn-discovery">
```

- [ ] **Step 4: Sync `aria-selected` in the JS click handler**

In `static/js/dashboard/dashboard.js`, find `setupTabs()` at line 698. Inside the click handler, the existing code does `tabs.forEach(t => t.classList.remove('active'))` (line 728). Add `aria-selected` sync on the same lines:

```js
// BEFORE (line 728):
tabs.forEach(t => t.classList.remove('active'));
e.target.classList.add('active');

// AFTER:
tabs.forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
});
e.target.classList.add('active');
e.target.setAttribute('aria-selected', 'true');
```

- [ ] **Step 5: Verify in browser**

Open DevTools → Elements panel → click each tab → inspect the button: `aria-selected` should toggle to `"true"` on the active tab and `"false"` on others.

- [ ] **Step 6: Commit**

```
git add templates/dashboard.html static/js/dashboard/dashboard.js
git commit -m "feat(dashboard): add ARIA role/aria-selected/aria-controls to tab navigation"
```

---

### Task 7: Standardize table cell padding

**Files:**
- Modify: `templates/dashboard.html` (~line 877 for `thead th`, ~line 903 for `tbody td`, ~line 384 for server-health override)

- [ ] **Step 1: Update the enterprise thead padding**

Find `.dashboard-enterprise .tactical-table thead th` (~line 877). It currently sets various padding. Change to:

```css
.dashboard-enterprise .tactical-table thead th {
    background: rgba(15, 18, 26, 0.96) !important;
    color: #b8c6d5;
    text-transform: uppercase;
    font-size: 0.62rem;
    letter-spacing: 0.075em;
    font-weight: 700;
    padding: 0.34rem 0.62rem;    /* ← standardized */
    border-bottom: 1px solid rgba(148, 163, 184, 0.24);
    white-space: nowrap;
    transition: background-color 140ms ease;
}
```

- [ ] **Step 2: Update the enterprise tbody padding**

Find `.dashboard-enterprise .tactical-table tbody td` (~line 903). Change:

```css
.dashboard-enterprise .tactical-table tbody td {
    padding: 0.44rem 0.62rem;    /* ← was 0.42rem 0.56rem */
    line-height: 1.3;            /* ← was 1.24 */
    color: var(--e-text-secondary);
    border-bottom: 1px solid rgba(148, 163, 184, 0.075);
    vertical-align: middle;
}
```

- [ ] **Step 3: Remove padding override on server-health-detail**

Find `#server-health-detail .tactical-table th, #server-health-detail .tactical-table td` (~line 384). It currently sets `padding: 0.55rem 0.75rem; font-size: 0.9rem`. Keep only font-size:

```css
/* BEFORE */
#server-health-detail .tactical-table th,
#server-health-detail .tactical-table td {
    padding: 0.55rem 0.75rem;
    font-size: 0.9rem;
}

/* AFTER — padding removed, font-size kept */
#server-health-detail .tactical-table th,
#server-health-detail .tactical-table td {
    font-size: 0.9rem;
}
```

- [ ] **Step 4: Verify in browser**

All tables — latency, loss, inventory, server health, subnet, top affected devices — should have consistent horizontal column alignment. Text in `th` and `td` should align at the same left edge.

- [ ] **Step 5: Commit**

```
git add templates/dashboard.html
git commit -m "fix(dashboard): standardize tactical-table cell padding to 0.34/0.44rem × 0.62rem"
```

---

### Task 8: Alerts table expand toggle

Only the Alerts table gets the "Show all rows" toggle. The toggle animates to actual `scrollHeight` (not a magic large number) to avoid layout jank.

**Files:**
- Modify: `templates/dashboard.html` (~line 5852 for HTML, first `<style>` block for CSS)
- Modify: `static/js/dashboard/dashboard.js` (add handler after existing init calls)

- [ ] **Step 1: Add CSS for the alerts wrapper and expand button**

In the first `<style>` block of `templates/dashboard.html`, add at the end (before the closing `</style>`):

```css
#alerts-table-responsive {
    max-height: 320px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    transition: max-height 260ms ease;
}

.btn-expand-rows {
    display: block;
    width: 100%;
    padding: 0.3rem;
    background: transparent;
    border: none;
    border-top: 1px dashed rgba(148, 163, 184, 0.18);
    font-size: 0.68rem;
    color: rgba(94, 234, 212, 0.7);
    cursor: pointer;
    text-align: center;
    transition: color 120ms ease;
}

.btn-expand-rows:hover {
    color: #5eead4;
}
```

- [ ] **Step 2: Update the HTML — add `id` and expand button**

Find line 5852 in `templates/dashboard.html`:

```html
<!-- BEFORE -->
<div class="table-responsive">
    <table class="tactical-table table-hover mb-0">
        <thead>
            <tr>
                <th>Severity</th>
```

Change to:

```html
<!-- AFTER — add id to wrapper, add button after </table> before </div> -->
<div class="table-responsive" id="alerts-table-responsive">
    <table class="tactical-table table-hover mb-0">
        <thead>
            <tr>
                <th>Severity</th>
```

Then find `</table>` followed by `</div>` for this specific table (the one containing `#table-alerts-body`) and insert the button between them:

```html
            </tbody>
        </table>
    </div>
    <button type="button" class="btn-expand-rows" id="alerts-expand-btn"
            aria-expanded="false" aria-controls="alerts-table-responsive">
        <i class="fas fa-chevron-down me-1"></i> Show all rows
    </button>
</div>
```

Note: the button is a sibling of `<table>`, inside the `#alerts-table-responsive` div. The outer `</div>` closes `#alerts-table-responsive`.

- [ ] **Step 3: Add the JS handler**

In `static/js/dashboard/dashboard.js`, find the bottom of the file or the `DOMContentLoaded` / init section. Add:

```js
function initAlertsExpand() {
    const alertsExpandBtn = document.getElementById('alerts-expand-btn');
    const alertsWrapper   = document.getElementById('alerts-table-responsive');

    if (!alertsExpandBtn || !alertsWrapper) return;  // both must exist

    alertsExpandBtn.addEventListener('click', () => {
        const isExpanded = alertsExpandBtn.getAttribute('aria-expanded') === 'true';

        if (isExpanded) {
            alertsWrapper.style.maxHeight = '320px';
            alertsExpandBtn.setAttribute('aria-expanded', 'false');
            alertsExpandBtn.innerHTML =
                '<i class="fas fa-chevron-down me-1"></i> Show all rows';
        } else {
            alertsWrapper.style.maxHeight = alertsWrapper.scrollHeight + 'px';
            alertsExpandBtn.setAttribute('aria-expanded', 'true');
            alertsExpandBtn.innerHTML =
                '<i class="fas fa-chevron-up me-1"></i> Show fewer rows';
        }
    });
}
```

Find where `initDeviceBreakdown()` or `setupTabs()` are called (look for a `DOMContentLoaded` block or `init()` function). Call `initAlertsExpand()` in the same place.

- [ ] **Step 4: Verify in browser**

Scroll to the Alerts section. The table should show ~4–5 rows then clip. Click "Show all rows" — table expands to full content height smoothly. Click "Show fewer rows" — collapses back. Check DevTools: `aria-expanded` toggles on the button. Other tables (latency, server health, subnet) have no such button.

- [ ] **Step 5: Commit**

```
git add templates/dashboard.html static/js/dashboard/dashboard.js
git commit -m "feat(dashboard): add expand toggle to alerts table only (scrollHeight animation)"
```

---

## Full verification checklist

After all tasks are complete, run through this in order:

1. Open `http://127.0.0.1:5000/dashboard`
2. Expand Network Intelligence panel → **row scroll** works (vertical scrollbar visible on tables with many rows)
3. If any table has wide columns → **column scroll** works (horizontal scrollbar reachable)
4. Collapse panel → animation plays cleanly, content disappears, no flash
5. Re-expand → scroll still works (overflow restored by `transitionend`)
6. Chrome DevTools device emulation (iPhone SE) → momentum scroll on all tables
7. Click Latency / Packet Loss / Inventory / Discovery tabs → active tab shows **teal underline**, others are dimmer
8. Tab bar narrowed until tabs overflow → bar scrolls **without showing a scrollbar**
9. DevTools → inspect active tab button → `aria-selected="true"`; others `"false"`
10. Server Health table (right column) → capped at **260px**, scrolls vertically and horizontally
11. Subnet table (bottom right) → scrolls in **both axes**
12. Alerts section → "Show all rows" button present, expands smoothly to content height
13. Other tables (latency, inventory, server health, subnet) → **no expand button**
14. Toggle light theme → active tab shows `#0d9488` teal, inactive tabs legible
15. All table columns left-align consistently (uniform padding)
