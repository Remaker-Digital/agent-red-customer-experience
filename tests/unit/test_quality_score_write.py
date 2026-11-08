# (c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""The per-reply quality score is stored on its message (architecture review F39, P3-1).

After the Critic approves a reply, the orchestrator scores it and calls
``ConversationSession.patch_message_metadata`` to merge ``quality_score`` into that
message's metadata, which ``quality_closeout`` later averages into
``quality_aggregate``. The method read the conversation with ``self._repo.get``, which
``ConversationRepository`` does not have, so every call raised ``AttributeError`` before
writing and no score was ever stored. A ``MagicMock`` repository hides that, so these
tests use a double specced from ``ConversationRepository`` (only its real methods exist),
the real repository over a container double that enforces If-Match, and the real
pipeline.
"""

from __future__ import annotations

import copy
import inspect
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, PropertyMock, create_autospec, patch

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosAccessConditionFailedError

from src.chat.models import StreamEventType
from src.chat.pipeline import ChatPipeline
from src.chat.quality_scorer import quality_scorer
from src.chat.session import ConversationSession
from src.multi_tenant.cosmos_schema import PreferencesDocument, TenantTier
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.system_prompt_builder import AgentRole

TENANT = "tenant-f39"
CONVERSATION = "conv-f39"
CUSTOMER_MESSAGE_ID = "m-customer"
EARLIER_AI_MESSAGE_ID = "m-ai-earlier"
AI_MESSAGE_ID = "m-ai"
LATE_MESSAGE_ID = "m-late"
AI_ROLE = "ai"
CUSTOMER_ROLE = "customer"
QUESTION = "Do you ship to Canada?"
REPLY = "We ship to Canada in 5 days."
SCORE = {"faithfulness": 4.0, "relevancy": 5.0, "tone": 5.0, "overall": 4.6, "issues": []}
PATCH = {"quality_score": SCORE}
ATTEMPTS = 3
FIRST_ETAG = '"1"'
SECOND_ETAG = '"2"'
NON_FATAL_WARNING = "Quality scoring failed (non-fatal)"


def _message(message_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": "2026-10-16T10:00:00+00:00",
        "message_id": message_id,
    }
    if metadata is not None:
        msg["metadata"] = metadata
    return msg


def _late_message() -> dict[str, Any]:
    return _message(LATE_MESSAGE_ID, CUSTOMER_ROLE, "And to Mexico?")


def _doc(etag: str = FIRST_ETAG, *extra: dict[str, Any]) -> dict[str, Any]:
    messages = [
        _message(EARLIER_AI_MESSAGE_ID, AI_ROLE, "Hello, how can I help?", {"intent": "greeting"}),
        _message(CUSTOMER_MESSAGE_ID, CUSTOMER_ROLE, QUESTION),
        _message(AI_MESSAGE_ID, AI_ROLE, REPLY, {"intent": "shipping"}),
        *extra,
    ]
    return {
        "id": CONVERSATION,
        "tenant_id": TENANT,
        "conversation_id": CONVERSATION,
        "status": "active",
        "turn_count": 2,
        "message_count": len(messages),
        "messages": messages,
        "_etag": etag,
    }


def _by_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["message_id"]: m for m in doc["messages"]}


def _conflict() -> CosmosAccessConditionFailedError:
    return CosmosAccessConditionFailedError(status_code=412, message="Precondition failed")


# -- a repository double with only ConversationRepository's methods --------------------


def _repo_double() -> Any:
    """Autospec of ConversationRepository: a method it lacks raises AttributeError."""
    return create_autospec(ConversationRepository, instance=True)


def _written(call: Any) -> tuple[dict[str, Any], str]:
    """The document and ETag of one replace_with_etag call, however it was passed."""
    bound = inspect.signature(ConversationRepository.replace_with_etag).bind(None, *call.args, **call.kwargs)
    return bound.arguments["doc"], bound.arguments["etag"]


async def test_the_score_is_merged_into_the_metadata_of_its_message() -> None:
    repo = _repo_double()
    repo.read.return_value = _doc()

    await ConversationSession(conversation_repo=repo).patch_message_metadata(
        TENANT,
        CONVERSATION,
        AI_MESSAGE_ID,
        PATCH,
    )

    repo.read.assert_awaited_once_with(TENANT, CONVERSATION)
    repo.replace_with_etag.assert_awaited_once_with(TENANT, CONVERSATION, ANY, FIRST_ETAG)
    written, _ = _written(repo.replace_with_etag.await_args)
    messages = _by_id(written)
    assert messages[AI_MESSAGE_ID]["metadata"] == {"intent": "shipping", "quality_score": SCORE}
    assert messages[EARLIER_AI_MESSAGE_ID]["metadata"] == {"intent": "greeting"}
    assert "metadata" not in messages[CUSTOMER_MESSAGE_ID]
    assert [m["message_id"] for m in written["messages"]] == [
        EARLIER_AI_MESSAGE_ID,
        CUSTOMER_MESSAGE_ID,
        AI_MESSAGE_ID,
    ]


async def test_a_message_without_metadata_gets_the_score() -> None:
    repo = _repo_double()
    repo.read.return_value = _doc()

    await ConversationSession(conversation_repo=repo).patch_message_metadata(
        TENANT,
        CONVERSATION,
        CUSTOMER_MESSAGE_ID,
        PATCH,
    )

    written, _ = _written(repo.replace_with_etag.await_args)
    assert _by_id(written)[CUSTOMER_MESSAGE_ID]["metadata"] == PATCH


async def test_an_unknown_message_writes_nothing_and_warns(caplog) -> None:
    repo = _repo_double()
    repo.read.return_value = _doc()

    with caplog.at_level(logging.WARNING, logger="src.chat.session"):
        await ConversationSession(conversation_repo=repo).patch_message_metadata(
            TENANT,
            CONVERSATION,
            "m-missing",
            PATCH,
        )

    repo.replace_with_etag.assert_not_awaited()
    assert "m-missing" in caplog.text


async def test_a_transcript_that_is_not_a_list_is_not_written_over() -> None:
    repo = _repo_double()
    doc = _doc()
    doc["messages"] = "still-ciphertext"
    repo.read.return_value = doc

    await ConversationSession(conversation_repo=repo).patch_message_metadata(
        TENANT,
        CONVERSATION,
        AI_MESSAGE_ID,
        PATCH,
    )

    repo.replace_with_etag.assert_not_awaited()


async def test_a_conflict_rereads_and_keeps_the_other_writers_message() -> None:
    repo = _repo_double()
    repo.read.side_effect = [_doc(FIRST_ETAG), _doc(SECOND_ETAG, _late_message())]
    repo.replace_with_etag.side_effect = [_conflict(), {}]

    await ConversationSession(conversation_repo=repo).patch_message_metadata(
        TENANT,
        CONVERSATION,
        AI_MESSAGE_ID,
        PATCH,
    )

    assert repo.read.await_count == 2
    assert [_written(call)[1] for call in repo.replace_with_etag.await_args_list] == [FIRST_ETAG, SECOND_ETAG]
    written, _ = _written(repo.replace_with_etag.await_args)
    messages = _by_id(written)
    assert LATE_MESSAGE_ID in messages
    assert messages[AI_MESSAGE_ID]["metadata"]["quality_score"] == SCORE


async def test_the_last_conflict_propagates_after_the_bounded_attempts() -> None:
    repo = _repo_double()
    repo.read.side_effect = lambda *args, **kwargs: _doc()
    repo.replace_with_etag.side_effect = _conflict()

    with pytest.raises(CosmosAccessConditionFailedError):
        await ConversationSession(conversation_repo=repo).patch_message_metadata(
            TENANT,
            CONVERSATION,
            AI_MESSAGE_ID,
            PATCH,
        )

    assert repo.read.await_count == ATTEMPTS
    assert repo.replace_with_etag.await_count == ATTEMPTS


# -- the real repository over a container that enforces If-Match ------------------------


class _EtagContainer:
    """Cosmos container double for one conversation document.

    Every committed write bumps ``_etag``. A replace with ``MatchConditions.IfNotModified``
    and a stale ETag raises a 412 and stores nothing. ``concurrent_writes`` are committed
    after the caller's read and just before its replace is checked; ``conflict_when``
    makes another writer touch the document before every replace whose body matches.
    """

    def __init__(self, doc: dict[str, Any]) -> None:
        self._version = 0
        self.doc: dict[str, Any] = {}
        self._commit(doc)
        self.replace_etags: list[str | None] = []
        self.concurrent_writes: list[Callable[[], None]] = []
        self.conflict_when: Callable[[dict[str, Any]], bool] | None = None

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
            self.concurrent_writes.pop(0)()
        elif self.conflict_when is not None and self.conflict_when(body):
            self._commit(self.doc)
        if conditional and etag != self.doc["_etag"]:
            raise _conflict()
        self._commit(body)
        return copy.deepcopy(self.doc)


def _carries_a_score(body: dict[str, Any]) -> bool:
    return any("quality_score" in (m.get("metadata") or {}) for m in body.get("messages", []))


@pytest.fixture
def container():
    store = _EtagContainer(_doc())
    with (
        patch.object(ConversationRepository, "_container", new_callable=PropertyMock, return_value=store),
        patch("src.multi_tenant.envelope_encryption.get_envelope_encryption_service", return_value=None),
    ):
        yield store


@pytest.fixture
def session(container):
    return ConversationSession(conversation_repo=ConversationRepository())


async def test_the_score_is_stored_with_if_match(session, container) -> None:
    await session.patch_message_metadata(TENANT, CONVERSATION, AI_MESSAGE_ID, PATCH)

    assert container.replace_etags == [FIRST_ETAG]
    assert _by_id(container.doc)[AI_MESSAGE_ID]["metadata"] == {"intent": "shipping", "quality_score": SCORE}


async def test_a_message_appended_during_the_score_write_survives(session, container) -> None:
    container.append_concurrently(_late_message())

    await session.patch_message_metadata(TENANT, CONVERSATION, AI_MESSAGE_ID, PATCH)

    assert container.replace_etags == [FIRST_ETAG, SECOND_ETAG]
    messages = _by_id(container.doc)
    assert LATE_MESSAGE_ID in messages
    assert messages[AI_MESSAGE_ID]["metadata"]["quality_score"] == SCORE


async def test_a_score_write_that_keeps_conflicting_changes_nothing(session, container) -> None:
    container.conflict_when = _carries_a_score

    with pytest.raises(CosmosAccessConditionFailedError):
        await session.patch_message_metadata(TENANT, CONVERSATION, AI_MESSAGE_ID, PATCH)

    assert len(container.replace_etags) == ATTEMPTS
    assert None not in container.replace_etags
    assert not _carries_a_score(container.doc)


# -- the orchestrator: a validated reply is scored and the score stored on it ----------


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


async def _run_pipeline(session: ConversationSession) -> list[Any]:
    preferences = PreferencesDocument(
        id=f"{TENANT}:v1",
        tenant_id=TENANT,
        version=1,
        created_at="2026-10-16T10:00:00+00:00",
    )
    tenant = SimpleNamespace(tier=TenantTier.STARTER)
    return [
        event
        async for event in _pipeline(session).execute(
            TENANT,
            CONVERSATION,
            QUESTION,
            tenant=tenant,
            preferences=preferences,
        )
    ]


def _validated_message_id(events: list[Any]) -> str:
    [validated] = [event for event in events if event.event is StreamEventType.VALIDATED]
    return validated.data["message_id"]


async def test_the_pipeline_stores_the_score_on_the_validated_reply(session, container) -> None:
    events = await _run_pipeline(session)

    reply_id = _validated_message_id(events)
    messages = _by_id(container.doc)
    expected = quality_scorer.score_turn(
        ai_response=messages[reply_id]["content"],
        customer_message=QUESTION,
        knowledge_context="",
    ).model_dump()
    assert messages[reply_id]["metadata"]["quality_score"] == expected
    scored = [m["message_id"] for m in container.doc["messages"] if "quality_score" in (m.get("metadata") or {})]
    assert scored == [reply_id]


async def test_the_pipeline_logs_and_continues_when_the_score_cannot_be_stored(
    session,
    container,
    caplog,
) -> None:
    container.conflict_when = _carries_a_score

    with caplog.at_level(logging.WARNING, logger="src.chat.pipeline.orchestrator"):
        events = await _run_pipeline(session)

    kinds = [event.event for event in events]
    reply_id = _validated_message_id(events)
    assert kinds[-1] is StreamEventType.DONE
    assert StreamEventType.ERROR not in kinds
    assert reply_id in _by_id(container.doc)
    assert not _carries_a_score(container.doc)
    assert NON_FATAL_WARNING in caplog.text
