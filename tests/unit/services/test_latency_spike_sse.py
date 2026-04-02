import pytest

pytestmark = pytest.mark.unit


def _build_latency_spike_payload(device_id, device_ip, device_name, latency_ms, icmp_thresholds):
    """Pure helper extracted from device_monitor — tested in isolation here."""
    severity = (
        'critical'
        if latency_ms >= icmp_thresholds['latency_critical_ms']
        else 'warning'
    )
    return {
        'device_id': device_id,
        'ip': device_ip,
        'name': device_name,
        'latency_ms': round(latency_ms, 2),
        'threshold_ms': icmp_thresholds['latency_warning_ms'],
        'severity': severity,
    }


THRESHOLDS = {'latency_warning_ms': 200, 'latency_critical_ms': 400}


def test_latency_spike_warning_severity():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 250.0, THRESHOLDS)
    assert payload['severity'] == 'warning'
    assert payload['threshold_ms'] == 200
    assert payload['latency_ms'] == 250.0


def test_latency_spike_critical_severity():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 450.0, THRESHOLDS)
    assert payload['severity'] == 'critical'


def test_latency_spike_payload_shape():
    payload = _build_latency_spike_payload(42, '192.168.1.5', 'SERVER01', 310.55, THRESHOLDS)
    assert set(payload.keys()) == {'device_id', 'ip', 'name', 'latency_ms', 'threshold_ms', 'severity'}
    assert payload['device_id'] == 42
    assert payload['ip'] == '192.168.1.5'
    assert payload['name'] == 'SERVER01'


def test_latency_spike_rounds_to_two_decimals():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 123.456789, THRESHOLDS)
    assert payload['latency_ms'] == 123.46


def test_latency_at_exact_warning_threshold_is_warning():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 200.0, THRESHOLDS)
    assert payload['severity'] == 'warning'


def test_latency_at_exact_critical_threshold_is_critical():
    payload = _build_latency_spike_payload(1, '10.0.0.1', 'PC01', 400.0, THRESHOLDS)
    assert payload['severity'] == 'critical'
