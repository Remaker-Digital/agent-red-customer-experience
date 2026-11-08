# tests/unit/test_websocket_auth.py: the chat WebSocket authenticates before accepting
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
``/api/chat/ws/{conversation_id}`` accepted every connection: the HTTP authentication
middleware is a ``BaseHTTPMiddleware``, which passes WebSocket scopes through, and the
handler checked nothing. Anyone with a conversation id could join it, read what is
broadcast to it (including human-agent replies after escalation) and inject messages.

The handler now refuses the handshake (close code 1008) unless the widget key is valid
and the conversation belongs to the key's tenant.

Architecture review S4 (docs/agent-red-architecture-review.md). The tests run through
the composed application (``app_client``), with a stand-in conversation store holding
one Starter conversation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from starlette.websockets import WebSocketDisconnect

from src.chat.session import ConversationNotFoundError
from tests.conftest import PROFESSIONAL_TENANT_ID, STARTER_TENANT_ID, TEST_WIDGET_KEY

OWN = "conv-starter-own"
OTHER = "conv-professional"


class _Conversations:
    """Holds one conversation per tenant; anything else is not found."""

    owners = {OWN: STARTER_TENANT_ID, OTHER: PROFESSIONAL_TENANT_ID}

    async def get_conversation(self, tenant_id: str, conversation_id: str):
        if self.owners.get(conversation_id) != tenant_id:
            raise ConversationNotFoundError(conversation_id, tenant_id)
        return {"conversation_id": conversation_id}


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    """Each test starts with a clean pre-auth limiter and rate-limit backend (process-wide singletons)."""
    import src.multi_tenant.security_hardening as hardening

    limiter = hardening.PreAuthRateLimiter()
    monkeypatch.setattr(hardening, "_pre_auth_limiter", limiter)
    original_backend = hardening.get_rate_limit_backend()
    hardening.set_rate_limit_backend(hardening.InMemoryRateLimitBackend())
    monkeypatch.setattr("src.chat.endpoints._local_handshakes", None)
    yield limiter
    hardening.set_rate_limit_backend(original_backend)


@pytest.fixture(autouse=True)
def no_origin_restriction():
    """Handshakes check approved origins on the active preferences (S14, #139); none are set here."""
    no_origins = AsyncMock(return_value={"approved_widget_origins": []})
    with patch("src.multi_tenant.repositories.preferences.PreferencesRepository.get_active", no_origins):
        yield


@pytest.fixture
def conversations(monkeypatch):
    import src.chat.endpoints as endpoints

    monkeypatch.setattr(endpoints, "_session", _Conversations())


def _refused(app_client, url: str) -> int:
    with pytest.raises(WebSocketDisconnect) as refused:
        with app_client.websocket_connect(url):
            pass
    return refused.value.code


@pytest.mark.parametrize(
    "url",
    [
        f"/api/chat/ws/{OWN}",
        f"/api/chat/ws/{OWN}?widget_key=pk_live_not_a_real_key_0000",
        f"/api/chat/ws/{OTHER}?widget_key={TEST_WIDGET_KEY}",
        f"/api/chat/ws/conv-missing?widget_key={TEST_WIDGET_KEY}",
    ],
    ids=["no-key", "unknown-key", "other-tenants-conversation", "missing-conversation"],
)
def test_handshake_is_refused(app_client, conversations, url):
    assert _refused(app_client, url) == 1008


@pytest.mark.parametrize("param", ["widget_key", "key"])
def test_own_conversation_connects_and_relays(app_client, conversations, param):
    url = f"/api/chat/ws/{OWN}?{param}={TEST_WIDGET_KEY}"
    with app_client.websocket_connect(url) as first, app_client.websocket_connect(url) as second:
        first.send_json({"type": "typing_start", "data": {}})
        relayed = second.receive_json()
    assert relayed["conversation_id"] == OWN
    assert relayed["type"] == "typing_start"


def test_refused_when_chat_service_is_not_ready(app_client, monkeypatch):
    import src.chat.endpoints as endpoints

    monkeypatch.setattr(endpoints, "_session", None)
    assert _refused(app_client, f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}") == 1008


@pytest.mark.parametrize("message_type", ["agent_message", "system_event"])
def test_clients_cannot_send_staff_or_server_messages(app_client, conversations, message_type):
    """The widget shows an inbound agent_message as a reply from staff, so a client may not send one."""
    url = f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}"
    with app_client.websocket_connect(url) as sender, app_client.websocket_connect(url) as other:
        sender.send_json({"type": message_type, "data": {"text": "Your refund is approved, confirm your card"}})
        refusal = sender.receive_json()
        sender.send_json({"type": "typing_start", "data": {}})
        first_relayed = other.receive_json()
    assert refusal["type"] == "error"
    assert first_relayed["type"] == "typing_start"


def test_customers_can_send_human_messages(app_client, conversations):
    """After escalation the customer's own messages travel as human_message (Decision UI-4)."""
    url = f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}"
    with app_client.websocket_connect(url) as customer, app_client.websocket_connect(url) as other:
        customer.send_json({"type": "human_message", "data": {"text": "Still waiting on my order"}})
        relayed = other.receive_json()
    assert relayed["type"] == "human_message"


