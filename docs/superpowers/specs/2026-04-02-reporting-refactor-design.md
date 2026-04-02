# Reporting Refactor — 3-Table PDF Structure
**Date:** 2026-04-02  
**Status:** Approved  
**Scope:** Server Fleet, Workstation Fleet, Device Inspector PDF reports

---

## Problem Statement

Current PDF reports (Device Inspector, Server Uptime, Workstation Uptime) render data as per-device "card" blocks that mix availability, ICMP metrics, and agent telemetry into a single unstructured layout. This produces:

- Cluttered, hard-to-scan output
- No clear separation between SLA compliance, network health, and diagnostic context
- Missing fields: `min_latency_ms`, `expected_scans`, `timeout_pct`, `data_confidence`
- Uptime % and Uptime Hours crammed into the same cell

The fix stays within ReportLab (existing dependency, already solid) and restructures layout from cards → 3 clean column tables per fleet section.

---

## Architecture

### Current Pipeline
```
DB queries → canonical rows (18 fields) → enterprise_pdf_service → per-device cards
```

### New Pipeline
```
DB queries → canonical rows (18 fields) → ReportMetricsEnricher → enriched rows (27 fields) → 3 × build_fleet_table()
```

### Files Changed

| File | Change Type | Description |
|---|---|---|
| `services/reporting/base.py` | Additive (+1 line) | Add `func.min(ping_time_ms)` to `_raw_scan_uptime_rows()` |
| `services/report_metrics_enricher.py` | New file | Enricher class — computes 9 new fields from canonical rows |
| `services/enterprise_pdf_service.py` | Restructure | Replace card loops with 3 `build_fleet_table()` calls per fleet section |
| `routes/reports.py` | Wire-up | Call enricher between report build and PDF generation |
| `tests/unit/services/test_report_metrics_enricher.py` | New test file | 14 unit tests for enricher in isolation |

### What Does NOT Change
- `core_metrics_service.py` — untouched
- Canonical 18-field row contract — untouched
- `enterprise_report_service.py` — untouched
- All 452 existing tests — must stay green

---

## Data Layer

### New Field: `min_latency_ms`
**Source:** `services/reporting/base.py` — `_raw_scan_uptime_rows()`  
**Change:** Add one aggregate alongside existing `avg_latency` and implicit `max`:
```python
func.min(combined.c.ping_time_ms).label("min_latency")
```
Filtered with same `CASE WHEN ping_time_ms BETWEEN 0 AND 60000` guard to exclude sentinel values.

### New Field: `expected_scans`
**Source:** Computed in enricher — no DB query.
```python
period_seconds = (end_date - start_date).total_seconds()
expected_scans = int(period_seconds / interval_seconds)  if interval_seconds > 0  else None
# Example: 30 days, 300s interval → 8,640 expected pings
```
`interval_seconds` read once from `AppSettings.monitoring_interval_seconds` (global setting, default 300s).

### New Field: `actual_scans`
**Source:** Alias of existing `total_scans` in canonical row. No new query.

### New Field: `timeout_pct`
**Source:** Computed in enricher from existing `timeout_count`.
```python
timeout_pct = (timeout_count / expected_scans) * 100  if expected_scans and expected_scans > 0  else None
```

### New Field: `data_confidence`
**Source:** Computed in enricher from `actual_scans` vs `expected_scans`.
```python
if expected_scans is None or expected_scans == 0 or actual_scans is None:
    confidence = "NO_DATA"
elif actual_scans >= expected_scans * 0.90:
    confidence = "HIGH"
elif actual_scans >= expected_scans * 0.70:
    confidence = "MEDIUM"
else:
    confidence = "LOW"
```

### New Field: `downtime_pct`
**Source:** Computed in enricher.
```python
downtime_pct = round(100.0 - uptime_pct, 2)  if uptime_pct is not None  else None
```

### New Field: `ping_interval_label`
**Source:** Derived from `interval_seconds` in enricher.
```python
ping_interval_label = f"{interval_seconds // 60} min"  if interval_seconds else "—"
```

