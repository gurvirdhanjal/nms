# Reporting Refactor — 3-Table PDF Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cluttered per-device card blocks in PDF reports with three clean column tables per fleet section (Availability & SLA, Ping/Latency, Telemetry), and add `min_latency_ms`, `expected_scans`, `timeout_pct`, and `data_confidence` fields.

**Architecture:** A new `ReportMetricsEnricher` service post-processes canonical rows (leaving the 18-field contract untouched) to add 9 enriched fields. The PDF service replaces per-device card loops with three `build_fleet_table()` calls per section. One additive line in `base.py` adds `min_latency_ms` to the scan aggregation query.

**Tech Stack:** Python 3, Flask, SQLAlchemy, ReportLab 4.x, pytest (SQLite for tests)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `services/reporting/base.py` | Modify line ~231 | Add `func.min(ping_time_ms)` aggregate to scan uptime query |
| `services/report_metrics_enricher.py` | Create | Enrich canonical rows with 9 new fields |
| `routes/reports.py` | Modify | Wire enricher between report build and PDF generation |
| `services/enterprise_pdf_service.py` | Modify (3 sections) | Replace card loops with 3-table layout; add helper functions |
| `tests/unit/services/test_report_metrics_enricher.py` | Create | 14 unit tests for enricher in isolation |

---

## Task 1: Add `min_latency_ms` to scan uptime query

**Files:**
- Modify: `services/reporting/base.py:224-241`
- Test: `tests/unit/services/test_report_formatting.py` (append to existing file)

### Context
`_raw_scan_uptime_rows()` already aggregates `avg_latency` with a `CASE WHEN ping_time_ms BETWEEN 0 AND 60000` guard. We add `func.min()` using the identical guard so offline devices return `NULL` (→ Python `None`) instead of `0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/test_report_formatting.py`:

```python
def test_raw_scan_uptime_rows_min_latency_key_present(app):
    """min_latency_ms must be a key in every row returned by _raw_scan_uptime_rows."""
    from services.reporting.base import ReportingServiceBase
    from datetime import datetime, timedelta
    with app.app_context():
        svc = ReportingServiceBase()
        end = datetime.utcnow()
        start = end - timedelta(days=1)
        rows = svc._raw_scan_uptime_rows(device_ids=[], start_date=start, end_date=end)
        # Empty result is fine — we just need the columns to exist when there is data.
        # Insert a minimal scan row and re-query to verify the key is present.
        from extensions import db
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        dev = Device(device_name="test-min-lat", device_ip="10.0.0.99", device_type="server")
        db.session.add(dev)
        db.session.flush()
        scan = DeviceScanHistory(
            device_ip="10.0.0.99", device_name="test-min-lat",
            status="online", ping_time_ms=12.5, packet_loss=0.0,
            scan_timestamp=start + timedelta(minutes=5),
        )
        db.session.add(scan)
        db.session.flush()
        rows = svc._raw_scan_uptime_rows(
            device_ids=[dev.device_id], start_date=start, end_date=end
        )
        assert len(rows) == 1
        row = rows[0]
        assert hasattr(row, "min_latency_ms") or "min_latency_ms" in dict(row._mapping)
        assert row.min_latency_ms == pytest.approx(12.5, abs=0.1)
        db.session.rollback()
```

- [ ] **Step 2: Run test — verify it fails**

```bash
pytest tests/unit/services/test_report_formatting.py::test_raw_scan_uptime_rows_min_latency_key_present -v
```

Expected: `FAILED` — `AttributeError: min_latency_ms`

- [ ] **Step 3: Add `min_latency_ms` aggregate to `_raw_scan_uptime_rows()`**

In `services/reporting/base.py`, find the `db.session.query(...)` block inside `_raw_scan_uptime_rows()`. After the `avg_latency` label (around line 231), add the `min` aggregate:

```python
            func.avg(
                case(
                    (combined.c.ping_time_ms.between(0, 60000), combined.c.ping_time_ms),
                    else_=literal_column("NULL"),
                )
            ).label("avg_latency"),
            func.min(                                              # ← ADD THIS BLOCK
                case(
                    (combined.c.ping_time_ms.between(0, 60000), combined.c.ping_time_ms),
                    else_=literal_column("NULL"),
                )
            ).label("min_latency_ms"),                            # ← END ADD
            func.avg(
                case(
                    (combined.c.packet_loss.between(0, 100), combined.c.packet_loss),
                    else_=literal_column("NULL"),
                )
            ).label("avg_packet_loss"),
```

- [ ] **Step 4: Run test — verify it passes**

```bash
pytest tests/unit/services/test_report_formatting.py::test_raw_scan_uptime_rows_min_latency_key_present -v
```

