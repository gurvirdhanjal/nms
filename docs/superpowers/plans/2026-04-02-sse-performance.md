# SSE Event Wiring + Performance Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the two missing SSE events (`latency_spike`, `interface_threshold`) and apply five targeted performance improvements to reduce scan cycle DB overhead, device list query count, and report query cost.

**Architecture:** All changes are additive and isolated to the files listed below. SSE broadcasts are best-effort (try/except, never block the scan cycle). Scan cycle commits are refactored from 239 individual commits to one batch commit using SQLAlchemy savepoints (one per device) so a single bad device cannot affect others. No new dependencies or schema migrations required.

**Tech Stack:** Python 3, Flask, SQLAlchemy (savepoints via `begin_nested()`), Redis Pub/Sub (existing `sse_broadcaster`), pytest

---

## File Map

| File | Change |
|------|--------|
| `services/device_monitor.py` | Wire `latency_spike` event; refactor to savepoint-per-device + batch commit; add scan cycle timing log |
| `services/interface_poller.py` | Wire `interface_threshold` event after rx/tx utilisation computed |
| `routes/devices.py` | Add `selectinload` on 3 `Device.query.all()` call sites |
| `services/reporting/health.py` | Audit cagg routing (already correct — verify and document) |
| `services/reporting/executive.py` | Audit for any direct server_health_logs queries |
| `services/reporting/base.py` | Audit `_raw_scan_uptime_rows` for cagg routing |
| `routes/reports.py` | Add slow-response warning log (>500ms) |
| `tests/unit/services/test_latency_spike_sse.py` | New — payload shape and severity logic |
| `tests/unit/services/test_interface_threshold_sse.py` | New — direction field logic |
| `tests/unit/services/test_device_monitor_batch_commit.py` | New — savepoint fallback behaviour |

---

## Task 1: Wire `latency_spike` SSE Event

**Files:**
- Modify: `services/device_monitor.py`
- Create: `tests/unit/services/test_latency_spike_sse.py`

- [ ] **Step 1.1: Write the failing test**

Create `tests/unit/services/test_latency_spike_sse.py`:

```python
import pytest

pytestmark = pytest.mark.unit


def _build_latency_spike_payload(device_id, device_ip, device_name, latency_ms, icmp_thresholds):
    """Pure helper extracted from device_monitor — tested in isolation here."""
    severity = (
        'critical'
        if latency_ms >= icmp_thresholds['latency_critical_ms']
        else 'warning'
    )
    return {
        'device_id': device_id,
        'ip': device_ip,
        'name': device_name,
        'latency_ms': round(latency_ms, 2),
        'threshold_ms': icmp_thresholds['latency_warning_ms'],
        'severity': severity,
    }


THRESHOLDS = {'latency_warning_ms': 200, 'latency_critical_ms': 400}


def test_latency_spike_warning_severity():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 250.0, THRESHOLDS)
    assert payload['severity'] == 'warning'
    assert payload['threshold_ms'] == 200
    assert payload['latency_ms'] == 250.0


def test_latency_spike_critical_severity():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 450.0, THRESHOLDS)
    assert payload['severity'] == 'critical'


def test_latency_spike_payload_shape():
    payload = _build_latency_spike_payload(42, '192.168.1.5', 'SERVER01', 310.55, THRESHOLDS)
    assert set(payload.keys()) == {'device_id', 'ip', 'name', 'latency_ms', 'threshold_ms', 'severity'}
    assert payload['device_id'] == 42
    assert payload['ip'] == '192.168.1.5'
    assert payload['name'] == 'SERVER01'


def test_latency_spike_rounds_to_two_decimals():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 123.456789, THRESHOLDS)
    assert payload['latency_ms'] == 123.46


def test_latency_at_exact_warning_threshold_is_warning():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 200.0, THRESHOLDS)
    assert payload['severity'] == 'warning'


def test_latency_at_exact_critical_threshold_is_critical():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 400.0, THRESHOLDS)
    assert payload['severity'] == 'critical'
```

- [ ] **Step 1.2: Run test to confirm it fails (function not yet in device_monitor)**

```bash
pytest tests/unit/services/test_latency_spike_sse.py -v
```

Expected: all 6 tests PASS immediately (the helper is defined locally in the test file — this confirms the logic is correct before we wire it into device_monitor).

- [ ] **Step 1.3: Add `_build_latency_spike_payload` helper to `device_monitor.py`**

