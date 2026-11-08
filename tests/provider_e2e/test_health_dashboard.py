# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Health Dashboard: loaded state + error empty state."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestHealthDashboardLoaded:
    def test_health_cards_and_version(self, provider_page: Page) -> None:
        main = provider_page.locator("main")
        expect(main.get_by_text("Platform Dashboard")).to_be_visible()
        expect(main.get_by_text("API Version")).to_be_visible()
        expect(main.get_by_text("v1.98.65-e2e", exact=True)).to_be_visible()
        expect(main.get_by_text("Cosmos DB")).to_be_visible()
        expect(main.get_by_text("Redis")).to_be_visible()
        expect(main.get_by_text("Key Vault")).to_be_visible()
        expect(main.get_by_text("Connected").first).to_be_visible()

    def test_dashboard_get_recorded(self, provider_page: Page) -> None:
        mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/dashboard")
        assert len(gets) >= 1, "Dashboard mount must GET /api/superadmin/dashboard"


class TestHealthDashboardError:
    def test_unable_to_load_empty_state(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/dashboard",
            {"detail": "unavailable"},
            status=503,
        )
        setup_provider_page(page, mocker, "/")
        expect(page.get_by_text("Unable to load dashboard")).to_be_visible(timeout=15_000)
        expect(page.get_by_text("Check your connection and try refreshing the page.")).to_be_visible()
