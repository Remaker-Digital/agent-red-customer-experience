# tests/unit/test_staff_session_revalidation.py: staff sessions are checked against the member record
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
A magic-link session lasts 8 hours. For a team member session the middleware took the
role from the JWT and read the member document only for its domain fields, inside a
try/except that logged a warning and carried on. A removed, deactivated or demoted
member kept the JWT's role until the token expired, and any lookup failure kept full
access. Architecture review S12 (docs/agent-red-architecture-review.md).

Every request that carries a member claim now reads the member document: a missing or
inactive member is refused with the same 401 as an expired session, the role comes
from the document, and a failed lookup refuses with a 503 that the pre-auth limiter
does not count. Owner sessions (no member claim) are unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

import src.multi_tenant.middleware as mw
from src.multi_tenant.auth import AuthenticationError
from src.multi_tenant.cosmos_schema import TeamMemberRole
from src.multi_tenant.magic_link_auth import create_magic_link_session_token
from src.multi_tenant.repositories import DocumentNotFoundError

TENANT_ID = "t-s12-001"
MEMBER_EMAIL = "agent@example.com"
MEMBER_ID = f"{TENANT_ID}:{MEMBER_EMAIL}"
SESSION_REFUSED = "Invalid or expired session token. Please sign in again."
REPO_PATH = "src.multi_tenant.repositories.TeamMemberRepository"


@pytest.fixture(autouse=True)
def _tenant_resolver(monkeypatch):
    """An active Professional tenant, with an empty tenant metadata cache."""
    monkeypatch.setattr(mw, "_tenant_meta_cache", {})
    resolver = AsyncMock(
        return_value={"id": TENANT_ID, "tenant_id": TENANT_ID, "status": "active", "tier": "professional"}
    )
    monkeypatch.setattr(mw, "_resolve_by_tenant_id", resolver)
    return resolver


def _member_token(role: str | None = "admin", member_id: str | None = MEMBER_ID) -> str:
    token, _ = create_magic_link_session_token(TENANT_ID, MEMBER_EMAIL, member_id=member_id, role=role)
    return token


def _member_doc(**fields) -> dict:
    doc = {
        "id": MEMBER_ID,
        "tenant_id": TENANT_ID,
        "email": MEMBER_EMAIL,
        "role": "admin",
        "is_active": True,
        "escalation_categories": ["support"],
        "agent_access": ["sales"],
        "staff_domain_tags": ["billing"],
    }
    doc.update(fields)
    return doc


def _team_repo(*, doc: dict | None = None, error: Exception | None = None) -> MagicMock:
    repo = MagicMock()
    repo.read = AsyncMock(return_value=doc, side_effect=error)
    return repo


async def _authenticate(token: str, repo: MagicMock):
    with patch(REPO_PATH, return_value=repo):
        return await mw.TenantAuthMiddleware(MagicMock())._auth_magic_link_session(token)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_a_removed_member_is_refused():
    repo = _team_repo(error=DocumentNotFoundError("team_members", MEMBER_ID, TENANT_ID))

    with pytest.raises(AuthenticationError) as refused:
        await _authenticate(_member_token(), repo)

    assert refused.value.status_code == 401
    assert refused.value.message == SESSION_REFUSED
    repo.read.assert_awaited_once_with(TENANT_ID, MEMBER_ID)


async def test_a_deactivated_member_is_refused():
    with pytest.raises(AuthenticationError) as refused:
        await _authenticate(_member_token(), _team_repo(doc=_member_doc(is_active=False)))

    assert refused.value.status_code == 401
    assert refused.value.message == SESSION_REFUSED


async def test_an_invalid_token_gets_the_same_refusal_as_a_removed_member():
    repo = _team_repo(doc=_member_doc())

    with pytest.raises(AuthenticationError) as refused:
        await _authenticate("not-a-session-jwt", repo)

    assert refused.value.status_code == 401
    assert refused.value.message == SESSION_REFUSED
    repo.read.assert_not_awaited()


@pytest.mark.parametrize("is_active", [None, "false", 0])
async def test_a_member_whose_active_flag_is_not_true_is_refused(is_active):
    with pytest.raises(AuthenticationError) as refused:
        await _authenticate(_member_token(), _team_repo(doc=_member_doc(is_active=is_active)))

    assert refused.value.status_code == 401


async def test_a_removed_member_without_a_role_claim_is_refused():
    """Sign-in issues a member id without a role when its member lookup failed."""
    repo = _team_repo(error=DocumentNotFoundError("team_members", MEMBER_ID, TENANT_ID))

    with pytest.raises(AuthenticationError) as refused:
        await _authenticate(_member_token(role=None), repo)

    assert refused.value.status_code == 401
    assert refused.value.message == SESSION_REFUSED


async def test_a_lookup_failure_refuses_with_503():
    with pytest.raises(AuthenticationError) as refused:
        await _authenticate(_member_token(), _team_repo(error=ConnectionError("cosmos unreachable")))

    assert refused.value.status_code == 503
    assert refused.value.message != SESSION_REFUSED


async def test_a_repository_that_cannot_start_refuses_with_503():
    with patch(REPO_PATH, side_effect=RuntimeError("cosmos not initialized")):
        with pytest.raises(AuthenticationError) as refused:
            await mw.TenantAuthMiddleware(MagicMock())._auth_magic_link_session(_member_token())

    assert refused.value.status_code == 503


