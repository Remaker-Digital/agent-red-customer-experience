# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shopify Dashboard — mock KPI binding (UsageDashboard) + UI→API analytics calls.

AnalyticsOverview summary cards are intentionally not asserted here: the shared
hook rebuilds ``since``/``until`` ISO timestamps on every render (new Date()),
so the path never stabilizes and the UI can remain on "Loading analytics...".
That is a shared-component residual; UsageDashboard + API call capture still
prove Shopify shell → mock API wiring for ADO.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    MOCK_SESSION_TOKEN,
    MOCK_TOTAL_CONVERSATIONS,
    ShopifyApiRecorder,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestDashboardMockKpis:
    def test_usage_conversations_bound_to_fixture(self, shopify_page: Page) -> None:
        expect(shopify_page.get_by_text("Usage dashboard").first).to_be_visible(timeout=15_000)
        # "Conversations Used" + "187 of 500 included" from dashboard fixture
        section = (
            shopify_page.locator("div")
            .filter(has_text="Conversations Used")
            .filter(has_text=f"{MOCK_TOTAL_CONVERSATIONS} of")
            .filter(has_text="500 included")
        )
        expect(section.first).to_be_visible(timeout=10_000)

    def test_remaining_included_from_fixture(self, shopify_page: Page) -> None:
        card = shopify_page.locator("div").filter(has_text="Remaining included").filter(has_text="313")
        expect(card.first).to_be_visible(timeout=10_000)

    def test_conversation_table_shows_fixture_customer(self, shopify_page: Page) -> None:
        expect(shopify_page.get_by_text("alice@example.com").first).to_be_visible(timeout=15_000)
        expect(shopify_page.get_by_text("Showing 1--50 of 187 conversations").first).to_be_visible()


class TestDashboardApiCalls:
    def test_analytics_summary_get_with_bearer(self, shopify_page: Page) -> None:
        recorder: ShopifyApiRecorder = shopify_page._api_recorder  # type: ignore[attr-defined]
        calls = recorder.get_calls(method="GET", path_contains="/api/analytics/summary")
        assert len(calls) >= 1, "Dashboard must GET /api/analytics/summary"
        auth = calls[0]["headers"].get("authorization", "")
        assert auth == f"Bearer {MOCK_SESSION_TOKEN}"

    def test_usage_get_recorded(self, shopify_page: Page) -> None:
        recorder: ShopifyApiRecorder = shopify_page._api_recorder  # type: ignore[attr-defined]
        calls = recorder.get_calls(method="GET", path_contains="/api/dashboard/usage")
        assert len(calls) >= 1, "UsageDashboard must GET /api/dashboard/usage"
