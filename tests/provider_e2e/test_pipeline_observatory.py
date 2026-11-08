# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Provider SPA — Pipeline Observatory: tabs, period filter → API asserts."""

import pytest
from playwright.sync_api import Page, expect

from .conftest import ProviderApiMocker, navigate_provider, setup_provider_page

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(90)]


@pytest.fixture()
def pipeline_page(provider_page: Page) -> Page:
    # Do not clear_calls — Mantine mounts all tab panels eagerly and their
    # GETs are part of the Strong coverage for tab data loading.
    navigate_provider(provider_page, "/pipeline", wait_text="Pipeline Observatory")
    return provider_page


class TestInfrastructureTab:
    def test_loads_infra_and_shows_total_requests(self, pipeline_page: Page) -> None:
        mocker: ProviderApiMocker = pipeline_page._api_mocker  # type: ignore[attr-defined]
        # Tabs mount eagerly — infra GET happens on page load
        infra_gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/pipeline/infrastructure")
        assert infra_gets, "Infrastructure tab must GET pipeline/infrastructure on mount"
        panel = pipeline_page.get_by_role("tabpanel", name="Infrastructure")
        expect(panel.get_by_text("Total Requests")).to_be_visible()
        expect(panel.get_by_text("48200").first).to_be_visible()
        expect(panel.get_by_text("API Gateway").first).to_be_visible()
        expect(panel.get_by_text("NATS JetStream").first).to_be_visible()

    def test_period_change_refetches_with_query(self, pipeline_page: Page) -> None:
        mocker: ProviderApiMocker = pipeline_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        panel = pipeline_page.get_by_role("tabpanel", name="Infrastructure")
        panel.get_by_role("textbox", name="Time Range").click()
        pipeline_page.get_by_role("option", name="Last Hour").click()
        pipeline_page.wait_for_timeout(400)

        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/pipeline/infrastructure")
        assert gets, "Period change must refetch infrastructure"
        assert any("period=1h" in c["url"] for c in gets), f"Expected period=1h, got {[c['url'] for c in gets]}"


class TestTabTransitions:
    def test_traffic_flow_tab_shows_topology(self, pipeline_page: Page) -> None:
        mocker: ProviderApiMocker = pipeline_page._api_mocker  # type: ignore[attr-defined]
        # Eager mount: topology already fetched; assert call + visible content after tab
        assert mocker.get_calls(method="GET", path_contains="/api/superadmin/pipeline/topology"), (
            "Traffic Flow mount must GET topology"
        )
        pipeline_page.get_by_role("tab", name="Traffic Flow").click()
        panel = pipeline_page.get_by_role("tabpanel", name="Traffic Flow")
        # UI humanizes agent ids (intent-classifier → Intent Classifier)
        expect(panel.get_by_text("Intent Classifier").first).to_be_visible()
        expect(panel.get_by_text("Source")).to_be_visible()
        mocker.clear_calls()
        panel.get_by_role("textbox", name="Time Range").click()
        pipeline_page.get_by_role("option", name="Last 7 Days").click()
        pipeline_page.wait_for_timeout(400)
        gets = mocker.get_calls(method="GET", path_contains="/api/superadmin/pipeline/topology")
        assert any("period=7d" in c["url"] for c in gets), (
            f"Expected period=7d topology refetch, got {[c['url'] for c in gets]}"
        )

    def test_agent_metrics_tab_shows_agents(self, pipeline_page: Page) -> None:
        pipeline_page.get_by_role("tab", name="Agent Metrics").click()
        panel = pipeline_page.get_by_role("tabpanel", name="Agent Metrics")
        expect(panel.get_by_text("Intent Classifier").first).to_be_visible()
        expect(panel.get_by_text("Response Generator").first).to_be_visible()

    def test_tenant_comparison_tab_shows_tenants(self, pipeline_page: Page) -> None:
        mocker: ProviderApiMocker = pipeline_page._api_mocker  # type: ignore[attr-defined]
        assert mocker.get_calls(method="GET", path_contains="/api/superadmin/pipeline/tenants"), (
            "Tenant Comparison mount must GET pipeline/tenants"
        )
        pipeline_page.get_by_role("tab", name="Tenant Comparison").click()
        expect(pipeline_page.get_by_text("Acme Corporation")).to_be_visible()
        expect(pipeline_page.get_by_text("Blanco Storefront")).to_be_visible()


class TestPipelineErrorStates:
    def test_infrastructure_error_message(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/pipeline/infrastructure",
            {"detail": "metrics unavailable"},
            status=503,
        )
        setup_provider_page(page, mocker, "/pipeline")
        page.wait_for_selector("text=Pipeline Observatory", timeout=15_000)
        expect(page.get_by_text("Unable to load infrastructure")).to_be_visible(timeout=5_000)

    def test_tenant_comparison_empty(self, page: Page, provider_vite_server) -> None:
        mocker = ProviderApiMocker()
        mocker.override(
            "/api/superadmin/pipeline/tenants",
            {"tenants": [], "total": 0, "sortBy": "totalConversations", "sortOrder": "desc"},
        )
        setup_provider_page(page, mocker, "/pipeline")
        page.wait_for_selector("text=Pipeline Observatory", timeout=15_000)
        page.get_by_role("tab", name="Tenant Comparison").click()
        expect(page.get_by_text("No tenant data available")).to_be_visible(timeout=5_000)
