from datetime import datetime, timedelta, timezone

import pytest

from services import report_meta
from services.timescaledb_service import TimescaleDBService


pytestmark = pytest.mark.unit


def test_get_report_definition_uses_timescaledb_caggs_for_server_health(monkeypatch):
    monkeypatch.setattr(TimescaleDBService, "is_timescaledb_enabled", lambda: True)

    hourly = report_meta.get_report_definition("device-health", "hourly")
    assert hourly["source_tables"] == ["server_health_hourly_cagg", "device"]
    assert hourly["freshness_sources"] == ["server_health_hourly_cagg"]

    daily = report_meta.get_report_definition("operational", "daily")
    assert daily["source_tables"] == ["server_health_daily_cagg", "dashboard_events", "device"]
    assert daily["freshness_sources"] == ["server_health_daily_cagg", "dashboard_events"]


def test_build_report_meta_reflects_selected_timescaledb_granularity(monkeypatch):
    monkeypatch.setattr(TimescaleDBService, "is_timescaledb_enabled", lambda: True)
    latest = datetime.now(timezone.utc) - timedelta(minutes=30)

    monkeypatch.setattr(
        report_meta,
        "_collect_source_stats",
        lambda report_type, start_date, end_date, granularity=None: {
            "server_health_daily_cagg": {
                "count": 4,
                "latest": latest,
                "range_count": 4,
                "distinct_buckets": 2,
            },
            "dashboard_events": {
                "count": 1,
                "latest": latest,
                "range_count": 1,
                "distinct_buckets": 1,
            },
            "device": {
                "count": 1,
                "latest": latest,
                "range_count": 1,
                "distinct_buckets": 1,
            },
        },
    )
    monkeypatch.setattr(report_meta, "_build_completeness_warnings", lambda *args, **kwargs: [])

    start_date = datetime.now(timezone.utc) - timedelta(days=45)
    end_date = datetime.now(timezone.utc)
    meta = report_meta.build_report_meta(
        "operational",
        {"heatmap_granularity": "daily"},
        start_date=start_date,
        end_date=end_date,
        row_count=12,
        cache_hit=False,
        cache_ttl_seconds=300,
        cache_age_seconds=0.0,
    )

    assert meta["report_type"] == "operational"
    assert meta["granularity"] == "daily"
    assert meta["source_tables"] == ["server_health_daily_cagg", "dashboard_events", "device"]
    assert meta["freshness_sources"] == ["server_health_daily_cagg", "dashboard_events"]
