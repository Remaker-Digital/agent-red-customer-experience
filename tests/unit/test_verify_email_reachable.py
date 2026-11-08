# tests/unit/test_verify_email_reachable.py: the emailed verification link reaches its handler
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
``GET /api/auth/verify-email/confirm`` is opened from an email, with no credentials, but its
path was missing from ``AUTH_EXEMPT_PREFIXES``, so in the composed application it returned
401 before its handler ran. The route's own tests used a bare ``FastAPI()`` and missed it.

Only the confirm link is exempt. ``POST /api/auth/verify-email/request`` takes the tenant id
from its body, so it stays behind authentication (Codex review of #144); being public, the
confirm link limits attempts per client IP before it reads the token store.

Architecture review F1 (docs/agent-red-architecture-review.md); see also RC-5.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.multi_tenant.auth import is_auth_exempt

CONFIRM = "/api/auth/verify-email/confirm"


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch):
    """A clean rate-limit backend per test (process-wide singleton)."""
    import src.multi_tenant.security_hardening as hardening

    original = hardening.get_rate_limit_backend()
    hardening.set_rate_limit_backend(hardening.InMemoryRateLimitBackend())
    monkeypatch.setattr("src.multi_tenant.email_verification._local_confirm_attempts", None, raising=False)
    yield
    hardening.set_rate_limit_backend(original)


@pytest.fixture
def no_such_token():
    consume = AsyncMock(return_value=None)
    with patch("src.multi_tenant.repositories.verification.VerificationTokenRepository.consume_token", consume):
        yield consume


def test_the_emailed_confirm_link_reaches_its_handler(app_client, no_such_token):
    response = app_client.get(CONFIRM, params={"token": "a" * 43})
    assert response.status_code == 400  # the handler's "invalid or expired link" page, not 401
    assert "invalid" in response.text


def test_the_verification_request_still_needs_credentials(app_client):
    """It takes the tenant id from its body, so it must not be public (Codex review of #144)."""
    response = app_client.post(
        "/api/auth/verify-email/request", json={"tenant_id": "any-tenant", "email": "someone@example.com"}
    )
    assert response.status_code == 401


def test_the_exemption_covers_only_the_confirm_link():
    assert is_auth_exempt(CONFIRM)
    assert not is_auth_exempt("/api/auth/verify-email/request")
    assert not is_auth_exempt("/api/auth/verify-emailer")


def test_confirm_attempts_are_limited_per_client_ip(app_client, no_such_token, monkeypatch):
    """A public route that reads the token store must not be unbounded (Codex review of #144)."""
    monkeypatch.setattr("src.multi_tenant.email_verification._CONFIRM_MAX", 2)
    for _ in range(2):
        assert app_client.get(CONFIRM, params={"token": "b" * 43}).status_code == 400
    assert app_client.get(CONFIRM, params={"token": "b" * 43}).status_code == 429
    assert no_such_token.await_count == 2  # the refused attempt never reached the store


class _UnreachableRedis:
    """A shared rate-limit backend whose store has gone away after startup."""

    def is_limited(self, key, *, max_requests, window_seconds):
        raise ConnectionError("Redis unreachable")


def test_confirm_attempts_are_counted_locally_when_the_shared_backend_fails(app_client, no_such_token, monkeypatch):
    """A Redis outage must neither break the emailed link nor lift its limit."""
    import src.multi_tenant.security_hardening as hardening

    hardening.set_rate_limit_backend(_UnreachableRedis())
    monkeypatch.setattr("src.multi_tenant.email_verification._CONFIRM_MAX", 2)
    for _ in range(2):
        assert app_client.get(CONFIRM, params={"token": "c" * 43}).status_code == 400  # reaches the handler
    assert app_client.get(CONFIRM, params={"token": "c" * 43}).status_code == 429  # still limited, counted locally


def test_the_confirmation_page_escapes_the_stored_address(app_client):
    """The link is public, so a crafted display-name address must not run script on this origin (Codex review of #144)."""
    crafted = '"<img src=x onerror=alert(1)>" <attacker@example.com>'
    token = {"tenant_id": "tenant-1", "email": crafted}
    with (
        patch(
            "src.multi_tenant.repositories.verification.VerificationTokenRepository.consume_token",
            AsyncMock(return_value=token),
        ),
        patch("src.multi_tenant.repositories.PreferencesRepository.read", AsyncMock(return_value={"id": "tenant-1"})),
        patch("src.multi_tenant.repositories.PreferencesRepository.patch", AsyncMock()),
    ):
        response = app_client.get(CONFIRM, params={"token": "d" * 43})
    assert response.status_code == 200
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text