Expected: `PASSED`

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
pytest tests/ -x -q
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add services/reporting/base.py tests/unit/services/test_report_formatting.py
git commit -m "feat(reporting): add min_latency_ms aggregate to _raw_scan_uptime_rows"
```

---

## Task 2: Create `ReportMetricsEnricher` — skeleton + period helpers

**Files:**
- Create: `services/report_metrics_enricher.py`
- Create: `tests/unit/services/test_report_metrics_enricher.py`

### Context
The enricher is a pure-Python class with no DB calls. It computes `expected_scans` once from the reporting period and global interval, then applies per-row calculations. Both `start_date` and `end_date` are normalised to naive UTC before subtraction to prevent `TypeError` when one is tz-aware.

- [ ] **Step 1: Write failing tests for the period helpers**

Create `tests/unit/services/test_report_metrics_enricher.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta


# ── Helper: import target ────────────────────────────────────────────────────

def _make_enricher(interval_s=300, start=None, end=None):
    from services.report_metrics_enricher import ReportMetricsEnricher
    end   = end   or datetime(2026, 2, 1, 0, 0, 0)
    start = start or (end - timedelta(days=30))
    return ReportMetricsEnricher(interval_s, start, end)


# ── Period helpers ───────────────────────────────────────────────────────────

def test_expected_scans_30_day_300s_interval():
    """30 days at 5-min interval = 8,640 expected pings."""
    e = _make_enricher(interval_s=300)
    assert e.expected_scans == 8640


def test_expected_scans_1_day_120s_interval():
    """24 hours at 2-min interval = 720 expected pings."""
    end   = datetime(2026, 2, 2, 0, 0, 0)
    start = datetime(2026, 2, 1, 0, 0, 0)
    e = _make_enricher(interval_s=120, start=start, end=end)
    assert e.expected_scans == 720


def test_expected_scans_zero_interval_returns_none():
    """interval_seconds=0 must not raise ZeroDivisionError."""
    e = _make_enricher(interval_s=0)
    assert e.expected_scans is None


def test_ping_interval_label_300s():
    e = _make_enricher(interval_s=300)
    assert e.ping_interval_label == "5 min"


def test_ping_interval_label_120s():
    e = _make_enricher(interval_s=120)
    assert e.ping_interval_label == "2 min"


def test_ping_interval_label_zero():
    e = _make_enricher(interval_s=0)
    assert e.ping_interval_label == "—"


def test_tz_aware_dates_normalised():
    """Timezone-aware start/end must not raise TypeError."""
    aware_end   = datetime(2026, 2, 1, tzinfo=timezone.utc)
    aware_start = aware_end - timedelta(days=1)
    e = _make_enricher(interval_s=300, start=aware_start, end=aware_end)
    assert e.expected_scans == 288   # 24h / 300s
```

- [ ] **Step 2: Run — verify all 7 tests fail**

```bash
pytest tests/unit/services/test_report_metrics_enricher.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: services.report_metrics_enricher`

- [ ] **Step 3: Create `services/report_metrics_enricher.py` with skeleton**

```python
"""
report_metrics_enricher.py — post-processing enricher for PDF report rows.

Takes canonical rows (18-field contract from core_metrics_service) and adds
9 new fields required for the 3-table PDF layout. No DB calls. No mutations.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)


def _to_naive_utc(dt: datetime) -> datetime:
    """Normalise a datetime to naive UTC. Prevents TypeError on aware/naive subtraction."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class ReportMetricsEnricher:
    """Enrich canonical device rows with computed reporting fields.

    Usage:
        enricher = ReportMetricsEnricher(interval_seconds, start_date, end_date)
        enriched_rows = enricher.enrich(report["server_rows"])
    """

    def __init__(self, interval_seconds: int, start_date: datetime, end_date: datetime) -> None:
        start = _to_naive_utc(start_date)
        end   = _to_naive_utc(end_date)
        period_s = (end - start).total_seconds()

        self.period_hours: float   = period_s / 3600.0
        self.interval_seconds: int = int(interval_seconds) if interval_seconds else 0

        if self.interval_seconds > 0:
            self.expected_scans: Optional[int]  = int(period_s / self.interval_seconds)
            self.ping_interval_label: str       = f"{self.interval_seconds // 60} min"
        else:
            self.expected_scans      = None
            self.ping_interval_label = "—"

    def enrich(self, rows: List[dict]) -> List[dict]:
        """Return a new list of enriched row dicts. Input rows are never mutated."""
        return [self._enrich_row(row) for row in rows]

    def _enrich_row(self, row: dict) -> dict:
        """Return a new dict: shallow copy of row + 9 enriched fields."""
        raise NotImplementedError  # implemented in Task 3
```

- [ ] **Step 4: Run period-helper tests — verify they pass**

```bash
pytest tests/unit/services/test_report_metrics_enricher.py -v
```

Expected: 7 tests `PASSED`, the `_enrich_row` NotImplementedError not yet reached.

- [ ] **Step 5: Commit**

```bash
git add services/report_metrics_enricher.py tests/unit/services/test_report_metrics_enricher.py
git commit -m "feat(enricher): add ReportMetricsEnricher skeleton with period helpers"
```

---

## Task 3: Enricher — per-row computed fields

**Files:**
- Modify: `services/report_metrics_enricher.py` (implement `_enrich_row`)
- Modify: `tests/unit/services/test_report_metrics_enricher.py` (append tests)

### Context
`actual_scans` is derived from `monitoring_coverage_pct` already in the canonical row (populated by `_bulk_icmp_coverage`). Workstation rows have `fleet="workstation"` and null ICMP fields — Table 2 will show `"—"` for all ping columns for those rows.

- [ ] **Step 1: Append failing tests**

Add to `tests/unit/services/test_report_metrics_enricher.py`:

```python
# ── Per-row computed fields ──────────────────────────────────────────────────

