# NOC Site Dashboard Redesign

**Date:** 2026-06-05
**Branch:** feat/floor-plan-geotagging
**Status:** Approved — ready for implementation

---

## Problem

The current site dashboard (`/sites/<id>/dashboard`) has three significant issues:

1. **Stale data** — online/offline status is rendered at page-load with no age check. A device last pinged 3 hours ago shows "Online".
2. **Wrong information hierarchy** — the page leads with a full "Recent Alerts" table that dominates the view. KPIs are secondary. There is no concept of departments as a grouping unit.
3. **No live feedback** — operators cannot tell whether data is current without reloading the page.

The live-polling infrastructure (scanner → `DeviceScanHistory` → `/api/sites/<id>/dashboard-stats` → JS poll loop) was built in the previous session and is already working. This spec covers the UI redesign that replaces the old static layout.

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Layout hierarchy | Three-tier: KPIs → Dept cards → Expandable dept panels | Gives executive summary, operational grouping, and device visibility without a giant flat list |
| Dept panel default state | Unhealthy depts open, healthy depts collapsed | Draws attention to problems; avoids information overload on healthy sites |
| Per-device detail | Modal with full snapshot (network + server health + active alerts) | Operators can triage without navigating to the device page |
| Alert placement | Active alert summary banner on dashboard; full table on dedicated Alerts page | Keeps the dashboard operational without becoming an alert log |
| Acknowledgement vs resolution | Use `resolved` only; no fake `acknowledged_at` unless the model adds it | Avoids misleading operators about alert state |

---

## Page Structure

### `/sites/<id>/dashboard`

```
Site Header
  Site name · address · timezone
  [Floor Plans] [Back to Sites]

Freshness Bar
  ● Live · 14:32:01  (amber/red when stale)

─── KPI Cards (row of 4) ──────────────────────────────
  [Total Devices]  [Online %]  [Offline]  [Active Alerts]

─── Active Alerts Banner (only shown when active alerts > 0) ──
  ⚠ Active Alerts: 4 unresolved across 2 departments
  [View all alerts →]

─── Department Health Score Cards (compact grid) ──────
  [IT  92%]  [Finance 80%]  [HR 75% ⚠]  [Ops 92%]
  Each card: dept name · health % · online/offline count · alert chip

─── Search ─────────────────────────────────────────────
  🔍 Search devices by name, IP, or type...  (client-side filter)

─── Expandable Department Panels ───────────────────────
  ▼ HR  (unhealthy — auto-open)
     ● ws-hr-01    192.168.2.10   4ms  0%
     ● ws-hr-02    192.168.2.11   5ms  0%
     ○ server-hr-01  192.168.2.5   offline   [2 alerts]
     + 9 more...

  ▶ IT  (healthy — collapsed by default)
  ▶ Finance  (healthy — collapsed)
  ▶ Operations  (healthy — collapsed)
```

### Department Panel — Expanded Device Row Structure

```
[status dot]  [device name ↗]  [IP]  [type]  [dept]  [ping]  [loss]  [last ping age]  [alert chip?]
```

**Click targets:**
- Row click (anywhere except name/chip) → opens device modal
- Device name or `↗` external icon → navigates to `/devices/<id>/details` (new tab)
- Alert chip → opens device modal scrolled/focused to alerts section

**Default sort per panel:** offline/alerting devices floated to top, then by name alphabetically.

---

## Per-Device Alert Modal

Triggered by clicking a device row. Closed by clicking outside or pressing Escape.

```
┌─ [status dot] server-hr-01  [OFFLINE]                    ✕ ─┐
│  HP ProLiant · 192.168.2.5 · HR Department · Floor 2        │
│                                                              │
│  ── Network ──────────────────────────────────────────────  │
│  Status: OFFLINE     Ping: timeout     Packet loss: 100%    │
│  Last scan: 2m 14s ago  (stale — amber indicator)           │
│                                                              │
│  ── Server Health (last agent push) ──────────────────────  │
│  CPU:  91%  [████████████░]  WARN                           │
│  RAM:  72%  [█████████░░░░]  OK                             │
│  Disk: 45%  [██████░░░░░░░]  OK                             │
│  (if no agent data: "No health data available for this       │
│   device. Agent may not be installed.")                      │
│                                                              │
│  ── Active Alerts (2) ─────────────────────────────────── │
│  [CRIT] Ping timeout — 5 consecutive failures      14:32   │
│  [WARN] CPU >90% sustained 15min                   14:18   │
│                                                              │
│  [Full device page ↗]  [Open floor plan ↗]  [Ping now]     │
└──────────────────────────────────────────────────────────────┘
```

