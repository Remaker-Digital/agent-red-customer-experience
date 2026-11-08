"""
E2E tests — Knowledge Score page (SPEC-1873).

Strong coverage:
  - Professional tier: score ring, factors, stats, gap table
  - Period segmented control → GET score/gaps with updated ``since``
  - Gap row expand → conversation IDs visible
  - Starter tier: upgrade alert; no score/gaps API calls
  - Error + empty gap states via overrides

Run with:
    pytest tests/e2e/test_knowledge_score_page.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import AdminApiMocker, setup_admin_page

pytestmark = pytest.mark.e2e


# ===========================================================================
# Structure (Professional tier — default fixture)
# ===========================================================================


class TestKnowledgeScoreStructure:
    """Score card, factors, stats, and gap table on Professional tier."""

    def test_page_heading_visible(self, admin_knowledge_score_page: Page) -> None:
        expect(
            admin_knowledge_score_page.get_by_role("heading", name="Knowledge Score")
        ).to_be_visible()

    def test_loads_score_and_gaps_apis(self, admin_knowledge_score_page: Page) -> None:
        mocker: AdminApiMocker = admin_knowledge_score_page._api_mocker  # type: ignore[attr-defined]
        score_gets = mocker.get_calls(method="GET", path_contains="/api/admin/knowledge/score")
        gap_gets = mocker.get_calls(method="GET", path_contains="/api/admin/knowledge/gaps/review")
        assert len(score_gets) >= 1, f"Expected score GET, got: {mocker.api_calls}"
        assert len(gap_gets) >= 1, f"Expected gaps GET, got: {mocker.api_calls}"
        assert any("since=" in c["url"] for c in score_gets), (
            f"Score GET should include since=, got: {score_gets}"
        )

    def test_composite_score_and_trend(self, admin_knowledge_score_page: Page) -> None:
        expect(admin_knowledge_score_page.locator("text=Composite Score")).to_be_visible()
        # Ring label shows integer score 74
        expect(admin_knowledge_score_page.locator("text=74").first).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=+3.1").first).to_be_visible()

    def test_factor_breakdown_visible(self, admin_knowledge_score_page: Page) -> None:
        for label in ("Coverage", "Relevance", "Escalation rate", "Freshness"):
            expect(admin_knowledge_score_page.locator(f"text={label}").first).to_be_visible()

    def test_stats_row_values(self, admin_knowledge_score_page: Page) -> None:
        expect(admin_knowledge_score_page.locator("text=Total conversations")).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=342").first).to_be_visible()
        expect(
            admin_knowledge_score_page.get_by_text("Unanswered", exact=True)
        ).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=28").first).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=KB entries")).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=156").first).to_be_visible()

    def test_gap_table_renders_clusters(self, admin_knowledge_score_page: Page) -> None:
        expect(
            admin_knowledge_score_page.locator("text=Unanswered Question Clusters")
        ).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=28 total gaps")).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=return_policy").first).to_be_visible()
        expect(
            admin_knowledge_score_page.locator(
                "text=What is your return policy for opened items?"
            ).first
        ).to_be_visible()


# ===========================================================================
# Period filter → API
# ===========================================================================


class TestKnowledgeScorePeriodFilter:
    """SegmentedControl period changes refetch with new since=."""

    def test_period_7d_refetches_with_since(
        self, admin_knowledge_score_page: Page
    ) -> None:
        mocker: AdminApiMocker = admin_knowledge_score_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        # Mantine SegmentedControl hides the radio input — click the visible label
        admin_knowledge_score_page.locator("label", has_text="7d").first.click()
        admin_knowledge_score_page.wait_for_timeout(600)

        score_gets = mocker.get_calls(method="GET", path_contains="/api/admin/knowledge/score")
        gap_gets = mocker.get_calls(method="GET", path_contains="/api/admin/knowledge/gaps/review")
        assert len(score_gets) >= 1, f"Period change should refetch score, got: {mocker.api_calls}"
        assert len(gap_gets) >= 1, f"Period change should refetch gaps, got: {mocker.api_calls}"
        # since= should be present on both
        assert all("since=" in c["url"] for c in score_gets)
        assert all("since=" in c["url"] for c in gap_gets)


# ===========================================================================
# Gap row expand
# ===========================================================================


class TestGapRowExpand:
    """Expanding a gap cluster reveals conversation IDs."""

    def test_expand_gap_row_shows_conversation_ids(
        self, admin_knowledge_score_page: Page
    ) -> None:
        row = admin_knowledge_score_page.locator("tr", has_text="return_policy")
        expect(row.first).to_be_visible()
        row.first.click()
        admin_knowledge_score_page.wait_for_timeout(300)
        expect(
            admin_knowledge_score_page.locator("text=Conversation IDs (4)").first
        ).to_be_visible()
        expect(admin_knowledge_score_page.locator("text=conv-001").first).to_be_visible()


# ===========================================================================
# Tier gating
# ===========================================================================


class TestKnowledgeScoreTier:
    """Starter tier shows upgrade prompt and skips score/gaps fetches."""

    def test_starter_tier_shows_upgrade_and_skips_api(
        self, page: Page, admin_vite_server, api_mocker_starter: AdminApiMocker
    ) -> None:
        setup_admin_page(page, api_mocker_starter)
        api_mocker_starter.clear_calls()

        page.locator("nav >> text=Knowledge score").first.click()
        page.wait_for_timeout(800)

        expect(page.get_by_role("heading", name="Knowledge Score")).to_be_visible()
        expect(page.locator("text=Professional tier required")).to_be_visible()
        expect(
            page.locator("text=Knowledge Score and gap review require a Professional")
        ).to_be_visible()
        # Must not fetch score/gaps when gated (prevents 403 logout)
        assert api_mocker_starter.get_calls(path_contains="/knowledge/score") == []
        assert api_mocker_starter.get_calls(path_contains="/gaps/review") == []
        # Score UI must not render
        expect(page.locator("text=Composite Score")).to_have_count(0)
        expect(page.locator("text=Factor Breakdown")).to_have_count(0)


# ===========================================================================
# Error / empty
# ===========================================================================


class TestKnowledgeScoreErrors:
    """Error and empty-gap states."""

    def test_score_error_alert(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/admin/knowledge/score",
            {"detail": "score unavailable"},
            status=500,
        )
        setup_admin_page(page, api_mocker)
        page.locator("nav >> text=Knowledge score").first.click()
        page.wait_for_timeout(800)
        expect(page.locator("text=Error loading score")).to_be_visible()

    def test_empty_gaps_message(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/admin/knowledge/gaps/review",
            {
                "tenantId": "test-tenant-001",
                "since": "2026-01-01T00:00:00Z",
                "until": "2026-02-01T00:00:00Z",
                "totalGaps": 0,
                "clusters": [],
            },
            status=200,
        )
        setup_admin_page(page, api_mocker)
        page.locator("nav >> text=Knowledge score").first.click()
        page.wait_for_timeout(800)
        expect(
            page.locator("text=No unanswered question clusters found for this period.")
        ).to_be_visible()
