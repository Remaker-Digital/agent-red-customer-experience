# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""v2.0.3 reads that must return plaintext from the real envelope service."""

from __future__ import annotations

import base64
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.multi_tenant.cosmos_schema import (
    COLLECTION_KNOWLEDGE_BASES,
    ConversationDocument,
    ConversationStatus,
    CustomerProfileDocument,
    KnowledgeBaseDocument,
    PreferencesDocument,
    TeamMemberDocument,
    TeamMemberRole,
    TenantDocument,
    BillingChannel as DocBillingChannel,
    TenantStatus as DocTenantStatus,
)
from src.multi_tenant.envelope_encryption import (
    EnvelopeEncryptionService,
    set_envelope_encryption_service,
)
from src.multi_tenant.repositories.base import EncryptedFieldPatchError, _dek_cache
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.repositories.customer import CustomerProfileRepository
from src.multi_tenant.repositories.knowledge import KnowledgeBaseRepository
from src.multi_tenant.repositories.preferences import PreferencesRepository
from src.multi_tenant.repositories.team import TeamMemberRepository
from src.multi_tenant.repositories.tenant import (
    CUSTOMER_EMAIL_LOOKUP_KEY,
    TenantRepository,
)
from src.multi_tenant.tenant_secret_service import TenantSecretType

_TENANT = "tenant-v203"
_EMAIL = "Owner@Example.com"
_NOW = "2026-11-06T00:00:00+00:00"
_LOOKUP_KEY = "lookup-key-for-tests"


@pytest.fixture
def encryption(mock_cosmos, mock_keyvault):
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    _dek_cache.clear()
    yield svc
    set_envelope_encryption_service(None)
    _dek_cache.clear()


async def _store_dek(svc, mock_keyvault, tenant_id: str = _TENANT) -> None:
    wrapped = svc.create_tenant_dek_sync(tenant_id)
    await mock_keyvault.store_secret(
        tenant_id,
        TenantSecretType.DEK,
        base64.b64encode(wrapped).decode("ascii"),
    )


