"""
Unit tests for the batch-commit refactor in DeviceMonitor.monitor_stored_devices():
  - begin_nested() (SAVEPOINT) is called once per successfully-processed device.
  - A single db.session.commit() is called after the loop, not inside it.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, call

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_db():
    """Return a mock that behaves like `extensions.db` for savepoint tests."""
    fake_db = MagicMock()
    # session.get() returns a device mock by default
    fake_device = MagicMock()
    fake_device.maintenance_mode = False
    fake_db.session.get.return_value = fake_device
    # begin_nested() returns a savepoint mock that has commit/rollback
    fake_sp = MagicMock()
    fake_db.session.begin_nested.return_value = fake_sp
    return fake_db, fake_sp


def _build_sys_modules_patch(fake_db):
    """
    Minimal sys.modules stubs so `import services.device_monitor` succeeds
    without a real Flask app, real Redis, or real SNMP stack.
    """
    fake_extensions = MagicMock()
    fake_extensions.db = fake_db

    return {
        'extensions': fake_extensions,
        'metrics.collector': MagicMock(),
        'metrics.normalizer': MagicMock(),
        'thresholds.evaluator': MagicMock(),
        'thresholds.rules': MagicMock(),
        'services.network_scanner': MagicMock(),
        'services.alert_manager': MagicMock(),
        'services.sse_broadcaster': MagicMock(),
    }


# ---------------------------------------------------------------------------
# Test 1: begin_nested() is called once per successfully-processed device
# ---------------------------------------------------------------------------

def test_begin_nested_called_per_device():
    """
    Each device that passes alert processing must trigger one begin_nested()
    call (one SAVEPOINT).  Two devices → two savepoints.
    """
    fake_db, fake_sp = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    # Patch AlertManager on the stub module
    fake_am = sys_patch['services.alert_manager'].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        'latency_warning_ms': 200,
        'latency_critical_ms': 400,
    }
    fake_am.process_scan_result.return_value = None

    # Patch MetricNormalizer
    fake_normalizer = sys_patch['metrics.normalizer'].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    with patch.dict('sys.modules', sys_patch):
        import importlib
        import services.device_monitor as dm
        importlib.reload(dm)

        monitor = dm.DeviceMonitor()

        # Two active devices
        two_devices = [
            {'id': 1, 'ip': '10.0.0.1', 'name': 'DEV1', 'status': 'Online',
             'latency': 10.0, 'packet_loss': 0.0, 'jitter': 0.5},
            {'id': 2, 'ip': '10.0.0.2', 'name': 'DEV2', 'status': 'Online',
             'latency': 15.0, 'packet_loss': 0.0, 'jitter': 0.3},
        ]

        # Stub out the async gather path: inject results directly
        async def fake_monitor():
            # Replicate the post-gather loop logic by calling the real method
            # but with pre-built results — achieved by patching asyncio.gather.
            pass

        # We test by patching asyncio.gather to return controlled results,
        # then running the actual coroutine.
        import asyncio as _asyncio

        original_gather = _asyncio.gather

        async def patched_gather(*coros, **kw):
            # Return our controlled two-device result set
            return two_devices

        with patch('asyncio.gather', side_effect=patched_gather), \
             patch.object(fake_db.session, 'query') as mock_query, \
             patch.object(fake_db.session, 'remove'):

            mock_query.return_value.all.return_value = [
                MagicMock(device_id=1, device_ip='10.0.0.1',
                          device_name='DEV1', maintenance_mode=False),
                MagicMock(device_id=2, device_ip='10.0.0.2',
                          device_name='DEV2', maintenance_mode=False),
            ]

            asyncio.run(monitor.monitor_stored_devices())

    # One SAVEPOINT per device
    assert fake_db.session.begin_nested.call_count == 2


# ---------------------------------------------------------------------------
# Test 2: only ONE db.session.commit() fires after the loop (not per device)
# ---------------------------------------------------------------------------

def test_batch_commit_called_once_after_loop():
    """
    db.session.commit() must be called exactly once — the batch commit that
    follows the loop.  It must NOT be called once per device (old behaviour).
    """
    fake_db, fake_sp = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    fake_am = sys_patch['services.alert_manager'].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        'latency_warning_ms': 200,
        'latency_critical_ms': 400,
    }
    fake_am.process_scan_result.return_value = None

    fake_normalizer = sys_patch['metrics.normalizer'].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    with patch.dict('sys.modules', sys_patch):
        import importlib
        import services.device_monitor as dm
        importlib.reload(dm)

        monitor = dm.DeviceMonitor()

        three_devices = [
            {'id': i, 'ip': f'10.0.0.{i}', 'name': f'DEV{i}', 'status': 'Online',
             'latency': 5.0, 'packet_loss': 0.0, 'jitter': 0.1}
            for i in range(1, 4)
        ]

        async def patched_gather(*coros, **kw):
            return three_devices

        with patch('asyncio.gather', side_effect=patched_gather), \
             patch.object(fake_db.session, 'query') as mock_query, \
             patch.object(fake_db.session, 'remove'):

            mock_query.return_value.all.return_value = [
                MagicMock(device_id=i, device_ip=f'10.0.0.{i}',
                          device_name=f'DEV{i}', maintenance_mode=False)
                for i in range(1, 4)
            ]

            asyncio.run(monitor.monitor_stored_devices())

    # The single batch commit — not per-device (would be 3 if old code)
    assert fake_db.session.commit.call_count == 1


# ---------------------------------------------------------------------------
# Test 3: savepoint rollback on StaleDataError — loop continues (no crash)
# ---------------------------------------------------------------------------

def test_savepoint_rolled_back_on_stale_data_error():
    """
    When begin_nested() is followed by a StaleDataError on sp.commit(),
    the savepoint is rolled back and the loop continues without crashing.
    The failed device must NOT appear in scan_results.
    """
    from sqlalchemy.orm.exc import StaleDataError

    fake_db, _ = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    fake_am = sys_patch['services.alert_manager'].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        'latency_warning_ms': 200,
        'latency_critical_ms': 400,
    }
    fake_am.process_scan_result.return_value = None

    fake_normalizer = sys_patch['metrics.normalizer'].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    # First device's savepoint raises StaleDataError on commit
    bad_sp = MagicMock()
    bad_sp.commit.side_effect = StaleDataError()
    good_sp = MagicMock()

    call_count = [0]

    def _begin_nested():
        call_count[0] += 1
        if call_count[0] == 1:
            return bad_sp
        return good_sp

    fake_db.session.begin_nested.side_effect = _begin_nested

    with patch.dict('sys.modules', sys_patch):
        import importlib
        import services.device_monitor as dm
        importlib.reload(dm)

        monitor = dm.DeviceMonitor()

        two_devices = [
            {'id': 1, 'ip': '10.0.0.1', 'name': 'STALE', 'status': 'Online',
             'latency': 10.0, 'packet_loss': 0.0, 'jitter': 0.1},
            {'id': 2, 'ip': '10.0.0.2', 'name': 'GOOD', 'status': 'Online',
             'latency': 10.0, 'packet_loss': 0.0, 'jitter': 0.1},
        ]

        async def patched_gather(*coros, **kw):
            return two_devices

        with patch('asyncio.gather', side_effect=patched_gather), \
             patch.object(fake_db.session, 'query') as mock_query, \
             patch.object(fake_db.session, 'remove'):

            mock_query.return_value.all.return_value = [
                MagicMock(device_id=1, device_ip='10.0.0.1',
                          device_name='STALE', maintenance_mode=False),
                MagicMock(device_id=2, device_ip='10.0.0.2',
                          device_name='GOOD', maintenance_mode=False),
            ]

            results = asyncio.run(monitor.monitor_stored_devices())

    # Only the second (good) device makes it into scan_results
    assert len(results) == 1
    assert results[0]['device_ip'] == '10.0.0.2'
    # Stale savepoint was rolled back
    bad_sp.rollback.assert_called_once()
