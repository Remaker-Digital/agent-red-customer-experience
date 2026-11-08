# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Quality closeout persists through ConversationRepository.read and patch."""

from __future__ import annotations

import pytest

from src.chat.quality_closeout import evaluate_quality_and_alert
from src.multi_tenant.repositories.conversation import ConversationRepository


class _Container:
    def __init__(self) -> None:
        self.operations: list[dict[str, object]] = []

    async def read_item(self, item: str, partition_key: str) -> dict[str, object]:
        return {
            "id": item,
            "tenant_id": partition_key,
            "messages": [
                {"role": "assistant", "metadata": {"quality_score": {"overall": 1.0}}},
                {"role": "assistant", "metadata": {"quality_score": {"overall": 0.5}}},
            ],
        }

    async def patch_item(
        self,
        item: str,
        partition_key: str,
        patch_operations: list[dict[str, object]],
        **kwargs: object,
    ) -> dict[str, str]:
        self.operations.extend(patch_operations)
        return {"id": item}


class _Manager:
    def __init__(self, container: _Container) -> None:
        self._container = container

    def get_container(self, name: str) -> _Container:
        return self._container


@pytest.mark.asyncio
async def test_closeout_persists_the_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    container = _Container()
    monkeypatch.setattr(
        "src.multi_tenant.repositories.base.get_cosmos_manager",
        lambda: _Manager(container),
    )
    await evaluate_quality_and_alert("tenant-q", "conv-q", ConversationRepository())
    assert container.operations == [
        {
            "op": "set",
            "path": "/quality_aggregate",
            "value": {"mean": 0.75, "min": 0.5, "max": 1.0, "count": 2},
        }
    ]
