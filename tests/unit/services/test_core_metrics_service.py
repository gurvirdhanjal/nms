"""
Unit tests for core_metrics_service.py.

These tests do NOT require a DB — they validate pure logic only.
DB-dependent functions (_bulk_inventory_uptime, etc.) are not tested here;
their logic is covered indirectly through integration tests and the enterprise
report service tests.
"""
import pytest

from datetime import datetime, timedelta

from services.core_metrics_service import (
    _compute_uptime_from_events,
    _detect_anomaly,
    _merge_flapping_incidents,
    _mttr_mtbf,
    _safe_round,
    coverage_level,
    downtime_hours,
    get_server_metrics_bulk,
    get_workstation_metrics_bulk,
    sla_tier,
    ANOMALY_LATENCY_MS,
    ANOMALY_PACKET_LOSS_PCT,
    ANOMALY_UPTIME_PCT,
    ANOMALY_VIOLATION_COUNT,
    GAP_THRESHOLD_S,
    SLA_GOLD, SLA_SILVER, SLA_BRONZE, SLA_WARNING,
    MIN_INCIDENT_DURATION_S, MAX_INCIDENT_DURATION_H, FLAP_MERGE_GAP_S,
)


# ── Empty-input guards ────────────────────────────────────────────────────────

def test_get_server_metrics_bulk_empty():
    """Empty device list returns empty list without DB calls."""
    assert get_server_metrics_bulk([], None, None, 168.0) == []


def test_get_workstation_metrics_bulk_empty():
    """Empty tracked device list returns empty list without DB calls."""
    assert get_workstation_metrics_bulk([], None, None, 168.0) == []


# ── Canonical row keys ────────────────────────────────────────────────────────

CANONICAL_KEYS = {
    "device_id", "device_name", "device_ip", "fleet",
    "uptime_pct", "downtime_hours", "sla_tier",
    "avg_latency_ms", "max_latency_ms", "avg_packet_loss_pct",
    "timeout_count", "incident_count", "mttr_min",
    "violation_count", "has_violation", "last_violation_time",
    "anomaly_flag", "anomaly_reason",
}


def _make_server_row(**overrides):
    """Return a minimal valid server row dict."""
    row = {
        "device_id": 1, "device_name": "srv-01", "device_ip": "10.0.0.1",
        "fleet": "server", "uptime_pct": 99.5, "downtime_hours": 0.08,
        "sla_tier": "Silver", "avg_latency_ms": 12.5, "max_latency_ms": 45.0,
        "avg_packet_loss_pct": 0.5, "timeout_count": 0, "incident_count": None,
        "mttr_min": None, "violation_count": 0, "has_violation": False,
        "last_violation_time": None, "anomaly_flag": False, "anomaly_reason": None,
    }
    row.update(overrides)
    return row


def test_canonical_server_row_keys():
    assert set(_make_server_row().keys()) == CANONICAL_KEYS


def _make_workstation_row(**overrides):
    """Return a minimal valid workstation row dict."""
    row = {
        "device_id": 10, "device_name": "ws-01", "device_ip": "192.168.1.50",
        "fleet": "workstation", "uptime_pct": 97.0, "downtime_hours": 5.0,
        "sla_tier": "Bronze", "avg_latency_ms": None, "max_latency_ms": None,
        "avg_packet_loss_pct": None, "timeout_count": None, "incident_count": 2,
        "mttr_min": 30.0, "violation_count": 3, "has_violation": True,
        "last_violation_time": "2026-03-20T10:00:00", "anomaly_flag": False,
        "anomaly_reason": None,
    }
    row.update(overrides)
    return row


def test_canonical_workstation_row_keys():
    assert set(_make_workstation_row().keys()) == CANONICAL_KEYS


# ── _detect_anomaly None guards ───────────────────────────────────────────────

def test_detect_anomaly_all_none():
    """All-None metric fields must not trigger a false anomaly flag."""
    flag, reason = _detect_anomaly({
        "uptime_pct": None,
        "avg_latency_ms": None,
        "avg_packet_loss_pct": None,
        "violation_count": None,
    })
    assert flag is False
    assert reason is None


def test_detect_anomaly_empty_row():
    """Missing keys (workstation row has no latency) must not trigger anomaly."""
    flag, reason = _detect_anomaly({})
    assert flag is False
    assert reason is None


