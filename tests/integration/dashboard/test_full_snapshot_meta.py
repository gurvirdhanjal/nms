import pytest

from models.user import User


pytestmark = pytest.mark.integration


def test_full_snapshot_includes_meta_for_admin_scope(admin_client):
    response = admin_client.get('/api/dashboard/full_snapshot')
    assert response.status_code == 200

    payload = response.get_json()
    assert isinstance(payload, dict)
    assert payload['meta']['role'] == 'admin'
    assert payload['meta']['scope_key'] == 'global'
    assert payload['meta']['scope_label'] == 'Global'
    assert payload['meta']['generated_at_utc']


def test_full_snapshot_includes_meta_for_manager_scope(manager_client):
    manager = User.query.get(2)
    response = manager_client.get('/api/dashboard/full_snapshot')
    assert response.status_code == 200

    payload = response.get_json()
    assert isinstance(payload, dict)
    assert payload['meta']['role'] == 'manager'
    assert payload['meta']['scope_key'] == f"site:{manager.site_id}"
    assert payload['meta']['scope_label'].startswith('Site')
    assert 'Alpha Site' in payload['meta']['scope_label']
    assert payload['meta']['generated_at_utc']
