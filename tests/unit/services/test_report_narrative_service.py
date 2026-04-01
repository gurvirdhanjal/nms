"""Tests for PR 16: report_narrative_service.py — per-report narrative generators.

Covers: dispatch routing, executive/alerts/security/device-inspector/server-fleet/
tracked-fleet narration, zero-data handling, delta formatting, None safety.
"""
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────

def _service():
    from services.report_narrative_service import ReportNarrativeService
    svc = ReportNarrativeService()
    # Pre-load thresholds with defaults to avoid DB/import dependency
    svc._thresholds = {
        "uptime_pct": {"warning": 95, "critical": 90, "inverted": True},
        "latency_ms": {"warning": 100, "critical": 200},
        "packet_loss_pct": {"warning": 5, "critical": 15},
        "cpu_usage_pct": {"warning": 80, "critical": 90},
        "memory_usage_pct": {"warning": 75, "critical": 95},
        "disk_usage_pct": {"warning": 90, "critical": 95},
    }
    return svc


_STANDARD_NARRATIVE_KEYS = {
    "executive_banner", "action_required", "section_intro",
    "top_findings", "interpretation", "action_items", "risk_summary",
}


# ── generate_narrative dispatch ──────────────────────────────────────────────

class TestGenerateNarrativeDispatch:

    def test_dispatches_executive(self):
        data = {"total_devices": 10, "uptime_score": 99, "health_distribution": {"Healthy": 10}}
        result = _service().generate_narrative("executive", data)
        assert result is not None
        assert "executive_banner" in result

    def test_dispatches_alerts(self):
        data = {"severity_breakdown": {"CRITICAL": 1}, "top_alerted_devices": []}
        result = _service().generate_narrative("alerts", data)
        assert result is not None

    def test_dispatches_security(self):
        data = {"summary": {"total_alerts": 5, "critical_alerts": 1}}
        result = _service().generate_narrative("security-compliance", data)
        assert result is not None

    def test_dispatches_device_inspector(self):
        data = {"device_name": "Test", "device_ip": "10.0.0.1"}
        result = _service().generate_narrative("device-inspector", data)
        assert result is not None

    def test_dispatches_server_fleet(self):
        data = {"server_rows": [{"device_name": "S1", "uptime_pct": 99}], "summary": {}}
        result = _service().generate_narrative("server-fleet", data)
        assert result is not None

    def test_dispatches_tracked_fleet(self):
        data = {"tracked_rows": [{"device_name": "W1", "uptime_pct": 99}]}
        result = _service().generate_narrative("tracked-fleet", data)
        assert result is not None

    def test_dispatches_operational(self):
        data = {"new_devices": [], "audit_log": []}
        result = _service().generate_narrative("operational", data)
        assert result is not None

    def test_dispatches_device_health(self):
        data = {"summary": [{"device_name": "S1"}], "total_samples": 100}
        result = _service().generate_narrative("device-health", data)
        assert result is not None

    def test_unknown_type_returns_none(self):
        result = _service().generate_narrative("nonexistent-report", {"some": "data"})
        assert result is None


# ── Executive narrative ──────────────────────────────────────────────────────

