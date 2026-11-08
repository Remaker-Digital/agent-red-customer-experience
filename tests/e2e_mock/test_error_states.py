# (C) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""SPEC-1706: Error states and edge case tests.

Strong asserts for real error/empty UI (not "body still has text").
Uses correct SPA routes and API paths exercised by the pages.
"""
import pytest
from playwright.sync_api import expect

from tests.e2e_mock.conftest import navigate_and_settle, MOCK_TENANT, api_origin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fulfill_500(route):
    route.fulfill(
        status=500,
        content_type="application/json",
        body='{"error": "Internal Server Error"}',
    )


def _fulfill_empty(route, body="[]"):
    route.fulfill(
        status=200,
        content_type="application/json",
        body=body,
        headers={"x-mock": "true"},
    )


def _exact_api_path(url: str, path: str) -> bool:
    """True when url path equals ``path`` (ignores query string)."""
    from urllib.parse import urlparse

    return urlparse(url).path.rstrip("/") == path.rstrip("/")


def _route_exact(page, path: str, handler):
    """Intercept only the exact API path; let sub-routes fall through to Vite mock."""

    def _wrap(route):
        if _exact_api_path(route.request.url, path):
            handler(route)
        else:
            route.fallback()

    page.route(f"**{path}*", _wrap)


def _no_crash(page) -> None:
    """Page must not show a React error boundary."""
    body = (page.locator("body").inner_text() or "").lower()
    for marker in (
        "unexpected application error",
        "application error",
        "unhandled",
        "stack trace",
    ):
        assert marker not in body, f"Crash UI visible: {marker}"


# ---------------------------------------------------------------------------
# TestApiFailures
# ---------------------------------------------------------------------------

class TestApiFailures:
    """Verify pages show real error UI when API returns 500."""

    @pytest.fixture(autouse=True)
    def _setup(self, page, mock_base_url):
        self.page = page
        self.base = mock_base_url

    def test_dashboard_500_does_not_crash(self):
        """Dashboard analytics 500 keeps chrome + heading (graceful zeros)."""
        _route_exact(self.page, "/api/analytics/summary", _fulfill_500)
        navigate_and_settle(self.page, "/", self.base)
        _no_crash(self.page)
        expect(self.page.get_by_role("heading", name="Dashboard")).to_be_visible()
        expect(self.page.get_by_text("Total conversations")).to_be_visible()

    def test_team_500_shows_error(self):
        _route_exact(self.page, "/api/admin/team", _fulfill_500)
        navigate_and_settle(self.page, "/team", self.base)
        _no_crash(self.page)
        expect(self.page.get_by_text("Failed to load team:")).to_be_visible()

    def test_knowledge_500_shows_error_banner(self):
        _route_exact(self.page, "/api/admin/knowledge", _fulfill_500)
        navigate_and_settle(self.page, "/knowledge-base", self.base)
        _no_crash(self.page)
        # ErrorBanner renders the hook error string and a Retry control
        expect(self.page.get_by_role("button", name="Retry")).to_be_visible()
        expect(self.page.get_by_text("500", exact=False)).to_be_visible()

    def test_config_500_shows_alert(self):
        _route_exact(self.page, "/api/config", _fulfill_500)
        navigate_and_settle(self.page, "/configuration", self.base)
        _no_crash(self.page)
        expect(
            self.page.get_by_text("Failed to load configuration")
        ).to_be_visible()

    def test_inbox_500_shows_error(self):
        _route_exact(self.page, "/api/admin/conversations", _fulfill_500)
        navigate_and_settle(self.page, "/inbox", self.base)
        _no_crash(self.page)
        expect(
            self.page.get_by_text("Failed to load conversations")
        ).to_be_visible()

    def test_billing_500_shows_usage_alert(self):
        """Billing page uses /api/dashboard/usage for the usage alert."""
        _route_exact(self.page, "/api/dashboard/usage", _fulfill_500)
        navigate_and_settle(self.page, "/billing", self.base)
        _no_crash(self.page)
        expect(
            self.page.get_by_text("Usage data unavailable")
        ).to_be_visible()
        expect(
            self.page.get_by_role("heading", name="Account and billing")
        ).to_be_visible()


# ---------------------------------------------------------------------------
# TestNetworkFailures
# ---------------------------------------------------------------------------

class TestNetworkFailures:
    """Verify pages handle network-level failures without crashing."""

    @pytest.fixture(autouse=True)
    def _setup(self, page, mock_base_url):
        self.page = page
        self.base = mock_base_url

    def test_dashboard_network_abort_no_crash(self):
        _route_exact(self.page, "/api/analytics/summary", lambda route: route.abort())
        navigate_and_settle(self.page, "/", self.base)
        _no_crash(self.page)
        expect(self.page.get_by_role("heading", name="Dashboard")).to_be_visible()

    def test_team_network_abort_shows_error(self):
        _route_exact(self.page, "/api/admin/team", lambda route: route.abort())
        navigate_and_settle(self.page, "/team", self.base)
        _no_crash(self.page)
        expect(self.page.get_by_text("Failed to load team:")).to_be_visible()

    def test_knowledge_network_abort_shows_retry(self):
        _route_exact(self.page, "/api/admin/knowledge", lambda route: route.abort())
        navigate_and_settle(self.page, "/knowledge-base", self.base)
        _no_crash(self.page)
        expect(self.page.get_by_role("button", name="Retry")).to_be_visible()

    def test_config_network_abort_shows_alert(self):
        _route_exact(self.page, "/api/config", lambda route: route.abort())
        navigate_and_settle(self.page, "/configuration", self.base)
        _no_crash(self.page)
        expect(
            self.page.get_by_text("Failed to load configuration")
        ).to_be_visible()


# ---------------------------------------------------------------------------
# TestEmptyData
# ---------------------------------------------------------------------------

class TestEmptyData:
    """Verify pages show real empty-state copy for empty payloads."""

    @pytest.fixture(autouse=True)
    def _setup(self, page, mock_base_url):
        self.page = page
        self.base = mock_base_url

    def test_team_empty_list(self):
        _route_exact(
            self.page,
            "/api/admin/team",
            lambda r: _fulfill_empty(r, '{"members": []}'),
        )
        navigate_and_settle(self.page, "/team", self.base)
        expect(self.page.get_by_text("No team members yet")).to_be_visible()

    def test_knowledge_empty_articles(self):
        _route_exact(
            self.page,
            "/api/admin/knowledge",
            lambda r: _fulfill_empty(r, '{"articles": []}'),
        )
        navigate_and_settle(self.page, "/knowledge-base", self.base)
        # Empty KB may render EmptyState or the filtered-table zero row.
        empty = self.page.get_by_text("No articles yet")
        filtered = self.page.get_by_text("No articles match your filters.")
        assert empty.count() > 0 or filtered.count() > 0, (
            "Expected empty knowledge-base copy"
        )
        expect(empty.or_(filtered).first).to_be_visible()

    def test_inbox_empty_conversations(self):
        _route_exact(
            self.page,
            "/api/admin/conversations",
            lambda r: _fulfill_empty(r, '{"conversations": []}'),
        )
        navigate_and_settle(self.page, "/inbox", self.base)
        expect(self.page.get_by_text("No conversations yet")).to_be_visible()

    def test_quick_actions_empty_list(self):
        _route_exact(
            self.page,
            "/api/admin/quick-actions",
            lambda r: _fulfill_empty(r, '{"actions": []}'),
        )
        navigate_and_settle(self.page, "/quick-actions", self.base)
        expect(
            self.page.get_by_text("No quick actions yet", exact=False)
        ).to_be_visible()


# ---------------------------------------------------------------------------
# TestMockHeaderVerification
# ---------------------------------------------------------------------------

class TestMockHeaderVerification:
    """Verify the mock server sets X-Mock: true on API responses."""

    @pytest.fixture(autouse=True)
    def _setup(self, page, mock_base_url):
        self.page = page
        self.base = mock_base_url

    def test_health_has_mock_header(self):
        resp = self.page.request.get(f"{api_origin(self.base)}/api/health")
        assert resp.status == 200
        assert resp.headers.get("x-mock") == "true"

    def test_tenant_lookup_has_mock_header(self):
        resp = self.page.request.get(
            f"{api_origin(self.base)}/api/tenants/lookup?tenant={MOCK_TENANT}"
        )
        assert resp.status == 200
        assert resp.headers.get("x-mock") == "true"

    def test_config_has_mock_header(self):
        resp = self.page.request.get(f"{api_origin(self.base)}/api/config")
        assert resp.status == 200
        assert resp.headers.get("x-mock") == "true"

    def test_team_has_mock_header(self):
        resp = self.page.request.get(f"{api_origin(self.base)}/api/admin/team")
        assert resp.status == 200
        assert resp.headers.get("x-mock") == "true"
