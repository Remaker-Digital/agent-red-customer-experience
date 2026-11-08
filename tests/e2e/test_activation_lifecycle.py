# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Activation lifecycle E2E — Activate / Deactivate / Restore via sidebar UI.

Strong UI→API coverage against Playwright route mocks (AdminApiMocker):
  - button visibility / enabled state by activation-status
  - confirmation dialogs
  - POST method/path/body assertions
  - resulting sidebar status after mutation (where status is re-fetched)

Surfaces under test:
  admin/standalone/layouts/StandaloneLayout.tsx
  admin/shared/ActivationDialog.tsx
  admin/shared/RestoreDialog.tsx

API contracts:
  GET  /api/config/activation-status
  GET  /api/config/draft
  GET  /api/config/draft/preflight
  POST /api/config/draft/activate   body: {}
  POST /api/config/deactivate
  POST /api/config/restore          body: {}
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import AdminApiMocker, setup_admin_page

pytestmark = pytest.mark.e2e

_ACTIVATE = re.compile(r"^Activate$")
_DEACTIVATE = re.compile(r"^Deactivate$")


def _status(
    *,
    is_active: bool = True,
    has_pending_changes: bool = False,
    can_activate: bool = True,
    active_version: int = 3,
    active_activated_at: str | None = "2026-03-01T12:00:00Z",
    is_configured: bool = True,
    draft_version: int | None = 4,
) -> dict:
    return {
        "status": "active" if is_active else "inactive",
        "is_configured": is_configured,
        "is_active": is_active,
        "can_activate": can_activate,
        "has_pending_changes": has_pending_changes,
        "active_version": active_version,
        "active_activated_at": active_activated_at,
        "draft_version": draft_version,
        "draft_saved_at": "2026-03-08T14:00:00Z" if has_pending_changes else None,
        "pending_changes": has_pending_changes,
    }


def _sidebar(page: Page):
    return page.locator(".mantine-AppShell-navbar")


def _lifecycle_button(page: Page, label: str):
    """Primary Activate/Deactivate control in the config group action row.

    Exact match required: Playwright ``has_text="Activate"`` also matches
    ``Deactivate`` (substring).
    """
    pattern = _DEACTIVATE if label == "Deactivate" else _ACTIVATE
    return _sidebar(page).locator("button", has_text=pattern).first


def _open_admin(page: Page, api_mocker: AdminApiMocker, status: dict) -> Page:
    api_mocker.override("/api/config/activation-status", status)
    setup_admin_page(page, api_mocker)
    # Dismiss onboarding if a non-activated fixture trips it
    try:
        close_btn = page.locator(
            '[data-testid="close-wizard"], .mantine-Modal-close'
        ).first
        if close_btn.is_visible(timeout=1500):
            close_btn.click()
            page.wait_for_timeout(200)
    except Exception:
        pass
    return page


# ===========================================================================
# Visibility / enabled state
# ===========================================================================


