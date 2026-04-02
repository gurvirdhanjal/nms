"""Tests for PR 16: report_formatting.py — shared formatting helpers.

Covers: timestamp formatting, duration formatting, device name normalization,
safe metric formatters, violation risk classification, severity labels,
report header/footer builders.
"""
import pytest
from datetime import datetime, timezone, timedelta

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mod():
    import services.report_formatting as m
    return m


# ── format_timestamp_utc ─────────────────────────────────────────────────────

class TestFormatTimestampUtc:

    def test_none_returns_na(self):
        assert _mod().format_timestamp_utc(None) == "N/A"

    def test_naive_datetime(self):
        # Naive datetime treated as UTC → converted to IST (+5:30)
        dt = datetime(2026, 3, 15, 14, 30, 0)
        result = _mod().format_timestamp_utc(dt)
        assert result == "15 Mar 2026 20:00 IST"

    def test_utc_aware_datetime(self):
        dt = datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = _mod().format_timestamp_utc(dt)
        assert result == "15 Mar 2026 20:00 IST"

    def test_timezone_aware_converts_to_ist(self):
        est = timezone(timedelta(hours=-5))
        dt = datetime(2026, 3, 15, 10, 0, 0, tzinfo=est)
        result = _mod().format_timestamp_utc(dt)
        # 10:00 EST = 15:00 UTC = 20:30 IST
        assert result == "15 Mar 2026 20:30 IST"

    def test_iso_string_parsed(self):
        # Naive ISO string treated as UTC → IST
        result = _mod().format_timestamp_utc("2026-03-15T14:30:00")
        assert result == "15 Mar 2026 20:00 IST"

    def test_iso_string_with_z_suffix(self):
        result = _mod().format_timestamp_utc("2026-03-15T14:30:00Z")
        assert result == "15 Mar 2026 20:00 IST"

    def test_unparseable_string_returned_as_is(self):
        result = _mod().format_timestamp_utc("not-a-date")
        assert result == "not-a-date"


# ── format_duration ──────────────────────────────────────────────────────────

class TestFormatDuration:

    def test_zero_hours(self):
        assert _mod().format_duration(0) == "0h"

    def test_half_hour(self):
        assert _mod().format_duration(0.5) == "30m"

    def test_two_and_half_hours(self):
        assert _mod().format_duration(2.5) == "2h 30m"

    def test_exact_24h_shows_days(self):
        assert _mod().format_duration(24) == "1 days"

    def test_720h_shows_30_days(self):
        assert _mod().format_duration(720) == "30 days"

    def test_none_returns_na(self):
        assert _mod().format_duration(None) == "N/A"

    def test_negative_returns_na(self):
        assert _mod().format_duration(-5) == "N/A"

    def test_25_hours_shows_days_and_hours(self):
        assert _mod().format_duration(25) == "1 days 1 hours"

    def test_whole_hours_no_minutes(self):
        assert _mod().format_duration(3) == "3h"


# ── normalize_device_display ─────────────────────────────────────────────────

class TestNormalizeDeviceDisplay:

    def test_generic_device_name_uses_ip(self):
        result = _mod().normalize_device_display("Device-192.168.1.100", "192.168.1.100")
        assert result == "192.168.1.100"

    def test_camera_serial_formatted(self):
        result = _mod().normalize_device_display("DS-2CD1234", "10.0.0.5")
        # Upper-case prefix match: name.upper().startswith("DS-2CD")
        assert result == "IP Camera \u2014 DS-2CD1234"

    def test_camera_lowercase_also_matches(self):
        result = _mod().normalize_device_display("ds-2cd5678", "10.0.0.6")
        assert result == "IP Camera \u2014 ds-2cd5678"

    def test_normal_name_returned_as_is(self):
        result = _mod().normalize_device_display("Server-Main", "10.0.0.1")
        assert result == "Server-Main"

    def test_none_name_none_ip_returns_unknown(self):
        result = _mod().normalize_device_display(None, None)
        assert result == "Unknown"

    def test_none_name_with_ip_returns_ip(self):
        result = _mod().normalize_device_display(None, "10.0.0.5")
        assert result == "10.0.0.5"

    def test_generic_device_no_ip_returns_name(self):
        # "Device-X.X.X.X" without ip parameter falls through since ip is None
        result = _mod().normalize_device_display("Device-192.168.1.1")
        assert result == "Device-192.168.1.1"


# ── fmt ──────────────────────────────────────────────────────────────────────

class TestFmt:

    def test_none_returns_na(self):
        assert _mod().fmt(None) == "N/A"

    def test_zero(self):
        assert _mod().fmt(0) == "0.0"

    def test_float_precision(self):
        assert _mod().fmt(92.567) == "92.6"

    def test_negative_value(self):
        assert _mod().fmt(-3.14) == "-3.1"

    def test_string_numeric_input(self):
        assert _mod().fmt("42.5") == "42.5"

    def test_non_numeric_string_returns_fallback(self):
        assert _mod().fmt("abc") == "N/A"

    def test_custom_format_spec(self):
        assert _mod().fmt(92.567, ".2f") == "92.57"

    def test_custom_fallback(self):
        assert _mod().fmt(None, fallback="--") == "--"


# ── fmt_pct ──────────────────────────────────────────────────────────────────

class TestFmtPct:

    def test_none_returns_na(self):
        assert _mod().fmt_pct(None) == "N/A"

    def test_normal_value(self):
        assert _mod().fmt_pct(74.29) == "74.3%"

    def test_zero(self):
        assert _mod().fmt_pct(0) == "0.0%"

    def test_hundred(self):
        assert _mod().fmt_pct(100) == "100.0%"

    def test_string_numeric(self):
        assert _mod().fmt_pct("55.5") == "55.5%"