def _server_row(**overrides):
    base = {
        "fleet": "server",
        "device_name": "srv-01",
        "device_ip": "192.168.1.1",
        "device_type": "Server",
        "uptime_pct": 98.5,
        "downtime_hours": 5.4,
        "sla_tier": "Bronze",
        "avg_latency_ms": 24.3,
        "max_latency_ms": 210.0,
        "min_latency_ms": 8.1,       # added by Task 1
        "avg_packet_loss_pct": 0.5,
        "timeout_count": 12,
        "monitoring_coverage_pct": 95.0,
        "avg_cpu": 45.0,
        "anomaly_reason": None,
    }
    base.update(overrides)
    return base


def test_downtime_pct_computed():
    e = _make_enricher()
    row = e.enrich([_server_row(uptime_pct=98.5)])[0]
    assert row["downtime_pct"] == pytest.approx(1.5, abs=0.01)


def test_downtime_pct_none_uptime_safe():
    e = _make_enricher()
    row = e.enrich([_server_row(uptime_pct=None)])[0]
    assert row["downtime_pct"] is None


def test_uptime_hours_computed():
    """30d period, 98.5% uptime → 98.5% of 720h = 709.2h."""
    e = _make_enricher()  # 30-day window
    row = e.enrich([_server_row(uptime_pct=98.5)])[0]
    assert row["uptime_hours"] == pytest.approx(709.2, abs=0.5)


def test_uptime_hours_none_safe():
    e = _make_enricher()
    row = e.enrich([_server_row(uptime_pct=None)])[0]
    assert row["uptime_hours"] is None


def test_actual_scans_derived_from_coverage_pct():
    """actual_scans = round(coverage_pct / 100 * expected_scans)."""
    e = _make_enricher(interval_s=300)   # expected = 8640
    # monitoring_coverage_pct=95 → actual ≈ 8208
    row = e.enrich([_server_row(monitoring_coverage_pct=95.0)])[0]
    assert row["actual_scans"] == round(95.0 / 100.0 * 8640)


def test_actual_scans_none_when_no_coverage():
    e = _make_enricher()
    row = e.enrich([_server_row(monitoring_coverage_pct=None)])[0]
    assert row["actual_scans"] is None


def test_timeout_pct_formula():
    """timeout_pct = timeout_count / expected_scans * 100."""
    e = _make_enricher(interval_s=300)   # expected = 8640
    row = e.enrich([_server_row(timeout_count=864)])[0]
    assert row["timeout_pct"] == pytest.approx(10.0, abs=0.01)


def test_timeout_pct_zero_expected_returns_none():
    e = _make_enricher(interval_s=0)
    row = e.enrich([_server_row(timeout_count=5)])[0]
    assert row["timeout_pct"] is None


def test_timeout_pct_none_count_returns_none():
    e = _make_enricher()
    row = e.enrich([_server_row(timeout_count=None)])[0]
    assert row["timeout_pct"] is None


def test_data_confidence_high():
    e = _make_enricher(interval_s=300)   # expected = 8640
    row = e.enrich([_server_row(monitoring_coverage_pct=95.0)])[0]
    assert row["data_confidence"] == "HIGH"


def test_data_confidence_medium():
    e = _make_enricher(interval_s=300)
    row = e.enrich([_server_row(monitoring_coverage_pct=75.0)])[0]
    assert row["data_confidence"] == "MEDIUM"


def test_data_confidence_low():
    e = _make_enricher(interval_s=300)
    row = e.enrich([_server_row(monitoring_coverage_pct=50.0)])[0]
    assert row["data_confidence"] == "LOW"


def test_data_confidence_no_data_none_coverage():
    e = _make_enricher(interval_s=300)
    row = e.enrich([_server_row(monitoring_coverage_pct=None)])[0]
    assert row["data_confidence"] == "NO_DATA"


def test_data_confidence_no_data_zero_interval():
    e = _make_enricher(interval_s=0)
    row = e.enrich([_server_row(monitoring_coverage_pct=80.0)])[0]
    assert row["data_confidence"] == "NO_DATA"


def test_ping_interval_label_on_row():
    e = _make_enricher(interval_s=300)
    row = e.enrich([_server_row()])[0]
    assert row["ping_interval_label"] == "5 min"
