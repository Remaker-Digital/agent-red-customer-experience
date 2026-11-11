# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Reads of fields that were written with the real envelope service.

A mock of encrypt or decrypt would still pass if a query returned the
stored ciphertext. These tests use EnvelopeEncryptionService in dev mode
and only substitute Cosmos and the secret store.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.provisioning import (
    BillingChannel,
    TenantRecord,
    TenantStatus,
    _provision_tenant_dek,
    configure_provisioning_repo,
    provision_tenant,
    spa_provision_tenant,
)
from src.multi_tenant.cosmos_schema import (
    BillingChannel as DocBillingChannel,
    KnowledgeBaseDocument,
    MemoryVectorDocument,
    TenantDocument,
    TenantStatus as DocTenantStatus,
)
from src.multi_tenant.envelope_encryption import (
    EnvelopeEncryptionService,
    set_envelope_encryption_service,
)
from src.multi_tenant.repositories.base import _dek_cache
from src.multi_tenant.repositories.knowledge import KnowledgeBaseRepository
from src.multi_tenant.repositories.memory import MemoryVectorRepository
from src.multi_tenant.repositories.tenant import TenantRepository
from src.multi_tenant.tenant_secret_service import TenantSecretType
from tests.helpers.fake_tenant_repo import FakeTenantRepo

_TENANT = "tenant-enc-001"
_TITLE = "Gift wrap policy"
_CONTENT = "The gift wrap note is printed on the receipt."
_CHUNK = "The customer asked about gift wrap."
_EMAIL = "owner@example.com"
_NOW = "2026-11-05T00:00:00+00:00"


@pytest.fixture
def encryption(mock_cosmos, mock_keyvault):
    """Real dev-mode service, empty DEK cache, secret store from the fixture."""
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    _dek_cache.clear()
    yield svc
    set_envelope_encryption_service(None)
    _dek_cache.clear()


async def _store_dek(svc: EnvelopeEncryptionService, mock_keyvault, tenant_id: str) -> None:
    wrapped = svc.create_tenant_dek_sync(tenant_id)
    await mock_keyvault.store_secret(
        tenant_id,
        TenantSecretType.DEK,
        base64.b64encode(wrapped).decode("ascii"),
    )


def _stored_title(mock_cosmos) -> str:
    from src.multi_tenant.cosmos_schema import COLLECTION_KNOWLEDGE_BASES

    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    return container.items[0]["title"]


@pytest.mark.unit
async def test_knowledge_vector_search_returns_plaintext(encryption, mock_cosmos, mock_keyvault):
    """A stored article is ciphertext. vector_search returns the title and content."""
    await _store_dek(encryption, mock_keyvault, _TENANT)
    repo = KnowledgeBaseRepository()
    await repo.upsert(
        _TENANT,
        KnowledgeBaseDocument(
            id="kb-enc-1",
            tenant_id=_TENANT,
            entry_type="policy",
            title=_TITLE,
            content=_CONTENT,
            embedding=[0.1, 0.2, 0.3],
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    stored = _stored_title(mock_cosmos)
    assert stored != _TITLE
    assert len(stored) >= 40

    found = await repo.vector_search(_TENANT, [0.1, 0.2, 0.3], top_k=5)
    assert len(found) == 1
    assert found[0]["title"] == _TITLE
    assert found[0]["content"] == _CONTENT


@pytest.mark.unit
async def test_memory_vector_search_returns_plaintext(encryption, mock_cosmos, mock_keyvault):
    """Layer 2 is off for the beta. The same read still has to decrypt."""
    await _store_dek(encryption, mock_keyvault, _TENANT)
    repo = MemoryVectorRepository()
    await repo.upsert(
        _TENANT,
        MemoryVectorDocument(
            id="mem-enc-1",
            tenant_id=_TENANT,
            customer_id="cust-1",
            conversation_id="conv-1",
            chunk_text=_CHUNK,
            chunk_index=0,
            embedding=[0.1, 0.2, 0.3],
            conversation_date=_NOW,
            created_at=_NOW,
        ),
    )
    found = await repo.vector_search(_TENANT, [0.1, 0.2, 0.3], top_k=5)
    assert len(found) == 1
    assert found[0]["chunk_text"] == _CHUNK


@pytest.mark.unit
async def test_tenant_lookup_by_unencrypted_key_returns_plaintext(
    encryption, mock_cosmos, mock_keyvault,
):
    """Stripe id and widget key hash are not encrypted. The email on the row is."""
    await _store_dek(encryption, mock_keyvault, _TENANT)
    repo = TenantRepository()
    await repo.upsert(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            stripe_customer_id="cus_enc_1",
            widget_key_hash="hash-enc-1",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    by_stripe = await repo.find_by_stripe_customer_id("cus_enc_1")
    by_widget = await repo.find_by_widget_key_hash("hash-enc-1")
    assert by_stripe is not None
    assert by_widget is not None
    assert by_stripe["customer_email"] == _EMAIL
    assert by_widget["customer_email"] == _EMAIL


@pytest.mark.unit
async def test_failed_dek_store_is_a_warning_and_tenant_is_created():
    """A store failure is visible. Tenant creation still succeeds."""
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    repo = FakeTenantRepo()
    configure_provisioning_repo(repo, team_repo=None)
    secret = AsyncMock()
    secret.store_secret = AsyncMock(side_effect=RuntimeError("Forbidden"))
    try:
        with (
            patch(
                "src.multi_tenant.tenant_secret_service.get_secret_service",
                return_value=secret,
            ),
            patch(
                "src.integrations.provisioning.activate_tenant",
                new_callable=AsyncMock,
                return_value=TenantRecord(
                    tenant_id="placeholder",
                    status=TenantStatus.ACTIVE,
                    billing_channel=BillingChannel.MANUAL,
                    created_at="2026-11-05T00:00:00+00:00",
                    updated_at="2026-11-05T00:00:00+00:00",
                ),
            ),
            patch(
                "src.integrations.provisioning.auto_provision_superadmin",
                new_callable=AsyncMock,
                return_value="ar_user_test",
            ),
            patch(
                "src.integrations.provisioning.auto_provision_widget_key",
                new_callable=AsyncMock,
                return_value="pk_live_test",
            ),
            patch(
                "src.multi_tenant.welcome_email.send_welcome_email",
                new_callable=AsyncMock,
            ),
        ):
            direct = await _provision_tenant_dek("tenant-warn-1")
            result = await spa_provision_tenant(
                merchant_name="Warn Shop",
                merchant_url=None,
                superadmin_email=_EMAIL,
                tier="starter",
            )
        assert direct
        assert "not stored" in direct.lower()
        assert result.tenant_id
        assert result.status
        assert any("not stored" in error.lower() for error in result.errors)
        assert secret.store_secret.await_count >= 1
    finally:
        set_envelope_encryption_service(None)
        configure_provisioning_repo(None, team_repo=None)


@pytest.mark.unit
async def test_provision_without_encryption_service_has_no_key_warning():
    """Encryption off is not a store failure. No warning is added for a missing service."""
    set_envelope_encryption_service(None)
    repo = FakeTenantRepo()
    configure_provisioning_repo(repo, team_repo=None)
    try:
        record = await provision_tenant(
            billing_channel=BillingChannel.MANUAL,
            tier="starter",
            customer_email=_EMAIL,
        )
        assert record.status == TenantStatus.PROVISIONING
        assert getattr(record, "warnings", []) == []
    finally:
        configure_provisioning_repo(None, team_repo=None)
