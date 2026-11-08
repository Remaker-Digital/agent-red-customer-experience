# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shopify admin — direct route loads (App Bridge NavMenu is not in the DOM)."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import SHOPIFY_ROUTES, navigate_shopify

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestShopifyDirectRoutes:
    """Deep-link each registered route; assert Polaris page title + no crash."""

    @pytest.mark.parametrize("label,path,title", SHOPIFY_ROUTES)
    def test_route_loads_with_title(self, shopify_page: Page, label: str, path: str, title: str) -> None:
        navigate_shopify(shopify_page, path, wait_text=title)
        expect(shopify_page.get_by_role("heading", name=title).first).to_be_visible(timeout=15_000)
        if path != "/":
            assert path in shopify_page.url
        else:
            assert shopify_page.url.rstrip("/").endswith("/admin/shopify") or ("/admin/shopify/" in shopify_page.url)
        body = shopify_page.locator("body").inner_text()
        assert "Application error" not in body
        assert "Page Error" not in body
        # Polaris Page chrome present
        assert shopify_page.locator("[class*='Polaris-Page']").count() > 0, f"{label} must render Polaris Page"


class TestShopifyRouteTransitions:
    """Sequential navigations keep the shell alive (state stays resolved)."""

    def test_dashboard_to_inbox_to_settings(self, shopify_page: Page) -> None:
        navigate_shopify(shopify_page, "/inbox", wait_text="Conversation Inbox")
        expect(shopify_page.get_by_role("heading", name="Conversation Inbox").first).to_be_visible()

        navigate_shopify(shopify_page, "/settings", wait_text="Settings")
        expect(shopify_page.get_by_role("heading", name="Settings").first).to_be_visible()
        expect(shopify_page.get_by_text("Data & Privacy")).to_be_visible()

        navigate_shopify(shopify_page, "/", wait_text="Dashboard")
        expect(shopify_page.get_by_role("heading", name="Dashboard").first).to_be_visible()
        # Cross-nav still present after transitions (tenant context retained)
        expect(shopify_page.locator("a", has_text="Open full admin").first).to_be_visible()
