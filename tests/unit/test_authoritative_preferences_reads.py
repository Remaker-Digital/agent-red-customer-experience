"""Authoritative preference reads distinguish absent data from a read error."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.multi_tenant.repositories import base
from src.multi_tenant.repositories.preferences import PreferencesRepository


@pytest.mark.parametrize("method", ["get_active", "list_previous"])
async def test_authoritative_preference_read_propagates_decryption_failure(monkeypatch, method):
    tenant_id = "fixture-tenant"
    stored_row = {"id": "fixture-preferences", "tenant_id": tenant_id}

    async def stored_rows():
        yield stored_row

    container = Mock()
    container.query_items.return_value = stored_rows()
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: SimpleNamespace(get_container=lambda _: container))
    repo = PreferencesRepository()
    failure = ValueError("fixture decryption failure")
    monkeypatch.setattr(repo, "_post_read", AsyncMock(side_effect=failure))

    with pytest.raises(ValueError) as exc:
        await getattr(repo, method)(tenant_id)

    assert exc.value is failure
