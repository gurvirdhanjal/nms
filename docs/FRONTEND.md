# NMS Design System — Version 5.0
## Network Operations Center · Enterprise Edition

> **Scope:** Every template, component, modal, table, JS-rendered element, and real-time monitoring surface.
> **Ground truth:** This document overrides all prior versions. If any implementation conflicts — the implementation is wrong.

---

## 0. Mental Model — Operational Clarity First

This is a live **Network Monitoring System (NMS)**.

It is **not** a SaaS marketing UI.
It is **not** decorative.
It is **not** expressive.

It is a **control surface** — built for anomaly detection under operational stress, used by engineers who make fast decisions in degraded conditions.

### The Contract

| Design Must... | Design Must Never... |
|---|---|
| Surface anomalies in < 1 second | Add decorative flourish |
| Keep healthy states visually quiet | Use vivid gradients for emphasis |
| Remain stable during live polling | Animate state changes |
| Show time context on every metric | Display raw bytes or raw seconds |
| Right-align all numeric values | Center-align metrics |
| Elevate critical alerts above all else | Use green on active tabs |

### Core Philosophy

> **Healthy systems must visually recede.**
> **Warning systems must be detectable within 1 second.**
> **Critical systems must be detectable in peripheral vision.**

If a design change improves aesthetics but reduces anomaly clarity — **it is rejected.**
If a design change increases clarity at zero aesthetic cost — **it is preferred.**

---

### 0.1 Server Details Interaction Contract

- Use a **hybrid pattern**:
  - Dashboard server rows open the shared **Server Details modal** for rapid triage.
  - `/devices/<id>/details` remains the canonical full details page.
- `/devices/<id>/details` must be an **inventory-first device profile**:
  - identity, assignment, topology, monitoring posture, latest telemetry from any source
  - SNMP is optional supplemental data only and must never be the primary dependency for the page
  - server telemetry operations belong on `/devices/<id>/server-monitoring`, not in the core profile contract
- The modal markup is a shared partial (`templates/partials/server_details_modal.html`) and is reused by both dashboard and device details surfaces.
- Connection data shown in modal/page must stay uniform:
  - Agent snapshot (`top_remote_ips`, `unique_remote_ips_count`)
  - Normalized top-20 connection rows derived from that same snapshot
- Current scope: **Agent snapshot only** for the connection table (SSH/WMI live fetch removed from this surface).

## 1. Operational UI Principles

### 1.1 Status Hierarchy (Visual Dominance Order)

```
Critical   → Highest visual weight. Immediately detectable.
Warning    → Secondary weight. Visible at a glance.
Degraded   → Tertiary. Noticeable on scan.
Healthy    → Must recede. Informational only. Never celebratory.
Unknown    → Neutral. Muted.
Offline    → Neutral-dark. Distinct from Unknown.
```

Green (`--s-healthy`) is **informational**, not celebratory. It must never visually dominate the layout. Limit its use to small status indicators and muted text — never large fills or bold accents.

### 1.2 Monitoring Stability Rule

The interface must remain **visually stable** during all polling updates.

- ❌ No layout shift during data refresh
- ❌ No element resizing based on value change
- ❌ No animated emphasis on state change
- ❌ No flashing, blinking, or pulsing — ever
- ✅ Status updates must be **instant and calm**
- ✅ Motion is allowed **only** for structural transitions: panel open/close, tab switch, modal entry

### 1.3 Time Context Rule

**Every state must include temporal context.** A status without time is incomplete and unacceptable.

Required temporal fields (use whichever applies):

| Context | Example |
|---|---|
| Last seen | `Last seen: 2m 14s ago` |
| Last polled | `Polled: 14:32:07 UTC` |
| Updated relative | `Updated 8s ago` |
| Downtime duration | `Down: 1h 24m` |
| Consecutive failures | `Failures: 7 consecutive` |

### 1.4 Monitoring Load Visibility Rule

**Operators must know when monitoring itself is degraded.**

The Global Status Strip must always expose:

