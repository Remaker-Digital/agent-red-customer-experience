"""
E2E tests — Integrations page.

Strong asserts for empty state, error UI, and tier gating (starter fixture
with tierMet=false integrations). Soft presence-only checks removed.

Run with:
    pytest tests/e2e/test_integrations_page.py -v --headed
    pytest tests/e2e/test_integrations_page.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import Page, expect

from .conftest import AdminApiMocker, setup_admin_page


pytestmark = pytest.mark.e2e

# API returns a bare array (matches Vite mock + useIntegrations typing).
STARTER_GATED_INTEGRATIONS = [
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
    {
        "type": "mailchimp",
        "name": "Mailchimp",
        "description": "Sync customer contacts and manage email campaigns.",
        "icon": "📧",
        "enabled": False,
        "status": None,
        "tierGate": "professional",
        "tierMet": False,
        "comingSoon": False,
    },
]


class TestIntegrationsPageStructure:
    """Verify the Integrations page renders expected elements."""

    def test_page_title(self, admin_integrations_page: Page) -> None:
        """SPEC-0901: Integrations page title is 'Integrations'."""
        assert "integrations" in admin_integrations_page.url.lower()
        expect(
            admin_integrations_page.get_by_role("heading", name="Integrations")
        ).to_be_visible()
        expect(
            admin_integrations_page.locator("text=Application error")
        ).to_have_count(0)

    def test_empty_state_when_no_integrations(
        self, admin_integrations_page: Page
    ) -> None:
        """Empty integrations list shows the empty-state copy."""
        expect(
            admin_integrations_page.get_by_text("No integrations available.")
        ).to_be_visible()


class TestIntegrationsErrorState:
    """Error UI when the integrations list fails to load."""

    def test_load_error_shows_failure_message(
        self, page: Page, admin_vite_server, api_mocker: AdminApiMocker
    ) -> None:
        api_mocker.override(
            "/api/admin/integrations",
            {"error": "Internal server error"},
            status=500,
        )
        setup_admin_page(page, api_mocker)
        page.get_by_text("Integrations", exact=True).first.click()
        page.wait_for_timeout(800)
        expect(page.get_by_text("Failed to load integrations:")).to_be_visible()


class TestIntegrationsTierGate:
    """Starter-tier tenants see upgrade prompts for gated integrations."""

    def test_starter_sees_upgrade_prompt(
        self, page: Page, admin_vite_server, api_mocker_starter: AdminApiMocker
    ) -> None:
        api_mocker_starter.override(
            "/api/admin/integrations", STARTER_GATED_INTEGRATIONS
        )
        setup_admin_page(page, api_mocker_starter)
        page.get_by_text("Integrations", exact=True).first.click()
        page.wait_for_timeout(800)

        expect(page.get_by_text("Zendesk").first).to_be_visible()
        expect(
            page.get_by_text("Upgrade to professional to use this integration").first
        ).to_be_visible()
        expect(
            page.get_by_text("Some integrations require Professional tier or above")
        ).to_be_visible()
