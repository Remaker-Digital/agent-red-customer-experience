# © 2026 Michael Palmeter. All rights reserved.
"""Trial status must be scoped to the authenticated tenant."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from src.multi_tenant.auth import TenantContext
from src.multi_tenant.cosmos_schema import TeamMemberRole
from src.multi_tenant.middleware import get_tenant_context
from src.multi_tenant.trial_management import (
    TrialManagementService,
    TrialStatus,
    TrialStatusCode,
    get_trial_status_endpoint,
    trial_router,
)

pytestmark = pytest.mark.unit


def test_the_mounted_status_handler_requires_authenticated_tenant_context():
    route = next(
        route
        for route in trial_router.routes
        if isinstance(route, APIRoute) and route.path == "/api/trial/status/{tenant_id}"
    )

    assert route.endpoint is get_trial_status_endpoint
    assert any(dependency.call is get_tenant_context for dependency in route.dependant.dependencies)
    assert 403 in route.responses


async def test_the_status_handler_denies_a_request_outside_the_authenticated_tenant():
    ctx = TenantContext(tenant_id="tenant-one", team_member_role=TeamMemberRole.SUPERADMIN)
    service = Mock(spec=TrialManagementService)

    with pytest.raises(HTTPException) as denied:
        await get_trial_status_endpoint(tenant_id="tenant-two", ctx=ctx, service=service)

    assert denied.value.status_code == 403
    service.get_trial_status.assert_not_awaited()


@pytest.mark.parametrize("role", [None, TeamMemberRole.VIEWER])
async def test_the_status_handler_preserves_existing_same_tenant_reads(role):
    tenant = "tenant-one"
    ctx = TenantContext(tenant_id=tenant, team_member_role=role)
    service = Mock(spec=TrialManagementService)
    service.get_trial_status = AsyncMock(
        return_value=TrialStatus(
            tenant_id=tenant,
            status=TrialStatusCode.ACTIVE,
            trial_started_at="2026-11-13T00:00:00+00:00",
            trial_expires_at="2026-11-27T00:00:00+00:00",
            days_remaining=13,
            hours_remaining=333,
            conversation_limit=50,
            conversations_used=7,
            conversations_remaining=43,
            usage_percent=14.0,
            model="gpt-4o-mini",
            tier_after_expiry="starter",
            can_send_message=True,
        )
    )

    result = await get_trial_status_endpoint(tenant_id=tenant, ctx=ctx, service=service)

    service.get_trial_status.assert_awaited_once_with(ctx.tenant_id)
    assert result.status == "active"
    assert result.conversations_used == 7
