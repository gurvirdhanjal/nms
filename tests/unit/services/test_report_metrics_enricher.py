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
