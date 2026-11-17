# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""A tenant's display_name never holds the owner's email or phone (decision 93).

SPEC-1881 built it from the contact address, so it held the owner's email in
plain text (display_name is not encrypted) and the escalation email used it as
the store name ("<owner email>-030"). It is now the merchant name with the
SPEC-1881 ordinal, or "Tenant <first 8 of the tenant id>" when there is none.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.chat.pipeline.critic_escalation import _lookup_store_name
from src.integrations.provisioning import BillingChannel, configure_provisioning_repo, provision_tenant
from tests.helpers.fake_tenant_repo import FakeDomainIndexRepo, FakeTenantRepo

_EMAIL = "owner@example.com"


@pytest.fixture
def repo():
    tenants = FakeTenantRepo()
    configure_provisioning_repo(tenants, team_repo=None, domain_index_repo=FakeDomainIndexRepo())
    yield tenants
    configure_provisioning_repo(None, team_repo=None, domain_index_repo=None)


@pytest.mark.unit
async def test_a_merchant_name_becomes_the_display_name(repo):
    record = await provision_tenant(
        billing_channel=BillingChannel.MANUAL,
        tier="starter",
        customer_email=_EMAIL,
        merchant_name="Acme Shop",
    )
    assert repo.store[record.tenant_id]["display_name"] == "Acme Shop-001"


@pytest.mark.unit
async def test_a_merchant_name_that_is_an_email_is_not_used(repo):
    record = await provision_tenant(
        billing_channel=BillingChannel.MANUAL,
        tier="starter",
        customer_email=_EMAIL,
        merchant_name=f" {_EMAIL} ",
    )
    assert repo.store[record.tenant_id]["display_name"] == f"Tenant {record.tenant_id[:8]}"


@pytest.mark.unit
async def test_a_checkout_without_a_merchant_name_gets_a_neutral_name(repo):
    record = await provision_tenant(
        billing_channel=BillingChannel.STRIPE,
        tier="starter",
        stripe_customer_id="cus_neutral",
        customer_email=_EMAIL,
    )
    display_name = repo.store[record.tenant_id]["display_name"]
    assert display_name == f"Tenant {record.tenant_id[:8]}"
    assert "@" not in display_name


@pytest.mark.unit
async def test_the_console_path_passes_the_merchant_name():
    """spa_provision_tenant hands its merchant_name to provision_tenant."""
    from src.integrations import provisioning

    captured = {}

    async def fake_provision(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after the call")

    with patch.object(provisioning, "provision_tenant", side_effect=fake_provision), pytest.raises(RuntimeError):
        await provisioning.spa_provision_tenant(
            merchant_name="Console Shop",
            merchant_url=None,
            superadmin_email=_EMAIL,
            tier="starter",
        )
    assert captured.get("merchant_name") == "Console Shop"


@pytest.mark.unit
async def test_an_older_email_display_name_is_not_used_as_the_store_name():
    """The 35 staging rows written before this change keep "<email>-NNN" until cleanup."""
    with patch(
        "src.multi_tenant.repository.TenantRepository.read",
        new_callable=AsyncMock,
        return_value={"display_name": f"{_EMAIL}-030"},
    ):
        assert await _lookup_store_name("tenant-1") == "Support"


@pytest.mark.unit
async def test_a_merchant_display_name_is_still_the_store_name():
    with patch(
        "src.multi_tenant.repository.TenantRepository.read",
        new_callable=AsyncMock,
        return_value={"display_name": "Acme Shop-001"},
    ):
        assert await _lookup_store_name("tenant-1") == "Acme Shop-001"
