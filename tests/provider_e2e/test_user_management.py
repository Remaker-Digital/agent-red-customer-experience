# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — User Management: create operator, deactivate, backup codes, notif email."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def users_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/users", wait_text="User Management")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestUserManagementLoaded:
    def test_superadmin_sees_operators_and_add(self, users_page: Page) -> None:
        expect(users_page.get_by_text("Platform Superadmin")).to_be_visible()
        expect(users_page.get_by_text("Ops Operator")).to_be_visible()
        expect(users_page.get_by_role("button", name="Add Operator")).to_be_visible()
        expect(users_page.get_by_role("button", name="Generate Backup Codes")).to_be_visible()
        # Conditional menu: Remove only on operator rows
        op_row = users_page.locator("tr", has_text="Ops Operator")
        expect(op_row.get_by_role("button", name="Remove")).to_be_visible()
        sa_row = users_page.locator("tr", has_text="Platform Superadmin")
        expect(sa_row.get_by_role("button", name="Remove")).to_have_count(0)


class TestCreateOperator:
    def test_create_posts_payload_and_shows_key(self, users_page: Page) -> None:
        mocker: ProviderApiMocker = users_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        users_page.get_by_role("button", name="Add Operator").click()
        dialog = users_page.get_by_role("dialog")
        dialog.get_by_label("Email").fill("e2e-ops@remaker.digital")
        dialog.get_by_label("Display Name").fill("E2E Operator")
        dialog.get_by_role("button", name="Create Operator").click()
        users_page.wait_for_timeout(500)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/api/superadmin/platform-admin/users",
        )
        posts = [p for p in posts if p["path"].rstrip("/").endswith("/platform-admin/users")]
        assert len(posts) == 1, f"Expected create POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["email"] == "e2e-ops@remaker.digital"
        assert body["displayName"] == "E2E Operator"
        expect(users_page.get_by_text("Operator Created")).to_be_visible(timeout=5_000)
        expect(users_page.get_by_text("ar_spa_plat_created_e2e_key")).to_be_visible()


class TestDeactivateOperator:
    def test_remove_deletes_operator(self, users_page: Page) -> None:
        mocker: ProviderApiMocker = users_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        users_page.locator("tr", has_text="Ops Operator").get_by_role("button", name="Remove").click()
        dialog = users_page.get_by_role("dialog")
        expect(dialog.get_by_text("Deactivate Operator")).to_be_visible()
        dialog.get_by_role("button", name="Deactivate").click()
        users_page.wait_for_timeout(500)

        deletes = mocker.get_calls(
            method="DELETE",
            path_contains="/platform-admin/users/admin-002",
        )
        assert len(deletes) == 1, f"Expected DELETE, got {deletes}"
        expect(users_page.get_by_text("Operator ops@remaker.digital deactivated")).to_be_visible(timeout=5_000)
        expect(users_page.get_by_text("Ops Operator")).to_have_count(0)


class TestBackupCodesAndNotifEmail:
    def test_generate_backup_codes_posts(self, users_page: Page) -> None:
        mocker: ProviderApiMocker = users_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()
        users_page.get_by_role("button", name="Generate Backup Codes").click()
        users_page.wait_for_timeout(400)
        posts = mocker.get_calls(
            method="POST",
            path_contains="/platform-admin/users/backup-codes",
        )
        assert len(posts) == 1, f"Expected backup-codes POST, got {posts}"
        expect(users_page.get_by_text("Backup Recovery Codes")).to_be_visible(timeout=5_000)
        expect(users_page.get_by_text("AAAA-1111")).to_be_visible()

    def test_notification_email_put(self, users_page: Page) -> None:
        mocker: ProviderApiMocker = users_page._api_mocker  # type: ignore[attr-defined]
        users_page.get_by_role("button", name="Edit").click()
        mocker.clear_calls()
        users_page.get_by_placeholder("notification@example.com").fill("alerts@remaker.digital")
        users_page.get_by_role("button", name="Save").click()
        users_page.wait_for_timeout(400)
        puts = mocker.get_calls(
            method="PUT",
            path_contains="/platform-admin/users/notification-email",
        )
        assert len(puts) == 1, f"Expected notification-email PUT, got {puts}"
        body = json.loads(puts[0]["body"] or "{}")
        assert body["email"] == "alerts@remaker.digital"
        expect(users_page.get_by_text("Notification email updated")).to_be_visible(timeout=5_000)


class TestOperatorRoleHidesCreate:
    def test_operator_only_list_hides_add_operator(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/platform-admin/users",
            [
                {
                    "adminId": "admin-002",
                    "email": "ops@remaker.digital",
                    "displayName": "Ops Operator",
                    "role": "operator",
                    "isActive": True,
                    "createdAt": "2025-11-15T14:00:00Z",
                    "lastLoginAt": "2026-03-10T16:30:00Z",
                    "notificationEmailAddress": None,
                    "backupCodesRemaining": 0,
                    "createdBy": "admin-001",
                },
            ],
        )
        setup_provider_page(page, mocker, "/users")
        page.wait_for_selector("text=User Management", timeout=15_000)
        expect(page.get_by_role("button", name="Add Operator")).to_have_count(0)
        expect(page.get_by_role("button", name="Remove")).to_have_count(0)
