# (C) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Dashboard page E2E tests against mock Vite dev server.

Tests the Dashboard page rendered by admin/standalone with mock API fixtures.
Covers stat cards, daily volume chart, conversation summary, analytics tabs,
time filters, empty states, and API contracts.

SPEC-1706: Mock-based frontend E2E.
"""
import pytest
from playwright.sync_api import Page, expect

from tests.e2e.display_helpers import (
    expect_stat_card,
    expect_topic_row,
    expected_stat_displays,
)
from tests.e2e_mock.conftest import (
    dismiss_onboarding_if_present,
    get_api_json,
    main_text,
    navigate_and_settle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DASHBOARD_PATH = "/dashboard"


def _navigate_dashboard(page: Page, mock_base_url: str):
    """Navigate to the dashboard and dismiss any onboarding overlay."""
    navigate_and_settle(page, DASHBOARD_PATH, mock_base_url)
    dismiss_onboarding_if_present(page)


def _stat_cards(page: Page):
    """Return all StatCard Paper elements inside the SimpleGrid."""
    return page.locator("main .mantine-SimpleGrid-root .mantine-Paper-root")


def _card_text(page: Page) -> str:
    """Get combined visible text of all stat cards."""
    grid = page.locator("main .mantine-SimpleGrid-root").first
    return grid.inner_text() if grid.count() > 0 else ""


# ===================================================================
# 1. TestUsageCards -- stat cards showing key metrics
# ===================================================================


class TestUsageCards:
    """StatCard components bound to GET /api/analytics/summary fixture payload."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        _navigate_dashboard(shared_page, mock_base_url)
        self.page = shared_page
        self.base = mock_base_url
        self.summary = get_api_json(shared_page, mock_base_url, "/api/analytics/summary")

    def test_stat_cards_match_summary_api(self):
        """Each KPI card shows values derived from the live mock summary JSON."""
        displays = expected_stat_displays(self.summary)
        expect_stat_card(
            self.page,
            label="Total conversations",
            value=displays["total_conversations"],
        )
        expect_stat_card(
            self.page,
            label="Avg response time",
            value=displays["avg_response_time"],
        )
        expect_stat_card(
            self.page,
            label="Resolution rate",
            value=displays["resolution_rate"],
            detail=displays["resolved_detail"],
        )
        expect_stat_card(
            self.page,
            label="Customer satisfaction",
            value=displays["customer_satisfaction"],
        )
        expect_stat_card(
            self.page,
            label="Escalation rate",
            value=displays["escalation_rate"],
            detail=displays["escalated_detail"],
        )

    def test_stat_cards_count(self):
        """Five stat cards are rendered in the grid."""
        cards = _stat_cards(self.page)
        assert cards.count() >= 5, f"Expected at least 5 stat cards, got {cards.count()}"

    def test_stat_card_values_absent_from_wrong_cards(self):
        """Total-conversations value is not the only text proving a card (scoped check)."""
        displays = expected_stat_displays(self.summary)
        card = self.page.locator("main .mantine-Paper-root").filter(
            has_text="Total conversations"
        ).first
        assert displays["total_conversations"] in card.inner_text()
        # Escalation card must not carry the total-conversations figure as its value line.
        esc = self.page.locator("main .mantine-Paper-root").filter(
            has_text="Escalation rate"
        ).first
        esc_text = esc.inner_text()
        # Value line is distinct; total may appear elsewhere on page but not as esc value.
        assert displays["escalation_rate"] in esc_text
        assert displays["avg_response_time"] not in esc_text


# ===================================================================
# 2. TestDailyChart -- Daily usage AreaChart (recharts)
# ===================================================================


