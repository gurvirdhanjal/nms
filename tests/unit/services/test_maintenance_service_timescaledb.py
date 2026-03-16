import pytest

from services import tracking_history as tracking_history_module
from services.maintenance_service import MaintenanceService
from services.timescaledb_service import TimescaleDBService


pytestmark = pytest.mark.unit


def _enable_timescaledb_for_maintenance(monkeypatch):
    monkeypatch.setattr(MaintenanceService, "_backend_name", staticmethod(lambda: "postgresql"))
    monkeypatch.setattr(TimescaleDBService, "is_timescaledb_enabled", lambda: True)


def test_tracking_rollups_and_retention_are_policy_managed_under_timescaledb(monkeypatch):
    _enable_timescaledb_for_maintenance(monkeypatch)
    service = MaintenanceService()

    rollup_result = service.run_tracking_rollups()
    assert rollup_result["success"] is True
    assert rollup_result["skipped"] is True
    assert rollup_result["policy_managed"] is True
    assert rollup_result["tasks"]["hourly_rollup"]["policy_managed"] is True
    assert rollup_result["tasks"]["daily_rollup"]["policy_managed"] is True

    retention_result = service.run_tracking_history_retention()
    assert retention_result["success"] is True
    assert retention_result["skipped"] is True
    assert retention_result["policy_managed"] is True
    assert "TimescaleDB" in retention_result["detail"]

    backfill_result = service.backfill_tracking_rollups(lookback_days=30)
    assert backfill_result["success"] is True
    assert backfill_result["skipped"] is True
    assert backfill_result["policy_managed"] is True


def test_server_health_retention_is_policy_managed_under_timescaledb(monkeypatch):
    _enable_timescaledb_for_maintenance(monkeypatch)
    service = MaintenanceService()

    result = service.run_server_health_retention(raw_days=7, hourly_days=30, daily_days=365)

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["policy_managed"] is True
    assert result["tasks"]["hourly_rollup"]["policy_managed"] is True
    assert result["tasks"]["daily_rollup"]["policy_managed"] is True
    assert result["tasks"]["raw_cleanup"]["policy_managed"] is True
    assert result["tasks"]["hourly_cleanup"]["policy_managed"] is True
    assert result["tasks"]["daily_cleanup"]["policy_managed"] is True


def test_tracking_history_retention_helper_skips_direct_deletes_under_timescaledb(monkeypatch):
    monkeypatch.setattr(
        type(tracking_history_module.db.engine.url),
        "get_backend_name",
        lambda self: "postgresql",
    )
    monkeypatch.setattr(tracking_history_module.TimescaleDBService, "is_timescaledb_enabled", lambda: True)

    result = tracking_history_module.run_tracking_retention(raw_days=30, hourly_days=365, daily_days=1095)

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["policy_managed"] is True
    assert result["task"] == "run_tracking_retention"
    assert result["backend"] == "postgresql"
