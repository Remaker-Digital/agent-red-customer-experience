"""
E2E tests — Conversation Preview page (SPEC-1872).

Strong coverage:
  - Professional tier: empty state, send → POST-SSE → assistant bubble
  - Config overrides panel toggle + overrides land in POST body
  - Trace collapse after stream completes
  - Clear resets history
  - Starter tier: upgrade alert; Send UI absent
  - 429 / stream error → Preview error alert

Run with:
    pytest tests/e2e/test_conversation_preview_page.py -v

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


class TestPreviewStructure:
    """Preview chrome on Professional tier."""

    def test_page_heading_visible(self, admin_preview_page: Page) -> None:
        expect(
            admin_preview_page.get_by_role("heading", name="Conversation Preview")
        ).to_be_visible()

    def test_empty_state_prompt(self, admin_preview_page: Page) -> None:
        expect(
            admin_preview_page.locator(
                "text=Send a message to preview your AI agent's response."
            )
        ).to_be_visible()

    def test_send_disabled_when_input_empty(self, admin_preview_page: Page) -> None:
        send = admin_preview_page.get_by_role("button", name="Send")
        expect(send).to_be_disabled()


# ===========================================================================
# Send → POST-SSE → UI
# ===========================================================================


class TestPreviewSend:
    """UI send triggers intercepted POST and renders streamed response."""

    def test_send_posts_preview_chat_and_renders_response(
        self, admin_preview_page: Page
    ) -> None:
        mocker: AdminApiMocker = admin_preview_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        input_box = admin_preview_page.get_by_placeholder(
            "Type a customer message to preview..."
        )
        input_box.fill("Where is my order?")
        admin_preview_page.get_by_role("button", name="Send").click()

        # User bubble
        expect(admin_preview_page.locator("text=Where is my order?").first).to_be_visible()

        # Wait for stream to complete and assistant content to land in history
        expect(
            admin_preview_page.locator("text=This is a preview response for e2e testing.")
        ).to_be_visible(timeout=10_000)
        expect(admin_preview_page.locator("text=Validated").first).to_be_visible()

        posts = mocker.get_calls(method="POST", path_contains="/api/admin/preview/chat")
        assert len(posts) == 1, f"Expected one preview POST, got: {mocker.api_calls}"
        body = json.loads(posts[0].get("body") or "{}")
        assert body.get("message") == "Where is my order?"
        assert "config_overrides" not in body

    def test_config_overrides_included_in_post_body(
        self, admin_preview_page: Page
    ) -> None:
        mocker: AdminApiMocker = admin_preview_page._api_mocker  # type: ignore[attr-defined]

        admin_preview_page.get_by_role("button", name="Config overrides").click()
        expect(
            admin_preview_page.locator("text=Response Overrides (temporary, not persisted)")
        ).to_be_visible()

        # Mantine Select: click the input near the Tone preset label
        tone_input = admin_preview_page.locator(
            ".mantine-Select-input, input[aria-haspopup='listbox']"
        ).first
        tone_input.click()
        admin_preview_page.get_by_role("option", name="Friendly").click()

        # Move confidence away from default 0.6 so override is sent
        slider = admin_preview_page.locator('[role="slider"]').first
        expect(slider).to_be_visible()
        # Focus and use keyboard to change value
        slider.focus()
        admin_preview_page.keyboard.press("ArrowRight")
        admin_preview_page.keyboard.press("ArrowRight")
        admin_preview_page.wait_for_timeout(200)

        mocker.clear_calls()
        admin_preview_page.get_by_placeholder(
            "Type a customer message to preview..."
        ).fill("Need a friendly answer")
        admin_preview_page.get_by_role("button", name="Send").click()

        expect(
            admin_preview_page.locator("text=This is a preview response for e2e testing.")
        ).to_be_visible(timeout=10_000)

        posts = mocker.get_calls(method="POST", path_contains="/api/admin/preview/chat")
        assert len(posts) == 1, f"Expected preview POST, got: {mocker.api_calls}"
        body = json.loads(posts[0].get("body") or "{}")
        assert body.get("message") == "Need a friendly answer"
        overrides = body.get("config_overrides") or {}
        assert overrides.get("response_tone_preset") == "friendly", (
            f"Expected tone override in body, got: {body}"
        )

    def test_show_trace_after_response(self, admin_preview_page: Page) -> None:
        admin_preview_page.get_by_placeholder(
            "Type a customer message to preview..."
        ).fill("Trace please")
        admin_preview_page.get_by_role("button", name="Send").click()
        expect(
            admin_preview_page.locator("text=This is a preview response for e2e testing.")
        ).to_be_visible(timeout=10_000)

        show_trace = admin_preview_page.locator("text=Show trace").first
        expect(show_trace).to_be_visible()
        show_trace.click()
        expect(admin_preview_page.locator("text=Decision Trace")).to_be_visible()
        expect(admin_preview_page.locator("text=general_inquiry").first).to_be_visible()

    def test_clear_resets_history(self, admin_preview_page: Page) -> None:
        admin_preview_page.get_by_placeholder(
            "Type a customer message to preview..."
        ).fill("Clear me")
        admin_preview_page.get_by_role("button", name="Send").click()
        expect(admin_preview_page.locator("text=Clear me").first).to_be_visible(timeout=5_000)
        expect(
            admin_preview_page.locator("text=This is a preview response for e2e testing.")
        ).to_be_visible(timeout=10_000)

        admin_preview_page.get_by_role("button", name="Clear").click()
        expect(
            admin_preview_page.locator(
                "text=Send a message to preview your AI agent's response."
            )
        ).to_be_visible()
        expect(admin_preview_page.locator("text=Clear me")).to_have_count(0)


# ===========================================================================
# Tier gating
# ===========================================================================


class TestPreviewTier:
    """Starter tier gates the entire preview surface."""

    def test_starter_tier_shows_upgrade_without_chat_ui(
        self, page: Page, admin_vite_server, api_mocker_starter: AdminApiMocker
    ) -> None:
        setup_admin_page(page, api_mocker_starter)
        api_mocker_starter.clear_calls()

        page.locator("nav >> text=Conversation preview").first.click()
        page.wait_for_timeout(800)

        expect(page.get_by_role("heading", name="Conversation Preview")).to_be_visible()
        expect(page.locator("text=Professional tier required")).to_be_visible()
        expect(page.get_by_role("button", name="Send")).to_have_count(0)
        expect(
            page.get_by_placeholder("Type a customer message to preview...")
        ).to_have_count(0)
        assert api_mocker_starter.get_calls(path_contains="/preview/chat") == []


# ===========================================================================
# Error states
# ===========================================================================


class TestPreviewErrors:
    """HTTP error from preview chat surfaces as Preview error alert."""

    def test_rate_limit_shows_preview_error(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/admin/preview/chat",
            {"detail": "Daily preview limit exceeded"},
            status=429,
        )
        setup_admin_page(page, api_mocker)
        page.locator("nav >> text=Conversation preview").first.click()
        page.wait_for_timeout(500)

        page.get_by_placeholder("Type a customer message to preview...").fill(
            "This should fail"
        )
        page.get_by_role("button", name="Send").click()
        expect(page.locator("text=Preview error")).to_be_visible(timeout=5_000)
        expect(page.locator("text=429")).to_be_visible()
