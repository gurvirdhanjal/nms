import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

THRESHOLDS = {'latency_warning_ms': 200, 'latency_critical_ms': 400}


def _get_payload_builder():
    """Import _build_latency_spike_payload while patching Flask/DB module-level imports."""
    with patch.dict('sys.modules', {
        'extensions': MagicMock(),
        'metrics.collector': MagicMock(),
        'thresholds.evaluator': MagicMock(),
        'thresholds.rules': MagicMock(),
    }):
        import importlib
        import services.device_monitor as dm
        importlib.reload(dm)
        return dm._build_latency_spike_payload


def test_latency_spike_warning_severity():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 250.0, THRESHOLDS)
    assert payload['severity'] == 'warning'
    assert payload['threshold_ms'] == 200
    assert payload['latency_ms'] == 250.0


def test_latency_spike_critical_severity():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 450.0, THRESHOLDS)
    assert payload['severity'] == 'critical'


def test_latency_spike_payload_shape():
    build = _get_payload_builder()
    payload = build(42, '192.168.1.5', 'SERVER01', 310.55, THRESHOLDS)
    assert set(payload.keys()) == {'device_id', 'ip', 'name', 'latency_ms', 'threshold_ms', 'severity'}
    assert payload['device_id'] == 42
    assert payload['ip'] == '192.168.1.5'
    assert payload['name'] == 'SERVER01'


def test_latency_spike_rounds_to_two_decimals():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 123.456789, THRESHOLDS)
    assert payload['latency_ms'] == 123.46


def test_latency_at_exact_warning_threshold_is_warning():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 200.0, THRESHOLDS)
    assert payload['severity'] == 'warning'
    assert payload['threshold_ms'] == 200


def test_latency_at_exact_critical_threshold_is_critical():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 400.0, THRESHOLDS)
    assert payload['severity'] == 'critical'
    assert payload['threshold_ms'] == 400


def test_critical_event_threshold_ms_reflects_critical_value():
    """threshold_ms must reflect the threshold that was actually breached, not always warning."""
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 450.0, THRESHOLDS)
    assert payload['severity'] == 'critical'
    assert payload['threshold_ms'] == 400  # critical threshold, not warning (200)


def test_warning_event_threshold_ms_reflects_warning_value():
    build = _get_payload_builder()
    payload = build(1, '10.0.0.1', 'PC01', 250.0, THRESHOLDS)
    assert payload['severity'] == 'warning'
    assert payload['threshold_ms'] == 200
