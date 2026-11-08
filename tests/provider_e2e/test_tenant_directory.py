# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Tenant Directory: filters, menus, create → API asserts."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def tenants_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/tenants", wait_text="Tenant Directory")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestTenantDirectoryStructure:
    def test_summary_and_rows_render(self, tenants_page: Page) -> None:
        expect(tenants_page.get_by_text("Total Tenants")).to_be_visible()
        expect(tenants_page.get_by_text("3", exact=True).first).to_be_visible()
        expect(tenants_page.get_by_text("Acme Corporation")).to_be_visible()
        expect(tenants_page.get_by_text("admin@acme-corp.com")).to_be_visible()
        expect(tenants_page.get_by_text("3 tenants found")).to_be_visible()

    def test_create_tenant_button_toggles_modal(self, tenants_page: Page) -> None:
        expect(tenants_page.get_by_role("dialog")).to_have_count(0)
        tenants_page.get_by_role("button", name="Create Tenant").click()
        expect(tenants_page.get_by_role("dialog")).to_be_visible()
        expect(tenants_page.get_by_label("Merchant Name")).to_be_visible()
        tenants_page.get_by_role("button", name="Cancel").click()
        expect(tenants_page.get_by_role("dialog")).to_have_count(0)


class TestTenantFilters:
    """Filter selects must change list and emit status/tier query params."""

    def test_status_filter_triggers_filtered_get(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        tenants_page.get_by_role("textbox", name="Status").click()
        tenants_page.get_by_role("option", name="Active").click()
        tenants_page.wait_for_timeout(400)

        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/tenants?")
        assert gets, "Status filter must re-fetch tenant list"
        assert any("status=active" in c["url"] for c in gets), (
            f"Expected status=active in GET urls, got {[c['url'] for c in gets]}"
        )
        # Only active tenants remain (2 of 3)
        expect(tenants_page.get_by_text("2 tenants found")).to_be_visible()
        expect(tenants_page.get_by_text("Acme Corporation")).to_be_visible()
        expect(tenants_page.get_by_text("Trial Shop")).to_have_count(0)

    def test_tier_filter_triggers_filtered_get(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        tenants_page.get_by_role("textbox", name="Tier").click()
        tenants_page.get_by_role("option", name="enterprise").click()
        tenants_page.wait_for_timeout(400)

        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/tenants?")
        assert any("tier=enterprise" in c["url"] for c in gets), (
            f"Expected tier=enterprise, got {[c['url'] for c in gets]}"
        )
        expect(tenants_page.get_by_text("1 tenant found")).to_be_visible()
        expect(tenants_page.get_by_text("Acme Corporation")).to_be_visible()

    def test_billing_channel_filter_triggers_filtered_get(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        tenants_page.get_by_role("textbox", name="Billing channel").click()
        tenants_page.get_by_role("option", name="shopify").click()
        tenants_page.wait_for_timeout(400)

        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/tenants?")
        assert any("billing_channel=shopify" in c["url"] for c in gets), (
            f"Expected billing_channel=shopify, got {[c['url'] for c in gets]}"
        )
        expect(tenants_page.get_by_text("2 tenants found")).to_be_visible()
        expect(tenants_page.get_by_text("Blanco Storefront")).to_be_visible()
        expect(tenants_page.get_by_text("Acme Corporation")).to_have_count(0)


class TestTenantRowMenuApi:
    def test_resend_welcome_email_posts(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        row = tenants_page.locator("tr", has_text="Acme Corporation")
        row.locator("button").last.click()
        tenants_page.get_by_role("menuitem", name="Resend Welcome Email").click()
        tenants_page.wait_for_timeout(400)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/tenants/acme-corp-001/resend-welcome-email",
        )
        assert len(posts) == 1, f"Expected one resend POST, got {posts}"
        expect(tenants_page.get_by_text("Welcome email sent to admin@acme-corp.com")).to_be_visible(timeout=5_000)

    def test_extend_30_days_patches_expiry(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        row = tenants_page.locator("tr", has_text="Acme Corporation")
        row.locator("button").last.click()
        tenants_page.get_by_role("menuitem", name="Extend 30 Days").click()
        tenants_page.wait_for_timeout(400)

        patches = mocker.get_calls(
            method="PATCH",
            path_contains="/tenants/acme-corp-001/expiry",
        )
        assert len(patches) == 1, f"Expected expiry PATCH, got {patches}"
        body = json.loads(patches[0]["body"] or "{}")
        assert body.get("expiresAt"), "Extend 30 Days must send expiresAt ISO string"

    def test_set_rate_limit_patches_payload(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        row = tenants_page.locator("tr", has_text="Acme Corporation")
        row.locator("button").last.click()
        tenants_page.get_by_role("menuitem", name="Set Rate Limit…").click()
        dialog = tenants_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        # GET rate-limit for prefill
        tenants_page.wait_for_timeout(400)
        gets = mocker.get_calls(
            method="GET",
            path_contains="/tenants/acme-corp-001/rate-limit",
        )
        assert gets, "Opening Set Rate Limit must GET current limit"
        mocker.clear_calls()

        dialog.get_by_label("Rate Limit (RPM)").fill("450")
        dialog.get_by_role("button", name="Save").click()
        tenants_page.wait_for_timeout(500)

        patches = mocker.get_calls(
            method="PATCH",
            path_contains="/tenants/acme-corp-001/rate-limit",
        )
        assert len(patches) == 1, f"Expected rate-limit PATCH, got {patches}"
        body = json.loads(patches[0]["body"] or "{}")
        assert body.get("rateLimitRpm") == 450
        expect(tenants_page.get_by_text("Rate limit updated: 450 RPM")).to_be_visible(timeout=5_000)


class TestTenantCreateApi:
    def test_create_tenant_posts_payload(self, tenants_page: Page) -> None:
        mocker: ProviderApiMocker = tenants_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        tenants_page.get_by_role("button", name="Create Tenant").click()
        dialog = tenants_page.get_by_role("dialog")
        dialog.get_by_label("Merchant Name").fill("E2E Merchant")
        dialog.get_by_label("Superadmin Email").fill("owner@e2e-merchant.test")
        # Tier defaults to starter — leave as-is
        dialog.get_by_role("button", name="Create Tenant").click()
        tenants_page.wait_for_timeout(500)

        posts = mocker.get_calls(method="POST", path_contains="/api/superadmin/tenants")
        # Exclude resend paths
        posts = [p for p in posts if p["path"].rstrip("/").endswith("/tenants")]
        assert len(posts) == 1, f"Expected create POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["merchantName"] == "E2E Merchant"
        assert body["superadminEmail"] == "owner@e2e-merchant.test"
        # Success UI: keys-delivered messaging or toast
        expect(tenants_page.get_by_text("Keys delivered")).to_be_visible(timeout=5_000)
        expect(tenants_page.get_by_text("Tenant Created")).to_be_visible()


class TestTenantEmptyAndError:
    def test_empty_state_when_filter_matches_none(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        # Force empty list for any tenants GET
        mocker.override(
            "/api/superadmin/tenants?",
            {"tenants": [], "total": 0, "skip": 0, "limit": 25},
        )
        # Summary still needed for filters
        setup_provider_page(page, mocker, "/tenants")
        page.wait_for_selector("text=Tenant Directory", timeout=15_000)
        expect(page.get_by_text("No tenants found")).to_be_visible()
        expect(page.get_by_text("0 tenants found")).to_be_visible()

    def test_error_toast_on_list_failure(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/tenants?",
            {"detail": "upstream failure"},
            status=500,
        )
        setup_provider_page(page, mocker, "/tenants")
        page.wait_for_selector("text=Tenant Directory", timeout=15_000)
        expect(page.get_by_text("Failed to load tenants")).to_be_visible(timeout=5_000)
