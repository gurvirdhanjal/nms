import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

THRESHOLD = 80


def _get_payload_builder():
    with patch.dict('sys.modules', {'extensions': MagicMock()}):
        import importlib
        import services.interface_poller as ip
        importlib.reload(ip)
        return ip._build_interface_threshold_payload


def test_rx_only_breach_direction():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 3, 85.0, 40.0, THRESHOLD)
    assert payload['direction'] == 'rx'


def test_tx_only_breach_direction():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 3, 50.0, 90.0, THRESHOLD)
    assert payload['direction'] == 'tx'


def test_both_breach_direction():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 3, 85.0, 95.0, THRESHOLD)
    assert payload['direction'] == 'both'


def test_payload_shape():
    build = _get_payload_builder()
    payload = build(12, 'GigabitEthernet0/1', 3, 87.4, 23.1, THRESHOLD)
    assert set(payload.keys()) == {
        'device_id', 'interface_name', 'if_index',
        'rx_util_pct', 'tx_util_pct', 'threshold_pct', 'direction'
    }
    assert payload['device_id'] == 12
    assert payload['threshold_pct'] == THRESHOLD


def test_exact_threshold_is_breach():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 1, 80.0, 30.0, THRESHOLD)
    assert payload['direction'] == 'rx'


def test_none_rx_util_not_breach():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 1, None, 90.0, THRESHOLD)
    assert payload['direction'] == 'tx'


def test_none_tx_util_not_breach():
    build = _get_payload_builder()
    payload = build(1, 'Gi0/1', 1, 90.0, None, THRESHOLD)
    assert payload['direction'] == 'rx'
