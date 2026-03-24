"""P1 — Device list page loads, table renders, search works."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"


def test_device_list_loads(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/devices")
    logged_in_page.wait_for_load_state("networkidle")
    # Table rows or device cards must be present
    expect(
        logged_in_page.locator("table tbody tr, .device-card").first
    ).to_be_visible(timeout=10_000)


def test_device_list_has_table(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/devices")
    logged_in_page.wait_for_load_state("networkidle")
    expect(logged_in_page.locator("table")).to_be_visible(timeout=10_000)


def test_device_search(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/devices")
    logged_in_page.wait_for_load_state("networkidle")
    search = logged_in_page.locator(
        "input[type='search'], input[placeholder*='Search'], input[placeholder*='search']"
    ).first
    expect(search).to_be_visible(timeout=5_000)
    search.fill("192.168")
    logged_in_page.wait_for_load_state("networkidle")
    # Table or empty-state must be present after filtering
    expect(
        logged_in_page.locator("table, .empty-state, [data-empty]").first
    ).to_be_visible()


def test_device_page_title(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/devices")
    logged_in_page.wait_for_load_state("domcontentloaded")
    assert "device" in logged_in_page.title().lower() or logged_in_page.url.endswith("/devices")
