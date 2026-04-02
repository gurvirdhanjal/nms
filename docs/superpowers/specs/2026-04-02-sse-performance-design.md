# SSE Event Wiring + Performance Improvements — Design Spec

**Date:** 2026-04-02  
**Status:** Approved  
**Scope:** Option B — SSE wiring + scan pipeline + query audit  
**Branch:** local-truth-apr01

---

## Problem Statement

Two SSE event types (`latency_spike`, `interface_threshold`) are subscribed in the dashboard
client (`sseClient.js`) but never published by any server-side code. Separately, four
independent performance issues cause sluggishness across the app:

1. Scan cycle fires 239 individual DB commits — one per device per cycle.
2. Device list routes trigger N+1 lazy-load queries (~700+ queries per page load).
3. TimescaleDB continuous aggregate routing may not be applied consistently in report
   services, causing heavy queries to scan raw hypertables instead of pre-aggregated views.
4. No scan cycle timing or slow-response logging — no visibility into where time is spent.

---

## In-Scope (Option B)

| # | Change | File(s) |
|---|--------|---------|
| 1 | Wire `latency_spike` SSE event | `services/device_monitor.py` |
| 2 | Wire `interface_threshold` SSE event | `services/interface_poller.py` |
| 3 | Batch scan cycle commits (239 → 1) | `services/device_monitor.py` |
| 4 | Fix N+1 queries on device list | `routes/devices.py` |
| 5 | Verify TimescaleDB cagg routing in report services | `services/reporting/health.py`, `services/reporting/executive.py` |
| 5b | Add scan cycle + slow-response performance logging | `services/device_monitor.py`, `routes/reports.py` |

---

## Out-of-Scope (deferred to TODO.md)

See `docs/TODO.md` for Option C items.

---

## Design

### 1. Wire `latency_spike` SSE Event

**File:** `services/device_monitor.py`  
**Location:** Inside `monitor_stored_devices()` scan result loop, after `AlertManager.process_scan_result()`.

**Rationale:** The `AlertManager` 3-strike system is for *persistent alert creation* (writes
`DashboardEvent`, increments `device.latency_strikes`). The SSE `latency_spike` event is a
lighter, *immediate* UI signal — fires on the first breach so the dashboard can highlight the
device without waiting for 3 consecutive strikes. These are separate concerns.

**Threshold source:** `AlertManager._get_icmp_thresholds(device)` — reuses per-device
compliance profile overrides. No new config needed.

**Severity logic:**
- `latency_ms >= latency_critical_ms` → `"critical"`
- `latency_ms >= latency_warning_ms` → `"warning"`

**Payload:**
```json
{
  "event_type": "latency_spike",
  "device_id": 42,
  "ip": "192.168.1.10",
  "name": "DESKTOP-ABC",
  "latency_ms": 245.3,
  "threshold_ms": 200,
  "severity": "warning"
}
```

**Guard:** Only fire when `latency_ms is not None` and device is online (offline devices
already fire `device_update_batch`; mixing the two would double-notify).

---

### 2. Wire `interface_threshold` SSE Event

**File:** `services/interface_poller.py`  
**Location:** Inside `poll_device_interfaces()`, immediately after `rx_util` / `tx_util` are
computed (current lines 244–245).

**Threshold:** Class constant `INTERFACE_UTIL_THRESHOLD_PCT = 80`, overridable via
`app.config['INTERFACE_UTIL_THRESHOLD_PCT']`. Applied independently to RX and TX.

**Direction field:** `"rx"` / `"tx"` / `"both"` depending on which side breached.

**Payload:**
```json
{
  "event_type": "interface_threshold",
  "device_id": 12,
  "interface_name": "GigabitEthernet0/1",
  "if_index": 3,
  "rx_util_pct": 87.4,
  "tx_util_pct": 23.1,
  "threshold_pct": 80,
  "direction": "rx"
}
```

**Note:** SNMP polling is currently paused (no managed switches). This event will not fire
in production until a managed switch is enabled. The code is wired and ready.

---

### 3. Batch Scan Cycle Commits (239 → 1)