def test_refused_handshakes_do_not_feed_the_pre_auth_block(app_client, conversations, monkeypatch):
    """A WebSocket key travels in the query string, which any page can plant in a visitor's browser, so
    refusals must not block the visitor (Codex review of #143). Probing is bounded by the handshake cap."""
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter(max_attempts=2))
    for _ in range(3):
        assert _refused(app_client, f"/api/chat/ws/conv-guess?widget_key={TEST_WIDGET_KEY}") == 1008
    assert not hardening.get_pre_auth_limiter()._trackers
    with app_client.websocket_connect(f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}") as socket:
        socket.send_json({"type": "typing_start", "data": {}})


async def test_trusted_proxy_sets_the_client_ip_for_websockets():
    """Behind the ingress, the limiter must see each client's address, not the proxy's."""
    from src.multi_tenant.security_middleware import TrustedProxyMiddleware

    seen: dict = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]

    scope = {
        "type": "websocket",
        # The ingress appends the address it saw; the leftmost entry is client-supplied (S17).
        "headers": [(b"x-forwarded-for", b"10.0.0.1, 203.0.113.7")],
        "client": ("10.0.0.1", 5000),
    }
    await TrustedProxyMiddleware(app, mode="x-forwarded-for")(scope, None, None)
    assert seen["client"][0] == "203.0.113.7"


def test_a_client_blocked_over_http_is_refused_on_the_websocket_too(app_client, conversations, monkeypatch):
    """A block earned with header credentials still applies to handshakes."""
    import src.multi_tenant.security_hardening as hardening

    limiter = hardening.PreAuthRateLimiter(max_attempts=2)
    monkeypatch.setattr(hardening, "_pre_auth_limiter", limiter)
    for _ in range(2):
        limiter.record_failure("testclient")
    assert _refused(app_client, f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}") == 1008


def test_all_handshakes_are_capped_per_client_ip(app_client, conversations, monkeypatch):
    """Successful handshakes cost lookups too, so every attempt counts (Codex review of #137)."""
    import src.chat.endpoints as endpoints

    monkeypatch.setattr(endpoints, "_WS_HANDSHAKES_PER_MINUTE", 2)
    own = f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}"
    for _ in range(2):
        with app_client.websocket_connect(own):
            pass
    assert _refused(app_client, own) == 1008


class _UnreachableRedis:
    """A shared rate-limit backend whose store has gone away after startup."""

    def is_limited(self, key, *, max_requests, window_seconds):
        raise ConnectionError("Redis unreachable")


def test_handshakes_survive_a_shared_backend_outage(app_client, conversations, monkeypatch):
    """The HTTP limiter falls back to a local count when Redis fails; sockets do too (Codex review of #137)."""
    import src.chat.endpoints as endpoints
    import src.multi_tenant.security_hardening as hardening

    hardening.set_rate_limit_backend(_UnreachableRedis())
    monkeypatch.setattr(endpoints, "_WS_HANDSHAKES_PER_MINUTE", 2)
    own = f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}"
    for _ in range(2):
        with app_client.websocket_connect(own) as socket:
            socket.send_json({"type": "typing_start", "data": {}})
    assert _refused(app_client, own) == 1008  # the cap still holds, counted locally


def test_unconfigured_key_resolution_is_not_counted(app_client, conversations, fresh_limiter, monkeypatch):
    """A 500 from the key check is a configuration fault, not a guess (Codex review of #139)."""
    monkeypatch.setattr("src.multi_tenant.middleware._resolve_by_widget_key_hash", None)
    for _ in range(3):
        assert _refused(app_client, f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}") == 1008
    assert not fresh_limiter._trackers


def test_unknown_keys_are_refused_but_not_counted(app_client, conversations, monkeypatch):
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter(max_attempts=2))
    for _ in range(3):
        assert _refused(app_client, f"/api/chat/ws/{OWN}?widget_key=pk_live_not_a_real_key_0000") == 1008
    assert not hardening.get_pre_auth_limiter()._trackers


def test_handshakes_without_a_key_are_not_counted(app_client, conversations, monkeypatch):
    """Browsers open cross-site WebSockets freely, so a keyless handshake must not block the visitor (Codex review of #143)."""
    import src.multi_tenant.security_hardening as hardening

    monkeypatch.setattr(hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter(max_attempts=2))
    for _ in range(3):
        assert _refused(app_client, f"/api/chat/ws/{OWN}") == 1008
    assert not hardening.get_pre_auth_limiter()._trackers
    with app_client.websocket_connect(f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}") as socket:
        socket.send_json({"type": "typing_start", "data": {}})


def test_without_a_per_client_address_the_cap_is_platform_wide(app_client, conversations, monkeypatch):
    """Behind the ingress with no TRUSTED_PROXY, the per-IP cap is replaced by a larger platform-wide one,
    not removed, since the pre-auth limiter is record-only there too (Codex review of #143)."""
    import src.chat.endpoints as endpoints

    monkeypatch.setattr(endpoints, "_WS_HANDSHAKES_PER_MINUTE", 1)
    monkeypatch.setattr(endpoints, "_WS_HANDSHAKES_PER_MINUTE_ALL_CLIENTS", 2)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    own = f"/api/chat/ws/{OWN}?widget_key={TEST_WIDGET_KEY}"
    for _ in range(2):  # above the per-IP cap of 1, within the platform-wide cap of 2
        with app_client.websocket_connect(own) as socket:
            socket.send_json({"type": "typing_start", "data": {}})
    assert _refused(app_client, own) == 1008
