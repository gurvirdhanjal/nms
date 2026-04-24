"""
Unit tests for DeviceMonitor.monitor_stored_devices():
  - alert/strike updates run in short transactions
  - scan history is bulk-inserted separately
  - deadlocked alert updates do not prevent ping sample storage
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.unit


def _make_fake_db():
    fake_db = MagicMock()
    fake_device = MagicMock()
    fake_device.maintenance_mode = False
    fake_db.session.get.return_value = fake_device
    return fake_db


def _build_sys_modules_patch(fake_db):
    fake_extensions = MagicMock()
    fake_extensions.db = fake_db

    return {
        "extensions": fake_extensions,
        "metrics.collector": MagicMock(),
        "metrics.normalizer": MagicMock(),
        "thresholds.evaluator": MagicMock(),
        "thresholds.rules": MagicMock(),
        "services.network_scanner": MagicMock(),
        "services.alert_manager": MagicMock(),
        "services.sse_broadcaster": MagicMock(),
    }


async def _return_results(coros, results):
    for coro in coros:
        coro.close()
    return results


def test_scan_history_uses_single_bulk_insert():
    fake_db = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    fake_am = sys_patch["services.alert_manager"].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        "latency_warning_ms": 200,
        "latency_critical_ms": 400,
    }
    fake_am.process_scan_result.return_value = None

    fake_normalizer = sys_patch["metrics.normalizer"].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    with patch.dict("sys.modules", sys_patch):
        import importlib
        import services.device_monitor as dm

        importlib.reload(dm)
        monitor = dm.DeviceMonitor()

        three_devices = [
            {"id": i, "ip": f"10.0.0.{i}", "name": f"DEV{i}", "status": "Online", "latency": 5.0, "packet_loss": 0.0, "jitter": 0.1}
            for i in range(1, 4)
        ]

        async def patched_gather(*coros, **kw):
            return await _return_results(coros, three_devices)

        with patch("asyncio.gather", side_effect=patched_gather), patch.object(fake_db.session, "query") as mock_query, patch.object(fake_db.session, "remove"):
            mock_query.return_value.all.return_value = [
                MagicMock(device_id=i, device_ip=f"10.0.0.{i}", device_name=f"DEV{i}", maintenance_mode=False)
                for i in range(1, 4)
            ]

            results = asyncio.run(monitor.monitor_stored_devices())

    assert len(results) == 3
    assert fake_db.session.bulk_insert_mappings.call_count == 1
    assert fake_db.session.commit.call_count == 4


def test_deadlocked_alert_update_does_not_block_scan_storage():
    fake_db = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    fake_am = sys_patch["services.alert_manager"].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        "latency_warning_ms": 200,
        "latency_critical_ms": 400,
    }

    deadlock_error = OperationalError(
        "UPDATE device SET packet_loss_strikes=0",
        {},
        Exception("deadlock detected"),
    )
    fake_am.process_scan_result.side_effect = deadlock_error

    fake_normalizer = sys_patch["metrics.normalizer"].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    with patch.dict("sys.modules", sys_patch):
        import importlib
        import services.device_monitor as dm

        importlib.reload(dm)
        monitor = dm.DeviceMonitor()

        one_device = [
            {"id": 1, "ip": "10.0.0.1", "name": "DEV1", "status": "Online", "latency": 15.0, "packet_loss": 0.0, "jitter": 0.1}
        ]

        async def patched_gather(*coros, **kw):
            return await _return_results(coros, one_device)

        with patch("asyncio.gather", side_effect=patched_gather), patch.object(fake_db.session, "query") as mock_query, patch.object(fake_db.session, "remove"):
            mock_query.return_value.all.return_value = [
                MagicMock(device_id=1, device_ip="10.0.0.1", device_name="DEV1", maintenance_mode=False)
            ]

            results = asyncio.run(monitor.monitor_stored_devices())

    assert len(results) == 1
    assert fake_db.session.rollback.call_count >= 1
    assert fake_db.session.bulk_insert_mappings.call_count == 1


def test_falls_back_to_per_record_insert_when_bulk_insert_fails():
    fake_db = _make_fake_db()
    sys_patch = _build_sys_modules_patch(fake_db)

    fake_am = sys_patch["services.alert_manager"].AlertManager
    fake_am.get_icmp_thresholds.return_value = {
        "latency_warning_ms": 200,
        "latency_critical_ms": 400,
    }
    fake_am.process_scan_result.return_value = None

    fake_normalizer = sys_patch["metrics.normalizer"].MetricNormalizer
    fake_normalizer.normalize_ping.return_value = {}

    fake_db.session.bulk_insert_mappings.side_effect = RuntimeError("bulk failed")

    with patch.dict("sys.modules", sys_patch):
        import importlib
        import services.device_monitor as dm

        importlib.reload(dm)
        monitor = dm.DeviceMonitor()

        two_devices = [
            {"id": 1, "ip": "10.0.0.1", "name": "DEV1", "status": "Online", "latency": 5.0, "packet_loss": 0.0, "jitter": 0.1},
            {"id": 2, "ip": "10.0.0.2", "name": "DEV2", "status": "Offline", "latency": None, "packet_loss": 100.0, "jitter": None},
        ]

        async def patched_gather(*coros, **kw):
            return await _return_results(coros, two_devices)

        with patch("asyncio.gather", side_effect=patched_gather), patch.object(fake_db.session, "query") as mock_query, patch.object(fake_db.session, "remove"):
            mock_query.return_value.all.return_value = [
                MagicMock(device_id=1, device_ip="10.0.0.1", device_name="DEV1", maintenance_mode=False),
                MagicMock(device_id=2, device_ip="10.0.0.2", device_name="DEV2", maintenance_mode=False),
            ]

            results = asyncio.run(monitor.monitor_stored_devices())

    assert len(results) == 2
    assert fake_db.session.bulk_insert_mappings.call_count == 1
    assert fake_db.session.add.call_count == 2