- Poll interval (e.g. `30s`)
- Last poll duration (e.g. `412ms`)
- Task queue backlog (if > 0)
- Sync timestamp (UTC)

If any of these values are missing or stale, the strip must indicate a **degraded** monitoring state.

### 1.5 Shared Primitive Rollout Rule

During migration windows:

- new shared UI primitives must be additive and route-scoped
- untouched operational surfaces stay on the legacy system until their migration step is merged and verified
- migrated surfaces keep a compatibility fallback so shared helper failure does not break the page
- shared helper scripts must not throw during script evaluation

### 1.6 Migrated Ops Feedback Rule

On migrated operational surfaces:

- do not use `spinner-border`
- do not use `fa-spin` for passive refresh states
- do not use `progress-bar-animated`
- do not wipe hydrated tables back to a generic `Loading...` state
- keep last-known-good data visible while refresh is in flight

---

## 2. CSS Architecture

### Class Hierarchy — Never Bypass

```
body
└── .tactical-theme
    └── .dashboard-enterprise
        ├── .overall-health-card          ← Layer 1: Global Status Strip
        ├── .tactical-stat-card           ← Layer 2: KPI Grid
        │   └── .enterprise-kpi
        ├── #device-breakdown             ← Layer 3: Network Intelligence
        │   └── .breakdown-panel
        │       ├── .breakdown-grid
        │       ├── .breakdown-subcard
        │       └── .breakdown-section-heading
        ├── .fleet-table-card             ← Layer 3b: Fleet Overview
        ├── .alerts-container             ← Layer 4: Alerts Command
        └── .enterprise-panel
```

### Hard Rules (Zero Exceptions)

| Rule | Reason |
|---|---|
| ❌ No `style=""` attributes (except `canvas height`) | Tokens must govern all layout |
| ❌ No unscoped CSS | Cascade pollution corrupts hierarchy |
| ❌ No hardcoded hex values in JavaScript | All colors via CSS variables |
| ❌ No `!important` outside `.dashboard-enterprise` | Forces cascade discipline |
| ❌ No decorative gradients in enterprise mode | Visual noise during stress |
| ❌ No `backdrop-filter: blur()` for aesthetics | Non-operational distraction |
| ❌ No infinite animations | Stress-inducing, distracting |
| ❌ No bounce easing | Non-operational feel |
| ❌ No `scale > 1.02` on hover | Disrupts layout stability |
| ❌ No layout shift on data update | Operator confusion |
| ❌ No `display: none` toggling without animated wrapper | Jarring transitions |
| ❌ No `chart.destroy()` on refresh | Chart flicker |
| ❌ No full table `innerHTML` rebuild during live updates | Causes reflow/repaint spikes |
| ✅ Use keyed row patching for live tables | Preserves scroll/focus and reduces churn |
| ❌ No center-aligned numeric metrics | Slower scan speed |
| ❌ No green active tabs | Misleading status emphasis |
| ❌ No `text-success` / `text-danger` Bootstrap defaults | Use enterprise tokens only |

---

## 3. Design Tokens (Enterprise Mode)

All tokens are defined on `.dashboard-enterprise`. **No raw values outside this system.**

