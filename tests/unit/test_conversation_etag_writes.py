# (c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Read-modify-write conversation writes are ETag-conditional (review F33, Codex P1).

Every writer that reads and decrypts the whole messages array (SPEC-1843) and replaces
the document does so under If-Match with a bounded retry: the routes' append_message and
update_message_metadata, the session's AI reply (add_ai_message), the escalation append
(append_message_with_metadata) and update_encrypted_fields. Without If-Match, anything
another writer stored between that read and the replace was silently dropped. These
tests run the real routes, session and repository against a container double that bumps
``_etag`` on every write and answers an If-Match mismatch with a 412.
"""

from __future__ import annotations

import base64
import copy
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosAccessConditionFailedError

from src.chat import endpoints
from src.chat.models import IssueReportRequest, MessageFeedbackRequest, SendMessageRequest, StreamEventType
from src.chat.pipeline import ChatPipeline
from src.chat.session import ConcurrencyExhaustedError, ConversationSession
from src.multi_tenant.cosmos_schema import PreferencesDocument, TenantTier
from src.multi_tenant.repositories.base import DocumentNotFoundError
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.system_prompt_builder import AgentRole

TENANT = "tenant-f33-etag"
CONVERSATION = "conv-f33-etag"
AI_MESSAGE_ID = "m-ai"
LATE_MESSAGE_ID = "m-late"
ATTEMPTS = 3
CTX = SimpleNamespace(tenant_id=TENANT)
REPLY = "We ship to Canada in 5 days."
ESCALATION_NOTICE = "Conversation escalated to a human agent."


class _EtagContainer:
    """Cosmos container double for one conversation document.

    Every committed write bumps ``_etag``. A replace with ``MatchConditions.IfNotModified``
    and a stale ETag raises CosmosAccessConditionFailedError (412) and stores nothing. A
    queued concurrent write is committed after the caller's read and just before its
    replace is checked, which is the window the repository has to protect. It is either
    a function or a coroutine function, such as a real route call that does its own read
    and replace on this container.
    """

    def __init__(self, doc: dict[str, Any]) -> None:
        self._version = 0
        self.doc: dict[str, Any] = {}
        self._commit(doc)
        self.replace_etags: list[str | None] = []
        self.concurrent_writes: list[Callable[[], Awaitable[Any] | None]] = []
        self.always_conflict = False

    def _commit(self, body: dict[str, Any]) -> None:
        self._version += 1
        self.doc = copy.deepcopy(body)
        self.doc["_etag"] = f'"{self._version}"'

    def append_concurrently(self, message: dict[str, Any]) -> None:
        def write() -> None:
            doc = copy.deepcopy(self.doc)
            doc["messages"].append(message)
            doc["message_count"] = len(doc["messages"])
            self._commit(doc)

        self.concurrent_writes.append(write)

    async def read_item(self, item: str, partition_key: str, **kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self.doc)

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        conditional = match_condition is MatchConditions.IfNotModified
        self.replace_etags.append(etag if conditional else None)
        if self.concurrent_writes:
            written = self.concurrent_writes.pop(0)()
            if inspect.isawaitable(written):
                await written
        elif self.always_conflict:
            self._commit(self.doc)  # another writer touches the document every time
        if conditional and etag != self.doc["_etag"]:
            raise CosmosAccessConditionFailedError(status_code=412, message="Precondition failed")
        self._commit(body)
        return copy.deepcopy(self.doc)

    def message_ids(self) -> list[str]:
        return [m["message_id"] for m in self.doc["messages"]]


def _message(message_id: str, role: str, content: str) -> dict[str, Any]:
    return {"role": role, "content": content, "timestamp": "2026-10-16T10:00:00+00:00", "message_id": message_id}


def _container() -> _EtagContainer:
    messages = [_message("m-customer", "customer", "Do you ship to Canada?"), _message(AI_MESSAGE_ID, "ai", "Yes.")]
    return _EtagContainer(
        {
            "id": CONVERSATION,
            "tenant_id": TENANT,
            "conversation_id": CONVERSATION,
            "status": "active",
            "turn_count": 1,
            "message_count": len(messages),
            "messages": messages,
            "started_at": "2026-10-16T10:00:00+00:00",
            "last_activity_at": "2026-10-16T10:00:00+00:00",
        }
    )


@pytest.fixture
def container():
    store = _container()
    with (
        patch.object(ConversationRepository, "_container", new_callable=PropertyMock, return_value=store),
        patch("src.multi_tenant.envelope_encryption.get_envelope_encryption_service", return_value=None),
    ):
        yield store


@pytest.fixture
def session(container):
    real_session = ConversationSession(conversation_repo=ConversationRepository())
    audit = MagicMock()
    audit.log_event = AsyncMock()
    with (
        patch.object(endpoints, "_get_session", return_value=real_session),
        patch("src.multi_tenant.repository.AuditLogRepository", return_value=audit),
        patch("src.multi_tenant.alert_delivery.send_escalation_alert", new=AsyncMock()),
    ):
        yield real_session


async def _report_issue():
    request = IssueReportRequest(issue_type="wrong_information", details="The price was wrong")
    return await endpoints.report_issue(CONVERSATION, request, CTX)


async def _rate_ai_message():
    request = MessageFeedbackRequest(rating="negative", comment="Out of date")
    return await endpoints.submit_message_feedback(CONVERSATION, AI_MESSAGE_ID, request, CTX)


def _ai_metadata(container: _EtagContainer) -> dict[str, Any]:
    return next(m for m in container.doc["messages"] if m["message_id"] == AI_MESSAGE_ID).get("metadata") or {}


# -- normal writes ---------------------------------------------------------------------


async def test_an_issue_report_is_written_with_if_match(session, container) -> None:
    response = await _report_issue()

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID, response.issue_id]
    assert container.doc["message_count"] == 3
    assert container.replace_etags == ['"1"']


async def test_feedback_is_written_with_if_match(session, container) -> None:
    response = await _rate_ai_message()

    assert response.accepted is True
    assert _ai_metadata(container)["feedback_rating"] == "negative"
    assert _ai_metadata(container)["feedback_comment"] == "Out of date"
    assert container.replace_etags == ['"1"']


# -- a write between the read and the replace -----------------------------------------


async def test_a_message_appended_during_an_issue_report_survives(session, container) -> None:
    container.append_concurrently(_message(LATE_MESSAGE_ID, "customer", "Hello?"))

    response = await _report_issue()

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID, LATE_MESSAGE_ID, response.issue_id]
    assert container.doc["message_count"] == 4
    assert container.replace_etags == ['"1"', '"2"']


async def test_a_message_appended_during_feedback_survives(session, container) -> None:
    container.append_concurrently(_message(LATE_MESSAGE_ID, "customer", "Thanks"))

    await _rate_ai_message()

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID, LATE_MESSAGE_ID]
    assert _ai_metadata(container)["feedback_rating"] == "negative"
    assert container.replace_etags == ['"1"', '"2"']


# -- a conflict that outlasts the retries ---------------------------------------------


async def test_an_issue_report_that_keeps_conflicting_changes_nothing_and_still_answers(
    session, container, caplog
) -> None:
    container.always_conflict = True
    before = copy.deepcopy(container.doc["messages"])

    with caplog.at_level(logging.WARNING, logger="src.chat.endpoints"):
        response = await _report_issue()

    assert response.accepted is True
    assert container.doc["messages"] == before
    assert container.doc["message_count"] == 2
    assert len(container.replace_etags) == ATTEMPTS
    assert "Failed to persist issue report" in caplog.text


async def test_feedback_that_keeps_conflicting_changes_nothing_and_still_answers(session, container, caplog) -> None:
    container.always_conflict = True
    before = copy.deepcopy(container.doc["messages"])

    with caplog.at_level(logging.WARNING, logger="src.chat.endpoints"):
        response = await _rate_ai_message()

    assert response.accepted is True
    assert container.doc["messages"] == before
    assert len(container.replace_etags) == ATTEMPTS
    assert "Failed to persist message feedback" in caplog.text


# -- the repository and the session's other callers -----------------------------------


async def test_update_message_metadata_rejects_an_index_past_the_transcript(container) -> None:
    with pytest.raises(DocumentNotFoundError):
        await ConversationRepository().update_message_metadata(TENANT, CONVERSATION, 5, {"x": 1})

    assert container.replace_etags == []


async def test_append_message_raises_the_412_once_the_attempts_are_used(container) -> None:
    container.always_conflict = True

    with pytest.raises(CosmosAccessConditionFailedError):
        await ConversationRepository().append_message(TENANT, CONVERSATION, _message("m-x", "system", "x"))

    assert len(container.replace_etags) == ATTEMPTS
    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID]


async def test_a_customer_message_keeps_a_concurrent_append(container) -> None:
    container.append_concurrently(_message(LATE_MESSAGE_ID, "ai", "One more thing"))
    request = SendMessageRequest(conversation_id=CONVERSATION, content="Where is my order?")

    response = await ConversationSession(conversation_repo=ConversationRepository()).add_customer_message(
        TENANT, request
    )

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID, LATE_MESSAGE_ID, response.message_id]


async def test_a_customer_message_that_keeps_conflicting_reports_concurrency_exhausted(container) -> None:
    container.always_conflict = True
    request = SendMessageRequest(conversation_id=CONVERSATION, content="Where is my order?")

    with pytest.raises(ConcurrencyExhaustedError):
        await ConversationSession(conversation_repo=ConversationRepository()).add_customer_message(TENANT, request)

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID]


# -- the other writers of the transcript: AI reply, escalation, encrypted fields -------
#
# Each race queues a real route call (an issue report or feedback) that commits after
# the writer's read and before its replace is checked.

_NONCE_AND_TAG = b"n" * 28  # stands in for the 12-byte nonce and 16-byte tag


def _fake_encrypt(wrapped_dek: bytes, plaintext: str, **_: Any) -> str:
    return base64.b64encode(_NONCE_AND_TAG + plaintext.encode("utf-8")).decode("ascii")


def _fake_decrypt(wrapped_dek: bytes, ciphertext: str, **_: Any) -> str:
    return base64.b64decode(ciphertext)[len(_NONCE_AND_TAG) :].decode("utf-8")


def _decrypted_messages(stored: str) -> list[dict[str, Any]]:
    plaintext = _fake_decrypt(b"", stored)
    assert plaintext.startswith("json:")
    return json.loads(plaintext[len("json:") :])


@pytest.fixture
def encryption(container):
    """SPEC-1843 field encryption on, with a reversible fake service and a tenant DEK."""
    service = SimpleNamespace(encrypt_field=_fake_encrypt, decrypt_field=_fake_decrypt)
    dek = AsyncMock(return_value=SimpleNamespace(wrapped_dek=b"wrapped-dek"))
    with (
        patch("src.multi_tenant.envelope_encryption.get_envelope_encryption_service", return_value=service),
        patch.object(ConversationRepository, "_get_tenant_dek", new=dek),
    ):
        yield service


@pytest.fixture
def escalation(session):
    with patch("src.chat.quality_closeout.evaluate_quality_and_alert", new=AsyncMock()):
        yield session


def _issue_report_kept(messages: list[dict[str, Any]]) -> bool:
    return any((m.get("metadata") or {}).get("type") == "issue_report" for m in messages)


def _feedback_kept(messages: list[dict[str, Any]]) -> bool:
    ai = next(m for m in messages if m["message_id"] == AI_MESSAGE_ID)
    return (ai.get("metadata") or {}).get("feedback_rating") == "negative"


CONCURRENT_ROUTE_WRITES = pytest.mark.parametrize(
    ("route", "kept"),
    [(_report_issue, _issue_report_kept), (_rate_ai_message, _feedback_kept)],
    ids=["issue-report", "feedback"],
)


async def _escalate(session: ConversationSession) -> None:
    await session.escalate_conversation(
        TENANT,
        CONVERSATION,
        escalation_reason="Asked for a person",
        escalation_category="general_inquiry",
        assigned_to="agent-7",
    )


async def _set_intent() -> dict[str, Any]:
    return await ConversationRepository().update_encrypted_fields(TENANT, CONVERSATION, {"customer_intent": "shipping"})


# -- normal writes ---------------------------------------------------------------------


async def test_an_ai_reply_is_written_with_if_match(session, container) -> None:
    message_id = await session.add_ai_message(
        TENANT, CONVERSATION, REPLY, agents_invoked=["response-generator"], model_used="gpt-4o", critic_passed=True
    )

    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID, message_id]
    assert container.doc["messages"][-1]["content"] == REPLY
    assert container.doc["message_count"] == 3
    assert container.doc["turn_count"] == 2
    assert container.doc["agents_invoked"] == ["response-generator"]
    assert container.doc["model_used"] == "gpt-4o"
    assert container.doc["critic_passed"] is True
    assert container.doc["is_billable"] is True
    assert container.replace_etags == ['"1"']


async def test_an_escalation_is_written_with_if_match(escalation, container) -> None:
    await _escalate(escalation)

    assert container.doc["messages"][-1]["content"] == ESCALATION_NOTICE
    assert container.doc["message_count"] == 3
    assert container.doc["status"] == "escalated"
    assert container.doc["escalation_category"] == "general_inquiry"
    assert container.doc["assigned_to"] == "agent-7"
    assert container.replace_etags == ['"1"']


async def test_an_encrypted_field_update_is_written_with_if_match(container) -> None:
    await _set_intent()

    assert container.doc["customer_intent"] == "shipping"
    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID]
    assert container.replace_etags == ['"1"']


# -- a route write between the writer's read and its replace --------------------------


@CONCURRENT_ROUTE_WRITES
async def test_an_ai_reply_read_before_a_route_write_keeps_it(session, container, route, kept) -> None:
    container.concurrent_writes.append(route)

    message_id = await session.add_ai_message(TENANT, CONVERSATION, REPLY)

    messages = container.doc["messages"]
    assert kept(messages)
    assert messages[-1]["message_id"] == message_id
    assert messages[-1]["content"] == REPLY
    assert container.doc["message_count"] == len(messages)
    assert container.doc["turn_count"] == 2


@CONCURRENT_ROUTE_WRITES
async def test_an_escalation_read_before_a_route_write_keeps_it(escalation, container, route, kept) -> None:
    container.concurrent_writes.append(route)

    await _escalate(escalation)

    messages = container.doc["messages"]
    assert kept(messages)
    assert messages[-1]["content"] == ESCALATION_NOTICE
    assert container.doc["message_count"] == len(messages)
    assert container.doc["status"] == "escalated"


@CONCURRENT_ROUTE_WRITES
async def test_an_encrypted_field_update_read_before_a_route_write_keeps_it(session, container, route, kept) -> None:
    container.concurrent_writes.append(route)

    await _set_intent()

    assert kept(container.doc["messages"])
    assert container.doc["customer_intent"] == "shipping"


async def test_an_ai_reply_retried_after_an_issue_report_stays_encrypted_and_scrubbed(
    session, container, encryption
) -> None:
    session.set_pii_scrubber(True, tenant_id=TENANT)
    container.concurrent_writes.append(_report_issue)

    await session.add_ai_message(TENANT, CONVERSATION, "We will write to ana@example.com")

    stored = container.doc["messages"]
    assert isinstance(stored, str), "the transcript is stored as ciphertext (SPEC-1843)"
    messages = _decrypted_messages(stored)
    assert _issue_report_kept(messages)
    assert "ana@example.com" not in messages[-1]["content"]
    assert "[REDACTED:email]" in messages[-1]["content"]


# -- a conflict that outlasts the retries, per caller ----------------------------------


async def test_an_ai_reply_that_keeps_conflicting_raises_concurrency_exhausted_and_logs_it(
    session, container, caplog
) -> None:
    container.always_conflict = True
    before = copy.deepcopy(container.doc["messages"])

    with caplog.at_level(logging.ERROR, logger="src.chat.session"), pytest.raises(ConcurrencyExhaustedError):
        await session.add_ai_message(TENANT, CONVERSATION, REPLY)

    assert container.doc["messages"] == before
    assert container.doc["turn_count"] == 1
    assert len(container.replace_etags) == ATTEMPTS
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "a reply that was not stored is logged as an error"
    assert CONVERSATION in errors[-1].getMessage()


async def test_an_escalation_that_keeps_conflicting_raises_and_changes_nothing(escalation, container) -> None:
    container.always_conflict = True
    before = copy.deepcopy(container.doc["messages"])

    with pytest.raises(CosmosAccessConditionFailedError):
        await _escalate(escalation)

    assert container.doc["messages"] == before
    assert container.doc["status"] == "active"
    assert len(container.replace_etags) == ATTEMPTS


async def test_an_encrypted_field_update_that_keeps_conflicting_raises_the_412(container) -> None:
    container.always_conflict = True

    with pytest.raises(CosmosAccessConditionFailedError):
        await _set_intent()

    assert "customer_intent" not in container.doc
    assert len(container.replace_etags) == ATTEMPTS


# -- the customer reply path ------------------------------------------------------------


def _pipeline(session: ConversationSession) -> ChatPipeline:
    """ChatPipeline over the real session, with the agents and the Critic stubbed."""
    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = dict.fromkeys(AgentRole, "prompt")
    prompt_builder.explain.return_value = {}
    pipeline = ChatPipeline(session=session, prompt_builder=prompt_builder, profile_service=AsyncMock())

    async def reply_stream(*args: Any, **kwargs: Any) -> Any:
        yield REPLY

    async def approve(tenant_id: str, conversation_id: str, text: str, *args: Any, **kwargs: Any) -> Any:
        return True, text, SimpleNamespace(verdict=None, flags=[], latency_ms=1.0)

    pipeline._call_intent_classifier = AsyncMock(return_value={"intent": "general_inquiry", "confidence": 0.95})
    pipeline._call_knowledge_retrieval = AsyncMock(return_value={"context": "", "sources": []})
    pipeline._call_response_generator_stream = reply_stream
    pipeline._validate_with_critic = approve
    pipeline._load_customer_profile = AsyncMock(return_value=None)
    pipeline._fire_analytics = AsyncMock()
    return pipeline


async def test_the_pipeline_tells_the_customer_when_their_reply_could_not_be_stored(session, container) -> None:
    container.always_conflict = True
    preferences = PreferencesDocument(
        id=f"{TENANT}:v1", tenant_id=TENANT, version=1, created_at="2026-10-16T10:00:00+00:00"
    )
    tenant = SimpleNamespace(tier=TenantTier.STARTER)

    events = [
        event
        async for event in _pipeline(session).execute(
            TENANT, CONVERSATION, "Do you ship to Canada?", tenant=tenant, preferences=preferences
        )
    ]

    kinds = [event.event for event in events]
    assert StreamEventType.VALIDATED not in kinds
    [error] = [event for event in events if event.event is StreamEventType.ERROR]
    assert error.data["recoverable"] is True
    assert kinds[-1] is StreamEventType.DONE
    assert container.message_ids() == ["m-customer", AI_MESSAGE_ID]
