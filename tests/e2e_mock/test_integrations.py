# (C) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Mock E2E tests for the Integrations page (/integrations).

Vite mock serves real integration fixtures (Shopify, Stripe, Zendesk, …).
Strong asserts cover card content, activate → API, and starter-tier gates.
"""
import json

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_mock.conftest import (
    navigate_and_settle,
    dismiss_onboarding_if_present,
    main_text,
    assert_mock_active,
)

INTEGRATIONS_PATH = "/integrations"

STARTER_GATED = [
    {
        "type": "zendesk",
        "name": "Zendesk",
        "description": "Route escalated conversations to Zendesk tickets.",
        "icon": "🎫",
        "enabled": False,
        "status": "disconnected",
        "tierGate": "professional",
        "tierMet": False,
        "comingSoon": False,
    },
]


def _fulfill_json(route, body, status=200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(body),
        headers={"x-mock": "true"},
    )


# ---------------------------------------------------------------------------
# TestPageStructure
# ---------------------------------------------------------------------------

class TestPageStructure:
    """Verify the integrations page structure against mock fixtures."""

    @pytest.fixture(autouse=True)
    def _navigate(self, shared_page: Page, mock_base_url: str):
        navigate_and_settle(shared_page, INTEGRATIONS_PATH, mock_base_url)
        dismiss_onboarding_if_present(shared_page)

    def test_page_title_or_heading(self, shared_page: Page):
        expect(shared_page.get_by_role("heading", name="Integrations")).to_be_visible()

    def test_fixture_integration_cards(self, shared_page: Page):
        """Mock fixture integrations render as named cards."""
        text = main_text(shared_page)
        for name in ("Shopify", "Stripe", "Zendesk", "Mailchimp"):
            assert name in text, f"Expected integration card '{name}' in page"

    def test_active_summary_footer(self, shared_page: Page):
        """Footer reports how many integrations are active (Shopify enabled)."""
        text = main_text(shared_page)
        assert "1 of 5 integrations active" in text.lower() or (
            "1 of 5" in text and "integrations active" in text.lower()
        ), f"Expected active summary footer, got: {text[-200:]}"

    def test_main_content_visible(self, shared_page: Page):
        main = shared_page.locator("main, [role='main'], .mantine-AppShell-main").first
        expect(main).to_be_visible(timeout=5000)


# ---------------------------------------------------------------------------
# TestTierGate — must fail if gate UI is absent for starter + tierMet=false
# ---------------------------------------------------------------------------

class TestTierGate:
    """Starter-tier override must surface upgrade prompts."""

    def test_starter_upgrade_prompt_required(
        self, page: Page, mock_base_url: str
    ):
        starter_tenant = {
            "valid": True,
            "tenant_id": "mock-tenant-001",
            "tier": "starter",
            "status": "active",
            "billing_channel": "stripe",
            "has_stripe_billing": True,
            "shopify_shop_domain": None,
            "brand_name": "Mock Store",
        }

        def handle_integrations(route):
            from urllib.parse import urlparse

            path = urlparse(route.request.url).path.rstrip("/")
            if route.request.method == "GET" and path == "/api/admin/integrations":
                _fulfill_json(route, STARTER_GATED)
            else:
                route.fallback()

        def handle_validate(route):
            if "validate-key" in route.request.url:
                _fulfill_json(route, starter_tenant)
            else:
                route.fallback()

        page.route("**/api/admin/integrations*", handle_integrations)
        page.route("**/api/tenants/auth/validate-key*", handle_validate)
        navigate_and_settle(page, INTEGRATIONS_PATH, mock_base_url)
        dismiss_onboarding_if_present(page)

        expect(page.get_by_text("Zendesk").first).to_be_visible()
        expect(
            page.get_by_text("Upgrade to professional to use this integration")
        ).to_be_visible()
        expect(
            page.get_by_text("Some integrations require Professional tier or above")
        ).to_be_visible()
        # Must NOT soft-pass: gated badge text is required
        expect(page.get_by_text("⬆ professional tier")).to_be_visible()


# ---------------------------------------------------------------------------
# TestActivateUiToApi
# ---------------------------------------------------------------------------

class TestActivateUiToApi:
    """Activate control posts to the mock integrations API."""

    def test_activate_stripe_posts(
        self, page: Page, mock_base_url: str
    ):
        navigate_and_settle(page, INTEGRATIONS_PATH, mock_base_url)
        dismiss_onboarding_if_present(page)

        posted: list[str] = []
        page.on(
            "request",
            lambda r: posted.append(r.url)
            if r.method == "POST" and "/integrations/" in r.url and "/activate" in r.url
            else None,
        )

        # Shopify is connected — first primary Activate control is Stripe.
        activate = page.locator("main button.ar-btn-action").filter(
            has_text="Activate"
        ).first
        expect(activate).to_be_visible()
        activate.click()
        page.wait_for_timeout(1500)
        assert any(
            "/integrations/stripe/activate" in u for u in posted
        ), f"Expected Stripe activate POST, saw: {posted}"


# ---------------------------------------------------------------------------
# TestLoadingState
# ---------------------------------------------------------------------------

class TestLoadingState:
    """Verify loading and degradation behavior."""

    def test_page_loads_interactive_content(
        self, page: Page, mock_base_url: str
    ):
        navigate_and_settle(page, INTEGRATIONS_PATH, mock_base_url)
        dismiss_onboarding_if_present(page)
        expect(page.get_by_role("heading", name="Integrations")).to_be_visible()
        clickables = page.locator("button, a, [role='button']")
        assert clickables.count() >= 1

    def test_error_state_on_list_failure(
        self, page: Page, mock_base_url: str
    ):
        def fail(route):
            from urllib.parse import urlparse

            path = urlparse(route.request.url).path.rstrip("/")
            if path == "/api/admin/integrations":
                _fulfill_json(route, {"error": "boom"}, status=500)
            else:
                route.fallback()

        page.route("**/api/admin/integrations*", fail)
        navigate_and_settle(page, INTEGRATIONS_PATH, mock_base_url)
        dismiss_onboarding_if_present(page)
        expect(page.get_by_text("Failed to load integrations:")).to_be_visible()

    def test_mock_api_active(self, page: Page, mock_base_url: str):
        assert_mock_active(page, mock_base_url)
