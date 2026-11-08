# (C) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""SPEC-1706 Billing / Account E2E tests against mock Vite dev server.

Tests cover: billing status display, contact preferences, email change
request/confirm flow, message packs, tier display, and API contracts.

Fixture data (billing.ts):
  plan: professional, status: active, includedConversations: 500,
  usedConversations: 342, overageRate: 0.15
  contactPreferences: email=admin@mockstore.com, recoveryEmail=null
"""

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e_mock.conftest import (
    api_origin,
    dismiss_onboarding_if_present,
    get_api_json,
    main_text,
    navigate_and_settle,
)


BILLING_PATH = "/billing"


def _go_billing(pg: Page, base_url: str) -> None:
    navigate_and_settle(pg, BILLING_PATH, base_url)
    dismiss_onboarding_if_present(pg)


class TestBillingStatus:
    """Verify the billing status section displays plan and usage data."""

    @pytest.fixture(autouse=True)
    def _navigate(self, shared_page: Page, mock_base_url: str):
        _go_billing(shared_page, mock_base_url)
        self.pg = shared_page

    def test_billing_page_loads(self):
        """Billing page renders with heading."""
        expect(self.pg.get_by_role("heading", name="Account and billing")).to_be_visible()

    def test_plan_name_displayed(self):
        """Plan name (professional) is shown."""
        expect(self.pg.get_by_text("Professional", exact=True).first).to_be_visible()

    def test_plan_status_active(self):
        """Plan status (active) is shown."""
        expect(self.pg.get_by_text("Active", exact=True).first).to_be_visible()

    def test_included_conversations_shown(self):
        """Included conversations (500) is displayed from usage fixture."""
        expect(self.pg.get_by_text("Included conversations")).to_be_visible()
        expect(self.pg.get_by_text("500/mo")).to_be_visible()

    def test_used_conversations_shown(self):
        """Used conversations (342) is displayed from usage fixture."""
        expect(self.pg.get_by_text("Used this period")).to_be_visible()
        text = main_text(self.pg)
        assert "342" in text

    def test_manage_subscription_visible(self):
        """Stripe tenants see Manage subscription (replaces soft overage OR)."""
        expect(
            self.pg.get_by_role("button", name="Manage subscription")
        ).to_be_visible()


class TestContactPreferences:
    """Verify contact preferences section renders (superadmin fixture)."""

    @pytest.fixture(autouse=True)
    def _navigate(self, shared_page: Page, mock_base_url: str):
        _go_billing(shared_page, mock_base_url)
        self.pg = shared_page
        self.url = mock_base_url

    def test_contact_section_present(self):
        expect(self.pg.get_by_text("Contact and security preferences")).to_be_visible()

    def test_admin_email_displayed(self):
        expect(
            self.pg.locator('input[value="admin@mockstore.com"]')
        ).to_be_visible()

    def test_recovery_email_field(self):
        expect(self.pg.get_by_label("Recovery email")).to_be_visible()

    def test_request_email_change_control(self):
        expect(
            self.pg.get_by_role("button", name="Request email change")
        ).to_be_visible()

    def test_contact_api_returns_preferences(self):
        """GET /api/admin/contact-preferences returns fixture data."""
        data = get_api_json(self.pg, self.url, "/api/admin/contact-preferences")
        assert data["email"] == "admin@mockstore.com"

    def test_contact_preferences_has_expected_fields(self):
        """API response includes email, recoveryEmail, notificationEmail."""
        data = get_api_json(self.pg, self.url, "/api/admin/contact-preferences")
        assert "email" in data
        assert "recoveryEmail" in data
        assert "notificationEmail" in data


class TestEmailChangeUiToApi:
    """Drive email-change request from the Billing UI (not page.request)."""

    def test_request_email_change_posts(
        self, page: Page, mock_base_url: str
    ):
        posted = {"body": None}

        def spy(route):
            if (
                route.request.method == "POST"
                and "/api/admin/email/request" in route.request.url
            ):
                posted["body"] = route.request.post_data
            route.fallback()

        page.route("**/api/admin/email/**", spy)
        _go_billing(page, mock_base_url)
        page.get_by_label("New email address").fill("newemail@mockstore.com")
        page.get_by_role("button", name="Request email change").click()
        page.wait_for_timeout(800)
        assert posted["body"], "Expected POST /api/admin/email/request from UI"
        assert "newemail@mockstore.com" in (posted["body"] or "")


class TestManageBillingUiToApi:
    """Manage subscription / billing buttons POST the Stripe portal."""

    def test_manage_subscription_posts_portal(
        self, page: Page, mock_base_url: str
    ):
        posted = {"seen": False}

        def spy(route):
            if (
                route.request.method == "POST"
                and "/api/billing/portal" in route.request.url
            ):
                posted["seen"] = True
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"portal_url": "https://billing.example/portal"}),
                )
            else:
                route.fallback()

        page.route("**/api/billing/portal*", spy)
        _go_billing(page, mock_base_url)
        page.get_by_role("button", name="Manage subscription").click()
        page.wait_for_timeout(500)
        assert posted["seen"], "Expected POST /api/billing/portal from Manage subscription"


class TestEmailChangeApiContracts:
    """Retain mock middleware contract checks for email confirm (no UI surface)."""

    def test_email_confirm_succeeds(self, page: Page, mock_base_url: str):
        resp = page.request.post(
            f"{api_origin(mock_base_url)}/api/admin/email/confirm",
            data=json.dumps({"token": "mock-token-123"}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 200
        body = resp.json()
        assert body["success"] is True

    def test_email_confirm_message(self, page: Page, mock_base_url: str):
        resp = page.request.post(
            f"{api_origin(mock_base_url)}/api/admin/email/confirm",
            data=json.dumps({"token": "any-token"}),
            headers={"Content-Type": "application/json"},
        )
        body = resp.json()
        assert "updated" in body["message"].lower() or "success" in body["message"].lower()


class TestMessagePacks:
    """Verify message pack balance and listing."""

    def test_packs_endpoint_returns_balance(self, shared_page: Page, mock_base_url: str):
        """GET /api/billing/packs returns balance of 5."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/packs")
        assert data["balance"] == 5

    def test_packs_endpoint_returns_empty_list(self, shared_page: Page, mock_base_url: str):
        """GET /api/billing/packs returns empty packs array."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/packs")
        assert data["packs"] == []

    def test_packs_has_balance_field(self, shared_page: Page, mock_base_url: str):
        """Response includes balance as integer."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/packs")
        assert isinstance(data["balance"], int)

    def test_packs_has_packs_field(self, shared_page: Page, mock_base_url: str):
        """Response includes packs as list."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/packs")
        assert isinstance(data["packs"], list)


class TestTierDisplay:
    """Verify tier-related display elements."""

    @pytest.fixture(autouse=True)
    def _navigate(self, shared_page: Page, mock_base_url: str):
        _go_billing(shared_page, mock_base_url)
        self.pg = shared_page

    def test_professional_tier_shown(self):
        """Professional tier badge is displayed on the billing page."""
        expect(self.pg.get_by_text("Professional", exact=True).first).to_be_visible()

    def test_conversation_packs_section(self):
        """Conversation packs section with Purchase CTAs."""
        expect(self.pg.get_by_text("Conversation packs")).to_be_visible()
        assert self.pg.get_by_role("button", name="Purchase").count() >= 3

    def test_page_title_account_and_billing(self):
        """Page title is Account and billing."""
        expect(
            self.pg.get_by_role("heading", name="Account and billing")
        ).to_be_visible()

    def test_manage_billing_visible(self):
        """Stripe tenants see Manage subscription and/or Manage billing CTAs."""
        manage = self.pg.get_by_role(
            "button", name=re.compile(r"Manage (subscription|billing)", re.I)
        )
        expect(manage.first).to_be_visible()


class TestBillingApiContracts:
    """Verify billing mock API endpoints return expected data shapes."""

    def test_billing_status_endpoint(self, shared_page: Page, mock_base_url: str):
        """GET /api/billing/status returns expected fields."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/status")
        assert data["plan"] == "professional"
        assert data["status"] == "active"
        assert data["includedConversations"] == 500

    def test_billing_status_has_usage(self, shared_page: Page, mock_base_url: str):
        """Billing status includes used conversations."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/status")
        assert data["usedConversations"] == 342

    def test_billing_status_has_overage(self, shared_page: Page, mock_base_url: str):
        """Billing status includes overage rate."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/status")
        assert data["overageRate"] == 0.15

    def test_billing_status_has_period(self, shared_page: Page, mock_base_url: str):
        """Billing status includes period start and end."""
        data = get_api_json(shared_page, mock_base_url, "/api/billing/status")
        assert "currentPeriodStart" in data
        assert "currentPeriodEnd" in data
