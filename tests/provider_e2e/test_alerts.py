# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Alert Config: create, enable toggle, acknowledge → API."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def alerts_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/alerts", wait_text="Alert Configuration")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestAlertRulesLoaded:
    def test_rules_and_tabs(self, alerts_page: Page) -> None:
        expect(alerts_page.get_by_role("tab", name="Rules (2)")).to_be_visible()
        panel = alerts_page.get_by_role("tabpanel", name="Rules (2)")
        expect(panel.get_by_text("High Error Rate").first).to_be_visible()
        expect(panel.get_by_text("Queue Depth").first).to_be_visible()
        expect(alerts_page.get_by_role("tab", name="History (2)")).to_be_visible()


class TestAlertCreate:
    def test_create_rule_posts_payload(self, alerts_page: Page) -> None:
        mocker: ProviderApiMocker = alerts_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        alerts_page.get_by_role("button", name="Create Rule").click()
        dialog = alerts_page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_placeholder("Alert rule name").fill("E2E Depth Guard")
        dialog.get_by_placeholder("What this rule monitors").fill("Deep journey rule")
        dialog.get_by_placeholder("e.g. queue_depth, error_rate").fill("queue_depth")
        # HelpTooltip also matches get_by_label("Threshold") — use textbox role
        dialog.get_by_role("textbox", name="Threshold").fill("42")
        dialog.get_by_role("button", name="Create Rule").click()
        alerts_page.wait_for_timeout(500)

        posts = mocker.get_calls(method="POST", path_contains="/api/superadmin/alerts/rules")
        posts = [p for p in posts if p["path"].rstrip("/").endswith("/alerts/rules")]
        assert len(posts) == 1, f"Expected create POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body["name"] == "E2E Depth Guard"
        assert body["description"] == "Deep journey rule"
        assert body["condition"]["metric"] == "queue_depth"
        assert body["condition"]["threshold"] == 42
        expect(alerts_page.get_by_text("Rule created").first).to_be_visible(timeout=5_000)
        expect(alerts_page.get_by_text("E2E Depth Guard").first).to_be_visible()


class TestAlertEnableToggle:
    def test_toggle_puts_enabled_false(self, alerts_page: Page) -> None:
        mocker: ProviderApiMocker = alerts_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        row = alerts_page.locator("tr", has_text="High Error Rate")
        # Mantine Switch — click the switch control in the Enabled column
        switch = row.locator('input[type="checkbox"]').first
        expect(switch).to_be_checked()
        # Click via label/track (input is often visually hidden)
        row.locator(".mantine-Switch-track").first.click()
        alerts_page.wait_for_timeout(500)

        puts = mocker.get_calls(method="PUT", path_contains="/api/superadmin/alerts/rules/rule-001")
        assert len(puts) == 1, f"Expected toggle PUT, got {puts}"
        body = json.loads(puts[0]["body"] or "{}")
        assert body == {"enabled": False}
        expect(alerts_page.get_by_text("Rule disabled")).to_be_visible(timeout=5_000)


class TestAlertHistoryAcknowledge:
    def test_acknowledge_posts(self, alerts_page: Page) -> None:
        mocker: ProviderApiMocker = alerts_page._api_mocker  # type: ignore[attr-defined]
        alerts_page.get_by_role("tab", name="History (2)").click()
        expect(alerts_page.get_by_role("button", name="Acknowledge").first).to_be_visible()
        mocker.clear_calls()

        alerts_page.get_by_role("button", name="Acknowledge").first.click()
        alerts_page.wait_for_timeout(500)

        posts = mocker.get_calls(
            method="POST",
            path_contains="/alerts/history/alert-ack-pending/acknowledge",
        )
        assert len(posts) == 1, f"Expected acknowledge POST, got {posts}"
        assert "alert_date=" in posts[0]["url"]
        expect(alerts_page.get_by_text("Alert acknowledged")).to_be_visible(timeout=5_000)
        expect(alerts_page.get_by_text("Ack: e2e-operator")).to_be_visible()


class TestAlertEvaluate:
    def test_evaluate_now_posts(self, alerts_page: Page) -> None:
        mocker: ProviderApiMocker = alerts_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()
        alerts_page.get_by_role("button", name="Evaluate Now").click()
        alerts_page.wait_for_timeout(400)
        posts = mocker.get_calls(method="POST", path_contains="/api/superadmin/alerts/evaluate")
        assert len(posts) == 1, f"Expected evaluate POST, got {posts}"
        expect(alerts_page.get_by_text("Evaluation complete")).to_be_visible(timeout=5_000)
