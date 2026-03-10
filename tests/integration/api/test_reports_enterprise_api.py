import logging
import time
import uuid
from datetime import datetime, timedelta

import pytest

from extensions import db
from models.dashboard import DailyDeviceStats, DashboardEvent
from models.department import Department
from models.device import Device
from models.report_export_job import ReportExportJob
from models.scan_history import DeviceScanHistory
from models.server_health import ServerHealthLog


pytestmark = pytest.mark.integration


def _scoped_device(name, ip, *, site_id=None, department_id=None, device_type="Server"):
    device = Device(
        device_name=name,
        device_type=device_type,
        device_ip=ip,
        site_id=site_id,
        department_id=department_id,
    )
    db.session.add(device)
    db.session.flush()
    return device


def _alert(device, *, severity="CRITICAL", timestamp=None, message=None):
    db.session.add(
        DashboardEvent(
            event_id=str(uuid.uuid4()),
            device_id=device.device_id,
            device_ip=device.device_ip,
            event_type="THRESHOLD",
            severity=severity,
            message=message or f"{device.device_name} alert",
            timestamp=timestamp or datetime.utcnow(),
            resolved=False,
            is_acknowledged=False,
        )
    )


def test_executive_report_includes_meta_and_backfill_warning(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Exec-1", "10.10.1.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    db.session.add(
        DeviceScanHistory(
            device_ip=device.device_ip,
            device_name=device.device_name,
            status="Online",
            scan_timestamp=now - timedelta(minutes=5),
            ping_time_ms=4.2,
        )
    )
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/executive?start={(now - timedelta(days=30)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    meta = payload["meta"]
    assert meta["scope_type"] == "global"
    assert meta["row_count"] >= 0
    assert meta["freshness_state"] == "fresh"
    assert "pdf" in meta["exportable_formats"]
    assert any("daily_device_stats" in warning for warning in meta["completeness_warnings"])


def test_executive_report_flags_low_rollup_coverage(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Exec-Coverage", "10.10.1.11", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    db.session.add(
        DeviceScanHistory(
            device_ip=device.device_ip,
            device_name=device.device_name,
            status="Online",
            scan_timestamp=now - timedelta(minutes=10),
            ping_time_ms=3.3,
        )
    )
    db.session.add(
        DailyDeviceStats(
            device_id=device.device_id,
            date=(now - timedelta(days=1)).date(),
            uptime_percent=99.0,
            total_scans=24,
            online_scans=24,
        )
    )
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/executive?start={(now - timedelta(days=7)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["meta"]["freshness_state"] == "fresh"
    assert any("rollup_coverage_low" in warning for warning in payload["meta"]["completeness_warnings"])


def test_alert_report_meta_can_be_delayed(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Delay-Alert", "10.10.1.12", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now - timedelta(hours=4), message="delay-window")
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/alerts?start={(now - timedelta(hours=6)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    assert response.get_json()["meta"]["freshness_state"] == "delayed"


def test_alert_report_timestamps_are_utc_marked(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Utc-Alert", "10.10.1.13", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now, message="utc-marked")
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/alerts?start={(now - timedelta(hours=1)).isoformat()}&end={(now + timedelta(minutes=1)).isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["period"]["start"].endswith("Z")
    assert payload["period"]["end"].endswith("Z")
    assert payload["alerts"][0]["timestamp"].endswith("Z")


def test_alerts_report_is_site_scoped_for_manager_and_device_filter_cannot_escape_scope(manager_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    beta_department = Department.query.filter_by(name="Beta Department").first()
    now = datetime.utcnow()

    alpha_device = _scoped_device(
        "Alpha-Alert",
        "10.20.1.10",
        site_id=alpha_department.site_id,
        department_id=alpha_department.id,
    )
    beta_device = _scoped_device(
        "Beta-Alert",
        "10.30.1.10",
        site_id=beta_department.site_id,
        department_id=beta_department.id,
    )
    _alert(alpha_device, timestamp=now, message="alpha-visible")
    _alert(beta_device, timestamp=now, message="beta-hidden")
    db.session.commit()

    params = f"?start={(now - timedelta(hours=1)).isoformat()}&end={(now + timedelta(minutes=1)).isoformat()}"
    response = manager_client.get(f"/api/reports/alerts{params}")
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["meta"]["scope_type"] == "site"
    messages = {row["message"] for row in payload["alerts"]}
    assert "alpha-visible" in messages
    assert "beta-hidden" not in messages

    escaped = manager_client.get(f"/api/reports/alerts{params}&device_ids={beta_device.device_id}")
    assert escaped.status_code == 200
    escaped_payload = escaped.get_json()
    assert escaped_payload["alerts"] == []


def test_sync_report_export_supports_pdf(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("PDF-Alert", "10.40.1.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now, message="pdf-export")
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/alerts/export?start={(now - timedelta(hours=1)).isoformat()}&end={(now + timedelta(minutes=1)).isoformat()}&format=pdf"
    )
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")


def test_invalid_report_type_returns_404_on_export(admin_client):
    response = admin_client.get("/api/reports/not-a-real-report/export?format=csv")
    assert response.status_code == 404
    assert "Unknown report type" in response.get_json()["error"]


def test_report_logging_includes_request_id(admin_client, caplog):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("RID-Alert", "10.41.1.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now, message="request-id-log")
    db.session.commit()

    with caplog.at_level(logging.INFO, logger="routes.reports"):
        response = admin_client.get(
            f"/api/reports/alerts?start={(now - timedelta(hours=1)).isoformat()}&end={(now + timedelta(minutes=1)).isoformat()}",
            headers={"X-Request-ID": "req-123"},
        )

    assert response.status_code == 200
    assert any("request_id=req-123" in record.getMessage() for record in caplog.records)


def test_executive_report_falls_back_to_raw_scan_history(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Exec-Raw", "10.41.2.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status="Online",
                scan_timestamp=now - timedelta(hours=3),
                ping_time_ms=5.0,
            ),
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status="Offline",
                scan_timestamp=now - timedelta(hours=2),
                ping_time_ms=None,
            ),
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status="Online",
                scan_timestamp=now - timedelta(hours=1),
                ping_time_ms=7.0,
            ),
        ]
    )
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/executive?start={(now - timedelta(days=1)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["availability_basis"] == "device_scan_history"
    assert payload["uptime_score"] == 66.67
    assert payload["avg_latency"] == 6.0
    assert payload["top_problematic"][0]["ip"] == device.device_ip


def test_device_health_report_falls_back_to_raw_when_rollups_missing(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Health-Raw", "10.41.3.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    db.session.add_all(
        [
            ServerHealthLog(
                device_id=device.device_id,
                cpu_usage=22.0,
                memory_usage=47.0,
                disk_usage=61.0,
                network_in_bps=1000.0,
                network_out_bps=2000.0,
                timestamp=now - timedelta(days=5),
            ),
            ServerHealthLog(
                device_id=device.device_id,
                cpu_usage=41.0,
                memory_usage=59.0,
                disk_usage=72.0,
                network_in_bps=1500.0,
                network_out_bps=2500.0,
                timestamp=now - timedelta(days=2),
            ),
        ]
    )
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/device-health?start={(now - timedelta(days=7)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["granularity"] == "raw"
    assert len(payload["summary"]) == 1
    assert str(device.device_id) in {str(key) for key in payload["time_series"].keys()}


def test_network_report_falls_back_to_raw_scan_history(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Network-Raw", "10.41.4.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    db.session.add_all(
        [
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status="Online",
                scan_timestamp=now - timedelta(hours=6),
                ping_time_ms=3.0,
                packet_loss=0.0,
            ),
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status="Offline",
                scan_timestamp=now - timedelta(hours=3),
                ping_time_ms=None,
                packet_loss=100.0,
            ),
        ]
    )
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/network?start={(now - timedelta(days=1)).isoformat()}&end={now.isoformat()}"
    )
    assert response.status_code == 200
    payload = response.get_json()

    assert payload["uptime_basis"] == "device_scan_history"
    assert payload["uptime_summary"][0]["device_name"] == device.device_name
    assert payload["uptime_summary"][0]["avg_uptime"] == 50.0


def test_report_rate_limit_is_per_report_type_and_cached_requests_do_not_fail(admin_client, app):
    from routes import reports as reports_module

    reports_module._report_cache.clear()
    reports_module._rate_limit_hits.clear()
    app.config["REPORT_RATE_LIMIT_PER_MINUTE"] = 1

    now = datetime.utcnow()
    params = f"?start={(now - timedelta(hours=1)).isoformat()}&end={now.isoformat()}"

    first_exec = admin_client.get(f"/api/reports/executive{params}")
    assert first_exec.status_code == 200

    cached_exec = admin_client.get(f"/api/reports/executive{params}")
    assert cached_exec.status_code == 200

    operational = admin_client.get(f"/api/reports/operational{params}")
    assert operational.status_code == 200


def test_sync_xlsx_export_sanitizes_formula_like_and_control_values(admin_client):
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Token-Alert", "10.41.5.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now, message="=SUM(1,1)\x07 token-risk")
    db.session.commit()

    response = admin_client.get(
        f"/api/reports/alerts/export?start={(now - timedelta(hours=1)).isoformat()}&end={(now + timedelta(minutes=1)).isoformat()}&format=xlsx"
    )
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.data.startswith(b"PK")


def test_async_export_jobs_use_db_backend(admin_client, app):
    app.config["REPORT_EXPORT_JOB_BACKEND"] = "db"
    alpha_department = Department.query.filter_by(name="Alpha Department").first()
    device = _scoped_device("Job-Alert", "10.50.1.10", site_id=alpha_department.site_id, department_id=alpha_department.id)
    now = datetime.utcnow()
    _alert(device, timestamp=now, message="async-job")
    db.session.commit()

    create_response = admin_client.post(
        "/api/reports/alerts/export-jobs",
        json={
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(minutes=1)).isoformat(),
            "format": "csv",
        },
    )
    assert create_response.status_code == 202
    job_id = create_response.get_json()["job_id"]
    assert db.session.get(ReportExportJob, job_id) is not None

    status = None
    for _ in range(40):
        status_response = admin_client.get(f"/api/reports/export-jobs/{job_id}")
        assert status_response.status_code == 200
        status = status_response.get_json()
        if status["status"] == "completed":
            break
        time.sleep(0.1)
    assert status is not None
    assert status["status"] == "completed"

    download_response = admin_client.get(f"/api/reports/export-jobs/{job_id}/download")
    assert download_response.status_code == 200
    assert download_response.data.startswith(b"Section,")


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/reports/maintenance-availability",
        "/api/reports/security-compliance",
        "/api/reports/inventory-assets",
        "/api/reports/tracking-operations",
        "/api/reports/printer-operations",
    ],
)
def test_new_enterprise_report_endpoints_return_meta(admin_client, app, endpoint):
    app.config["REPORT_RATE_LIMIT_PER_MINUTE"] = 100
    response = admin_client.get(endpoint)
    assert response.status_code == 200
    payload = response.get_json()
    assert "meta" in payload
    assert "source_tables" in payload["meta"]
    assert "freshness_state" in payload["meta"]