**Action buttons:**
- **Full device page ↗** — navigates to `/devices/<id>/details` (always shown)
- **Open floor plan ↗** — navigates to `/sites/<id>/floor-plans` with the device pre-selected (shown only if the device has a floor plan placement; check via `FloorPlanDevice` relationship)
- **Ping now** — calls existing manual-ping API; button shows spinner then refreshes modal data

**Empty/error states in modal:**
- No agent data: show "No health data — agent not installed or not reporting"
- No active alerts: show "No active alerts for this device"
- Modal data fails to load: show "Unable to load device data. Retry?"

---

## Dedicated Alerts Page

### `/sites/<id>/alerts`

Sidebar link: **Alerts** added as a global sidebar entry (visible at all times, not only when on a site page). The link navigates to `/alerts` — a global alerts page filterable by site. When accessed from within a site dashboard, the site filter pre-selects the current site. Badge shows total active alert count across all sites; goes away when count reaches 0.

```
Page Header
  Site name › Alerts

Filter Bar
  [All Depts ▼]  [All Severity ▼]  [Active | Resolved | All]  [Date range]
  🔍 Filter by device name or message...

Alert Table
  Severity | Device | Dept | Message | Metric | Time | Status | Action

  [CRIT]  server-hr-01  HR  Ping timeout  connectivity  14:32  Active  [Resolve]
  [WARN]  ws-fin-09     Finance  CPU >90%  cpu  14:18  Active  [Resolve]
  [INFO]  ap-floor2     IT  High latency  latency  11:04  Resolved  —
```

**"Resolve" action:** Calls `PATCH /api/alerts/<id>/resolve` which sets `DashboardEvent.resolved = True` and `DashboardEvent.resolved_at = utcnow()`. The button label is **Resolve**, not Acknowledge — these are different concepts. Do not use "acknowledge" in the UI unless `acknowledged_at` and `acknowledged_by` fields are added to `DashboardEvent`.

**Live polling:** same interval as dashboard; badge in sidebar updates automatically.

---

## API Endpoints

Three focused endpoints — do not add dept aggregates or alert rows to `dashboard-stats`.

### `GET /api/sites/<id>/dashboard-stats`

Returns KPI numbers + per-device state + dept health aggregates (online/offline/alert count per dept). Used by the JS polling loop to update KPI cards, dept score cards, device row badges, and the freshness bar.

```json
{
  "stats": { "device_count": 48, "online_count": 41, "offline_count": 7, "warning_count": 3 },
  "dept_aggregates": [
    { "dept_id": 1, "dept_name": "IT", "total": 13, "online": 12, "offline": 1, "alerts": 0, "health_pct": 92 },
    { "dept_id": 2, "dept_name": "HR", "total": 12, "online": 9, "offline": 3, "alerts": 2, "health_pct": 75 }
    // health_pct = round(online / total * 100) if total > 0 else 0
    // A dept is "unhealthy" (panel auto-opens) when health_pct < 100 OR alerts > 0
  ],
  "devices": [
    { "device_id": 5, "state": "offline", "ping_ms": null, "packet_loss": null, "last_scan_at": "2026-06-05T14:32:01" }
  ],
  "active_alert_count": 4,
  "monitoring_interval_s": 15,
  "generated_at": "2026-06-05T14:34:12"
}
```

### `GET /api/sites/<id>/alerts`

Returns alert rows for the Alerts page. Supports query params: `dept_id`, `severity`, `status` (active/resolved/all), `limit`, `offset`.

