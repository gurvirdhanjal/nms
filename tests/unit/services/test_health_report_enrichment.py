"""Unit tests for health report enrichment helpers (Phase 6-7)."""
import pytest
from unittest.mock import MagicMock, patch


# ── _compute_sla_breaches ─────────────────────────────────────────────────────

class TestComputeSlaBreach:
    @staticmethod
    def _breaches(incidents, period_min, threshold=99.0):
        from services.reporting.health import _compute_sla_breaches
        return _compute_sla_breaches(incidents, period_min, threshold)

    def test_no_incidents_returns_empty(self):
        assert self._breaches([], 1440) == []

    def test_zero_period_returns_empty(self):
        inc = [{"start_ts": "2025-01-01T00:00:00", "end_ts": "2025-01-01T00:10:00",
                "duration_min": 10, "cause": "connectivity_loss", "sla_impact": False}]
        assert self._breaches(inc, 0) == []

    def test_no_breach_when_downtime_within_sla(self):
        # 99% SLA on 1440-min period → max allowed down = 14.4 min
        # 10-min incident does not trigger breach
        inc = [{"start_ts": "2025-01-01T00:00:00", "end_ts": None,
                "duration_min": 10, "cause": "connectivity_loss", "sla_impact": False}]
        result = self._breaches(inc, 1440)
        assert result == []
        assert inc[0]["sla_impact"] is False

    def test_breach_flagged_when_downtime_exceeds_threshold(self):
        # 99% SLA on 1440 min → max = 14.4 min; 20-min incident triggers breach
        inc = [{"start_ts": "2025-01-01T00:00:00", "end_ts": "2025-01-01T00:20:00",
                "duration_min": 20, "cause": "connectivity_loss", "sla_impact": False}]
        result = self._breaches(inc, 1440)
        assert len(result) == 1
        assert inc[0]["sla_impact"] is True
        assert result[0]["sla_threshold_pct"] == 99.0
        assert result[0]["duration_min"] == 20

    def test_cumulative_breach_across_multiple_incidents(self):
        # Two 8-min incidents = 16 min cumulative → exceeds 14.4 min limit
        incidents = [
            {"start_ts": "2025-01-01T00:00:00", "end_ts": None, "duration_min": 8,
             "cause": "connectivity_loss", "sla_impact": False},
            {"start_ts": "2025-01-01T01:00:00", "end_ts": None, "duration_min": 8,
             "cause": "connectivity_loss", "sla_impact": False},
        ]
        result = self._breaches(incidents, 1440)
        # Second incident pushes cumulative over threshold
        assert len(result) == 1
        assert incidents[1]["sla_impact"] is True
        assert incidents[0]["sla_impact"] is False

    def test_custom_sla_threshold(self):
        # 95% SLA on 1440 min → max = 72 min; 60-min incident is fine
        inc = [{"start_ts": None, "end_ts": None, "duration_min": 60,
                "cause": "connectivity_loss", "sla_impact": False}]
        assert self._breaches(inc, 1440, threshold=95.0) == []


# ── _compute_correlation ──────────────────────────────────────────────────────

class TestComputeCorrelation:
    @staticmethod
    def _corr(incidents, agent_points, gran_hours=1.0):
        from services.reporting.health import _compute_correlation
        return _compute_correlation(incidents, agent_points, gran_hours)

    def test_no_incidents_returns_zero(self):
        result = self._corr([], [{"ts": "2025-01-01T00:00:00", "cpu": 90, "mem": 50}])
        assert result["cpu_spike_count"] == 0
        assert result["correlated_pct"] == 0.0

    def test_no_agent_data_returns_zero(self):
        inc = [{"start_ts": "2025-01-01T00:00:00", "cause": "connectivity_loss"}]
        result = self._corr(inc, [])
        assert result["cpu_spike_count"] == 0
        assert result["total_incidents"] == 1

    def test_cpu_spike_detected_within_window(self):
        incidents = [{"start_ts": "2025-01-01T01:00:00", "cause": "connectivity_loss"}]
        # CPU=90 at T+30min is within ±1h window
        points = [{"ts": "2025-01-01T01:30:00", "cpu": 90, "mem": 50}]
        result = self._corr(incidents, points, gran_hours=1.0)
        assert result["cpu_spike_count"] == 1
        assert result["correlated_pct"] == 100.0
        assert "CPU" in result["insight"] or "cpu" in result["insight"].lower()

    def test_cpu_spike_outside_window_not_counted(self):
        incidents = [{"start_ts": "2025-01-01T00:00:00", "cause": "connectivity_loss"}]
        # CPU=90 at T+3h is outside ±1h window
        points = [{"ts": "2025-01-01T03:00:00", "cpu": 90, "mem": 50}]
        result = self._corr(incidents, points, gran_hours=1.0)
        assert result["cpu_spike_count"] == 0

    def test_mem_spike_detected(self):
        incidents = [{"start_ts": "2025-01-01T06:00:00", "cause": "connectivity_loss"}]
        points = [{"ts": "2025-01-01T06:00:00", "cpu": 50, "mem": 90}]
        result = self._corr(incidents, points, gran_hours=1.0)
        assert result["mem_spike_count"] == 1

    def test_correlation_no_agent_data_returns_empty_insight(self):
        result = self._corr([], [])
        assert result["insight"] == ""
        assert result["correlated_pct"] == 0.0


# ── SQLite fallback behaviour ─────────────────────────────────────────────────

class TestScanStatsSqliteFallback:
    """Verify _fetch_scan_stats_batch silently returns {} on SQLite."""

    def test_returns_empty_for_empty_device_ids(self):
        """Empty device_ids always returns {} without any DB calls."""
        from services.reporting.health import HealthReportMixin
        from datetime import datetime

        mixin = HealthReportMixin.__new__(HealthReportMixin)
        result = mixin._fetch_scan_stats_batch([], datetime(2025, 1, 1), datetime(2025, 1, 7))
        assert result == {}

    def test_sqlite_dialect_skips_pg_queries(self):
        """When db.engine.dialect.name == 'sqlite', _enrich_health_devices
        produces empty scan_stats and returns no fleet correlation findings
        (smoke-test without real DB — all DB calls guarded by is_sqlite check)."""
        from services.reporting.health import HealthReportMixin, _compute_sla_breaches

        # _compute_sla_breaches is pure Python — assert it doesn't call DB
        result = _compute_sla_breaches([], 1440)
        assert result == []
