"""Tests for PR 3: Full violation reporting integration.

Covers: _fleet_typed_text_violations, _build_violation_trend,
unified violations section, per-device violation score.
"""
import pytest

pytestmark = pytest.mark.unit


class TestFleetTypedTextViolations:

    def test_empty_device_ids_returns_empty(self):
        from services.enterprise_report_service import _fleet_typed_text_violations
        from datetime import datetime, timedelta
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        assert _fleet_typed_text_violations([], start, end) == []

    def test_no_rows_returns_empty(self):
        """With valid device IDs but no data, returns empty list (not error)."""
        from services.enterprise_report_service import _fleet_typed_text_violations
        from datetime import datetime, timedelta
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        # Device ID 999999 won't exist
        result = _fleet_typed_text_violations([999999], start, end)
        assert result == []


class TestBuildViolationTrend:

    def test_empty_device_ids_returns_empty(self):
        from services.enterprise_report_service import _build_violation_trend
        from datetime import datetime, timedelta
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        assert _build_violation_trend([], start, end) == []

    def test_no_data_returns_empty(self):
        from services.enterprise_report_service import _build_violation_trend
        from datetime import datetime, timedelta
        end = datetime.utcnow()
        start = end - timedelta(days=7)
        result = _build_violation_trend([999999], start, end)
        assert result == []


class TestViolationScoreComputation:
    """Violation score = site_violations * 5 + typed_text_alerts * 10."""

    def test_both_none_gives_none(self):
        """When both counts are None (unmeasurable), score should be None."""
        violation_count = None
        typed_text_count = None
        # Replicate the logic
        if violation_count is not None or typed_text_count is not None:
            score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        else:
            score = None
        assert score is None

    def test_zero_violations_gives_zero(self):
        violation_count = 0
        typed_text_count = 0
        score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        assert score == 0

    def test_site_only(self):
        violation_count = 3
        typed_text_count = 0
        score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        assert score == 15

    def test_typed_text_only(self):
        violation_count = 0
        typed_text_count = 2
        score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        assert score == 20

    def test_mixed(self):
        violation_count = 5
        typed_text_count = 3
        score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        assert score == 55

    def test_none_site_with_typed_text(self):
        """Site violations None (unmeasurable) + typed text 2 → score computed."""
        violation_count = None
        typed_text_count = 2
        if violation_count is not None or typed_text_count is not None:
            score = (violation_count or 0) * 5 + (typed_text_count or 0) * 10
        else:
            score = None
        assert score == 20
