"""Tests for TrustedProxyMiddleware (SPEC-1663).

Verifies that reverse proxy headers (Cloudflare CF-Connecting-IP,
generic X-Forwarded-For) are correctly extracted and applied to
the ASGI scope so downstream middleware sees the real client IP.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from src.multi_tenant.security_middleware import (
    TrustedProxyMiddleware,
    _is_valid_ip,
    TRUSTED_PROXY_CLOUDFLARE,
    TRUSTED_PROXY_FORWARDED_FOR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(
    *,
    client: tuple[str, int] = ("10.0.0.1", 12345),
    headers: dict[str, str] | None = None,
    scope_type: str = "http",
) -> dict:
    """Build a minimal ASGI scope with optional headers."""
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))
    return {
        "type": scope_type,
        "client": client,
        "headers": raw_headers,
        "path": "/api/test",
    }


async def _passthrough_app(scope, receive, send):
    """ASGI app that records the scope it sees."""
    # Store on the scope itself for test inspection
    scope["_test_seen"] = True


# ---------------------------------------------------------------------------
# TestIsValidIP
# ---------------------------------------------------------------------------


class TestIsValidIP:
    """Test the _is_valid_ip() validation helper."""

    def test_valid_ipv4(self):
        assert _is_valid_ip("192.168.1.1") is True

    def test_valid_ipv4_localhost(self):
        assert _is_valid_ip("127.0.0.1") is True

    def test_valid_ipv6_loopback(self):
        assert _is_valid_ip("::1") is True

    def test_valid_ipv6_full(self):
        assert _is_valid_ip("2001:db8::1") is True

    def test_empty_string(self):
        assert _is_valid_ip("") is False

    def test_hostname(self):
        assert _is_valid_ip("example.com") is False

    def test_too_long(self):
        assert _is_valid_ip("x" * 46) is False

    def test_injection_attempt(self):
        assert _is_valid_ip("192.168.1.1; DROP TABLE") is False

    def test_path_traversal(self):
        assert _is_valid_ip("../../../etc/passwd") is False

    def test_html_injection(self):
        assert _is_valid_ip("<script>alert(1)</script>") is False

    @pytest.mark.parametrize("value", ["999.999.999.999", "dead", "::zz", "1.2.3"])
    def test_malformed_addresses_are_rejected(self, value):
        """The check parses the address rather than matching a loose pattern (S17)."""
        assert _is_valid_ip(value) is False


# ---------------------------------------------------------------------------
# TestTrustedProxyMiddleware — disabled mode
# ---------------------------------------------------------------------------


class TestTrustedProxyDisabled:
    """When TRUSTED_PROXY is unset, middleware is a no-op passthrough."""

    @pytest.mark.asyncio
    async def test_no_mode_passthrough(self):
        """Middleware does not modify scope when mode is empty."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"cf-connecting-ip": "203.0.113.42"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode="")
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_invalid_mode_logged_and_disabled(self):
        """Invalid mode value is logged and treated as disabled."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"cf-connecting-ip": "203.0.113.42"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode="invalid-mode")
        assert mw.mode == ""
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_non_http_passthrough(self):
        """Non-HTTP scopes (websocket, lifespan) pass through unchanged."""
        scope = _make_scope(scope_type="lifespan")
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)


# ---------------------------------------------------------------------------
# TestTrustedProxyMiddleware — Cloudflare mode
# ---------------------------------------------------------------------------


class TestCloudflareMode:
    """Test CF-Connecting-IP extraction in Cloudflare mode."""

    @pytest.mark.asyncio
    async def test_cf_connecting_ip_applied(self):
        """Real client IP extracted from CF-Connecting-IP header."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"cf-connecting-ip": "203.0.113.42"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("203.0.113.42", 12345)

    @pytest.mark.asyncio
    async def test_cf_ipv6(self):
        """IPv6 address extracted correctly."""
        scope = _make_scope(
            client=("10.0.0.1", 0),
            headers={"cf-connecting-ip": "2001:db8::1"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("2001:db8::1", 0)

    @pytest.mark.asyncio
    async def test_cf_country_stored(self):
        """CF-IPCountry header stored in scope state."""
        scope = _make_scope(
            client=("10.0.0.1", 0),
            headers={
                "cf-connecting-ip": "203.0.113.1",
                "cf-ipcountry": "US",
            },
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["state"]["cf_ipcountry"] == "US"

    @pytest.mark.asyncio
    async def test_cf_ray_stored(self):
        """CF-Ray header stored in scope state."""
        scope = _make_scope(
            client=("10.0.0.1", 0),
            headers={
                "cf-connecting-ip": "203.0.113.1",
                "cf-ray": "abc123-IAD",
            },
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["state"]["cf_ray"] == "abc123-IAD"

    @pytest.mark.asyncio
    async def test_cf_missing_header_no_change(self):
        """Without CF-Connecting-IP, scope is unchanged."""
        scope = _make_scope(client=("10.0.0.1", 12345))
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_cf_invalid_ip_ignored(self):
        """Invalid CF-Connecting-IP value is ignored (not applied)."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"cf-connecting-ip": "not-an-ip"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_cf_injection_attempt_blocked(self):
        """Injection attempt in CF-Connecting-IP is rejected."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"cf-connecting-ip": "192.168.1.1; DROP TABLE users"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        # IP should NOT be applied — injection detected
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_cf_whitespace_trimmed(self):
        """Whitespace around IP value is trimmed."""
        scope = _make_scope(
            client=("10.0.0.1", 0),
            headers={"cf-connecting-ip": "  203.0.113.5  "},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_CLOUDFLARE)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("203.0.113.5", 0)


# ---------------------------------------------------------------------------
# TestTrustedProxyMiddleware — X-Forwarded-For mode
# ---------------------------------------------------------------------------


class TestXForwardedForMode:
    """Test X-Forwarded-For extraction in generic proxy mode."""

    @pytest.mark.asyncio
    async def test_single_ip(self):
        """Single IP in X-Forwarded-For is extracted."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"x-forwarded-for": "198.51.100.23"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("198.51.100.23", 12345)

    @pytest.mark.asyncio
    async def test_multiple_ips_rightmost_used(self):
        """The rightmost entry is the one the trusted proxy appended (S17, RC-17).

        Azure Container Apps ingress appends the address it saw to any
        X-Forwarded-For the client sent; only the rightmost entry is its own.
        """
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"x-forwarded-for": "198.51.100.1, 10.0.0.5, 203.0.113.9"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("203.0.113.9", 12345)

    @pytest.mark.asyncio
    async def test_client_supplied_entries_are_ignored(self):
        """A caller who prepends a fresh address per request cannot change its key (Codex review of #143)."""
        for spoofed in ("6.6.6.6", "7.7.7.7", "198.51.100.200"):
            scope = _make_scope(
                client=("10.0.0.1", 12345),
                headers={"x-forwarded-for": f"{spoofed}, 203.0.113.9"},
            )
            mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
            await mw(scope, AsyncMock(), AsyncMock())
            assert scope["client"] == ("203.0.113.9", 12345)

    @pytest.mark.asyncio
    async def test_hops_counts_trusted_proxies_from_the_right(self):
        """With a proxy in front of the ingress, TRUSTED_PROXY_HOPS=2 skips that proxy's entry."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"x-forwarded-for": "6.6.6.6, 198.51.100.7, 147.243.1.1"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR, hops=2)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("198.51.100.7", 12345)

    @pytest.mark.asyncio
    async def test_fewer_entries_than_hops_leaves_the_client_unchanged(self):
        """Never fall back to a client-supplied entry."""
        scope = _make_scope(client=("10.0.0.1", 12345), headers={"x-forwarded-for": "203.0.113.9"})
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR, hops=2)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_repeated_header_lines_are_read_in_order(self):
        """Several X-Forwarded-For lines form one list; the last line's last entry is the trusted one."""
        scope = _make_scope(client=("10.0.0.1", 12345))
        scope["headers"] = [(b"x-forwarded-for", b"9.9.9.9"), (b"x-forwarded-for", b"203.0.113.7")]
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("203.0.113.7", 12345)

    @pytest.mark.parametrize(("raw", "expected"), [("", 1), ("2", 2), ("0", 1), ("-1", 1), ("abc", 1)])
    def test_hops_come_from_the_environment(self, monkeypatch, raw, expected):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", raw)
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        assert mw.hops == expected

    @pytest.mark.asyncio
    async def test_xff_missing_no_change(self):
        """Without X-Forwarded-For, scope is unchanged."""
        scope = _make_scope(client=("10.0.0.1", 12345))
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_invalid_rightmost_entry_is_ignored_without_fallback(self):
        """An invalid trusted entry leaves the client unchanged; the leftmost is never used."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"x-forwarded-for": "198.51.100.1, malicious.example.com"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("10.0.0.1", 12345)

    @pytest.mark.asyncio
    async def test_invalid_client_supplied_entry_does_not_matter(self):
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={"x-forwarded-for": "malicious.example.com, 203.0.113.9"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("203.0.113.9", 12345)

    @pytest.mark.asyncio
    async def test_xff_ipv6(self):
        """IPv6 address in X-Forwarded-For is extracted."""
        scope = _make_scope(
            client=("10.0.0.1", 0),
            headers={"x-forwarded-for": "2001:db8::42"},
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        assert scope["client"] == ("2001:db8::42", 0)

    @pytest.mark.asyncio
    async def test_xff_does_not_read_cf_headers(self):
        """In XFF mode, CF-Connecting-IP is NOT read."""
        scope = _make_scope(
            client=("10.0.0.1", 12345),
            headers={
                "cf-connecting-ip": "203.0.113.99",
                "x-forwarded-for": "198.51.100.1",
            },
        )
        mw = TrustedProxyMiddleware(_passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR)
        await mw(scope, AsyncMock(), AsyncMock())
        # Should use XFF, not CF-Connecting-IP
        assert scope["client"] == ("198.51.100.1", 12345)
        # CF metadata should NOT be in state
        assert "cf_ipcountry" not in scope.get("state", {})


# ---------------------------------------------------------------------------
# TestTrustedProxyMiddleware — env var configuration
# ---------------------------------------------------------------------------


class TestEnvVarConfig:
    """Test that TRUSTED_PROXY env var configures the middleware."""

    @pytest.mark.asyncio
    async def test_env_var_cloudflare(self):
        """TRUSTED_PROXY=cloudflare enables Cloudflare mode."""
        with patch.dict("os.environ", {"TRUSTED_PROXY": "cloudflare"}):
            mw = TrustedProxyMiddleware(_passthrough_app)
            assert mw.mode == TRUSTED_PROXY_CLOUDFLARE

    @pytest.mark.asyncio
    async def test_env_var_xff(self):
        """TRUSTED_PROXY=x-forwarded-for enables XFF mode."""
        with patch.dict("os.environ", {"TRUSTED_PROXY": "x-forwarded-for"}):
            mw = TrustedProxyMiddleware(_passthrough_app)
            assert mw.mode == TRUSTED_PROXY_FORWARDED_FOR

    @pytest.mark.asyncio
    async def test_env_var_empty(self):
        """Empty TRUSTED_PROXY disables the middleware."""
        with patch.dict("os.environ", {"TRUSTED_PROXY": ""}):
            mw = TrustedProxyMiddleware(_passthrough_app)
            assert mw.mode == ""

    @pytest.mark.asyncio
    async def test_env_var_not_set(self):
        """Missing TRUSTED_PROXY disables the middleware."""
        with patch.dict("os.environ", {}, clear=False):
            env = dict(os.environ)
            env.pop("TRUSTED_PROXY", None)
            with patch.dict("os.environ", env, clear=True):
                mw = TrustedProxyMiddleware(_passthrough_app)
                assert mw.mode == ""

    @pytest.mark.asyncio
    async def test_mode_param_overrides_env(self):
        """Explicit mode= parameter overrides TRUSTED_PROXY env var."""
        with patch.dict("os.environ", {"TRUSTED_PROXY": "cloudflare"}):
            mw = TrustedProxyMiddleware(
                _passthrough_app, mode=TRUSTED_PROXY_FORWARDED_FOR,
            )
            assert mw.mode == TRUSTED_PROXY_FORWARDED_FOR


# ---------------------------------------------------------------------------
# TestMiddlewareRegistration
# ---------------------------------------------------------------------------


class TestMiddlewareRegistration:
    """Verify TrustedProxyMiddleware is wired into the middleware stack."""

    def test_import_from_lifecycle(self):
        """TrustedProxyMiddleware is importable from lifecycle's imports."""
        from src.multi_tenant.security_middleware import TrustedProxyMiddleware as T
        assert T is not None

    def test_register_middleware_includes_trusted_proxy(self):
        """register_middleware() adds TrustedProxyMiddleware to the app."""
        from unittest.mock import MagicMock
        from src.app.lifecycle import register_middleware

        app = MagicMock()
        app.add_middleware = MagicMock()
        register_middleware(app)

        # Collect all middleware classes passed to add_middleware
        added_classes = [
            call.args[0] for call in app.add_middleware.call_args_list
        ]
        assert TrustedProxyMiddleware in added_classes


# Needed for env var test
import os  # noqa: E402
