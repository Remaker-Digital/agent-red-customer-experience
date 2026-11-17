# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""The scheduled retention job reports the counts returned by its service."""

from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.jobs import run_retention
from src.multi_tenant import cosmos_client, data_retention, entitlement_service, repository
from src.multi_tenant.data_retention import RetentionScanResult

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("errors", "expected_exit_code"), [(0, 0), (1, 1)])
async def test_retention_job_reports_each_result_count_and_returns_exit_code(
    monkeypatch, caplog, errors, expected_exit_code
):
    manager = Mock()
    manager.connected.return_value = MagicMock()
    monkeypatch.setattr(cosmos_client, "get_cosmos_manager", lambda: manager)
    service = Mock()
    service.enforce_retention = AsyncMock(
        return_value=RetentionScanResult(
            tenants_scanned=5,
            tenants_with_deletions=2,
            conversations_deleted=3,
            profiles_purged=4,
            vectors_deleted=6,
            errors=errors,
        )
    )
    monkeypatch.setattr(data_retention, "DataRetentionService", Mock(return_value=service))
    for repo in (
        "AuditLogRepository",
        "ConversationRepository",
        "CustomerProfileRepository",
        "MemoryVectorRepository",
        "TenantRepository",
    ):
        monkeypatch.setattr(repository, repo, Mock())
    caplog.set_level("INFO", logger=run_retention.__name__)

    exit_code = await run_retention.main()

    service.enforce_retention.assert_awaited_once_with()
    assert exit_code == expected_exit_code
    assert (
        "Data retention job complete: tenants=5 conversations_deleted=3 "
        f"profiles_purged=4 vectors_deleted=6 errors={errors}"
    ) in caplog.messages

@pytest.mark.parametrize(
    ("collection", "operation", "successful_writes"),
    [
        ("conversation", "query", 2),
        ("conversation", "delete", 3),
        ("profile", "query", 2),
        ("profile", "patch", 3),
    ],
)
async def test_retention_job_reports_failed_tenant_and_preserves_successful_counts(
    monkeypatch, caplog, collection, operation, successful_writes
):
    manager = Mock()
    manager.connected.return_value = MagicMock()
    monkeypatch.setattr(cosmos_client, "get_cosmos_manager", lambda: manager)
    entitlements = Mock(get_tier_config=AsyncMock(return_value={"history_depth_days": 90}))
    monkeypatch.setattr(entitlement_service, "get_entitlement_service", lambda: entitlements)

    tenants = Mock(list_archival_tenants=AsyncMock(return_value=[
        {"tenant_id": "tenant-first", "tier": "starter"},
        {"tenant_id": "tenant-second", "tier": "starter"},
    ]))
    expired = [{"id": "expired-one"}, {"id": "expired-two"}]
    conversations = Mock(query=AsyncMock(return_value=expired), delete=AsyncMock())
    expired_profiles = [dict(row, _etag="synthetic-etag", has_history=True) for row in expired]
    profiles = Mock(query=AsyncMock(return_value=expired_profiles), patch=AsyncMock())
    vectors = Mock(query=AsyncMock(return_value=[]))
    audit = Mock(log_event=AsyncMock())

    async def fail_one_operation(tenant_id, **kwargs):
        if tenant_id == "tenant-first" and (operation == "query" or kwargs["document_id"] == "expired-one"):
            raise RuntimeError("synthetic retention repository failure")
        if operation == "query":
            return expired_profiles if collection == "profile" else expired
        return None

    failed_repository = conversations if collection == "conversation" else profiles
    getattr(failed_repository, operation).side_effect = fail_one_operation

    monkeypatch.setattr(repository, "TenantRepository", Mock(return_value=tenants))
    monkeypatch.setattr(repository, "ConversationRepository", Mock(return_value=conversations))
    monkeypatch.setattr(repository, "CustomerProfileRepository", Mock(return_value=profiles))
    monkeypatch.setattr(repository, "MemoryVectorRepository", Mock(return_value=vectors))
    monkeypatch.setattr(repository, "AuditLogRepository", Mock(return_value=audit))
    caplog.set_level("INFO", logger=run_retention.__name__)

    assert await run_retention.main() == 1

    conversations_deleted = successful_writes if collection == "conversation" else 4
    profiles_purged = successful_writes if collection == "profile" else 4
    assert (
        f"Data retention job complete: tenants=2 conversations_deleted={conversations_deleted} "
        f"profiles_purged={profiles_purged} vectors_deleted=0 errors=1"
    ) in caplog.messages


@pytest.mark.parametrize("has_history", [True, False])
async def test_legacy_profile_retention_can_run_twice(has_history):
    tenant = "tenant-synthetic"
    tenants = Mock(list_archival_tenants=AsyncMock(return_value=[
        {"tenant_id": tenant, "tier": "starter"},
    ]))
    profile = {"interaction_count": 2}
    if has_history:
        profile["conversation_history"] = ["synthetic conversation"]

    async def query_profiles(**kwargs):
        return [{
            "id": "legacy-profile",
            "_etag": "synthetic-etag",
            "has_history": "conversation_history" in profile,
            "interaction_count": profile["interaction_count"],
        }]

    async def patch_profile(tenant_id, document_id, operations, *, etag=None):
        for operation in operations:
            field = operation["path"].removeprefix("/")
            if operation["op"] == "remove":
                if field not in profile:
                    raise RuntimeError("Cosmos remove target is absent")
                del profile[field]
            else:
                profile[field] = operation["value"]

    profiles = Mock(query=AsyncMock(side_effect=query_profiles), patch=AsyncMock(side_effect=patch_profile))
    audit = Mock(log_event=AsyncMock())
    service = data_retention.DataRetentionService(tenant_repo=tenants, profile_repo=profiles, audit_repo=audit)

    first = await service.enforce_retention()
    second = await service.enforce_retention()

    assert (first.profiles_purged, first.errors) == (1, 0)
    assert (second.profiles_purged, second.errors) == (0, 0)
    assert profile == {"interaction_count": 0}
    profiles.patch.assert_awaited_once()
    assert profiles.patch.await_args.kwargs["etag"] == "synthetic-etag"
    audit.log_event.assert_awaited_once()


async def test_legacy_profile_without_etag_is_not_patched():
    profiles = Mock(
        query=AsyncMock(return_value=[{"id": "legacy-profile", "has_history": True}]),
        patch=AsyncMock(),
    )
    service = data_retention.DataRetentionService(tenant_repo=Mock(), profile_repo=profiles)

    result = await service._purge_stale_profile_data("tenant-synthetic", "2026-01-01T00:00:00+00:00")

    assert result == (0, True)
    profiles.patch.assert_not_awaited()