```css
.dashboard-enterprise {
    /* ── Spacing ── */
    --e-space-1: 0.4rem;
    --e-space-2: 0.62rem;
    --e-space-3: 0.82rem;
    --e-space-4: 1.15rem;
    --e-space-5: 1.55rem;

    /* ── Surfaces ── */
    --e-bg-base:       #070a10;
    --e-bg-panel:      rgba(16, 19, 27, 0.92);
    --e-bg-panel-soft: rgba(16, 19, 27, 0.72);
    --e-bg-row-alt:    rgba(255, 255, 255, 0.018);
    --e-bg-row-hover:  rgba(148, 163, 184, 0.10);

    /* ── Borders ── */
    --e-border:        rgba(148, 163, 184, 0.16);
    --e-border-strong: rgba(148, 163, 184, 0.32);
    --e-border-panel:  rgba(148, 163, 184, 0.20);

    /* ── Typography ── */
    --e-text-primary:   #e6edf5;
    --e-text-secondary: #c3cfdb;
    --e-text-muted:     #8a97a6;
    --e-text-dim:       #5e6b78;

    /* ── Status Colors ── */
    --s-critical:  #dc3545;
    --s-warning:   #ffc107;
    --s-degraded:  #fd7e14;
    --s-healthy:   #20c997;
    --s-offline:   #6c757d;
    --s-unknown:   #adb5bd;

    /* ── Status Backgrounds (muted, for badges/rows) ── */
    --s-critical-bg:  rgba(220, 53, 69, 0.12);
    --s-critical-bd:  rgba(220, 53, 69, 0.30);
    --s-warning-bg:   rgba(255, 193, 7, 0.10);
    --s-warning-bd:   rgba(255, 193, 7, 0.28);
    --s-healthy-bg:   rgba(32, 201, 151, 0.10);
    --s-healthy-bd:   rgba(32, 201, 151, 0.24);
    --s-offline-bg:   rgba(108, 117, 125, 0.12);
    --s-offline-bd:   rgba(108, 117, 125, 0.28);

    /* ── Animation Durations ── */
    --a-micro:      130ms;
    --a-transition: 180ms;
    --a-panel:      300ms;
    --a-max:        400ms;

    /* ── Typography Scale ── */
    --t-kpi-value:    1.2rem;
    --t-kpi-label:    0.63rem;
    --t-table-header: 0.62rem;
    --t-body:         0.82rem;
    --t-mono:         'IBM Plex Mono', monospace;
    --t-sans:         'IBM Plex Sans', sans-serif;
}
```

### Token Usage Rules