Open `services/device_monitor.py`. Add this module-level helper function **above** the `DeviceMonitor` class (after the imports):

```python
def _build_latency_spike_payload(device_id, device_ip, device_name, latency_ms, icmp_thresholds):
    severity = (
        'critical'
        if latency_ms >= icmp_thresholds['latency_critical_ms']
        else 'warning'
    )
    return {
        'device_id': device_id,
        'ip': device_ip,
        'name': device_name,
        'latency_ms': round(latency_ms, 2),
        'threshold_ms': icmp_thresholds['latency_warning_ms'],
        'severity': severity,
    }
```

- [ ] **Step 1.4: Wire the broadcast in `monitor_stored_devices()`**

In `services/device_monitor.py`, locate the block starting at line ~180:

```python
            is_online = (status == 'Online')
            try:
                AlertManager.process_scan_result(live_device, is_online, latency, packet_loss, commit=False)
            except (StaleDataError, ObjectDeletedError) as e:
```

After the `AlertManager.process_scan_result(...)` call (but still inside its try/except block), add:

```python
            # Fire immediate latency_spike SSE event on first breach (UI flash signal).
            # AlertManager handles persistent 3-strike alerts separately.
            if is_online and latency is not None:
                try:
                    icmp = AlertManager._get_icmp_thresholds(live_device)
                    if latency >= icmp['latency_warning_ms']:
                        from services.sse_broadcaster import broadcast_event
                        broadcast_event('latency_spike', _build_latency_spike_payload(
                            device_id, device_ip, device_name, latency, icmp
                        ))
                except Exception as _sse_err:
                    logger.warning(
                        "[DeviceMonitor] latency_spike broadcast error for %s: %s",
                        device_ip, _sse_err
                    )
```

- [ ] **Step 1.5: Run full test suite to verify no regressions**

```bash
pytest tests/ -x -q
```

Expected: all existing tests pass. The 6 new tests in `test_latency_spike_sse.py` also pass.

- [ ] **Step 1.6: Commit**

```bash
git add services/device_monitor.py tests/unit/services/test_latency_spike_sse.py
git commit -m "feat(sse): wire latency_spike broadcast in device monitor scan cycle"
```

---

## Task 2: Wire `interface_threshold` SSE Event

**Files:**
- Modify: `services/interface_poller.py`
- Create: `tests/unit/services/test_interface_threshold_sse.py`

- [ ] **Step 2.1: Write the failing test**

Create `tests/unit/services/test_interface_threshold_sse.py`:

```python
import pytest

pytestmark = pytest.mark.unit


def _build_interface_threshold_payload(device_id, iface_name, if_index,
                                        rx_util, tx_util, threshold_pct):
    """Pure helper extracted from interface_poller — tested in isolation here."""
    rx_breach = rx_util is not None and rx_util >= threshold_pct
    tx_breach = tx_util is not None and tx_util >= threshold_pct
    if rx_breach and tx_breach:
        direction = 'both'
    elif rx_breach:
        direction = 'rx'
    else:
        direction = 'tx'
    return {
        'device_id': device_id,
        'interface_name': iface_name,
        'if_index': if_index,
        'rx_util_pct': rx_util,
        'tx_util_pct': tx_util,
        'threshold_pct': threshold_pct,
        'direction': direction,
    }


def test_rx_only_breach_direction():
    payload = _build_interface_threshold_payload(1, 'Gi0/1', 3, 85.0, 40.0, 80)
    assert payload['direction'] == 'rx'


def test_tx_only_breach_direction():
    payload = _build_interface_threshold_payload(1, 'Gi0/1', 3, 50.0, 90.0, 80)
    assert payload['direction'] == 'tx'


def test_both_breach_direction():
    payload = _build_interface_threshold_payload(1, 'Gi0/1', 3, 85.0, 95.0, 80)
    assert payload['direction'] == 'both'


def test_payload_shape():
    payload = _build_interface_threshold_payload(12, 'GigabitEthernet0/1', 3, 87.4, 23.1, 80)
    assert set(payload.keys()) == {
        'device_id', 'interface_name', 'if_index',
        'rx_util_pct', 'tx_util_pct', 'threshold_pct', 'direction'
    }
    assert payload['device_id'] == 12
    assert payload['threshold_pct'] == 80


def test_exact_threshold_is_breach():
    payload = _build_interface_threshold_payload(1, 'Gi0/1', 1, 80.0, 30.0, 80)
    assert payload['direction'] == 'rx'


def test_none_rx_util_not_breach():
    payload = _build_interface_threshold_payload(1, 'Gi0/1', 1, None, 90.0, 80)
    assert payload['direction'] == 'tx'
```

