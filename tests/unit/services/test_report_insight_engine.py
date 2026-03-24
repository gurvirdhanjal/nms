"""Tests for PR 13: Rule-based report insight engine.

Covers: severity classification, finding generation, metric severity mapping,
Gemini fallback behavior, and response validation.
"""
import pytest

pytestmark = pytest.mark.unit


def _engine():
    from services.report_insight_engine import ReportInsightEngine
    return ReportInsightEngine()


def _make_report(uptime=99.5, latency=50, cpu=45, mem=60, disk=55,
                 tracked_rows=None, violations=None):
    return {
        "uptime_score": uptime,
        "avg_latency": latency,
        "summary": {
            "fleet_avg_uptime": uptime,
            "fleet_avg_cpu": cpu,
            "fleet_avg_mem": mem,
            "fleet_avg_disk": disk,
            "total_devices": 10,
        },
        "tracked_rows": tracked_rows or [],
        "violations": violations or {},
    }


class TestSeverityClassification:

    def test_uptime_critical(self):
        assert _engine().classify_severity("uptime_pct", 85) == "critical"

    def test_uptime_warning(self):
        assert _engine().classify_severity("uptime_pct", 92) == "warning"

    def test_uptime_healthy(self):
        assert _engine().classify_severity("uptime_pct", 99.9) == "healthy"

    def test_latency_critical(self):
        assert _engine().classify_severity("latency_ms", 353) == "critical"

    def test_latency_warning(self):
        assert _engine().classify_severity("latency_ms", 150) == "warning"

    def test_latency_healthy(self):
        assert _engine().classify_severity("latency_ms", 50) == "healthy"

    def test_packet_loss_critical(self):
        assert _engine().classify_severity("packet_loss_pct", 41) == "critical"

    def test_cpu_healthy(self):
        assert _engine().classify_severity("cpu_usage_pct", 45) == "healthy"

    def test_cpu_warning(self):
        assert _engine().classify_severity("cpu_usage_pct", 85) == "warning"

    def test_none_value_nodata(self):
        assert _engine().classify_severity("uptime_pct", None) == "nodata"

    def test_unknown_metric_healthy(self):
        assert _engine().classify_severity("unknown_metric", 999) == "healthy"


class TestFindingGeneration:

    def test_healthy_report_no_findings(self):
        report = _make_report(uptime=99.9, latency=5, cpu=30, mem=40, disk=50)
        findings = _engine().generate_findings(report)
        assert findings == []

    def test_critical_uptime_generates_finding(self):
        report = _make_report(uptime=76.2)
        findings = _engine().generate_findings(report)
        uptime_findings = [f for f in findings if f["metric"] == "uptime_score"]
        assert len(uptime_findings) == 1
        assert uptime_findings[0]["severity"] == "critical"
        assert "76.2%" in uptime_findings[0]["text"]

    def test_high_latency_generates_finding(self):
        report = _make_report(latency=353)
        findings = _engine().generate_findings(report)
        lat_findings = [f for f in findings if f["metric"] == "avg_latency"]
        assert len(lat_findings) == 1
        assert lat_findings[0]["severity"] == "critical"

    def test_max_5_findings(self):
        # Everything critical
        report = _make_report(uptime=50, latency=500, cpu=99, mem=99, disk=99)
        report["violations"] = {"total_site_violations": 100, "total_typed_text_alerts": 50}
        findings = _engine().generate_findings(report)
        assert len(findings) <= 5

    def test_flapping_detection(self):
        tracked = [{"device_name": "PC-001", "flapping_score": 0.9}]
        report = _make_report(tracked_rows=tracked)
        findings = _engine().generate_findings(report)
        flap_findings = [f for f in findings if f["metric"] == "flapping_score"]
        assert len(flap_findings) == 1
        assert flap_findings[0]["severity"] == "warning"

    def test_violation_spike(self):
        report = _make_report(violations={"total_site_violations": 30, "total_typed_text_alerts": 5})
        findings = _engine().generate_findings(report)
        viol_findings = [f for f in findings if f["metric"] == "total_violations"]
        assert len(viol_findings) == 1


class TestGenerateInsights:

    def test_healthy_report_summary(self):
        report = _make_report(uptime=99.9, latency=5)
        insights = _engine().generate_insights(report)
        assert "acceptable" in insights["executive_summary"].lower()
        assert insights["insight_source"] == "rule_based"

    def test_critical_report_summary(self):
        report = _make_report(uptime=76)
        insights = _engine().generate_insights(report)
        assert "critical" in insights["executive_summary"].lower()

    def test_metric_severities_populated(self):
        report = _make_report(uptime=76, latency=353)
        insights = _engine().generate_insights(report)
        assert insights["metric_severities"]["uptime_score"] == "critical"
        assert insights["metric_severities"]["avg_latency"] == "critical"

    def test_recommendations_deduped(self):
        report = _make_report(uptime=76, latency=353)
        insights = _engine().generate_insights(report)
        recs = insights["recommendations"]
        assert len(recs) == len(set(recs))  # No duplicates


class TestGeminiFallback:

    def test_no_findings_skips_gemini(self):
        report = _make_report(uptime=99.9)
        insights = _engine().generate_insights(report)
        # enhance_with_gemini should return original when no findings
        enhanced = _engine().enhance_with_gemini(insights, report)
        assert enhanced["insight_source"] == "rule_based"

    def test_invalid_json_returns_original(self):
        from services.report_insight_engine import _parse_gemini_response
        original = {"findings": [], "executive_summary": "test", "recommendations": []}
        result = _parse_gemini_response("not json", original)
        assert result is original

    def test_valid_json_enhances(self):
        from services.report_insight_engine import _parse_gemini_response
        original = {"findings": [{"severity": "critical", "text": "bad"}],
                     "executive_summary": "rule based", "recommendations": ["do X"]}
        gemini_json = '{"executive_summary": "AI enhanced summary", "enhanced_recommendations": ["AI rec 1"]}'
        result = _parse_gemini_response(gemini_json, original)
        assert result["executive_summary"] == "AI enhanced summary"
        assert result["insight_source"] == "gemini_enhanced"

    def test_oversized_summary_rejected(self):
        from services.report_insight_engine import _parse_gemini_response
        original = {"findings": [], "executive_summary": "test", "recommendations": []}
        gemini_json = '{"executive_summary": "' + "x" * 600 + '"}'
        result = _parse_gemini_response(gemini_json, original)
        assert result["executive_summary"] == "test"  # Original preserved