- Token names that begin with `--e-` are layout/structural tokens
- Token names that begin with `--s-` are status semantic tokens
- Token names that begin with `--a-` are animation tokens
- Token names that begin with `--t-` are typography tokens
- **Never mix token categories** (e.g. don't use `--s-critical` for borders on non-critical elements)

---

## 4. Status Colors — Monitoring Tuned

| Status | Token | Hex | Usage Rule |
|---|---|---|---|
| Critical | `--s-critical` | `#dc3545` | Full saturation. Use freely. Must be visible at all sizes. |
| Warning | `--s-warning` | `#ffc107` | Full saturation. Must contrast clearly against green. |
| Degraded | `--s-degraded` | `#fd7e14` | Between warning and critical. Use for partial failures. |
| Healthy | `--s-healthy` | `#20c997` | **Muted use only.** Small dots, thin text. Never fills. |
| Offline | `--s-offline` | `#6c757d` | Neutral-dark. Distinct from unknown. |
| Unknown | `--s-unknown` | `#adb5bd` | Light neutral. Informational. |

### Anti-Patterns for Status Colors

```css
/* ❌ WRONG — Green dominates the layout */
.status-healthy { background: #20c997; color: white; font-size: 1.4rem; }

/* ✅ CORRECT — Green recedes, is informational */
.status-healthy { color: var(--s-healthy); font-size: 0.72rem; opacity: 0.85; }

/* ❌ WRONG — Critical is easy to miss */
.status-critical { color: rgba(220,53,69,0.4); font-size: 0.6rem; }

/* ✅ CORRECT — Critical demands attention */
.status-critical { color: var(--s-critical); font-weight: 700; }
```

---

## 5. Layout Hierarchy — 4 Operational Layers

### Layer 1 — Global Status Strip (`.overall-health-card`)

The top bar. Always visible. Never hidden. Contains:

- Current health state + colored dot indicator
- Critical count + Down count (bold, prominent)
- Poll interval + last poll duration
- Sync timestamp (UTC)
- Last updated (relative)

**Strip must use `--e-border-strong` when critical count > 0.**

### Layer 2 — KPI Grid (`.tactical-stat-card`)

Six cards maximum. Fixed column count. Never reflow to fewer columns on large screens.

KPI order is **fixed by operational urgency** — do not reorder for visual symmetry:

```
1. Health Score / Devices Online
2. Availability %
3. Critical Count
4. Offline Count
5. Resource Saturation (CPU/Memory)
6. Performance Metric (Latency/Jitter)
```

### Layer 3 — Breakdown Intelligence (`#device-breakdown` + Fleet)

Expandable panels for deep diagnostics. Contains:

- Network performance tables (latency, loss, jitter)
- Inventory breakdown chart
- Fleet server health KPIs + table
- Service impact analysis

### Layer 4 — Alerts Command (`.alerts-container`)

The bottom command surface. When critical alerts > 0:

- Visually elevates above Fleet Overview (reduced divider spacing)
- Header border strengthens to `--e-border-strong`
- Alert table rows for critical status use `--s-critical-bg` background
- **No animation. No flash. Only structural priority shift.**

### 5.1 Alerts Escalation Rule

```
IF active_critical_alerts > 0:
  .alerts-container border → --e-border-strong
  .alerts-container margin-top → --e-space-2 (reduce from --e-space-4)
  alert table: critical rows → background: --s-critical-bg
  header label → color: --s-critical
ELSE:
  .alerts-container border → --e-border
  layout → default
```

Zero animation. Zero flash. Only layout priority shift.

---

## 6. Typography System

### Type Scale

| Element | Size | Weight | Transform | Font | Token |
|---|---|---|---|---|---|
| KPI Value | `1.2rem` | 700 | — | IBM Plex Mono | `--t-kpi-value` |
| KPI Label | `0.63rem` | 600 | UPPERCASE | IBM Plex Sans | `--t-kpi-label` |
| Table Header | `0.62rem` | 700 | UPPERCASE | IBM Plex Sans | `--t-table-header` |
| Table Cell | `0.80rem` | 400 | — | IBM Plex Sans | `--t-body` |
| Mono Value | — | 500 | — | IBM Plex Mono | `--t-mono` |
| Section Title | `0.92rem` | 700 | UPPERCASE | IBM Plex Sans | — |

### Typography Rules

- All KPI values: IBM Plex Mono, `font-variant-numeric: tabular-nums`
- All percentage, duration, count, byte values: IBM Plex Mono
- All labels and prose: IBM Plex Sans
- Letter spacing on labels: `0.12em` to `0.16em`
- Letter spacing on table headers: `0.08em` to `0.12em`
- No oversized KPI text (hard max: `1.4rem` for any metric in a KPI card)
- No decorative type scaling

---

## 7. KPI Cards — Specification

### Structure

```html
<div class="tactical-stat-card [critical|warning|healthy]">
  <div class="enterprise-kpi">
    <div class="kpi-header">
      <span class="kpi-label">LABEL TEXT</span>
      <!-- optional status badge -->
    </div>
    <div class="kpi-value [critical|warning|healthy]">VALUE</div>
    <div class="kpi-sub">
      <!-- time context REQUIRED -->
      Updated 8s ago · Prev: 97.2%
    </div>
    <!-- optional sparkline canvas -->
  </div>
</div>
```

### Card State Modifiers

```css
/* Top accent line by status */
.dashboard-enterprise .tactical-stat-card::after {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
}
.tactical-stat-card.critical::after { background: var(--s-critical); }
.tactical-stat-card.warning::after  { background: var(--s-warning); }
.tactical-stat-card.healthy::after  { background: rgba(32, 201, 151, 0.35); }
/* healthy is muted — never full saturation */
```

### Hover Behavior

```css
.dashboard-enterprise .tactical-stat-card:hover {
    transform: translateY(-1px); /* max allowed */
    border-color: var(--e-border-strong);
    /* no box-shadow expansion */
    /* no scale */
    /* no glow */
}
```

---

## 8. Tables — Monitoring Optimized

### Structure Rules

- Numeric columns: `text-align: right`, class `.metric`
- All numeric cells: `font-variant-numeric: tabular-nums`, IBM Plex Mono
- Column widths: **frozen** — never resize on data update
- Row height: fixed — never expand on value change
- Sticky headers: `position: sticky; top: 0`

### Live Update Rules

- Never rebuild full table bodies for polling/SSE updates.
- Use keyed row patching:
  - Add row only when key is new.
  - Remove row only when key is missing.
  - Update cell text/content only when changed.
- Keep row identity stable with `data-id`/`data-row-key`.
- Preserve operator context: scroll position, input focus, open menus.
- Empty/loading/error states should use a single placeholder row, not full table replacement loops.

### Table CSS

```css
.dashboard-enterprise .tactical-table thead th {
    font-size: var(--t-table-header);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--e-text-secondary);
    background: rgba(12, 15, 22, 0.97);
    border-bottom: 1px solid var(--e-border-strong);
    padding: 0.46rem 0.60rem;
    position: sticky;
    top: 0;
    z-index: 2;
    white-space: nowrap;
}

.dashboard-enterprise .tactical-table tbody td {
    padding: 0.40rem 0.60rem;
    font-size: 0.80rem;
    color: var(--e-text-secondary);
    border-bottom: 1px solid var(--e-border);
    line-height: 1.3;
    font-variant-numeric: tabular-nums;
}

.dashboard-enterprise .tactical-table tbody td.metric {
    text-align: right;
    font-family: var(--t-mono);
    font-size: 0.78rem;
}

.dashboard-enterprise .tactical-table tbody tr:nth-child(even) {
    background: var(--e-bg-row-alt);
}

.dashboard-enterprise .tactical-table tbody tr:hover {
    background: var(--e-bg-row-hover);
    /* no box-shadow, no transform */
}
```

### Availability Cells — Context Required

Availability color **alone is insufficient**. Hover tooltip must expose:

```
Last failure: 2025-11-14 03:42 UTC
Total downtime: 4h 12m
Consecutive failures: 7
```

Background tiers (muted, no vivid gradients):

| Tier | Range | Background |
|---|---|---|
| Excellent | 99–100% | `rgba(32, 201, 151, 0.18)` |
| Good | 95–98% | `rgba(56, 139, 192, 0.18)` |
| Warning | 90–94% | `rgba(255, 193, 7, 0.18)` |
| Bad | < 90% | `rgba(220, 53, 69, 0.20)` |
| Unknown | — | Hatched pattern, no color |

---

## 9. Animation System — Operational Constraints

### Allowed Durations

| Type | Range | Token |
|---|---|---|
| Micro (hover, state swap) | 120–150ms | `--a-micro` |
| Transition (panel, tab) | 150–220ms | `--a-transition` |
| Panel Expand | 260–360ms | `--a-panel` |
| Maximum (anything) | ≤ 400ms | `--a-max` |

### Allowed Easings

```css
ease          /* default transitions */
cubic-bezier(0.4, 0, 0.2, 1)   /* panel open/close only */
ease-out      /* exit transitions */
```

### Forbidden

| Animation | Reason |
|---|---|
| `animation: * infinite` | Stress-inducing, distracting |
| Pulse / glow loops | Induces false urgency |
| Bounce easing | Non-operational feel |
| `scale > 1.02` | Layout disruption |
| Flashing on state change | Seizure risk, operationally confusing |
| Neon box-shadow | Decorative, not diagnostic |
| State change animation | All state changes must be **instant** |

### Panel Expand Pattern (Only Allowed Complex Animation)

```css
#device-breakdown {
    max-height: 0;
    opacity: 0;
    transform: translateY(-8px);
    overflow: hidden;
    transition:
        max-height var(--a-panel) cubic-bezier(0.4, 0, 0.2, 1),
        opacity    var(--a-transition) ease,
        transform  var(--a-panel) ease;
}

#device-breakdown.is-active {
    max-height: 3000px;
    opacity: 1;
    transform: translateY(0);
}
```

---

## 10. Charts — Diagnostic Only

Charts are **supporting context** for metrics. Numbers dominate. Charts never replace numbers.

### Rules

```javascript
// ✅ Always update, never recreate
chart.data.datasets[0].data = newData;
chart.update('none'); // no animation on data update

// ❌ Never do this
chart.destroy();
new Chart(ctx, config);
```

### Chart Style Tokens

```javascript
const CHART_DEFAULTS = {
    gridColor:      'rgba(148, 163, 184, 0.08)',
    tickColor:      '#8a97a6',
    criticalLine:   '#dc3545',
    warningLine:    '#ffc107',
    healthyLine:    'rgba(32, 201, 151, 0.70)',
    sparklineAlpha: 0.55,   // ≤ 0.6 — sparklines are always secondary
    fillAlpha:      0.08,   // fill under line — very subtle
};
```

### Chart Anti-Patterns

- ❌ Dominant gradient fills (≥ 0.4 alpha)
- ❌ Thick bright lines (> 2px on sparklines)
- ❌ Animated chart entry on data update
- ❌ Pie charts for status breakdown (use bar or donut with muted palette)

---

## 11. Badges & Status Indicators

### Badge Spec

```css
.dashboard-enterprise .tactical-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    border-radius: 3px;         /* rectangular, not pill — more authoritative */
    border: 1px solid transparent;
    font-family: var(--t-mono);
    font-size: 0.60rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 0.18rem 0.48rem;
    line-height: 1;
}

.tactical-badge-critical {
    background: var(--s-critical-bg);
    border-color: var(--s-critical-bd);
    color: #f5a0a8;
}

.tactical-badge-warning {
    background: var(--s-warning-bg);
    border-color: var(--s-warning-bd);
    color: #f2d47a;
}

.tactical-badge-healthy {
    background: var(--s-healthy-bg);
    border-color: var(--s-healthy-bd);
    color: #7acfb8;
}

.tactical-badge-offline {
    background: var(--s-offline-bg);
    border-color: var(--s-offline-bd);
    color: #9aabb8;
}
```

### Status Dot

```css
.status-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
}
.status-dot.critical { background: var(--s-critical); }
.status-dot.warning  { background: var(--s-warning); }
.status-dot.healthy  { background: var(--s-healthy); }
.status-dot.offline  { background: var(--s-offline); }
.status-dot.unknown  { background: var(--s-unknown); }
```

---

## 12. Error Handling

All API errors must route to `#global-error`. Silent failures are prohibited.

```css
#global-error {
    display: none;
    background: var(--s-critical-bg);
    border: 1px solid var(--s-critical-bd);
    border-left: 3px solid var(--s-critical);
    border-radius: 4px;
    padding: var(--e-space-3) var(--e-space-4);
    font-family: var(--t-mono);
    font-size: 0.72rem;
    color: #f5a0a8;
    margin-bottom: var(--e-space-3);
}

#global-error.visible { display: block; }
```

Components must not display local error states. All errors surface globally.

---

## 13. Metric Formatting

**Never show raw bytes or raw seconds.**

| Metric | Format | Example |
|---|---|---|
| Percent | `XX.X%` | `82.4%` |
| Bytes | Auto-scaled | `1.4 GB`, `842 MB`, `12.3 KB` |
| Network rate | Auto-scaled | `4.2 MB/s`, `840 KB/s` |
| Latency | `ms` | `14.2 ms` |
| Time (absolute) | UTC ISO | `14:32:07 UTC` |
| Time (relative) | Human | `2m 14s ago` |
| Uptime | `Xd Xh Xm` | `14d 6h 32m` |
| Load avg | 2 decimal | `1.24` |
| Count | Integer | `247` |

---

## 14. Panel Surfaces

### Base Panel

```css
.dashboard-enterprise .tactical-card,
.dashboard-enterprise .breakdown-panel,
.dashboard-enterprise .alerts-container,
.dashboard-enterprise .fleet-table-card,
.dashboard-enterprise .enterprise-panel {
    background: var(--e-bg-panel);
    border: 1px solid var(--e-border-panel);
    border-radius: 6px;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.28);
}
```

### Panel Header

```css
.dashboard-enterprise .card-header,
.dashboard-enterprise .panel-header {
    padding: var(--e-space-2) var(--e-space-4);
    border-bottom: 1px solid var(--e-border);
    background: transparent;
    font-size: var(--t-kpi-label);
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--e-text-secondary);
    display: flex;
    align-items: center;
    justify-content: space-between;
}
```

### Elevated Panel (Critical State)

```css
/* Applied when critical alerts > 0 */
.alerts-container.alerts-elevated {
    border-color: var(--e-border-strong);
    /* no glow, no shadow expansion */
}
```

---

## 15. Buttons & Controls

```css
.dashboard-enterprise .tactical-btn-outline {
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid var(--e-border);
    color: var(--e-text-secondary);
    font-size: 0.72rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    padding: 0.32rem 0.72rem;
    border-radius: 4px;
    transition: border-color var(--a-micro) ease, background var(--a-micro) ease;
    cursor: pointer;
}

.dashboard-enterprise .tactical-btn-outline:hover {
    border-color: var(--e-border-strong);
    background: rgba(148, 163, 184, 0.12);
    color: var(--e-text-primary);
    /* no transform, no scale, no shadow */
}

.dashboard-enterprise .tactical-btn-outline.active {
    border-color: var(--e-border-strong);
    background: rgba(148, 163, 184, 0.16);
    color: var(--e-text-primary);
}

/* Tab buttons — active is NOT green */
.dashboard-enterprise .tabs button.active {
    border-color: var(--e-border-strong);
    background: rgba(148, 163, 184, 0.18);
    color: var(--e-text-primary);
    /* ❌ NEVER green — green active tabs are prohibited */
}
```

### Form Inputs

```css
.dashboard-enterprise .tactical-input,
.dashboard-enterprise .tactical-select {
    height: 32px;
    background: rgba(9, 13, 19, 0.80);
    border: 1px solid var(--e-border);
    border-radius: 4px;
    color: var(--e-text-primary);
    font-family: var(--t-sans);
    font-size: 0.78rem;
    padding: 0 0.62rem;
    transition: border-color var(--a-micro) ease;
}

.dashboard-enterprise .tactical-input:focus,
.dashboard-enterprise .tactical-select:focus {
    border-color: var(--e-border-strong);
    outline: none;
    box-shadow: none;
}
```

---

## 16. Dividers

```css
.dashboard-enterprise .enterprise-divider {
    border: none;
    border-top: 1px solid var(--e-border);
    margin: var(--e-space-4) 0;
}

/* Section separator with fade effect */
.dashboard-enterprise .enterprise-divider-fade {
    border: none;
    height: 1px;
    background: linear-gradient(
        90deg,
        transparent,
        var(--e-border-strong) 20%,
        var(--e-border-strong) 80%,
        transparent
    );
    margin: var(--e-space-4) 0;
}
```

---

## 17. Responsive Breakpoints

| Breakpoint | Behavior |
|---|---|
| `≥ 1400px` | Full 6-column KPI grid, dual-column breakdown |
| `1200–1400px` | 5-column KPI, dual-column breakdown |
| `992–1200px` | 4-column KPI, single-column breakdown |
| `768–992px` | 2-column KPI, single-column all |
| `< 768px` | 1-column, all panels stacked |

Layout must **never break** at any breakpoint. Test with live data enabled.

---

## 18. Changes from v4.0

| Area | v4.0 | v5.0 |
|---|---|---|
| Token `--e-bg-base` | Not defined | Added `#070a10` |
| Token `--e-text-dim` | Not defined | Added `#5e6b78` |
| Status backgrounds | Inconsistent | Full set: `-bg`, `-bd` per status |
| Animation tokens | Hardcoded values | Full `--a-*` token set |
| Typography tokens | Partial | Full `--t-*` token set |
| Badge border-radius | `999px` (pill) | `3px` (rectangular — more authoritative) |
| KPI hover scale | Inconsistent (`-2px`) | Fixed to `-1px` (stability rule) |
| `--s-degraded` | Missing | Added `#fd7e14` |
| Bootstrap color overrides | Partial | All `text-success/danger/warning` replaced |
| Panel border radius | `10px` | `6px` (more utilitarian) |
| Chart fill alpha | Undefined | Hard limit `0.08` |

---

## 19. Pre-Ship Checklist

### Tokens

- [ ] No hardcoded hex values in CSS
- [ ] No hardcoded hex values in JavaScript
- [ ] All `--s-*` tokens used for status colors
- [ ] All `--e-*` tokens used for spacing/surfaces
- [ ] No `!important` outside `.dashboard-enterprise`

### Typography

- [ ] KPI values: `1.2rem`, IBM Plex Mono
- [ ] KPI labels: `0.63rem`, uppercase, `0.12em+` letter-spacing
- [ ] Table headers: `0.62rem`, uppercase
- [ ] All numeric fields: `tabular-nums`
- [ ] All metric values: right-aligned

### Animation

- [ ] All transitions ≤ 400ms
- [ ] No `ease-in-out` bounce substitutes
- [ ] No infinite loops or keyframes with `infinite`
- [ ] State changes are instant (no transition on data update)
- [ ] Charts update with `.update('none')`

### Monitoring Compliance

- [ ] Every KPI card has time context visible
- [ ] Global Status Strip shows poll interval + duration
- [ ] Error banner `#global-error` is wired to all API calls
- [ ] No local silent error states in components
- [ ] No green active tabs anywhere
- [ ] No centered numeric metrics anywhere
- [ ] Live tables use keyed row patching (no full tbody rebuild on refresh)
- [ ] Placeholder rows used for loading/empty/error states

### Prohibited Patterns (Final Check)

- [ ] No neon glow shadows
- [ ] No pulse loops
- [ ] No decorative `backdrop-filter: blur()`
- [ ] No vivid gradient fills on availability cells
- [ ] No `chart.destroy()` calls
- [ ] No `tbody.innerHTML = rows.map(...).join('')` in live-update paths
- [ ] No layout shift reproducible during polling

---

## 20. Final Principle

> This interface is not designed to impress.
>
> It is designed to **reduce cognitive load**, **surface operational risk**,
> **remain stable under live polling**, and **support fast, confident decisions**.
>
> **If a visual decision increases drama — it is wrong.**
> **If a visual decision increases clarity — it is correct.**

---

*Version 5.0 — NMS Enterprise Edition*
*Update this document whenever tokens change. The dashboard remains the reference implementation.*

## 0.2 Tracking Device Management Page Contract

- `/tracking` is incremental and must remain flow-safe for adjacent pages.
- The **stored device list is the primary surface**; discovery and sync are secondary.
- Use a prominent **Add Device** action in the stored-list header (modal-based add/edit remains the current interaction).
- Keep tracking page assets scoped and modular:
  - `templates/tracking/device_tracking.html`
  - `static/js/tracking/device_tracking.js`
  - `static/css/tracking/device_tracking.css`
- Frontend network calls on this page must:
  - use `credentials: 'same-origin'`
  - parse `content-type` before JSON parsing
  - show controlled UI errors for non-JSON/HTTP failures (no parser crashes, no browser `alert()` usage)
- Preserve existing tracking route contracts for scan/sync/save/delete in this increment.

## 0.3 Live Tracking Error-Handling Contract

- `/tracking/live` uses a shared `fetchJsonSafe()` transport helper for all JSON API calls.
- The helper must:
  - always send `credentials: 'same-origin'`
  - validate `content-type` before parsing
  - surface non-JSON bodies as controlled errors with short snippets
  - map `401/404/409/5xx` into actionable UI messages
- The page must expose a persistent dismissible top-level alert zone for session/backend/transport failures.
- Device-level polling failures (for example agent unreachable `503`) should degrade row state gracefully without breaking the poll loop.