```

- [ ] **Step 2: Run — verify these tests fail**

```bash
pytest tests/unit/services/test_report_metrics_enricher.py -k "computed or derived or formula or confidence or label_on" -v
```

Expected: all `FAILED` with `NotImplementedError`

- [ ] **Step 3: Implement `_enrich_row()`**

Replace the `raise NotImplementedError` in `services/report_metrics_enricher.py`:

```python
    def _enrich_row(self, row: dict) -> dict:
        """Return a new dict: shallow copy of row + 9 enriched fields."""
        enriched = dict(row)   # shallow copy — never mutates input

        expected = self.expected_scans

        # ── actual_scans (derived from monitoring_coverage_pct) ──────────────
        cov_pct = row.get("monitoring_coverage_pct")
        if cov_pct is not None and expected:
            actual_scans: Optional[int] = round(cov_pct / 100.0 * expected)
        else:
            actual_scans = None

        # ── timeout_pct ───────────────────────────────────────────────────────
        tc = row.get("timeout_count")
        if tc is not None and expected and expected > 0:
            timeout_pct: Optional[float] = round(float(tc) / expected * 100.0, 2)
        else:
            timeout_pct = None

        # ── data_confidence ───────────────────────────────────────────────────
        if expected is None or expected == 0 or cov_pct is None:
            data_confidence = "NO_DATA"
        elif cov_pct >= 90.0:
            data_confidence = "HIGH"
        elif cov_pct >= 70.0:
            data_confidence = "MEDIUM"
        else:
            data_confidence = "LOW"

        # ── downtime_pct ──────────────────────────────────────────────────────
        up = row.get("uptime_pct")
        downtime_pct: Optional[float] = round(100.0 - up, 2) if up is not None else None

        # ── uptime_hours ──────────────────────────────────────────────────────
        uptime_hours: Optional[float] = (
            round((up / 100.0) * self.period_hours, 1) if up is not None else None
        )

        # ── agent_status ──────────────────────────────────────────────────────
        fleet = row.get("fleet", "")
        if fleet == "workstation":
            agent_status = "Installed" if up is not None else "Offline"
        else:
            # Server/infra: agent is present when server_agent telemetry exists (avg_cpu populated)
            agent_status = "Installed" if row.get("avg_cpu") is not None else "N/A"

        enriched.update({
            "actual_scans":        actual_scans,
            "expected_scans":      expected,
            "timeout_pct":         timeout_pct,
            "data_confidence":     data_confidence,
            "downtime_pct":        downtime_pct,
            "uptime_hours":        uptime_hours,
            "ping_interval_label": self.ping_interval_label,
            "agent_status":        agent_status,
            # min_latency_ms already in row from base.py query change (Task 1)
            # Pass through cleanly so PDF can always use row.get("min_latency_ms")
            "min_latency_ms":      row.get("min_latency_ms"),
        })
        return enriched
```

- [ ] **Step 4: Run per-row tests — verify they pass**

```bash
pytest tests/unit/services/test_report_metrics_enricher.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/report_metrics_enricher.py tests/unit/services/test_report_metrics_enricher.py
git commit -m "feat(enricher): implement _enrich_row with all 9 computed fields"
```

---

## Task 4: Enricher — `enrich()` contract tests (mutation guard + agent_status)

**Files:**
- Modify: `tests/unit/services/test_report_metrics_enricher.py` (append)

- [ ] **Step 1: Append final enricher tests**

```python
# ── enrich() contract ────────────────────────────────────────────────────────

def test_enrich_does_not_mutate_input_rows():
    """enrich() must return new dicts; originals must be unchanged."""
    e = _make_enricher()
    original = _server_row()
    original_keys = set(original.keys())
    _ = e.enrich([original])
    assert set(original.keys()) == original_keys   # no new keys on input


def test_enrich_empty_list_returns_empty():
    e = _make_enricher()
    assert e.enrich([]) == []


def test_enrich_server_row_agent_status_na_when_no_cpu():
    e = _make_enricher()
    row = e.enrich([_server_row(avg_cpu=None)])[0]
    assert row["agent_status"] == "N/A"


def test_enrich_server_row_agent_status_installed_when_cpu_present():
    e = _make_enricher()
    row = e.enrich([_server_row(avg_cpu=55.0)])[0]
    assert row["agent_status"] == "Installed"


def test_enrich_workstation_row_agent_installed():
    e = _make_enricher()
    ws_row = _server_row(fleet="workstation", uptime_pct=92.0, avg_cpu=None)
    row = e.enrich([ws_row])[0]
    assert row["agent_status"] == "Installed"


def test_enrich_workstation_row_agent_offline_when_no_uptime():
    e = _make_enricher()
    ws_row = _server_row(fleet="workstation", uptime_pct=None, avg_cpu=None)
    row = e.enrich([ws_row])[0]
    assert row["agent_status"] == "Offline"
```

- [ ] **Step 2: Run — verify all pass**

```bash
pytest tests/unit/services/test_report_metrics_enricher.py -v
```

Expected: all 27 tests `PASSED`.

- [ ] **Step 3: Run full suite**

```bash
pytest tests/ -x -q
```

Expected: all existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/services/test_report_metrics_enricher.py
git commit -m "test(enricher): add enrich() contract tests — mutation guard and agent_status"
```

---

## Task 5: Wire enricher into `routes/reports.py`

**Files:**
- Modify: `routes/reports.py`

