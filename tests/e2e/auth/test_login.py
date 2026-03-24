"""P0 — Login / logout / unauthenticated redirect."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"


def test_login_success(page: Page):
    page.goto(BASE_URL + "/login")
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "123")
    page.click("button[type='submit']")
    page.wait_for_url("**/dashboard")
    assert "/dashboard" in page.url


def test_logout(logged_in_page: Page):
    # Logout link is inside a Bootstrap dropdown; navigate directly.
    # Logout redirects to /login?message=... so match with trailing glob.
    logged_in_page.goto(BASE_URL + "/logout")
    logged_in_page.wait_for_url("**/login**")
    assert "/login" in logged_in_page.url


def test_unauthenticated_redirect(page: Page):
    page.goto(BASE_URL + "/dashboard")
    page.wait_for_url("**/login")
    assert "/login" in page.url


def test_wrong_password(page: Page):
    page.goto(BASE_URL + "/login")
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "wrongpassword")
    page.click("button[type='submit']")
    page.wait_for_load_state("domcontentloaded")
    # Must stay on login page and show an error
    assert "/login" in page.url
    expect(page.locator(".alert-danger")).to_be_visible()


def test_login_page_has_fields(page: Page):
    page.goto(BASE_URL + "/login")
    expect(page.locator("input[name='username']")).to_be_visible()
    expect(page.locator("input[name='password']")).to_be_visible()
    expect(page.locator("button[type='submit']")).to_be_visible()
