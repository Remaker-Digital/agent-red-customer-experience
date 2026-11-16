"""Trial status uses the persisted count of billable conversations."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.multi_tenant.repositories import base
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.trial_management import TrialManagementService, TrialStatusCode


async def test_trial_status_uses_the_tenant_partition_billable_count(monkeypatch):
    tenant_id = "trial-count-fixture"
    tenant_repo = AsyncMock()
    tenant_repo.read.return_value = {
        "tier": "trial",
        "status": "active",
        "trial_expires_at": (datetime.now(UTC) + timedelta(days=10)).isoformat(),
        "trial_conversation_limit": 5,
    }

    async def count_rows():
        yield 5

    container = Mock()
    container.query_items.return_value = count_rows()
    manager = SimpleNamespace(get_container=Mock(return_value=container))
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: manager)
    service = TrialManagementService(
        tenant_repo=tenant_repo,
        usage_repo=AsyncMock(),
        conversation_repo=ConversationRepository(),
    )

    status = await service.get_trial_status(tenant_id)

    assert status is not None
    assert status.conversations_used == 5
    assert status.conversations_remaining == 0
    assert status.status == TrialStatusCode.CAP_REACHED
    container.query_items.assert_called_once_with(
        query="SELECT VALUE COUNT(1) FROM c WHERE c.is_billable = true",
        partition_key=tenant_id,
    )
