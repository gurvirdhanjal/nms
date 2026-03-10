import pytest
from flask import session

from middleware.rbac import build_scope_context
from models.user import User


pytestmark = pytest.mark.unit


def test_admin_scope_context_is_global(app):
    with app.test_request_context('/'):
        session['role'] = 'admin'
        session['user_id'] = 1

        context = build_scope_context()
        assert context['scope_key'] == 'global'
        assert context['scope_label'] == 'Global'


def test_manager_scope_context_uses_site_label(app):
    manager = User.query.get(2)
    with app.test_request_context('/'):
        session['role'] = 'manager'
        session['user_id'] = 2
        session['site_id'] = manager.site_id

        context = build_scope_context()
        assert context['scope_key'] == f"site:{manager.site_id}"
        assert context['scope_label'].startswith('Site')
        assert 'Alpha Site' in context['scope_label']


def test_viewer_scope_context_uses_department_label(app):
    viewer = User.query.get(3)
    with app.test_request_context('/'):
        session['role'] = 'viewer'
        session['user_id'] = 3
        session['site_id'] = viewer.site_id
        session['department_id'] = viewer.department_id

        context = build_scope_context()
        assert context['scope_key'] == f"department:{viewer.department_id}"
        assert context['scope_label'].startswith('Department')
        assert 'Alpha Department' in context['scope_label']