### Context
Find the `enterprise_uptime_report()` endpoint in `routes/reports.py`. It calls `build_enterprise_uptime_report()` then `generate_enterprise_pdf()`. Insert the enricher call between them. Also find the Device Inspector endpoint — it passes a `stats` dict differently (not canonical rows), so apply enricher there separately.

- [ ] **Step 1: Locate the PDF generation call**

```bash
grep -n "generate_enterprise_pdf\|generate_device_inspector_pdf\|build_enterprise_uptime" routes/reports.py
```

Note the line numbers for the next step.

- [ ] **Step 2: Add enricher import at top of `routes/reports.py`**

Find the import block at the top of `routes/reports.py`. Add:

```python
from services.report_metrics_enricher import ReportMetricsEnricher
from services.settings_service import get_monitoring_interval
```

- [ ] **Step 3: Wire enricher before `generate_enterprise_pdf()`**

In the `enterprise_uptime_report()` function, find where `report` is built and `generate_enterprise_pdf(report, ...)` is called. Between those two lines insert:

```python
        # Enrich canonical rows with 9 new fields for 3-table PDF layout
        _interval = get_monitoring_interval()
        _enricher = ReportMetricsEnricher(_interval, start_date, end_date)
        if report.get("server_rows"):
            report["server_rows"]  = _enricher.enrich(report["server_rows"])
        if report.get("tracked_rows"):
            report["tracked_rows"] = _enricher.enrich(report["tracked_rows"])
```

`start_date` and `end_date` should already be local variables in the function from the period parsing logic above. Verify by reading the surrounding code before editing.

- [ ] **Step 4: Run the existing report route tests**

```bash
pytest tests/ -k "report" -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add routes/reports.py
git commit -m "feat(routes): wire ReportMetricsEnricher into enterprise PDF generation"
```

---

## Task 6: PDF — Add helper functions for new column specs

**Files:**
- Modify: `services/enterprise_pdf_service.py` (after the existing `_kpi_color` function, around line 273)

### Context
Three new helpers are needed before the column spec constants:
- `_fmt_min_max(row)` — formats "8 / 210" for Min/Max latency column
- `_agent_color(row)` — badge colours for Agent Status column
- `_confidence_color(row)` — reuses existing `_CONFIDENCE_COLORS` / `_CONFIDENCE_BG` dicts

- [ ] **Step 1: Add helpers after `_kpi_color` (around line 273)**

Find the line `def kpi_strip(items: list) -> Table:` in `enterprise_pdf_service.py`. Insert the following block immediately before it:

```python
# ── 3-table layout helpers ────────────────────────────────────────────────────

def _fmt_min_max(row: dict) -> str:
    """Format 'Min / Max' latency cell. Returns '8 / 210' or '— / —'."""
    mn = row.get("min_latency_ms")
    mx = row.get("max_latency_ms")
    mn_s = f"{mn:.0f}" if mn is not None else "—"
    mx_s = f"{mx:.0f}" if mx is not None else "—"
    return f"{mn_s} / {mx_s}"


def _agent_color(row: dict):
    """Return (text_hex, bg_hex) for Agent Status badge cell."""
    status = (row.get("agent_status") or "").upper()
    if status == "INSTALLED":
        return ("#166534", "#DCFCE7")   # green
    if status == "OFFLINE":
        return ("#991B1B", "#FEE2E2")   # red
    return ("#374151", "#F3F4F6")        # grey for N/A / unknown


def _confidence_color_fn(row: dict):
    """Return (text_hex, bg_hex) using the existing _CONFIDENCE_COLORS/_CONFIDENCE_BG dicts."""
    level = (row.get("data_confidence") or "NO_DATA").upper()
    text  = _CONFIDENCE_COLORS.get(level, _CONFIDENCE_COLORS["NO_DATA"])
    bg    = _CONFIDENCE_BG.get(level, _CONFIDENCE_BG["NO_DATA"])
    return (text, bg)
```

- [ ] **Step 2: Add col spec constants after the helpers**

Immediately after the helpers above, add the three column spec lists. All widths sum to 100%.

