import time

import pytest

from extensions import db
from models.restricted_site_policy import RestrictedSiteDomainMeta
from models.tracked_device import TrackedDevice


pytestmark = pytest.mark.performance


SLA_MS = {
    'website_policy_get': 350,
    'alerts_get': 350,
    'mixed_sequence': 450,
}



def _timed_request(client, method, url, **kwargs):
    start = time.perf_counter()
    response = client.open(url, method=method, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return response, elapsed_ms


def _assert_sla_compliance(latencies, statuses, scenario_name):
    ok = [lat for lat, status in zip(latencies, statuses) if status < 400]
    failures = len([status for status in statuses if status >= 400])
    assert len(statuses) > 0
    assert (failures / len(statuses)) <= 0.05
    within = len([lat for lat in ok if lat <= SLA_MS[scenario_name]])
    compliance = within / max(1, len(ok))
    assert compliance >= 0.95, f'{scenario_name} compliance={compliance:.2%}'


def _seed_device():
    device = TrackedDevice(mac_address='AA:BB:CC:DD:EE:70', device_name='Perf Device', availability_status='online')
    db.session.add(device)
    db.session.commit()
    return device


def test_perf_get_website_policy_burst(admin_client):
    device = _seed_device()
    for i in range(3):
        db.session.add(RestrictedSiteDomainMeta(device_id=device.id, domain=f'example{i}.com', category='Custom'))
    db.session.commit()

    latencies = []
    statuses = []
    for _ in range(40):
        response, elapsed = _timed_request(admin_client, 'GET', f'/api/devices/{device.id}/website-policy')
        latencies.append(elapsed)
        statuses.append(response.status_code)

    _assert_sla_compliance(latencies, statuses, 'website_policy_get')


def test_perf_get_alerts_burst(admin_client):
    device = _seed_device()

    latencies = []
    statuses = []
    for _ in range(40):
        response, elapsed = _timed_request(admin_client, 'GET', f'/api/devices/{device.id}/alerts')
        latencies.append(elapsed)
        statuses.append(response.status_code)

    _assert_sla_compliance(latencies, statuses, 'alerts_get')


def test_perf_mixed_get_and_mutation_sequence(admin_client):
    device = _seed_device()

    latencies = []
    statuses = []
    for index in range(30):
        response, elapsed = _timed_request(admin_client, 'POST', f'/api/devices/{device.id}/website-policy', json={'domain': f'mixed{index}.com'})
        latencies.append(elapsed)
        statuses.append(response.status_code)

        response, elapsed = _timed_request(admin_client, 'GET', f'/api/devices/{device.id}/website-policy')
        latencies.append(elapsed)
        statuses.append(response.status_code)

        response, elapsed = _timed_request(admin_client, 'DELETE', f'/api/devices/{device.id}/website-policy', json={'domains': [f'mixed{index}.com']})
        latencies.append(elapsed)
        statuses.append(response.status_code)

    _assert_sla_compliance(latencies, statuses, 'mixed_sequence')
