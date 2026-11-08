# (c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Issue reports, message feedback and consent reach the conversation document (review F33).

The three routes wrote through ``session._conversation_repo``, which ConversationSession
does not have, and swallowed the AttributeError, so nothing was stored. These tests use a
real ConversationSession (only get_conversation is replaced), so a wrong attribute name
cannot hide behind a mock.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.chat import endpoints
from src.chat.models import ConsentUpdateRequest, IssueReportRequest, MessageFeedbackRequest, MessageRole
from src.chat.session import ConversationSession

TENANT = "tenant-f33"
CONVERSATION = "conv-f33"
CTX = SimpleNamespace(tenant_id=TENANT)


def _session(state: object) -> tuple[ConversationSession, MagicMock]:
    repo = MagicMock()
    repo.append_message = AsyncMock()
    repo.update_message_metadata = AsyncMock()
    repo.patch = AsyncMock()
    session = ConversationSession(conversation_repo=repo)
    session.get_conversation = AsyncMock(return_value=state)  # type: ignore[method-assign]
    return session, repo


def _audit_repo() -> MagicMock:
    audit = MagicMock()
    audit.log_event = AsyncMock()
    return audit


def test_the_session_exposes_its_conversation_repository() -> None:
    repo = MagicMock()
    assert ConversationSession(conversation_repo=repo).conversation_repo is repo


async def test_an_issue_report_is_appended_to_the_conversation() -> None:
    session, repo = _session(SimpleNamespace(messages=[], customer_id=None))
    request = IssueReportRequest(issue_type="wrong_information", details="The price was wrong")

    with (
        patch.object(endpoints, "_get_session", return_value=session),
        patch("src.multi_tenant.repository.AuditLogRepository", return_value=_audit_repo()),
        patch("src.multi_tenant.alert_delivery.send_escalation_alert", new=AsyncMock()),
    ):
        response = await endpoints.report_issue(CONVERSATION, request, CTX)

    repo.append_message.assert_awaited_once()
    tenant_id, conversation_id, message = repo.append_message.await_args.args
    assert (tenant_id, conversation_id) == (TENANT, CONVERSATION)
    assert message["metadata"]["type"] == "issue_report"
    assert message["message_id"] == response.issue_id


async def test_message_feedback_is_stored_on_the_rated_message() -> None:
    messages = [
        SimpleNamespace(message_id="m-customer", role=MessageRole.CUSTOMER),
        SimpleNamespace(message_id="m-ai", role=MessageRole.AI),
    ]
    session, repo = _session(SimpleNamespace(messages=messages, customer_id=None))

    with (
        patch.object(endpoints, "_get_session", return_value=session),
        patch("src.multi_tenant.repository.AuditLogRepository", return_value=_audit_repo()),
    ):
        await endpoints.submit_message_feedback(CONVERSATION, "m-ai", MessageFeedbackRequest(rating="positive"), CTX)

    repo.update_message_metadata.assert_awaited_once()
    tenant_id, conversation_id, index, data = repo.update_message_metadata.await_args.args
    assert (tenant_id, conversation_id, index) == (TENANT, CONVERSATION, 1)
    assert "positive" in data.values()


async def test_consent_is_recorded_on_the_conversation() -> None:
    session, repo = _session(SimpleNamespace(messages=[], customer_id=None))

    with (
        patch.object(endpoints, "_get_session", return_value=session),
        patch("src.multi_tenant.repository.AuditLogRepository", return_value=_audit_repo()),
    ):
        await endpoints.update_consent(CONVERSATION, ConsentUpdateRequest(consent_status="granted"), CTX)

    repo.patch.assert_awaited_once()
    tenant_id, conversation_id, operations = repo.patch.await_args.args
    assert (tenant_id, conversation_id) == (TENANT, CONVERSATION)
    assert {"op": "set", "path": "/consent_status", "value": "granted"} in operations
