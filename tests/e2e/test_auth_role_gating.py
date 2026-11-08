# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Auth / 2FA / role-gating Playwright coverage for standalone admin (ADO).

These tests deliberately do **not** rely on sessionStorage injection as the
only gate. They drive ApiKeyLogin and TwoFaChallenge through the UI and
assert UI→API validate-key / MFA calls, then verify ProtectedRoute + nav
visibility by whoami role (viewer vs admin/superadmin).

Depends on PlaywrightUi / PlaywrightMock wiring from PR #173 once merged.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    ADMIN_VITE_PORT,
    MOCK_TENANT_CONTEXT,
    AdminApiMocker,
    admin_standalone_url,
    dismiss_onboarding_if_present,
    fill_pin_input,
    login_with_api_key_ui,
    open_login_page,
)

pytestmark = pytest.mark.usefixtures("admin_vite_server")

TENANT = MOCK_TENANT_CONTEXT["tenant_id"]

# Admin-only nav labels (must be hidden for viewer / escalation_agent).
ADMIN_NAV_LABELS = [
    "Team members",
    "AI configuration",
    "Agents configuration",
    "Knowledge base",
    "Quick actions",
    "Widget configuration",
    "Integrations",
    "Knowledge score",
    "Conversation preview",
    "Memory & privacy",
    "Account & billing",
]


def _sidebar(page: Page):
    return page.locator("nav, .mantine-AppShell-navbar").first


# ---------------------------------------------------------------------------
# Login / validate-key (no sessionStorage bypass)
# ---------------------------------------------------------------------------


class TestApiKeyLoginFlow:
    def test_missing_tenant_shows_invalid_url(self, page: Page, api_mocker: AdminApiMocker):
        page.route("**/api/**", api_mocker.handle_route)
        page.goto(
            f"http://localhost:{ADMIN_VITE_PORT}/admin/standalone/",
            wait_until="networkidle",
        )
        page.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("heading", name="Invalid URL")).to_be_visible(timeout=10_000)
        expect(page.get_by_text("tenant identifier")).to_be_visible()
        # Must not reach authenticated shell
        assert _sidebar(page).locator("text=Dashboard").count() == 0

    def test_invalid_api_key_shows_error_and_stays_on_login(
        self, page: Page, api_mocker: AdminApiMocker
    ):
        open_login_page(page, api_mocker, tenant=TENANT)
        # Magic-link is primary when ?tenant= is present
        expect(page.get_by_role("button", name="Send sign-in link")).to_be_visible(timeout=10_000)
        login_with_api_key_ui(page, api_key="ar_invalid_key")

        expect(page.get_by_text("Invalid API key")).to_be_visible(timeout=10_000)
        # Still on login — no sidebar Dashboard
        assert page.locator(".mantine-AppShell-navbar").count() == 0

        validate_calls = api_mocker.get_calls(method="POST", path_contains="validate-key")
        assert len(validate_calls) >= 1, "UI must POST validate-key for failed login"
        assert "tenant=" in validate_calls[-1]["url"]
        assert validate_calls[-1]["headers"].get("x-api-key") == "ar_invalid_key"

    def test_valid_api_key_login_reaches_dashboard_via_validate_key(
        self, page: Page, api_mocker: AdminApiMocker
    ):
        open_login_page(page, api_mocker, tenant=TENANT)
        login_with_api_key_ui(page, api_key="ar_test_key_for_e2e_testing")

        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)

        # Auth state change: sidebar present; login CTA gone
        expect(_sidebar(page).get_by_text("Dashboard", exact=True)).to_be_visible()
        assert page.get_by_role("button", name="Sign in with API key").count() == 0

        validate_calls = api_mocker.get_calls(method="POST", path_contains="validate-key")
        assert len(validate_calls) >= 1, "Login UI must call validate-key"
        # Login form + layout re-validate both POST validate-key with tenant
        assert any("tenant=" in c["url"] for c in validate_calls)
        assert any(
            c["headers"].get("x-api-key") == "ar_test_key_for_e2e_testing"
            for c in validate_calls
        )

        stored = page.evaluate("sessionStorage.getItem('agentred_api_key')")
        assert stored == "ar_test_key_for_e2e_testing"


# ---------------------------------------------------------------------------
# Two-factor challenge (magic-link verify → MFA)
# ---------------------------------------------------------------------------