def _knowledge(doc_id: str, title: str, content: str) -> KnowledgeBaseDocument:
    return KnowledgeBaseDocument(
        id=doc_id,
        tenant_id=_TENANT,
        entry_type="policy",
        title=title,
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.unit
async def test_create_upsert_patch_and_list_return_plaintext(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    created = await repo.create(_TENANT, _knowledge("kb-1", "Gift wrap", "Printed on the receipt."))
    assert created["title"] == "Gift wrap"
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    assert container.items[0]["title"] != "Gift wrap"

    replaced = await repo.upsert(_TENANT, _knowledge("kb-1", "Gift wrap", "Printed on the receipt."))
    assert replaced["content"] == "Printed on the receipt."

    patched = await repo.patch(
        _TENANT,
        "kb-1",
        [{"op": "set", "path": "/is_active", "value": False}],
    )
    assert patched["title"] == "Gift wrap"
    assert patched["is_active"] is False
    with pytest.raises(EncryptedFieldPatchError):
        await repo.patch(
            _TENANT,
            "kb-1",
            [{"op": "set", "path": "/title", "value": "Changed"}],
        )

    listed = await repo.list_filtered(_TENANT, search="receipt")
    assert [row["title"] for row in listed] == ["Gift wrap"]
    assert await repo.count_filtered(_TENANT, search="receipt") == 1
    assert await repo.count_filtered(_TENANT, search="missing") == 0


@pytest.mark.unit
async def test_team_point_read_returns_plaintext(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = TeamMemberRepository()
    await repo.create(
        _TENANT,
        TeamMemberDocument(
            id=f"{_TENANT}:{_EMAIL}",
            tenant_id=_TENANT,
            email=_EMAIL,
            display_name="Owner",
            role=TeamMemberRole.ADMIN,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    found = await repo.find_by_email(_TENANT, _EMAIL)
    assert found is not None
    assert found["email"] == _EMAIL
    assert found["display_name"] == "Owner"
    members = await repo.list_members(_TENANT)
    assert members[0]["email"] == _EMAIL
    assert await repo.find_by_email(_TENANT, "missing@example.com") is None


@pytest.mark.unit
async def test_find_by_email_matches_a_canonical_member(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = TeamMemberRepository()
    canonical = "ada@example.com"
    await repo.create(
        _TENANT,
        TeamMemberDocument(
            id=f"{_TENANT}:{canonical}",
            tenant_id=_TENANT,
            email=canonical,
            display_name="Ada",
            role=TeamMemberRole.ADMIN,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    found = await repo.find_by_email(_TENANT, " Ada@Example.com ")
    assert found is not None
    assert found["id"] == f"{_TENANT}:{canonical}"
    assert found["email"] == canonical


@pytest.mark.unit
async def test_invite_and_customer_admin_store_the_canonical_address(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    admin_tenant = "tenant-v203-admin"
    await _store_dek(encryption, mock_keyvault, admin_tenant)
    from src.multi_tenant.admin_team_api import (
        CreateTeamMemberRequest,
        configure_admin_team_services,
        create_team_member,
        provision_customer_admin,
    )
    from src.multi_tenant.cosmos_schema import COLLECTION_TEAM_MEMBERS

    repo = TeamMemberRepository()
    configure_admin_team_services(repo)
    ctx = MagicMock()
    ctx.tenant_id = _TENANT
    ctx.user_id = "admin"
    ctx.team_member_email = "admin@example.com"
    try:
        with patch(
            "src.multi_tenant.alert_delivery.send_team_invite_alert",
            return_value=None,
        ):
            created = await create_team_member(
                CreateTeamMemberRequest(
                    email="Ada@Example.com",
                    display_name="Ada",
                    role="admin",
                ),
                ctx=ctx,
            )
        provisioned = await provision_customer_admin(admin_tenant, "Ada@Example.com")
    finally:
        configure_admin_team_services(None)

    canonical = "ada@example.com"
    assert created.id == f"{_TENANT}:{canonical}"
    assert created.email == canonical
    assert provisioned["member_id"] == f"{admin_tenant}:{canonical}"
    assert provisioned["email"] == canonical
    stored_ids = [
        item["id"] for item in mock_cosmos.get_container(COLLECTION_TEAM_MEMBERS).items
    ]
    assert stored_ids == [f"{_TENANT}:{canonical}", f"{admin_tenant}:{canonical}"]


@pytest.mark.unit
async def test_a_typed_member_id_is_still_a_duplicate(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    from fastapi import HTTPException

    from src.multi_tenant.admin_team_api import (
        CreateTeamMemberRequest,
        configure_admin_team_services,
        create_team_member,
        provision_customer_admin,
    )
    from src.multi_tenant.cosmos_schema import COLLECTION_TEAM_MEMBERS

    typed = "Ada@Example.com"
    repo = TeamMemberRepository()
    await repo.create(
        _TENANT,
        TeamMemberDocument(
            id=f"{_TENANT}:{typed}",
            tenant_id=_TENANT,
            email=typed,
            display_name="Ada",
            role=TeamMemberRole.ADMIN,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    configure_admin_team_services(repo)
    ctx = MagicMock()
    ctx.tenant_id = _TENANT
    ctx.user_id = "admin"
    ctx.team_member_email = "admin@example.com"
    try:
        with pytest.raises(HTTPException) as exc_info:
            await create_team_member(
                CreateTeamMemberRequest(email=typed, display_name="Ada", role="viewer"),
                ctx=ctx,
            )
        provisioned = await provision_customer_admin(_TENANT, typed)
    finally:
        configure_admin_team_services(None)

    assert exc_info.value.status_code == 409
    assert provisioned["created"] is False
    assert provisioned["member_id"] == f"{_TENANT}:{typed}"
    stored_ids = [
        item["id"] for item in mock_cosmos.get_container(COLLECTION_TEAM_MEMBERS).items
    ]
    assert stored_ids == [f"{_TENANT}:{typed}"]


@pytest.mark.unit
async def test_superadmin_provisioning_stores_the_canonical_address(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    from src.integrations.provisioning import auto_provision_superadmin
    from src.multi_tenant.cosmos_schema import COLLECTION_TEAM_MEMBERS

    key = await auto_provision_superadmin(_TENANT, "Ada@Example.com")
    assert key
    stored = mock_cosmos.get_container(COLLECTION_TEAM_MEMBERS).items
    assert [item["id"] for item in stored] == [f"{_TENANT}:ada@example.com"]
    assert await auto_provision_superadmin(_TENANT, "Ada@Example.com") is None
    assert [item["id"] for item in stored] == [f"{_TENANT}:ada@example.com"]


@pytest.mark.unit
async def test_owner_email_lookup_uses_the_keyed_hash(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    stored = mock_cosmos.get_container(COLLECTION_TENANTS).items[0]
    assert stored["customer_email"] != _EMAIL
    assert stored["customer_email_hash"]
    found = await repo.find_by_customer_email("owner@example.com")
    assert found is not None
    assert found["customer_email"] == _EMAIL

    rows = await repo.cross_partition_query(
        "SELECT * FROM c WHERE c.id = @id",
        [{"name": "@id", "value": _TENANT}],
    )
    assert rows[0]["customer_email"] == _EMAIL


@pytest.mark.unit
async def test_clearing_the_owner_email_removes_the_lookup_hash(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    await repo.update_encrypted_fields(_TENANT, _TENANT, {"customer_email": None})
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    stored = mock_cosmos.get_container(COLLECTION_TENANTS).items[0]
    assert stored.get("customer_email") is None
    assert "customer_email_hash" not in stored


@pytest.mark.unit
async def test_owner_email_lookup_without_the_key_returns_none(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.MANUAL,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    _filter_email_queries(mock_cosmos.get_container(COLLECTION_TENANTS))
    assert await repo.find_by_customer_email(_EMAIL) is None


@pytest.mark.unit
async def test_one_bad_row_does_not_stop_the_list(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    await repo.create(_TENANT, _knowledge("kb-good", "Good title", "Good content"))
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    container.items.append(
        {
            "id": "kb-bad",
            "tenant_id": _TENANT,
            "entry_type": "policy",
            "title": base64.b64encode(b"not-a-real-ciphertext-value!!").decode("ascii"),
            "content": "plain",
            "is_active": True,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    rows = await repo.query(_TENANT, "SELECT * FROM c", [])
    assert any(row["id"] == "kb-good" and row["title"] == "Good title" for row in rows)
    assert all(row["id"] != "kb-bad" for row in rows)


@pytest.mark.unit
async def test_conversation_customer_and_preferences_lists_return_plaintext(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    messages = [{"role": "customer", "content": "Where is my order?"}]
    conversations = ConversationRepository()
    created = await conversations.create(
        _TENANT,
        ConversationDocument(
            id="conv-1",
            tenant_id=_TENANT,
            conversation_id="conv-1",
            status=ConversationStatus.ACTIVE,
            customer_id="cust-1",
            messages=messages,
            started_at=_NOW,
            last_activity_at=_NOW,
        ),
    )
    assert created["messages"] == messages
    listed = await conversations.list_by_customer(_TENANT, "cust-1")
    assert listed[0]["messages"] == messages

    customers = CustomerProfileRepository()
    profile = await customers.create(
        _TENANT,
        CustomerProfileDocument(
            id=f"{_TENANT}:cid-1",
            tenant_id=_TENANT,
            customer_id="cid-1",
            canonical_id="cid-1",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    assert profile["customer_id"] == "cid-1"
    consented = await customers.list_with_consent(_TENANT, "not_asked")
    assert consented[0]["id"] == f"{_TENANT}:cid-1"

    preferences = PreferencesRepository()
    saved = await preferences.create(
        _TENANT,
        PreferencesDocument(
            id=f"{_TENANT}:1",
            tenant_id=_TENANT,
            version=1,
            custom_instructions="Mention the gift wrap.",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    assert saved["custom_instructions"] == "Mention the gift wrap."
    versions = await preferences.list_versions(_TENANT)
    assert versions[0]["custom_instructions"] == "Mention the gift wrap."


def _filter_email_queries(container) -> None:
    """Honor the owner-email predicates the in-memory container otherwise ignores."""
    from tests.conftest import MockQueryIterator

    def query_items(query, parameters=None, **kwargs):
        items = list(container.items)
        params = {item["name"]: item["value"] for item in (parameters or [])}
        if "c.customer_email_hash = @email_hash" in query:
            items = [
                row for row in items
                if row.get("customer_email_hash") == params.get("@email_hash")
            ]
        elif "c.customer_email = @email" in query:
            items = [
                row for row in items
                if row.get("customer_email") == params.get("@email")
            ]
        return MockQueryIterator(items)

    container.query_items = query_items


def _project_select(query: str, items: list[dict]) -> list[dict]:
    """Apply a Cosmos SELECT list so a missing c.id is really missing."""
    import re

    from tests.conftest import MockQueryIterator

    if "COUNT" in query.upper():
        return MockQueryIterator([len(items)])
    if re.search(r"SELECT\s+\*", query, re.I):
        return MockQueryIterator([dict(item) for item in items])
    match = re.search(r"SELECT\s+(.+?)\s+FROM", query, re.I | re.S)
    if match is None:
        return MockQueryIterator([dict(item) for item in items])
    columns = []
    for part in match.group(1).split(","):
        part = part.strip()
        if part.startswith("c."):
            columns.append(part.split()[0][2:])
    projected = []
    for item in items:
        projected.append({column: item.get(column) for column in columns})
    return MockQueryIterator(projected)


def _use_exact_select(container):
    def query_items(query, parameters=None, **kwargs):
        return _project_select(query, list(container.items))

    container.query_items = query_items


async def _encrypted_tenant(encryption, mock_keyvault, *, email=_EMAIL, brand=None):
    await _store_dek(encryption, mock_keyvault)
    repo = TenantRepository()
    doc = TenantDocument(
        id=_TENANT,
        tenant_id=_TENANT,
        status=DocTenantStatus.ACTIVE,
        billing_channel=DocBillingChannel.STRIPE,
        customer_email=email,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await repo.create(_TENANT, doc)
    return repo


@pytest.mark.unit
async def test_directory_pipeline_and_service_messages_decrypt_projected_rows(
    encryption, mock_cosmos, mock_keyvault,
):
    """The three console queries must return plaintext from their own SELECT."""
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS
    from src.multi_tenant.superadmin_api import _monolith as state
    from src.multi_tenant.superadmin_api._platform import (
        _resolve_service_message_recipients,
        get_tenant_comparison,
    )
    from src.multi_tenant.superadmin_api._tenants import list_all_tenants

    repo = await _encrypted_tenant(encryption, mock_keyvault)
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    _use_exact_select(container)
    state._tenant_repo = repo
    try:
        directory = await list_all_tenants(
            status=None,
            tier=None,
            billing_channel=None,
            skip=0,
            limit=50,
        )
        assert directory.tenants[0].customer_email == "O***@Example.com"

        class _Aggregator:
            async def get_tenant_comparison(self, period):
                return {}

        with patch(
            "src.multi_tenant.superadmin_api._platform.get_aggregator",
            return_value=_Aggregator(),
        ):
            comparison = await get_tenant_comparison()
        assert comparison.tenants[0].display_name == "O***@Example.com"

        recipients = await _resolve_service_message_recipients()
        assert [item.email for item in recipients] == [_EMAIL]
    finally:
        state._tenant_repo = None


@pytest.mark.unit
async def test_point_read_of_a_bad_row_raises(encryption, mock_cosmos, mock_keyvault):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    await repo.create(_TENANT, _knowledge("kb-good", "Good title", "Good content"))
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    container.items.append(
        {
            "id": "kb-bad",
            "tenant_id": _TENANT,
            "entry_type": "policy",
            "title": base64.b64encode(b"not-a-real-ciphertext-value!!").decode("ascii"),
            "content": "plain",
            "is_active": True,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with pytest.raises(Exception):
        await repo.read(_TENANT, "kb-bad")


@pytest.mark.unit
async def test_update_encrypted_fields_on_a_bad_row_writes_nothing(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    stored_title = base64.b64encode(b"not-a-real-ciphertext-value!!").decode("ascii")
    container.items.append(
        {
            "id": "kb-bad",
            "tenant_id": _TENANT,
            "entry_type": "policy",
            "title": stored_title,
            "content": "plain",
            "is_active": True,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with pytest.raises(Exception):
        await repo.update_encrypted_fields(_TENANT, "kb-bad", {"title": "Changed"})
    assert container.items[0]["title"] == stored_title


@pytest.mark.unit
async def test_owner_lookup_falls_back_to_plaintext_email(encryption, mock_cosmos, mock_keyvault):
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    await _store_dek(encryption, mock_keyvault)
    repo = TenantRepository()
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    container.items.append(
        {
            "id": _TENANT,
            "tenant_id": _TENANT,
            "status": "active",
            "customer_email": _EMAIL,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    _filter_email_queries(container)
    found = await repo.find_by_customer_email(_EMAIL)
    assert found is not None
    assert found["customer_email"] == _EMAIL

    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    found_with_key = await repo.find_by_customer_email(_EMAIL)
    assert found_with_key is not None
    assert found_with_key["customer_email"] == _EMAIL


@pytest.mark.unit
async def test_owner_lookup_uses_the_hash_and_an_email_change_replaces_it(
    encryption, mock_cosmos, mock_keyvault,
):
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    await _store_dek(encryption, mock_keyvault)
    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    _filter_email_queries(container)
    first_hash = container.items[0]["customer_email_hash"]
    found = await repo.find_by_customer_email("owner@example.com")
    assert found is not None
    assert found["customer_email"] == _EMAIL

    await repo.update_encrypted_fields(_TENANT, _TENANT, {"customer_email": "next@example.com"})
    assert container.items[0]["customer_email_hash"] != first_hash
    assert await repo.find_by_customer_email(_EMAIL) is None
    changed = await repo.find_by_customer_email("next@example.com")
    assert changed is not None
    assert changed["customer_email"] == "next@example.com"

    mock_keyvault._store.pop(CUSTOMER_EMAIL_LOOKUP_KEY, None)
    await repo.update_encrypted_fields(_TENANT, _TENANT, {"customer_email": "later@example.com"})
    assert "customer_email_hash" not in container.items[0]
    assert await repo.find_by_customer_email("next@example.com") is None


def _raise_dek_fetch(mock_keyvault, *, tenant_id: str | None = None, times: int | None = None):
    """Make DEK reads fail. Absence stays a normal None from the untouched store."""
    store = mock_keyvault._store
    calls = {"n": 0}

    async def get_secret(tid: str, secret_type) -> str | None:
        calls["n"] += 1
        targeted = tenant_id is None or tid == tenant_id
        if targeted and (times is None or calls["n"] <= times):
            raise RuntimeError("key vault unavailable")
        return store.get(f"tenant-{tid}-{secret_type.value}")

    mock_keyvault.get_secret = get_secret
    return calls


@pytest.mark.unit
async def test_a_failed_dek_read_writes_nothing_when_the_next_fetch_works(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    await repo.create(_TENANT, _knowledge("kb-1", "Gift wrap", "Printed on the receipt."))
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    stored_title = container.items[0]["title"]
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault, times=1)
    with pytest.raises(Exception) as exc:
        await repo.update_encrypted_fields(_TENANT, "kb-1", {"title": "Changed"})
    assert type(exc.value).__name__ == "TenantDekUnavailableError"
    assert container.items[0]["title"] == stored_title


@pytest.mark.unit
async def test_a_failed_dek_write_does_not_store_plain_text(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault)
    repo = KnowledgeBaseRepository()
    with pytest.raises(Exception) as exc:
        await repo.create(_TENANT, _knowledge("kb-new", "Plain title", "Plain content"))
    assert type(exc.value).__name__ == "TenantDekUnavailableError"
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    assert all(item.get("title") != "Plain title" for item in container.items)


@pytest.mark.unit
async def test_no_dek_still_passes_plain_text_both_ways(encryption, mock_cosmos, mock_keyvault):
    repo = KnowledgeBaseRepository()
    created = await repo.create(_TENANT, _knowledge("kb-plain", "Plain title", "Plain content"))
    assert created["title"] == "Plain title"
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    assert container.items[0]["title"] == "Plain title"
    assert (await repo.read(_TENANT, "kb-plain"))["title"] == "Plain title"


@pytest.mark.unit
async def test_a_single_tenant_query_raises_when_the_dek_fetch_fails(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    await repo.create(_TENANT, _knowledge("kb-1", "Gift wrap", "Printed on the receipt."))
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault)
    with pytest.raises(Exception) as exc:
        await repo.query(_TENANT, "SELECT * FROM c", [])
    assert type(exc.value).__name__ == "TenantDekUnavailableError"


@pytest.mark.unit
async def test_an_id_query_returns_ids_when_the_dek_fetch_fails(
    encryption, mock_cosmos, mock_keyvault,
):
    await _store_dek(encryption, mock_keyvault)
    repo = KnowledgeBaseRepository()
    await repo.create(_TENANT, _knowledge("kb-1", "Gift wrap", "Printed on the receipt."))
    container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
    _use_exact_select(container)
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault)
    rows = await repo.query(_TENANT, "SELECT c.id FROM c", [])
    assert [row["id"] for row in rows] == ["kb-1"]


@pytest.mark.unit
async def test_customer_erasure_deletes_conversations_when_the_dek_fetch_fails(
    encryption, mock_cosmos, mock_keyvault,
):
    from unittest.mock import AsyncMock, MagicMock

    from src.multi_tenant.cosmos_schema import COLLECTION_CONVERSATIONS
    from src.multi_tenant.gdpr_services import CosmosDataStoreAdapter

    await _store_dek(encryption, mock_keyvault)
    conversations = ConversationRepository()
    await conversations.create(
        _TENANT,
        ConversationDocument(
            id="conv-1",
            tenant_id=_TENANT,
            conversation_id="conv-1",
            status=ConversationStatus.ACTIVE,
            customer_id="cust-1",
            messages=[{"role": "customer", "content": "Where is my order?"}],
            started_at=_NOW,
            last_activity_at=_NOW,
        ),
    )
    container = mock_cosmos.get_container(COLLECTION_CONVERSATIONS)
    _use_exact_select(container)
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault)
    profiles = MagicMock()
    profiles.get_by_customer_id = AsyncMock(return_value=None)
    memory = MagicMock()
    memory.delete_by_customer = AsyncMock(return_value=0)
    adapter = CosmosDataStoreAdapter(
        tenant_repo=MagicMock(),
        conversation_repo=conversations,
        usage_repo=MagicMock(),
        customer_profile_repo=profiles,
        knowledge_base_repo=MagicMock(),
        memory_vector_repo=memory,
        preferences_repo=MagicMock(),
    )
    result = await adapter.delete_customer_data(_TENANT, "cust-1")
    assert result["conversations"]["deleted"] == 1
    assert container.items == []


@pytest.mark.unit
async def test_a_cross_partition_list_skips_a_tenant_whose_dek_fetch_fails(
    encryption, mock_cosmos, mock_keyvault,
):
    other = "tenant-other"
    await _store_dek(encryption, mock_keyvault, _TENANT)
    await _store_dek(encryption, mock_keyvault, other)
    repo = TenantRepository()
    for tenant_id, email in ((_TENANT, _EMAIL), (other, "other@example.com")):
        await repo.create(
            tenant_id,
            TenantDocument(
                id=tenant_id,
                tenant_id=tenant_id,
                status=DocTenantStatus.ACTIVE,
                billing_channel=DocBillingChannel.STRIPE,
                customer_email=email,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        )
    _dek_cache.clear()
    _raise_dek_fetch(mock_keyvault, tenant_id=_TENANT)
    rows = await repo.cross_partition_query("SELECT * FROM c")
    assert [row["tenant_id"] for row in rows] == [other]
    assert rows[0]["customer_email"] == "other@example.com"
    listed = await repo.list_expired_tenants()
    assert [row["tenant_id"] for row in listed] == [other]


@pytest.mark.unit
async def test_a_missing_lookup_key_on_write_removes_the_old_hash(
    encryption, mock_cosmos, mock_keyvault,
):
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    await _store_dek(encryption, mock_keyvault)
    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    assert container.items[0]["customer_email_hash"]
    mock_keyvault._store.pop(CUSTOMER_EMAIL_LOOKUP_KEY, None)
    await repo.update_encrypted_fields(_TENANT, _TENANT, {"customer_email": "later@example.com"})
    assert "customer_email_hash" not in container.items[0]


@pytest.mark.unit
async def test_a_failed_lookup_key_read_on_write_leaves_the_document_unchanged(
    encryption, mock_cosmos, mock_keyvault,
):
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    await _store_dek(encryption, mock_keyvault)
    await mock_keyvault.set_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY, _LOOKUP_KEY)
    repo = TenantRepository()
    await repo.create(
        _TENANT,
        TenantDocument(
            id=_TENANT,
            tenant_id=_TENANT,
            status=DocTenantStatus.ACTIVE,
            billing_channel=DocBillingChannel.STRIPE,
            customer_email=_EMAIL,
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    stored_email = container.items[0]["customer_email"]
    stored_hash = container.items[0]["customer_email_hash"]
    original_read = container.read_item

    async def read_item(item, partition_key, **kwargs):
        return dict(await original_read(item, partition_key, **kwargs))

    container.read_item = read_item

    async def get_secret_raw(name: str):
        raise RuntimeError("key vault unavailable")

    mock_keyvault.get_secret_raw = get_secret_raw
    with pytest.raises(Exception):
        await repo.update_encrypted_fields(
            _TENANT, _TENANT, {"customer_email": "other@example.com"},
        )
    assert container.items[0]["customer_email"] == stored_email
    assert container.items[0]["customer_email_hash"] == stored_hash


@pytest.mark.unit
async def test_a_failed_lookup_key_read_uses_the_plain_query(encryption, mock_cosmos, mock_keyvault, caplog):
    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS

    repo = TenantRepository()
    container = mock_cosmos.get_container(COLLECTION_TENANTS)
    container.items.append(
        {
            "id": _TENANT,
            "tenant_id": _TENANT,
            "status": "active",
            "customer_email": _EMAIL,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    _filter_email_queries(container)

    async def get_secret_raw(name: str):
        raise RuntimeError("key vault unavailable")

    mock_keyvault.get_secret_raw = get_secret_raw
    with caplog.at_level("WARNING"):
        found = await repo.find_by_customer_email(_EMAIL)
    assert found is not None
    assert found["customer_email"] == _EMAIL
    assert "could not be read" in caplog.text
    assert "is not stored" not in caplog.text


@pytest.mark.unit
async def test_customer_erasure_deletes_a_conversation_that_cannot_be_decrypted(
    encryption, mock_cosmos, mock_keyvault,
):
    from unittest.mock import AsyncMock, MagicMock

    from src.multi_tenant.cosmos_schema import COLLECTION_CONVERSATIONS
    from src.multi_tenant.gdpr_services import CosmosDataStoreAdapter

    await _store_dek(encryption, mock_keyvault)
    conversations = ConversationRepository()
    await conversations.create(
        _TENANT,
        ConversationDocument(
            id="conv-good",
            tenant_id=_TENANT,
            conversation_id="conv-good",
            status=ConversationStatus.ACTIVE,
            customer_id="cust-1",
            messages=[{"role": "customer", "content": "Where is my order?"}],
            started_at=_NOW,
            last_activity_at=_NOW,
        ),
    )
    container = mock_cosmos.get_container(COLLECTION_CONVERSATIONS)
    container.items.append(
        {
            "id": "conv-bad",
            "tenant_id": _TENANT,
            "conversation_id": "conv-bad",
            "status": "active",
            "customer_id": "cust-1",
            "messages": base64.b64encode(b"not-a-real-ciphertext-value!!").decode("ascii"),
            "started_at": _NOW,
            "last_activity_at": _NOW,
        }
    )
    _use_exact_select(container)
    profiles = MagicMock()
    profiles.get_by_customer_id = AsyncMock(return_value=None)
    memory = MagicMock()
    memory.delete_by_customer = AsyncMock(return_value=0)
    adapter = CosmosDataStoreAdapter(
        tenant_repo=MagicMock(),
        conversation_repo=conversations,
        usage_repo=MagicMock(),
        customer_profile_repo=profiles,
        knowledge_base_repo=MagicMock(),
        memory_vector_repo=memory,
        preferences_repo=MagicMock(),
    )
    result = await adapter.delete_customer_data(_TENANT, "cust-1")
    assert result["conversations"]["deleted"] == 2
    assert container.items == []


@pytest.mark.unit
async def test_a_bad_team_row_does_not_log_the_email(encryption, mock_cosmos, mock_keyvault, caplog):
    from src.multi_tenant.cosmos_schema import COLLECTION_TEAM_MEMBERS

    await _store_dek(encryption, mock_keyvault)
    repo = TeamMemberRepository()
    email = "owner@example.com"
    container = mock_cosmos.get_container(COLLECTION_TEAM_MEMBERS)
    container.items.append(
        {
            "id": f"{_TENANT}:{email}",
            "tenant_id": _TENANT,
            "email": base64.b64encode(b"not-a-real-ciphertext-value!!").decode("ascii"),
            "display_name": "Owner",
            "role": "admin",
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )
    with caplog.at_level("WARNING"):
        listed = await repo.list_members(_TENANT)
    assert listed == []
    assert email not in caplog.text


@pytest.mark.unit
async def test_an_uninitialized_vault_passes_plain_text(mock_cosmos):
    """A vault that was never opened is not a fetch failure."""
    from unittest.mock import patch

    from src.multi_tenant.tenant_secret_service import TenantSecretService

    fresh = TenantSecretService()
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    _dek_cache.clear()
    try:
        with patch(
            "src.multi_tenant.tenant_secret_service.get_secret_service",
            return_value=fresh,
        ):
            repo = KnowledgeBaseRepository()
            created = await repo.create(
                _TENANT, _knowledge("kb-plain", "Plain title", "Plain content"),
            )
        assert created["title"] == "Plain title"
        container = mock_cosmos.get_container(COLLECTION_KNOWLEDGE_BASES)
        assert container.items[0]["title"] == "Plain title"
    finally:
        set_envelope_encryption_service(None)
        _dek_cache.clear()


@pytest.mark.unit
async def test_an_uninitialized_vault_does_not_fail_an_owner_email_write(mock_cosmos):
    """The lookup key is absent until the vault is opened. The write still completes."""
    from unittest.mock import patch

    from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS
    from src.multi_tenant.tenant_secret_service import TenantSecretService

    fresh = TenantSecretService()
    svc = EnvelopeEncryptionService(dev_mode=True)
    set_envelope_encryption_service(svc)
    _dek_cache.clear()
    try:
        with patch(
            "src.multi_tenant.tenant_secret_service.get_secret_service",
            return_value=fresh,
        ):
            repo = TenantRepository()
            await repo.create(
                _TENANT,
                TenantDocument(
                    id=_TENANT,
                    tenant_id=_TENANT,
                    status=DocTenantStatus.ACTIVE,
                    billing_channel=DocBillingChannel.STRIPE,
                    customer_email=_EMAIL,
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
            )
        stored = mock_cosmos.get_container(COLLECTION_TENANTS).items[0]
        assert stored["customer_email"] == _EMAIL
        assert "customer_email_hash" not in stored
    finally:
        set_envelope_encryption_service(None)
        _dek_cache.clear()


# SonarCloud log injection (decision 70): a CR or LF in an address or a
# tenant id must not start a new line in the log.
_FORGED = "\r\nFORGED entry"


def _one_line_messages(caplog, marker: str) -> list[str]:
    messages = [record.getMessage() for record in caplog.records if marker in record.getMessage()]
    assert messages, f"no log line containing {marker!r}"
    for message in messages:
        assert "\r" not in message
        assert "\n" not in message
    return messages


@pytest.mark.unit
def test_loggable_removes_line_breaks():
    from src.multi_tenant.log_safety import loggable

    assert loggable("a\r\nb\nc\rd") == "abcd"
    assert loggable(None) == "None"


@pytest.mark.unit
async def test_team_member_creation_log_stays_one_line(
    encryption, mock_cosmos, mock_keyvault, caplog,
):
    await _store_dek(encryption, mock_keyvault)
    from src.multi_tenant.admin_team_api import (
        CreateTeamMemberRequest,
        configure_admin_team_services,
        create_team_member,
    )

    configure_admin_team_services(TeamMemberRepository())
    ctx = MagicMock()
    ctx.tenant_id = _TENANT
    ctx.user_id = "admin"
    ctx.team_member_email = "admin@example.com"
    caplog.set_level(logging.INFO)
    try:
        with patch(
            "src.multi_tenant.alert_delivery.send_team_invite_alert",
            return_value=None,
        ):
            await create_team_member(
                CreateTeamMemberRequest(
                    email=f"ada@example.com{_FORGED}",
                    display_name="Ada",
                    role="admin",
                ),
                ctx=ctx,
            )
    finally:
        configure_admin_team_services(None)

    [message] = _one_line_messages(caplog, "Team member created")
    assert "forged entry" in message.lower()


@pytest.mark.unit
async def test_superadmin_provisioning_log_stays_one_line(
    encryption, mock_cosmos, mock_keyvault, caplog,
):
    await _store_dek(encryption, mock_keyvault)
    from src.integrations.provisioning import auto_provision_superadmin

    caplog.set_level(logging.INFO)
    assert await auto_provision_superadmin(_TENANT, f"ada@example.com{_FORGED}")
    [message] = _one_line_messages(caplog, "Superadmin auto-provisioned")
    assert "forged entry" in message.lower()


@pytest.mark.unit
async def test_a_failed_dek_fetch_log_stays_one_line(
    encryption, mock_cosmos, mock_keyvault, caplog,
):
    tenant = f"{_TENANT}{_FORGED}"
    _raise_dek_fetch(mock_keyvault)
    caplog.set_level(logging.WARNING)
    doc = _knowledge("kb-new", "Title", "Content").model_copy(update={"tenant_id": tenant})
    with pytest.raises(Exception) as exc:
        await KnowledgeBaseRepository().create(tenant, doc)
    assert type(exc.value).__name__ == "TenantDekUnavailableError"
    _one_line_messages(caplog, "Failed to fetch DEK")
