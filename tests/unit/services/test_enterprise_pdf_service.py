"""Tests for services/enterprise_pdf_service.py.

Covers: empty report → valid PDF, fleet filtering, None values in rows.
Pure function tests — no DB or mocks needed (takes a dict, returns BytesIO).
"""
import io
import pytest

pytestmark = pytest.mark.unit


def _minimal_report(**overrides):
    """Minimal valid report structure for testing."""
    report = {
        "period": {"start": "2026-03-01T00:00:00", "end": "2026-03-15T00:00:00", "days": 14, "hours": 336.0},
        "summary": {
            "total_devices": 0,
            "server_devices": 0,
            "tracked_devices": 0,
            "devices_with_data": 0,
            "fleet_avg_uptime": None,
            "fleet_avg_cpu": None,
            "fleet_avg_mem": None,
            "fleet_avg_disk": None,
            "agent_deployed_count": 0,
            "sla_distribution": {"Gold": 0, "Silver": 0, "Bronze": 0, "Warning": 0, "Critical": 0, "Unknown": 0},
            "worst_devices": [],
            "best_devices": [],
        },
        "server_rows": [],
        "tracked_rows": [],
        "generated_at": "2026-03-15T12:00:00",
    }
    report.update(overrides)
    return report


def _server_row(**overrides):
    """Minimal server row."""
    row = {
        "device_id": 1, "device_name": "server-01", "device_ip": "10.0.0.1",
        "device_type": "server", "uptime_pct": 99.95, "downtime_hours": 0.17,
        "sla_tier": "Gold", "avg_cpu": 45.2, "max_cpu": 82.1, "avg_mem": 60.0,
        "max_mem": 78.0, "avg_disk": 55.0, "max_disk": 70.0, "avg_load_1m": 1.2,
        "avg_net_in_bps": 5000.0, "avg_net_out_bps": 3000.0, "avg_disk_read_ms": 2.1,
        "avg_disk_write_ms": 3.5, "sample_count": 100, "data_source": "hourly",
        "avg_latency_ms": 5.0, "max_latency_ms": 12.0, "avg_packet_loss_pct": 0.1,
        "total_alerts": 2,
    }
    row.update(overrides)
    return row


def _tracked_row(**overrides):
    """Minimal tracked row."""
    row = {
        "device_id": 100, "device_name": "LAPTOP-01", "employee_name": "John Doe",
        "device_ip": "192.168.1.50", "hostname": "laptop-01", "department": "IT",
        "probe_method": "agent", "last_agent_sync_at": None, "uptime_pct": 98.5,
        "downtime_hours": 5.04, "sla_tier": "Bronze", "incident_count": 3,
        "mttr_min": 15.0, "mtbf_hours": 24.0, "last_seen": "2026-03-14T18:30:00",
        "availability_status": "online", "total_keyboard_events": 5000,
        "total_mouse_events": 8000, "total_active_hours": 40.0, "avg_active_hours_day": 5.7,
        "avg_cpu_during_active": 35.0, "top_app": "VS Code", "policy_violations": 0,
        "productivity_score": 72.5, "focus_score": 65.0,
    }
    row.update(overrides)
    return row


class TestGenerateEnterprisePdf:

    def test_empty_report_produces_valid_pdf(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        result = generate_enterprise_pdf(_minimal_report())
        assert isinstance(result, io.BytesIO)
        assert result.getvalue().startswith(b'%PDF-')

    def test_server_fleet_omits_tracked_section(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        report = _minimal_report(
            server_rows=[_server_row()],
            summary={**_minimal_report()["summary"], "server_devices": 1, "total_devices": 1},
        )
        result = generate_enterprise_pdf(report, fleet="server")
        content = result.getvalue()
        assert content.startswith(b'%PDF-')
        assert len(content) > 500  # non-trivial PDF

    def test_workstation_fleet_omits_server_section(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        report = _minimal_report(
            tracked_rows=[_tracked_row()],
            summary={**_minimal_report()["summary"], "tracked_devices": 1, "total_devices": 1},
        )
        result = generate_enterprise_pdf(report, fleet="workstation")
        content = result.getvalue()
        assert content.startswith(b'%PDF-')
        assert len(content) > 500

    def test_none_values_in_rows_no_crash(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        report = _minimal_report(
            server_rows=[_server_row(uptime_pct=None, avg_cpu=None, avg_latency_ms=None)],
            tracked_rows=[_tracked_row(uptime_pct=None, mttr_min=None, last_seen=None)],
            summary={**_minimal_report()["summary"], "total_devices": 2},
        )
        result = generate_enterprise_pdf(report, fleet="all")
        assert result.getvalue().startswith(b'%PDF-')

    def test_invalid_fleet_defaults_to_all(self):
        from services.enterprise_pdf_service import generate_enterprise_pdf
        result = generate_enterprise_pdf(_minimal_report(), fleet="bogus")
        assert result.getvalue().startswith(b'%PDF-')