```json
{
  "alerts": [
    {
      "alert_id": 201,
      "severity": "CRITICAL",
      "device_id": 5,
      "device_name": "server-hr-01",
      "device_ip": "192.168.2.5",
      "dept_name": "HR",
      "metric_name": "connectivity",
      "message": "Ping timeout — 5 consecutive failures",
      "timestamp": "2026-06-05T14:32:01",
      "resolved": false,
      "resolved_at": null
    }
  ],
  "total": 4,
  "active_count": 4
}
```

### `GET /api/sites/<id>/device/<device_id>/modal`

Returns full device snapshot for the modal. Queries: latest scan record, latest server health log, active alerts for this device, floor plan placement.

```json
{
  "device": {
    "device_id": 5,
    "device_name": "server-hr-01",
    "device_type": "Server",
    "device_ip": "192.168.2.5",
    "dept_name": "HR"
  },
  "network": {
    "state": "offline",
    "ping_ms": null,
    "packet_loss": 100.0,
    "last_scan_at": "2026-06-05T14:32:01"
  },
  "health": {
    "cpu_pct": 91.2,
    "memory_pct": 72.1,
    "disk_pct": 45.0,
    "recorded_at": "2026-06-05T14:31:55",
    "available": true
  },
  "active_alerts": [
    { "alert_id": 201, "severity": "CRITICAL", "message": "Ping timeout", "timestamp": "2026-06-05T14:32:01" }
  ],
  "floor_plan_placement": {
    "floor_plan_id": 3,
    "floor_plan_name": "Floor 2",
    "has_placement": true
  }
}
```

---

## Empty and Error States

All UI sections must handle these states explicitly:

| Section | Empty state | Error state |
|---|---|---|
| KPI cards | Show `0` values with muted color | Show `—` with amber freshness dot |
| Dept score cards | "No departments assigned to this site" | Skeleton cards with error icon |
| Active alerts banner | Banner hidden (only shows when alerts > 0) | — |
| Dept panel (no devices) | "No devices in this department" (inside panel) | — |
| Device rows | — | "Unable to load device data" |
| Modal — server health | "No health data — agent not reporting" | Same |
| Modal — active alerts | "No active alerts for this device" | — |
| Alerts page (no alerts) | "No alerts match your current filters" | "Unable to load alerts. Retry?" |
| Freshness bar | "Loading live data…" on first load | Amber dot + "Update failed — retrying…" |

**Stale data indicator** (JS-side, not server-side):
- `< 2×` monitoring interval since last scan: normal (no indicator)
- `2–5×` interval: amber text + amber freshness dot
- `> 5×` interval: red text + red freshness dot

---

## Files to Create or Modify

| File | Change |
|---|---|
| `templates/sites/dashboard.html` | Full rewrite of body sections — new three-tier layout |
| `templates/alerts.html` | New — global Alerts page (filterable by site) |
| `templates/base.html` | Add Alerts sidebar link with live badge (global, always visible) |
| `static/css/sites.css` | Add: dept score card, dept panel, search box, device row click targets, modal, alerts page styles |
| `static/js/site_dashboard.js` | Extend: dept panel expand/collapse, search filter, modal open/close/load, active alert banner, dept score card update |
| `static/js/alerts.js` | New — filter bar, alert table live-poll, resolve action |
| `routes/sites.py` | Enhance `dashboard-stats` (add dept_aggregates + active_alert_count); add `device/<device_id>/modal` endpoint |
| `routes/alerts.py` | New Blueprint — `GET /alerts` (HTML), `GET /api/alerts` (JSON, filterable by site/dept/severity/status), `PATCH /api/alerts/<id>/resolve` |

**No changes to:**
- `services/dashboard_availability.py` — already exposes what's needed
- `models/` — no schema changes (we use `resolved` as-is; no fake acknowledge fields)
- `services/network_scanner.py` / `services/scheduler.py` — live data pipeline is already correct

---

## Out of Scope

- Dept-level drill-down page (separate feature; the modal + "View all" link covers the need)
- Alert notification / push (separate feature)
- Historical trend sparklines in dept cards (nice-to-have; add after core is shipped)
- Bulk resolve on the Alerts page (add in a follow-up)