**File:** `services/device_monitor.py` — `monitor_stored_devices()`

**Current behaviour:** `db.session.commit()` inside the loop = 239 DB round-trips per scan cycle.

**New behaviour — savepoint-per-device:**

1. **Inside loop:** Wrap each device in `db.session.begin_nested()` (SQLAlchemy SAVEPOINT).
   On `StaleDataError` / `ObjectDeletedError`: rollback to that savepoint only — previously
   processed devices in the same transaction are unaffected. Skip the bad device and continue.

2. **After loop:** Single `db.session.commit()` for the entire batch.

3. **Fallback:** If the final batch commit itself fails (e.g. DB connection drop): rollback,
   then retry all devices individually using the existing per-device commit logic.

**Why savepoints, not plain `flush()`:** A plain `db.session.rollback()` mid-loop would undo
all previously flushed writes since the last commit, losing devices 1–N-1 when device N
fails. `begin_nested()` creates a SQL SAVEPOINT so rollback is scoped to that device only.

**Why `flush()` not just `add()`:** `AlertManager` writes `DashboardEvent` rows and mutates
`device.latency_strikes` within the same session. `flush()` ensures these are visible to
subsequent ORM reads within the same scan cycle without committing.

**Expected gain:** Scan cycle DB overhead drops from ~239 network round-trips to 1.

---

### 4. Fix N+1 Queries on Device List

**File:** `routes/devices.py`  
**Call sites:** Lines 795, 808, 1762 (`Device.query.all()`)

**Fix:** Add `selectinload` for relationships accessed during serialization:

```python
from sqlalchemy.orm import selectinload

Device.query.options(
    selectinload(Device.site),
    selectinload(Device.department),
    selectinload(Device.compliance_profile),
).all()
```

**Why `selectinload` not `joinedload`:** `joinedload` produces a JOIN that multiplies result
rows when a device has to-many relationships. `selectinload` fires one `IN (...)` query per
relationship — safe for both to-one and to-many, and easier to reason about.

**Expected gain:** Device list page query count drops from ~700+ to ~4 (1 devices + 3 relationship loads).

---

### 5. TimescaleDB Continuous Aggregate Routing

**Files:** `services/reporting/health.py`, `services/reporting/executive.py`

**Rule:**
- Range `≤ 24h` → raw `server_health_logs`
- Range `≤ 30d` → `server_health_hourly_cagg`
- Range `> 30d` → `server_health_daily_cagg`

**Action:** Audit every time-range query in both files. Where routing logic is absent or
incorrect, add the branch. No schema or migration changes needed — all four caggs already
exist and are populated.

---

### 5b. Performance Logging

**Scan cycle timing** (`services/device_monitor.py`):  
Log `[DeviceMonitor] scan cycle completed in X.Xs for N devices` at `INFO` after
`monitor_stored_devices()` completes.

**Slow report responses** (`routes/reports.py`):  
Add `before_request` timestamp on the reports blueprint. In `after_request`, if elapsed
time exceeds 500ms, log at `WARNING`:  
`[Reports] slow response: <endpoint> took X.Xs`

---

## Error Handling

| Scenario | Handling |
|----------|----------|
| `latency_spike` broadcast fails | `try/except`, log warning, continue — SSE is best-effort |
| `interface_threshold` broadcast fails | Same as above |
| Batch commit fails | Rollback + per-device retry fallback |
| `selectinload` relationship missing | SQLAlchemy returns `None` for optional FK — no change to existing null-check logic |
| TimescaleDB cagg not refreshed | Query falls back to raw table (existing behaviour) — no change |

---

## Testing

- Unit tests for `latency_spike` payload shape and severity logic
- Unit tests for `interface_threshold` direction field (`rx` / `tx` / `both`)
- Unit test for batch commit fallback path (simulate final commit failure)
- Existing 592 tests must continue to pass — no regressions

---

## What Is NOT Changing

- SSE transport stays as SSE (no WebSockets)
- `AlertManager` 3-strike logic is unchanged
- Waitress thread count stays at 16 (already correct)
- No new dependencies
- No schema migrations
