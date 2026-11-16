"""Check that warm-tier archival without Blob Storage makes no writes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.jobs import run_archival
from src.multi_tenant.archival_pipeline import ArchivalPipelineService, ArchivalScanResult
from src.multi_tenant.cosmos_schema import (
    COLLECTION_CONVERSATIONS,
    COLLECTION_CUSTOMER_PROFILES,
    COLLECTION_MEMORY_VECTORS,
    COLLECTION_TENANTS,
)
from src.multi_tenant.repositories import base
from src.multi_tenant.repository import (
    ConversationRepository,
    CustomerProfileRepository,
    MemoryVectorRepository,
    TenantRepository,
)


async def test_archival_dry_run_uses_real_repository_queries_without_writes(monkeypatch):
    tenant_id = "tenant-enterprise"
    rows = {
        COLLECTION_TENANTS: [{"tenant_id": tenant_id, "tier": "enterprise"}],
        COLLECTION_CONVERSATIONS: [1],
        COLLECTION_CUSTOMER_PROFILES: [2],
        COLLECTION_MEMORY_VECTORS: [0],
    }
    containers = {name: MagicMock() for name in rows}

    async def query_rows(documents):
        for document in documents:
            yield document

    for name, container in containers.items():
        container.query_items.return_value = query_rows(rows[name])
    manager = SimpleNamespace(get_container=containers.__getitem__)
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: manager)
    audit = AsyncMock()
    service = ArchivalPipelineService(
        tenant_repo=TenantRepository(),
        conversation_repo=ConversationRepository(),
        profile_repo=CustomerProfileRepository(),
        vector_repo=MemoryVectorRepository(),
        audit_repo=audit,
    )
    upload = AsyncMock()
    monkeypatch.setattr(service, "_upload_to_blob", upload)
    serialize = MagicMock()
    monkeypatch.setattr(service, "_serialize_to_parquet", serialize)
    cutoff = "2026-01-01T00:00:00+00:00"
    monkeypatch.setattr(service, "_calculate_cutoff", lambda tier, now: cutoff)

    result = await service.run_archival_scan()

    assert result.tenants_scanned == 1
    assert result.candidate_documents == 3
    assert result.documents_archived == 0
    assert result.bytes_written == 0
    assert result.errors == 0
    assert containers[COLLECTION_TENANTS].mock_calls == [
        call.query_items(
            query="SELECT c.tenant_id, c.tier FROM c WHERE c.status IN ('active', 'past_due')",
            max_item_count=100,
        ),
    ]
    for name, date_field in (
        (COLLECTION_CONVERSATIONS, "ended_at"),
        (COLLECTION_CUSTOMER_PROFILES, "updated_at"),
        (COLLECTION_MEMORY_VECTORS, "created_at"),
    ):
        assert containers[name].mock_calls == [
            call.query_items(
                query=(f"SELECT VALUE COUNT(1) FROM c WHERE c.tenant_id = @tid AND c.{date_field} < @cutoff"),
                partition_key=tenant_id,
                parameters=[{"name": "@tid", "value": tenant_id}, {"name": "@cutoff", "value": cutoff}],
            ),
        ]
    upload.assert_not_awaited()
    serialize.assert_not_called()
    audit.log_event.assert_not_awaited()


async def test_warm_tier_dry_run_preserves_originals():
    conversations = AsyncMock()
    conversations.query_count.return_value = 1
    audit = AsyncMock()
    service = ArchivalPipelineService(
        tenant_repo=AsyncMock(), conversation_repo=conversations, audit_repo=audit,
    )

    result = await service.archive_warm_tier(
        "tenant-starter", "starter", "2026-01-01T00:00:00+00:00", datetime(2026, 2, 1, tzinfo=UTC),
    )

    conversations.delete.assert_not_awaited()
    conversations.query.assert_not_awaited()
    audit.log_event.assert_not_awaited()
    assert result.candidate_documents == 1
    assert result.conversations_archived == 0
    assert result.bytes_written == 0


async def test_archival_dry_run_reports_partial_counts_and_skipped_tenants(monkeypatch):
    tenants = AsyncMock()
    tenants.list_archival_tenants.return_value = [
        {"tenant_id": "tenant-a", "tier": "enterprise"},
        {"tenant_id": "tenant-b", "tier": "enterprise"},
        {"tenant_id": "tenant-c", "tier": "starter"},
    ]
    conversations = AsyncMock()
    conversations.query_count.side_effect = [RuntimeError("read failed"), 0]
    vectors = AsyncMock()
    vectors.query_count.side_effect = [2, 1]
    service = ArchivalPipelineService(tenant_repo=tenants, conversation_repo=conversations, vector_repo=vectors)
    monkeypatch.setattr(service, "_calculate_cutoff", lambda tier, now: "2026-01-01" if tier == "enterprise" else None)

    result = await service.run_archival_scan()

    assert result.candidate_documents == 3
    assert result.errors == 1
    assert result.tenants_scanned == 3
    assert result.tenants_skipped == 1
    assert [detail.failed_collections for detail in result.tenant_details] == [("conversations",), ()]
    assert conversations.query_count.await_count == 2
    assert vectors.query_count.await_count == 2


async def test_archival_dry_run_rejects_invalid_tenant_metadata(monkeypatch):
    tenants = AsyncMock()
    tenants.list_archival_tenants.return_value = [
        {"tenant_id": "", "tier": "enterprise"},
        {"tenant_id": "missing-tier"},
        {"tenant_id": "unknown-tier", "tier": "unknown"},
        {"tenant_id": "valid-tenant", "tier": "enterprise"},
    ]
    conversations = AsyncMock()
    conversations.query_count.return_value = 0
    service = ArchivalPipelineService(tenant_repo=tenants, conversation_repo=conversations)
    monkeypatch.setattr(service, "_calculate_cutoff", lambda tier, now: "2026-01-01")

    result = await service.run_archival_scan()

    assert result.tenants_scanned == 4
    assert result.candidate_documents == 0
    assert result.errors == 3
    assert conversations.query_count.await_count == 1
    assert conversations.query_count.await_args.kwargs["tenant_id"] == "valid-tenant"


@pytest.mark.parametrize("rows", [[], [None], [True], [-1], [0, 1]])
async def test_archival_dry_run_rejects_missing_or_invalid_count(monkeypatch, rows):
    async def query_rows():
        for row in rows:
            yield row

    container = MagicMock()
    container.query_items.return_value = query_rows()
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: SimpleNamespace(get_container=lambda name: container))
    service = ArchivalPipelineService(tenant_repo=AsyncMock(), conversation_repo=ConversationRepository())

    result = await service.archive_warm_tier("tenant-a", "enterprise", "2026-01-01", datetime(2026, 2, 1, tzinfo=UTC))

    assert result.candidate_documents == 0
    assert result.failed_collections == ("conversations",)


async def test_archival_dry_run_distinguishes_an_empty_inventory_from_a_failed_tenant_read():
    tenants = AsyncMock()
    tenants.list_archival_tenants.side_effect = [[], RuntimeError("read failed")]
    service = ArchivalPipelineService(tenant_repo=tenants)

    empty = await service.run_archival_scan()
    failed = await service.run_archival_scan()

    assert empty.candidate_documents == failed.candidate_documents == 0
    assert empty.errors == 0
    assert failed.errors == 1


@pytest.mark.parametrize(("rows", "expected"), [([], 0), ([4, 9], 4)])
async def test_existing_repository_count_keeps_its_default_behavior(monkeypatch, rows, expected):
    async def query_rows():
        for row in rows:
            yield row

    container = MagicMock()
    container.query_items.return_value = query_rows()
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: SimpleNamespace(get_container=lambda name: container))

    assert await ConversationRepository().query_count("tenant-a", "SELECT VALUE COUNT(1) FROM c") == expected


async def test_warm_tier_records_archive_event_after_upload():
    from azure.storage.blob import ContentSettings

    conversations = AsyncMock()
    conversations.query.return_value = [{"id": "conv-1"}]
    audit = AsyncMock()
    blob_client = MagicMock()
    blob = blob_client.get_container_client.return_value.get_blob_client.return_value
    blob.upload_blob = AsyncMock()
    audit.log_event.side_effect = lambda **kwargs: blob.upload_blob.assert_awaited_once()
    service = ArchivalPipelineService(
        tenant_repo=AsyncMock(), conversation_repo=conversations, audit_repo=audit, blob_client=blob_client,
    )

    result = await service.archive_warm_tier(
        "tenant-enterprise", "enterprise", "2026-01-01T00:00:00+00:00", datetime(2026, 2, 1, tzinfo=UTC),
    )

    audit.log_event.assert_awaited_once()
    settings = blob.upload_blob.await_args.kwargs["content_settings"]
    assert isinstance(settings, ContentSettings)
    assert settings.content_type == "application/octet-stream"
    assert result.conversations_archived == 1


@pytest.mark.parametrize("failure", ["serialization", "upload"])
async def test_archival_scan_reports_failed_batches_without_deleting(monkeypatch, failure):
    tenants = AsyncMock()
    tenants.list_archival_tenants.return_value = [{"tenant_id": "tenant-a", "tier": "starter"}]
    conversations = AsyncMock()
    conversations.query.return_value = [{"id": "conversation-a"}]
    audit = AsyncMock()
    service = ArchivalPipelineService(
        tenant_repo=tenants, conversation_repo=conversations, audit_repo=audit, blob_client=MagicMock(),
    )
    monkeypatch.setattr(service, "_calculate_cutoff", lambda tier, now: "2026-01-01")
    serialize = MagicMock(return_value=None if failure == "serialization" else b"synthetic")
    upload = AsyncMock(return_value=False)
    monkeypatch.setattr(service, "_serialize_to_parquet", serialize)
    monkeypatch.setattr(service, "_upload_to_blob", upload)

    result = await service.run_archival_scan()

    assert result.errors == 1
    assert result.documents_archived == result.bytes_written == 0
    assert result.tenant_details[0].failed_collections == ("conversations",)
    assert upload.await_count == int(failure == "upload")
    conversations.delete.assert_not_awaited()
    audit.log_event.assert_not_awaited()


@pytest.mark.parametrize("errors", [0, 1])
async def test_archival_job_labels_dry_run_estimates(monkeypatch, caplog, errors):
    from src.multi_tenant import cosmos_client

    monkeypatch.setattr(run_archival, "os", SimpleNamespace(environ={}))
    monkeypatch.setattr(cosmos_client, "get_cosmos_manager", MagicMock())
    result = ArchivalScanResult(tenants_scanned=1, documents_archived=0, bytes_written=0, errors=errors,
                                candidate_documents=2)
    scan = AsyncMock(return_value=result)
    monkeypatch.setattr(ArchivalPipelineService, "run_archival_scan", scan)
    caplog.set_level("INFO", logger=run_archival.__name__)

    assert await run_archival.main() == errors

    scan.assert_awaited_once()
    assert f"mode=dry_run tenants=1 tenants_skipped=0 candidate_documents=2 errors={errors}" in caplog.text
    assert " bytes=" not in caplog.text


async def test_archival_job_keeps_configured_blob_reporting(monkeypatch, caplog):
    import sys

    from src.multi_tenant import cosmos_client

    blob_client = MagicMock()
    storage_module = SimpleNamespace(BlobServiceClient=SimpleNamespace(from_connection_string=lambda _: blob_client))
    monkeypatch.setitem(sys.modules, "azure.storage.blob.aio", storage_module)
    monkeypatch.setattr(run_archival, "os", SimpleNamespace(environ={"AZURE_STORAGE_CONNECTION_STRING": "synthetic"}))
    get_cosmos_manager = MagicMock()
    monkeypatch.setattr(cosmos_client, "get_cosmos_manager", get_cosmos_manager)
    result = ArchivalScanResult(tenants_scanned=1, documents_archived=2, bytes_written=42, errors=0)
    monkeypatch.setattr(ArchivalPipelineService, "run_archival_scan", AsyncMock(return_value=result))
    caplog.set_level("INFO", logger=run_archival.__name__)

    assert await run_archival.main() == 0
    assert "mode=upload tenants=1 documents=2 bytes=42 errors=0" in caplog.text
    get_cosmos_manager.return_value.connected.assert_called_once_with()
