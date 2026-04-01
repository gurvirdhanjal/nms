"""Unit tests for ReportingServiceBase._degradation_score()."""
import pytest


class TestDegradationScore:
    """Test the composite degradation scoring function."""

    @staticmethod
    def _score(uptime_pct, avg_latency_ms, avg_packet_loss_pct):
        # Import inline to avoid Flask app context requirement at module level
        from services.reporting.base import ReportingServiceBase
        return ReportingServiceBase._degradation_score(uptime_pct, avg_latency_ms, avg_packet_loss_pct)

    def test_all_none_returns_none(self):
        assert self._score(None, None, None) is None

    def test_perfect_health_returns_zero(self):
        assert self._score(100.0, 0, 0) == 0.0

    def test_fully_degraded(self):
        # 0% uptime + 500ms+ latency + 20%+ packet loss = 100
        score = self._score(0.0, 500.0, 20.0)
        assert score == 100.0

    def test_uptime_deficit_weight(self):
        # 80% uptime, no latency/loss → (100-80)*0.5 = 10
        score = self._score(80.0, 0, 0)
        assert score == 10.0

    def test_latency_penalty_weight(self):
        # 100% uptime, 250ms latency → 0 + min(250/500,1)*25 = 12.5
        score = self._score(100.0, 250.0, 0)
        assert score == 12.5

    def test_latency_capped_at_25(self):
        # 100% uptime, 1000ms latency → 0 + min(1000/500,1)*25 = 25
        score = self._score(100.0, 1000.0, 0)
        assert score == 25.0

    def test_packet_loss_penalty_weight(self):
        # 100% uptime, 0 latency, 10% loss → 0 + 0 + min(10/20,1)*25 = 12.5
        score = self._score(100.0, 0, 10.0)
        assert score == 12.5

    def test_packet_loss_capped_at_25(self):
        # 100% uptime, 0 latency, 50% loss → 0 + 0 + min(50/20,1)*25 = 25
        score = self._score(100.0, 0, 50.0)
        assert score == 25.0

    def test_combined_degradation(self):
        # 90% uptime, 200ms latency, 5% loss
        # (100-90)*0.5 + min(200/500,1)*25 + min(5/20,1)*25
        # = 5.0 + 10.0 + 6.25 = 21.25
        score = self._score(90.0, 200.0, 5.0)
        assert score == 21.25

    def test_uptime_none_treated_as_worst(self):
        # None uptime → 50.0 penalty (worst)
        score = self._score(None, 0, 0)
        assert score == 50.0

    def test_latency_none_no_penalty(self):
        # None latency → 0 latency penalty
        score = self._score(100.0, None, 0)
        assert score == 0.0

    def test_packet_loss_none_no_penalty(self):
        score = self._score(100.0, 0, None)
        assert score == 0.0

    def test_negative_uptime_clamped(self):
        # Negative uptime clamped to 0 → (100-0)*0.5 = 50
        score = self._score(-10.0, 0, 0)
        assert score == 50.0

    def test_uptime_over_100_clamped(self):
        # >100 clamped to 100 → (100-100)*0.5 = 0
        score = self._score(150.0, 0, 0)
        assert score == 0.0

    def test_result_is_rounded(self):
        score = self._score(99.0, 100.0, 3.0)
        # (100-99)*0.5 + min(100/500,1)*25 + min(3/20,1)*25
        # = 0.5 + 5.0 + 3.75 = 9.25
        assert score == 9.25
        assert isinstance(score, float)

    def test_only_uptime_provided(self):
        score = self._score(95.0, None, None)
        assert score == 2.5  # (100-95)*0.5
