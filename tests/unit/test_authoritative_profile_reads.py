"""Authoritative canonical profile lookups report read failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.multi_tenant.repositories import base
from src.multi_tenant.repositories.base import DocumentNotFoundError
from src.multi_tenant.repositories.customer import CustomerProfileRepository


async def test_legacy_canonical_profile_lookup_preserves_decryption_error(monkeypatch):
    tenant_id = "fixture-tenant"
    canonical_id = "cid_fixture"
    stored_profile = {"id": "fixture-legacy-profile", "tenant_id": tenant_id}

    async def stored_rows():
        yield stored_profile

    container = Mock()
    container.query_items.return_value = stored_rows()
    monkeypatch.setattr(base, "get_cosmos_manager", lambda: SimpleNamespace(get_container=lambda _: container))
    repo = CustomerProfileRepository()
    monkeypatch.setattr(
        repo,
        "read",
        AsyncMock(side_effect=DocumentNotFoundError("customer_profiles", "fixture-canonical-id", tenant_id)),
    )
    failure = ValueError("fixture decryption failure")
    monkeypatch.setattr(repo, "_post_read", AsyncMock(side_effect=failure))

    with pytest.raises(ValueError) as exc:
        await repo.get_by_customer_id(tenant_id, canonical_id)

    assert exc.value is failure
