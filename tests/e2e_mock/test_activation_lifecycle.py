# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Activation lifecycle mock E2E — Activate / Deactivate / Restore via sidebar UI.

UI-driven mutations against Vite ``dev:mock`` with request spies (route continue).
Asserts:
  - button visibility / enabled state
  - confirmation dialogs
  - POST method/path/body
  - mock-store status after mutation (GET activation-status)

Surfaces: StandaloneLayout + ActivationDialog + RestoreDialog
"""

from __future__ import annotations

import json
import re

from playwright.sync_api import Page, expect

from tests.e2e_mock.conftest import (
    api_origin,
    dismiss_onboarding_if_present,
    get_api_json,
    navigate_and_settle,
)

_ACTIVATE = re.compile(r"^Activate$")
_DEACTIVATE = re.compile(r"^Deactivate$")


def _sidebar(page: Page):
    return page.locator(".mantine-AppShell-navbar")


def _lifecycle_button(page: Page, label: str):
    """Exact label match — ``has_text='Activate'`` also matches Deactivate."""
    pattern = _DEACTIVATE if label == "Deactivate" else _ACTIVATE
    return _sidebar(page).locator("button", has_text=pattern).first


def _install_api_spy(page: Page) -> list[dict]:
    """Record POST/PUT bodies for /api/config/* while continuing to Vite mocks."""
    captured: list[dict] = []

    def handle(route):
        req = route.request
        if req.method in ("POST", "PUT", "PATCH"):
            captured.append(
                {
                    "url": req.url,
                    "method": req.method,
                    "body": req.post_data,
                }
            )
        route.continue_()

    page.route("**/api/config/**", handle)
    return captured


def _spy_calls(
    captured: list[dict], *, method: str | None = None, path_contains: str | None = None
) -> list[dict]:
    result = captured
    if method:
        result = [c for c in result if c["method"] == method]
    if path_contains:
        result = [c for c in result if path_contains in c["url"]]
    return result


def _open_dashboard(page: Page, mock_base_url: str) -> None:
    navigate_and_settle(page, "/", mock_base_url)
    dismiss_onboarding_if_present(page)


def _seed_pending_via_put(page: Page, mock_base_url: str) -> None:
    """Mutate mock store so has_pending_changes becomes true (PUT /api/config)."""
    origin = api_origin(mock_base_url)
    resp = page.request.put(
        f"{origin}/api/config",
        data=json.dumps({"fields": {"brand_name": "Lifecycle Seed Co"}}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200, f"Seed PUT failed: {resp.status} {resp.text()}"


# ===========================================================================
# Visibility (default fixture + seeded pending)
# ===========================================================================


class TestActivationControlVisibility:
    """Sidebar disposition against Vite mock fixture / seeded state."""

    def test_default_fixture_shows_deactivate(self, page: Page, mock_base_url: str) -> None:
        _open_dashboard(page, mock_base_url)
        expect(_lifecycle_button(page, "Deactivate")).to_be_visible()
        expect(_lifecycle_button(page, "Deactivate")).to_be_enabled()
        assert _sidebar(page).locator("button", has_text=_ACTIVATE).count() == 0

    def test_default_rollback_enabled(self, page: Page, mock_base_url: str) -> None:
        _open_dashboard(page, mock_base_url)
        rollback = _sidebar(page).locator("button", has_text="Roll back")
        expect(rollback).to_be_visible()
        expect(rollback).to_be_enabled()

    def test_default_discard_disabled(self, page: Page, mock_base_url: str) -> None:
        _open_dashboard(page, mock_base_url)
        discard = _sidebar(page).locator("button", has_text="Discard")
        expect(discard).to_be_disabled()

    def test_pending_seed_shows_activate(self, page: Page, mock_base_url: str) -> None:
        _seed_pending_via_put(page, mock_base_url)
        status = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert status["has_pending_changes"] is True
        _open_dashboard(page, mock_base_url)
        expect(_lifecycle_button(page, "Activate")).to_be_visible()
        expect(_sidebar(page).locator("button", has_text="Discard")).to_be_enabled()


# ===========================================================================
# Deactivate (UI → mock store)
# ===========================================================================


class TestDeactivateFlow:
    def test_deactivate_cancel_no_post(self, page: Page, mock_base_url: str) -> None:
        captured = _install_api_spy(page)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _lifecycle_button(page, "Deactivate").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text("Deactivate Configuration")).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()
        assert _spy_calls(captured, method="POST", path_contains="/deactivate") == []

        status = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert status["is_active"] is True

    def test_deactivate_confirm_posts_and_mutates_store(
        self, page: Page, mock_base_url: str
    ) -> None:
        captured = _install_api_spy(page)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _lifecycle_button(page, "Deactivate").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text("immediately stop the chat widget")).to_be_visible()
        dialog.get_by_role("button", name="Deactivate").click()
        page.wait_for_timeout(800)

        posts = _spy_calls(captured, method="POST", path_contains="/api/config/deactivate")
        assert len(posts) == 1, f"Expected deactivate POST, got: {captured}"

        status = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert status["is_active"] is False

        expect(_lifecycle_button(page, "Activate")).to_be_visible()


# ===========================================================================
# Activate (UI → mock store)
# ===========================================================================


class TestActivateFlow:
    def test_activate_blocked_when_can_activate_false(
        self, page: Page, mock_base_url: str
    ) -> None:
        """Route-intercept preflight hard-block; Activate now must not appear."""
        _seed_pending_via_put(page, mock_base_url)
        captured = _install_api_spy(page)

        # Register AFTER spy so Playwright matches this handler first (LIFO).
        def block_preflight(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                headers={"X-Mock": "true"},
                body=json.dumps(
                    {
                        "can_activate": False,
                        "hard_errors": [
                            {
                                "field": "brand_name",
                                "message": "Brand name is required",
                                "page": "agent-configuration",
                            }
                        ],
                        "warnings": [],
                    }
                ),
            )

        page.route("**/api/config/draft/preflight*", block_preflight)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _lifecycle_button(page, "Activate").click()
        page.wait_for_timeout(500)
        expect(page.get_by_text("Required before activation")).to_be_visible()
        expect(page.get_by_text("Brand name is required")).to_be_visible()
        assert page.locator("button", has_text="Activate now").count() == 0
        assert _spy_calls(captured, method="POST", path_contains="draft/activate") == []

    def test_activate_confirm_posts_and_clears_pending(
        self, page: Page, mock_base_url: str
    ) -> None:
        _seed_pending_via_put(page, mock_base_url)
        before = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert before["has_pending_changes"] is True
        draft_version = before["draft_version"]

        captured = _install_api_spy(page)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _lifecycle_button(page, "Activate").click()
        page.wait_for_timeout(500)
        expect(page.get_by_text("Activate configuration")).to_be_visible()
        expect(page.get_by_text("Changes to activate")).to_be_visible()

        page.locator("button", has_text="Activate now").click()
        expect(page.get_by_text("Are you sure you want to activate")).to_be_visible()
        page.locator("button", has_text="Yes, activate").click()
        page.wait_for_timeout(800)

        posts = _spy_calls(captured, method="POST", path_contains="/api/config/draft/activate")
        assert len(posts) == 1, f"Expected activate POST, got: {captured}"
        body = posts[0].get("body")
        assert body is not None
        parsed = json.loads(body) if isinstance(body, str) else body
        assert parsed == {}

        after = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert after["has_pending_changes"] is False
        assert after["is_active"] is True
        assert after["active_version"] == draft_version

        expect(_lifecycle_button(page, "Deactivate")).to_be_visible()


