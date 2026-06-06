# Dashboard UX Polish — Scroll, Column Access, Tab Redesign

**Date:** 2026-06-06
**Branch:** feat/floor-plan-geotagging (or new branch)

---

## Context

The dashboard's Network Intelligence panel uses a CSS collapse animation (`max-height: 0 → 2800px`) that requires `overflow: hidden` on `#device-breakdown`. The `.is-active` expanded state never overrides this, so all inner `.table-responsive` scroll containers are clipped — both vertically (rows flow off the bottom) and horizontally (wide columns are unreachable). The subnet scroll also only sets `overflow-y`, cutting off columns. The fleet/server health table has no `max-height` so its scrollbar never activates.

Separately, the tab navigation uses pill-style buttons with a subtle active state. Users miss that other tables exist behind inactive tabs.

**Approved design direction:**
- Restore scroll (both axes) on all table containers
- Underline tab style with teal active bar
- "Show all rows" expand toggle **only** on the Alerts table; all other tables simply scroll

---

## Fixes

### 1. Root cause — `dashboard-flow` scroll-kill override + secondary `#device-breakdown` overflow clipping

**Primary fix — File:** `templates/dashboard.html` (~line 3115)

The root `<div>` at line 3281 carries `class="container-fluid dashboard-enterprise dashboard-dense dashboard-flow"`. A CSS block at line 3115 uses `!important` to override every scroll container:

```css
/* DELETE this entire block */
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

Delete these 9 lines. All other `dashboard-flow` rules (spacing, typography, colours) are kept.

**Secondary fix — File:** `templates/dashboard.html` (second `<style>` block, ~line 2456)

Even with the above removed, `#device-breakdown` has `overflow: hidden` for its collapse animation and `.is-active` never overrides it. It also sets `transform: translateY(0)` — a no-op visually, but a known Firefox quirk silently demotes `overflow: visible` to `overflow: clip` on transformed elements. Both are fixed together:

```css
#device-breakdown.is-active {
    max-height: 2800px;
    opacity: 1;
    transform: none;        /* ← was translateY(0); none removes the stacking-context clash */
    margin-top: 0.65rem;
    pointer-events: auto;
    /* overflow set by JS transitionend handler, not CSS, so animation isn't broken */
}
```

In `openDeviceBreakdown()` (`dashboard.js:822`), after `el.classList.add('is-active')`, attach a one-shot `transitionend` listener. In `closeDeviceBreakdown()` (`dashboard.js:846`), reset overflow **before** removing the class so the collapse animation re-clips content:

```js
// openDeviceBreakdown() — after el.classList.add('is-active')
el.addEventListener('transitionend', function onExpand(e) {
    if (e.propertyName === 'max-height' && el.classList.contains('is-active')) {
        el.style.overflow = 'visible';
        el.removeEventListener('transitionend', onExpand);
    }
}, { once: true });   // belt-and-suspenders: { once: true } auto-removes

// closeDeviceBreakdown() — BEFORE el.classList.remove('is-active')
el.style.overflow = 'hidden';
el.classList.remove('is-active');
```

### 2. Subnet scroll — add horizontal axis + touch

**File:** `templates/dashboard.html` (`.dashboard-enterprise` styles, ~line 1028)

```css
.dashboard-enterprise .network-segments-panel .subnet-table-scroll {
    max-height: 340px;
    overflow: auto;                      /* was overflow-y: auto — adds x-axis */
    -webkit-overflow-scrolling: touch;   /* momentum scroll on iOS */
    overscroll-behavior: contain;
}
```

### 3. Server Health table — add max-height + scroll + touch

**File:** `templates/dashboard.html`

```css
.fleet-table-card .table-responsive {
    max-height: 260px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
    scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
}
```

### 4. Breakdown table touch support

**File:** `templates/dashboard.html` (second `<style>` block, ~line 2964)

The existing `#device-breakdown .table-responsive` rule already has `overflow: auto`. Add touch momentum:

```css
#device-breakdown .table-responsive {
    /* existing properties kept */
    -webkit-overflow-scrolling: touch;   /* ← ADD */
}
```

### 5. Tab style — pill → underline

**File:** `templates/dashboard.html` (first `<style>` block, `.tabs` / `.tabs button` rules, ~lines 26–74 and 715–740)

```css
.tabs {
    display: flex;
    gap: 0;
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;          /* Firefox: hide scroll on tab bar */
}

.tabs::-webkit-scrollbar {
    display: none;                  /* WebKit: hide scroll on tab bar */
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

/* Light theme */
html[data-theme="light"] .tabs {
    border-bottom-color: rgba(15, 23, 42, 0.15);
}

html[data-theme="light"] .tabs button {
    color: rgba(51, 65, 85, 0.6);
}

html[data-theme="light"] .tabs button.active,
html[data-theme="light"] .tabs button[aria-selected="true"] {
    color: #0d9488;                 /* teal-600 for legibility on white */
    border-bottom-color: #0d9488;
}

html[data-theme="light"] .tabs button:hover {
    color: #1e293b;
    border-bottom-color: rgba(15, 23, 42, 0.25);
}
```

Remove now-irrelevant `border-radius`, `background`, `border` properties from the `.dashboard-enterprise .tabs` and `#device-breakdown .tabs` overrides.

**Accessibility — add ARIA to existing tab buttons in HTML:**

Each tab `<button>` needs `role="tab"` and `aria-selected`. Each tab panel needs `role="tabpanel"` and `aria-labelledby`. The JS `initTabs()` function already toggles `.active` on buttons — also toggle `aria-selected`:

