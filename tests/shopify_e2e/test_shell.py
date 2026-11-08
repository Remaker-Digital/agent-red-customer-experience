# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shopify admin shell — Polaris Frame, tenant resolve, App Bridge auth, cross-nav."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    MOCK_SESSION_TOKEN,
    MOCK_SHOP,
    ShopifyApiRecorder,
    setup_shopify_page,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestShopifyShellLoads:
    def test_polaris_frame_and_dashboard_title(self, shopify_page: Page) -> None:
        frame = shopify_page.locator("[class*='Polaris-Frame']")
        expect(frame.first).to_be_attached()
        expect(shopify_page.get_by_role("heading", name="Dashboard").first).to_be_visible()
        body = shopify_page.locator("body").inner_text()
        assert "Application error" not in body
        assert "Page Error" not in body

    def test_app_root_mounted(self, shopify_page: Page) -> None:
        root = shopify_page.locator("#app")
        expect(root).to_be_attached()
        assert root.locator("> *").count() > 0


class TestTenantResolveAndAuth:
    def test_lookup_uses_mock_shop_and_bearer(self, shopify_page: Page) -> None:
        recorder: ShopifyApiRecorder = shopify_page._api_recorder  # type: ignore[attr-defined]
        lookups = recorder.get_calls(method="GET", path_contains="/api/tenants/lookup")
        assert len(lookups) >= 1, "Shell must GET /api/tenants/lookup"
        call = lookups[0]
        shop_vals = call["query"].get("shop") or []
        assert MOCK_SHOP in shop_vals, f"lookup shop query must be {MOCK_SHOP}, got {shop_vals}"
        auth = call["headers"].get("authorization", "")
        assert auth == f"Bearer {MOCK_SESSION_TOKEN}", f"apiFetch must send App Bridge mock token, got {auth!r}"
        shop_header = call["headers"].get("x-shopify-shop", "")
        assert shop_header == MOCK_SHOP, f"X-Shopify-Shop must be {MOCK_SHOP}, got {shop_header!r}"

    def test_tenant_not_found_shows_critical_banner(
        self, page: Page, shopify_vite_server, api_recorder: ShopifyApiRecorder
    ) -> None:
        api_recorder.override(
            "/api/tenants/lookup",
            {
                "found": False,
                "tenant_id": None,
                "tier": None,
                "status": None,
            },
        )
        setup_shopify_page(page, api_recorder, "/", wait_text=None)
        expect(page.get_by_text(f'Store "{MOCK_SHOP}" is not registered')).to_be_visible(timeout=15_000)
        expect(page.get_by_text("open your app from")).to_be_visible()
        expect(page.get_by_role("heading", name="Dashboard")).to_have_count(0)

    def test_lookup_http_error_shows_banner(
        self, page: Page, shopify_vite_server, api_recorder: ShopifyApiRecorder
    ) -> None:
        api_recorder.override("/api/tenants/lookup", {"detail": "upstream unavailable"}, status=503)
        setup_shopify_page(page, api_recorder, "/", wait_text=None)
        expect(page.get_by_text("Tenant lookup failed: 503")).to_be_visible(timeout=15_000)


class TestCrossNavChrome:
    def test_documentation_and_full_admin_links(self, shopify_page: Page) -> None:
        docs = shopify_page.locator("a", has_text="Documentation")
        expect(docs.first).to_be_visible()
        assert "agentredcx.com" in (docs.first.get_attribute("href") or "")
        assert docs.first.get_attribute("target") == "_blank"

        full = shopify_page.locator("a", has_text="Open full admin")
        expect(full.first).to_be_visible()
        href = full.first.get_attribute("href") or ""
        assert "/admin/standalone" in href

    def test_setup_wizard_open_and_dismiss(self, shopify_page: Page) -> None:
        shopify_page.locator("a", has_text="Setup wizard").first.click()
        expect(shopify_page.get_by_text("Set up your AI assistant")).to_be_visible(timeout=10_000)
        shopify_page.get_by_role("button", name="Skip for now").click()
        expect(shopify_page.get_by_text("Set up your AI assistant")).to_have_count(0)
        # Shell remains usable after dismiss
        expect(shopify_page.get_by_role("heading", name="Dashboard").first).to_be_visible()


class TestMockShopInjected:
    def test_url_contains_mock_shop_after_load(self, shopify_page: Page) -> None:
        # Mock plugin rewriteState injects shop when missing.
        assert MOCK_SHOP in shopify_page.url or "shop=" in shopify_page.url
        # Direct assert via JS (history may rewrite without full navigation)
        shop = shopify_page.evaluate("() => new URLSearchParams(window.location.search).get('shop')")
        assert shop == MOCK_SHOP