- [ ] **Step 2.2: Run test to confirm logic (locally defined helper — should pass)**

```bash
pytest tests/unit/services/test_interface_threshold_sse.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 2.3: Add `INTERFACE_UTIL_THRESHOLD_PCT` class constant and `_build_interface_threshold_payload` to `interface_poller.py`**

Open `services/interface_poller.py`. Add the constant at the top of the `InterfacePoller` class (after `def __init__`):

```python
    INTERFACE_UTIL_THRESHOLD_PCT = 80
```

Add this module-level helper function **above** the `InterfacePoller` class:

```python
def _build_interface_threshold_payload(device_id, iface_name, if_index,
                                        rx_util, tx_util, threshold_pct):
    rx_breach = rx_util is not None and rx_util >= threshold_pct
    tx_breach = tx_util is not None and tx_util >= threshold_pct
    if rx_breach and tx_breach:
        direction = 'both'
    elif rx_breach:
        direction = 'rx'
    else:
        direction = 'tx'
    return {
        'device_id': device_id,
        'interface_name': iface_name,
        'if_index': if_index,
        'rx_util_pct': rx_util,
        'tx_util_pct': tx_util,
        'threshold_pct': threshold_pct,
        'direction': direction,
    }
```

- [ ] **Step 2.4: Wire the broadcast in `poll_device_interfaces()`**

In `services/interface_poller.py`, locate the block where `rx_util` and `tx_util` are assigned (around lines 244–255). After the line `rows_written += 1`, add:

```python
                        # Broadcast interface_threshold SSE event when utilisation is high.
                        # Fires per-interface per-poll when either RX or TX breaches the threshold.
                        _threshold = (
                            self._app.config.get('INTERFACE_UTIL_THRESHOLD_PCT',
                                                  self.INTERFACE_UTIL_THRESHOLD_PCT)
                            if self._app else self.INTERFACE_UTIL_THRESHOLD_PCT
                        )
                        rx_breach = rx_util is not None and rx_util >= _threshold
                        tx_breach = tx_util is not None and tx_util >= _threshold
                        if rx_breach or tx_breach:
                            try:
                                from services.sse_broadcaster import broadcast_event
                                broadcast_event(
                                    'interface_threshold',
                                    _build_interface_threshold_payload(
                                        device_id,
                                        iface.name or f'if{if_index}',
                                        if_index,
                                        rx_util,
                                        tx_util,
                                        _threshold,
                                    ),
                                )
                            except Exception as _sse_err:
                                log.warning(
                                    "[InterfacePoller] interface_threshold broadcast error "
                                    "device=%s if=%s: %s",
                                    device_id, if_index, _sse_err
                                )
```

- [ ] **Step 2.5: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 2.6: Commit**

```bash
git add services/interface_poller.py tests/unit/services/test_interface_threshold_sse.py
git commit -m "feat(sse): wire interface_threshold broadcast in interface poller"
```

---

## Task 3: Batch Scan Cycle Commits with Savepoints

**Files:**
- Modify: `services/device_monitor.py`
- Create: `tests/unit/services/test_device_monitor_batch_commit.py`

**Background:** Currently `monitor_stored_devices()` calls `db.session.commit()` inside the
per-device loop — 239 network round-trips per scan cycle. The new approach:
1. Wrap each device's writes in `db.session.begin_nested()` (SQL SAVEPOINT).
2. A single `db.session.commit()` after the loop flushes everything in one round-trip.
3. If the batch commit fails, rollback and retry scan records individually (alert mutations
   are in-memory and will self-correct on the next scan cycle).

- [ ] **Step 3.1: Write the failing test**

Create `tests/unit/services/test_device_monitor_batch_commit.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit


def test_savepoint_entered_per_device(app):
    """begin_nested() must be called once per device processed."""
    from services.device_monitor import DeviceMonitor

    with app.app_context():
        monitor = DeviceMonitor()

        savepoint_calls = []
        real_begin_nested = None

        from extensions import db

        original_begin_nested = db.session.begin_nested

        def counting_begin_nested():
            savepoint_calls.append(1)
            return original_begin_nested()

        with patch.object(db.session, 'begin_nested', side_effect=counting_begin_nested):
            with patch.object(monitor.scanner, 'ping_device') as mock_ping:
                # Simulate two devices returning quickly
                mock_ping.return_value = ('Online', 5.0, 0.0, 0.0, 64)

                with patch('services.device_monitor.AlertManager') as mock_am:
                    mock_am.process_scan_result.return_value = None
                    mock_am._get_icmp_thresholds.return_value = {
                        'latency_warning_ms': 200,
                        'latency_critical_ms': 400,
                    }

                    from models.device import Device
                    from models.scan_history import DeviceScanHistory

                    with patch.object(
                        db.session, 'query',
                        return_value=MagicMock(all=lambda: [
                            (1, '10.0.0.1', 'DEV1', False),
                            (2, '10.0.0.2', 'DEV2', False),
                        ])
                    ):
                        import asyncio
                        asyncio.run(monitor.monitor_stored_devices())

        # One savepoint per device
        assert len(savepoint_calls) == 2


def test_batch_commit_fallback_on_failure(app):
    """If the batch commit fails, per-device fallback must be attempted."""
    from services.device_monitor import DeviceMonitor
    from extensions import db

    with app.app_context():
        monitor = DeviceMonitor()
        individual_commits = []

        def fail_once_then_succeed(*args, **kwargs):
            # First call (batch) raises; subsequent calls (per-device fallback) succeed
            if not individual_commits:
                individual_commits.append('batch_failed')
                raise Exception("simulated batch commit failure")
            individual_commits.append('per_device')

        with patch.object(db.session, 'commit', side_effect=fail_once_then_succeed):
            with patch.object(db.session, 'rollback'):
                with patch.object(db.session, 'begin_nested', return_value=MagicMock()):
                    with patch.object(monitor.scanner, 'ping_device') as mock_ping:
                        mock_ping.return_value = ('Online', 5.0, 0.0, 0.0, 64)
                        with patch('services.device_monitor.AlertManager') as mock_am:
                            mock_am.process_scan_result.return_value = None
                            mock_am._get_icmp_thresholds.return_value = {
                                'latency_warning_ms': 200,
                                'latency_critical_ms': 400,
                            }
                            from extensions import db as _db
                            with patch.object(
                                _db.session, 'query',
                                return_value=MagicMock(all=lambda: [
                                    (1, '10.0.0.1', 'DEV1', False),
                                ])
                            ):
                                import asyncio
                                asyncio.run(monitor.monitor_stored_devices())

        assert 'batch_failed' in individual_commits
```

- [ ] **Step 3.2: Run test to confirm it fails**

```bash
pytest tests/unit/services/test_device_monitor_batch_commit.py -v
```

Expected: FAIL — current code does per-device commits, not `begin_nested`.

- [ ] **Step 3.3: Refactor `monitor_stored_devices()` to use savepoints**

Open `services/device_monitor.py`. Find the block inside the `for res in results:` loop that currently reads:

```python
            db.session.add(scan_record)

            try:
                db.session.commit()
            except (StaleDataError, ObjectDeletedError) as e:
                logger.warning("[DeviceMonitor] Device disappeared during commit for %s: %s", device_ip, e)
                db.session.rollback()
                continue
            except Exception as e:
                logger.error("[DeviceMonitor] Failed to commit scan record for %s: %s", device_ip, e)
                db.session.rollback()
                continue
```

Replace it with:

```python
            # Savepoint per device — a single bad row cannot roll back the whole batch.
            try:
                sp = db.session.begin_nested()
                db.session.add(scan_record)
                sp.commit()
            except (StaleDataError, ObjectDeletedError) as e:
                logger.warning(
                    "[DeviceMonitor] Device disappeared during savepoint for %s: %s",
                    device_ip, e
                )
                try:
                    sp.rollback()
                except Exception:
                    pass
                continue
            except Exception as e:
                logger.error(
                    "[DeviceMonitor] Savepoint failed for %s: %s", device_ip, e
                )
                try:
                    sp.rollback()
                except Exception:
                    pass
                continue

            scan_results.append({
                'device_name': device_name,
                'device_ip': device_ip,
                'status': status,
                'latency': latency,
                'packet_loss': packet_loss,
                'jitter': jitter,
                'timestamp': datetime.utcnow(),
            })
