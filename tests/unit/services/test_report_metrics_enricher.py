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
        "min_latency_ms": 8.1,
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
    """30d period, 98.5% uptime -> 98.5% of 720h = 709.2h."""
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
