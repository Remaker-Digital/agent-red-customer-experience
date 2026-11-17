"""F45: POST /api/tenants/auth/validate-key accepts a magic-link session.

The standalone admin calls validate-key after every sign-in. After a magic
link or a six-digit code it sends ``X-Session-Token`` and no ``X-API-Key``.
The endpoint answered 400, and the admin then signed the user out.

The session must be valid, and signed for the tenant named in the request.
A member session is checked against the member document, as the
authentication middleware does. The API key path is unchanged.

© 2026 Michael Palmeter. All rights reserved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    PROFESSIONAL_TENANT_ID,
    STARTER_TENANT_ID,
    TEST_API_KEY_STARTER,
    auth_headers_api_key,
)

pytestmark = [pytest.mark.security, pytest.mark.unit]

URL = "/api/tenants/auth/validate-key"

TENANT_DOC = {
    "id": STARTER_TENANT_ID,
    "tenant_id": STARTER_TENANT_ID,
    "status": "active",
    "tier": "starter",
    "billing_channel": "stripe",
    "stripe_customer_id": "cus_test",
}


def _session(tenant_id: str = STARTER_TENANT_ID, **kwargs) -> str:
    from src.multi_tenant.magic_link_auth import create_magic_link_session_token

    token, _expires = create_magic_link_session_token(tenant_id, "owner@example.com", **kwargs)
    return token


def _tenant_repo(doc: dict | None = TENANT_DOC) -> MagicMock:
    repo = MagicMock()
    repo.read = AsyncMock(return_value=doc)
    return repo


@pytest.fixture
def prov():
    import src.integrations.provisioning as module

    with patch.object(module, "_read_brand_name", new_callable=AsyncMock, return_value=None):
        yield module


def test_owner_session_resolves_the_tenant(app_client: TestClient, prov):
    with patch.object(prov, "_tenant_repo", _tenant_repo()):
        resp = app_client.post(
            URL,
            json={"tenant": STARTER_TENANT_ID},
            headers={"X-Session-Token": _session()},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["valid"] is True
    assert data["tenant_id"] == STARTER_TENANT_ID
    assert data["status"] == "active"
    assert data["tier"] == "starter"
    assert data["has_stripe_billing"] is True
    assert data["role"] == "admin"
    assert data["email"] == "owner@example.com"


def test_member_session_takes_the_role_from_the_member_record(app_client: TestClient, prov):
    member_doc = {"id": "m-1", "role": "viewer", "is_active": True}
    token = _session(member_id="m-1", role="admin")
    with (
        patch.object(prov, "_tenant_repo", _tenant_repo()),
        patch(
            "src.multi_tenant.repositories.TeamMemberRepository.read",
            new_callable=AsyncMock,
            return_value=member_doc,
        ),
    ):
        resp = app_client.post(URL, json={"tenant": STARTER_TENANT_ID}, headers={"X-Session-Token": token})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["role"] == "viewer"
    assert data["team_member_id"] == "m-1"


def test_expired_session_is_401_not_400(app_client: TestClient, prov):
    from src.multi_tenant import magic_link_auth

    past = datetime.now(UTC) - timedelta(hours=1)
    token = jwt.encode(
        {
            "sub": STARTER_TENANT_ID,
            "email": "owner@example.com",
            "type": "magic_link_session",
            "iat": int((past - timedelta(hours=8)).timestamp()),
            "exp": int(past.timestamp()),
        },
        magic_link_auth._JWT_SECRET,
        algorithm="HS256",
    )
    with patch.object(prov, "_tenant_repo", _tenant_repo()):
        resp = app_client.post(URL, json={"tenant": STARTER_TENANT_ID}, headers={"X-Session-Token": token})
    assert resp.status_code == 401


def test_invalid_session_is_401_not_400(app_client: TestClient, prov):
    with patch.object(prov, "_tenant_repo", _tenant_repo()):
        resp = app_client.post(
            URL,
            json={"tenant": STARTER_TENANT_ID},
            headers={"X-Session-Token": "not-a-session"},
        )
    assert resp.status_code == 401


def test_session_for_another_tenant_gets_no_answer(app_client: TestClient, prov):
    repo = _tenant_repo()
    with patch.object(prov, "_tenant_repo", repo):
        resp = app_client.post(
            URL,
            json={"tenant": PROFESSIONAL_TENANT_ID},
            headers={"X-Session-Token": _session(STARTER_TENANT_ID)},
        )
    assert resp.status_code == 401
    assert PROFESSIONAL_TENANT_ID not in resp.text
    assert STARTER_TENANT_ID not in resp.text
    repo.read.assert_not_awaited()


def test_session_for_a_removed_member_is_401(app_client: TestClient, prov):
    from src.multi_tenant.repositories import DocumentNotFoundError

    token = _session(member_id="m-gone", role="admin")
    with (
        patch.object(prov, "_tenant_repo", _tenant_repo()),
        patch(
            "src.multi_tenant.repositories.TeamMemberRepository.read",
            new_callable=AsyncMock,
            side_effect=DocumentNotFoundError("team_members", "m-gone", STARTER_TENANT_ID),
        ),
    ):
        resp = app_client.post(URL, json={"tenant": STARTER_TENANT_ID}, headers={"X-Session-Token": token})
    assert resp.status_code == 401


def test_session_when_the_store_is_down_is_503(app_client: TestClient, prov):
    repo = MagicMock()
    repo.read = AsyncMock(side_effect=RuntimeError("store down"))
    with patch.object(prov, "_tenant_repo", repo):
        resp = app_client.post(
            URL,
            json={"tenant": STARTER_TENANT_ID},
            headers={"X-Session-Token": _session()},
        )
    assert resp.status_code == 503


def test_no_credential_is_still_400(app_client: TestClient, prov):
    resp = app_client.post(URL, json={"tenant": STARTER_TENANT_ID})
    assert resp.status_code == 400


def test_api_key_path_is_unchanged(app_client: TestClient, prov):
    result = {**TENANT_DOC}
    with patch.object(
        prov,
        "_validate_api_key_for_tenant",
        new_callable=AsyncMock,
        return_value=result,
    ) as check:
        resp = app_client.post(
            URL,
            json={"tenant": STARTER_TENANT_ID},
            headers={**auth_headers_api_key(TEST_API_KEY_STARTER), "X-Session-Token": "ignored"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"
    check.assert_awaited_once_with(STARTER_TENANT_ID, TEST_API_KEY_STARTER)
