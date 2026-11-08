# tests/unit/test_widget_origins.py: approved widget origins come from the active preferences
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
SPEC-1840 lets a merchant restrict its widget key to approved origins. The admin saves
``approved_widget_origins`` on the tenant's preferences (``PreferencesDocument``), but the
widget-key check read it from the tenant document returned by the key lookup, which never
holds it: the list was always empty and every origin was allowed, for HTTP widget requests
and the chat WebSocket alike. The abuse detector read the same empty field and reported
every widget tenant as unrestricted.

Both now read the active preferences (cached for 60 seconds). If they cannot be read, the
last value read is used; with none, the request is refused rather than allowing every origin.

Architecture review S14 (found in Codex's review of #137). The endpoint tests run through
the composed application (``app_client``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.multi_tenant.middleware as middleware
from src.multi_tenant.config.models import ConfigReadResult
from tests.conftest import STARTER_TENANT_ID, TEST_WIDGET_KEY

APPROVED = "https://shop.example"


def _clear_cache():
    getattr(middleware, "_widget_origins_cache", {}).clear()


@pytest.fixture(autouse=True)
def _fresh_cache():
    _clear_cache()
    yield
    _clear_cache()


@pytest.fixture
def preferences():
    """The active preferences double; set ``.return_value`` or ``.side_effect`` per test.

    ``preferences.previous`` returns the 'previous' documents (none by default).
    """
    get_active = AsyncMock(return_value={"approved_widget_origins": [APPROVED]})
    get_active.previous = AsyncMock(return_value=[])
    with (
        patch("src.multi_tenant.repositories.preferences.PreferencesRepository.get_active", get_active),
        patch("src.multi_tenant.repositories.preferences.PreferencesRepository.list_previous", get_active.previous),
    ):
        yield get_active


@pytest.fixture
def config():
    fake = MagicMock()
    fake.get_config = AsyncMock(
        return_value=ConfigReadResult(
            tenant_id=STARTER_TENANT_ID,
            tier="starter",
            version=1,
            config={"widget_primary_color": "#000"},
            from_cache=False,
        )
    )
    with patch("src.multi_tenant.tenant_config_api.get_config_processor", return_value=fake):
        yield fake


def _widget_get(app_client, origin: str | None):
    headers = {"X-Widget-Key": TEST_WIDGET_KEY}
    if origin:
        headers["Origin"] = origin
    return app_client.get("/api/config", headers=headers)


def test_an_unapproved_origin_is_refused(app_client, preferences, config):
    assert _widget_get(app_client, "https://evil.example").status_code == 403
    config.get_config.assert_not_awaited()


def test_an_approved_origin_is_allowed(app_client, preferences, config):
    assert _widget_get(app_client, APPROVED).status_code == 200


def test_no_origin_header_is_refused_when_origins_are_configured(app_client, preferences, config):
    assert _widget_get(app_client, None).status_code == 403


def test_any_origin_is_allowed_when_none_are_configured(app_client, preferences, config):
    preferences.return_value = {"approved_widget_origins": []}
    assert _widget_get(app_client, "https://anywhere.example").status_code == 200


def test_origins_are_cached_between_requests(app_client, preferences, config):
    _widget_get(app_client, APPROVED)
    _widget_get(app_client, APPROVED)
    assert preferences.await_count == 1


def test_a_preferences_read_failure_with_nothing_cached_fails_closed(app_client, preferences, config):
    """A transient error must not lift a merchant's restriction (Codex review of #139)."""
    preferences.side_effect = RuntimeError("Cosmos unavailable")
    assert _widget_get(app_client, "https://anywhere.example").status_code == 503
    config.get_config.assert_not_awaited()


def test_a_preferences_read_failure_keeps_the_last_value_read(app_client, preferences, config, monkeypatch):
    assert _widget_get(app_client, APPROVED).status_code == 200  # caches [APPROVED]
    monkeypatch.setattr(middleware, "_WIDGET_ORIGINS_TTL_S", 0.0)  # the cached value is now stale
    preferences.side_effect = RuntimeError("Cosmos unavailable")
    assert _widget_get(app_client, "https://evil.example").status_code == 403
    assert _widget_get(app_client, APPROVED).status_code == 200


async def test_the_abuse_detector_reads_the_same_origins(preferences):
    from src.multi_tenant.abuse_detection import _detect_widget_abuse

    # Real tenant documents hold only the key's hash (Codex review of #139).
    tenant_doc = {"tenant_id": STARTER_TENANT_ID, "widget_key_hash": "0f" * 32}
    now = datetime.now(UTC)
    assert await _detect_widget_abuse(STARTER_TENANT_ID, tenant_doc, now) is None

    _clear_cache()
    preferences.return_value = {"approved_widget_origins": []}
    signal = await _detect_widget_abuse(STARTER_TENANT_ID, tenant_doc, now)
    assert signal is not None


def _widget_get_with_referer(app_client, referer: str):
    return app_client.get("/api/config", headers={"X-Widget-Key": TEST_WIDGET_KEY, "Referer": referer})


def test_a_referer_on_an_approved_origin_is_allowed(app_client, preferences, config):
    """Without an Origin header the Referer's scheme and host are compared (Codex review of #139)."""
    assert _widget_get_with_referer(app_client, f"{APPROVED}/products/1?variant=2").status_code == 200


def test_a_referer_on_another_origin_is_refused(app_client, preferences, config):
    assert _widget_get_with_referer(app_client, "https://evil.example/shop.example/page").status_code == 403


@pytest.mark.parametrize(
    ("origin", "referer", "expected"),
    [
        ("https://a.example", "https://b.example/x", "https://a.example"),
        (None, "https://shop.example:8443/p/1", "https://shop.example:8443"),
        (None, "not a url", None),
        (None, None, None),
    ],
)
def test_request_origin(origin, referer, expected):
    assert middleware.request_origin(origin, referer) == expected


def test_a_missing_active_document_keeps_the_last_value_read(app_client, preferences, config, monkeypatch):
    """Activation briefly leaves no active document; that must not lift the restriction (Codex review of #139)."""
    assert _widget_get(app_client, APPROVED).status_code == 200  # caches [APPROVED]
    monkeypatch.setattr(middleware, "_WIDGET_ORIGINS_TTL_S", 0.0)
    preferences.return_value = None  # between demoting the active document and promoting the draft
    assert _widget_get(app_client, "https://evil.example").status_code == 403
    preferences.return_value = {"approved_widget_origins": ["https://new.example"]}
    assert _widget_get(app_client, "https://new.example").status_code == 200  # the gap was not cached


def test_a_preferences_outage_does_not_count_as_a_failed_credential(app_client, preferences, config, monkeypatch):
    """A 503 is the service's fault, so it must not push the caller toward a pre-auth block (Codex review of #139)."""
    import src.multi_tenant.security_hardening as hardening

    limiter = hardening.PreAuthRateLimiter()
    monkeypatch.setattr(hardening, "_pre_auth_limiter", limiter)
    preferences.side_effect = RuntimeError("Cosmos unavailable")
    assert _widget_get(app_client, APPROVED).status_code == 503
    assert not limiter._trackers  # nothing recorded against the client


def test_a_malformed_referer_is_refused_not_a_server_error(app_client, preferences, config):
    """``urlsplit`` raises on a broken authority; that is a missing origin, not a 500 (Codex review of #139)."""
    assert _widget_get_with_referer(app_client, "https://[broken/page").status_code == 403


def test_the_chat_websocket_enforces_approved_origins(app_client, preferences, monkeypatch):
    """#137 authenticates the socket through the same widget-key check, so origins apply there too."""
    from starlette.websockets import WebSocketDisconnect

    import src.chat.endpoints as endpoints
    import src.multi_tenant.security_hardening as hardening

    class _Conversations:
        async def get_conversation(self, tenant_id, conversation_id):
            return {"conversation_id": conversation_id}

    monkeypatch.setattr(endpoints, "_session", _Conversations())
    monkeypatch.setattr(hardening, "_pre_auth_limiter", hardening.PreAuthRateLimiter())
    monkeypatch.setattr(hardening, "_rate_limit_backend", hardening.InMemoryRateLimitBackend())
    url = f"/api/chat/ws/conv-1?widget_key={TEST_WIDGET_KEY}"
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        app_client.websocket_connect(url, headers={"Origin": "https://evil.example"}),
    ):
        pass
    assert refused.value.code == 1008
    with app_client.websocket_connect(url, headers={"Origin": APPROVED}) as socket:
        socket.send_json({"type": "typing_start", "data": {}})


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        ("https://shop.example", ["https://shop.example"]),  # a bare string is one origin, not its characters
        (["https://shop.example", 42, None, "  "], ["https://shop.example"]),  # non-strings would raise in rstrip()
        ({"origin": "https://shop.example"}, []),
    ],
    ids=["string", "mixed-list", "mapping"],
)
async def test_saved_origins_are_canonicalized(preferences, saved, expected):
    """The field is not in the config registry, so drafts can save any value (Codex review of #139)."""
    preferences.return_value = {"approved_widget_origins": saved}
    assert await middleware.approved_widget_origins(STARTER_TENANT_ID, {}) == expected


