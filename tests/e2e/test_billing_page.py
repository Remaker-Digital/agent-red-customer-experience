"""
E2E tests — Billing & Usage page.

Tests ADMIN_UI specs for the Billing page:
  - Page title
  - Manage subscription button
  - Manage billing button
  - Metric help tooltips
  - Purchase CTAs

Strong asserts (GOV-18): exact visible controls and labels — no soft OR
fallback to generic body text.

Run with:
    pytest tests/e2e/test_billing_page.py -v --headed
    pytest tests/e2e/test_billing_page.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e


class TestBillingPageStructure:
    """Verify the Billing page renders all expected elements."""

    def test_page_title(self, admin_billing_page: Page) -> None:
        """SPEC-0878: Billing page title is 'Account and billing'."""
        expect(
            admin_billing_page.get_by_role("heading", name="Account and billing")
        ).to_be_visible()

    def test_manage_subscription_button(self, admin_billing_page: Page) -> None:
        """SPEC-0875: Billing has 'Manage subscription' button (Stripe tenants)."""
        expect(
            admin_billing_page.get_by_role("button", name="Manage subscription")
        ).to_be_visible()

    def test_manage_billing_button(self, admin_billing_page: Page) -> None:
        """SPEC-0876: Billing has 'Manage billing' button (Stripe tenants)."""
        expect(
            admin_billing_page.get_by_role("button", name="Manage billing")
        ).to_be_visible()


class TestBillingFeatures:
    """Verify Billing page UX features. WI 281, 282."""

    def test_metric_cards_help_tooltips(self, admin_billing_page: Page) -> None:
        """WI 281: Billing metric cards have help tooltips with doc links."""
        expect(admin_billing_page.get_by_text("Conversations used")).to_be_visible()
        expect(admin_billing_page.get_by_text("Pack balance")).to_be_visible()
        expect(admin_billing_page.get_by_text("Current overage")).to_be_visible()
        help_icons = admin_billing_page.locator('[aria-label]')
        # HelpTooltip sets aria-label to the help text
        assert help_icons.count() >= 3, (
            "Billing metric / plan help tooltips should be present"
        )

    def test_purchase_button_exists(self, admin_billing_page: Page) -> None:
        """WI 282: Conversation pack Purchase buttons are present."""
        purchase_btns = admin_billing_page.get_by_role("button", name="Purchase")
        expect(purchase_btns.first).to_be_visible()
        assert purchase_btns.count() >= 3, (
            "Expected three conversation-pack Purchase buttons"
        )


class TestBillingPortalUiToApi:
    """UI click → intercepted billing portal POST."""

    def test_manage_subscription_posts_portal(
        self, admin_billing_page: Page
    ) -> None:
        """Manage subscription triggers POST /api/billing/portal."""
        mocker = admin_billing_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()
        admin_billing_page.get_by_role("button", name="Manage subscription").click()
        admin_billing_page.wait_for_timeout(500)
        posts = mocker.get_calls(method="POST", path_contains="/api/billing/portal")
        assert len(posts) >= 1, "Expected POST /api/billing/portal from Manage subscription"
