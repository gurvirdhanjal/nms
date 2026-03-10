import pytest


pytestmark = pytest.mark.integration


def test_dashboard_scope_summary_for_admin(admin_client):
    response = admin_client.get('/dashboard')
    assert response.status_code == 200
    assert b'Scope: Global' in response.data


def test_dashboard_scope_summary_for_manager(manager_client):
    response = manager_client.get('/dashboard')
    assert response.status_code == 200
    assert b'Scope: Site' in response.data
    assert b'Alpha Site' in response.data


def test_dashboard_scope_summary_for_viewer(viewer_client):
    response = viewer_client.get('/dashboard')
    assert response.status_code == 200
    assert b'Scope: Department' in response.data
    assert b'Alpha Department' in response.data
