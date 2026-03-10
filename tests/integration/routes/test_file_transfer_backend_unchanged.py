import pytest


pytestmark = pytest.mark.integration


def test_file_transfer_dashboard_route_and_auth_are_unchanged(admin_client, viewer_client):
    admin_response = admin_client.get('/file_transfer')
    assert admin_response.status_code in {200, 302}

    viewer_response = viewer_client.get('/file_transfer', follow_redirects=False)
    assert viewer_response.status_code in {302, 403}


def test_file_transfer_api_route_still_exists_with_admin_guard(admin_client, viewer_client):
    admin_response = admin_client.post('/api/files/local/list', json={})
    assert admin_response.status_code != 404

    viewer_response = viewer_client.post('/api/files/local/list', json={})
    assert viewer_response.status_code in {302, 403}
