import pytest

from middleware.rbac import build_ui_capabilities


pytestmark = pytest.mark.unit


def test_admin_ui_capabilities_include_all_primary_navigation():
    caps = build_ui_capabilities('admin')

    assert caps['dashboard'] is True
    assert caps['devices'] is True
    assert caps['reports'] is True
    assert caps['tracking'] is True
    assert caps['maintenance'] is True
    assert caps['sites'] is True
    assert caps['departments'] is True
    assert caps['subnets'] is True
    assert caps['discovery'] is True
    assert caps['users'] is True


def test_manager_ui_capabilities_are_scoped_and_no_user_admin():
    caps = build_ui_capabilities('manager')

    assert caps['dashboard'] is True
    assert caps['devices'] is True
    assert caps['sites'] is True
    assert caps['departments'] is True
    assert caps['users'] is True
    assert caps['subnets'] is True
    assert caps['discovery'] is True


def test_unknown_role_defaults_to_safe_capabilities():
    caps = build_ui_capabilities('unknown-role')

    assert all(value is False for value in caps.values())
