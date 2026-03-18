"""Tests for build_scope_context() guest/admin scope separation.

Verifies that guest/empty role gets scope_type='none' (not 'global'),
while admin gets scope_type='global'.
"""
import pytest

pytestmark = pytest.mark.unit


class TestBuildScopeContextGuest:

    def test_empty_session_gets_none_scope(self, app):
        with app.test_request_context():
            from flask import session
            # Empty session — no role set
            from middleware.rbac import build_scope_context
            ctx = build_scope_context()
            assert ctx['scope_type'] == 'none'
            assert ctx['role'] == 'guest'

    def test_guest_role_gets_none_scope(self, app):
        with app.test_request_context():
            from middleware.rbac import build_scope_context
            ctx = build_scope_context(role='guest')
            assert ctx['scope_type'] == 'none'
            assert ctx['scope_key'] == 'none'

    def test_empty_string_role_gets_none_scope(self, app):
        with app.test_request_context():
            from middleware.rbac import build_scope_context
            ctx = build_scope_context(role='')
            assert ctx['scope_type'] == 'none'

    def test_admin_role_gets_global_scope(self, app):
        with app.test_request_context():
            from flask import session
            session['role'] = 'admin'
            from middleware.rbac import build_scope_context
            ctx = build_scope_context(role='admin')
            assert ctx['scope_type'] == 'global'
            assert ctx['role'] == 'admin'

    def test_viewer_gets_department_scope(self, app):
        with app.test_request_context():
            from flask import session
            session['role'] = 'viewer'
            session['site_id'] = 1
            session['department_id'] = 1
            from middleware.rbac import build_scope_context
            ctx = build_scope_context(role='viewer')
            assert ctx['scope_type'] == 'department'

    def test_guest_scope_is_not_global(self, app):
        """Regression: guest must never get global scope (was a bug)."""
        with app.test_request_context():
            from middleware.rbac import build_scope_context
            ctx = build_scope_context(role='guest')
            assert ctx['scope_type'] != 'global', "Guest must never get global scope"
