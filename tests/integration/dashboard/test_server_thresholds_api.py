import pytest


pytestmark = pytest.mark.integration


def test_server_thresholds_get_and_post_are_versioned(admin_client, viewer_client):
    get_response = viewer_client.get('/api/server/thresholds')
    assert get_response.status_code == 200
    payload = get_response.get_json()
    assert payload['version'] == 1
    assert payload['metrics']['cpu_usage_pct']['enabled'] is True
    assert payload['metrics']['cpu_usage_pct']['default_warning'] == 80.0

    forbidden_response = viewer_client.post(
        '/api/server/thresholds',
        json={
            'version': 1,
            'change_reason': 'viewer should not write',
            'metrics': {
                'cpu_usage_pct': {'enabled': True, 'warning': 81, 'critical': 91},
            },
        },
    )
    assert forbidden_response.status_code == 403

    save_response = admin_client.post(
        '/api/server/thresholds',
        json={
            'version': 1,
            'change_reason': 'Raise cpu thresholds',
            'metrics': {
                'cpu_usage_pct': {'enabled': True, 'warning': 81, 'critical': 91},
            },
        },
    )
    assert save_response.status_code == 200
    saved_payload = save_response.get_json()
    assert saved_payload['version'] == 2
    assert saved_payload['metrics']['cpu_usage_pct']['warning'] == 81.0
    assert saved_payload['metrics']['cpu_usage_pct']['critical'] == 91.0

    stale_response = admin_client.post(
        '/api/server/thresholds',
        json={
            'version': 1,
            'change_reason': 'stale write',
            'metrics': {
                'cpu_usage_pct': {'enabled': True, 'warning': 82, 'critical': 92},
            },
        },
    )
    assert stale_response.status_code == 409
    assert stale_response.get_json()['code'] == 'CONFLICT_VERSION'
