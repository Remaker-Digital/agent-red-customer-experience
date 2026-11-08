# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Support Diagnostics: tenant search lookup → GET assert."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def diagnostics_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/diagnostics", wait_text="Support Diagnostics")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestDiagnosticsLookup:
    def test_run_diagnostics_gets_tenant_snapshot(self, diagnostics_page: Page) -> None:
        mocker: ProviderApiMocker = diagnostics_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        diagnostics_page.get_by_placeholder("Enter tenant ID or email address...").fill("acme-corp-001")
        diagnostics_page.get_by_role("button", name="Run Diagnostics").click()
        diagnostics_page.wait_for_timeout(500)

        gets = mocker.get_calls(
            method="GET",
            path_contains="/api/superadmin/diagnostics/acme-corp-001",
        )
        gets = [g for g in gets if not g["path"].endswith("/errors")]
        assert len(gets) == 1, f"Expected diagnostics GET, got {gets}"
        # Snapshot cards from mock payload
        expect(diagnostics_page.get_by_text("enterprise").first).to_be_visible()
        expect(diagnostics_page.get_by_text("stripe").first).to_be_visible()
        expect(diagnostics_page.get_by_text("active").first).to_be_visible()

    def test_missing_tenant_warning(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        setup_provider_page(page, mocker, "/diagnostics")
        page.wait_for_selector("text=Support Diagnostics", timeout=15_000)
        page.get_by_placeholder("Enter tenant ID or email address...").fill("missing-tenant")
        page.get_by_role("button", name="Run Diagnostics").click()
        expect(page.get_by_text("Tenant not found")).to_be_visible(timeout=5_000)
