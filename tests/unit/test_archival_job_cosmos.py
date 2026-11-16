"""Exercise the scheduled archival dry run with an offline Cosmos SDK fake."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.jobs import run_archival
from src.multi_tenant import cosmos_client
from src.multi_tenant.cosmos_schema import (
    COLLECTION_CONVERSATIONS,
    COLLECTION_CUSTOMER_PROFILES,
    COLLECTION_MEMORY_VECTORS,
    COLLECTION_TENANTS,
)


@pytest.fixture
def offline_cosmos(monkeypatch):
    manager = cosmos_client.CosmosManager()
    client = MagicMock()
    client.close = AsyncMock()
    database = client.get_database_client.return_value
    rows = {
        COLLECTION_TENANTS: [{"tenant_id": "tenant-enterprise", "tier": "enterprise"}],
        COLLECTION_CONVERSATIONS: [1],
        COLLECTION_CUSTOMER_PROFILES: [2],
        COLLECTION_MEMORY_VECTORS: [0],
    }
    containers = {name: MagicMock() for name in rows}

    async def query_rows(documents):
        client.__aenter__.assert_awaited_once_with()
        for document in documents:
            yield document

    for name, container in containers.items():
        container.query_items.return_value = query_rows(rows[name])
    database.get_container_client.side_effect = containers.__getitem__
    constructor = MagicMock(return_value=client)
    provision = AsyncMock(side_effect=AssertionError("archival dry run must not provision Cosmos"))
    monkeypatch.setattr(cosmos_client, "_manager", manager)
    monkeypatch.setattr(cosmos_client, "CosmosClient", constructor)
    monkeypatch.setattr(cosmos_client, "initialize_database", provision)
    monkeypatch.setattr(cosmos_client, "os", SimpleNamespace(environ={
        "COSMOS_DB_ENDPOINT": "https://cosmos.invalid",
        "COSMOS_DB_KEY": "fake-test-value",
        "COSMOS_DB_DATABASE": "test-db",
    }))
    monkeypatch.setattr(run_archival, "os", SimpleNamespace(environ={}))
    return manager, constructor, client, containers, provision


async def test_fresh_archival_job_connects_queries_and_closes_without_provisioning(offline_cosmos, caplog):
    manager, constructor, client, containers, provision = offline_cosmos
    caplog.set_level("INFO", logger=run_archival.__name__)

    assert await run_archival.main() == 0

    constructor.assert_called_once()
    client.get_database_client.assert_called_once_with("test-db")
    assert client.mock_calls[0] == call.get_database_client("test-db")
    assert client.mock_calls[-1] == call.close()
    client.__aenter__.assert_awaited_once_with()
    provision.assert_not_awaited()
    client.close.assert_awaited_once_with()
    for container in containers.values():
        container.query_items.assert_called_once()
        assert all(method == "query_items" for method, *_ in container.mock_calls)
    with pytest.raises(RuntimeError, match="CosmosManager not initialized"):
        manager.get_container(COLLECTION_TENANTS)
    assert "mode=dry_run tenants=1 tenants_skipped=0 candidate_documents=3 errors=0" in caplog.text


async def test_archival_job_closes_after_query_failure(offline_cosmos):
    _, _, client, containers, provision = offline_cosmos
    containers[COLLECTION_TENANTS].query_items.side_effect = RuntimeError("synthetic read failure")

    assert await run_archival.main() == 1

    provision.assert_not_awaited()
    client.close.assert_awaited_once_with()


async def test_archival_upload_reports_failed_collection_read(offline_cosmos, monkeypatch, caplog):
    from azure.storage.blob import aio

    from src.multi_tenant.repository import (
        ConversationRepository,
        CustomerProfileRepository,
        MemoryVectorRepository,
    )

    _, _, client, _, provision = offline_cosmos
    blob_client = MagicMock()
    factory = MagicMock(return_value=blob_client)
    monkeypatch.setattr(aio, "BlobServiceClient", SimpleNamespace(from_connection_string=factory))
    run_archival.os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "synthetic"

    read_conversations = AsyncMock(side_effect=RuntimeError("synthetic provider details"))
    read_profiles = AsyncMock(return_value=[])
    read_vectors = AsyncMock(return_value=[])
    monkeypatch.setattr(ConversationRepository, "query", read_conversations)
    monkeypatch.setattr(CustomerProfileRepository, "query", read_profiles)
    monkeypatch.setattr(MemoryVectorRepository, "query", read_vectors)
    caplog.set_level("INFO", logger=run_archival.__name__)

    assert await run_archival.main() == 1

    read_conversations.assert_awaited_once()
    read_profiles.assert_awaited_once()
    read_vectors.assert_awaited_once()
    blob_client.get_container_client.assert_not_called()
    assert "mode=upload tenants=1 documents=0 bytes=0 errors=1" in caplog.text
    assert "synthetic provider details" not in caplog.text
    client.close.assert_awaited_once_with()
    provision.assert_not_awaited()


async def test_archival_job_closes_after_partial_connection_failure(offline_cosmos):
    _, _, client, containers, provision = offline_cosmos
    client.get_database_client.side_effect = RuntimeError("synthetic connection failure")

    assert await run_archival.main() == 1

    provision.assert_not_awaited()
    client.close.assert_awaited_once_with()
    for container in containers.values():
        container.query_items.assert_not_called()


async def test_archival_job_closes_after_cosmos_sdk_enter_failure(offline_cosmos, monkeypatch):
    from azure.identity import aio

    manager, _, client, containers, provision = offline_cosmos
    credential = MagicMock()
    credential.close = AsyncMock()
    monkeypatch.setattr(aio, "DefaultAzureCredential", MagicMock(return_value=credential))
    cosmos_client.os.environ["COSMOS_USE_MANAGED_ID"] = "true"
    client.__aenter__.side_effect = RuntimeError("synthetic account setup failure")

    assert await run_archival.main() == 1

    client.__aenter__.assert_awaited_once_with()
    client.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()
    provision.assert_not_awaited()
    for container in containers.values():
        container.query_items.assert_not_called()
    with pytest.raises(RuntimeError, match="CosmosManager not initialized"):
        manager.get_container(COLLECTION_TENANTS)


async def test_archival_job_with_missing_connection_settings_does_not_construct_client(offline_cosmos, caplog):
    _, constructor, client, _, provision = offline_cosmos
    del cosmos_client.os.environ["COSMOS_DB_ENDPOINT"]

    assert await run_archival.main() == 1

    constructor.assert_not_called()
    client.close.assert_not_awaited()
    provision.assert_not_awaited()
    assert "Archival dry run could not finish (RuntimeError)" in caplog.text


async def test_archival_job_reuses_and_leaves_an_existing_connection_open(offline_cosmos):
    manager, constructor, client, _, provision = offline_cosmos
    await manager._ensure_client()
    await client.__aenter__()

    assert await run_archival.main() == 0

    constructor.assert_called_once()
    client.__aenter__.assert_awaited_once_with()
    provision.assert_not_awaited()
    client.close.assert_not_awaited()


@pytest.mark.parametrize("close_fails", [False, True])
async def test_archival_job_closes_an_owned_managed_credential(offline_cosmos, monkeypatch, close_fails):
    from azure.identity import aio

    _, _, client, _, provision = offline_cosmos
    credential = MagicMock()
    credential.close = AsyncMock()
    monkeypatch.setattr(aio, "DefaultAzureCredential", MagicMock(return_value=credential))
    cosmos_client.os.environ["COSMOS_USE_MANAGED_ID"] = "true"
    if close_fails:
        client.close.side_effect = RuntimeError("synthetic close failure")

    assert await run_archival.main() == int(close_fails)

    client.close.assert_awaited_once_with()
    credential.close.assert_awaited_once_with()
    provision.assert_not_awaited()


@pytest.mark.parametrize("scan_fails", [False, True])
async def test_archival_upload_job_owns_cosmos_connection(offline_cosmos, monkeypatch, caplog, scan_fails):
    from azure.storage import blob
    from azure.storage.blob import aio
    from src.multi_tenant import archival_pipeline

    _, constructor, client, _, provision = offline_cosmos
    blob_open = AsyncMock()
    blob_close = AsyncMock()

    class BlobClient:
        async def __aenter__(self):
            await blob_open()
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            await blob_close()

    blob_client = BlobClient()
    factory = MagicMock(return_value=blob_client)
    sync_factory = MagicMock(side_effect=RuntimeError("synchronous client selected"))
    monkeypatch.setattr(blob, "BlobServiceClient", SimpleNamespace(from_connection_string=sync_factory))
    monkeypatch.setattr(aio, "BlobServiceClient", SimpleNamespace(from_connection_string=factory))
    run_archival.os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "synthetic"
    service = MagicMock()

    sensitive_message = "synthetic upload scan failure"

    async def scan():
        client.__aenter__.assert_awaited_once_with()
        blob_open.assert_awaited_once_with()
        if scan_fails:
            raise RuntimeError(sensitive_message)
        return SimpleNamespace(tenants_scanned=1, documents_archived=2, bytes_written=3, errors=0)

    service.run_archival_scan = AsyncMock(side_effect=scan)
    service_factory = MagicMock(return_value=service)
    monkeypatch.setattr(archival_pipeline, "ArchivalPipelineService", service_factory)

    assert await run_archival.main() == int(scan_fails)
    if scan_fails:
        assert "Archival upload could not finish (RuntimeError)" in caplog.text
        assert sensitive_message not in caplog.text
    assert service_factory.call_args.kwargs["blob_client"] is blob_client
    sync_factory.assert_not_called()
    factory.assert_called_once_with("synthetic")
    blob_close.assert_awaited_once_with()
    service.run_archival_scan.assert_awaited_once_with()
    constructor.assert_called_once()
    client.close.assert_awaited_once_with()
    provision.assert_not_awaited()

async def test_archival_upload_uses_real_tenant_inventory(offline_cosmos, monkeypatch):
    from azure.storage.blob import aio
    from src.multi_tenant.archival_pipeline import ArchivalPipelineService, TenantArchivalResult

    _, _, client, containers, provision = offline_cosmos
    factory = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(aio, "BlobServiceClient", SimpleNamespace(from_connection_string=factory))
    run_archival.os.environ["AZURE_STORAGE_CONNECTION_STRING"] = "synthetic"

    async def archive_tenant(tenant_id, tier, cutoff, now):
        assert (tenant_id, tier) == ("tenant-enterprise", "enterprise")
        return TenantArchivalResult(
            tenant_id=tenant_id,
            tier=tier,
            conversations_archived=0,
            profiles_archived=0,
            vectors_archived=0,
            bytes_written=0,
        )

    archive = AsyncMock(side_effect=archive_tenant)
    monkeypatch.setattr(ArchivalPipelineService, "_archive_for_tenant", archive)

    assert await run_archival.main() == 0
    inventory = containers[COLLECTION_TENANTS].query_items
    inventory.assert_called_once()
    assert inventory.call_args.kwargs["query"] == (
        "SELECT c.tenant_id, c.tier FROM c WHERE c.status IN ('active', 'past_due')"
    )
    assert "partition_key" not in inventory.call_args.kwargs
    archive.assert_awaited_once()
    client.close.assert_awaited_once_with()
    provision.assert_not_awaited()
