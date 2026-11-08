# tests/unit/test_billing_caller_scope.py: billing routes act only for the calling tenant
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
``POST /api/billing/portal``, ``/api/packs/*`` and ``/api/usage/*`` took a tenant id or
Stripe customer id from the request and acted on it with no ownership check, so any
authenticated caller could open another tenant's Stripe Customer Portal, read its pack
balance, start pack checkouts for it, or record usage that sends Stripe meter events
(overage charges) to its account. ``/api/packs/`` was also exempt from authentication.

Now each route resolves the caller's own Stripe customer and refuses any other id with
403, usage is recorded only at the caller's tier, and ``/api/packs/`` needs a credential.

Architecture review S3 (docs/agent-red-architecture-review.md). The tests run through the
composed application (``app_client``), acting as the Starter tenant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.multi_tenant.cosmos_schema import TenantTier
from tests.conftest import (
    PROFESSIONAL_TENANT_ID,
    STARTER_TENANT_ID,
    TEST_API_KEY_STARTER,
    AuthenticatedClient,
    make_tenant_document,
)
from tests.helpers.fake_tenant_repo import FakeDomainIndexRepo, FakeTenantRepo, run_sync

STARTER_CUSTOMER = make_tenant_document()["stripe_customer_id"]
OTHER_TENANT = PROFESSIONAL_TENANT_ID
OTHER_CUSTOMER = make_tenant_document(tenant_id=PROFESSIONAL_TENANT_ID, tier=TenantTier.PROFESSIONAL)[
    "stripe_customer_id"
]


@pytest.fixture
def caller(app_client):
    """The Starter tenant, with provisioning holding both tenants' records."""
    from src.integrations.provisioning import configure_provisioning_repo

    repo = FakeTenantRepo()
    run_sync(repo.upsert(STARTER_TENANT_ID, make_tenant_document()))
    run_sync(
        repo.upsert(OTHER_TENANT, make_tenant_document(tenant_id=OTHER_TENANT, tier=TenantTier.PROFESSIONAL))
    )
    configure_provisioning_repo(repo, team_repo=None, domain_index_repo=FakeDomainIndexRepo())
    try:
        yield AuthenticatedClient(app_client, TEST_API_KEY_STARTER, STARTER_TENANT_ID)
    finally:
        configure_provisioning_repo(None, team_repo=None)


@pytest.fixture
def stripe_calls():
    portal = MagicMock(url="https://billing.stripe.com/p/session/test", id="bps_test")
    checkout = MagicMock(id="cs_test", url="https://checkout.stripe.com/pay/cs_test")
    with (
        patch("stripe.billing_portal.Session.create", return_value=portal) as portal_create,
        patch("stripe.checkout.Session.create", return_value=checkout) as checkout_create,
        patch("src.integrations.stripe_usage.record_conversation") as record,
    ):
        yield {"portal": portal_create, "checkout": checkout_create, "record": record}


@pytest.mark.parametrize(
    "body",
    [{"tenant_id": OTHER_TENANT}, {"stripe_customer_id": OTHER_CUSTOMER}],
    ids=["other-tenant-id", "other-customer-id"],
)
def test_portal_refuses_another_tenant(caller, stripe_calls, body):
    assert caller.post("/api/billing/portal", json=body).status_code == 403
    stripe_calls["portal"].assert_not_called()


def test_portal_without_identifiers_opens_the_callers_own(caller, stripe_calls):
    """An empty body names no tenant, so the caller gets its own portal instead of 400."""
    response = caller.post("/api/billing/portal", json={})
    assert response.status_code == 200
    assert stripe_calls["portal"].call_args.kwargs["customer"] == STARTER_CUSTOMER


def test_portal_with_no_body_opens_the_callers_own(caller, stripe_calls):
    """The standalone admin's "Manage billing" button posts with no body at all (no 422)."""
    response = caller.post("/api/billing/portal")
    assert response.status_code == 200
    assert stripe_calls["portal"].call_args.kwargs["customer"] == STARTER_CUSTOMER


@pytest.mark.parametrize(
    "extra",
    [{"tenant_id": OTHER_TENANT}, {"stripe_customer_id": OTHER_CUSTOMER}],
    ids=["other-tenant-id", "other-customer-id"],
)
def test_pack_purchase_refuses_another_tenant(caller, stripe_calls, extra):
    response = caller.post("/api/packs/purchase", json={"pack_id": "pack_1k", **extra})
    assert response.status_code == 403
    stripe_calls["checkout"].assert_not_called()


@pytest.mark.parametrize("named", [OTHER_CUSTOMER, OTHER_TENANT], ids=["other-customer-id", "other-tenant-id"])
def test_pack_balance_refuses_another_tenant(caller, named):
    assert caller.get(f"/api/packs/balance/{named}").status_code == 403


def test_pack_balance_by_the_callers_tenant_id(caller):
    """The admin billing page passes the caller's tenant id; it gets the caller's own balance."""
    from src.integrations.stripe_packs import credit_pack_balance

    credit_pack_balance(STARTER_CUSTOMER, "pack_1k", 1000)
    response = caller.get(f"/api/packs/balance/{STARTER_TENANT_ID}")
    assert response.status_code == 200
    assert response.json()["stripe_customer_id"] == STARTER_CUSTOMER
    assert response.json()["total_remaining"] == 1000


def test_pack_routes_need_a_credential(app_client):
    assert app_client.get(f"/api/packs/balance/{STARTER_CUSTOMER}").status_code == 401
    assert app_client.post("/api/packs/purchase", json={"pack_id": "pack_1k"}).status_code == 401


def test_usage_record_refuses_another_tenant(caller, stripe_calls):
    response = caller.post("/api/usage/record", json={"stripe_customer_id": OTHER_CUSTOMER, "tier": "professional"})
    assert response.status_code == 403
    stripe_calls["record"].assert_not_called()


def test_usage_record_refuses_another_tier(caller, stripe_calls):
    """A higher tier would raise the included allowance and hold back overage reports."""
    response = caller.post("/api/usage/record", json={"stripe_customer_id": STARTER_CUSTOMER, "tier": "enterprise"})
    assert response.status_code == 403
    stripe_calls["record"].assert_not_called()


def test_usage_summary_refuses_another_tenant(caller):
    assert caller.get(f"/api/usage/{OTHER_CUSTOMER}", params={"tier": "professional"}).status_code == 403


def test_usage_summary_refuses_another_tier(caller):
    """The tier sets the included allowance shown; it must be the caller's own."""
    assert caller.get(f"/api/usage/{STARTER_CUSTOMER}", params={"tier": "enterprise"}).status_code == 403


def test_usage_summary_at_the_callers_tier(caller):
    response = caller.get(f"/api/usage/{STARTER_CUSTOMER}", params={"tier": "starter"})
    assert response.status_code == 200
    assert response.json()["included_allowance"] == 1000