# ===========================================================================
# Restore (UI → mock store)
# ===========================================================================


class TestRestoreFlow:
    def test_restore_cancel_no_post(self, page: Page, mock_base_url: str) -> None:
        captured = _install_api_spy(page)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _sidebar(page).locator("button", has_text="Roll back").click()
        expect(page.get_by_text("Restore previous configuration")).to_be_visible()
        page.locator("button", has_text="Cancel").last.click()
        page.wait_for_timeout(300)
        assert _spy_calls(captured, method="POST", path_contains="/api/config/restore") == []

    def test_restore_confirm_posts_and_decrements_version(
        self, page: Page, mock_base_url: str
    ) -> None:
        before = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert before["active_version"] >= 2

        captured = _install_api_spy(page)
        _open_dashboard(page, mock_base_url)
        captured.clear()

        _sidebar(page).locator("button", has_text="Roll back").click()
        expect(page.get_by_text(f"Current active: v{before['active_version']}")).to_be_visible()
        page.locator("button", has_text="Restore now").click()
        page.wait_for_timeout(800)

        posts = _spy_calls(captured, method="POST", path_contains="/api/config/restore")
        assert len(posts) == 1, f"Expected restore POST, got: {captured}"
        body = posts[0].get("body")
        assert body is not None
        parsed = json.loads(body) if isinstance(body, str) else body
        assert parsed == {}

        after = get_api_json(page, mock_base_url, "/api/config/activation-status")
        assert after["active_version"] == before["active_version"] - 1
        assert after["is_active"] is True
