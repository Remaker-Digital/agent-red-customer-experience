"""Accessibility smoke suite for the standalone admin SPA (SPEC-1846/WI-1505).

Admin-e2e starts only the standalone Vite app with Playwright route mocks.
Provider control-plane and chat-widget surfaces are covered by
``tests/accessibility`` (dev:mock) and ``tests/visual`` respectively — those
paths are not started here, so this module targets standalone routes only.

Requires axe-playwright-python. Uses the admin-e2e entries of the count-based
known-violations baseline in ``tests/accessibility/known_violations.py``. The
route mocks here render different data than ``tests/accessibility`` (dev:mock),
so each suite keeps its own entries; never borrow the other suite's.

Run with:
    pytest tests/e2e/test_accessibility.py --headed

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import pytest
from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

from tests.accessibility.known_violations import ADMIN_E2E, baseline_issues
from tests.e2e.conftest import ADMIN_BASE, ADMIN_TENANT_ID

pytestmark = pytest.mark.e2e

# Standalone admin routes (basename /admin/standalone). Guard values match
# tests/accessibility/test_axe_ci.py against the live SPA headings.
STANDALONE_PAGES = [
    ("/", "Dashboard", "heading", "Dashboard"),
    ("/configuration", "Configuration", "heading", "Agent configuration"),
    ("/inbox", "Inbox", "placeholder", "Search conversations..."),
    ("/analytics", "Analytics", "heading", "Analytics"),
    ("/team", "Team", "heading", "Team members"),
    ("/knowledge-base", "Knowledge Base", "heading", "Knowledge base"),
    ("/integrations", "Integrations", "heading", "Integrations"),
    ("/billing", "Billing", "heading", "Account and billing"),
    ("/widget", "Widget", "heading", "Widget configuration"),
]


def _goto_standalone(page: Page, path: str) -> None:
    """Navigate within the already-authenticated admin e2e session."""
    separator = "&" if "?" in path else "?"
    page.goto(f"{ADMIN_BASE}{path}{separator}tenant={ADMIN_TENANT_ID}")
    page.wait_for_load_state("networkidle")


def _assert_a11y_with_baseline(page: Page, page_name: str) -> None:
    """Fail on new, regressed or stale critical/serious axe rules vs the admin-e2e baseline."""
    results = Axe().run(page)
    errors = baseline_issues(ADMIN_E2E, page_name, results.response.get("violations", []))
    if errors:
        raise AssertionError(
            f"{len(errors)} a11y issue(s) on {page_name} ({ADMIN_E2E} baseline):\n" + "\n".join(errors)
        )


class TestStandaloneAdminAccessibility:
    """WCAG 2.1 AA smoke checks for standalone admin pages in admin-e2e."""

    @pytest.mark.parametrize(
        "path,page_name,guard_type,guard_value",
        STANDALONE_PAGES,
        ids=[p[1] for p in STANDALONE_PAGES],
    )
    def test_standalone_page_a11y(
        self,
        admin_page: Page,
        path: str,
        page_name: str,
        guard_type: str,
        guard_value: str,
    ) -> None:
        """Standalone admin {page_name} has no new critical/serious a11y violations."""
        _goto_standalone(admin_page, path)

        if guard_type == "heading":
            marker = admin_page.get_by_role("heading", name=guard_value)
        elif guard_type == "placeholder":
            marker = admin_page.get_by_placeholder(guard_value)
        else:
            raise ValueError(f"Unknown guard_type: {guard_type}")
        expect(marker.first).to_be_visible(timeout=10_000)

        _assert_a11y_with_baseline(admin_page, page_name)


@pytest.mark.skip(
    reason=(
        "Provider control-plane routes are not served by admin/standalone Vite; "
        "covered by tests/provider_e2e and provider console surfaces."
    )
)
class TestControlPlaneAccessibilityPlaceholder:
    """Retained skip so historical parametrized control-plane IDs stay documented."""

    def test_control_plane_not_in_admin_e2e(self) -> None:
        assert False, "unreachable"


@pytest.mark.skip(
    reason=(
        "Chat widget Vite (port 3100) is not started by admin-e2e; "
        "widget a11y/visual covered by tests/visual and tests/accessibility."
    )
)
class TestWidgetAccessibility:
    """Widget a11y requires a separate widget Vite process — skipped in admin-e2e."""

    def test_widget_chat_panel_a11y(self) -> None:
        assert False, "unreachable"

    def test_widget_launcher_button_a11y(self) -> None:
        assert False, "unreachable"