### New Field: `uptime_hours`
**Source:** Computed in enricher from `uptime_pct` and period length.
```python
period_hours = (end_date - start_date).total_seconds() / 3600.0
uptime_hours = round((uptime_pct / 100.0) * period_hours, 1)  if uptime_pct is not None  else None
```

### New Field: `agent_status`
**Source:** Derived from existing canonical fields.
- Server fleet rows (`fleet == "server"`) → `"N/A"`
- Tracked device rows → `"Installed"` if `has_agent` is truthy, else `"Offline"`

### Division-by-Zero Safety
Every computed field that involves division uses an explicit `if denominator > 0 else None` guard. The PDF formatter renders `None` as `"—"` via existing `_fmt_num()` / `_fmt_uptime()` helpers.

---

## ReportMetricsEnricher Class

**File:** `services/report_metrics_enricher.py`

```
ReportMetricsEnricher
├── __init__(interval_seconds: int, start_date: datetime, end_date: datetime)
│     Computes expected_scans once; stores interval label.
├── enrich(rows: list[dict]) → list[dict]
│     Iterates rows, calls _enrich_row per row, returns new list.
└── _enrich_row(row: dict, expected_scans: int | None) → dict
      Returns a new dict (shallow copy of input + new fields).
      Never mutates input rows.
```

**Usage in `routes/reports.py`:**
```python
from services.report_metrics_enricher import ReportMetricsEnricher
from services.settings_service import get_monitoring_interval

interval = get_monitoring_interval()
enricher = ReportMetricsEnricher(interval, start_date, end_date)

report["server_rows"]  = enricher.enrich(report["server_rows"])
report["tracked_rows"] = enricher.enrich(report["tracked_rows"])
```

**Usage for Device Inspector (single device):**
```python
report["device_rows"] = enricher.enrich([device_row])
```

**Constraints:**
- Stateless per row — no cross-row state
- `interval_seconds=0` → all expected/timeout fields return `None`
- All new fields added with `None` default — existing PDF `.get("field", "—")` calls never break

---

## PDF Restructure

### Section Structure (per fleet)
```
[KPI strip]
[Exception strip — worst SLA devices]
[TABLE 1 of 3 — Availability & SLA Ledger]       ← build_fleet_table()
[TABLE 2 of 3 — Ping, Latency & Packet Health]   ← build_fleet_table()
[TABLE 3 of 3 — Telemetry & Diagnostic Context]  ← build_fleet_table()
```

### Table 1 — Availability & SLA Ledger
*Focus: Uptime and Downtime only.*

| Column | Width | Source Field |
|---|---|---|
| Device Name | 18% | `device_name` (max 26 chars) |
| IP Address | 11% | `device_ip` |
| Device Role | 10% | `device_type` |
| SLA Tier | 9% | `sla_tier` (colour-coded badge) |
| Uptime % | 9% | `uptime_pct` |
| Uptime (Hrs) | 9% | `uptime_hours` (computed by enricher: `uptime_pct/100 × period_hours`) |
| Downtime % | 9% | `downtime_pct` |
| Downtime (Hrs) | 10% | `downtime_hours` (existing field) |

### Table 2 — Ping, Latency & Packet Health
*Focus: ICMP health and network stability.*

| Column | Width | Source Field |
|---|---|---|
| Device Name | 18% | `device_name` (max 26 chars) |
| Ping Interval | 10% | `ping_interval_label` |
| Avg Latency (ms) | 12% | `avg_latency_ms` |
| Min / Max (ms) | 13% | `min_latency_ms` / `max_latency_ms` (formatted as "X / Y") |
| Packet Loss % | 11% | `avg_packet_loss_pct` |
| Total Timeouts | 11% | `timeout_count` |
| Timeout % | 10% | `timeout_pct` |

### Table 3 — Telemetry & Diagnostic Context
*Focus: Agent status, data confidence, and actionable alerts.*

