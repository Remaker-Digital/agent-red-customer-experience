# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shopify Settings — GDPR export UI→API + failure toast."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import (
    MOCK_SESSION_TOKEN,
    ShopifyApiRecorder,
    navigate_shopify,
    setup_shopify_page,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


class TestGdprExportSuccess:
    def test_export_posts_and_toasts(self, shopify_page: Page) -> None:
        navigate_shopify(shopify_page, "/settings", wait_text="Settings")
        expect(shopify_page.get_by_text("Data & Privacy")).to_be_visible()

        recorder: ShopifyApiRecorder = shopify_page._api_recorder  # type: ignore[attr-defined]
        recorder.clear_calls()

        shopify_page.get_by_role("button", name="Export My Data").click()
        expect(shopify_page.get_by_text("Data export initiated. You will receive a download link.")).to_be_visible(
            timeout=10_000
        )

        posts = recorder.get_calls(method="POST", path_contains="/api/admin/gdpr/export")
        assert len(posts) >= 1, "Export button must POST /api/admin/gdpr/export"
        auth = posts[0]["headers"].get("authorization", "")
        assert auth == f"Bearer {MOCK_SESSION_TOKEN}"


class TestGdprExportFailure:
    def test_export_failure_toast(self, page: Page, shopify_vite_server, api_recorder: ShopifyApiRecorder) -> None:
        api_recorder.override(
            "/api/admin/gdpr/export",
            {"detail": "export unavailable"},
            status=500,
        )
        setup_shopify_page(page, api_recorder, "/settings", wait_text="Settings")
        page.get_by_role("button", name="Export My Data").click()
        expect(page.get_by_text("Data export failed. Please try again.")).to_be_visible(timeout=10_000)
        posts = api_recorder.get_calls(method="POST", path_contains="/api/admin/gdpr/export")
        assert len(posts) >= 1