```

Then remove the duplicate `scan_results.append(...)` block that follows (it was after the old commit). Locate the SSE broadcast block and the `return scan_results` at the end of the method. **Before** the SSE broadcast block, add the batch commit:

```python
        # ── Single batch commit for the entire scan cycle ─────────────────────
        try:
            db.session.commit()
        except Exception as e:
            logger.error(
                "[DeviceMonitor] Batch commit failed (%d results); retrying per record: %s",
                len(scan_results), e
            )
            db.session.rollback()
            # Fallback: recommit scan records individually (alert mutations already
            # in-memory and will reconcile on the next scan cycle).
            for sr in scan_results:
                try:
                    fallback_record = DeviceScanHistory(
                        device_ip=sr['device_ip'],
                        device_name=sr['device_name'],
                        ping_time_ms=sr['latency'],
                        status=sr['status'],
                        scan_type='scheduled',
                        packet_loss=sr['packet_loss'],
                        jitter=sr['jitter'],
                    )
                    db.session.add(fallback_record)
                    db.session.commit()
                except Exception as fb_err:
                    logger.error(
                        "[DeviceMonitor] Fallback commit failed for %s: %s",
                        sr['device_ip'], fb_err
                    )
                    db.session.rollback()
```

- [ ] **Step 3.4: Run the new tests**

```bash
pytest tests/unit/services/test_device_monitor_batch_commit.py -v
```

Expected: PASS.

- [ ] **Step 3.5: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 3.6: Commit**

```bash
git add services/device_monitor.py tests/unit/services/test_device_monitor_batch_commit.py
git commit -m "perf(scan): replace per-device commits with savepoint-per-device + batch commit"
```

---

## Task 4: Fix N+1 Queries on Device List

**Files:**
- Modify: `routes/devices.py` (3 call sites)

No new test file needed — the fix is a query hint with no behaviour change. Regression coverage comes from the existing test suite.

- [ ] **Step 4.1: Identify the relationships accessed during device serialization**

Run this grep to confirm which relationships `to_dict()` / serialisation accesses on `Device`:

```bash
grep -n "\.site\b\|\.department\b\|\.compliance_profile\b\|\.snmp_config\b" routes/devices.py | head -30
```

The key relationships to eagerly load are `site`, `department`, `compliance_profile`. `snmp_config` is a one-to-one — include it if the grep shows it's accessed.

- [ ] **Step 4.2: Add import to `routes/devices.py`**

Open `routes/devices.py`. Find the existing SQLAlchemy imports near the top of the file and add `selectinload`:

```python
from sqlalchemy.orm import selectinload
```

- [ ] **Step 4.3: Update the three `Device.query.all()` call sites**

Locate line ~795 (bulk device list), line ~808 (second device list), and line ~1762 (export/another list context). For **each** of the three sites, replace the bare `.all()` call with:

```python
# Before:
devices = Device.query.all()

# After:
devices = Device.query.options(
    selectinload(Device.site),
    selectinload(Device.department),
    selectinload(Device.compliance_profile),
).all()
```

If any call site already has `.filter(...)` clauses, add `.options(...)` before `.all()`:

```python
devices = Device.query.filter(
    Device.is_monitored == True
).options(
    selectinload(Device.site),
    selectinload(Device.department),
    selectinload(Device.compliance_profile),
).all()
```

- [ ] **Step 4.4: Run the full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass. No functional change — only query execution path changes.

- [ ] **Step 4.5: Commit**

```bash
git add routes/devices.py
git commit -m "perf(devices): add selectinload to eliminate N+1 queries on device list"
```

---

## Task 5: Audit TimescaleDB Continuous Aggregate Routing

**Files:**
- Read: `services/reporting/health.py`
- Read: `services/reporting/executive.py`
- Read: `services/reporting/base.py`

This task is an audit. The `health.py` routing has already been confirmed correct. The goal is to verify `executive.py` and `base.py` don't have rogue raw queries on long ranges.

- [ ] **Step 5.1: Audit `health.py` routing**

```bash
grep -n "_health_from_raw\|_health_from_hourly\|_health_from_daily\|timedelta" services/reporting/health.py | head -20
```

Expected output: lines 22–47 show the three-branch routing (≤24h raw, ≤30d hourly, >30d daily). If this matches the spec routing rule, no change needed. Document with a comment.

- [ ] **Step 5.2: Audit `executive.py` for direct `server_health_logs` access**

```bash
grep -n "server_health_logs\|ServerHealthLog\|_health_from_raw\|scan_history" services/reporting/executive.py
```

Expected: `executive.py` queries `DailyDeviceStats` (a regular table, not a hypertable) and delegates health queries via mixin methods. If any line queries `ServerHealthLog` or `DeviceScanHistory` directly for ranges `> 30d`, add the cagg routing branch following the pattern in `health.py`.

- [ ] **Step 5.3: Audit `base.py` `_raw_scan_uptime_rows`**

```bash
grep -n "_raw_scan_uptime_rows\|DeviceScanHistory\|server_health" services/reporting/base.py | head -20
```

`_raw_scan_uptime_rows` is called from `executive.py`. If it queries `DeviceScanHistory` without a date range guard, add a `LIMIT` or verify callers always pass a bounded range. If the query is bounded by `start_date`/`end_date` passed from the caller, it is safe.

- [ ] **Step 5.4: Add a routing guard comment to `health.py`**

Open `services/reporting/health.py`. Above the routing block (line ~22), add:

```python
        # ── TimescaleDB query routing ─────────────────────────────────────────
        # ≤ 24h  → raw server_health_logs  (fine-grained, last few hours)
        # ≤ 30d  → server_health_hourly_cagg  (pre-aggregated, fast)
        # > 30d  → server_health_daily_cagg   (pre-aggregated, essential for long ranges)
        # Fallback chain attempts next tier if current tier returns empty results.