def test_detect_anomaly_workstation_no_latency():
    """Workstation rows have None latency/packet_loss — must not flag."""
    row = _make_workstation_row(avg_latency_ms=None, avg_packet_loss_pct=None,
                                uptime_pct=98.0, violation_count=0)
    flag, reason = _detect_anomaly(row)
    assert flag is False


def test_detect_anomaly_triggers_latency():
    flag, reason = _detect_anomaly(_make_server_row(avg_latency_ms=ANOMALY_LATENCY_MS + 1))
    assert flag is True
    assert "latency" in reason


def test_detect_anomaly_triggers_packet_loss():
    flag, reason = _detect_anomaly(_make_server_row(avg_packet_loss_pct=ANOMALY_PACKET_LOSS_PCT + 1))
    assert flag is True
    assert "packet_loss" in reason


def test_detect_anomaly_triggers_uptime():
    flag, reason = _detect_anomaly(_make_server_row(uptime_pct=ANOMALY_UPTIME_PCT - 1))
    assert flag is True
    assert "uptime" in reason


def test_detect_anomaly_triggers_violations():
    flag, reason = _detect_anomaly(_make_workstation_row(violation_count=ANOMALY_VIOLATION_COUNT + 1))
    assert flag is True
    assert "violations" in reason


def test_detect_anomaly_multi_reason():
    """Multiple triggers produce a semicolon-joined reason string."""
    flag, reason = _detect_anomaly(_make_server_row(
        avg_latency_ms=ANOMALY_LATENCY_MS + 1,
        uptime_pct=ANOMALY_UPTIME_PCT - 1,
    ))
    assert flag is True
    assert "latency" in reason
    assert "uptime" in reason


# ── sla_tier ──────────────────────────────────────────────────────────────────

def test_sla_tier_none():
    assert sla_tier(None) == "Unknown"

def test_sla_tier_gold():
    assert sla_tier(SLA_GOLD) == "Gold"

def test_sla_tier_silver():
    assert sla_tier(SLA_SILVER) == "Silver"

def test_sla_tier_bronze():
    assert sla_tier(SLA_BRONZE) == "Bronze"

def test_sla_tier_warning():
    assert sla_tier(SLA_WARNING) == "Warning"

def test_sla_tier_critical():
    assert sla_tier(0.0) == "Critical"


# ── downtime_hours ────────────────────────────────────────────────────────────

def test_downtime_hours_none():
    assert downtime_hours(None, 168.0) is None

def test_downtime_hours_full_uptime():
    assert downtime_hours(100.0, 168.0) == 0.0

def test_downtime_hours_calculation():
    # 99% uptime over 100h = 1h downtime
    result = downtime_hours(99.0, 100.0)
    assert result == pytest.approx(1.0, abs=0.01)


# ── _safe_round ───────────────────────────────────────────────────────────────

def test_safe_round_none():
    assert _safe_round(None) is None

def test_safe_round_value():
    assert _safe_round(3.14159) == 3.14

def test_safe_round_non_numeric():
    assert _safe_round("bad") is None


# ── Constants re-exported correctly ──────────────────────────────────────────

def test_constants_values():
    assert ANOMALY_LATENCY_MS == 300.0
    assert ANOMALY_PACKET_LOSS_PCT == 50.0
    assert ANOMALY_UPTIME_PCT == 90.0
    assert ANOMALY_VIOLATION_COUNT == 10
    assert MIN_INCIDENT_DURATION_S == 10
    assert MAX_INCIDENT_DURATION_H == 72
    assert FLAP_MERGE_GAP_S == 120
    assert GAP_THRESHOLD_S == 1800


# ── Monitoring gap detection ─────────────────────────────────────────────────

class _MockEvent:
    """Minimal stand-in for TrackedDeviceAvailabilityEvent."""
    def __init__(self, status: str, observed_at: datetime):
        self.status = status
        self.observed_at = observed_at