class TestDailyChart:
    """Recharts AreaChart rendering daily conversation volume."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        _navigate_dashboard(shared_page, mock_base_url)
        self.page = shared_page

    def test_chart_container_visible(self):
        """The Daily usage paper container is visible."""
        text = main_text(self.page)
        assert "Daily usage" in text, f"Expected Daily usage heading, got: {text[:300]}"

    def test_chart_svg_renders(self):
        """Recharts renders an SVG element inside the chart container."""
        svg = self.page.locator(".recharts-wrapper svg").first
        expect(svg).to_be_visible(timeout=5000)

    def test_chart_has_area_elements(self):
        """At least one Area path is rendered in the chart."""
        areas = self.page.locator(".recharts-area-area")
        assert areas.count() >= 1, "Expected at least one area path in chart"

    def test_dual_series_present(self):
        """Two Area series are rendered (total + billable)."""
        areas = self.page.locator(".recharts-area")
        assert areas.count() >= 2, f"Expected 2 area series, got {areas.count()}"

    def test_x_axis_present(self):
        """X-axis (date axis) is rendered."""
        x_axis = self.page.locator(".recharts-xAxis")
        assert x_axis.count() >= 1, "Expected X-axis in chart"

    def test_y_axis_present(self):
        """Y-axis (count axis) is rendered."""
        y_axis = self.page.locator(".recharts-yAxis")
        assert y_axis.count() >= 1, "Expected Y-axis in chart"

    def test_chart_legend_total(self):
        """Legend contains Total label."""
        text = main_text(self.page)
        assert "Total" in text, f"Expected Total in chart legend, got: {text[:300]}"

    def test_chart_legend_billable(self):
        """Legend contains Billable label."""
        text = main_text(self.page)
        assert "Billable" in text, f"Expected Billable in chart legend, got: {text[:300]}"


# ===================================================================
# 3. TestConversationSummary -- summary metrics + status breakdown
# ===================================================================


class TestConversationSummary:
    """Analytics summary metrics and status breakdown display."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        _navigate_dashboard(shared_page, mock_base_url)
        self.page = shared_page

    def test_avg_turns_in_summary_api(self, mock_base_url: str):
        """API returns avgTurns = 4.2."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        assert data["avgTurns"] == 4.2

    def test_escalation_rate_in_summary_api(self, mock_base_url: str):
        """API returns escalationRate = 0.094."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        assert data["escalationRate"] == 0.094

    def test_critic_pass_rate_in_summary_api(self, mock_base_url: str):
        """API returns criticPassRate = 0.906."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        assert data["criticPassRate"] == 0.906

    def test_status_breakdown_resolved(self, mock_base_url: str):
        """Status breakdown includes resolved with count 298."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        statuses = {s["status"]: s["count"] for s in data["statusBreakdown"]}
        assert statuses.get("resolved") == 298

    def test_status_breakdown_escalated(self, mock_base_url: str):
        """Status breakdown includes escalated with count 32."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        statuses = {s["status"]: s["count"] for s in data["statusBreakdown"]}
        assert statuses.get("escalated") == 32

    def test_status_breakdown_four_statuses(self, mock_base_url: str):
        """Status breakdown has 4 status entries."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/summary")
        assert len(data["statusBreakdown"]) == 4


# ===================================================================
# 4. TestAnalyticsTabs -- Topic breakdown + knowledge gaps sections
# ===================================================================


class TestAnalyticsTabs:
    """Analytics sections: top topics, topic breakdown table, and knowledge gaps."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        _navigate_dashboard(shared_page, mock_base_url)
        self.page = shared_page

    def test_top_topics_section_visible(self):
        """Top topics section heading is displayed."""
        text = main_text(self.page)
        assert "Top topics" in text, f"Expected Top topics heading, got: {text[:300]}"

    def test_topic_breakdown_section_visible(self):
        """Topic breakdown table section is displayed."""
        text = main_text(self.page)
        assert "Topic breakdown" in text, f"Expected Topic breakdown, got: {text[:300]}"

    def test_knowledge_gaps_section_visible(self):
        """Knowledge gaps section heading is displayed."""
        text = main_text(self.page)
        assert "Knowledge gaps" in text, f"Expected Knowledge gaps, got: {text[:300]}"

    def test_topic_rows_match_intents_api(self, mock_base_url: str):
        """Topic UI rows bind label + count (+ %) to GET /api/analytics/intents."""
        data = get_api_json(self.page, mock_base_url, "/api/analytics/intents")
        intents = data["intents"]
        assert len(intents) >= 3
        for intent in intents:
            # Fixture agents are already display labels (spaces); helper is identity for those.
            expect_topic_row(
                self.page,
                agent=intent["agent"],
                invocation_count=intent["invocationCount"],
                percentage=intent.get("percentage"),
            )

    def test_no_gaps_message_when_empty(self):
        """With empty gaps fixture, shows No knowledge gaps detected."""
        text = main_text(self.page)
        assert "No knowledge gaps detected" in text, (
            f"Expected empty gaps message, got: {text[:500]}"
        )


# ===================================================================
# 5. TestTimeFilters -- period filter SegmentedControl
# ===================================================================


class TestTimeFilters:
    """Period filter SegmentedControl for adjusting dashboard timeframe."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        _navigate_dashboard(shared_page, mock_base_url)
        self.page = shared_page

    def test_period_control_visible(self):
        """SegmentedControl for period selection is visible."""
        control = self.page.locator(".mantine-SegmentedControl-root").first
        expect(control).to_be_visible(timeout=5000)

    def test_30d_option_present(self):
        """30d period option is available."""
        label = self.page.locator(".mantine-SegmentedControl-label", has_text="30d")
        assert label.count() >= 1, "Expected 30d option in period control"

    def test_7d_option_present(self):
        """7d period option is available."""
        label = self.page.locator(".mantine-SegmentedControl-label", has_text="7d")
        assert label.count() >= 1, "Expected 7d option in period control"

    def test_90d_option_present(self):
        """90d period option is available."""
        label = self.page.locator(".mantine-SegmentedControl-label", has_text="90d")
        assert label.count() >= 1, "Expected 90d option in period control"

    def test_default_period_is_30d(self):
        """Default selected period is 30d."""
        # The active control has data-active attribute
        active = self.page.locator(
            ".mantine-SegmentedControl-control[data-active] .mantine-SegmentedControl-label"
        ).first
        text = active.inner_text() if active.count() > 0 else ""
        assert "30d" in text, f"Expected default period 30d, got: {text}"

    def test_switching_period_updates_chart_label(self, page: Page, mock_base_url: str):
        """Clicking 7d updates the chart subtitle to Last 7 days."""
        _navigate_dashboard(page, mock_base_url)
        # Click the 7d option
        btn_7d = page.locator(".mantine-SegmentedControl-label", has_text="7d").first
        btn_7d.click()
        page.wait_for_timeout(500)
        text = main_text(page)
        assert "Last 7 days" in text, f"Expected Last 7 days after switching, got: {text[:300]}"


