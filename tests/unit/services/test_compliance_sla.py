"""Tests for PR 2: ComplianceProfile SLA integration in enterprise reports.

Covers: sla_tier with custom thresholds, fallback to defaults,
partial rules_json, and _bulk_load_sla_thresholds.
"""
import pytest

pytestmark = pytest.mark.unit


class TestSlaTierWithCustomThresholds:

    def test_custom_gold_threshold(self):
        from services.enterprise_report_service import sla_tier
        # Custom: Gold at 99.5 instead of 99.9
        thresholds = {"sla_gold": 99.5}
        assert sla_tier(99.5, thresholds) == "Gold"
        assert sla_tier(99.6, thresholds) == "Gold"

    def test_custom_gold_higher_than_default(self):
        from services.enterprise_report_service import sla_tier
        # Custom: Gold requires 99.99 (stricter)
        thresholds = {"sla_gold": 99.99}
        assert sla_tier(99.9, thresholds) == "Silver"   # Would be Gold with defaults
        assert sla_tier(99.99, thresholds) == "Gold"

    def test_all_custom_thresholds(self):
        from services.enterprise_report_service import sla_tier
        thresholds = {
            "sla_gold": 98.0,
            "sla_silver": 95.0,
            "sla_bronze": 90.0,
            "sla_warning": 80.0,
        }
        assert sla_tier(99.0, thresholds) == "Gold"
        assert sla_tier(97.0, thresholds) == "Silver"
        assert sla_tier(92.0, thresholds) == "Bronze"
        assert sla_tier(85.0, thresholds) == "Warning"
        assert sla_tier(75.0, thresholds) == "Critical"

    def test_partial_thresholds_fallback(self):
        from services.enterprise_report_service import sla_tier
        # Only override Gold to 99.0; rest use module defaults (silver=99.5, bronze=99.0, warning=95.0)
        thresholds = {"sla_gold": 99.0}
        # 99.0 >= custom gold (99.0) → Gold
        assert sla_tier(99.0, thresholds) == "Gold"
        # 99.49 >= custom gold (99.0) → Gold
        assert sla_tier(99.49, thresholds) == "Gold"
        # 98.9 < custom gold (99.0), < default silver (99.5), < default bronze (99.0), >= default warning (95.0)
        assert sla_tier(98.9, thresholds) == "Warning"
        # 96.0 < gold/silver/bronze, >= warning (95.0)
        assert sla_tier(96.0, thresholds) == "Warning"
        # 94.0 < all thresholds → Critical
        assert sla_tier(94.0, thresholds) == "Critical"

    def test_none_thresholds_uses_defaults(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.9, None) == "Gold"
        assert sla_tier(99.5, None) == "Silver"

    def test_empty_thresholds_uses_defaults(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(99.9, {}) == "Gold"
        assert sla_tier(99.5, {}) == "Silver"

    def test_none_uptime_always_unknown(self):
        from services.enterprise_report_service import sla_tier
        assert sla_tier(None, {"sla_gold": 99.0}) == "Unknown"
        assert sla_tier(None, None) == "Unknown"


class TestBulkLoadSlaThresholds:

    def test_empty_profile_map(self):
        from services.enterprise_report_service import _bulk_load_sla_thresholds
        result, usage = _bulk_load_sla_thresholds({})
        assert result == {}
        assert usage == {}

    def test_all_none_profiles(self):
        from services.enterprise_report_service import _bulk_load_sla_thresholds
        result, usage = _bulk_load_sla_thresholds({1: None, 2: None})
        assert result == {}
        assert usage == {}
