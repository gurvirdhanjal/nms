import time
from datetime import datetime

import pytest

from extensions import db
from models.dashboard import DashboardEvent
from models.device import Device
from models.scan_history import DeviceScanHistory


pytestmark = pytest.mark.performance


def _timed_get(client, endpoint):
    start = time.perf_counter()
    response = client.get(endpoint)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return response, elapsed_ms


def _compliance(samples_ms, threshold_ms):
    if not samples_ms:
        return 0.0
    within = sum(1 for value in samples_ms if value <= threshold_ms)
    return (within / len(samples_ms)) * 100.0


def _seed_perf_data():
    for idx in range(1, 16):
        device = Device(
            device_name=f'Perf Device {idx}',
            device_type='workstation',
            device_ip=f'10.10.1.{idx}',
            site_id=1,
            department_id=1,
        )
        db.session.add(device)
        db.session.flush()
        db.session.add(
            DeviceScanHistory(
                device_ip=device.device_ip,
                device_name=device.device_name,
                status='Online',
                ping_time_ms=50 + idx,
                packet_loss=0,
                scan_timestamp=datetime.utcnow(),
            )
        )
        db.session.add(
            DashboardEvent(
                event_id=f'perf-alert-{idx}',
                device_id=device.device_id,
                device_ip=device.device_ip,
                event_type='THRESHOLD',
                severity='WARNING',
                message='Perf warning',
                resolved=False,
                timestamp=datetime.utcnow(),
            )
        )
    db.session.commit()


def test_dashboard_scope_perf_sla(manager_client):
    _seed_perf_data()

    scenarios = [
        ('summary_burst', '/api/dashboard/summary', 350, 40),
        ('full_snapshot_burst', '/api/dashboard/full_snapshot?fresh=1', 900, 20),
        ('mixed_sequence', '/api/dashboard/alerts?status=active&limit=100', 450, 40),
    ]

    for scenario_name, endpoint, threshold_ms, iterations in scenarios:
        latencies = []
        failures = 0

        for _ in range(iterations):
            response, elapsed = _timed_get(manager_client, endpoint)
            latencies.append(elapsed)
            if response.status_code >= 400:
                failures += 1

        compliance_pct = _compliance(latencies, threshold_ms)
        error_rate_pct = (failures / iterations) * 100.0

        assert compliance_pct >= 95.0, f'{scenario_name} compliance below SLA: {compliance_pct:.2f}%'
        assert error_rate_pct <= 5.0, f'{scenario_name} error rate above SLA: {error_rate_pct:.2f}%'