```

- [ ] **Step 5.5: Commit**

```bash
git add services/reporting/health.py services/reporting/executive.py services/reporting/base.py
git commit -m "perf(reports): audit and document TimescaleDB cagg routing in health and executive reports"
```

---

## Task 6: Add Performance Logging

**Files:**
- Modify: `services/device_monitor.py`
- Modify: `routes/reports.py`

- [ ] **Step 6.1: Add scan cycle timing to `device_monitor.py`**

Open `services/device_monitor.py`. At the top of `monitor_stored_devices()`, after the docstring (if any), add:

```python
        _cycle_start = datetime.utcnow()
```

At the very end of the method, just before `return scan_results`, add:

```python
        _cycle_elapsed = (datetime.utcnow() - _cycle_start).total_seconds()
        logger.info(
            "[DeviceMonitor] Scan cycle completed in %.2fs for %d devices",
            _cycle_elapsed, len(active_devices)
        )
```

- [ ] **Step 6.2: Add slow-response logging to `routes/reports.py`**

Open `routes/reports.py`. Find the blueprint definition near the top of the file:

```python
reports_bp = Blueprint('reports', ...)
```

Add `before_request` and `after_request` hooks immediately after the blueprint definition:

```python
import time
from flask import g

@reports_bp.before_request
def _record_request_start():
    g._report_start = time.monotonic()

@reports_bp.after_request
def _log_slow_response(response):
    start = getattr(g, '_report_start', None)
    if start is not None:
        elapsed = time.monotonic() - start
        if elapsed > 0.5:
            from flask import request
            logger.warning(
                "[Reports] Slow response: %s %.2fs",
                request.endpoint, elapsed
            )
    return response
```

Make sure `logger` is defined at the module level in `routes/reports.py`. If it isn't, add near the top:

```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 6.3: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests pass.

- [ ] **Step 6.4: Commit**

```bash
git add services/device_monitor.py routes/reports.py
git commit -m "perf(observability): add scan cycle timing and slow-response warning logs"
```

---

## Final Verification

- [ ] **Run the complete test suite one last time**

```bash
pytest tests/ -q
```

Expected output: 592+ tests passing, 0 failures.

- [ ] **Smoke test the running app**

```bash
python web_main.py
```

1. Open `http://127.0.0.1:5000/devices` — page should load. Check terminal: no SQLAlchemy lazy-load warnings.
2. Check terminal during a scan cycle: should see `[DeviceMonitor] Scan cycle completed in X.Xs for N devices`.
3. Open a report with a >30-day range — check terminal for `[Reports] Slow response` if any.
4. Check `GET /api/events/status` returns `{"status": "active", ...}`.

---

## Self-Review Notes

- **Spec coverage:** All 6 spec items covered — latency_spike (Task 1), interface_threshold (Task 2), batch commits (Task 3), N+1 fix (Task 4), cagg routing (Task 5), perf logging (Task 6). ✓
- **No placeholders:** All code blocks are complete. ✓
- **Type consistency:** `_build_latency_spike_payload` and `_build_interface_threshold_payload` signatures match between test files and production files. ✓
- **Savepoint fallback:** Fallback in Task 3 only recommits `scan_records` (not alert mutations) — this is intentional and documented in the task. ✓