class TestNarrateExecutive:

    def test_happy_path_all_keys_present(self):
        data = {
            "total_devices": 50,
            "uptime_score": 97.5,
            "avg_latency": 45,
            "health_distribution": {"Healthy": 40, "Critical": 2, "Warning": 8},
            "top_problematic": [],
            "sla_metrics": {"mtta_seconds": 1800},
        }
        result = _service()._narrate_executive(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        # Check KPIs populated
        kpis = result["executive_banner"]["kpis"]
        assert len(kpis) >= 5
        labels = [k["label"] for k in kpis]
        assert "Fleet Availability" in labels
        assert "Total Devices" in labels
        assert "Critical" in labels

    def test_action_required_for_zero_uptime_device(self):
        """Chronically offline devices trigger a summary warning via chronically_offline key."""
        data = {
            "total_devices": 10,
            "uptime_score": 80,
            "avg_latency": 50,
            "health_distribution": {"Healthy": 8, "Critical": 2},
            "top_problematic": [],
            "chronically_offline": {
                "count": 1,
                "devices": [{"name": "Dead-Server", "ip": "10.0.0.99"}],
                "note": "Devices with 0% uptime",
            },
        }
        result = _service()._narrate_executive(data)
        assert len(result["action_required"]) >= 1
        assert result["action_required"][0]["severity"] == "warning"
        assert "offline" in result["action_required"][0]["text"].lower()

    def test_zero_data_returns_no_monitoring_message(self):
        data = {"total_devices": 0}
        result = _service()._narrate_executive(data)
        assert "No monitoring data" in result["section_intro"]

    def test_delta_positive(self):
        data = {
            "total_devices": 10,
            "uptime_score": 97.0,
            "avg_latency": 50,
            "health_distribution": {"Healthy": 9, "Critical": 1},
            "prev_uptime_score": 94.0,
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        # Trend should contain up-arrow and pp
        assert "\u2191" in result["section_intro"]  # up-arrow
        assert "pp" in result["section_intro"]

    def test_delta_negative(self):
        data = {
            "total_devices": 10,
            "uptime_score": 90.0,
            "avg_latency": 50,
            "health_distribution": {"Healthy": 8, "Critical": 2},
            "prev_uptime_score": 95.0,
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        assert "\u2193" in result["section_intro"]  # down-arrow

    def test_delta_none_previous(self):
        """No prev_uptime_score → no trend in intro."""
        data = {
            "total_devices": 10,
            "uptime_score": 97.0,
            "avg_latency": 50,
            "health_distribution": {"Healthy": 9, "Critical": 1},
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        assert "Trend:" not in result["section_intro"]

    def test_high_latency_produces_finding(self):
        data = {
            "total_devices": 10,
            "uptime_score": 99.0,
            "avg_latency": 250,
            "health_distribution": {"Healthy": 10},
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        # Should have a critical finding about latency
        sev_texts = [(f["severity"], f["text"]) for f in result["top_findings"]]
        assert any("250ms" in t and sev == "critical" for sev, t in sev_texts)

    def test_all_healthy_produces_ok_finding(self):
        data = {
            "total_devices": 10,
            "uptime_score": 99.5,
            "avg_latency": 20,
            "health_distribution": {"Healthy": 10},
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        assert any(f["severity"] == "ok" for f in result["top_findings"])


# ── Alerts narrative ─────────────────────────────────────────────────────────

class TestNarrateAlerts:

    def test_happy_path(self):
        data = {
            "severity_breakdown": {"CRITICAL": 5, "WARNING": 20},
            "top_alerted_devices": [
                {"device_name": "Switch-01", "alert_count": 15},
            ],
            "tta": {"seconds": 7200},
            "alerts": [],
        }
        result = _service()._narrate_alerts(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        kpis = result["executive_banner"]["kpis"]
        labels = [k["label"] for k in kpis]
        assert "Total Alerts" in labels
        assert "Critical" in labels
        assert "Avg TTA" in labels
        # Findings sorted by severity
        severities = [f["severity"] for f in result["top_findings"]]
        if "critical" in severities and "warning" in severities:
            assert severities.index("critical") < severities.index("warning")

    def test_zero_alerts_returns_no_alerts_message(self):
        data = {"severity_breakdown": {}}
        result = _service()._narrate_alerts(data)
        assert "No alerts recorded" in result["section_intro"]
        # Should still have the "ok" finding
        assert any(f["severity"] == "ok" for f in result["top_findings"])

    def test_tta_sla_exceeded(self):
        """TTA > 3600s → finding about SLA."""
        data = {
            "severity_breakdown": {"WARNING": 10},
            "top_alerted_devices": [],
            "tta": {"seconds": 7200},
            "alerts": [],
        }
        result = _service()._narrate_alerts(data)
        tta_findings = [f for f in result["top_findings"] if "SLA" in f["text"] or "TTA" in f["text"]]
        assert len(tta_findings) >= 1


# ── Security narrative ───────────────────────────────────────────────────────

class TestNarrateSecurity:

    def test_ai_violations_produce_critical_finding(self):
        data = {
            "summary": {
                "total_alerts": 10, "critical_alerts": 2,
                "unresolved_alerts": 1, "acknowledged_alerts": 5,
                "restricted_site_violations": 15, "integrity_findings": 0,
                "threshold_breaches": 0,
            },
            "restricted_site_violations": [
                {"domain": "chatgpt.com", "count": 10},
                {"domain": "youtube.com", "count": 5},
            ],
            "recent_alerts": [],
        }
        result = _service()._narrate_security(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        # Should have a critical finding about AI
        ai_findings = [f for f in result["top_findings"]
                       if "AI" in f["text"] or "exfiltration" in f["text"].lower()]
        assert len(ai_findings) >= 1

    def test_zero_events_returns_no_data(self):
        data = {"summary": {"total_alerts": 0, "restricted_site_violations": 0}}
        result = _service()._narrate_security(data)
        assert "No monitoring data" in result["section_intro"]

    def test_interpretation_mentions_ai(self):
        data = {
            "summary": {
                "total_alerts": 5, "critical_alerts": 1,
                "unresolved_alerts": 0, "acknowledged_alerts": 5,
                "restricted_site_violations": 10, "integrity_findings": 0,
                "threshold_breaches": 0,
            },
            "restricted_site_violations": [
                {"domain": "chatgpt.com", "count": 10},
            ],
            "recent_alerts": [],
        }
        result = _service()._narrate_security(data)
        assert "AI" in result["interpretation"]


# ── Device Inspector narrative ───────────────────────────────────────────────

class TestNarrateDeviceInspector:

    def test_high_packet_loss_impact_language(self):
        """55% packet loss → specific impact language about unusable device."""
        data = {
            "device_name": "Switch-01", "device_ip": "10.0.0.1",
            "device_type": "switch", "status": "degraded",
            "avg_packet_loss_pct": 55,
        }
        result = _service()._narrate_device_inspector(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        # Look for the impact language
        pkt_findings = [f for f in result["top_findings"] if "packet loss" in f["text"].lower()]
        assert len(pkt_findings) >= 1
        assert "unusable" in pkt_findings[0]["text"].lower() or "fail" in pkt_findings[0]["text"].lower()

    def test_high_latency_remote_desktop_lag(self):
        """353ms latency → 'remote desktop' lag language."""
        data = {
            "device_name": "Workstation-01", "device_ip": "10.0.0.5",
            "device_type": "workstation", "status": "online",
            "avg_latency_ms": 353,
        }
        result = _service()._narrate_device_inspector(data)
        # Interpretation should mention remote desktop lag
        assert "remote desktop" in result["interpretation"].lower() or \
               "lag" in result["interpretation"].lower()

    def test_healthy_device_ok_finding(self):
        """Device with no issues → ok finding."""
        data = {
            "device_name": "Server-Main", "device_ip": "10.0.0.1",
            "device_type": "server", "status": "online",
            "uptime_pct": 99.9, "avg_latency_ms": 5, "avg_packet_loss_pct": 0,
        }
        result = _service()._narrate_device_inspector(data)
        assert any(f["severity"] == "ok" for f in result["top_findings"])

    def test_low_uptime_critical_finding(self):
        """<90% uptime → critical finding."""
        data = {
            "device_name": "Switch-Bad", "device_ip": "10.0.0.2",
            "device_type": "switch", "status": "degraded",
            "uptime_pct": 80.0,
        }
        result = _service()._narrate_device_inspector(data)
        assert any(f["severity"] == "critical" for f in result["top_findings"])

    def test_none_metrics_no_type_error(self):
        """All metrics None → should not raise TypeError."""
        data = {
            "device_name": "Unknown-Dev", "device_ip": "10.0.0.3",
            "device_type": "unknown", "status": "unknown",
            "uptime_pct": None, "avg_latency_ms": None, "avg_packet_loss_pct": None,
        }
        result = _service()._narrate_device_inspector(data)
        assert result is not None
        assert isinstance(result["top_findings"], list)


# ── Server Fleet narrative ───────────────────────────────────────────────────

class TestNarrateServerFleet:

    def test_mixed_sla_tiers(self):
        data = {
            "server_rows": [
                {"device_name": "Gold-1", "device_ip": "10.0.0.1", "uptime_pct": 99.9, "avg_cpu": 30},
                {"device_name": "Crit-1", "device_ip": "10.0.0.2", "uptime_pct": 85.0, "avg_cpu": 50},
                {"device_name": "Gold-2", "device_ip": "10.0.0.3", "uptime_pct": 99.5, "avg_cpu": 40},
            ],
            "summary": {
                "sla_distribution": {"Gold": 2, "Critical": 1, "Unknown": 0},
                "fleet_avg_uptime": 94.8,
            },
        }
        result = _service()._narrate_server_fleet(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        kpis = result["executive_banner"]["kpis"]
        labels = [k["label"] for k in kpis]
        assert "Infrastructure Devices" in labels
        assert "Gold SLA" in labels
        assert "Critical SLA" in labels
        # Should have findings for the low-uptime device
        assert any("Crit-1" in f["text"] or "85" in f["text"] for f in result["top_findings"])

    def test_empty_server_rows_returns_zero_data(self):
        data = {"server_rows": [], "summary": {}}
        result = _service()._narrate_server_fleet(data)
        assert "No monitoring data" in result["section_intro"]

    def test_action_items_for_offline_device(self):
        data = {
            "server_rows": [
                {"device_name": "Dead-Server", "device_ip": "10.0.0.1", "uptime_pct": 0.0, "avg_cpu": 0},
            ],
            "summary": {
                "sla_distribution": {"Critical": 1},
                "fleet_avg_uptime": 0.0,
            },
        }
        result = _service()._narrate_server_fleet(data)
        assert len(result["action_required"]) >= 1
        assert "persistently offline" in result["action_items"][0].lower() or \
               "Investigate" in result["action_items"][0]


# ── Tracked/Workstation Fleet narrative ──────────────────────────────────────

class TestNarrateTrackedFleet:

    def test_high_flapping_populates_action_required(self):
        data = {
            "tracked_rows": [
                {
                    "device_name": "Laptop-01", "device_ip": "10.0.0.10",
                    "uptime_pct": 92.0, "mttr_min": 15,
                    "incident_count": 20, "flapping_score": 0.8,
                },
            ],
        }
        result = _service()._narrate_tracked_fleet(data)
        assert _STANDARD_NARRATIVE_KEYS.issubset(result.keys())
        assert len(result["action_required"]) >= 1
        assert "flapping" in result["action_required"][0]["text"].lower()

    def test_empty_tracked_rows_returns_zero_data(self):
        data = {"tracked_rows": []}
        result = _service()._narrate_tracked_fleet(data)
        assert "No monitoring data" in result["section_intro"]

    def test_high_mttr_finding(self):
        data = {
            "tracked_rows": [
                {
                    "device_name": "Laptop-Slow", "device_ip": "10.0.0.11",
                    "uptime_pct": 95.0, "mttr_min": 120,
                    "incident_count": 5, "flapping_score": 0.1,
                },
            ],
        }
        result = _service()._narrate_tracked_fleet(data)
        mttr_findings = [f for f in result["top_findings"] if "MTTR" in f["text"]]
        assert len(mttr_findings) >= 1

    def test_many_incidents_finding(self):
        data = {
            "tracked_rows": [
                {
                    "device_name": "Laptop-Noisy", "device_ip": "10.0.0.12",
                    "uptime_pct": 90.0, "mttr_min": 10,
                    "incident_count": 25, "flapping_score": 0.1,
                },
            ],
        }
        result = _service()._narrate_tracked_fleet(data)
        inc_findings = [f for f in result["top_findings"] if "incidents" in f["text"].lower()]
        assert len(inc_findings) >= 1


# ── Hostile case: mixed uptime outlier ───────────────────────────────────────

class TestHostileCase:

    def test_100_and_0_uptime_calls_out_outlier(self):
        """Fleet with 100% and 0% uptime → narrative uses chronically_offline summary."""
        data = {
            "total_devices": 2,
            "uptime_score": 50.0,
            "avg_latency": 30,
            "health_distribution": {"Healthy": 1, "Critical": 1},
            "top_problematic": [
                {"name": "Healthy-Box", "ip": "10.0.0.1", "uptime": 100.0},
            ],
            "chronically_offline": {
                "count": 1,
                "devices": [{"name": "Dead-Box", "ip": "10.0.0.99"}],
                "note": "Devices with 0% uptime",
            },
        }
        result = _service()._narrate_executive(data)
        # action_required should include the offline summary
        assert len(result["action_required"]) >= 1
        assert any("offline" in ar["text"].lower() for ar in result["action_required"])
        # risk_summary should mention offline
        assert result["risk_summary"] is not None
        assert "offline" in result["risk_summary"].lower()


# ── None/graceful handling across all generators ─────────────────────────────

class TestNoneGraceful:

    def test_executive_none_values(self):
        """Executive with None uptime/latency → no TypeError."""
        data = {
            "total_devices": 5,
            "uptime_score": None,
            "avg_latency": None,
            "health_distribution": {},
            "top_problematic": [],
        }
        result = _service()._narrate_executive(data)
        assert result is not None
        assert isinstance(result["top_findings"], list)

    def test_alerts_none_tta(self):
        """Alerts with None TTA → no TypeError."""
        data = {
            "severity_breakdown": {"WARNING": 3},
            "top_alerted_devices": [],
            "tta": {},
            "alerts": [],
        }
        result = _service()._narrate_alerts(data)
        assert result is not None

    def test_device_inspector_minimal_data(self):
        """Device inspector with only name → no TypeError."""
        data = {"device_name": "Test"}
        result = _service()._narrate_device_inspector(data)
        assert result is not None

    def test_server_fleet_none_summary_fields(self):
        """Server fleet with None summary fields → no TypeError."""
        data = {
            "server_rows": [{"device_name": "S1"}],
            "summary": {
                "sla_distribution": {},
                "fleet_avg_uptime": None,
            },
        }
        result = _service()._narrate_server_fleet(data)
        assert result is not None

    def test_tracked_fleet_none_metric_fields(self):
        """Tracked fleet with None metrics → no TypeError."""
        data = {
            "tracked_rows": [
                {
                    "device_name": "W1", "device_ip": "10.0.0.1",
                    "uptime_pct": None, "mttr_min": None,
                    "incident_count": None, "flapping_score": None,
                },
            ],
        }
        result = _service()._narrate_tracked_fleet(data)
        assert result is not None

    def test_security_none_violation_counts(self):
        """Security with None violation counts → no TypeError."""
        data = {
            "summary": {
                "total_alerts": 1, "critical_alerts": 0,
                "unresolved_alerts": 0, "acknowledged_alerts": 0,
                "restricted_site_violations": 0,
                "integrity_findings": 0, "threshold_breaches": 0,
            },
            "restricted_site_violations": [
                {"domain": "chatgpt.com", "count": None},
            ],
            "recent_alerts": [],
        }
        result = _service()._narrate_security(data)
        assert result is not None
