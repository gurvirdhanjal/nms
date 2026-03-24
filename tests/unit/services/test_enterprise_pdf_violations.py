"""Tests for PR 5: PDF violations section and confidence footnotes.

Covers: _build_violations_section, _build_confidence_footnotes,
and end-to-end PDF generation with violations data.
"""
import io
import pytest

pytestmark = pytest.mark.unit


def _make_report(violations=None, confidence=None):
    """Build a minimal valid report dict for PDF generation."""
    return {
        "period": {
            "start": "2026-03-01T00:00:00",
            "end": "2026-03-21T00:00:00",
            "days": 20,
            "hours": 480.0,
        },
        "summary": {
            "total_devices": 2,
            "server_devices": 1,
            "tracked_devices": 1,
            "devices_with_data": 2,
            "fleet_avg_uptime": 99.5,
            "fleet_avg_cpu": 45.0,
            "fleet_avg_mem": 60.0,
            "fleet_avg_disk": 55.0,
            "agent_deployed_count": 1,
            "sla_distribution": {"Gold": 1, "Silver": 1, "Bronze": 0, "Warning": 0, "Critical": 0, "Unknown": 0},
            "worst_devices": [],
            "best_devices": [],
        },
        "server_rows": [],
        "tracked_rows": [],
        "website_violation_details": [],
        "violations": violations,
        "generated_at": "2026-03-21T12:00:00",
        "_confidence": confidence,
    }


class TestBuildViolationsSection:

    def test_empty_violations_returns_empty(self):
        from services.enterprise_pdf_service import _build_violations_section
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        report = _make_report(violations=None)
        result = _build_violations_section(report, styles)
        assert result == []

    def test_zero_violations_returns_empty(self):
        from services.enterprise_pdf_service import _build_violations_section
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        violations = {
            "restricted_site_events": [],
            "typed_text_alerts": [],
            "total_site_violations": 0,
            "total_typed_text_alerts": 0,
            "top_offenders": [],
            "trend": [],
        }
        report = _make_report(violations=violations)
        result = _build_violations_section(report, styles)
        assert result == []

    def test_with_site_violations_returns_flowables(self):
        from services.enterprise_pdf_service import _build_violations_section
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        violations = {
            "restricted_site_events": [
                {"device_name": "PC-001", "employee_name": "John", "domain": "bad.com", "violation_count": 5, "last_violation": "2026-03-20T10:00:00"},
            ],
            "typed_text_alerts": [],
            "total_site_violations": 5,
            "total_typed_text_alerts": 0,
            "top_offenders": [
                {"device_name": "PC-001", "employee_name": "John", "site_violations": 5, "typed_text_alerts": 0},
            ],
            "trend": [],
        }
        report = _make_report(violations=violations)
        result = _build_violations_section(report, styles)
        assert len(result) > 0  # Should contain flowables

    def test_with_both_violation_types(self):
        from services.enterprise_pdf_service import _build_violations_section
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        violations = {
            "restricted_site_events": [
                {"device_name": "PC-001", "employee_name": "John", "domain": "bad.com", "violation_count": 3, "last_violation": "2026-03-20"},
            ],
            "typed_text_alerts": [
                {"device_name": "PC-001", "employee_name": "John", "pattern_type": "credit_card", "severity": "high", "alert_count": 2, "last_detected": "2026-03-19"},
            ],
            "total_site_violations": 3,
            "total_typed_text_alerts": 2,
            "top_offenders": [],
            "trend": [],
        }
        report = _make_report(violations=violations)
        result = _build_violations_section(report, styles)
        assert len(result) > 0


class TestBuildConfidenceFootnotes:

    def test_empty_confidence_returns_empty(self):
        from services.enterprise_pdf_service import _build_confidence_footnotes
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        report = _make_report(confidence=None)
        result = _build_confidence_footnotes(report, styles)
        assert result == []

    def test_with_confidence_returns_flowables(self):
        from services.enterprise_pdf_service import _build_confidence_footnotes
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        confidence = {
            "fleet_avg_uptime": {"level": "HIGH", "source": "daily_device_stats"},
            "server_fleet": {"level": "MEDIUM", "source": "raw"},
        }
        report = _make_report(confidence=confidence)
        result = _build_confidence_footnotes(report, styles)
        assert len(result) > 0


class TestEnterprisePdfWithViolations:

    def test_pdf_generates_with_violations(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        violations = {
            "restricted_site_events": [
                {"device_name": "PC-001", "employee_name": "John", "domain": "bad.com", "violation_count": 5, "last_violation": "2026-03-20T10:00:00"},
            ],
            "typed_text_alerts": [
                {"device_name": "PC-001", "employee_name": "John", "pattern_type": "credit_card", "severity": "high", "alert_count": 2, "last_detected": "2026-03-19T10:00:00"},
            ],
            "total_site_violations": 5,
            "total_typed_text_alerts": 2,
            "top_offenders": [
                {"device_name": "PC-001", "employee_name": "John", "site_violations": 5, "typed_text_alerts": 2},
            ],
            "trend": [],
        }
        confidence = {
            "fleet_avg_uptime": {"level": "HIGH", "source": "daily_device_stats"},
        }
        report = _make_report(violations=violations, confidence=confidence)
        buf = generate_enterprise_pdf(report, fleet="all")
        assert isinstance(buf, io.BytesIO)
        content = buf.getvalue()
        assert len(content) > 500  # Valid PDF should be reasonably sized
        assert content[:5] == b'%PDF-'
