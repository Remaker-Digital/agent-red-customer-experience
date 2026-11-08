# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Service Messages: filter, preview, confirm send → API."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def service_messages_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/service-messages", wait_text="Service Messages")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestServiceMessagePreviewAndSend:
    def test_preview_with_status_filter_then_send(self, service_messages_page: Page) -> None:
        mocker: ProviderApiMocker = service_messages_page._api_mocker  # type: ignore[attr-defined]

        service_messages_page.get_by_label("Subject").fill("E2E platform notice")
        service_messages_page.get_by_label("Message body (HTML supported)").fill("<p>Deep journey service message</p>")

        # MultiSelect filter — open and pick Active
        service_messages_page.get_by_role("textbox", name="Tenant status").click()
        service_messages_page.get_by_role("option", name="Active").click()
        # Close dropdown (Escape) so Preview is clickable
        service_messages_page.keyboard.press("Escape")
        mocker.clear_calls()

        service_messages_page.get_by_role("button", name="Preview Recipients").click()
        service_messages_page.wait_for_timeout(500)

        previews = mocker.get_calls(
            method="POST",
            path_contains="/api/superadmin/service-messages/preview",
        )
        assert len(previews) == 1, f"Expected preview POST, got {previews}"
        assert "filter_status=active" in previews[0]["url"], f"Expected filter_status=active in {previews[0]['url']}"
        expect(service_messages_page.get_by_text("admin@acme-corp.com")).to_be_visible()
        expect(service_messages_page.get_by_text("2 tenant(s)")).to_be_visible()

        send_btn = service_messages_page.get_by_role("button", name="Send Service Message")
        expect(send_btn).to_be_enabled()
        mocker.clear_calls()
        send_btn.click()
        dialog = service_messages_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Confirm & Send").click()
        service_messages_page.wait_for_timeout(500)

        sends = mocker.get_calls(
            method="POST",
            path_contains="/api/superadmin/service-messages/send",
        )
        assert len(sends) == 1, f"Expected send POST, got {sends}"
        body = json.loads(sends[0]["body"] or "{}")
        assert body["subject"] == "E2E platform notice"
        assert "Deep journey" in body["body"]
        assert body.get("filterStatus") == ["active"]
        expect(service_messages_page.get_by_text("Message sent", exact=True)).to_be_visible(timeout=5_000)
        expect(service_messages_page.get_by_text("Sent to 2 of 2 recipient(s).")).to_be_visible()
