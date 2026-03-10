from pathlib import Path

import pytest

import routes.file_transfer as file_transfer_routes


pytestmark = pytest.mark.integration


def test_create_local_folder_and_delete_folder(admin_client, tmp_path, monkeypatch):
    monkeypatch.setattr(file_transfer_routes, 'CLIENT_FOLDER', tmp_path.as_posix())

    create_response = admin_client.post(
        '/api/files/local/create_folder',
        json={'path': tmp_path.as_posix(), 'name': 'nested-folder'},
    )

    assert create_response.status_code == 200
    created_path = Path(create_response.get_json()['path'])
    assert created_path.exists() is True

    delete_response = admin_client.delete('/api/files/local/delete', json={'path': created_path.as_posix()})

    assert delete_response.status_code == 200
    assert created_path.exists() is False


def test_delete_local_file_validates_missing_and_nonexistent_paths(admin_client, tmp_path):
    file_path = tmp_path / 'deleteme.txt'
    file_path.write_text('payload', encoding='utf-8')

    missing_path = admin_client.delete('/api/files/local/delete', json={})
    success = admin_client.delete('/api/files/local/delete', json={'path': file_path.as_posix()})
    nonexistent = admin_client.delete('/api/files/local/delete', json={'path': file_path.as_posix()})

    assert missing_path.status_code == 400
    assert success.status_code == 200
    assert nonexistent.status_code == 404