class TestActivationControlVisibility:
    """Sidebar Activate/Deactivate/Roll back disposition from activation-status."""

    def test_active_no_pending_shows_deactivate(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(is_active=True, has_pending_changes=False))
        btn = _lifecycle_button(page, "Deactivate")
        expect(btn).to_be_visible()
        expect(btn).to_be_enabled()
        assert _sidebar(page).locator("button", has_text=_ACTIVATE).count() == 0

    def test_pending_changes_shows_activate(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(is_active=True, has_pending_changes=True))
        btn = _lifecycle_button(page, "Activate")
        expect(btn).to_be_visible()
        expect(btn).to_be_enabled()
        assert _sidebar(page).locator("button", has_text=_DEACTIVATE).count() == 0

    def test_inactive_configured_shows_activate(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(
            page,
            api_mocker,
            _status(
                is_active=False,
                has_pending_changes=False,
                active_version=2,
                active_activated_at="2026-02-01T00:00:00Z",
            ),
        )
        expect(_lifecycle_button(page, "Activate")).to_be_visible()

    def test_rollback_enabled_when_version_ge_2(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(active_version=3))
        rollback = _sidebar(page).locator("button", has_text="Roll back")
        expect(rollback).to_be_visible()
        expect(rollback).to_be_enabled()

    def test_rollback_disabled_when_version_lt_2(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(active_version=1))
        rollback = _sidebar(page).locator("button", has_text="Roll back")
        expect(rollback).to_be_visible()
        expect(rollback).to_be_disabled()

    def test_discard_disabled_without_pending_changes(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(has_pending_changes=False))
        discard = _sidebar(page).locator("button", has_text="Discard")
        expect(discard).to_be_visible()
        expect(discard).to_be_disabled()

    def test_discard_enabled_with_pending_changes(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(has_pending_changes=True))
        discard = _sidebar(page).locator("button", has_text="Discard")
        expect(discard).to_be_enabled()


# ===========================================================================
# Deactivate
# ===========================================================================


class TestDeactivateFlow:
    """Deactivate confirmation → POST /api/config/deactivate."""

    def test_deactivate_cancel_does_not_post(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(is_active=True, has_pending_changes=False))
        api_mocker.clear_calls()
        _lifecycle_button(page, "Deactivate").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("Deactivate Configuration")).to_be_visible()
        dialog.get_by_role("button", name="Cancel").click()
        expect(dialog).to_be_hidden()
        assert api_mocker.get_calls(method="POST", path_contains="/deactivate") == []

    def test_deactivate_confirm_posts_and_shows_activate(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/config/activation-status",
            _status(is_active=True, has_pending_changes=False),
        )
        setup_admin_page(page, api_mocker)
        api_mocker.clear_calls()

        _lifecycle_button(page, "Deactivate").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text("immediately stop the chat widget")).to_be_visible()

        # Subsequent activation-status GETs after success should show inactive
        api_mocker.override(
            "/api/config/activation-status",
            _status(is_active=False, has_pending_changes=False),
        )

        dialog.get_by_role("button", name="Deactivate").click()
        page.wait_for_timeout(800)

        posts = api_mocker.get_calls(method="POST", path_contains="/api/config/deactivate")
        assert len(posts) == 1, f"Expected one deactivate POST, got: {api_mocker.api_calls}"
        assert api_mocker.get_calls(method="POST", path_contains="draft/activate") == []

        expect(_lifecycle_button(page, "Activate")).to_be_visible()


# ===========================================================================
# Activate
# ===========================================================================


