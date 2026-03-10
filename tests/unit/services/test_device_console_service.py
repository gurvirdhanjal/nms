import pytest

from services.device_console_service import (
    build_policy_payload,
    calculate_risk_score,
    derive_device_state,
    normalize_domain_input,
    normalize_policy_category,
    normalize_policy_reason,
    normalize_violation_status,
    normalize_violation_severity,
    risk_level_from_score,
)


pytestmark = pytest.mark.unit


def test_normalize_domain_input_accepts_hostnames():
    assert normalize_domain_input('https://www.Example.com/path') == 'example.com'


def test_normalize_domain_input_rejects_invalid_values():
    assert normalize_domain_input('localhost') is None
    assert normalize_domain_input('') is None


def test_policy_payload_builder_shapes_expected_contract():
    payload = build_policy_payload(
        mode='active',
        domains=[{'domain': 'youtube.com', 'category': 'Productivity'}],
        violations_today=2,
        recent_violations=[{'domain': 'youtube.com', 'time': '2026-03-05T10:32:00'}],
    )

    assert payload['mode'] == 'active'
    assert payload['restricted_sites'] == ['youtube.com']
    assert payload['violations_today'] == 2
    assert payload['recent_violations'][0]['domain'] == 'youtube.com'


@pytest.mark.parametrize(
    'category,expected',
    [
        ('Productivity', 'Productivity'),
        ('security', 'Security'),
        ('unexpected', 'Custom'),
    ],
)
def test_normalize_policy_category(category, expected):
    assert normalize_policy_category(category) == expected


def test_normalize_policy_reason_trims_and_caps():
    value = 'a' * 700
    result = normalize_policy_reason(value)
    assert len(result) == 500


@pytest.mark.parametrize(
    'severity,expected',
    [
        ('HIGH', 'High'),
        ('critical', 'High'),
        ('WARNING', 'Medium'),
        ('low', 'Low'),
    ],
)
def test_normalize_violation_severity(severity, expected):
    assert normalize_violation_severity(severity) == expected


@pytest.mark.parametrize(
    'status,expected',
    [('ack', 'acknowledged'), ('resolved', 'resolved'), ('other', 'active')],
)
def test_normalize_violation_status(status, expected):
    assert normalize_violation_status(status) == expected


def test_risk_score_calculation_uses_policy_and_telemetry_inputs():
    score = calculate_risk_score(
        alerts=[{'severity': 'high'}, {'severity': 'medium'}],
        suspicious_processes=2,
        telemetry_state='critical',
        policy_violations=3,
    )
    assert score == 100
    assert risk_level_from_score(score) == 'high'


@pytest.mark.parametrize(
    'score,expected',
    [(0, 'low'), (34, 'low'), (35, 'medium'), (69, 'medium'), (70, 'high')],
)
def test_risk_level_from_score_thresholds(score, expected):
    assert risk_level_from_score(score) == expected


def test_derive_device_state_includes_normalized_contract():
    state = derive_device_state(
        connectivity='online',
        telemetry='degraded',
        policy_violations=1,
        suspicious_processes=2,
        alerts=[{'severity': 'medium'}],
    )

    assert state['connectivity'] == 'online'
    assert state['telemetry'] == 'degraded'
    assert state['policy'] == 'violations'
    assert state['risk'] in {'low', 'medium', 'high'}
    assert isinstance(state['risk_score'], int)