# ── fmt_ms ───────────────────────────────────────────────────────────────────

class TestFmtMs:

    def test_none_returns_na(self):
        assert _mod().fmt_ms(None) == "N/A"

    def test_normal_value(self):
        assert _mod().fmt_ms(353) == "353ms"

    def test_fractional_value(self):
        assert _mod().fmt_ms(0.5) == "0ms"

    def test_large_value(self):
        assert _mod().fmt_ms(1500) == "1500ms"


# ── classify_violation_risk ──────────────────────────────────────────────────

class TestClassifyViolationRisk:

    def test_chatgpt_com_is_high(self):
        assert _mod().classify_violation_risk("chatgpt.com") == "HIGH"

    def test_chatgpt_subdomain_is_high(self):
        assert _mod().classify_violation_risk("ab.chatgpt.com") == "HIGH"

    def test_claude_ai_is_high(self):
        assert _mod().classify_violation_risk("claude.ai") == "HIGH"

    def test_youtube_com_is_medium(self):
        assert _mod().classify_violation_risk("youtube.com") == "MEDIUM"

    def test_netflix_is_medium(self):
        assert _mod().classify_violation_risk("netflix.com") == "MEDIUM"

    def test_example_com_is_low(self):
        assert _mod().classify_violation_risk("example.com") == "LOW"

    def test_empty_string_is_low(self):
        assert _mod().classify_violation_risk("") == "LOW"

    def test_none_is_low(self):
        assert _mod().classify_violation_risk(None) == "LOW"

    def test_case_insensitive(self):
        assert _mod().classify_violation_risk("ChatGPT.com") == "HIGH"


# ── severity_label ───────────────────────────────────────────────────────────

class TestSeverityLabel:

    def test_critical_with_emoji(self):
        result = _mod().severity_label("critical")
        assert "CRITICAL" in result
        assert "\U0001f534" in result  # red circle emoji

    def test_warning_with_emoji(self):
        result = _mod().severity_label("warning")
        assert "WARNING" in result

    def test_ok_with_emoji(self):
        result = _mod().severity_label("ok")
        assert "OK" in result

    def test_healthy_maps_to_ok(self):
        result = _mod().severity_label("healthy", emoji=False)
        assert result == "OK"

    def test_nodata_label(self):
        result = _mod().severity_label("nodata", emoji=False)
        assert result == "NO DATA"

    def test_unknown_passthrough(self):
        result = _mod().severity_label("unknown", emoji=False)
        assert result == "unknown"

    def test_no_emoji_mode(self):
        result = _mod().severity_label("critical", emoji=False)
        assert result == "CRITICAL"
        # Should not contain emoji
        assert "\U0001f534" not in result

    def test_none_input_does_not_raise(self):
        # (None or "").lower() → "" → not in _SEVERITY_LABELS → returns original `level` (None)
        # The function should not raise; it returns None as passthrough
        result = _mod().severity_label(None, emoji=False)
        assert result is None


# ── build_report_header ──────────────────────────────────────────────────────

class TestBuildReportHeader:

    def test_structure(self):
        header = _mod().build_report_header(
            "executive", "All Sites",
            datetime(2026, 3, 1, 0, 0, 0), datetime(2026, 3, 15, 23, 59, 59),
        )
        assert header["report_type"] == "executive"
        assert header["scope"] == "All Sites"
        assert "Mar 2026" in header["period_start"]
        assert "Mar 2026" in header["period_end"]
        assert "IST" in header["period_start"]
        assert "generated_at" in header
        assert "CONFIDENTIAL" in header["classification"]

    def test_none_dates(self):
        header = _mod().build_report_header("alerts", "Site A", None, None)
        assert header["period_start"] == "N/A"
        assert header["period_end"] == "N/A"


# ── build_report_footer ──────────────────────────────────────────────────────

class TestBuildReportFooter:

    def test_default_structure(self):
        footer = _mod().build_report_footer()
        assert "MEDIUM" in footer["data_confidence"]
        assert "mixed sources" in footer["data_confidence"]
        assert "Network Monitoring System" in footer["generated_by"]

    def test_custom_confidence(self):
        footer = _mod().build_report_footer("HIGH", "agent telemetry")
        assert "HIGH" in footer["data_confidence"]
        assert "agent telemetry" in footer["data_confidence"]


def test_raw_scan_uptime_rows_min_latency_key_present(app):
    """min_latency_ms must be a key in every row returned by _raw_scan_uptime_rows."""
    import pytest
    from unittest.mock import patch
    from services.reporting.base import ReportingServiceBase
    from datetime import datetime, timedelta
    _ADMIN_SCOPE = {
        'role': 'admin', 'scope_type': 'global', 'scope_key': 'global',
        'scope_label': 'Global', 'site_id': None, 'department_id': None,
    }
    with app.app_context():
        from extensions import db
        from models.device import Device
        from models.scan_history import DeviceScanHistory
        end = datetime.utcnow()
        start = end - timedelta(days=1)
        with patch('services.reporting.base.build_scope_context', return_value=_ADMIN_SCOPE):
            svc = ReportingServiceBase()
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
        with patch('services.reporting.base.scoped_query', side_effect=lambda m: m.query):
            rows = svc._raw_scan_uptime_rows(
                device_ids=[dev.device_id], start_date=start, end_date=end
            )
        assert len(rows) == 1
        row = rows[0]
        assert hasattr(row, "min_latency_ms") or "min_latency_ms" in dict(row._mapping)
        assert row.min_latency_ms == pytest.approx(12.5, abs=0.1)
        db.session.rollback()
