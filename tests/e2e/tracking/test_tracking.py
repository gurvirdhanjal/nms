"""P1 — Tracking page loads, stored device list is visible."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"


def test_tracking_page_loads(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/tracking")
    logged_in_page.wait_for_load_state("networkidle")
    # Page must render — at least one card or table must be visible
    expect(
        logged_in_page.locator(".card, table").first
    ).to_be_visible(timeout=10_000)


def test_tracking_page_has_device_list(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/tracking")
    logged_in_page.wait_for_load_state("networkidle")
    # The stored device list table (or empty-state) must appear
    expect(
        logged_in_page.locator("table, .empty-state, [data-empty]").first
    ).to_be_visible(timeout=10_000)


def test_tracking_requires_auth(page: Page):
    page.goto(BASE_URL + "/tracking")
    page.wait_for_url("**/login")
    assert "/login" in page.url