# ---------------------------------------------------------------------------
# The role and domain fields come from the member document
# ---------------------------------------------------------------------------


async def test_a_demoted_member_gets_the_demoted_role_on_the_next_request():
    ctx = await _authenticate(_member_token(role="admin"), _team_repo(doc=_member_doc(role="viewer")))

    assert ctx.team_member_role == TeamMemberRole.VIEWER


@pytest.mark.parametrize(
    ("signed", "current"),
    [("viewer", "admin"), ("escalation_agent", "superadmin"), ("admin", "superadmin"), ("viewer", "escalation_agent")],
)
async def test_a_promoted_member_keeps_the_signed_role_until_the_next_sign_in(signed, current):
    """Promotion would skip the 2FA check sign-in applies to admin roles."""
    ctx = await _authenticate(_member_token(role=signed), _team_repo(doc=_member_doc(role=current)))

    assert ctx.team_member_role == TeamMemberRole(signed)


async def test_an_unknown_signed_role_is_treated_as_viewer():
    ctx = await _authenticate(_member_token(role="owner"), _team_repo(doc=_member_doc(role="admin")))

    assert ctx.team_member_role == TeamMemberRole.VIEWER


async def test_an_unknown_role_on_the_member_falls_back_to_viewer():
    ctx = await _authenticate(_member_token(role="admin"), _team_repo(doc=_member_doc(role="owner")))

    assert ctx.team_member_role == TeamMemberRole.VIEWER


async def test_an_active_member_keeps_working_with_its_domain_fields():
    doc = _member_doc(role="escalation_agent")

    ctx = await _authenticate(_member_token(role="escalation_agent"), _team_repo(doc=doc))

    assert ctx.auth_method == "magic_link_session"
    assert ctx.tenant_id == TENANT_ID
    assert ctx.team_member_id == MEMBER_ID
    assert ctx.team_member_email == MEMBER_EMAIL
    assert ctx.team_member_role == TeamMemberRole.ESCALATION_AGENT
    assert ctx.escalation_categories == ("support",)
    assert ctx.agent_access == ("sales",)
    assert ctx.staff_domain_tags == ("billing",)


async def test_a_member_document_without_the_active_flag_is_active():
    """TeamMemberDocument.is_active defaults to True."""
    doc = _member_doc()
    del doc["is_active"]

    ctx = await _authenticate(_member_token(), _team_repo(doc=doc))

    assert ctx.team_member_role == TeamMemberRole.ADMIN


async def test_an_active_member_without_a_role_claim_keeps_an_unresolved_role():
    """The role is not filled in from the document: sign-in skipped the 2FA check for this token."""
    ctx = await _authenticate(_member_token(role=None), _team_repo(doc=_member_doc(role="admin")))

    assert ctx.team_member_id == MEMBER_ID
    assert ctx.team_member_role is None
    assert ctx.escalation_categories == ()
    assert ctx.agent_access == ()
    assert ctx.staff_domain_tags == ()


async def test_an_owner_session_without_member_claims_is_unchanged():
    token, _ = create_magic_link_session_token(TENANT_ID, "owner@example.com")
    repo_class = MagicMock()

    with patch(REPO_PATH, repo_class):
        ctx = await mw.TenantAuthMiddleware(MagicMock())._auth_magic_link_session(token)

    repo_class.assert_not_called()
    assert ctx.tenant_id == TENANT_ID
    assert ctx.auth_method == "magic_link_session"
    assert ctx.team_member_email == "owner@example.com"
    assert ctx.team_member_id is None
    assert ctx.team_member_role is None
    assert ctx.escalation_categories == ()
    assert ctx.agent_access == ()
    assert ctx.staff_domain_tags == ()


# ---------------------------------------------------------------------------
# Through the middleware: what reaches the client and the pre-auth limiter
# ---------------------------------------------------------------------------


def _session_request(token: str) -> MagicMock:
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/api/admin/conversations"
    request.headers = Headers({"x-session-token": token})
    request.query_params = {}
    request.scope = {"client": ("203.0.113.9", 50000)}
    return request


async def _dispatch(token: str, repo: MagicMock):
    limiter = MagicMock()
    call_next = AsyncMock()
    with (
        patch(REPO_PATH, return_value=repo),
        patch("src.multi_tenant.security_hardening.get_pre_auth_limiter", return_value=limiter),
    ):
        response = await mw.TenantAuthMiddleware(MagicMock()).dispatch(_session_request(token), call_next)
    return response, limiter, call_next


async def test_a_lookup_failure_is_a_503_that_does_not_count_toward_the_pre_auth_block():
    response, limiter, call_next = await _dispatch(_member_token(), _team_repo(error=TimeoutError("cosmos timeout")))

    assert response.status_code == 503
    limiter.record_failure.assert_not_called()
    call_next.assert_not_awaited()


async def test_a_removed_member_is_a_401_that_counts_like_an_expired_session():
    repo = _team_repo(error=DocumentNotFoundError("team_members", MEMBER_ID, TENANT_ID))

    response, limiter, call_next = await _dispatch(_member_token(), repo)

    assert response.status_code == 401
    assert SESSION_REFUSED.encode() in response.body
    limiter.record_failure.assert_called_once_with("203.0.113.9")
    call_next.assert_not_awaited()