```python
# ── Fleet table column specs (3-table layout) ─────────────────────────────────
# All width lists must sum to 100% to fill ReportLab's landscape content width.

_COLS_AVAILABILITY = [
    {"header": "Device Name",    "width": "26%", "key": "device_name",    "max_chars": 26, "align": "LEFT"},
    {"header": "IP Address",     "width": "11%", "key": "device_ip",      "align": "LEFT"},
    {"header": "Device Role",    "width": "9%",  "key": "device_type",    "align": "CENTER"},
    {"header": "SLA Tier",       "width": "9%",  "key": "sla_tier",       "align": "CENTER",
     "color_fn": lambda r: _sla_badge_style(r.get("sla_tier", "Unknown"))},
    {"header": "Uptime %",       "width": "9%",  "fmt": lambda r: _fmt_uptime(r.get("uptime_pct")),    "align": "RIGHT"},
    {"header": "Uptime (Hrs)",   "width": "9%",  "fmt": lambda r: _fmt_hours(r.get("uptime_hours")),   "align": "RIGHT"},
    {"header": "Downtime %",     "width": "9%",  "fmt": lambda r: _fmt_uptime(r.get("downtime_pct")),  "align": "RIGHT"},
    {"header": "Downtime (Hrs)", "width": "18%", "fmt": lambda r: _fmt_hours(r.get("downtime_hours")), "align": "RIGHT"},
]  # 26+11+9+9+9+9+9+18 = 100%

_COLS_PING = [
    {"header": "Device Name",       "width": "22%", "key": "device_name",         "max_chars": 26, "align": "LEFT"},
    {"header": "Ping Interval",     "width": "10%", "key": "ping_interval_label",  "align": "CENTER"},
    {"header": "Avg Latency (ms)",  "width": "14%", "fmt": lambda r: _fmt_num(r.get("avg_latency_ms"), ""), "align": "RIGHT"},
    {"header": "Min / Max (ms)",    "width": "16%", "fmt": _fmt_min_max,            "align": "RIGHT"},
    {"header": "Packet Loss %",     "width": "12%", "fmt": lambda r: _fmt_num(r.get("avg_packet_loss_pct"), "%"), "align": "RIGHT"},
    {"header": "Total Timeouts",    "width": "12%", "fmt": lambda r: str(r.get("timeout_count") or "—"),       "align": "RIGHT"},
    {"header": "Timeout %",         "width": "14%", "fmt": lambda r: _fmt_num(r.get("timeout_pct"), "%"),       "align": "RIGHT"},
]  # 22+10+14+16+12+12+14 = 100%

_COLS_TELEMETRY = [
    {"header": "Device Name",      "width": "22%", "key": "device_name",     "max_chars": 26, "align": "LEFT"},
    {"header": "Agent Status",     "width": "12%", "key": "agent_status",    "align": "CENTER", "color_fn": _agent_color},
    {"header": "Expected Scans",   "width": "13%", "fmt": lambda r: str(r.get("expected_scans") or "—"), "align": "RIGHT"},
    {"header": "Actual Scans",     "width": "13%", "fmt": lambda r: str(r.get("actual_scans")   or "—"), "align": "RIGHT"},
    {"header": "Data Confidence",  "width": "14%", "key": "data_confidence", "align": "CENTER", "color_fn": _confidence_color_fn},
    {"header": "Top Violations",   "width": "26%", "key": "anomaly_reason",  "max_chars": 32,   "align": "LEFT"},
]  # 22+12+13+13+14+26 = 100%
```

- [ ] **Step 3: Verify no syntax errors**

```bash
python -c "import services.enterprise_pdf_service; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add services/enterprise_pdf_service.py
git commit -m "feat(pdf): add 3-table helper functions and column spec constants"
```

---

## Task 7: PDF — Replace server fleet card loop with 3 tables

**Files:**
- Modify: `services/enterprise_pdf_service.py` — `_build_server_fleet()` function (~line 1476)

### Context
`_build_server_fleet()` currently ends with a `for r in rows: elems.extend(_build_server_device_card(r, styles))` loop. Replace that loop with three `build_fleet_table()` calls. Keep the KPI strip and exception strip unchanged.

- [ ] **Step 1: Find the exact card loop lines**

```bash
grep -n "_build_server_device_card\|for r in rows" services/enterprise_pdf_service.py
```

Note the line numbers.

- [ ] **Step 2: Replace the card loop in `_build_server_fleet()`**

Find this block in `_build_server_fleet()` (approximately lines 1535-1540):

```python
    # Per-device KeepTogether cards: identity row + ICMP metrics row + agent row
    # Each card is atomic — guaranteed not to split across a page break.
    elems.append(SP_BLOCK)
    for r in rows:
        elems.extend(_build_server_device_card(r, styles))
    return elems
```

Replace with:

```python
    styles_ref = getSampleStyleSheet()

    # ── TABLE 1 of 3 — Availability & SLA Ledger ─────────────────────────────
    elems.append(_table_label("TABLE 1 of 3 — Availability & SLA Ledger", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_AVAILABILITY,
        caption="Uptime and downtime for the reporting period. SLA tier based on uptime %."
    ))

    # ── TABLE 2 of 3 — Ping, Latency & Packet Health ─────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 2 of 3 — Ping, Latency & Packet Health", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_PING,
        caption="ICMP health metrics. Timeout % = timeouts / expected pings × 100."
    ))

    # ── TABLE 3 of 3 — Telemetry & Diagnostic Context ────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 3 of 3 — Telemetry & Diagnostic Context", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_TELEMETRY,
        caption="LOW CONFIDENCE flag = actual scans < 70% of expected. Violations = anomaly_reason."
    ))

    return elems
```

- [ ] **Step 3: Verify no syntax errors**

