# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Auth / 2FA / role-gating against the Vite mock backend (SPEC-1706).

Exercises real ApiKeyLogin + TwoFaChallenge UI paths and role-based nav /
ProtectedRoute denial without using sessionStorage injection as the only gate.

ADO: picked up by PlaywrightMock (`tests/e2e_mock`) once PR #173 lands.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from tests.e2e_mock.conftest import (
    MOCK_API_KEY,
    MOCK_TENANT,
    api_origin,
    dismiss_onboarding_if_present,
    fill_mantine_pin,
    login_via_api_key_ui,
    navigate_to,
    open_unauthenticated_login,
    set_whoami_role,
)

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


class TestMockApiKeyLogin:
    def test_invalid_key_shows_error(self, unauthenticated_page: Page, mock_base_url: str):
        page = unauthenticated_page
        open_unauthenticated_login(page, mock_base_url)

        expect(page.get_by_role("button", name="Send sign-in link")).to_be_visible(timeout=15_000)
        login_via_api_key_ui(page, api_key="ar_invalid_key")

        expect(page.get_by_text("Invalid API key")).to_be_visible(timeout=10_000)
        assert page.locator(".mantine-AppShell-navbar").count() == 0
        assert page.evaluate("sessionStorage.getItem('agentred_api_key')") in (None, "")

    def test_valid_key_login_hits_validate_key_and_reaches_dashboard(
        self, unauthenticated_page: Page, mock_base_url: str
    ):
        page = unauthenticated_page
        open_unauthenticated_login(page, mock_base_url)

        validate_posts: list[dict] = []

        def _on_request(request):
            if request.method == "POST" and "validate-key" in request.url:
                validate_posts.append(
                    {
                        "url": request.url,
                        "api_key": request.headers.get("x-api-key"),
                    }
                )

        page.on("request", _on_request)
        login_via_api_key_ui(page, api_key=MOCK_API_KEY)

        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)

        expect(_sidebar(page).get_by_text("Dashboard", exact=True)).to_be_visible()
        assert page.get_by_role("button", name="Sign in with API key").count() == 0
        assert page.evaluate("sessionStorage.getItem('agentred_api_key')") == MOCK_API_KEY

        assert len(validate_posts) >= 1, "Login must POST /api/tenants/auth/validate-key"
        assert any(MOCK_TENANT in (p["url"] or "") for p in validate_posts)
        assert any(p["api_key"] == MOCK_API_KEY for p in validate_posts)


class TestMockTwoFaChallenge:
    def test_magic_link_2fa_challenge_success(
        self, unauthenticated_page: Page, mock_base_url: str
    ):
        page = unauthenticated_page
        totp_posts: list[str] = []

        def _on_request(request):
            if request.method == "POST" and "2fa/totp/verify" in request.url:
                totp_posts.append(request.post_data or "")

        page.on("request", _on_request)
        page.goto(
            f"{mock_base_url}/verify-magic-link?token=requires-2fa&tenant={MOCK_TENANT}"
        )
        page.wait_for_load_state("networkidle")

        expect(page.get_by_role("heading", name="Two-factor authentication")).to_be_visible(
            timeout=15_000
        )

        # Invalid code first — aria-label is "Verify code"
        fill_mantine_pin(page, "000000")
        page.get_by_role("button", name="Verify code").click()
        expect(page.get_by_text(re.compile(r"Invalid code", re.I))).to_be_visible(timeout=10_000)
        assert len(totp_posts) >= 1

        # Clear pin inputs then enter valid TOTP (onComplete may auto-submit)
        pin_inputs = page.locator(".mantine-PinInput-input")
        for i in range(min(pin_inputs.count(), 6)):
            pin_inputs.nth(i).fill("")
        fill_mantine_pin(page, "123456")
        verify = page.get_by_role("button", name="Verify code")
        if verify.count() and verify.first.is_visible():
            verify.first.click()

        page.wait_for_selector("text=Dashboard", timeout=20_000)
        dismiss_onboarding_if_present(page)
        expect(_sidebar(page).get_by_text("Dashboard", exact=True)).to_be_visible()

        assert any("123456" in (p or "") for p in totp_posts), totp_posts
        session = page.evaluate("sessionStorage.getItem('agentred_session_token')")
        assert session == "mock-session-token-after-2fa"


class TestMockRoleGating:
    def _login_then_set_role(
        self, page: Page, mock_base_url: str, role: str, email: str
    ) -> None:
        """Real UI login, then mutate whoami and reload so role gates apply."""
        open_unauthenticated_login(page, mock_base_url)
        login_via_api_key_ui(page, api_key=MOCK_API_KEY)
        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)

        set_whoami_role(page, mock_base_url, role=role, email=email)
        # Reload so layout re-fetches whoami with the new role (auth already in sessionStorage
        # from the UI login above — not init-script injection).
        page.reload(wait_until="networkidle")
        page.wait_for_selector("text=Dashboard", timeout=15_000)
        dismiss_onboarding_if_present(page)

    def test_viewer_nav_and_denied_routes(
        self, unauthenticated_page: Page, mock_base_url: str
    ):
        page = unauthenticated_page
        self._login_then_set_role(
            page, mock_base_url, role="viewer", email="viewer@mockstore.com"
        )

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

        navigate_to(page, "/configuration", mock_base_url)
        page.wait_for_timeout(500)
        expect(page).to_have_url(re.compile(r"/inbox"), timeout=10_000)

        navigate_to(page, "/billing", mock_base_url)
        expect(page).to_have_url(re.compile(r"/inbox"), timeout=10_000)

    def test_admin_can_open_configuration(
        self, unauthenticated_page: Page, mock_base_url: str
    ):
        page = unauthenticated_page
        self._login_then_set_role(
            page, mock_base_url, role="admin", email="admin@mockstore.com"
        )

        sidebar = _sidebar(page)
        expect(sidebar.get_by_text("AI configuration", exact=True)).to_be_visible()
        expect(sidebar.get_by_text("Account & billing", exact=True)).to_be_visible()

        sidebar.get_by_text("AI configuration", exact=True).click()
        page.wait_for_timeout(500)
        expect(page).to_have_url(re.compile(r"/configuration"), timeout=10_000)
        assert "/inbox" not in page.url

    def test_whoami_endpoint_reflects_set_role(
        self, unauthenticated_page: Page, mock_base_url: str
    ):
        """Contract check: set-whoami mutates the mock store used by the SPA."""
        page = unauthenticated_page
        origin = api_origin(mock_base_url)
        before = page.request.get(f"{origin}/api/admin/team/whoami").json()
        assert before["role"] == "superadmin"

        set_whoami_role(page, mock_base_url, role="viewer", email="viewer@mockstore.com")
        after = page.request.get(f"{origin}/api/admin/team/whoami").json()
        assert after["role"] == "viewer"
        assert after["email"] == "viewer@mockstore.com"
