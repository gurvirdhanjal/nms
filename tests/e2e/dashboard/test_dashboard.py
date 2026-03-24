"""P0/P1 — Dashboard loads, KPI cards and alert table are visible."""
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:5001"


def test_dashboard_loads(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/dashboard")
    logged_in_page.wait_for_load_state("networkidle")
    # At least one card-style element should be present
    expect(logged_in_page.locator(".card").first).to_be_visible()


def test_dashboard_has_device_stats(logged_in_page: Page):
    # Navigate inside the expect_response block so the request is captured
    with logged_in_page.expect_response(
        lambda r: "/api/dashboard/summary" in r.url, timeout=10_000
    ):
        logged_in_page.goto(BASE_URL + "/dashboard")
        logged_in_page.wait_for_load_state("networkidle")
    # KPI stat wrappers or stat-cards must be visible
    expect(
        logged_in_page.locator(".kpi-card, .stat-card, .kpi-stat-wrap, .card").first
    ).to_be_visible()


def test_top_problems_table(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/dashboard")
    logged_in_page.wait_for_load_state("networkidle")
    # Table or empty-state must appear (not a blank page)
    expect(
        logged_in_page.locator("table, .empty-state, [data-empty]").first
    ).to_be_visible(timeout=10_000)


def test_dashboard_nav_link_active(logged_in_page: Page):
    logged_in_page.goto(BASE_URL + "/dashboard")
    logged_in_page.wait_for_load_state("domcontentloaded")
    # Sidebar link for Dashboard should carry the active class
    active_link = logged_in_page.locator(".sidebar-link.active")
    expect(active_link).to_be_visible()