```bash
python -c "import services.enterprise_pdf_service; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Smoke-test PDF generation (manual)**

Start the dev server (`python web_main.py`), navigate to Reports → Server Fleet → Export PDF. Verify:
- PDF opens without error
- Three labelled tables appear in the Server & Infrastructure Fleet section
- Column headers match spec (Device Name, IP Address, Device Role, SLA Tier, Uptime %, etc.)
- No 15% gap on the right margin

- [ ] **Step 5: Commit**

```bash
git add services/enterprise_pdf_service.py
git commit -m "feat(pdf): replace server fleet card loop with 3-table layout"
```

---

## Task 8: PDF — Replace tracked fleet card loop with 3 tables

**Files:**
- Modify: `services/enterprise_pdf_service.py` — `_build_tracked_fleet()` function (~line 1545)

### Context
`_build_tracked_fleet()` has the same structure as `_build_server_fleet()`. Find its card loop and apply the identical 3-table replacement. Note: workstation rows have `avg_latency_ms=None` and `timeout_count=None` by design — Table 2 will show `"—"` for all ping metrics, which is correct.

- [ ] **Step 1: Find the card loop in `_build_tracked_fleet()`**

```bash
grep -n "_build_tracked_fleet\|for r in rows\|_build_workstation" services/enterprise_pdf_service.py | head -20
```

Note the line range for `_build_tracked_fleet()`.

- [ ] **Step 2: Replace the card loop**

Find the `for r in rows: elems.extend(...)` loop at the end of `_build_tracked_fleet()`. Replace it with the identical 3-table block used in Task 7:

```python
    styles_ref = getSampleStyleSheet()

    # ── TABLE 1 of 3 — Availability & SLA Ledger ─────────────────────────────
    elems.append(_table_label("TABLE 1 of 3 — Availability & SLA Ledger", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_AVAILABILITY,
        caption="Uptime and downtime for the reporting period. SLA tier based on uptime %."
    ))

    # ── TABLE 2 of 3 — Ping, Latency & Packet Health ─────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 2 of 3 — Ping, Latency & Packet Health", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_PING,
        caption="Workstation devices use event-based availability — ICMP columns show — by design."
    ))

    # ── TABLE 3 of 3 — Telemetry & Diagnostic Context ────────────────────────
    elems.append(SP_TABLE_GAP)
    elems.append(_table_label("TABLE 3 of 3 — Telemetry & Diagnostic Context", styles_ref))
    elems.extend(build_fleet_table(
        rows, _COLS_TELEMETRY,
        caption="LOW CONFIDENCE flag = actual scans < 70% of expected. Violations = anomaly_reason."
    ))

    return elems
```

- [ ] **Step 3: Verify no syntax errors**

```bash
python -c "import services.enterprise_pdf_service; print('OK')"
```

- [ ] **Step 4: Smoke-test workstation PDF (manual)**

Export Employee Workstation Fleet PDF. Verify three labelled tables appear. Table 2 shows `"—"` for latency/packet loss/timeout columns — expected and correct.

- [ ] **Step 5: Commit**

```bash
git add services/enterprise_pdf_service.py
git commit -m "feat(pdf): replace tracked fleet card loop with 3-table layout"
```

---

## Task 9: PDF — Device Inspector restructure

**Files:**
- Modify: `services/enterprise_pdf_service.py` — `generate_device_inspector_pdf()` (~line 2589)

### Context
The Device Inspector already has three distinct sections (Availability, Latency & Packet Loss, Agent Telemetry) that are structurally close to the 3-table spec. Changes are targeted:

1. **Availability table (~line 2686):** Add SLA Tier column and use `_fmt_uptime()` formatting. Replace the hardcoded `int(period_hours * 12)` expected-scans calculation with `get_monitoring_interval()`.
2. **Latency table (~line 2736):** Add Ping Interval and Timeout % columns. Min Latency already present.
3. **Agent table (~line 2754):** Rename section to "Telemetry & Diagnostic Context", add Data Confidence field.

- [ ] **Step 1: Replace hardcoded 5-min interval in Device Inspector**

Find line 2650 in `generate_device_inspector_pdf()`:
```python
    expected_scans = int(period_hours * 12)   # 5-min interval = 12/hr
```

Replace with:
```python
    from services.settings_service import get_monitoring_interval as _get_interval
    _interval_s     = _get_interval()
    _interval_per_h = 3600.0 / _interval_s if _interval_s > 0 else 12.0
    expected_scans  = int(period_hours * _interval_per_h)
    _interval_label = f"{_interval_s // 60} min" if _interval_s > 0 else "5 min"
