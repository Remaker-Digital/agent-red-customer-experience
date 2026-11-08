# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Maintenance Mode: edit toggle → PUT."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def maintenance_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/maintenance", wait_text="Maintenance Mode")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestMaintenanceView:
    def test_normal_operation_badge(self, maintenance_page: Page) -> None:
        expect(maintenance_page.get_by_text("NORMAL OPERATION")).to_be_visible()
        expect(maintenance_page.get_by_role("button", name="Edit")).to_be_visible()


class TestMaintenanceToggle:
    def test_enable_puts_payload(self, maintenance_page: Page) -> None:
        mocker: ProviderApiMocker = maintenance_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        maintenance_page.get_by_role("button", name="Edit").click()
        expect(maintenance_page.get_by_text("Enable Maintenance Mode")).to_be_visible()
        # Mantine Switch input is visually hidden — toggle via label click
        maintenance_page.get_by_text("Enable Maintenance Mode").click()
        maintenance_page.get_by_label("Maintenance Message").fill("E2E maintenance window")
        maintenance_page.get_by_role("button", name="Enable Maintenance").click()
        maintenance_page.wait_for_timeout(500)

        puts = mocker.get_calls(method="PUT", path_contains="/api/superadmin/maintenance")
        assert len(puts) == 1, f"Expected maintenance PUT, got {puts}"
        body = json.loads(puts[0]["body"] or "{}")
        assert body["enabled"] is True
        assert body["message"] == "E2E maintenance window"
        expect(maintenance_page.get_by_text("Maintenance mode enabled")).to_be_visible(timeout=5_000)
        expect(maintenance_page.get_by_text("MAINTENANCE ACTIVE")).to_be_visible()

    def test_save_failure(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/maintenance",
            {"detail": "cannot enable"},
            status=400,
        )
        # Override applies to GET too — need selective handling.
        # Re-register: first fulfill GET via default by not matching PUT-only...
        # Pattern match is substring — override breaks GET. Use custom approach:
        original = mocker.handle_route

        def selective(route):
            if route.request.method == "PUT" and "/maintenance" in route.request.url:
                mocker.api_calls.append(
                    {
                        "url": route.request.url,
                        "method": "PUT",
                        "path": route.request.url,
                        "body": route.request.post_data,
                    }
                )
                route.fulfill(
                    status=400,
                    content_type="application/json",
                    body='{"detail":"cannot enable"}',
                )
                return
            original(route)

        page.route("**/api/**", selective)
        page.add_init_script(
            """
            try { sessionStorage.setItem('agentred_provider_key', 'ar_spa_plat_e2e_test_key'); }
            catch (e) {}
            """
        )
        page.goto(
            "http://localhost:3400/admin/provider/maintenance",
            wait_until="domcontentloaded",
        )
        page.wait_for_selector("text=Maintenance Mode", timeout=15_000)
        page.get_by_role("button", name="Edit").click()
        page.get_by_text("Enable Maintenance Mode").click()
        page.get_by_role("button", name="Enable Maintenance").click()
        expect(page.get_by_text("cannot enable").or_(page.get_by_text("Save failed"))).to_be_visible(timeout=5_000)