class TestMonitoringGapDetection:
    """Tests for _compute_uptime_from_events gap-aware denominator."""

    def test_no_events_returns_none_with_zero_coverage(self):
        start = datetime(2026, 3, 1)
        end = datetime(2026, 3, 2)
        up, inc, cov = _compute_uptime_from_events([], start, end)
        assert up is None
        assert inc == []
        assert cov["observed_seconds"] == 0.0
        assert cov["monitoring_coverage_pct"] == 0.0
        assert cov["total_gap_seconds"] == 86400.0  # full 24h

    def test_no_gap_full_coverage(self):
        """Events every 5 min → no gaps, coverage 100%."""
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 1, 0)
        events = [_MockEvent("online", start + timedelta(minutes=i * 5)) for i in range(13)]
        up, inc, cov = _compute_uptime_from_events(events, start, end)
        assert up == 100.0
        assert cov["gap_count"] == 0
        assert cov["monitoring_coverage_pct"] == 100.0

    def test_single_gap_reduces_denominator(self):
        """45-min gap between events → gap_count=1, coverage<100%."""
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 2, 0)
        events = [
            _MockEvent("online", start),
            _MockEvent("online", start + timedelta(minutes=30)),
            # 45-min gap (> GAP_THRESHOLD_S)
            _MockEvent("online", start + timedelta(minutes=75)),
            _MockEvent("online", start + timedelta(minutes=90)),
        ]
        up, inc, cov = _compute_uptime_from_events(events, start, end)
        assert cov["gap_count"] == 1
        assert cov["total_gap_seconds"] == 45 * 60
        assert cov["monitoring_coverage_pct"] < 100.0
        assert up == 100.0  # device was always online when monitored

    def test_gap_during_offline_reduces_incident(self):
        """Device offline, 2h monitoring gap, then online → incident excludes gap."""
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 4, 0)
        events = [
            _MockEvent("online", start),
            _MockEvent("offline", start + timedelta(minutes=30)),
            # 2h monitoring gap
            _MockEvent("online", start + timedelta(minutes=150)),
            _MockEvent("online", start + timedelta(minutes=180)),
        ]
        up, inc, cov = _compute_uptime_from_events(events, start, end)
        assert cov["gap_count"] >= 1
        assert len(inc) == 1
        # Without gap correction, incident = 120 min.
        # With gap correction, incident should be much shorter (30 min max).
        assert inc[0]["duration_min"] <= 30.0

    def test_gap_threshold_boundary_not_a_gap(self):
        """Gap of exactly GAP_THRESHOLD_S does NOT trigger."""
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 1, 0)
        events = [
            _MockEvent("online", start),
            _MockEvent("online", start + timedelta(seconds=GAP_THRESHOLD_S)),
        ]
        up, inc, cov = _compute_uptime_from_events(events, start, end)
        assert cov["gap_count"] == 0

    def test_no_tail_gap_for_event_driven_system(self):
        """Tail gap (last event → window end) is NOT detected because
        availability events are state-change-driven — silence means stable."""
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 4, 0)
        events = [
            _MockEvent("online", start),
            _MockEvent("online", start + timedelta(minutes=30)),
        ]
        up, inc, cov = _compute_uptime_from_events(events, start, end)
        # No inter-event gaps (30 min < GAP_THRESHOLD_S)
        assert cov["gap_count"] == 0
        assert up == 100.0

    def test_coverage_meta_keys(self):
        start = datetime(2026, 3, 1, 0, 0)
        end = datetime(2026, 3, 1, 1, 0)
        events = [_MockEvent("online", start)]
        _, _, cov = _compute_uptime_from_events(events, start, end)
        assert "observed_seconds" in cov
        assert "total_gap_seconds" in cov
        assert "gap_count" in cov
        assert "monitoring_coverage_pct" in cov


# ── downtime_hours with observed_hours ───────────────────────────────────────

def test_downtime_hours_with_observed_hours():
    """When observed_hours is provided, use it instead of period_hours."""
    result = downtime_hours(99.0, 720.0, observed_hours=500.0)
    assert result == pytest.approx(5.0, abs=0.01)   # 1% of 500h


def test_downtime_hours_observed_none_fallback():
    """When observed_hours is None, fall back to period_hours (backward compat)."""
    result = downtime_hours(99.0, 720.0, observed_hours=None)
    assert result == pytest.approx(7.2, abs=0.01)   # 1% of 720h


# ── coverage_level ───────────────────────────────────────────────────────────

def test_coverage_level_high():
    assert coverage_level(95.0) == "high"
    assert coverage_level(100.0) == "high"

def test_coverage_level_medium():
    assert coverage_level(80.0) == "medium"
    assert coverage_level(94.9) == "medium"

def test_coverage_level_low():
    assert coverage_level(50.0) == "low"
    assert coverage_level(0.0) == "low"

def test_coverage_level_unknown():
    assert coverage_level(None) == "unknown"
