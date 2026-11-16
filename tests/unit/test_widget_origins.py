# Copyright 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Public widget origins through the composed application."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat.models import ConversationStartResponse
from src.multi_tenant.config.models import ConfigReadResult
from tests.conftest import STARTER_TENANT_ID, TEST_WIDGET_KEY

APPROVED = "https://shop.example"
INDEPENDENT_WIDGET_ORIGINS = ("https://shop.example.net", "https://help.example.org")


@pytest.fixture
def preferences():
    """An existing tenant can still have a legacy allowlist in storage."""
    get_active = AsyncMock(return_value={"approved_widget_origins": [APPROVED]})
    with patch("src.multi_tenant.repositories.preferences.PreferencesRepository.get_active", get_active):
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


@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
def test_public_widget_bootstrap_accepts_unregistered_origins(app_client, preferences, config, origin):
    response = _widget_get(app_client, origin)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    preferences.assert_not_awaited()


@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/config", "GET"),
        ("/api/chat/conversations", "POST"),
        ("/api/chat/message", "POST"),
        ("/api/chat/otp/send", "POST"),
        ("/api/chat/conversations/example/consent", "POST"),
    ],
)
def test_widget_only_preflight_accepts_unregistered_origins(app_client, origin, path, method):
    response = app_client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type,x-widget-key",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
@pytest.mark.parametrize(
    ("path", "method", "headers"),
    [
        ("/api/config", "PUT", "x-widget-key"),
        ("/api/config/draft", "GET", "x-widget-key"),
        ("/api/admin/team-members", "GET", "x-widget-key"),
        ("/api/config", "GET", "x-widget-key,x-session-token"),
        ("/api/chat/conversations", "POST", "x-widget-key,x-api-key"),
        ("/api/config", "GET", "authorization"),
    ],
)
def test_unregistered_origin_cannot_preflight_admin_requests(app_client, origin, path, method, headers):
    response = app_client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers

@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
def test_public_widget_script_preflight(app_client, origin):
    response = app_client.options(
        "/widget.js",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
def test_widget_conversation_creation_accepts_unregistered_origins(app_client, preferences, origin, monkeypatch):
    import src.chat.endpoints as endpoints

    preferences.return_value = {
        "activated_at": "2026-11-15T00:00:00Z",
        "approved_widget_origins": [APPROVED],
    }
    session = MagicMock()
    session.start_conversation = AsyncMock(return_value=ConversationStartResponse(
        conversation_id="example",
        stream_url="/api/chat/stream/example",
        ws_url="/api/chat/ws/example",
        created_at="2026-11-15T00:00:00Z",
    ))
    monkeypatch.setattr(endpoints, "_session", session)
    response = app_client.post(
        "/api/chat/conversations",
        json={},
        headers={"Origin": origin, "X-Widget-Key": TEST_WIDGET_KEY},
    )

    assert response.status_code == 201
    assert response.json()["conversation_id"] == "example"
    assert response.headers["access-control-allow-origin"] == "*"
    assert session.start_conversation.await_args.kwargs["tenant_id"] == STARTER_TENANT_ID


@pytest.mark.parametrize("origin", INDEPENDENT_WIDGET_ORIGINS)
def test_public_widget_sse_receives_cors_headers(app_client, origin, monkeypatch):
    import src.chat.endpoints as endpoints

    monkeypatch.setattr(endpoints, "_session", None)
    response = app_client.get(
        "/api/chat/stream/test-conversation",
        params={"widget_key": TEST_WIDGET_KEY},
        headers={"Origin": origin},
    )

    assert response.status_code == 503
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({"Authorization": "Bearer fixture"}, {}),
        ({"X-API-Key": "fixture"}, {}),
        ({"X-Session-Token": "fixture"}, {}),
        ({"Cookie": "fixture=value"}, {}),
        ({}, {"api_key": "fixture"}),
        ({}, {"session_token": "fixture"}),
    ],
)
def test_widget_key_does_not_expand_cors_for_account_credentials(app_client, config, headers, params):
    response = app_client.get(
        "/api/config",
        params=params,
        headers={"Origin": INDEPENDENT_WIDGET_ORIGINS[0], "X-Widget-Key": TEST_WIDGET_KEY, **headers},
    )

    assert "access-control-allow-origin" not in response.headers


def test_trusted_origin_keeps_credentialed_cors(app_client):
    origin = "https://existing-merchant.myshopify.com"
    response = app_client.options(
        "/api/config",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-widget-key,x-session-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


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
