# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shopify Inbox — route load + conversations UI→API."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import MOCK_SESSION_TOKEN, ShopifyApiRecorder, navigate_shopify

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestShopifyInbox:
    def test_inbox_loads_and_fetches_conversations(self, shopify_page: Page) -> None:
        recorder: ShopifyApiRecorder = shopify_page._api_recorder  # type: ignore[attr-defined]
        recorder.clear_calls()

        navigate_shopify(shopify_page, "/inbox", wait_text="Conversation Inbox")
        expect(shopify_page.get_by_role("heading", name="Conversation Inbox").first).to_be_visible()

        # Wait for inbox fetch
        shopify_page.wait_for_timeout(800)
        calls = recorder.get_calls(path_contains="/api/admin/conversations")
        assert len(calls) >= 1, "Inbox must call /api/admin/conversations"
        get_or_search = [c for c in calls if c["method"] in ("GET", "POST") and "/api/admin/conversations" in c["path"]]
        assert get_or_search, "Expected GET or search POST for conversations"
        auth = get_or_search[0]["headers"].get("authorization", "")
        assert auth == f"Bearer {MOCK_SESSION_TOKEN}"
