# tests/unit/test_verification_token_id_guard.py: token ids Cosmos cannot store
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
VerificationTokenRepository receives token ids from public links (email
verification, email change, magic link, tenant recovery) and from OTP flows. The
Cosmos SDK puts the id into the request path without escaping "/", so an id that
Cosmos cannot store (empty, containing "/" or a backslash, or over 1,023 bytes)
would address another resource path. Such ids are now "no such token" before
any Cosmos call. Every id Cosmos can store still reaches it unchanged.

Found while reproducing RC-5 (docs/gtkb-requirement-changes.md).
"""

from __future__ import annotations

import secrets
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.multi_tenant.repositories.verification import VerificationTokenRepository

UNSTORABLE = [
    pytest.param("", id="empty"),
    pytest.param("abc/def", id="slash"),
    pytest.param("../../colls/other/docs/x", id="path-segments"),
    pytest.param("abc\\def", id="backslash"),
    pytest.param("x" * 1024, id="1024-bytes"),
    pytest.param("é" * 512, id="1024-bytes-multibyte"),
]

STORABLE = [
    pytest.param(secrets.token_urlsafe(32), id="link-token"),
    pytest.param("otp:tenant-1:a#b?c@example.com", id="otp-email-with-hash-and-question-mark"),
    pytest.param("sms_otp_member-1", id="sms-otp"),
    pytest.param("x" * 1023, id="1023-bytes"),
    pytest.param("é" * 511, id="1022-bytes-multibyte"),
]


@pytest.fixture
def container():
    """A Cosmos container double, returned by the repository's manager lookup."""
    fake = MagicMock()
    fake.read_item = AsyncMock(
        return_value={"id": "stored", "used": False, "tenant_id": "t1", "email": "a@b.co", "_etag": '"etag-1"'},
    )
    fake.patch_item = AsyncMock()
    fake.delete_item = AsyncMock()
    manager = MagicMock()
    manager.get_container.return_value = fake
    with patch("src.multi_tenant.repositories.verification.get_cosmos_manager", return_value=manager):
        yield fake


@pytest.mark.parametrize("token_id", UNSTORABLE)
async def test_consume_refuses_ids_cosmos_cannot_store(container, token_id):
    assert await VerificationTokenRepository().consume_token(token_id, "email_verification") is None
    container.read_item.assert_not_awaited()
    container.patch_item.assert_not_awaited()


@pytest.mark.parametrize("token_id", UNSTORABLE)
async def test_delete_refuses_ids_cosmos_cannot_store(container, token_id):
    assert await VerificationTokenRepository().delete_token(token_id, "email_verification") is False
    container.delete_item.assert_not_awaited()


@pytest.mark.parametrize("token_id", STORABLE)
async def test_storable_ids_still_reach_cosmos(container, token_id):
    doc = await VerificationTokenRepository().consume_token(token_id, "email_verification")
    assert doc is not None
    container.read_item.assert_awaited_once_with(item=token_id, partition_key="email_verification")
    container.patch_item.assert_awaited_once()


def test_confirm_page_reports_an_invalid_link_without_touching_cosmos():
    """No database here: before the guard this request reached Cosmos and got a 500 page."""
    from src.multi_tenant.email_verification import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/api/auth/verify-email/confirm", params={"token": "abc/def"})
    assert response.status_code == 400
    assert "invalid" in response.text
