"""P1/P2 — Reports page loads, query executes, export polling works."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"


def test_reports_page_loads(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/reports")
    logged_in_page.wait_for_load_state("networkidle")
    expect(logged_in_page.locator(".card").first).to_be_visible(timeout=10_000)


def test_reports_page_has_selectors(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/reports")
    logged_in_page.wait_for_load_state("networkidle")
    # At least one report range/type selector must be present
    expect(
        logged_in_page.locator("select, .report-range-select").first
    ).to_be_visible(timeout=10_000)


def test_reports_page_has_dense_shell(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/reports")
    logged_in_page.wait_for_load_state("networkidle")
    expect(logged_in_page.locator('[data-report-summary-strip="dense"]')).to_be_visible(timeout=10_000)
    expect(logged_in_page.locator('[data-report-tab-shell="dense"]')).to_be_visible(timeout=10_000)


def test_reports_executive_section_visible(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/reports")
    logged_in_page.wait_for_load_state("networkidle")
    # The executive report section or first report card must exist
    expect(
        logged_in_page.locator(".card, .reports-enterprise").first
    ).to_be_visible(timeout=10_000)


def test_reports_api_responds(logged_in_page: Page):
    """Verify the executive report API endpoint returns a non-5xx response."""
    with logged_in_page.expect_response(
        lambda r: "/api/reports/executive" in r.url,
        timeout=15_000,
    ) as resp_info:
        logged_in_page.goto(BASE_URL + "/reports")
        logged_in_page.wait_for_load_state("networkidle")
    response = resp_info.value
    assert response.status < 500, f"Executive report API returned {response.status}"


def test_reports_requires_auth(page: Page):
    page.goto(BASE_URL + "/reports")
    page.wait_for_url("**/login")
    assert "/login" in page.url