def test_a_number_among_saved_origins_is_not_a_server_error(app_client, preferences, config):
    preferences.return_value = {"approved_widget_origins": [42, APPROVED]}
    assert _widget_get(app_client, APPROVED).status_code == 200
    assert _widget_get(app_client, "https://evil.example").status_code == 403


def test_the_widget_canary_can_send_an_origin(monkeypatch):
    """A canary tenant that restricts origins would fail every check without one (Codex review of #139)."""
    import src.jobs.run_widget_canary as canary

    captured = {}

    class _Stop(Exception):
        pass

    def fake_client(**kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setenv("CANARY_TARGET_URL", "https://gateway.example")
    monkeypatch.setenv("CANARY_WIDGET_KEY", "pk_live_canary")
    monkeypatch.setenv("CANARY_ORIGIN", APPROVED)
    monkeypatch.setattr(canary.httpx, "Client", fake_client)
    with pytest.raises(_Stop):
        canary.main()
    assert captured["headers"] == {"Origin": APPROVED}


def test_a_missing_active_document_uses_the_demoted_policy(app_client, preferences, config):
    """Activation demotes the active document before promoting the draft, and a failed promotion
    stops there; the demoted ('previous') document still restricts, even with nothing cached
    (Codex review of #139)."""
    preferences.return_value = None
    preferences.previous.return_value = [{"approved_widget_origins": [APPROVED]}]
    assert _widget_get(app_client, "https://evil.example").status_code == 403
    assert _widget_get(app_client, APPROVED).status_code == 200


def test_the_demoted_policy_is_not_cached(app_client, preferences, config):
    """Once the draft is promoted, its origins apply on the next request."""
    preferences.return_value = None
    preferences.previous.return_value = [{"approved_widget_origins": [APPROVED]}]
    assert _widget_get(app_client, APPROVED).status_code == 200
    preferences.return_value = {"approved_widget_origins": ["https://new.example"]}
    assert _widget_get(app_client, "https://new.example").status_code == 200


def test_a_failed_previous_lookup_with_nothing_cached_fails_closed(app_client, preferences, config):
    preferences.return_value = None
    preferences.previous.side_effect = RuntimeError("Cosmos unavailable")
    assert _widget_get(app_client, APPROVED).status_code == 503


def test_a_preferences_outage_does_not_block_websocket_clients(app_client, preferences, monkeypatch):
    """A 503 on the socket path is the service's fault, as on HTTP (Codex review of #139)."""
    from starlette.websockets import WebSocketDisconnect

    import src.chat.endpoints as endpoints
    import src.multi_tenant.security_hardening as hardening

    class _Conversations:
        async def get_conversation(self, tenant_id, conversation_id):
            return {"conversation_id": conversation_id}

    limiter = hardening.PreAuthRateLimiter(max_attempts=2)
    monkeypatch.setattr(endpoints, "_session", _Conversations())
    monkeypatch.setattr(endpoints, "_local_handshakes", None)
    monkeypatch.setattr(hardening, "_pre_auth_limiter", limiter)
    monkeypatch.setattr(hardening, "_rate_limit_backend", hardening.InMemoryRateLimitBackend())
    url = f"/api/chat/ws/conv-1?widget_key={TEST_WIDGET_KEY}"
    preferences.side_effect = RuntimeError("Cosmos unavailable")
    for _ in range(3):
        with (
            pytest.raises(WebSocketDisconnect) as refused,
            app_client.websocket_connect(url, headers={"Origin": APPROVED}),
        ):
            pass
        assert refused.value.code == 1008
    assert not limiter._trackers
    preferences.side_effect = None
    with app_client.websocket_connect(url, headers={"Origin": APPROVED}) as socket:
        socket.send_json({"type": "typing_start", "data": {}})


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_canary_origin_sends_no_origin_header(monkeypatch, value):
    import src.jobs.run_widget_canary as canary

    captured = {}

    class _Stop(Exception):
        pass

    def fake_client(**kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setenv("CANARY_TARGET_URL", "https://gateway.example")
    monkeypatch.setenv("CANARY_WIDGET_KEY", "pk_live_canary")
    monkeypatch.setenv("CANARY_ORIGIN", value)
    monkeypatch.setattr(canary.httpx, "Client", fake_client)
    with pytest.raises(_Stop):
        canary.main()
    assert captured["headers"] is None


def test_the_canary_job_definition_passes_every_setting_the_canary_reads():
    """terraform/scaling_profiles.tf must pass each CANARY_* variable run_widget_canary reads (Codex review of #139)."""
    import re
    from pathlib import Path

    reads = set(re.findall(r'os\.environ\.get\("(CANARY_[A-Z_]+)"', Path("src/jobs/run_widget_canary.py").read_text()))
    tf = Path("terraform/scaling_profiles.tf").read_text()
    start = tf.index('resource "azurerm_container_app_job" "widget_canary"')
    end = tf.find('\nresource "', start + 1)
    job = tf[start : end if end != -1 else len(tf)]
    assert reads, "the canary reads no CANARY_* settings"
    missing = sorted(name for name in reads if f'"{name}"' not in job)
    assert not missing, f"widget_canary job does not pass {missing}"
    assert re.search(
        r'variable "canary_origin" \{[^}]*default\s*=\s*""', Path("terraform/variables.tf").read_text(), re.S
    )


def test_a_failed_restore_fails_closed(app_client, preferences, config):
    """restore_previous() demotes the active document while its target is still 'previous'; if the
    promotion fails, two 'previous' documents remain and neither can be told to be the one that was
    active, so nothing cached means 503, not the higher version's policy (Codex review of #139)."""
    preferences.return_value = None
    preferences.previous.return_value = [
        {"version": 3, "approved_widget_origins": []},  # the restore target: unrestricted
        {"version": 2, "approved_widget_origins": [APPROVED]},  # demoted from active by the failed restore
    ]
    assert _widget_get(app_client, "https://evil.example").status_code == 503


def test_a_failed_restore_keeps_the_last_value_read(app_client, preferences, config, monkeypatch):
    assert _widget_get(app_client, APPROVED).status_code == 200  # caches [APPROVED]
    monkeypatch.setattr(middleware, "_WIDGET_ORIGINS_TTL_S", 0.0)
    preferences.return_value = None
    preferences.previous.return_value = [{"approved_widget_origins": []}, {"approved_widget_origins": [APPROVED]}]
    assert _widget_get(app_client, "https://evil.example").status_code == 403
