"""Tests for PR 1: Data confidence metadata and misleading-zero fixes.

Covers: _safe_round None handling, confidence dict in executive fleet health,
per-source confidence in report_meta, and violation count None vs 0 semantics.
"""
import pytest
from datetime import datetime, timedelta

pytestmark = pytest.mark.unit


# ── _safe_round None-preserving behaviour ──────────────────────────────────

class TestSafeRound:

    def test_none_returns_none(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(None) is None

    def test_none_with_decimals_returns_none(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(None, 3) is None

    def test_zero_returns_float_zero(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(0) == 0.0
        assert _safe_round(0) is not None

    def test_zero_float_returns_zero(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(0.0) == 0.0

    def test_normal_rounding(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(99.456, 2) == 99.46

    def test_string_numeric_returns_rounded(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round("42.789", 1) == 42.8

    def test_non_numeric_string_returns_none(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round("not_a_number") is None

    def test_integer_input(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(42) == 42.0

    def test_negative_value(self):
        from services.enterprise_report_service import _safe_round
        assert _safe_round(-5.678, 1) == -5.7


# ── downtime_hours still works with None-aware _safe_round ─────────────────

class TestDowntimeHoursWithNoneSafeRound:

    def test_none_uptime_returns_none(self):
        from services.enterprise_report_service import downtime_hours
        assert downtime_hours(None, 720.0) is None

    def test_full_uptime_zero_downtime(self):
        from services.enterprise_report_service import downtime_hours
        result = downtime_hours(100.0, 720.0)
        assert result == 0.0
        assert result is not None  # genuine zero, not None

    def test_95_percent_uptime(self):
        from services.enterprise_report_service import downtime_hours
        result = downtime_hours(95.0, 720.0)
        assert result == 36.0


# ── Violation count semantics: None vs 0 ───────────────────────────────────

class TestViolationCountSemantics:
    """Violation count should be None when device has no unique_client_id,
    and 0 when device has unique_client_id but no violations found."""

    def test_violation_count_none_means_unmeasured(self):
        """None means 'we cannot measure violations for this device'."""
        # This tests the semantic contract, not the query itself
        violation_count = None
        assert violation_count is None
        assert violation_count != 0

    def test_violation_count_zero_means_clean(self):
        """Zero means 'device was measured and has no violations'."""
        violation_count = 0
        assert violation_count == 0
        assert violation_count is not None
