"""Display value verification tests for the Dashboard page.

Each KPI assertion is card- or row-scoped and derived from MOCK_* payloads
(GOV-18). Bare page-wide ``get_by_text("142")`` checks are intentionally
avoided so decorative numbers cannot rubber-stamp a pass.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import copy

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    MOCK_ANALYTICS_SUMMARY,
    MOCK_CONFIG,
    MOCK_INBOX_CONVERSATIONS,
    MOCK_INTENT_BREAKDOWN,
    MOCK_KNOWLEDGE_GAPS_DATA,
    AdminApiMocker,
    setup_admin_page,
)
from .display_helpers import (
    agent_display_label,
    expect_conversation_card,
    expect_stat_card,
    expect_topic_row,
    expected_stat_displays,
)

pytestmark = pytest.mark.e2e


def _wait_for_data(page: Page) -> None:
    """Allow dashboard data hooks to populate the DOM."""
    page.wait_for_timeout(500)


def _assert_summary_cards(page: Page, summary: dict) -> None:
    displays = expected_stat_displays(summary)
    expect_stat_card(
        page,
        label="Total conversations",
        value=displays["total_conversations"],
    )
    expect_stat_card(
        page,
        label="Avg response time",
        value=displays["avg_response_time"],
    )
    expect_stat_card(
        page,
        label="Resolution rate",
        value=displays["resolution_rate"],
        detail=displays["resolved_detail"],
    )
    expect_stat_card(
        page,
        label="Customer satisfaction",
        value=displays["customer_satisfaction"],
    )
    expect_stat_card(
        page,
        label="Escalation rate",
        value=displays["escalation_rate"],
        detail=displays["escalated_detail"],
    )


# ===========================================================================
# TestDashboardStatCardValues — values bound to MOCK_ANALYTICS_SUMMARY
# ===========================================================================

class TestDashboardStatCardValues:
    """Every stat card value must match the MOCK_ANALYTICS_SUMMARY payload."""

    def test_stat_cards_match_mock_summary(self, admin_page: Page):
        """All five KPI cards show label+value(+detail) from the mock summary."""
        _wait_for_data(admin_page)
        _assert_summary_cards(admin_page, MOCK_ANALYTICS_SUMMARY)

    def test_stat_cards_track_overridden_summary(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ):
        """Overriding /api/analytics/summary changes every KPI card (binding proof)."""
        alt = copy.deepcopy(MOCK_ANALYTICS_SUMMARY)
        alt.update(
            {
                "totalConversations": 777,
                "avgResponseTime": 5.5,
                "resolutionRate": 0.5,
                "customerSatisfaction": 3.1,
                "escalationRate": 0.25,
                "escalationCount": 99,
            }
        )
        api_mocker.override("/api/analytics/summary", alt)
        setup_admin_page(page, api_mocker)
        _wait_for_data(page)
        _assert_summary_cards(page, alt)
        # Prove the default decorative fixture numbers are gone.
        expect(page.get_by_text("142", exact=True)).to_have_count(0)
        expect(page.get_by_text("2.3s", exact=True)).to_have_count(0)


# ===========================================================================
# TestDashboardRecentConversationValues — conversation list from inbox mock
# ===========================================================================

class TestDashboardRecentConversationValues:
    """Each conversation card must show correct customer, status, and count."""

    def test_recent_conversations_match_inbox_mock(self, admin_page: Page):
        """Customer name is paired with messageCount/status from MOCK_INBOX."""
        _wait_for_data(admin_page)
        for conv in MOCK_INBOX_CONVERSATIONS["conversations"][:3]:
            expect_conversation_card(
                admin_page,
                customer_name=conv["customerName"],
                message_count=conv["messageCount"],
                status=conv["status"],
            )

    def test_escalated_conversation_shows_escalated_label(self, admin_page: Page):
        """conv-002 (escalated) shows 'Escalated' assignment text on its card."""
        _wait_for_data(admin_page)
        escalated = next(
            c
            for c in MOCK_INBOX_CONVERSATIONS["conversations"]
            if c["status"] == "escalated"
        )
        card = admin_page.locator("main .mantine-Paper-root").filter(
            has_text=escalated["customerName"]
        ).first
        expect(card.get_by_text("Escalated", exact=True).first).to_be_visible()

    def test_active_unassigned_shows_unassigned(self, admin_page: Page):
        """Active conversation with assignedTo=None shows 'Unassigned' on its card."""
        _wait_for_data(admin_page)
        active = next(
            c
            for c in MOCK_INBOX_CONVERSATIONS["conversations"]
            if c["status"] == "active" and c.get("assignedTo") is None
        )
        card = admin_page.locator("main .mantine-Paper-root").filter(
            has_text=active["customerName"]
        ).first
        expect(card.get_by_text("Unassigned", exact=True).first).to_be_visible()


# ===========================================================================
# TestDashboardTopTopicValues — intent breakdown from MOCK_INTENT_BREAKDOWN
# ===========================================================================

class TestDashboardTopTopicValues:
    """Each topic row pairs display label with invocation count / percentage."""

    def test_top_topics_match_intent_mock(self, admin_page: Page):
        """Compact top-topics list shows each mock intent count beside its label."""
        _wait_for_data(admin_page)
        for intent in MOCK_INTENT_BREAKDOWN["intents"]:
            expect_topic_row(
                admin_page,
                agent=intent["agent"],
                invocation_count=intent["invocationCount"],
            )

    def test_topic_breakdown_table_matches_intent_mock(self, admin_page: Page):
        """Topic breakdown table rows bind count and percentage to the same label."""
        _wait_for_data(admin_page)
        for intent in MOCK_INTENT_BREAKDOWN["intents"]:
            label = agent_display_label(intent["agent"])
            row = admin_page.locator("main table tbody tr").filter(has_text=label).first
            expect(row).to_be_visible()
            expect(
                row.get_by_text(str(intent["invocationCount"]), exact=True).first
            ).to_be_visible()
            pct = float(intent["percentage"])
            pct_text = f"{int(pct)}%" if pct == int(pct) else f"{pct}%"
            expect(row.get_by_text(pct_text, exact=True).first).to_be_visible()

    def test_topic_counts_track_overridden_intents(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ):
        """Overriding /api/analytics/intents updates topic counts in the UI."""
        alt_intents = {
            "intents": [
                {"agent": "order-tracking", "invocationCount": 91, "percentage": 50.0},
                {"agent": "product-inquiry", "invocationCount": 55, "percentage": 30.0},
                {"agent": "return-refund", "invocationCount": 36, "percentage": 20.0},
            ]
        }
        api_mocker.override("/api/analytics/intents", alt_intents)
        setup_admin_page(page, api_mocker)
        _wait_for_data(page)
        for intent in alt_intents["intents"]:
            expect_topic_row(
                page,
                agent=intent["agent"],
                invocation_count=intent["invocationCount"],
            )
        # Default fixture counts must not remain as page-wide rubber stamps.
        expect(page.get_by_text("45", exact=True)).to_have_count(0)
        expect(page.get_by_text("38", exact=True)).to_have_count(0)


# ===========================================================================
# TestDashboardKnowledgeGapValues — gap table from MOCK_KNOWLEDGE_GAPS_DATA
# ===========================================================================

class TestDashboardKnowledgeGapValues:
    """Each knowledge gap row must display correct conversation data."""

    def test_gap_rows_match_mock(self, admin_page: Page):
        """Gap table rows pair conversationId with status, turns, and messages."""
        _wait_for_data(admin_page)
        gaps_table = admin_page.locator("main table").filter(
            has_text="conv-gap-001"
        ).first
        expect(gaps_table).to_be_visible()
        for gap in MOCK_KNOWLEDGE_GAPS_DATA["gaps"]:
            row = gaps_table.locator("tbody tr").filter(
                has_text=gap["conversationId"]
            ).first
            expect(row).to_be_visible()
            expect(row.get_by_text(gap["status"], exact=True).first).to_be_visible()
            expect(
                row.get_by_text(str(gap["turnCount"]), exact=True).first
            ).to_be_visible()
            expect(
                row.get_by_text(str(gap["messageCount"]), exact=True).first
            ).to_be_visible()
            if gap.get("customerId"):
                expect(
                    row.get_by_text(gap["customerId"], exact=True).first
                ).to_be_visible()

    def test_gap_count_badge_matches_mock_length(self, admin_page: Page):
        """Gap count badge derives from len(MOCK_KNOWLEDGE_GAPS_DATA['gaps'])."""
        _wait_for_data(admin_page)
        expected = f"{len(MOCK_KNOWLEDGE_GAPS_DATA['gaps'])} gaps"
        expect(admin_page.get_by_text(expected, exact=True).first).to_be_visible()

    def test_first_gap_started_at_scoped_to_row(self, admin_page: Page):
        """startedAt renders inside the gap row (not a bare day-of-month match)."""
        _wait_for_data(admin_page)
        gap = MOCK_KNOWLEDGE_GAPS_DATA["gaps"][0]
        row = admin_page.locator("main table tbody tr").filter(
            has_text=gap["conversationId"]
        ).first
        # formatLastSeen → locale short date containing year from ISO timestamp
        expect(row.get_by_text("2026", exact=False).first).to_be_visible()


# ===========================================================================
# TestDashboardSetupChecklist — conditional display verification
# ===========================================================================

class TestDashboardSetupChecklist:
    """Setup checklist shown only when tenant is not active."""

    def test_checklist_hidden_when_active(self, admin_page: Page):
        """Active tenant (default mock) does NOT show 'Setup progress'."""
        _wait_for_data(admin_page)
        checklist = admin_page.get_by_text("Setup progress")
        expect(checklist).to_have_count(0)

    def test_checklist_visible_when_inactive(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ):
        """Inactive tenant shows 'Setup progress' checklist."""
        api_mocker.override("/api/config/activation-status", {
            "status": "draft",
            "is_configured": True,
            "is_active": False,
            "can_activate": True,
            "has_pending_changes": True,
            "active_version": 1,
            "active_activated_at": "2026-02-20T12:00:00Z",
            "draft_version": 2,
        })
        setup_admin_page(page, api_mocker)
        _wait_for_data(page)
        expect(page.get_by_text("Setup progress").first).to_be_visible()

    def test_checklist_shows_brand_name_item(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ):
        """Checklist includes 'Brand name configured' step."""
        api_mocker.override("/api/config/activation-status", {
            "status": "draft",
            "is_configured": True,
            "is_active": False,
            "active_activated_at": "2026-02-20T12:00:00Z",
            "active_version": 1,
        })
        setup_admin_page(page, api_mocker)
        _wait_for_data(page)
        expect(page.get_by_text("Brand name configured").first).to_be_visible()

    def test_checklist_shows_system_activated_item(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ):
        """Checklist includes 'System activated' step."""
        api_mocker.override("/api/config/activation-status", {
            "status": "draft",
            "is_configured": True,
            "is_active": False,
            "active_activated_at": "2026-02-20T12:00:00Z",
            "active_version": 1,
        })
        setup_admin_page(page, api_mocker)
        _wait_for_data(page)
        expect(page.get_by_text("System activated").first).to_be_visible()


# ===========================================================================
# TestDashboardPeriodFilter — verify default and available filter options
# ===========================================================================

class TestDashboardPeriodFilter:
    """Period filter defaults and options must render correctly."""

    def test_default_period_30d_visible(self, admin_page: Page):
        """Default period '30d' label is visible in the SegmentedControl."""
        selected = admin_page.locator("label").filter(has_text="30d")
        expect(selected.first).to_be_visible()

    def test_chart_shows_last_30_days_by_default(self, admin_page: Page):
        """Chart period label reads 'Last 30 days' on initial load."""
        expect(admin_page.get_by_text("Last 30 days").first).to_be_visible()

    def test_7d_option_visible(self, admin_page: Page):
        """Period option '7d' is available."""
        expect(admin_page.locator("label").filter(has_text="7d").first).to_be_visible()

    def test_14d_option_visible(self, admin_page: Page):
        """Period option '14d' is available."""
        expect(admin_page.locator("label").filter(has_text="14d").first).to_be_visible()

    def test_90d_option_visible(self, admin_page: Page):
        """Period option '90d' is available."""
        expect(admin_page.locator("label").filter(has_text="90d").first).to_be_visible()

    def test_store_name_from_brand_config(self, admin_page: Page):
        """Store name header shows brand_name from MOCK_CONFIG."""
        _wait_for_data(admin_page)
        brand = str(MOCK_CONFIG["config"]["brand_name"])
        expect(admin_page.get_by_text(brand).first).to_be_visible()
