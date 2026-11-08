"""
E2E tests — Agents configuration page.

Strong coverage (GOV-18):
  - List + search filter (visibility transitions)
  - Select agent → detail panel + URL ?agent= sync + detail GETs
  - Enable/disable agent → PUT /api/admin/agents/{id}/overlay
  - Skill binding toggle → PUT / DELETE binding
  - Empty / error states via route overrides

Run with:
    pytest tests/e2e/test_agents_page.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import AdminApiMocker, setup_admin_page

pytestmark = pytest.mark.e2e


# ===========================================================================
# Structure
# ===========================================================================


class TestAgentsPageStructure:
    """Agents page chrome and list rendering."""

    def test_page_title_visible(self, admin_agents_page: Page) -> None:
        expect(admin_agents_page.get_by_role("heading", name="Agents configuration")).to_be_visible()

    def test_page_description_visible(self, admin_agents_page: Page) -> None:
        expect(
            admin_agents_page.locator("text=Configure agents and manage skill bindings.")
        ).to_be_visible()

    def test_agent_list_loads_via_api(self, admin_agents_page: Page) -> None:
        """Selecting the page triggers GET /api/admin/agents and renders rows."""
        mocker: AdminApiMocker = admin_agents_page._api_mocker  # type: ignore[attr-defined]
        list_gets = [
            c
            for c in mocker.get_calls(method="GET", path_contains="/api/admin/agents")
            if c["url"].rstrip("/").endswith("/api/admin/agents")
            or c["url"].rstrip("/").endswith("/api/admin/agents?")
            or "/api/admin/agents?" in c["url"]
            or (
                "/api/admin/agents" in c["url"]
                and "/overlay" not in c["url"]
                and "/bindings" not in c["url"]
                and "/skills" not in c["url"]
                and "/effective" not in c["url"]
                and "/bindable" not in c["url"]
                and "/available" not in c["url"]
            )
        ]
        # Prefer exact list endpoint; fall back to any agents GET that is not a sub-resource
        assert list_gets or mocker.get_calls(method="GET", path_contains="/api/admin/agents"), (
            f"Expected GET /api/admin/agents, got: {mocker.api_calls}"
        )
        expect(admin_agents_page.locator("text=Campaigns Agent").first).to_be_visible()
        expect(admin_agents_page.locator("text=Intent Classifier").first).to_be_visible()
        expect(admin_agents_page.locator("text=Gateway Agent").first).to_be_visible()

    def test_disabled_badge_on_gateway(self, admin_agents_page: Page) -> None:
        gateway_block = admin_agents_page.locator("div").filter(
            has_text="Gateway Agent"
        ).filter(has_text="disabled")
        assert gateway_block.count() >= 1, "Gateway Agent should show a disabled badge"

    def test_empty_selection_prompt(self, admin_agents_page: Page) -> None:
        expect(admin_agents_page.locator("text=Select an agent to view details")).to_be_visible()


# ===========================================================================
# Search filter
# ===========================================================================


class TestAgentSearch:
    """Search filters the agent list (visibility transitions)."""

    def test_search_filters_to_matching_agent(self, admin_agents_page: Page) -> None:
        search = admin_agents_page.get_by_placeholder("Search agents…")
        expect(search).to_be_visible()
        search.fill("Campaigns")
        admin_agents_page.wait_for_timeout(200)
        expect(admin_agents_page.locator("text=Campaigns Agent").first).to_be_visible()
        expect(admin_agents_page.locator("text=Intent Classifier")).to_have_count(0)

    def test_search_no_match_shows_empty(self, admin_agents_page: Page) -> None:
        search = admin_agents_page.get_by_placeholder("Search agents…")
        search.fill("zzz-no-such-agent")
        admin_agents_page.wait_for_timeout(200)
        expect(admin_agents_page.locator("text=No matching agents")).to_be_visible()
        expect(admin_agents_page.locator("text=Campaigns Agent")).to_have_count(0)

    def test_clear_search_restores_list(self, admin_agents_page: Page) -> None:
        search = admin_agents_page.get_by_placeholder("Search agents…")
        search.fill("Gateway")
        admin_agents_page.wait_for_timeout(150)
        expect(admin_agents_page.locator("text=Campaigns Agent")).to_have_count(0)
        search.fill("")
        admin_agents_page.wait_for_timeout(150)
        expect(admin_agents_page.locator("text=Campaigns Agent").first).to_be_visible()
        expect(admin_agents_page.locator("text=Intent Classifier").first).to_be_visible()


# ===========================================================================
# Selection → detail + URL + API
# ===========================================================================


class TestAgentSelection:
    """Selecting an agent opens detail and syncs URL + detail GETs."""

    def test_select_agent_loads_detail_and_apis(self, admin_agents_page: Page) -> None:
        mocker: AdminApiMocker = admin_agents_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        admin_agents_page.locator("text=Campaigns Agent").first.click()
        expect(admin_agents_page.get_by_role("heading", name="Campaigns Agent")).to_be_visible()
        expect(admin_agents_page.locator("text=Select an agent to view details")).to_have_count(0)

        # URL sync (?agent=campaigns)
        assert "agent=campaigns" in admin_agents_page.url, (
            f"Expected ?agent=campaigns in URL, got: {admin_agents_page.url}"
        )

        admin_agents_page.wait_for_timeout(400)
        overlay_gets = mocker.get_calls(method="GET", path_contains="/agents/campaigns/overlay")
        bindings_gets = mocker.get_calls(method="GET", path_contains="/agents/campaigns/bindings")
        bindable_gets = mocker.get_calls(method="GET", path_contains="/agents/campaigns/bindable-skills")
        assert len(overlay_gets) >= 1, f"Expected overlay GET, got: {mocker.api_calls}"
        assert len(bindings_gets) >= 1, f"Expected bindings GET, got: {mocker.api_calls}"
        assert len(bindable_gets) >= 1, f"Expected bindable-skills GET, got: {mocker.api_calls}"

    def test_skills_table_renders_after_select(self, admin_agents_page: Page) -> None:
        admin_agents_page.locator("text=Campaigns Agent").first.click()
        expect(admin_agents_page.locator("text=List Active Campaigns").first).to_be_visible()
        expect(admin_agents_page.locator("text=Get Talking Points").first).to_be_visible()
        expect(admin_agents_page.locator("text=3 available").first).to_be_visible()


# ===========================================================================
# UI → API mutations
# ===========================================================================


class TestAgentMutations:
    """Toggle agent / skill bindings assert intercepted PUT/DELETE."""

    def test_toggle_agent_sends_overlay_put(self, admin_agents_page: Page) -> None:
        mocker: AdminApiMocker = admin_agents_page._api_mocker  # type: ignore[attr-defined]
        admin_agents_page.locator("text=Campaigns Agent").first.click()
        expect(admin_agents_page.get_by_role("heading", name="Campaigns Agent")).to_be_visible()

        mocker.clear_calls()
        # Mantine Switch: click the label/root, not the hidden input
        switch_root = admin_agents_page.locator(".mantine-Switch-root").filter(has_text="Enabled").first
        expect(switch_root).to_be_visible()
        switch_root.click()
        admin_agents_page.wait_for_timeout(500)

        puts = mocker.get_calls(method="PUT", path_contains="/agents/campaigns/overlay")
        assert len(puts) >= 1, f"Toggle should PUT overlay, got: {mocker.api_calls}"
        body = json.loads(puts[0].get("body") or "{}")
        assert body.get("enabled") is False, f"Expected enabled=false after toggle, got: {body}"

    def test_enable_unbound_skill_sends_put_binding(self, admin_agents_page: Page) -> None:
        mocker: AdminApiMocker = admin_agents_page._api_mocker  # type: ignore[attr-defined]
        admin_agents_page.locator("text=Campaigns Agent").first.click()
        expect(admin_agents_page.locator("text=Get Talking Points").first).to_be_visible()

        mocker.clear_calls()
        row = admin_agents_page.locator("tr", has_text="Get Talking Points")
        row.locator(".mantine-Switch-root").first.click()
        admin_agents_page.wait_for_timeout(500)

        puts = mocker.get_calls(method="PUT", path_contains="get-talking-points")
        puts = [c for c in puts if "/binding" in c["url"]]
        assert len(puts) >= 1, f"Enable skill should PUT binding, got: {mocker.api_calls}"
        body = json.loads(puts[0].get("body") or "{}")
        assert body.get("enabled") is True
        assert body.get("mode") == "read"

    def test_disable_bound_skill_sends_delete_binding(self, admin_agents_page: Page) -> None:
        mocker: AdminApiMocker = admin_agents_page._api_mocker  # type: ignore[attr-defined]
        admin_agents_page.locator("text=Campaigns Agent").first.click()
        expect(admin_agents_page.locator("text=List Active Campaigns").first).to_be_visible()

        mocker.clear_calls()
        row = admin_agents_page.locator("tr", has_text="List Active Campaigns")
        row.locator(".mantine-Switch-root").first.click()
        admin_agents_page.wait_for_timeout(500)

        deletes = mocker.get_calls(method="DELETE", path_contains="list-active")
        deletes = [c for c in deletes if "/binding" in c["url"]]
        assert len(deletes) >= 1, f"Disable skill should DELETE binding, got: {mocker.api_calls}"


# ===========================================================================
# Error / empty states
# ===========================================================================


class TestAgentsErrorStates:
    """Error and empty list states via overrides."""

    def test_agents_list_error_message(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override("/api/admin/agents", {"detail": "boom"}, status=500)
        setup_admin_page(page, api_mocker)
        page.locator("nav >> text=Agents configuration").first.click()
        page.wait_for_timeout(800)
        expect(page.locator("text=Failed to load agents")).to_be_visible()

    def test_empty_agents_list(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override("/api/admin/agents", [], status=200)
        setup_admin_page(page, api_mocker)
        page.locator("nav >> text=Agents configuration").first.click()
        page.wait_for_timeout(800)
        expect(page.locator("text=No agents registered")).to_be_visible()
