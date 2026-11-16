"""Exercise scheduled retention startup with a fake Cosmos SDK."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs import run_retention
from src.multi_tenant import cosmos_client
from src.multi_tenant.cosmos_schema import COLLECTION_TENANTS


@pytest.mark.parametrize("query_fails", [False, True])
async def test_fresh_retention_job_owns_cosmos_connection(monkeypatch, caplog, query_fails):
    manager = cosmos_client.CosmosManager()
    client = MagicMock()
    client.close = AsyncMock()
    database = client.get_database_client.return_value
    tenants = database.get_container_client.return_value

    async def tenant_rows():
        client.__aenter__.assert_awaited_once_with()
        if query_fails:
            raise RuntimeError("synthetic query failure")
        yield {"tenant_id": "tenant-enterprise", "tier": "enterprise"}

    tenants.query_items.return_value = tenant_rows()
    constructor = MagicMock(return_value=client)
    provision = AsyncMock(side_effect=AssertionError("retention must not provision Cosmos"))
    monkeypatch.setattr(cosmos_client, "_manager", manager)
    monkeypatch.setattr(cosmos_client, "CosmosClient", constructor)
    monkeypatch.setattr(cosmos_client, "initialize_database", provision)
    monkeypatch.setattr(
        cosmos_client,
        "os",
        SimpleNamespace(
            environ={
                "COSMOS_DB_ENDPOINT": "https://cosmos.invalid",
                "COSMOS_DB_KEY": "fake-test-value",
                "COSMOS_DB_DATABASE": "test-db",
            }
        ),
    )
    caplog.set_level("INFO", logger=run_retention.__name__)

    assert await run_retention.main() == int(query_fails)

    constructor.assert_called_once()
    database.get_container_client.assert_called_once_with(COLLECTION_TENANTS)
    tenants.query_items.assert_called_once()
    provision.assert_not_awaited()
    client.close.assert_awaited_once_with()
    if not query_fails:
        assert "tenants=1 conversations_deleted=0 profiles_purged=0 vectors_deleted=0 errors=0" in caplog.text


async def test_retention_deletes_expired_conversation_in_its_tenant_partition(monkeypatch):
    from src.multi_tenant.data_retention import DataRetentionService
    from src.multi_tenant.repository import AuditLogRepository, ConversationRepository, CustomerProfileRepository

    tenant_id = "tenant-starter"
    repository = ConversationRepository()
    container = MagicMock()
    container.delete_item = AsyncMock()

    async def expired_rows():
        yield {"id": "expired-conversation"}

    container.query_items.return_value = expired_rows()
    profiles = MagicMock()

    async def expired_profiles():
        yield {"id": "expired-profile", "_etag": "profile-etag", "has_history": True}

    profiles.query_items.return_value = expired_profiles()
    profiles.patch_item = AsyncMock(return_value={"id": "expired-profile"})
    audit = MagicMock()
    audit.create_item = AsyncMock()
    service = DataRetentionService(
        tenant_repo=MagicMock(),
        conversation_repo=repository,
        profile_repo=CustomerProfileRepository(),
        audit_repo=AuditLogRepository(),
    )
    monkeypatch.setattr(ConversationRepository, "_container", property(lambda _: container))
    monkeypatch.setattr(CustomerProfileRepository, "_container", property(lambda _: profiles))
    monkeypatch.setattr(AuditLogRepository, "_container", property(lambda _: audit))
    result = await service._enforce_for_tenant(tenant_id, "starter", 90, "2026-01-01T00:00:00Z")

    assert result.conversations_deleted == 1
    assert result.profiles_purged == 1
    assert profiles.query_items.call_args.kwargs["partition_key"] == tenant_id
    assert profiles.patch_item.call_args.kwargs["partition_key"] == tenant_id
    assert profiles.patch_item.call_args.kwargs["item"] == "expired-profile"
    assert profiles.patch_item.call_args.kwargs["etag"] == "profile-etag"
    assert audit.create_item.call_args.kwargs["body"]["payload"]["count"] == 2
    assert container.query_items.call_args.kwargs["partition_key"] == tenant_id
    container.delete_item.assert_awaited_once_with(item="expired-conversation", partition_key=tenant_id)
