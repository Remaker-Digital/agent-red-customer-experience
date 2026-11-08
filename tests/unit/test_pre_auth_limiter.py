# tests/unit/test_pre_auth_limiter.py: repeated failed sign-ins block the client IP
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
Failed authentications were recorded against the client IP, but nothing ever checked the
count: ``PreAuthRateLimitMiddleware`` was never registered and ``is_blocked`` had no other
caller. The auth-exempt key check (``/api/tenants/auth/validate-key``) and the standalone
admin password gate recorded nothing at all.

The middleware is now registered (inside TrustedProxy, outside authentication), and the two
exempt endpoints count failures and refuse blocked clients themselves.

Architecture review S9 (docs/agent-red-architecture-review.md). The tests run through the
composed application (``app_client``) with a limiter that blocks after three failures.
"""

from __future__ import annotations

import pytest

from tests.conftest import STARTER_TENANT_ID, TEST_API_KEY_STARTER

LIMIT = 3


@pytest.fixture
def small_limiter(monkeypatch):
    import src.multi_tenant.security_hardening as hardening

    limiter = hardening.PreAuthRateLimiter(max_attempts=LIMIT)
    monkeypatch.setattr(hardening, "_pre_auth_limiter", limiter)
    return limiter


def _config(app_client, key: str):
    return app_client.get("/api/config", params={"tenant": STARTER_TENANT_ID}, headers={"X-API-Key": key})


def test_failed_sign_ins_block_the_client_ip(app_client, small_limiter):
    for _ in range(LIMIT + 1):
        assert _config(app_client, "ar_live_not_a_real_key").status_code in (401, 429)
    blocked = _config(app_client, TEST_API_KEY_STARTER)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_a_valid_key_is_not_blocked_by_default(app_client, small_limiter):
    assert _config(app_client, TEST_API_KEY_STARTER).status_code != 429


class _NoMatchStore:
    async def verify_key_hash(self, tenant_id, key_hash):
        return None


def test_failed_key_checks_block_the_client_ip(app_client, small_limiter, monkeypatch):
    import src.integrations.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_tenant_repo", _NoMatchStore())

    def check(key: str):
        return app_client.post(
            "/api/tenants/auth/validate-key", json={"tenant": STARTER_TENANT_ID}, headers={"X-API-Key": key}
        )

    for _ in range(LIMIT + 1):
        assert check("ar_live_not_a_real_key").status_code in (401, 429)
    blocked = check(TEST_API_KEY_STARTER)
    assert blocked.status_code == 429
    # The contract the standalone key login reads (ApiKeyLogin.tsx).
    from src.multi_tenant.security_hardening import AUTH_BLOCK_DURATION_SECONDS

    assert blocked.headers["Retry-After"] == str(AUTH_BLOCK_DURATION_SECONDS)
    assert blocked.json()["detail"].startswith("Too many failed authentication attempts")


def test_wrong_admin_passwords_block_the_client_ip(app_client, small_limiter, monkeypatch):
    import src.app.standalone_auth as standalone

    monkeypatch.setattr(standalone, "_admin_password_hash", standalone._hash_password("the-right-password"))
    app_client.cookies.set("agentred_csrf", "csrf-token")

    def sign_in(password: str):
        return app_client.post(
            "/admin/standalone/_auth",
            data={"password": password, "csrf_token": "csrf-token"},
            follow_redirects=False,
        )

    for _ in range(LIMIT + 1):
        assert sign_in("wrong").status_code in (403, 429)
    assert sign_in("the-right-password").status_code == 429


def test_other_successful_requests_do_not_reset_password_failures(app_client, small_limiter, monkeypatch):
    """A public widget key or any valid key must not clear the password count (Codex review of #143)."""
    import src.app.standalone_auth as standalone

    monkeypatch.setattr(standalone, "_admin_password_hash", standalone._hash_password("the-right-password"))
    app_client.cookies.set("agentred_csrf", "csrf-token")

    def sign_in(password: str):
        return app_client.post(
            "/admin/standalone/_auth",
            data={"password": password, "csrf_token": "csrf-token"},
            follow_redirects=False,
        )

    for _ in range(LIMIT + 1):
        assert sign_in("wrong").status_code in (403, 429)
        assert _config(app_client, TEST_API_KEY_STARTER).status_code == 200  # a success from the same IP
    assert sign_in("the-right-password").status_code == 429


class _UnavailableStore:
    async def verify_key_hash(self, tenant_id, key_hash):
        raise ConnectionError("Cosmos unavailable")


def test_a_key_store_outage_is_not_a_failed_key_check(app_client, small_limiter, monkeypatch):
    """An outage answers 503 and counts nothing against the caller (Codex review of #143)."""
    import src.integrations.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_tenant_repo", _UnavailableStore())
    response = app_client.post(
        "/api/tenants/auth/validate-key", json={"tenant": STARTER_TENANT_ID}, headers={"X-API-Key": "ar_live_any"}
    )
    assert response.status_code == 503
    assert not small_limiter._trackers


def _admin_sign_in(app_client, monkeypatch):
    import src.app.standalone_auth as standalone

    monkeypatch.setattr(standalone, "_admin_password_hash", standalone._hash_password("the-right-password"))
    app_client.cookies.set("agentred_csrf", "csrf-token")

    def sign_in(password: str):
        return app_client.post(
            "/admin/standalone/_auth",
            data={"password": password, "csrf_token": "csrf-token"},
            follow_redirects=False,
        )

    return sign_in


def test_exempt_ips_keep_their_exemption_at_the_password_gate(app_client, monkeypatch):
    """PRE_AUTH_RATE_LIMIT_EXEMPT_IPS lists raw IPs; the gate's own key must honor them (Codex review of #143)."""
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(
        hardening,
        "_pre_auth_limiter",
        hardening.PreAuthRateLimiter(max_attempts=LIMIT, exempt_ips=frozenset({"testclient"})),
    )
    sign_in = _admin_sign_in(app_client, monkeypatch)
    for _ in range(LIMIT + 1):
        assert sign_in("wrong").status_code == 403
    assert sign_in("the-right-password").status_code == 303


def test_service_side_authentication_errors_are_not_counted(app_client, small_limiter, monkeypatch):
    """A 5xx AuthenticationError is a configuration or store failure, not a guess (Codex review of #143).

    Unexpected exceptions still count (tests/unit/test_code_quality_s132.py); that choice is parked as RC-14.
    """
    from src.multi_tenant.auth import AuthenticationError
    from src.multi_tenant.middleware import TenantAuthMiddleware

    async def failing(self, request):
        raise AuthenticationError("Widget key resolution not configured.", status_code=500)

    monkeypatch.setattr(TenantAuthMiddleware, "_authenticate", failing)
    for _ in range(LIMIT + 1):
        assert _config(app_client, TEST_API_KEY_STARTER).status_code == 500
    assert not small_limiter._trackers


def test_the_blocked_page_pairs_its_form_token_with_a_cookie(app_client, small_limiter, monkeypatch):
    """Otherwise the first sign-in after the cooldown fails CSRF validation (Codex review of #143)."""
    import re

    sign_in = _admin_sign_in(app_client, monkeypatch)
    for _ in range(LIMIT):
        sign_in("wrong")
    blocked = sign_in("wrong")
    assert blocked.status_code == 429
    form_token = re.search(r'name="csrf_token" value="([^"]+)"', blocked.text).group(1)
    assert blocked.cookies.get("agentred_csrf") == form_token


class _OneKeyStore:
    """Finds a tenant only for the one key whose hash it holds."""

    def __init__(self, key: str):
        import hashlib

        self._hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    async def verify_key_hash(self, tenant_id, key_hash):
        return {"id": tenant_id, "tenant_id": tenant_id} if key_hash == self._hash else None


def test_a_valid_key_clears_earlier_key_check_failures(app_client, small_limiter, monkeypatch):
    """As the authentication middleware does, so earlier typos cannot lock out a later sign-in (Codex review of #143)."""
    import src.integrations.provisioning as provisioning

    monkeypatch.setattr(provisioning, "_tenant_repo", _OneKeyStore("ar_live_the_real_key"))

    def check(key: str):
        return app_client.post(
            "/api/tenants/auth/validate-key", json={"tenant": STARTER_TENANT_ID}, headers={"X-API-Key": key}
        )

    for _ in range(LIMIT - 1):
        assert check("ar_live_a_typo").status_code == 401
    assert check("ar_live_the_real_key").status_code == 200
    for _ in range(LIMIT - 1):
        assert check("ar_live_a_typo").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://attacker.example"}], ids=["bare", "cross-site"])
def test_requests_without_a_credential_are_not_counted(app_client, small_limiter, headers):
    """Any page can make a visitor's browser send these, so they must not block the visitor (Codex review of #143)."""
    for _ in range(LIMIT + 1):
        assert app_client.get("/api/config", params={"tenant": STARTER_TENANT_ID}, headers=headers).status_code == 401
    assert not small_limiter._trackers
    assert _config(app_client, TEST_API_KEY_STARTER).status_code == 200


@pytest.mark.parametrize("param", ["api_key", "session_token", "widget_key", "key"])
def test_a_refused_query_credential_is_not_counted(app_client, small_limiter, param):
    """Any page can put a credential in a cross-site URL without a preflight (Codex review of #143)."""
    for _ in range(LIMIT + 1):
        app_client.get("/api/config", params={"tenant": STARTER_TENANT_ID, param: "not_a_real_credential"})
    assert not small_limiter._trackers
    assert _config(app_client, TEST_API_KEY_STARTER).status_code == 200


def test_a_refused_header_credential_still_counts(app_client, small_limiter):
    """A header credential needs a CORS preflight cross-site; the residual from allowed origins is RC-16."""
    for _ in range(LIMIT):
        app_client.get("/api/config", params={"tenant": STARTER_TENANT_ID}, headers={"X-API-Key": "ar_live_nope"})
    assert small_limiter.is_blocked("testclient")


@pytest.mark.parametrize(
    ("environment", "proxy", "expected"),
    [
        ("production", "", False),
        ("staging", "", False),
        ("production", "bogus", False),
        ("production", "x-forwarded-for", True),
        ("staging", "cloudflare", True),
        ("development", "", True),
        (None, "", True),
    ],
)
def test_client_ip_is_per_client(monkeypatch, environment, proxy, expected):
    from src.multi_tenant.security_hardening import client_ip_is_per_client

    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("TRUSTED_PROXY", proxy)
    assert client_ip_is_per_client() is expected


def test_without_a_per_client_address_blocks_are_recorded_not_enforced(monkeypatch):
    """Behind the ingress with no TRUSTED_PROXY every request shares one address (commit 67282b64)."""
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    limiter = hardening.PreAuthRateLimiter(max_attempts=LIMIT)
    assert limiter.enforcing is False
    for _ in range(LIMIT):
        limiter.record_failure("100.100.0.1")
    assert limiter.is_blocked("100.100.0.1") is False
    assert limiter.is_blocked("100.100.0.1", enforce=True) is True


def test_a_record_only_limiter_lets_valid_keys_through(app_client, monkeypatch):
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(
        hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter(max_attempts=LIMIT, enforcing=False)
    )
    for _ in range(LIMIT + 1):
        assert _config(app_client, "ar_live_not_a_real_key").status_code == 401
    assert _config(app_client, TEST_API_KEY_STARTER).status_code == 200


def test_the_password_gate_enforces_even_when_ip_blocks_are_record_only(app_client, monkeypatch):
    """A password is low-entropy; with no usable client address the guess budget is shared, not removed."""
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(
        hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter(max_attempts=LIMIT, enforcing=False)
    )
    sign_in = _admin_sign_in(app_client, monkeypatch)
    for _ in range(LIMIT + 1):
        assert sign_in("wrong").status_code in (403, 429)
    assert sign_in("the-right-password").status_code == 429


def test_the_api_key_sign_in_explains_a_lockout():
    """The standalone key login must not show a lockout as "Server error (429)" (Codex review of #143)."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "admin" / "standalone" / "login" / "ApiKeyLogin.tsx").read_text(
        encoding="utf-8"
    )
    match = re.search(r"const handleLogin.*?(?=\n  const |\n  /\*)", source, re.DOTALL)
    assert match, "handleLogin not found"
    body = match.group(0)
    assert "resp.status === 429" in body, "handleLogin has no 429 branch"
    assert body.index("resp.status === 429") < body.index("Server error ("), "429 is handled before the fallback"
    assert "Retry-After" in body and "detail" in body and "minute" in body