```js
// In initTabs() tab click handler (dashboard.js ~line 728):
tabs.forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
});
e.target.classList.add('active');
e.target.setAttribute('aria-selected', 'true');
```

HTML attributes to add on the tab buttons in `templates/dashboard.html`:
```html
<button role="tab" aria-selected="true"  aria-controls="tab-latency"       data-target="tab-latency">Latency</button>
<button role="tab" aria-selected="false" aria-controls="tab-loss"           data-target="tab-loss">Packet Loss</button>
<button role="tab" aria-selected="false" aria-controls="tab-inventory-list" data-target="tab-inventory-list">Inventory</button>
<button role="tab" aria-selected="false" aria-controls="tab-discovery"      data-target="tab-discovery">Discovery</button>
```

And on each panel `<div>`:
```html
<div id="tab-latency"       class="tab-content is-active" role="tabpanel" aria-labelledby="[btn-id]">
```

### 6. Uniform cell padding

**File:** `templates/dashboard.html`

```css
.dashboard-enterprise .tactical-table thead th {
    padding: 0.34rem 0.62rem;
}

.dashboard-enterprise .tactical-table tbody td {
    padding: 0.44rem 0.62rem;
    line-height: 1.3;
}
```

The `#server-health-detail .tactical-table th, td` override at ~line 384 sets `padding: 0.55rem 0.75rem` and `font-size: 0.9rem`. Keep the font-size override (server health is a detail view that benefits from larger text) but remove the padding override so horizontal alignment matches other tables.

### 7. Alerts table expand toggle (Alerts only)

**Animation jank note:** `max-height` transitions on large values (`0 → 2000px`) cause the browser to lay out the full 2000px height every frame, causing jank. Use `scrollHeight` to animate to the actual content height instead:

**File:** `templates/dashboard.html` line 5852

```html
<div class="table-responsive" id="alerts-table-responsive">
    <table class="tactical-table table-hover mb-0">
        ...
    </table>
</div>
<button type="button" class="btn-expand-rows" id="alerts-expand-btn"
        aria-expanded="false" aria-controls="alerts-table-responsive">
    <i class="fas fa-chevron-down me-1"></i> Show all rows
</button>
```

CSS (no max-height transition here — JS drives it via `scrollHeight`):

```css
#alerts-table-responsive {
    max-height: 320px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
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

.btn-expand-rows:hover { color: #5eead4; }
```

JS — guard both elements together; animate to actual `scrollHeight` to avoid max-height jank:

```js
const alertsExpandBtn = document.getElementById('alerts-expand-btn');
const alertsWrapper   = document.getElementById('alerts-table-responsive');

if (alertsExpandBtn && alertsWrapper) {               // both must exist
    alertsExpandBtn.addEventListener('click', () => {
        const isExpanded = alertsExpandBtn.getAttribute('aria-expanded') === 'true';

        if (isExpanded) {
            alertsWrapper.style.maxHeight = '320px';
            alertsExpandBtn.setAttribute('aria-expanded', 'false');
            alertsExpandBtn.innerHTML =
                '<i class="fas fa-chevron-down me-1"></i> Show all rows';
        } else {
            // Animate to actual content height — no layout thrash from a huge magic number
            alertsWrapper.style.maxHeight = alertsWrapper.scrollHeight + 'px';
            alertsExpandBtn.setAttribute('aria-expanded', 'true');
            alertsExpandBtn.innerHTML =
                '<i class="fas fa-chevron-up me-1"></i> Show fewer rows';
        }
    });
}
```

Add a CSS transition directly on the element (not on `.expanded`) so it works with the dynamic `scrollHeight` value:

```css
#alerts-table-responsive {
    max-height: 320px;
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    transition: max-height 260ms ease;   /* transition the inline style change */
}
```

---

## Files to modify

| File | What changes |
|------|-------------|
| `templates/dashboard.html` | CSS: overflow/transform fix, tab underline + light-theme colors, WebKit scrollbar hide on tab bar, touch scrolling on all containers, uniform padding, subnet x-axis, fleet table max-height, alerts expand CSS |
| `templates/dashboard.html` | HTML: tab button ARIA attrs (`role`, `aria-selected`, `aria-controls`), tab panel `role="tabpanel"`, alerts expand button with `aria-expanded` / `aria-controls` |
| `static/js/dashboard/dashboard.js` | JS: `openDeviceBreakdown` transitionend + `transform: none` guard; `closeDeviceBreakdown` overflow reset; `initTabs` aria-selected sync; alerts expand handler with `scrollHeight` animation |

---

## Verification

1. Open `http://127.0.0.1:5000/dashboard`
2. Expand the Network Intelligence panel → tables show scrollbars for vertical rows and horizontal columns
3. On iOS/Android (or Chrome DevTools device emulation) → momentum scroll works in all tables
4. Collapse the panel → animation plays with no content flash; re-expand → overflow restores correctly
5. Click Latency / Loss / Inventory tabs → active tab shows teal underline; `aria-selected` toggles in DevTools
6. Keyboard-navigate tabs → focus ring visible, `aria-selected` reflects state
7. Tab bar has more tabs than visible width → tabs scroll without showing a scrollbar
8. Right column: Server Health table capped at 260px with scroll
9. Bottom right: Subnet table scrollable in both directions (horizontal columns reachable)
10. Alerts section: "Show all rows" toggles smoothly to actual content height (no snap to 2000px)
11. Other tables have no expand button
12. Toggle light theme → tab active bar shows teal-600, inactive tabs legible on white background