| Column | Width | Source Field |
|---|---|---|
| Device Name | 18% | `device_name` (max 26 chars) |
| Agent Status | 11% | `agent_status` (colour-coded) |
| Expected Scans | 11% | `expected_scans` |
| Actual Scans | 11% | `actual_scans` |
| Data Confidence | 12% | `data_confidence` (HIGH/MEDIUM/LOW/NO_DATA, colour-coded) |
| Top Violations | 22% | `anomaly_reason` (max 32 chars) |

### Colour Functions (new, added to `enterprise_pdf_service.py`)
- `_agent_color(row)` — green for Installed, red for Offline, grey for N/A
- `_confidence_color(row)` — reuses existing `_CONFIDENCE_COLORS` / `_CONFIDENCE_BG` dicts

### Reused Unchanged
- `build_fleet_table()` — column-spec driven builder
- `_table_label()` — "TABLE N of 3 —" caption
- `_build_exception_strip()` — top-5 worst SLA devices
- `kpi_strip()` — fleet KPI summary bar
- `base_table_style()` — header/body/grid styling
- All `_fmt_*` formatting helpers

---

## Testing

**New file:** `tests/unit/services/test_report_metrics_enricher.py`

| Test | Assertion |
|---|---|
| `test_expected_scans_30_day_300s_interval` | 30d × 300s = 8,640 |
| `test_expected_scans_zero_interval_safe` | `None`, no ZeroDivisionError |
| `test_timeout_pct_formula` | `(count / expected) * 100` |
| `test_timeout_pct_zero_expected_returns_none` | `None` guard |
| `test_data_confidence_high` | actual ≥ 90% of expected → `"HIGH"` |
| `test_data_confidence_medium` | 70–89% → `"MEDIUM"` |
| `test_data_confidence_low` | < 70% → `"LOW"` |
| `test_data_confidence_no_data` | expected = 0 or actual = None → `"NO_DATA"` |
| `test_downtime_pct_computed` | `100 - uptime_pct` |
| `test_downtime_pct_none_uptime_safe` | None input → None output |
| `test_enrich_does_not_mutate_input_rows` | Input dicts unchanged after enrich() |
| `test_enrich_empty_list` | Returns `[]` |
| `test_enrich_server_row_agent_status_na` | Server fleet row → `"N/A"` |
| `test_enrich_tracked_row_agent_status` | Tracked row with agent → `"Installed"` |

**Existing suite:** All 452 tests must pass with zero modifications.

---

## Implementation Caveats

### Column Width Budget
`build_fleet_table()` passes `colWidths` directly to ReportLab's `Table()`. Widths must sum to the full printable width. All three table col-spec lists must distribute **100%** across their columns — no dead-space gap on the right. Redistribute excess to the widest text columns:

| Table | Fix |
|---|---|
| Table 1 | Expand `device_name` 18% → 25%, `downtime_hours` 10% → 16% |
| Table 2 | Expand `device_name` 18% → 26%, `min/max` 13% → 14% |
| Table 3 | Expand `device_name` 18% → 25%, `top_violations` 22% → 29% |

Implementation must verify all three col-spec width lists sum to exactly 100% before merging.

### `min_latency_ms` Sentinel Filter
The `CASE WHEN ping_time_ms BETWEEN 0 AND 60000 ELSE NULL END` guard inside `func.min()` ensures offline devices return SQL `NULL` (→ Python `None`) rather than `0` or `60000`. Verify via unit test: a device with zero online scans must produce `min_latency_ms = None`, not `0`.

### Timezone Consistency in Enricher
`ReportMetricsEnricher.__init__` must normalise both `start_date` and `end_date` to naive UTC before subtraction:
```python
def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

start_date = _to_naive_utc(start_date)
end_date   = _to_naive_utc(end_date)
period_seconds = (end_date - start_date).total_seconds()
```
This prevents `TypeError` when one boundary is tz-aware (API caller) and the other is naive (DB timestamp).

---

## Out of Scope
- Per-device scan intervals (global interval only — option B chosen)
- HTML→PDF migration (deferred, no Playwright/WeasyPrint dependency added)
- Changes to `core_metrics_service.py` or the 18-field canonical row contract
- Any changes to frontend report tabs (HTML reports unchanged)
