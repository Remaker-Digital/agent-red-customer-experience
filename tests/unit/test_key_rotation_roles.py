# tests/unit/test_key_rotation_roles.py: only admins rotate keys, and the response is truthful
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
``POST /api/keys/rotate`` and ``/api/keys/rotate-widget-key`` checked only that some
tenant context existed, and ``/api/keys`` is not on the admin-only path list, so a
viewer or escalation agent could mint a new tenant API key (which carries the admin
role) or rotate the storefront widget key. The API-key response also promised the old
key would stay valid for 24 hours, but nothing read the stored previous hash: the old
key stopped working at once.

Both routes now require an admin, a superadmin or a tenant-level credential, and the
response says the old key no longer works.

Architecture review S5 (docs/agent-red-architecture-review.md). The tests run through
the composed application (``app_client``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import (
    STARTER_TENANT_ID,
    TEST_API_KEY_STARTER,
    TEST_USER_KEY,
    TEST_USER_MEMBER_DOC,
    AuthenticatedClient,
)

ROUTES = ["/api/keys/rotate", "/api/keys/rotate-widget-key"]


@pytest.fixture
def tenant_repo():
    from src.multi_tenant.security_hardening import configure_key_rotation_services

    repo = MagicMock()
    repo.read = AsyncMock(return_value={"tenant_id": STARTER_TENANT_ID, "api_key_hash": "old-hash"})
    repo.patch = AsyncMock()
    configure_key_rotation_services(secret_service=MagicMock(), tenant_repo=repo)
    yield repo
    configure_key_rotation_services(secret_service=None, tenant_repo=None)


def _member_client(app_client, monkeypatch, role: str) -> AuthenticatedClient:
    monkeypatch.setitem(TEST_USER_MEMBER_DOC["team_member"], "role", role)
    return AuthenticatedClient(app_client, TEST_USER_KEY, STARTER_TENANT_ID)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("role", ["viewer", "escalation_agent"])
def test_read_only_roles_cannot_rotate_keys(app_client, monkeypatch, tenant_repo, role, route):
    response = _member_client(app_client, monkeypatch, role).post(route)
    assert response.status_code == 403
    tenant_repo.patch.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_admins_can_rotate_keys(app_client, monkeypatch, tenant_repo, role, route):
    response = _member_client(app_client, monkeypatch, role).post(route)
    assert response.status_code == 200
    tenant_repo.patch.assert_awaited_once()


def test_tenant_key_rotation_says_the_old_key_stops_working(app_client, tenant_repo):
    before = datetime.now(UTC)
    response = AuthenticatedClient(app_client, TEST_API_KEY_STARTER, STARTER_TENANT_ID).post("/api/keys/rotate")
    assert response.status_code == 200
    body = response.json()
    assert "no longer works" in body["message"]
    assert datetime.fromisoformat(body["old_key_valid_until"]) >= before
    assert datetime.fromisoformat(body["old_key_valid_until"]) <= datetime.now(UTC)
    paths = {op["path"] for op in tenant_repo.patch.call_args.kwargs["operations"]}
    assert "/previous_api_key_hash" not in paths


def _session_request(team_member_id):
    from src.multi_tenant.auth import TenantContext
    from src.multi_tenant.cosmos_schema import TenantStatus, TenantTier

    request = MagicMock()
    request.url.path = "/api/keys/rotate"
    request.state.tenant_context = TenantContext(
        tenant_id=STARTER_TENANT_ID,
        tier=TenantTier.STARTER,
        status=TenantStatus.ACTIVE,
        auth_method="magic_link_session",
        team_member_id=team_member_id,
        team_member_role=None,
    )
    return request


async def test_a_member_session_without_a_role_cannot_rotate_keys():
    """A magic-link sign-in whose member lookup failed carries a member id but no role (Codex review)."""
    from fastapi import HTTPException

    from src.multi_tenant.security_hardening import _require_key_manager

    with pytest.raises(HTTPException) as refused:
        await _require_key_manager(_session_request("member-001"))
    assert refused.value.status_code == 403


async def test_a_tenant_owner_session_can_rotate_keys():
    """Owner magic links carry no member id; they remain a tenant-level credential."""
    from src.multi_tenant.security_hardening import _require_key_manager

    ctx = await _require_key_manager(_session_request(None))
    assert ctx.tenant_id == STARTER_TENANT_ID
