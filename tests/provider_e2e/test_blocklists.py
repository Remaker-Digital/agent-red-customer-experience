# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Blocklists: tab switch, check value, edit save → API."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def blocklists_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/blocklists", wait_text="Allow/Block Lists")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestBlocklistLists:
    def test_lists_tab_shows_types(self, blocklists_page: Page) -> None:
        expect(blocklists_page.get_by_role("tab", name="Lists (2)")).to_be_visible()
        panel = blocklists_page.get_by_role("tabpanel", name="Lists (2)")
        expect(panel.get_by_text("Email Domains")).to_be_visible()
        expect(panel.get_by_text("IP Addresses")).to_be_visible()
        expect(panel.get_by_text("spam.example.com")).to_have_count(0)


class TestBlocklistCheckValue:
    def test_check_tab_posts_and_shows_block(self, blocklists_page: Page) -> None:
        mocker: ProviderApiMocker = blocklists_page._api_mocker  # type: ignore[attr-defined]
        blocklists_page.get_by_role("tab", name="Check Value").click()
        panel = blocklists_page.get_by_role("tabpanel", name="Check Value")
        mocker.clear_calls()

        panel.get_by_role("textbox", name="List Type").click()
        blocklists_page.get_by_role("option", name="Email Domains").click()
        panel.get_by_label("Value to Check").fill("spam.example.com")
        panel.get_by_role("button", name="Check").click()
        blocklists_page.wait_for_timeout(400)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/api/superadmin/blocklists/email_domain/check",
        )
        assert len(posts) == 1, f"Expected check POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["value"] == "spam.example.com"
        expect(panel.get_by_text("BLOCK", exact=True)).to_be_visible()
        expect(panel.get_by_text("Matched:")).to_be_visible()
        expect(panel.get_by_text("spam.example.com").first).to_be_visible()


class TestBlocklistEditSave:
    def test_edit_adds_entry_and_puts(self, blocklists_page: Page) -> None:
        mocker: ProviderApiMocker = blocklists_page._api_mocker  # type: ignore[attr-defined]
        row = blocklists_page.locator("tr", has_text="Email Domains")
        row.get_by_role("button", name="Edit").click()
        dialog = blocklists_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("spam.example.com")).to_be_visible()

        dialog.get_by_placeholder("Value (IP, domain, etc.)").fill("phish.test")
        dialog.get_by_placeholder("Reason (optional)").fill("e2e blocklist entry")
        dialog.get_by_role("button", name="Add").click()
        expect(dialog.get_by_text("phish.test")).to_be_visible()
        mocker.clear_calls()

        dialog.get_by_role("button", name="Save").click()
        blocklists_page.wait_for_timeout(500)

        puts = mocker.get_calls(
            method="PUT",
            path_contains="/api/superadmin/blocklists/email_domain",
        )
        assert len(puts) == 1, f"Expected blocklist PUT, got {puts}"
        body = json.loads(puts[0]["body"] or "{}")
        values = [e["value"] for e in body.get("entries", [])]
        assert "spam.example.com" in values
        assert "phish.test" in values
        assert body.get("defaultAction") == "allow"
        expect(blocklists_page.get_by_text("Updated email_domain blocklist")).to_be_visible(timeout=5_000)
