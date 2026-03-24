"""P1/P2 — Role-based access enforcement."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"
ADMIN_USER = "admin"
ADMIN_PASS = "123"


def test_admin_can_access_user_management(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/user_management")
    logged_in_page.wait_for_load_state("networkidle")
    # Admin should see the user management page (table or heading)
    expect(
        logged_in_page.locator("table, h1, h2, .card").first
    ).to_be_visible(timeout=10_000)
    assert "/user_management" in logged_in_page.url


def test_unauthenticated_user_management_redirects(page: Page):
    page.goto(BASE_URL + "/user_management")
    page.wait_for_url("**/login")
    assert "/login" in page.url


def test_unauthenticated_devices_redirects(page: Page):
    page.goto(BASE_URL + "/devices")
    page.wait_for_url("**/login")
    assert "/login" in page.url


def test_unauthenticated_reports_redirects(page: Page):
    page.goto(BASE_URL + "/reports")
    page.wait_for_url("**/login")
    assert "/login" in page.url


def test_admin_session_persists_across_pages(page: Page):
    """Admin login persists when navigating between pages."""
    page.goto(BASE_URL + "/login")
    page.fill("input[name='username']", ADMIN_USER)
    page.fill("input[name='password']", ADMIN_PASS)
    page.click("button[type='submit']")
    page.wait_for_url("**/dashboard")

    # Navigate to devices — should NOT redirect to login
    page.goto(BASE_URL + "/devices")
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url

    # Navigate to reports — should NOT redirect to login
    page.goto(BASE_URL + "/reports")
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url