```

- [ ] **Step 2: Add SLA Tier column to Availability table**

Find the `avail_data` list definition (~line 2686). Replace the existing 8-column headers/row:

```python
    tier = (
        "Gold"    if uptime >= SLA_GOLD    else
        "Silver"  if uptime >= SLA_SILVER  else
        "Bronze"  if uptime >= SLA_BRONZE  else
        "Warning" if uptime >= SLA_WARNING else "Critical"
    )
    tc, tbg = _sla_badge_style(tier)
    uptime_h = round(uptime / 100.0 * period_hours, 2)
    timeout_count = stats.get('no_response_count', 0) or 0
    timeout_pct   = round(timeout_count / expected_scans * 100, 2) if expected_scans > 0 else 0.0

    avail_data = [
        [
            _h("Device Role"), _h("SLA Tier"), _h("Uptime %"), _h("Uptime (Hrs)"),
            _h("Downtime %"), _h("Downtime (Hrs)"), _h("Actual Scans"), _h("Expected Scans"),
        ],
        [
            _cell(stats.get('device_type', '—')),
            _cell(tier),
            _cell(_fmt_uptime(uptime)),
            _cell(f"{uptime_h:.1f} h"),
            _cell(f"{(100.0 - uptime):.2f}%"),
            _cell(f"{downtime_h:.2f} h"),
            _cell(f"{total_scans:,}"),
            _cell(f"{expected_scans:,}"),
        ],
    ]
    # Apply SLA badge colour to tier cell (column index 1)
    ts_avail = base_table_style()
    ts_avail.add('BACKGROUND', (1, 1), (1, 1), hex_color(tbg))
    ts_avail.add('TEXTCOLOR',  (1, 1), (1, 1), hex_color(tc))
    ts_avail.add('FONTNAME',   (1, 1), (1, 1), 'Helvetica-Bold')
    _col_avail = [_CONTENT_W / 8] * 8
```

Update the table render below to use `_col_avail`:
```python
    story.append(Table(avail_data, colWidths=_col_avail, hAlign='LEFT',
                       style=ts_avail, repeatRows=1))
```

- [ ] **Step 3: Add Ping Interval and Timeout % to Latency table**

Find the latency section (~line 2736). Replace the existing 5-column latency table with a 6-column version:

```python
    if stats.get('avg_latency') is not None:
        _col_lat = [_CONTENT_W / 6] * 6
        lat_data = [
            [_h("Ping Interval"), _h("Avg Latency"), _h("Min Latency"),
             _h("Max Latency"), _h("Avg Pkt Loss"), _h("Timeout %")],
            [
                _cell(_interval_label),
                _cell(_fmt_num(stats.get('avg_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('min_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('max_latency'),     ' ms')),
                _cell(_fmt_num(stats.get('avg_packet_loss'), '%')),
                _cell(f"{timeout_pct:.2f}%" if expected_scans > 0 else "—"),
            ],
        ]
        story.append(Paragraph('<b>Ping, Latency &amp; Packet Health</b>',
            ParagraphStyle('sec', parent=styles['Normal'], fontName='Helvetica-Bold',
                           fontSize=11, spaceBefore=8, spaceAfter=5,
                           textColor=hex_color(NAVY))))
        story.append(Table(lat_data, colWidths=_col_lat, hAlign='LEFT',
                           style=base_table_style(), repeatRows=1))
        story.append(Spacer(1, 0.5*cm))
```

- [ ] **Step 4: Rename Agent section heading**

Find the `story.append(Paragraph('<b>Agent Telemetry</b>'...` line (~line 2800). Rename to:

```python
        story.append(Paragraph('<b>Telemetry &amp; Diagnostic Context</b>',
```

- [ ] **Step 5: Verify no syntax errors**

```bash
python -c "import services.enterprise_pdf_service; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Smoke-test Device Inspector PDF (manual)**

Navigate to a device's Inspector PDF export. Verify:
- Availability table shows SLA Tier badge, correct uptime/downtime split
- Latency table shows Ping Interval column and Timeout %
- Agent section is titled "Telemetry & Diagnostic Context"

- [ ] **Step 7: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add services/enterprise_pdf_service.py routes/reports.py
git commit -m "feat(pdf): restructure Device Inspector into 3-section layout (availability, ping, telemetry)"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Dynamic ping interval → global `get_monitoring_interval()` (Task 5 wire-up, Task 9 step 1)
- [x] Expected vs actual scans → `ReportMetricsEnricher.expected_scans` + `actual_scans` (Tasks 2-3)
- [x] Timeout % formula → `(timeout_count / expected_scans) * 100` (Task 3)
- [x] Data confidence scoring → `HIGH/MEDIUM/LOW/NO_DATA` with 90%/70% thresholds (Task 3)
- [x] Table 1 Availability & SLA → `_COLS_AVAILABILITY` (Task 6)
- [x] Table 2 Ping, Latency & Packet Health → `_COLS_PING` (Task 6)
- [x] Table 3 Telemetry & Diagnostic Context → `_COLS_TELEMETRY` (Task 6)
- [x] Column widths sum to 100% → verified in each col spec comment (Task 6)
- [x] `min_latency_ms` sentinel guard → `CASE WHEN BETWEEN 0 AND 60000 ELSE NULL` (Task 1)
- [x] Timezone consistency → `_to_naive_utc()` in enricher `__init__` (Task 2)
- [x] Division-by-zero safety → all computed fields guarded (Task 3)
- [x] Device Inspector restructured → Tasks 9 steps 1-4
- [x] Server fleet 3-table → Task 7
- [x] Workstation fleet 3-table → Task 8
- [x] 14 enricher unit tests → Tasks 2-4
- [x] All 452 existing tests must stay green → `pytest tests/ -x -q` in every task
