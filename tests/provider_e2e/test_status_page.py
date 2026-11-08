# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Status Page: show-resolved toggle, create/update/resolve → API."""

import json
import re

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def status_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/status", wait_text="Status Page")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestStatusPageLoaded:
    def test_active_incident_rendered(self, status_page: Page) -> None:
        expect(status_page.get_by_text("Elevated API latency")).to_be_visible()
        expect(status_page.get_by_text("investigating").first).to_be_visible()
        expect(status_page.get_by_role("button", name="Create Incident")).to_be_visible()
        # Resolved section starts collapsed (toggle advertises Show, not Hide)
        expect(status_page.get_by_role("button", name=re.compile(r"Show 1 Resolved Incident"))).to_be_visible()
        expect(status_page.get_by_role("button", name=re.compile(r"Hide 1 Resolved Incident"))).to_have_count(0)


class TestShowResolvedToggle:
    def test_toggle_reveals_and_hides_resolved(self, status_page: Page) -> None:
        status_page.get_by_role("button", name=re.compile(r"Show 1 Resolved Incident")).click()
        expect(status_page.get_by_role("button", name=re.compile(r"Hide 1 Resolved Incident"))).to_be_visible()
        expect(status_page.get_by_text("Scheduled database migration")).to_be_visible()
        expect(status_page.get_by_text("resolved", exact=True).first).to_be_visible()
        status_page.get_by_role("button", name=re.compile(r"Hide 1 Resolved Incident")).click()
        expect(status_page.get_by_role("button", name=re.compile(r"Show 1 Resolved Incident"))).to_be_visible()
        expect(status_page.get_by_role("button", name=re.compile(r"Hide 1 Resolved Incident"))).to_have_count(0)


class TestIncidentMutations:
    def test_create_incident_posts_payload(self, status_page: Page) -> None:
        mocker: ProviderApiMocker = status_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        status_page.get_by_role("button", name="Create Incident").click()
        dialog = status_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_label("Title").fill("E2E synthetic outage")
        dialog.get_by_label("Description").fill("Deep journey create")
        dialog.get_by_role("textbox", name="Severity").click()
        status_page.get_by_role("option", name="Critical").click()
        dialog.get_by_role("button", name="Create Incident").click()
        status_page.wait_for_timeout(500)

        posts = mocker.get_calls(method="POST", path_contains="/api/superadmin/incidents")
        posts = [p for p in posts if p["path"].rstrip("/").endswith("/incidents")]
        assert len(posts) == 1, f"Expected create POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["title"] == "E2E synthetic outage"
        assert body["description"] == "Deep journey create"
        assert body["severity"] == "critical"
        expect(status_page.get_by_text("Incident created")).to_be_visible(timeout=5_000)
        expect(status_page.get_by_text("E2E synthetic outage")).to_be_visible()

    def test_add_update_posts_status_and_message(self, status_page: Page) -> None:
        mocker: ProviderApiMocker = status_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        status_page.get_by_role("button", name="Add Update").first.click()
        dialog = status_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("textbox", name="Status").click()
        status_page.get_by_role("option", name="Identified").click()
        dialog.get_by_label("Update Message").fill("Root cause confirmed in e2e")
        dialog.get_by_role("button", name="Post Update").click()
        status_page.wait_for_timeout(500)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/incidents/inc-active-001/update",
        )
        assert len(posts) == 1, f"Expected update POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["status"] == "identified"
        assert body["message"] == "Root cause confirmed in e2e"
        expect(status_page.get_by_text("Incident updated")).to_be_visible(timeout=5_000)

    def test_resolve_posts_and_moves_to_resolved(self, status_page: Page) -> None:
        mocker: ProviderApiMocker = status_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        status_page.get_by_role("button", name="Resolve").first.click()
        status_page.wait_for_timeout(500)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/incidents/inc-active-001/resolve",
        )
        assert len(posts) == 1, f"Expected resolve POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body.get("message") == "Incident resolved"
        expect(status_page.get_by_text("Incident resolved")).to_be_visible(timeout=5_000)
        # Active list empty → operational badge; resolved count increases
        expect(status_page.get_by_text("All Systems Operational")).to_be_visible()
        expect(status_page.get_by_role("button", name=re.compile(r"Show 2 Resolved Incidents"))).to_be_visible()
