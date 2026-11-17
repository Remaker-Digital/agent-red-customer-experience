# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""A Shopify redact webhook is acknowledged only when the request was carried out.

Shopify retries a webhook that does not get a 2xx answer. Before this change
shop/redact and customers/redact answered 200 when the tenant lookup failed or
the deletion raised, so the data stayed and Shopify did not ask again. A clean
"not found" still answers 200: the shop is not ours, or it was already deleted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.integrations import shopify_gdpr_webhooks as gdpr
from src.multi_tenant.repositories.domain_index import DomainIndexRepository

_SECRET = "test-gdpr-secret"
_SHOP = "redact-shop.myshopify.com"
_TENANT = "tenant-redact-1"
_SHOP_REDACT = ("/api/shopify/gdpr/shop-redact", {"shop_id": 1, "shop_domain": _SHOP})
_CUSTOMERS_REDACT = (
    "/api/shopify/gdpr/customers-redact",
    {"shop_id": 1, "shop_domain": _SHOP, "customer": {"id": 7}, "orders_to_redact": []},
)
_REDACTS = [_SHOP_REDACT, _CUSTOMERS_REDACT]


class _Index:
    def __init__(self, result: str | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.strict_calls: list[bool] = []

    async def lookup(self, domain: str, *, strict: bool = False) -> str | None:
        self.strict_calls.append(strict)
        if self.error is not None:
            raise self.error
        return self.result


class _Tenants:
    def __init__(self, doc: dict | None = None, error: Exception | None = None) -> None:
        self.doc = doc
        self.error = error

    async def find_by_shopify_domain(self, domain: str) -> dict | None:
        if self.error is not None:
            raise self.error
        return self.doc


def _deletion(error: Exception | None = None) -> SimpleNamespace:
    result = SimpleNamespace(deletion_id="del-1", stores_deleted=["tenants"])
    return SimpleNamespace(
        delete_tenant=AsyncMock(side_effect=error, return_value=result),
        delete_customer=AsyncMock(side_effect=error, return_value=result),
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(gdpr.router)
    with patch.object(gdpr, "_SHOPIFY_API_SECRET", _SECRET):
        yield TestClient(app)
    gdpr.configure_shopify_gdpr_services()


def _post(client: TestClient, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    signature = base64.b64encode(hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    return client.post(
        path,
        content=body,
        headers={"Content-Type": "application/json", "X-Shopify-Hmac-Sha256": signature},
    )


@pytest.mark.unit
@pytest.mark.parametrize("path,payload", _REDACTS)
def test_a_failed_lookup_is_not_acknowledged(client, path, payload):
    """Index and fallback both fail: the tenant may exist, so Shopify must retry."""
    deletion = _deletion()
    gdpr.configure_shopify_gdpr_services(
        deletion_service=deletion,
        tenant_repo=_Tenants(error=RuntimeError("cosmos down")),
        domain_index_repo=_Index(error=RuntimeError("cosmos down")),
    )
    response = _post(client, path, payload)
    assert response.status_code == 503
    deletion.delete_tenant.assert_not_awaited()
    deletion.delete_customer.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("path,payload", _REDACTS)
def test_a_failed_index_and_a_clean_fallback_miss_is_not_acknowledged(client, path, payload):
    """A row indexed but without a lookup hash would be missed, so a failed index is not a clean miss."""
    gdpr.configure_shopify_gdpr_services(
        deletion_service=_deletion(),
        tenant_repo=_Tenants(doc=None),
        domain_index_repo=_Index(error=RuntimeError("cosmos down")),
    )
    assert _post(client, path, payload).status_code == 503


@pytest.mark.unit
@pytest.mark.parametrize("path,payload", _REDACTS)
def test_a_failed_index_with_a_fallback_hit_is_carried_out(client, path, payload):
    deletion = _deletion()
    gdpr.configure_shopify_gdpr_services(
        deletion_service=deletion,
        tenant_repo=_Tenants(doc={"id": _TENANT, "tenant_id": _TENANT}),
        domain_index_repo=_Index(error=RuntimeError("cosmos down")),
    )
    response = _post(client, path, payload)
    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}
    if path == _SHOP_REDACT[0]:
        deletion.delete_tenant.assert_awaited_once_with(_TENANT, force=True)
    else:
        deletion.delete_customer.assert_awaited_once_with(_TENANT, "7")


@pytest.mark.unit
@pytest.mark.parametrize("path,payload", _REDACTS)
def test_a_clean_miss_is_acknowledged(client, path, payload):
    """Not ours, or already deleted: Shopify's own checks expect 200 here."""
    index = _Index(result=None)
    gdpr.configure_shopify_gdpr_services(
        deletion_service=_deletion(),
        tenant_repo=_Tenants(doc=None),
        domain_index_repo=index,
    )
    response = _post(client, path, payload)
    assert response.status_code == 200
    assert index.strict_calls == [True]


@pytest.mark.unit
@pytest.mark.parametrize("path,payload", _REDACTS)
def test_a_failed_deletion_is_not_acknowledged(client, path, payload):
    gdpr.configure_shopify_gdpr_services(
        deletion_service=_deletion(error=RuntimeError("delete failed")),
        tenant_repo=_Tenants(doc=None),
        domain_index_repo=_Index(result=_TENANT),
    )
    assert _post(client, path, payload).status_code == 503


@pytest.mark.unit
async def test_strict_index_lookup_raises_only_on_a_real_failure():
    """Not found is None in both modes. Any other error is None by default and raised when strict."""
    repo = DomainIndexRepository()
    missing = SimpleNamespace(
        read_item=AsyncMock(side_effect=CosmosResourceNotFoundError(status_code=404, message="gone"))
    )
    broken = SimpleNamespace(read_item=AsyncMock(side_effect=RuntimeError("cosmos down")))
    manager = SimpleNamespace(get_container=lambda name: container)

    with patch("src.multi_tenant.repositories.domain_index.get_cosmos_manager", return_value=manager):
        container = missing
        assert await repo.lookup(_SHOP) is None
        assert await repo.lookup(_SHOP, strict=True) is None
        container = broken
        assert await repo.lookup(_SHOP) is None
        with pytest.raises(RuntimeError):
            await repo.lookup(_SHOP, strict=True)
