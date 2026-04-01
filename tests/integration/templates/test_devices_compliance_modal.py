import pytest

from extensions import db
from models.compliance_profile import ComplianceProfile

pytestmark = pytest.mark.integration


def test_devices_page_renders_compliance_profile_controls(admin_client):
    profile = ComplianceProfile(
        name='Strict Ops',
        rules_json={
            'cpu_warning': 73,
            'memory_warning': 82,
            'disk_critical': 94,
        },
    )
    db.session.add(profile)
    db.session.commit()

    response = admin_client.get('/devices')
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert 'name="compliance_profile_id"' in html
    assert 'id="complianceThresholdPreview"' in html
    assert 'Strict Ops' in html
    assert '/admin/compliance-profiles' in html
    assert 'maintenance mode takes precedence over any compliance profile' in html.lower()
