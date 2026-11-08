# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Entitlement Config: tabs, edit save → PUT."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def entitlements_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/entitlements", wait_text="Entitlement Configuration")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestEntitlementTabs:
    def test_documents_listed(self, entitlements_page: Page) -> None:
        panel = entitlements_page.get_by_role("tabpanel").first
        expect(panel.get_by_text("tier.professional", exact=True)).to_be_visible()
        expect(panel.get_by_text("pricing.starter", exact=True)).to_be_visible()
        expect(entitlements_page.get_by_role("tab", name="Documents (2)")).to_be_visible()

    def test_drift_tab_shows_differences(self, entitlements_page: Page) -> None:
        entitlements_page.get_by_role("tab", name="Drift Check (1)").click()
        expect(entitlements_page.get_by_text("live has extra key: memoryEnabled")).to_be_visible()
        expect(entitlements_page.get_by_text("No drift")).to_be_visible()

    def test_history_tab_shows_entries(self, entitlements_page: Page) -> None:
        entitlements_page.get_by_role("tab", name="History (1)").click()
        expect(entitlements_page.get_by_text("Enable memory for professional")).to_be_visible()
        expect(entitlements_page.get_by_text("v2 → v3")).to_be_visible()


class TestEntitlementSave:
    def test_edit_save_puts_value(self, entitlements_page: Page) -> None:
        mocker: ProviderApiMocker = entitlements_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        row = entitlements_page.locator("tr", has_text="tier.professional")
        row.get_by_role("button", name="Edit").click()
        dialog = entitlements_page.get_by_role("dialog")
        expect(dialog).to_be_visible()

        dialog.get_by_label("Change Reason").fill("e2e entitlement update")
        # Bump maxAgents in JSON
        json_input = dialog.get_by_label("Configuration Value (JSON)")
        json_input.fill('{"maxAgents": 8, "memoryEnabled": true}')
        dialog.get_by_role("button", name="Save").click()
        entitlements_page.wait_for_timeout(500)

        puts = mocker.get_calls(method="PUT", path_contains="/api/superadmin/entitlements/tier.professional")
        assert len(puts) == 1, f"Expected PUT, got {puts}"
        body = json.loads(puts[0]["body"] or "{}")
        assert body["value"]["maxAgents"] == 8
        assert body["changeReason"] == "e2e entitlement update"
        expect(entitlements_page.get_by_text("Updated tier.professional")).to_be_visible(timeout=5_000)

    def test_save_failure_toast(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/entitlements/tier.professional",
            {"detail": "version conflict"},
            status=409,
        )
        setup_provider_page(page, mocker, "/entitlements")
        page.wait_for_selector("text=Entitlement Configuration", timeout=15_000)
        page.locator("tr", has_text="tier.professional").get_by_role("button", name="Edit").click()
        page.get_by_role("dialog").get_by_label("Change Reason").fill("conflict test")
        page.get_by_role("dialog").get_by_role("button", name="Save").click()
        expect(page.get_by_text("version conflict").or_(page.get_by_text("Save failed"))).to_be_visible(timeout=5_000)
