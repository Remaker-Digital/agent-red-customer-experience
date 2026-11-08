# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Deployment Management: trigger → POST, empty/error states."""

import json

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def deployments_page(provider_page: Page) -> Page:
    navigate_provider(provider_page, "/deployments", wait_text="Deployment Management")
    mocker: ProviderApiMocker = provider_page._api_mocker  # type: ignore[attr-defined]
    mocker.clear_calls()
    return provider_page


class TestDeploymentList:
    def test_history_rows_from_mock(self, deployments_page: Page) -> None:
        expect(deployments_page.get_by_text("v1.98.65", exact=True)).to_be_visible()
        expect(deployments_page.get_by_text("succeeded", exact=True).first).to_be_visible()
        expect(deployments_page.get_by_text("v1.98.64", exact=True)).to_be_visible()

    def test_refresh_reloads_list(self, deployments_page: Page) -> None:
        mocker: ProviderApiMocker = deployments_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()
        deployments_page.get_by_role("button", name="Refresh").click()
        deployments_page.wait_for_timeout(400)
        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/deployments")
        assert len(gets) >= 1, "Refresh must GET deployments"


class TestTriggerPipeline:
    def test_trigger_modal_and_post(self, deployments_page: Page) -> None:
        mocker: ProviderApiMocker = deployments_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        deployments_page.get_by_role("button", name="Trigger Pipeline").click()
        dialog = deployments_page.get_by_role("dialog")
        expect(dialog).to_be_visible()

        # Suggested next patch from last succeeded v1.98.65 → v1.98.66
        version_input = dialog.get_by_label("Version")
        expect(version_input).to_be_visible()
        current = version_input.input_value()
        if not current:
            version_input.fill("v1.98.66")
        else:
            assert current.startswith("v1.98."), f"Unexpected suggested version {current}"

        dialog.get_by_role("button", name="Start Pipeline").click()
        deployments_page.wait_for_timeout(500)

        posts = mocker.get_calls(method="POST", path_contains="/api/superadmin/deployments/trigger")
        assert len(posts) == 1, f"Expected trigger POST, got {posts}"
        body = json.loads(posts[0]["body"] or "{}")
        assert body.get("action") == "full"
        assert body.get("version"), "Trigger must send version"
        # Optimistic active card appears
        expect(deployments_page.get_by_text("Active Pipelines")).to_be_visible()
        expect(deployments_page.get_by_text("queued", exact=False).first).to_be_visible()

    def test_trigger_failure_shows_error(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/deployments/trigger",
            {"detail": "Pipeline busy"},
            status=409,
        )
        setup_provider_page(page, mocker, "/deployments")
        page.wait_for_selector("text=Deployment Management", timeout=15_000)
        page.get_by_role("button", name="Trigger Pipeline").click()
        dialog = page.get_by_role("dialog")
        version = dialog.get_by_label("Version")
        if not version.input_value():
            version.fill("v1.98.99")
        dialog.get_by_role("button", name="Start Pipeline").click()
        expect(page.get_by_text("Pipeline busy").or_(page.get_by_text("Trigger failed"))).to_be_visible(timeout=5_000)


class TestDeploymentEmptyState:
    def test_empty_history_message(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/deployments",
            {"deployments": [], "total": 0},
        )
        setup_provider_page(page, mocker, "/deployments")
        page.wait_for_selector("text=Deployment Management", timeout=15_000)
        expect(page.get_by_text("No completed deployments yet.")).to_be_visible()
