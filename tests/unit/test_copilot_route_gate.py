# tests/unit/test_copilot_route_gate.py: only team members reach the Co-pilot
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
The classifier can label a customer's message ``admin_assistance``. The router used to
send that intent to the staff Co-pilot for any caller; SPEC-1558 gates it from widget-key
conversations. Architecture review S6 (docs/agent-red-architecture-review.md).
"""

from __future__ import annotations

import pytest

from src.chat.pipeline.intent_router import IntentRouter, RouteTarget


@pytest.mark.parametrize("intent", ["admin_assistance", "product_question"])
def test_customers_never_reach_the_co_pilot(intent):
    route = IntentRouter().resolve(tenant_id="t-1", intent=intent, confidence=0.95)
    assert route.target != RouteTarget.CO_PILOT


@pytest.mark.parametrize("role", ["admin", "viewer", "escalation_agent"])
def test_team_members_reach_the_co_pilot(role):
    route = IntentRouter().resolve(tenant_id="t-1", intent="admin_assistance", confidence=0.95, team_member_role=role)
    assert route.target == RouteTarget.CO_PILOT


def test_a_tenant_admin_credential_reaches_the_co_pilot_for_admin_assistance():
    """ar_live_ keys and Shopify sessions carry no member role but are the merchant (Codex review)."""
    route = IntentRouter().resolve(tenant_id="t-1", intent="admin_assistance", confidence=0.95, trusted_admin=True)
    assert route.target == RouteTarget.CO_PILOT


def test_a_tenant_admin_credential_asks_other_questions_on_the_customer_pipeline():
    route = IntentRouter().resolve(tenant_id="t-1", intent="product_question", confidence=0.95, trusted_admin=True)
    assert route.target != RouteTarget.CO_PILOT


@pytest.mark.parametrize(
    ("auth_method", "member_id", "role", "expected"),
    [
        ("api_key", None, None, True),
        ("shopify_session", None, None, True),
        ("magic_link_session", None, None, True),  # owner sign-in
        ("magic_link_session", "member-1", None, False),  # member whose role was not resolved
        ("widget_key", None, None, False),
        ("user_api_key", "member-1", "viewer", False),  # team members route by their role instead
    ],
)
def test_tenant_admin_credential(auth_method, member_id, role, expected):
    from src.chat.endpoints import _is_tenant_admin_credential
    from src.multi_tenant.auth import TenantContext
    from src.multi_tenant.cosmos_schema import TeamMemberRole, TenantStatus, TenantTier

    ctx = TenantContext(
        tenant_id="t-1",
        tier=TenantTier.STARTER,
        status=TenantStatus.ACTIVE,
        auth_method=auth_method,
        team_member_id=member_id,
        team_member_role=TeamMemberRole(role) if role else None,
    )
    assert _is_tenant_admin_credential(ctx) is expected