class TestTwoFaChallengeFlow:
    def test_magic_link_requiring_2fa_shows_challenge_then_succeeds(
        self, page: Page, api_mocker: AdminApiMocker
    ):
        page.route("**/api/**", api_mocker.handle_route)
        page.goto(
            f"http://localhost:{ADMIN_VITE_PORT}/admin/standalone/verify-magic-link"
            f"?token=requires-2fa&tenant={TENANT}",
            wait_until="networkidle",
        )

        expect(page.get_by_role("heading", name="Two-factor authentication")).to_be_visible(
            timeout=15_000
        )
        expect(page.get_by_text("authenticator app")).to_be_visible()

        # Wrong code → stay on challenge
        fill_pin_input(page, "000000")
        page.get_by_role("button", name="Verify").click()
        expect(page.get_by_text(re.compile(r"Invalid code", re.I))).to_be_visible(timeout=10_000)
        expect(page.get_by_role("heading", name="Two-factor authentication")).to_be_visible()

        # Correct TOTP. PinInput onComplete calls verifyTotp, which sets the
        # Verify button loading until the challenge unmounts. Clicking it
        # waits for a button that will not become actionable, and that wait
        # runs into the test timeout. The completed code is the submit.
        fill_pin_input(page, api_mocker.valid_totp_code)

        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)
        expect(_sidebar(page).get_by_text("Dashboard", exact=True)).to_be_visible()

        totp_calls = api_mocker.get_calls(method="POST", path_contains="2fa/totp/verify")
        assert len(totp_calls) >= 2, "Must POST totp/verify for invalid then valid codes"
        session = page.evaluate("sessionStorage.getItem('agentred_session_token')")
        assert session == api_mocker.session_token_after_2fa

    def test_2fa_cancel_returns_to_sign_in(self, page: Page, api_mocker: AdminApiMocker):
        page.route("**/api/**", api_mocker.handle_route)
        page.goto(
            f"http://localhost:{ADMIN_VITE_PORT}/admin/standalone/verify-magic-link"
            f"?token=requires-2fa&tenant={TENANT}",
            wait_until="networkidle",
        )
        expect(page.get_by_role("heading", name="Two-factor authentication")).to_be_visible(
            timeout=15_000
        )
        page.get_by_text("Cancel and return to sign in").click()
        expect(page.get_by_role("button", name="Send sign-in link")).to_be_visible(timeout=10_000)
        assert page.locator(".mantine-AppShell-navbar").count() == 0


# ---------------------------------------------------------------------------
# Role / ProtectedRoute gating
# ---------------------------------------------------------------------------


class TestRoleGating:
    def _login_as_role(self, page: Page, role: str) -> AdminApiMocker:
        mocker = AdminApiMocker(whoami_role=role)
        if role == "viewer":
            mocker.whoami_email = "viewer@testco.com"
        elif role == "escalation_agent":
            mocker.whoami_email = "agent@testco.com"
        open_login_page(page, mocker, tenant=TENANT)
        login_with_api_key_ui(page)
        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)
        # whoami must have been fetched after validate-key
        whoami = mocker.get_calls(method="GET", path_contains="whoami")
        assert len(whoami) >= 1, "Layout must call /api/admin/team/whoami after login"
        return mocker

    def test_viewer_hides_admin_nav_and_denies_protected_routes(self, page: Page):
        self._login_as_role(page, "viewer")

        sidebar = _sidebar(page)
        expect(sidebar.get_by_text("Dashboard", exact=True)).to_be_visible()
        expect(sidebar.get_by_text("Inbox", exact=True)).to_be_visible()

        # The sidebar shows every item while userRole is still null. count()
        # does not wait for whoami to hide them. to_have_count waits, and a
        # label that stays visible still fails.
        for label in ADMIN_NAV_LABELS:
            expect(
                sidebar.get_by_text(label, exact=True),
                f"Viewer must not see nav item {label!r}",
            ).to_have_count(0)

        # Direct deep-link to admin-only route → ProtectedRoute redirects to /inbox
        page.goto(
            admin_standalone_url("/configuration", tenant=TENANT),
            wait_until="networkidle",
        )
        dismiss_onboarding_if_present(page)
        expect(page).to_have_url(re.compile(r"/inbox"), timeout=10_000)
        # Configuration page chrome must not render
        assert page.get_by_role("heading", name=re.compile(r"configuration", re.I)).count() == 0 or \
            "inbox" in page.url.lower()

        page.goto(
            admin_standalone_url("/billing", tenant=TENANT),
            wait_until="networkidle",
        )
        expect(page).to_have_url(re.compile(r"/inbox"), timeout=10_000)

    def test_escalation_agent_denied_admin_routes(self, page: Page):
        self._login_as_role(page, "escalation_agent")
        sidebar = _sidebar(page)
        expect(sidebar.get_by_text("Inbox", exact=True)).to_be_visible()
        for label in ("AI configuration", "Account & billing", "Team members"):
            expect(
                sidebar.get_by_text(label, exact=True),
                f"Escalation agent must not see nav item {label!r}",
            ).to_have_count(0)

        page.goto(
            admin_standalone_url("/agents", tenant=TENANT),
            wait_until="networkidle",
        )
        expect(page).to_have_url(re.compile(r"/inbox"), timeout=10_000)

    def test_admin_sees_protected_nav_and_can_open_configuration(self, page: Page):
        self._login_as_role(page, "admin")
        sidebar = _sidebar(page)

        expect(sidebar.get_by_text("Team members", exact=True)).to_be_visible()
        expect(sidebar.get_by_text("AI configuration", exact=True)).to_be_visible()
        expect(sidebar.get_by_text("Account & billing", exact=True)).to_be_visible()

        sidebar.get_by_text("AI configuration", exact=True).click()
        page.wait_for_timeout(500)
        expect(page).to_have_url(re.compile(r"/configuration"), timeout=10_000)
        # Must not bounce to inbox
        assert "/inbox" not in page.url

    def test_superadmin_sees_admin_nav(self, page: Page):
        self._login_as_role(page, "superadmin")
        sidebar = _sidebar(page)
        expect(sidebar.get_by_text("AI configuration", exact=True)).to_be_visible()
        expect(sidebar.get_by_text("Knowledge base", exact=True)).to_be_visible()
