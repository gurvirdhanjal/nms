import pytest
import json
import re


pytestmark = pytest.mark.integration


def _extract_rbac_context(payload):
    text = payload.decode('utf-8', errors='ignore')
    match = re.search(r'window\.__RBAC_CONTEXT__\s*=\s*(\{.*?\});', text, re.DOTALL)
    assert match, 'RBAC context script not found in response'
    return json.loads(match.group(1))


def test_sidebar_hides_files_link_for_all_roles(admin_client, manager_client, viewer_client, operator_client):
    for client in (admin_client, manager_client, viewer_client, operator_client):
        response = client.get('/dashboard')
        assert response.status_code == 200
        assert b'link-text">Files<' not in response.data


def test_sidebar_respects_manager_capabilities(manager_client):
    manager_response = manager_client.get('/dashboard')
    assert manager_response.status_code == 200
    manager_context = _extract_rbac_context(manager_response.data)
    assert manager_context['role'] == 'manager'
    assert manager_context['capabilities']['sites'] is True
    assert manager_context['capabilities']['departments'] is True


def test_sidebar_respects_viewer_capabilities(viewer_client):
    viewer_response = viewer_client.get('/dashboard')
    assert viewer_response.status_code == 200
    viewer_context = _extract_rbac_context(viewer_response.data)
    assert viewer_context['role'] == 'viewer'
    assert viewer_context['capabilities']['sites'] is False
    assert viewer_context['capabilities']['departments'] is False
