# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — navigation visibility, route transitions, theme toggle, logout."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import NAV_ITEMS, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestProviderNavVisibility:
    """Sidebar must expose every NAV_GROUPS item (conditional menus / nav)."""

    def test_all_nav_labels_visible(self, provider_page: Page) -> None:
        for label, _path in NAV_ITEMS:
            link = provider_page.locator("nav").get_by_text(label, exact=True)
            expect(link.first).to_be_visible()

    def test_nav_group_headers_visible(self, provider_page: Page) -> None:
        for group in (
            "Overview",
            "Operations",
            "Control Plane",
            "Compliance & Security",
            "Account",
        ):
            expect(provider_page.locator("nav").get_by_text(group, exact=True)).to_be_visible()


class TestProviderNavRouting:
    """Clicking nav items transitions route and main content (not presence-only)."""

    @pytest.mark.parametrize(
        "label,path,expect_text",
        [
            ("Tenants", "/tenants", "Tenant Directory"),
            ("Deployments", "/deployments", "Deployment Management"),
            ("Pipeline Observatory", "/pipeline", "Pipeline Observatory"),
            ("Entitlements", "/entitlements", "Entitlement Configuration"),
            ("Maintenance", "/maintenance", "Maintenance Mode"),
            ("Dashboard", "/", "Platform Dashboard"),
        ],
    )
    def test_nav_click_changes_route_and_heading(
        self, provider_page: Page, label: str, path: str, expect_text: str
    ) -> None:
        # Leave dashboard first so returning to Dashboard is a real transition
        if path == "/":
            provider_page.locator("nav").get_by_text("Tenants", exact=True).first.click()
            provider_page.wait_for_selector("text=Tenant Directory", timeout=10_000)
        provider_page.locator("nav").get_by_text(label, exact=True).first.click()
        expect(provider_page.get_by_text(expect_text).first).to_be_visible(timeout=10_000)
        if path == "/":
            assert provider_page.url.rstrip("/").endswith("/admin/provider")
        else:
            assert path in provider_page.url
        body = provider_page.locator("body").inner_text()
        assert "Application error" not in body


class TestProviderChromeInteractions:
    """Header toggles and logout are real state transitions."""

    def test_dark_mode_toggle_flips_color_scheme(self, provider_page: Page) -> None:
        root = provider_page.locator("html")
        before = root.get_attribute("data-mantine-color-scheme")
        provider_page.get_by_label("Toggle dark mode").click()
        provider_page.wait_for_timeout(200)
        after = root.get_attribute("data-mantine-color-scheme")
        assert after in ("light", "dark")
        assert after != before, f"Theme toggle must flip scheme (was {before})"

    def test_logout_returns_to_login(self, provider_page: Page) -> None:
        provider_page.get_by_label("Sign out").click()
        expect(provider_page.locator("nav").get_by_text("Tenants", exact=True)).to_have_count(0)
        expect(provider_page.get_by_label("Service Provider API key")).to_be_visible(timeout=10_000)
        expect(provider_page.get_by_label("Sign in")).to_be_visible()
        key = provider_page.evaluate("() => sessionStorage.getItem('agentred_provider_key')")
        assert key is None, "Logout must clear agentred_provider_key"


class TestProviderDirectRoutes:
    """Deep-link routes load without Application error (nav smoke complement)."""

    @pytest.mark.parametrize("label,path", NAV_ITEMS)
    def test_direct_route_loads(self, provider_page: Page, label: str, path: str) -> None:
        navigate_provider(provider_page, path)
        expect(provider_page.locator("body")).not_to_contain_text("Application error")
        assert "/admin/provider" in provider_page.url
        if path != "/":
            assert path in provider_page.url
        # ScrollArea may clip labels; require presence in the nav DOM, not viewport
        link = provider_page.locator("nav").get_by_text(label, exact=True).first
        expect(link).to_be_attached()
        link.scroll_into_view_if_needed()
        expect(link).to_be_visible()
