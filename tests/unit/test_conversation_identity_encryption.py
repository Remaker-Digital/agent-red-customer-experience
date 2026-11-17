# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""The customer's email and phone on a conversation are encrypted at rest.

P6 on v2.0.4 (run 11121234-c07a, encryption on) found ``identity_email`` in
plain text on the GC-8 conversation while every other personal field was
encrypted. These tests use the real EnvelopeEncryptionService in dev mode and
only substitute Cosmos and the secret store, so they see what is stored.

The identity preprocessor sets both fields. A patch of an encrypted field is
refused by the repository, and each writer catches that error, so a field
added to ``_encryption_fields`` without changing its writers would silently
stop being stored. Every writer is driven here.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from src.chat.identity_preprocessor import preprocess_identity
from src.multi_tenant.cosmos_schema import COLLECTION_CONVERSATIONS
from src.multi_tenant.envelope_encryption import (
    EnvelopeEncryptionService,
    set_envelope_encryption_service,
)
from src.multi_tenant.repositories.base import _dek_cache
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.tenant_secret_service import TenantSecretType

_TENANT = "tenant-identity-1"
_CONV = "conv-identity-1"
_EMAIL = "ada@example.com"
_PHONE = "+15551234567"
_NOW = "2026-11-12T00:00:00+00:00"
_PRE = "src.chat.identity_preprocessor"


@pytest.fixture
async def encryption(mock_cosmos, mock_keyvault):
    """Real dev-mode envelope service with a stored DEK for the tenant."""
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    _dek_cache.clear()
    wrapped = svc.create_tenant_dek_sync(_TENANT)
    await mock_keyvault.store_secret(_TENANT, TenantSecretType.DEK, base64.b64encode(wrapped).decode("ascii"))
    yield svc
    set_envelope_encryption_service(None)
    _dek_cache.clear()


def _container(mock_cosmos):
    return mock_cosmos.get_container(COLLECTION_CONVERSATIONS)


def _seed(mock_cosmos, **fields) -> None:
    doc = {
        "id": _CONV,
        "tenant_id": _TENANT,
        "status": "active",
        "customer_verified": False,
        "message_count": 1,
        "started_at": _NOW,
        "updated_at": _NOW,
    }
    doc.update(fields)
    _container(mock_cosmos).items.append(doc)


def _stored(mock_cosmos) -> dict:
    return next(item for item in _container(mock_cosmos).items if item["id"] == _CONV)


def _assert_ciphertext(value: object, plain: str) -> None:
    assert isinstance(value, str)
    assert value != plain
    assert plain not in value
    assert len(value) >= 40


@pytest.mark.unit
def test_the_identity_fields_are_declared_encrypted():
    fields = ConversationRepository._encryption_fields
    assert "identity_email" in fields
    assert "identity_phone" in fields


@pytest.mark.unit
async def test_an_email_given_before_any_code_is_stored_encrypted(encryption, mock_cosmos):
    _seed(mock_cosmos)
    with patch(f"{_PRE}._send_otp_for_conversation", new_callable=AsyncMock, return_value=True):
        action = await preprocess_identity(_CONV, _TENANT, f"my email is {_EMAIL}")
    assert action.action == "email_received"
    stored = _stored(mock_cosmos)
    _assert_ciphertext(stored["identity_email"], _EMAIL)
    assert stored["identity_otp_sent_at"]
    assert stored["identity_otp_attempts"] == 0
    read_back = await ConversationRepository().read(_TENANT, _CONV)
    assert read_back["identity_email"] == _EMAIL


@pytest.mark.unit
async def test_the_code_check_reads_the_decrypted_email(encryption, mock_cosmos):
    """Step two of the flow: the stored email must come back as the address."""
    _seed(mock_cosmos)
    with patch(f"{_PRE}._send_otp_for_conversation", new_callable=AsyncMock, return_value=True):
        await preprocess_identity(_CONV, _TENANT, _EMAIL)
    verify = AsyncMock(return_value=False)
    with patch(f"{_PRE}._verify_otp_for_conversation", verify):
        await preprocess_identity(_CONV, _TENANT, "123456")
    verify.assert_awaited_once()
    assert verify.await_args.kwargs.get("email", verify.await_args.args[0] if verify.await_args.args else None) == _EMAIL


@pytest.mark.unit
async def test_a_phone_given_before_any_code_is_stored_encrypted(encryption, mock_cosmos):
    _seed(mock_cosmos)
    with (
        patch("src.multi_tenant.tier_utils.check_tier_gate", new_callable=AsyncMock, return_value=True),
        patch(f"{_PRE}._send_sms_otp_for_conversation", new_callable=AsyncMock, return_value=True),
    ):
        action = await preprocess_identity(_CONV, _TENANT, _PHONE)
    assert action.action == "phone_received"
    stored = _stored(mock_cosmos)
    _assert_ciphertext(stored["identity_phone"], _PHONE)
    assert stored["identity_sms_sent_at"]
    read_back = await ConversationRepository().read(_TENANT, _CONV)
    assert read_back["identity_phone"] == _PHONE


@pytest.mark.unit
async def test_a_phone_given_while_an_email_code_is_pending_is_stored_encrypted(encryption, mock_cosmos):
    _seed(mock_cosmos, identity_otp_sent_at=_NOW, identity_otp_attempts=0)
    with (
        patch("src.multi_tenant.tier_utils.check_tier_gate", new_callable=AsyncMock, return_value=True),
        patch(f"{_PRE}._send_sms_otp_for_conversation", new_callable=AsyncMock, return_value=True),
    ):
        action = await preprocess_identity(_CONV, _TENANT, f"text me at {_PHONE}")
    assert action.action == "phone_received"
    _assert_ciphertext(_stored(mock_cosmos)["identity_phone"], _PHONE)
    assert (await ConversationRepository().read(_TENANT, _CONV))["identity_phone"] == _PHONE


@pytest.mark.unit
async def test_an_email_given_while_an_sms_code_is_pending_is_stored_encrypted(encryption, mock_cosmos):
    _seed(mock_cosmos, identity_sms_sent_at=_NOW, identity_sms_attempts=0)
    with patch(f"{_PRE}._send_otp_for_conversation", new_callable=AsyncMock, return_value=True):
        action = await preprocess_identity(_CONV, _TENANT, f"use {_EMAIL} instead")
    assert action.action == "email_received"
    _assert_ciphertext(_stored(mock_cosmos)["identity_email"], _EMAIL)
    assert (await ConversationRepository().read(_TENANT, _CONV))["identity_email"] == _EMAIL


@pytest.mark.unit
async def test_the_admin_list_returns_the_decrypted_identity(encryption, mock_cosmos):
    _seed(mock_cosmos, last_activity_at=_NOW)
    with patch(f"{_PRE}._send_otp_for_conversation", new_callable=AsyncMock, return_value=True):
        await preprocess_identity(_CONV, _TENANT, _EMAIL)
    rows = await ConversationRepository().list_filtered(_TENANT)
    assert [row["identity_email"] for row in rows if row["id"] == _CONV] == [_EMAIL]