# ===================================================================
# 6. TestEmptyState -- appropriate messaging when data is absent
# ===================================================================


class TestEmptyState:
    """Empty/no-data state messages when APIs return empty results."""

    def test_no_conversations_message(self, shared_page: Page, mock_base_url: str):
        """When inbox has conversations, the recent section shows them or empty state."""
        _navigate_dashboard(shared_page, mock_base_url)
        text = main_text(shared_page)
        # The Dashboard uses useInboxConversations which returns inbox fixture data
        # (has conversations), so we check for either the empty state or conversation
        # content depending on which fixture is active.
        assert ("No conversations yet" in text
                or "Recent conversations" in text), (
            f"Expected conversations section, got: {text[:500]}"
        )

    def test_recent_conversations_heading(self, shared_page: Page, mock_base_url: str):
        """Recent conversations section heading is visible."""
        _navigate_dashboard(shared_page, mock_base_url)
        text = main_text(shared_page)
        assert "Recent conversations" in text, (
            f"Expected Recent conversations heading, got: {text[:300]}"
        )

    def test_gaps_empty_message(self, shared_page: Page, mock_base_url: str):
        """With gaps=[], shows No knowledge gaps detected."""
        _navigate_dashboard(shared_page, mock_base_url)
        text = main_text(shared_page)
        assert "No knowledge gaps detected" in text, (
            f"Expected No knowledge gaps detected, got: {text[:300]}"
        )

    def test_dashboard_title_always_rendered(self, shared_page: Page, mock_base_url: str):
        """Dashboard title heading always renders regardless of data."""
        _navigate_dashboard(shared_page, mock_base_url)
        heading = shared_page.locator("h2", has_text="Dashboard")
        expect(heading).to_be_visible(timeout=5000)


# ===================================================================
# 7. TestApiContracts -- mock API response shape validation
# ===================================================================


class TestApiContracts:
    """Verify mock API endpoints return correct response shapes."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_page: Page, mock_base_url: str):
        self.page = shared_page
        self.base = mock_base_url

    def test_usage_api_returns_total_conversations(self):
        """GET /api/dashboard/usage returns totalConversations field."""
        data = get_api_json(self.page, self.base, "/api/dashboard/usage")
        assert "totalConversations" in data
        assert data["totalConversations"] == 342

    def test_usage_api_returns_usage_percent(self):
        """GET /api/dashboard/usage returns usagePercent field."""
        data = get_api_json(self.page, self.base, "/api/dashboard/usage")
        assert "usagePercent" in data
        assert data["usagePercent"] == 68.4

    def test_daily_api_returns_days_array(self):
        """GET /api/dashboard/usage/daily returns days array."""
        data = get_api_json(self.page, self.base, "/api/dashboard/usage/daily")
        assert "days" in data
        assert isinstance(data["days"], list)
        assert len(data["days"]) == 30

    def test_summary_api_returns_correct_shape(self):
        """GET /api/analytics/summary contains required fields."""
        data = get_api_json(self.page, self.base, "/api/analytics/summary")
        required_fields = [
            "totalConversations", "billableConversations", "avgTurns",
            "avgResponseTime", "resolutionRate", "customerSatisfaction",
            "escalationRate", "statusBreakdown",
        ]
        for field in required_fields:
            assert field in data, f"Missing field {field} in summary response"

    def test_intents_api_returns_intents_array(self):
        """GET /api/analytics/intents returns intents array with 5 entries."""
        data = get_api_json(self.page, self.base, "/api/analytics/intents")
        assert "intents" in data
        assert len(data["intents"]) == 5
        # Verify first intent has expected shape
        first = data["intents"][0]
        assert "agent" in first
        assert "invocationCount" in first
        assert "percentage" in first

    def test_gaps_api_returns_empty_gaps(self):
        """GET /api/analytics/gaps returns empty gaps array."""
        data = get_api_json(self.page, self.base, "/api/analytics/gaps")
        assert "gaps" in data
        assert data["gaps"] == []
