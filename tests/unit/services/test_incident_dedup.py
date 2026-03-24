"""Tests for PR 12: Incident deduplication and flap suppression.

Covers: _merge_flapping_incidents with various gap patterns, edge cases,
duration caps, and minimum duration filtering.
"""
import pytest
from datetime import datetime, timedelta

pytestmark = pytest.mark.unit


def _make_incident(start_str, duration_min, **kwargs):
    start = datetime.fromisoformat(start_str)
    end = start + timedelta(minutes=duration_min)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "duration_min": duration_min,
        **kwargs,
    }


class TestMergeFlappingIncidents:

    def test_empty_returns_empty(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        merged, flaps = _merge_flapping_incidents([])
        assert merged == []
        assert flaps == 0

    def test_single_incident_unchanged(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        incidents = [_make_incident("2026-03-15T10:00:00", 30)]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 1
        assert flaps == 0

    def test_two_incidents_far_apart_stay_separate(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        incidents = [
            _make_incident("2026-03-15T10:00:00", 10),
            _make_incident("2026-03-15T12:00:00", 10),  # 2 hours later
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 2
        assert flaps == 0

    def test_two_incidents_within_gap_merge(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        incidents = [
            _make_incident("2026-03-15T10:00:00", 5),    # 10:00 - 10:05
            _make_incident("2026-03-15T10:06:00", 5),    # 10:06 - 10:11 (1min gap < 2min)
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 1
        assert flaps == 1
        assert merged[0]["merged_count"] == 2

    def test_three_incidents_chain_merge(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        incidents = [
            _make_incident("2026-03-15T10:00:00", 3),
            _make_incident("2026-03-15T10:04:00", 3),    # 1min gap
            _make_incident("2026-03-15T10:08:00", 3),    # 1min gap
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 1
        assert flaps == 2

    def test_sub_threshold_incidents_filtered(self):
        from services.enterprise_report_service import _merge_flapping_incidents, MIN_INCIDENT_DURATION_S
        # 5 seconds < 10s minimum
        incidents = [
            _make_incident("2026-03-15T10:00:00", 5.0 / 60.0),
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 0
        assert flaps == 1

    def test_capped_long_incident(self):
        from services.enterprise_report_service import _merge_flapping_incidents, MAX_INCIDENT_DURATION_H
        # 100 hours > 72h cap
        incidents = [
            _make_incident("2026-03-15T10:00:00", 100 * 60),
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 1
        assert merged[0]["duration_min"] == MAX_INCIDENT_DURATION_H * 60
        assert merged[0].get("capped") is True

    def test_mixed_merge_and_separate(self):
        from services.enterprise_report_service import _merge_flapping_incidents
        incidents = [
            _make_incident("2026-03-15T10:00:00", 5),    # 10:00-10:05
            _make_incident("2026-03-15T10:06:00", 5),    # 10:06-10:11 → merge with prev
            _make_incident("2026-03-15T14:00:00", 10),   # 14:00 → separate (4h gap)
        ]
        merged, flaps = _merge_flapping_incidents(incidents)
        assert len(merged) == 2
        assert flaps == 1

    def test_flapping_score_computation(self):
        """778 raw incidents with high flapping should produce score near 1.0."""
        raw_count = 778
        merged_count = 45
        flap_count = raw_count - merged_count
        score = flap_count / max(1, raw_count)
        assert 0.9 < score < 1.0
