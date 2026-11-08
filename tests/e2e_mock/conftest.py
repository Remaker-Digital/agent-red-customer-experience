# (C) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Shared fixtures for SPEC-1706 mock-based frontend E2E tests.

Manages a session-scoped Vite dev server running in mock mode, plus
class-scoped and function-scoped Playwright page fixtures with store
reset between mutation tests.
"""
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADMIN_STANDALONE = PROJECT_ROOT / "admin" / "standalone"
MOCK_TENANT = "mock-tenant-001"
MOCK_API_KEY = "mock-api-key-for-testing"
VITE_STARTUP_TIMEOUT = 30  # seconds

# Cache the API origin (extracted once from mock_base_url)
_api_origin_cache = None


def api_origin(mock_base_url: str) -> str:
    """Extract origin (scheme://host:port) from a Vite base URL.

    Vite serves the SPA at /admin/standalone/ but mock API middleware
    intercepts at /api/* (root path). This helper strips the base path.
    """
    global _api_origin_cache
    if _api_origin_cache is None:
        parsed = urlparse(mock_base_url)
        _api_origin_cache = f"{parsed.scheme}://{parsed.netloc}"
    return _api_origin_cache


# ---------------------------------------------------------------------------
# Session-scoped: start Vite dev server once for the entire test run
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mock_base_url():
    """Start npm run dev:mock and yield the base URL (e.g. http://localhost:5173/admin/standalone)."""
    env = {**os.environ, "BROWSER": "none", "NO_COLOR": "1"}  # prevent auto-open + disable ANSI
    # shell=True needed on Windows where npm is a .cmd script
    proc = subprocess.Popen(
        "npm run dev:mock",
        cwd=str(ADMIN_STANDALONE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        shell=True,
    )

    base_url = None
    deadline = time.time() + VITE_STARTUP_TIMEOUT

    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        # Vite prints "Local:   http://localhost:5173/admin/standalone/" when ready
        m = re.search(r"Local:\s*(https?://\S+)", line)
        if m:
            base_url = m.group(1).rstrip("/")
            break

    if not base_url:
        proc.terminate()
        pytest.fail("Vite dev server did not start within timeout")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Browser context
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context(browser, mock_base_url):
    """Session-scoped browser context with mock auth pre-injected.

    The SPA checks sessionStorage for 'agentred_api_key' on mount.
    Without it, all pages render the login form instead of the admin UI.
    We inject a mock key via add_init_script so every new page is
    automatically authenticated before the React app mounts.
    """
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        base_url=mock_base_url,
    )
    # Inject mock API key into sessionStorage BEFORE any page JS runs.
    # This bypasses the ApiKeyLogin gate in admin/standalone/index.tsx.
    context.add_init_script(f"""
        try {{
            sessionStorage.setItem('agentred_api_key', '{MOCK_API_KEY}');
        }} catch (e) {{
            // sessionStorage unavailable — tests will see login page
        }}
    """)
    yield context
    context.close()


# ---------------------------------------------------------------------------
# Class-scoped: shared page for read-only tests (faster)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def shared_page(browser_context, mock_base_url) -> Page:
    """One browser page per test class -- for read-only assertions only.

    Resets the mock store at class start so prior mutation suites (e.g. role
    gating via set-whoami) cannot leave whoami/viewer state that redirects
    admin routes like /billing to /inbox.
    """
    page = browser_context.new_page()
    origin = api_origin(mock_base_url)
    resp = page.request.post(f"{origin}/api/__test__/reset")
    assert resp.status == 200, f"Store reset failed: {resp.status}"
    yield page
    page.close()


# ---------------------------------------------------------------------------
# Function-scoped: fresh page with store reset for mutation tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def page(browser_context, mock_base_url) -> Page:
    """Fresh page with store reset -- for CRUD/mutation tests."""
    page = browser_context.new_page()
    # Reset mock store to initial fixture state (API at origin, not base path)
    origin = api_origin(mock_base_url)
    resp = page.request.post(f"{origin}/api/__test__/reset")
    assert resp.status == 200, f"Store reset failed: {resp.status}"
    yield page
    page.close()


@pytest.fixture(scope="function")
def unauthenticated_page(browser, mock_base_url) -> Page:
    """Fresh page with NO sessionStorage auth injection (real login gate).

    Use for ApiKeyLogin / TwoFaChallenge suites. Store is reset so whoami
    starts as fixture superadmin until tests call set-whoami.

    Does not install a persistent init_script that clears auth on every
    navigation — UI login must survive deep-link / reload checks.
    """
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        base_url=mock_base_url,
    )
    page = context.new_page()
    origin = api_origin(mock_base_url)
    resp = page.request.post(f"{origin}/api/__test__/reset")
    assert resp.status == 200, f"Store reset failed: {resp.status}"
    yield page
    # Restore fixture whoami so later shared_page classes are not stuck as viewer.
    page.request.post(f"{origin}/api/__test__/reset")
    context.close()


def set_whoami_role(page: Page, mock_base_url: str, role: str, email: str | None = None) -> dict:
    """Mutate mock whoami role (test-only endpoint) and return the body."""
    import json as _json

    origin = api_origin(mock_base_url)
    payload = {"role": role}
    if email:
        payload["email"] = email
    resp = page.request.post(
        f"{origin}/api/__test__/set-whoami",
        data=_json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200, f"set-whoami failed: {resp.status} {resp.text()}"
    return resp.json()


def open_unauthenticated_login(page: Page, mock_base_url: str, tenant: str = MOCK_TENANT) -> None:
    """Navigate to the login gate with tenant context (no auth injection)."""
    page.goto(f"{mock_base_url}/?tenant={tenant}")
    page.wait_for_load_state("networkidle")
    page.evaluate(
        """() => {
            try {
                sessionStorage.removeItem('agentred_api_key');
                sessionStorage.removeItem('agentred_session_token');
            } catch (e) {}
        }"""
    )
    page.reload(wait_until="networkidle")


def login_via_api_key_ui(page: Page, api_key: str = MOCK_API_KEY) -> None:
    """Drive ApiKeyLogin: switch from magic-link primary view and submit key."""
    api_key_btn = page.get_by_role("button", name="Sign in with API key")
    if api_key_btn.count() and api_key_btn.first.is_visible():
        api_key_btn.first.click()
        page.wait_for_selector('[aria-label="API key"]', timeout=5_000)
    page.get_by_label("API key").fill(api_key)
    # exact=True — "Sign in with email" also matches name="Sign in"
    page.get_by_role("button", name="Sign in", exact=True).click()


def fill_mantine_pin(page: Page, code: str) -> None:
    """Fill a Mantine PinInput digit-by-digit."""
    pin_inputs = page.locator(".mantine-PinInput-input")
    if pin_inputs.count() >= len(code):
        for i, digit in enumerate(code):
            pin_inputs.nth(i).fill(digit)
    else:
        page.keyboard.type(code)


# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------

def navigate_to(page: Page, path: str, mock_base_url: str, wait: str = "networkidle"):
    """Navigate to an admin page with tenant param and wait for load."""
    separator = "&" if "?" in path else "?"
    url = f"{mock_base_url}{path}{separator}tenant={MOCK_TENANT}"
    page.goto(url)
    page.wait_for_load_state(wait)


def navigate_and_settle(page: Page, path: str, mock_base_url: str):
    """Navigate and wait for both network idle and DOM settle."""
    navigate_to(page, path, mock_base_url)
    page.wait_for_timeout(500)


def dismiss_onboarding_if_present(page: Page):
    """Dismiss the onboarding wizard modal if it appears."""
    try:
        close_btn = page.locator("[data-testid=\"close-wizard\"], .mantine-Modal-close").first
        if close_btn.is_visible(timeout=2000):
            close_btn.click()
            page.wait_for_timeout(300)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Assertion helpers — API calls use origin (not Vite base path)
# ---------------------------------------------------------------------------

def assert_mock_active(page: Page, mock_base_url: str):
    """Verify the mock API is active by checking X-Mock header."""
    origin = api_origin(mock_base_url)
    resp = page.request.get(f"{origin}/api/health")
    assert resp.status == 200
    assert resp.headers.get("x-mock") == "true", "Mock API not active"


def get_api_json(page: Page, mock_base_url: str, path: str) -> dict:
    """Fetch a mock API endpoint and return parsed JSON."""
    origin = api_origin(mock_base_url)
    resp = page.request.get(f"{origin}{path}")
    assert resp.status == 200, f"API {path} returned {resp.status}"
    return resp.json()


def post_api_json(page: Page, mock_base_url: str, path: str, data: dict = None) -> dict:
    """POST to a mock API endpoint and return parsed JSON.

    Uses ``data`` for JSON-serialised body so the mock plugin's
    ``readBody → JSON.parse()`` can parse it correctly.
    """
    origin = api_origin(mock_base_url)
    if data is not None:
        import json as _json
        resp = page.request.post(
            f"{origin}{path}",
            data=_json.dumps(data),
            headers={"Content-Type": "application/json"},
        )
    else:
        resp = page.request.post(f"{origin}{path}")
    return resp.json()


def main_text(page: Page) -> str:
    """Get visible text from main content area."""
    main = page.locator("main, [role=\"main\"], .mantine-AppShell-main").first
    return main.inner_text() if main.count() > 0 else page.locator("body").inner_text()