class TestActivateFlow:
    """ActivationDialog → preflight → confirm → POST /api/config/draft/activate."""

    def test_activate_blocked_when_preflight_fails(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/config/activation-status",
            _status(is_active=False, has_pending_changes=True, can_activate=False),
        )
        api_mocker.override(
            "/api/config/draft/preflight",
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
            },
        )
        api_mocker.override(
            "/api/config/draft",
            {
                "has_pending_changes": True,
                "active_version": 1,
                "active_activated_at": "2026-02-01T00:00:00Z",
                "draft_version": 2,
                "changed_fields": ["brand_name"],
                "draft_config": {"brand_name": ""},
                "active_config": {"brand_name": "Old"},
            },
        )
        setup_admin_page(page, api_mocker)
        api_mocker.clear_calls()

        _lifecycle_button(page, "Activate").click()
        page.wait_for_timeout(500)

        expect(page.get_by_text("Activate configuration")).to_be_visible()
        expect(page.get_by_text("Required before activation")).to_be_visible()
        expect(page.get_by_text("Brand name is required")).to_be_visible()
        # Confirm CTA must not appear when hard-blocked
        assert page.locator("button", has_text="Activate now").count() == 0
        assert page.locator("button", has_text="Yes, activate").count() == 0

        preflight = api_mocker.get_calls(method="GET", path_contains="draft/preflight")
        assert len(preflight) >= 1
        assert api_mocker.get_calls(method="POST", path_contains="draft/activate") == []

    def test_activate_confirm_posts_empty_json_body(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/config/activation-status",
            _status(is_active=True, has_pending_changes=True, can_activate=True),
        )
        api_mocker.override(
            "/api/config/draft/preflight",
            {"can_activate": True, "hard_errors": [], "warnings": []},
        )
        api_mocker.override(
            "/api/config/draft",
            {
                "has_pending_changes": True,
                "active_version": 3,
                "active_activated_at": "2026-03-01T12:00:00Z",
                "draft_version": 5,
                "changed_fields": ["brand_name", "brand_voice"],
                "draft_config": {"brand_name": "NewCo", "brand_voice": "Warm"},
                "active_config": {"brand_name": "OldCo", "brand_voice": "Formal"},
            },
        )
        api_mocker.override(
            "/api/config/draft/activate",
            {
                "success": True,
                "version": 5,
                "activated_at": "2026-03-15T12:00:00Z",
                "errors": [],
                "warnings": [],
            },
        )
        setup_admin_page(page, api_mocker)
        api_mocker.clear_calls()

        _lifecycle_button(page, "Activate").click()
        page.wait_for_timeout(500)

        expect(page.get_by_text("Activate configuration")).to_be_visible()
        expect(page.get_by_text("Changes to activate")).to_be_visible()
        expect(page.get_by_text("2 fields changed")).to_be_visible()

        page.locator("button", has_text="Activate now").click()
        expect(page.get_by_text("Are you sure you want to activate")).to_be_visible()

        # After success, status refresh should show active / no pending → Deactivate
        api_mocker.override(
            "/api/config/activation-status",
            _status(is_active=True, has_pending_changes=False, active_version=5),
        )

        page.locator("button", has_text="Yes, activate").click()
        page.wait_for_timeout(800)

        posts = api_mocker.get_calls(method="POST", path_contains="/api/config/draft/activate")
        assert len(posts) == 1, f"Expected activate POST, got: {api_mocker.api_calls}"
        body = posts[0].get("body")
        assert body is not None
        parsed = json.loads(body) if isinstance(body, str) else body
        assert parsed == {}, f"Activate body must be empty object, got: {parsed!r}"

        preflight = api_mocker.get_calls(method="GET", path_contains="draft/preflight")
        draft_gets = api_mocker.get_calls(method="GET", path_contains="/api/config/draft")
        assert len(preflight) >= 1
        assert any("/draft" in c["url"] and "preflight" not in c["url"] for c in draft_gets)

        expect(_lifecycle_button(page, "Deactivate")).to_be_visible()


# ===========================================================================
# Restore (Roll back)
# ===========================================================================


class TestRestoreFlow:
    """RestoreDialog → POST /api/config/restore."""

    def test_restore_cancel_does_not_post(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(active_version=3))
        api_mocker.clear_calls()
        _sidebar(page).locator("button", has_text="Roll back").click()
        expect(page.get_by_text("Restore previous configuration")).to_be_visible()
        page.locator("button", has_text="Cancel").last.click()
        page.wait_for_timeout(300)
        assert api_mocker.get_calls(method="POST", path_contains="/api/config/restore") == []

    def test_restore_confirm_posts_and_shows_version(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        _open_admin(page, api_mocker, _status(active_version=3))
        api_mocker.clear_calls()

        _sidebar(page).locator("button", has_text="Roll back").click()
        expect(page.get_by_text("Restore previous configuration")).to_be_visible()
        expect(page.get_by_text("Current active: v3")).to_be_visible()

        page.locator("button", has_text="Restore now").click()
        page.wait_for_timeout(800)

        posts = api_mocker.get_calls(method="POST", path_contains="/api/config/restore")
        assert len(posts) == 1, f"Expected restore POST, got: {api_mocker.api_calls}"
        body = posts[0].get("body")
        assert body is not None
        parsed = json.loads(body) if isinstance(body, str) else body
        assert parsed == {}, f"Restore body must be empty object, got: {parsed!r}"
