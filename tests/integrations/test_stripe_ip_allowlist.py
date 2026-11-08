"""Stripe webhook IP allowlisting tests (WI #162).

Tests the defense-in-depth IP allowlisting for the Stripe webhook endpoint.
Validates that:
    - IP check is bypassed when disabled (default)
    - Known Stripe IPs are accepted when enabled
    - Unknown IPs are rejected with 403 when enabled
    - X-Forwarded-For is correctly parsed for reverse proxy scenarios
    - Localhost is always allowed (Stripe CLI development)
    - The IP allowlist contains expected Stripe ranges

Run:
    pytest tests/integrations/test_stripe_ip_allowlist.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from src.integrations.stripe_webhooks import (
    STRIPE_WEBHOOK_IP_RANGES,
    _ALLOWED_IPS,
    _check_stripe_ip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    client_host: str = "127.0.0.1",
    forwarded_for: str | None = None,
) -> MagicMock:
    """Create a mock Request with configurable IP address."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_host
    headers = {}
    if forwarded_for is not None:
        headers["x-forwarded-for"] = forwarded_for
    request.headers = headers
    return request


# ===========================================================================
# IP Allowlist Constants
# ===========================================================================


class TestIPAllowlistConstants:
    """Validate the IP allowlist configuration."""

    def test_stripe_ip_ranges_not_empty(self):
        """Stripe IP ranges list contains at least one entry."""
        assert len(STRIPE_WEBHOOK_IP_RANGES) > 0

    def test_stripe_ip_ranges_are_valid_ips(self):
        """All entries in STRIPE_WEBHOOK_IP_RANGES look like valid IPv4 addresses."""
        import ipaddress

        for ip in STRIPE_WEBHOOK_IP_RANGES:
            # Should not raise ValueError
            parsed = ipaddress.ip_address(ip)
            assert parsed.version == 4

    def test_allowed_ips_includes_stripe_ranges(self):
        """The _ALLOWED_IPS set includes all entries from STRIPE_WEBHOOK_IP_RANGES."""
        for ip in STRIPE_WEBHOOK_IP_RANGES:
            assert ip in _ALLOWED_IPS

    def test_allowed_ips_includes_localhost(self):
        """Localhost addresses are always in the allowlist."""
        assert "127.0.0.1" in _ALLOWED_IPS
        assert "::1" in _ALLOWED_IPS
        assert "localhost" in _ALLOWED_IPS

    def test_allowed_ips_count(self):
        """Total allowed IPs = Stripe ranges + localhost entries."""
        localhost_count = 3  # 127.0.0.1, ::1, localhost
        assert len(_ALLOWED_IPS) == len(set(STRIPE_WEBHOOK_IP_RANGES)) + localhost_count


# ===========================================================================
# IP Check — Disabled (default)
# ===========================================================================


class TestIPCheckDisabled:
    """When STRIPE_IP_ALLOWLIST_ENABLED is false (default), all IPs pass."""

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", False)
    def test_any_ip_allowed_when_disabled(self):
        """Any IP address passes when allowlisting is disabled."""
        request = _make_request(client_host="1.2.3.4")
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", False)
    def test_unknown_ip_allowed_when_disabled(self):
        """Unknown IP addresses pass when allowlisting is disabled."""
        request = _make_request(client_host="10.99.99.99")
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", False)
    def test_forwarded_for_ignored_when_disabled(self):
        """X-Forwarded-For header is ignored when allowlisting is disabled."""
        request = _make_request(
            client_host="127.0.0.1",
            forwarded_for="10.99.99.99",
        )
        assert _check_stripe_ip(request) is True


# ===========================================================================
# IP Check — Enabled
# ===========================================================================


class TestIPCheckEnabled:
    """When STRIPE_IP_ALLOWLIST_ENABLED is true, only known IPs pass."""

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_stripe_ip_accepted(self):
        """Known Stripe IP is accepted."""
        stripe_ip = STRIPE_WEBHOOK_IP_RANGES[0]
        request = _make_request(client_host=stripe_ip)
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_all_stripe_ips_accepted(self):
        """All Stripe IPs in the published list are accepted."""
        for ip in STRIPE_WEBHOOK_IP_RANGES:
            request = _make_request(client_host=ip)
            assert _check_stripe_ip(request) is True, f"Stripe IP {ip} was rejected"

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_unknown_ip_rejected(self):
        """Unknown IP is rejected."""
        request = _make_request(client_host="10.99.99.99")
        assert _check_stripe_ip(request) is False

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_localhost_ipv4_accepted(self):
        """Localhost IPv4 (127.0.0.1) is always accepted for Stripe CLI."""
        request = _make_request(client_host="127.0.0.1")
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_localhost_ipv6_accepted(self):
        """Localhost IPv6 (::1) is always accepted."""
        request = _make_request(client_host="::1")
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_localhost_name_accepted(self):
        """Localhost by name is always accepted."""
        request = _make_request(client_host="localhost")
        assert _check_stripe_ip(request) is True


# ===========================================================================
# X-Forwarded-For handling
# ===========================================================================


class TestXForwardedFor:
    """The allowlist uses the client address TrustedProxyMiddleware resolved, never the raw header.

    The header's leftmost entries are client-supplied: on Azure Container Apps a caller can send
    ``X-Forwarded-For: <Stripe IP>`` and the ingress appends the real address after it
    (architecture review S18; RC-17).
    """

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_a_spoofed_leftmost_stripe_address_is_rejected(self):
        stripe_ip = STRIPE_WEBHOOK_IP_RANGES[0]
        request = _make_request(
            client_host="10.99.99.99",  # resolved by TrustedProxyMiddleware from the trusted end
            forwarded_for=f"{stripe_ip}, 10.99.99.99",
        )
        assert _check_stripe_ip(request) is False

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_the_resolved_client_address_is_used(self):
        stripe_ip = STRIPE_WEBHOOK_IP_RANGES[0]
        request = _make_request(client_host=stripe_ip, forwarded_for="10.99.99.99")
        assert _check_stripe_ip(request) is True

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_an_unknown_resolved_address_is_rejected(self):
        request = _make_request(client_host="10.99.99.99")
        assert _check_stripe_ip(request) is False

    @patch("src.integrations.stripe_webhooks._ENABLE_IP_ALLOWLIST", True)
    def test_no_client_ip_rejected(self):
        """Request with no client info is rejected when allowlist enabled."""
        request = MagicMock()
        request.client = None
        request.headers = {}
        assert _check_stripe_ip(request) is False
