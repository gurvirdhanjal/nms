"""
E2E test configuration for pytest-playwright.

IMPORTANT: The parent tests/conftest.py defines autouse=True fixtures
(_app_context, _reset_db) that spin up a SQLite test DB.  E2E tests
hit a live running server, so those fixtures are shadowed here as
no-ops to prevent them from running inside the tests/e2e/ directory.
"""
import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:5001"
ADMIN_USER = "admin"
ADMIN_PASS = "123"


# ── Shadow parent autouse fixtures (no-ops for E2E tests) ──────────────────────
# pytest resolves fixtures by name using the closest conftest in the directory
# hierarchy.  These no-ops shadow _app_context(app) and _reset_db(app, …) from
# tests/conftest.py so the session-scoped `app` fixture (which spins up a SQLite
# test DB) is never requested and never runs for tests in this directory.
#
# ⚠ If tests/conftest.py ever renames these fixtures, update the names below.

@pytest.fixture(autouse=True)
def _app_context():  # noqa: F811 — shadows tests/conftest.py::_app_context(app)
    yield


@pytest.fixture(autouse=True)
def _reset_db():  # noqa: F811 — shadows tests/conftest.py::_reset_db(app, …)
    yield


# ── E2E helpers ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture
def logged_in_page(page: Page):
    """Returns a Page already authenticated as admin."""
    page.goto(f"{BASE_URL}/login")
    page.fill("input[name='username']", ADMIN_USER)
    page.fill("input[name='password']", ADMIN_PASS)
    page.click("button[type='submit']")
    page.wait_for_url("**/dashboard")
    return page
