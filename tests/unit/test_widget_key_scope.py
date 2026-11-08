# tests/unit/test_widget_key_scope.py: what the public widget key may do
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
The widget key (``pk_live_...``) is embedded in storefront pages, so anyone can
copy it. It may drive the chat endpoints and read the widget's own configuration,
nothing more. Before this change it was accepted for every method under
``/api/config``, so it could save and activate a merchant's agent configuration,
and ``GET /api/config`` returned the merchant's full settings (escalation
contacts, custom AI instructions) and, with ``state=draft``, the unpublished draft.

Architecture review S1 (docs/agent-red-architecture-review.md). The endpoint
tests run through the composed application (``app_client``), middleware included.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.multi_tenant.auth import is_widget_key_allowed_path
from src.multi_tenant.config.models import ConfigReadResult
from src.multi_tenant.middleware import TenantAuthMiddleware
from tests.conftest import STARTER_TENANT_ID, TEST_API_KEY_STARTER, TEST_WIDGET_KEY, auth_headers_api_key

WIDGET = {"X-Widget-Key": TEST_WIDGET_KEY}

FULL_CONFIG = {
    "brand_name": "Acme",
    "consent_collection_enabled": True,
    "customer_email_verification": "optional",
    "structured_blocks_enabled": False,
    "widget_primary_color": "#ff3621",
    "widget_greeting_message": "Hi",
    "escalation_email": "owner@example.com",
    "whatsapp_business_phone": "+15550100",
    "custom_instructions": "Never mention the supplier.",
}
WIDGET_KEYS = {
    "brand_name",
    "consent_collection_enabled",
    "customer_email_verification",
    "structured_blocks_enabled",
    "widget_primary_color",
    "widget_greeting_message",
}


@pytest.fixture
def processor():
    """A config processor double whose active config is FULL_CONFIG."""
    fake = MagicMock()
    fake.get_config = AsyncMock(
        return_value=ConfigReadResult(
            tenant_id=STARTER_TENANT_ID, tier="starter", version=3, config=dict(FULL_CONFIG), from_cache=False
        )
    )
    activation = MagicMock()
    activation.get_draft_state = AsyncMock()
    # Widget-key requests check approved origins on the active preferences (S14, #139); none are set here.
    no_origins = AsyncMock(return_value={"approved_widget_origins": []})
    with (
        patch("src.multi_tenant.tenant_config_api.get_config_processor", return_value=fake),
        patch("src.multi_tenant.tenant_config_api.get_activation_service", return_value=activation),
        patch("src.multi_tenant.repositories.preferences.PreferencesRepository.get_active", no_origins),
    ):
        fake.activation = activation
        yield fake


@pytest.mark.parametrize(
    ("path", "method", "allowed"),
    [
        ("/api/chat/message", "POST", True),
        ("/api/chat/conversations/c1/end", "POST", True),
        ("/api/config", "GET", True),
        ("/api/config", "HEAD", True),
        ("/api/config", "PUT", False),
        ("/api/config", "POST", False),
        ("/api/config/draft/activate", "POST", False),
        ("/api/config/rollback", "POST", False),
        ("/api/config/versions", "GET", False),
        ("/api/config/draft", "GET", False),
        ("/api/configuration", "GET", False),
        ("/ws/chat/c1", "GET", False),
        ("/api/billing/portal", "POST", False),
    ],
)
def test_scope_rule(path, method, allowed):
    assert is_widget_key_allowed_path(path, method) is allowed


def test_scope_needs_the_request_method():
    """No default method: a caller that forgets it must not get the read-only rule by accident."""
    assert (
        inspect.signature(TenantAuthMiddleware._auth_widget_key).parameters["method"].default is inspect.Parameter.empty
    )
    assert is_widget_key_allowed_path("/api/config", "") is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/api/config"),
        ("POST", "/api/config/draft/activate"),
        ("POST", "/api/config/rollback"),
        ("POST", "/api/config/deactivate"),
        ("GET", "/api/config/versions"),
        ("GET", "/api/config/draft"),
    ],
)
def test_widget_key_cannot_write_config_or_read_other_config_routes(app_client, processor, method, path):
    response = app_client.request(method, path, headers=WIDGET, json={"fields": {"brand_name": "Pwned"}})
    assert response.status_code == 401
    processor.get_config.assert_not_awaited()
    processor.activation.get_draft_state.assert_not_awaited()


def test_widget_key_reads_only_the_widget_view(app_client, processor):
    response = app_client.get("/api/config", headers=WIDGET)
    assert response.status_code == 200
    assert set(response.json()["config"]) == WIDGET_KEYS


def test_widget_key_never_gets_the_draft(app_client, processor):
    response = app_client.get("/api/config", params={"state": "draft"}, headers=WIDGET)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "active"
    assert set(body["config"]) == WIDGET_KEYS
    processor.activation.get_draft_state.assert_not_awaited()


def test_tenant_api_key_still_reads_the_full_config(app_client, processor):
    response = app_client.get(
        "/api/config", params={"tenant": STARTER_TENANT_ID}, headers=auth_headers_api_key(TEST_API_KEY_STARTER)
    )
    assert response.status_code == 200
    assert set(response.json()["config"]) == set(FULL_CONFIG)


def test_widget_key_still_reaches_chat(app_client):
    response = app_client.post("/api/chat/conversations", headers=WIDGET, json={"initial_message": "hello"})
    assert response.status_code != 401
