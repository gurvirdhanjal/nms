import pytest

from extensions import db
from models.compliance_profile import ComplianceProfile
from models.device import Device

pytestmark = pytest.mark.unit


def test_assigned_compliance_profile_overrides_effective_alert_thresholds(monkeypatch, app):
    from services.alert_manager import AlertManager

    base_thresholds = {
        'metrics': {
            'cpu_usage_pct': {'warning': 80.0, 'critical': 95.0, 'enabled': True},
            'memory_usage_pct': {'warning': 85.0, 'critical': 95.0, 'enabled': True},
            'disk_usage_pct': {'warning': 80.0, 'critical': 95.0, 'enabled': True},
        }
    }

    monkeypatch.setattr(
        'services.alert_manager.get_merged_thresholds',
        lambda: {
            'metrics': {
                key: dict(value)
                for key, value in base_thresholds['metrics'].items()
            }
        },
    )

    profile = ComplianceProfile(
        name='Threshold Override Profile',
        rules_json={
            'cpu_warning': 70,
            'memory_critical': 88,
            'disk_warning': 76,
            'sla_gold': 99.95,
        },
    )
    db.session.add(profile)
    db.session.flush()

    device = Device(
        device_name='Override Device',
        device_type='server',
        device_ip='10.60.0.10',
        compliance_profile_id=profile.id,
    )
    db.session.add(device)
    db.session.commit()

    thresholds = AlertManager._get_thresholds(device)

    assert thresholds['metrics']['cpu_usage_pct']['warning'] == 70.0
    assert thresholds['metrics']['cpu_usage_pct']['critical'] == 95.0
    assert thresholds['metrics']['memory_usage_pct']['warning'] == 85.0
    assert thresholds['metrics']['memory_usage_pct']['critical'] == 88.0
    assert thresholds['metrics']['disk_usage_pct']['warning'] == 76.0
    assert thresholds['metrics']['disk_usage_pct']['critical'] == 95.0
