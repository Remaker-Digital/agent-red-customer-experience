# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""
E2E fixtures for the Shopify embedded admin SPA (`admin/shopify`).

Uses Vite ``--mode mock`` on port 3200 so:
  - App Bridge CDN is stripped and ``window.shopify.idToken()`` is injected
  - ``?shop=mock-store.myshopify.com`` is injected for tenant resolution
  - ``/api/*`` is served by ``admin/shopify/mocks`` (no backend / no live Shopify)

``npx vite --mode mock`` avoids the Windows-only ``predev`` PowerShell sync
used by ``npm run dev``. Call capture uses Playwright ``page.route`` with
``continue_`` so Strong tests can assert UI→API method/path/Authorization
while still hitting the Vite mock middleware.

Live Shopify production paths remain under ``tests/e2e_live/shopify/`` (ADO-excluded).

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, Route

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SHOPIFY_VITE_PORT = 3200
SHOPIFY_DIR = Path(__file__).resolve().parent.parent.parent / "admin" / "shopify"
SHOPIFY_BASE = f"http://localhost:{SHOPIFY_VITE_PORT}/admin/shopify"
MOCK_SHOP = "mock-store.myshopify.com"
MOCK_SESSION_TOKEN = "mock-shopify-session-token"

# Routes match admin/shopify/index.tsx (App Bridge NavMenu is not in DOM).
SHOPIFY_ROUTES: list[tuple[str, str, str]] = [
    ("Dashboard", "/", "Dashboard"),
    ("Inbox", "/inbox", "Conversation Inbox"),
    ("Agent configuration", "/configuration", "Agent configuration"),
    ("Knowledge Base", "/knowledge-base", "Knowledge Base"),
    ("Widget configuration", "/widget", "Widget configuration"),
    ("Billing", "/billing", "Billing & Usage"),
    ("Settings", "/settings", "Settings"),
]

# Fixture KPIs from admin/shopify/mocks/fixtures/dashboard.ts
MOCK_TOTAL_CONVERSATIONS = "187"


# ---------------------------------------------------------------------------
# Call recorder (Strong UI→API)
# ---------------------------------------------------------------------------


class ShopifyApiRecorder:
    """Records /api/* traffic; optional path overrides for error-state tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._overrides: dict[str, tuple[Any, int]] = {}

    def override(self, path_contains: str, body: Any, status: int = 200) -> None:
        self._overrides[path_contains] = (body, status)

    def clear_calls(self) -> None:
        self.calls.clear()

    def get_calls(
        self,
        *,
        method: str | None = None,
        path_contains: str | None = None,
    ) -> list[dict[str, Any]]:
        out = self.calls
        if method:
            out = [c for c in out if c["method"] == method.upper()]
        if path_contains:
            out = [c for c in out if path_contains in c["path"]]
        return out

    def handle_route(self, route: Route) -> None:
        request = route.request
        url = request.url
        parsed = urlparse(url)
        path = parsed.path
        method = request.method.upper()
        headers = {k.lower(): v for k, v in request.headers.items()}

        self.calls.append(
            {
                "method": method,
                "path": path,
                "url": url,
                "query": parse_qs(parsed.query),
                "headers": headers,
                "post_data": request.post_data,
            }
        )

        for needle, (body, status) in self._overrides.items():
            if needle in path:
                route.fulfill(
                    status=status,
                    content_type="application/json",
                    body=json.dumps(body),
                )
                return

        # Let Vite mock middleware serve the response.
        route.continue_()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _port_is_open(port: int) -> bool:
    for family, addr in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((addr, port))
                return True
        except (ConnectionRefusedError, OSError):
            continue
    return False


def shopify_url(path: str = "/") -> str:
    if path in ("", "/"):
        return f"{SHOPIFY_BASE}/"
    return f"{SHOPIFY_BASE}/{path.lstrip('/')}"


def navigate_shopify(page: Page, path: str, wait_text: str | None = None) -> None:
    page.goto(shopify_url(path), wait_until="domcontentloaded")
    if wait_text:
        page.wait_for_selector(f"text={wait_text}", timeout=20_000)
    page.wait_for_timeout(300)


def setup_shopify_page(
    page: Page,
    recorder: ShopifyApiRecorder,
    path: str = "/",
    *,
    wait_text: str | None = "Dashboard",
    dismiss_onboarding: bool = True,
) -> Page:
    """Wire recorder, optional onboarding dismiss, navigate."""
    page.route("**/api/**", recorder.handle_route)
    if dismiss_onboarding:
        page.add_init_script(
            """
            try {
                sessionStorage.setItem('agentred-onboarding-dismissed', '1');
            } catch (e) {}
            """
        )
    target = shopify_url(path)
    # Dashboard wait_text only applies when landing on /
    if path not in ("", "/") and wait_text == "Dashboard":
        wait_text = None
    page.goto(target, wait_until="domcontentloaded")
    if wait_text:
        page.wait_for_selector(f"text={wait_text}", timeout=20_000)
    page.wait_for_timeout(400)
    page._api_recorder = recorder  # type: ignore[attr-defined]
    return page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def shopify_vite_server():
    """Start Shopify Vite in mock mode on port 3200 (session-scoped)."""
    node_modules = SHOPIFY_DIR / "node_modules"
    if not node_modules.exists():
        pytest.skip("admin/shopify/node_modules not found — run `cd admin/shopify && npm ci`")

    if _port_is_open(SHOPIFY_VITE_PORT):
        yield None
        return

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    env = {
        **os.environ,
        "VITE_API_URL": "",
        "BROWSER": "none",
        "NO_COLOR": "1",
        "CI": "true",
    }
    proc = subprocess.Popen(
        [
            "npx",
            "vite",
            "--mode",
            "mock",
            "--port",
            str(SHOPIFY_VITE_PORT),
            "--strictPort",
        ],
        cwd=str(SHOPIFY_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        env=env,
    )

    for _ in range(60):
        if _port_is_open(SHOPIFY_VITE_PORT):
            break
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"Shopify Vite exited with code {proc.returncode}.\nstdout: {stdout}\nstderr: {stderr}")
        time.sleep(1)
    else:
        proc.kill()
        raise RuntimeError(f"Shopify Vite did not start on port {SHOPIFY_VITE_PORT} within 60 s")

    yield proc

    if sys.platform == "win32":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def api_recorder() -> ShopifyApiRecorder:
    return ShopifyApiRecorder()


@pytest.fixture()
def shopify_page(page: Page, shopify_vite_server, api_recorder: ShopifyApiRecorder) -> Page:
    """Authenticated Shopify shell on Dashboard with mock App Bridge + API."""
    setup_shopify_page(page, api_recorder, "/", wait_text="Dashboard")
    return page
