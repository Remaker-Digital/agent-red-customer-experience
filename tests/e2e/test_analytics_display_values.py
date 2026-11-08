"""
E2E display-value tests — Analytics page.

Validates every visible data binding on the Analytics page against
deterministic mock data from conftest.py.

Stat cards (from MOCK_ANALYTICS_SUMMARY):
  - Total conversations: 142 (detail: "Billable: 128")
  - Avg response time: 2.3s
  - Resolution rate: 90.1% (detail: "128 resolved")
  - Customer satisfaction: 4.2/5
  - Escalation rate: 12.7% (detail: "18 escalated")

Topic breakdown table (from MOCK_INTENT_BREAKDOWN):
  - 5 intents with agent name, invocation count, and percentage

Uses the ``admin_page`` fixture and opens ``/analytics`` directly (route exists;
Analytics is not in the current sidebar nav).

Run with:
    pytest tests/e2e/test_analytics_display_values.py -v --headed
    pytest tests/e2e/test_analytics_display_values.py -v

(c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    ADMIN_VITE_PORT,
    MOCK_ANALYTICS_SUMMARY,
    MOCK_INTENT_BREAKDOWN,
    MOCK_TENANT_CONTEXT,
)
from .display_helpers import (
    agent_display_label,
    expect_stat_card,
    expected_stat_displays,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _go_to_analytics(page: Page) -> Page:
    """Open the Analytics route while preserving SPEC-1644 ?tenant=."""
    page.goto(
        f"http://localhost:{ADMIN_VITE_PORT}/admin/standalone/analytics"
        f"?tenant={MOCK_TENANT_CONTEXT['tenant_id']}",
        wait_until="networkidle",
    )
    page.wait_for_selector("text=Analytics", timeout=15_000)
    return page


def _wait_for_analytics_data(page: Page) -> None:
    """Wait for stat cards to populate from API."""
    page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Fixture: analytics page
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin_analytics_page(admin_page: Page) -> Page:
    """Navigate to the Analytics page and wait for data."""
    _go_to_analytics(admin_page)
    _wait_for_analytics_data(admin_page)
    return admin_page


# ===========================================================================
# TestAnalyticsStatCardValues — 5 stat cards with labels, values, details
# ===========================================================================


class TestAnalyticsStatCardValues:
    """Verify each Analytics stat card binds value(+detail) to MOCK_ANALYTICS_SUMMARY."""

    def test_stat_cards_match_mock_summary(self, admin_analytics_page: Page) -> None:
        """Five KPI cards show mock-derived values on the same Paper as their labels."""
        displays = expected_stat_displays(MOCK_ANALYTICS_SUMMARY)
        expect_stat_card(
            admin_analytics_page,
            label="Total conversations",
            value=displays["total_conversations"],
            detail=f"Billable: {MOCK_ANALYTICS_SUMMARY['billableConversations']}",
        )
        expect_stat_card(
            admin_analytics_page,
            label="Avg response time",
            value=displays["avg_response_time"],
        )
        expect_stat_card(
            admin_analytics_page,
            label="Resolution rate",
            value=displays["resolution_rate"],
            detail=displays["resolved_detail"],
        )
        expect_stat_card(
            admin_analytics_page,
            label="Customer satisfaction",
            value=displays["customer_satisfaction"],
        )
        expect_stat_card(
            admin_analytics_page,
            label="Escalation rate",
            value=displays["escalation_rate"],
            detail=displays["escalated_detail"],
        )


# ===========================================================================
# TestAnalyticsTopicValues — Topic breakdown table with 5 intents
# ===========================================================================


class TestAnalyticsTopicValues:
    """Verify the topic breakdown table displays all intent data."""

    def test_topic_breakdown_heading(self, admin_analytics_page: Page) -> None:
        """Topic breakdown section heading is visible."""
        heading = admin_analytics_page.get_by_text("Topic breakdown")
        expect(heading.first).to_be_visible()

    def test_table_header_topic(self, admin_analytics_page: Page) -> None:
        """Table has 'Topic' column header."""
        header = admin_analytics_page.locator("th").filter(has_text="Topic")
        expect(header.first).to_be_visible()

    def test_table_header_count(self, admin_analytics_page: Page) -> None:
        """Table has 'Count' column header."""
        header = admin_analytics_page.locator("th").filter(has_text="Count")
        expect(header.first).to_be_visible()

    def test_table_header_distribution(self, admin_analytics_page: Page) -> None:
        """Table has 'Distribution' column header."""
        header = admin_analytics_page.locator("th").filter(has_text="Distribution")
        expect(header.first).to_be_visible()

    def test_topic_rows_match_intent_mock(self, admin_analytics_page: Page) -> None:
        """Each topic table row pairs label with mock count and percentage."""
        for intent in MOCK_INTENT_BREAKDOWN["intents"]:
            label = agent_display_label(intent["agent"])
            row = admin_analytics_page.locator("main table tbody tr").filter(
                has_text=label
            ).first
            expect(row).to_be_visible()
            expect(row.get_by_text(label, exact=True).first).to_be_visible()
            expect(
                row.get_by_text(str(intent["invocationCount"]), exact=True).first
            ).to_be_visible()
            pct = float(intent["percentage"])
            pct_text = f"{int(pct)}%" if pct == int(pct) else f"{pct}%"
            expect(row.get_by_text(pct_text, exact=True).first).to_be_visible()

    def test_all_five_topic_rows(self, admin_analytics_page: Page) -> None:
        """Topic breakdown table has one row per MOCK_INTENT_BREAKDOWN intent."""
        expected = len(MOCK_INTENT_BREAKDOWN["intents"])
        for intent in MOCK_INTENT_BREAKDOWN["intents"]:
            label = agent_display_label(intent["agent"])
            row = admin_analytics_page.locator("main table tbody tr").filter(
                has_text=label
            )
            assert row.count() >= 1, f"Missing topic row for {label}"
        rows = admin_analytics_page.locator("main table tbody tr")
        assert rows.count() >= expected, (
            f"Expected at least {expected} topic rows, found {rows.count()}"
        )
