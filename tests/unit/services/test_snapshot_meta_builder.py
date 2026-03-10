from datetime import datetime

import pytest
from flask import session

from models.user import User
from routes.dashboard import _snapshot_meta


pytestmark = pytest.mark.unit


def test_snapshot_meta_contains_role_scope_and_timestamp(app):
    manager = User.query.get(2)
    with app.test_request_context('/api/dashboard/full_snapshot'):
        session['role'] = 'manager'
        session['user_id'] = 2
        session['site_id'] = manager.site_id

        meta = _snapshot_meta()

    assert meta['role'] == 'manager'
    assert meta['scope_key'] == f"site:{manager.site_id}"
    assert meta['scope_label'].startswith('Site')
    assert 'Alpha Site' in meta['scope_label']
    assert 'generated_at_utc' in meta
    assert datetime.fromisoformat(meta['generated_at_utc'].replace('Z', '+00:00'))
