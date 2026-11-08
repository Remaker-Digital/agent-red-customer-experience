# tests/unit/test_trusted_proxy_hops.py: the client IP comes from the trusted end of X-Forwarded-For
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
TrustedProxyMiddleware's x-forwarded-for mode used the leftmost X-Forwarded-For entry. Azure
Container Apps ingress appends the address it saw to any value the client sent, and only the
rightmost entry is its own (https://learn.microsoft.com/azure/container-apps/ingress-overview#http-headers),
so a caller could prepend a fresh address per request and dodge every per-IP limit, or aim a block
at a victim. The middleware now trusts the entry TRUSTED_PROXY_HOPS positions from the right
(default 1), and the Stripe webhook allowlist uses the address it resolved.

Architecture review S17 and S18 (Codex reviews of #143 and #145); the SPEC-1663 change is RC-17.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.multi_tenant.security_middleware import (
    TRUSTED_PROXY_FORWARDED_FOR,
    TrustedProxyMiddleware,
    _is_valid_ip,
)


async def _resolved(header: str | list[str], *, hops: int | None = None) -> str:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]

    lines = [header] if isinstance(header, str) else header
    scope = {
        "type": "http",
        "client": ("10.0.0.1", 5000),
        "headers": [(b"x-forwarded-for", line.encode()) for line in lines],
    }
    await TrustedProxyMiddleware(app, mode=TRUSTED_PROXY_FORWARDED_FOR, hops=hops)(scope, AsyncMock(), AsyncMock())
    return seen["client"][0]


@pytest.mark.parametrize("spoofed", ["6.6.6.6", "7.7.7.7", "198.51.100.200"])
async def test_a_client_supplied_entry_never_becomes_the_client(spoofed):
    assert await _resolved(f"{spoofed}, 203.0.113.9") == "203.0.113.9"


async def test_hops_skip_trusted_proxies_from_the_right():
    assert await _resolved("6.6.6.6, 198.51.100.7, 147.243.1.1", hops=2) == "198.51.100.7"


async def test_too_few_entries_leave_the_peer_address():
    assert await _resolved("203.0.113.9", hops=2) == "10.0.0.1"


async def test_an_invalid_trusted_entry_leaves_the_peer_address():
    assert await _resolved("203.0.113.9, not-an-address") == "10.0.0.1"


async def test_repeated_header_lines_are_one_list():
    assert await _resolved(["9.9.9.9", "6.6.6.6, 203.0.113.7"]) == "203.0.113.7"


@pytest.mark.parametrize(("raw", "expected"), [("", 1), ("3", 3), ("0", 1), ("-2", 1), ("two", 1)])
def test_hops_from_the_environment(monkeypatch, raw, expected):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", raw)
    assert TrustedProxyMiddleware(AsyncMock(), mode=TRUSTED_PROXY_FORWARDED_FOR).hops == expected


@pytest.mark.parametrize("hops", [0, -1, -5, True])
async def test_an_explicit_hop_count_below_one_is_normalized(hops):
    """hops=0 would index the leftmost, client-supplied entry (Codex review of #145)."""
    middleware = TrustedProxyMiddleware(AsyncMock(), mode=TRUSTED_PROXY_FORWARDED_FOR, hops=hops)
    assert middleware.hops == 1
    assert await _resolved("6.6.6.6, 203.0.113.9", hops=hops) == "203.0.113.9"


@pytest.mark.parametrize("value", ["999.999.999.999", "dead", "::zz", "1.2.3", "203.0.113.9; DROP"])
def test_malformed_addresses_are_rejected(value):
    assert _is_valid_ip(value) is False


@pytest.mark.parametrize("value", ["203.0.113.9", "2001:db8::42", "::1"])
def test_addresses_are_accepted(value):
    assert _is_valid_ip(value) is True


async def test_a_spoofed_stripe_address_does_not_pass_the_webhook_allowlist():
    """End to end: the allowlist sees the address the middleware resolved (Codex review of #145)."""
    from starlette.requests import Request

    from src.integrations.stripe_webhooks import STRIPE_WEBHOOK_IP_RANGES, _check_stripe_ip

    stripe_ip = STRIPE_WEBHOOK_IP_RANGES[0]
    checked: dict = {}

    async def app(scope, receive, send):
        checked["allowed"] = _check_stripe_ip(Request(scope))

    async def through_middleware(header: str) -> bool:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/webhooks/stripe",
            "client": ("10.0.0.1", 5000),
            "headers": [(b"x-forwarded-for", header.encode())],
        }
        await TrustedProxyMiddleware(app, mode=TRUSTED_PROXY_FORWARDED_FOR)(scope, AsyncMock(), AsyncMock())
        return checked["allowed"]

    with patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True):
        assert await through_middleware(f"{stripe_ip}, 198.51.100.66") is False  # caller prepended Stripe's address
        assert await through_middleware(stripe_ip) is True  # Stripe's own request; the ingress appended it
