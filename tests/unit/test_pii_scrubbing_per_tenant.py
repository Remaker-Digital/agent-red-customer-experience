# tests/unit/test_pii_scrubbing_per_tenant.py: stored transcripts follow each tenant's own pii_scrubbing (review S19)
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
One ConversationSession serves every tenant. POST /api/chat/message stores the
customer message in its own request, before the SSE stream runs
ChatPipeline.execute(), and execute() used to switch PII scrubbing on or off
for the whole session at the start of each turn. So a tenant's transcript
followed whichever tenant's turn had run last in the process.

These tests drive the real send_message endpoint function, the real
ChatPipeline.execute() (agents, Critic and profile load stubbed) and the
session wired the way app startup wires it (configure_conversation_session),
over an in-memory conversation store and a stubbed preferences read.

A tenant's recorded setting is ordered by when the configuration it came from
was activated (activated_at), so a turn or read that saw an older
configuration never replaces a newer one.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.chat.session as session_module
from src.chat import endpoints as chat_endpoints
from src.chat.models import MessageRole, SendMessageRequest
from src.chat.pipeline import ChatPipeline
from src.chat.session import ConversationSession, configure_conversation_session
from src.multi_tenant.cosmos_schema import PreferencesDocument, TenantTier
from src.multi_tenant.repositories.preferences import PreferencesRepository
from src.multi_tenant.system_prompt_builder import AgentRole

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
CONV_A = "conv-a"
CONV_B = "conv-b"

EMAIL = "ana@example.com"
PHONE = "555-123-4567"
CUSTOMER_TEXT = f"Reach me at {EMAIL} or {PHONE}"
REPLY_TEXT = f"We will write to {EMAIL} and call {PHONE}."

# When configurations were activated, in order.
T1 = "2026-10-01T00:00:00+00:00"
T2 = "2026-10-02T09:00:00+00:00"
T3 = "2026-10-03T09:00:00+00:00"


# ---------------------------------------------------------------------------
# In-memory stand-ins
# ---------------------------------------------------------------------------


class _ConversationStore:
    """ConversationRepository stand-in: one document per (tenant, conversation)."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}

    def open(self, tenant_id: str, conversation_id: str) -> None:
        self.docs[(tenant_id, conversation_id)] = {
            "id": conversation_id,
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "status": "active",
            "messages": [],
            "message_count": 0,
            "turn_count": 0,
            "_etag": "0",
        }

    def stored(self, tenant_id: str, conversation_id: str, role: MessageRole) -> list[str]:
        messages = self.docs[(tenant_id, conversation_id)]["messages"]
        return [m["content"] for m in messages if m["role"] == role.value]

    async def read(self, tenant_id: str, conversation_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.docs[(tenant_id, conversation_id)])

    get = read

    async def replace_with_etag(self, tenant_id: str, conversation_id: str, doc: dict[str, Any], etag: str) -> None:
        self.docs[(tenant_id, conversation_id)] = copy.deepcopy(doc)

    async def append_message(self, tenant_id: str, conversation_id: str, message: dict[str, Any]) -> None:
        self.docs[(tenant_id, conversation_id)]["messages"].append(copy.deepcopy(message))

    async def append_message_with_metadata(
        self,
        tenant_id: str,
        conversation_id: str,
        message: dict[str, Any],
        metadata_updates: dict[str, Any] | None = None,
        *,
        count_turn: bool = False,
    ) -> None:
        doc = self.docs[(tenant_id, conversation_id)]
        doc["messages"].append(copy.deepcopy(message))
        doc["turn_count"] += int(count_turn)
        doc.update(metadata_updates or {})

    async def patch(self, *args: Any, **kwargs: Any) -> None:
        return None


def _prefs(tenant_id: str, pii_scrubbing: bool, *, activated_at: str | None = T1, version: int = 1) -> dict[str, Any]:
    """A tenant's active preferences document as stored; activated_at=None leaves the field out."""
    document = {
        "id": f"{tenant_id}:v{version}",
        "tenant_id": tenant_id,
        "version": version,
        "is_current": True,
        "created_at": T1,
        "activated_at": activated_at,
        "pii_scrubbing": pii_scrubbing,
    }
    if activated_at is None:
        del document["activated_at"]
    return document


class _World:
    """The app as startup wires it: one session for every tenant, used by the endpoints and the pipeline."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, settings: dict[str, bool]) -> None:
        self.store = _ConversationStore()
        self.preferences = {tenant: _prefs(tenant, on) for tenant, on in settings.items()}
        self.preference_reads: list[str] = []
        self.gates: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}
        self.read_gates: dict[str, tuple[asyncio.Event, asyncio.Event]] = {}

        async def get_current(_repo: Any, tenant_id: str) -> dict[str, Any] | None:
            self.preference_reads.append(tenant_id)
            found = copy.deepcopy(self.preferences.get(tenant_id))
            gate = self.read_gates.pop(tenant_id, None)
            if gate is not None:
                started, release = gate
                started.set()
                await release.wait()
            return found or None

        monkeypatch.setattr(PreferencesRepository, "get_current", get_current)
        monkeypatch.setattr(session_module, "_session", None)
        self.session = configure_conversation_session(conversation_repo=self.store)
        monkeypatch.setattr(chat_endpoints, "_session", self.session)
        self.pipeline = self._pipeline()

    def _pipeline(self) -> ChatPipeline:
        prompt_builder = MagicMock()
        prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
        prompt_builder.explain.return_value = {}
        pipeline = ChatPipeline(session=self.session, prompt_builder=prompt_builder, profile_service=AsyncMock())

        async def reply_stream(*args: Any, **kwargs: Any) -> Any:
            yield REPLY_TEXT

        async def approve(tenant_id: str, conversation_id: str, text: str, *args: Any, **kwargs: Any) -> Any:
            return True, text, SimpleNamespace(verdict=None, flags=[], latency_ms=1.0)

        async def load_profile(tenant_id: str, *args: Any, **kwargs: Any) -> None:
            gate = self.gates.get(tenant_id)
            if gate is not None:
                started, release = gate
                started.set()
                await release.wait()
            return None

        pipeline._call_intent_classifier = AsyncMock(return_value={"intent": "general_inquiry", "confidence": 0.95})
        pipeline._call_knowledge_retrieval = AsyncMock(return_value={"context": "", "sources": []})
        pipeline._call_response_generator_stream = reply_stream
        pipeline._validate_with_critic = approve
        pipeline._load_customer_profile = load_profile
        pipeline._fire_analytics = AsyncMock()
        return pipeline

    def gate(self, tenant_id: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Hold the tenant's next turn right after execute() has configured scrubbing for it."""
        self.gates[tenant_id] = (asyncio.Event(), asyncio.Event())
        return self.gates[tenant_id]

    def hold_read(self, tenant_id: str) -> tuple[asyncio.Event, asyncio.Event]:
        """Hold the tenant's next preferences read after it fetched the stored document, until released."""
        self.read_gates[tenant_id] = (asyncio.Event(), asyncio.Event())
        return self.read_gates[tenant_id]

    async def send(self, tenant_id: str, conversation_id: str, content: str = CUSTOMER_TEXT) -> None:
        """POST /api/chat/message, as the widget sends it."""
        request = SendMessageRequest(
            conversation_id=conversation_id,
            content=content,
            idempotency_key=str(uuid.uuid4()),
        )
        await chat_endpoints.send_message(request, ctx=SimpleNamespace(tenant_id=tenant_id))

    async def turn(self, tenant_id: str, conversation_id: str, loaded: dict[str, Any] | None = None) -> list[Any]:
        """The SSE stream's pipeline run, with the preferences the chat routes load for the tenant.

        ``loaded`` is a document the turn loaded earlier; by default it loads the stored one now.
        """
        tenant = MagicMock()
        tenant.tier = TenantTier.STARTER
        preferences = PreferencesDocument(**(loaded if loaded is not None else self.preferences[tenant_id]))
        return [
            event
            async for event in self.pipeline.execute(
                tenant_id, conversation_id, CUSTOMER_TEXT, tenant=tenant, preferences=preferences
            )
        ]


async def _until_held(started: asyncio.Event, turn: asyncio.Task) -> None:
    """Wait until the turn holds at its gate; fail instead of hanging if it ended first."""
    waiter = asyncio.create_task(started.wait())
    done, _ = await asyncio.wait({waiter, turn}, return_when=asyncio.FIRST_COMPLETED)
    if turn in done:
        waiter.cancel()
        turn.result()
        pytest.fail("the turn ended before reaching its gate")


async def _turn_records_during_a_read(world: _World, monkeypatch: pytest.MonkeyPatch, loaded: dict[str, Any]) -> None:
    """Store a customer message whose preferences read is in flight while a turn records ``loaded``.

    The cached setting, if any, has expired, so the message's write reads the
    stored document. The turn, which loaded ``loaded`` earlier, starts
    execute() during that read and holds after recording its setting. The read
    then returns, and the turn is released (its reply uses the cache again).
    """
    ttl = session_module._PII_PREFERENCE_TTL_SECONDS
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", 0.0)
    read_started, finish_read = world.hold_read(TENANT_A)
    message = asyncio.create_task(world.send(TENANT_A, CONV_A))
    await _until_held(read_started, message)
    turn_started, release_turn = world.gate(TENANT_A)
    turn = asyncio.create_task(world.turn(TENANT_A, CONV_A, loaded))
    await _until_held(turn_started, turn)
    finish_read.set()
    await message
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", ttl)
    release_turn.set()
    await turn


def _assert_scrubbed(content: str) -> None:
    assert EMAIL not in content
    assert PHONE not in content
    assert "[REDACTED:email]" in content
    assert "[REDACTED:phone]" in content


def _assert_raw(content: str) -> None:
    assert EMAIL in content
    assert PHONE in content
    assert "[REDACTED" not in content


def _assert_follows(content: str, scrubbing: bool) -> None:
    if scrubbing:
        _assert_scrubbed(content)
    else:
        _assert_raw(content)


BOTH_ORDERS = pytest.mark.parametrize(
    ("a_on", "b_on"),
    [(True, False), (False, True)],
    ids=["A-scrubs-B-does-not", "A-does-not-B-scrubs"],
)


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


@BOTH_ORDERS
async def test_customer_message_after_another_tenants_turn_follows_its_own_setting(
    monkeypatch: pytest.MonkeyPatch, a_on: bool, b_on: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: a_on, TENANT_B: b_on})
    world.store.open(TENANT_A, CONV_A)
    world.store.open(TENANT_B, CONV_B)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_B, CONV_B)

    [b_message] = world.store.stored(TENANT_B, CONV_B, MessageRole.CUSTOMER)
    _assert_follows(b_message, b_on)
    [a_reply] = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    _assert_follows(a_reply, a_on)


@BOTH_ORDERS
async def test_interleaved_turns_each_store_per_their_own_setting(
    monkeypatch: pytest.MonkeyPatch, a_on: bool, b_on: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: a_on, TENANT_B: b_on})
    world.store.open(TENANT_A, CONV_A)
    world.store.open(TENANT_B, CONV_B)
    a_started, a_release = world.gate(TENANT_A)
    b_started, b_release = world.gate(TENANT_B)

    # A's message and turn start; the turn holds after configuring scrubbing.
    await world.send(TENANT_A, CONV_A)
    a_turn = asyncio.create_task(world.turn(TENANT_A, CONV_A))
    await _until_held(a_started, a_turn)

    # B's message is stored and B's turn starts while A's turn is in flight.
    await world.send(TENANT_B, CONV_B)
    b_turn = asyncio.create_task(world.turn(TENANT_B, CONV_B))
    await _until_held(b_started, b_turn)

    # A's reply is stored after B's turn configured scrubbing, then B's.
    a_release.set()
    await a_turn
    b_release.set()
    await b_turn

    [a_message] = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    [a_reply] = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    [b_message] = world.store.stored(TENANT_B, CONV_B, MessageRole.CUSTOMER)
    [b_reply] = world.store.stored(TENANT_B, CONV_B, MessageRole.AI)
    # Written while the other tenant's turn was in flight:
    _assert_follows(a_reply, a_on)
    _assert_follows(b_message, b_on)
    # Written with no other tenant's turn starting in between:
    _assert_follows(a_message, a_on)
    _assert_follows(b_reply, b_on)


# ---------------------------------------------------------------------------
# A tenant's first message in a fresh process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scrubbing", [True, False], ids=["scrubbing-on", "scrubbing-off"])
async def test_first_customer_message_in_a_fresh_process_follows_the_stored_preference(
    monkeypatch: pytest.MonkeyPatch, scrubbing: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: scrubbing})
    world.store.open(TENANT_A, CONV_A)

    await world.send(TENANT_A, CONV_A)

    [message] = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_follows(message, scrubbing)


async def test_the_preference_is_read_once_and_then_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, {TENANT_A: True})
    world.store.open(TENANT_A, CONV_A)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    assert world.preference_reads == [TENANT_A]
    for message in world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER):
        _assert_scrubbed(message)


# ---------------------------------------------------------------------------
# Preference changes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("before", "after"), [(True, False), (False, True)], ids=["on-to-off", "off-to-on"])
async def test_a_preference_change_is_picked_up_by_the_next_turn(
    monkeypatch: pytest.MonkeyPatch, before: bool, after: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: before})
    world.store.open(TENANT_A, CONV_A)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    world.preferences[TENANT_A] = _prefs(TENANT_A, after)
    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    first_reply, second_reply = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    messages = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_follows(first_reply, before)
    _assert_follows(second_reply, after)
    _assert_follows(messages[-1], after)


async def test_a_cached_preference_is_read_again_once_it_is_older_than_the_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: False})
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)

    world.preferences[TENANT_A] = _prefs(TENANT_A, True)
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", 0.0)
    await world.send(TENANT_A, CONV_A)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_raw(first)
    _assert_scrubbed(second)
    assert world.preference_reads == [TENANT_A, TENANT_A]


# ---------------------------------------------------------------------------
# Session-level behaviour
# ---------------------------------------------------------------------------


def _session(store: _ConversationStore, resolver: Any = None) -> ConversationSession:
    if resolver is None:
        return ConversationSession(conversation_repo=store)
    return ConversationSession(conversation_repo=store, pii_scrubbing_resolver=resolver)


async def test_a_setting_recorded_for_one_tenant_never_applies_to_another() -> None:
    store = _ConversationStore()
    store.open(TENANT_A, CONV_A)
    store.open(TENANT_B, CONV_B)
    session = _session(store)

    session.set_pii_scrubber(True, tenant_id=TENANT_A)
    await session.add_ai_message(TENANT_B, CONV_B, REPLY_TEXT)
    await session.add_ai_message(TENANT_A, CONV_A, REPLY_TEXT)

    _assert_raw(store.stored(TENANT_B, CONV_B, MessageRole.AI)[0])
    _assert_scrubbed(store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])


def test_set_pii_scrubber_needs_the_tenant() -> None:
    session = _session(_ConversationStore())

    with pytest.raises(TypeError):
        session.set_pii_scrubber(True)  # type: ignore[call-arg]


async def test_a_failed_preference_read_with_nothing_known_scrubs_the_write() -> None:
    """The privacy-safe choice: the message is kept, without raw contact details."""
    store = _ConversationStore()
    store.open(TENANT_A, CONV_A)
    resolver = AsyncMock(side_effect=RuntimeError("preferences unavailable"))
    session = _session(store, resolver)

    await session.add_customer_message(TENANT_A, SendMessageRequest(conversation_id=CONV_A, content=CUSTOMER_TEXT))

    _assert_scrubbed(store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])
    assert session._pii_scrubbing == {}, "a guess is never recorded"


async def test_send_message_stores_the_message_scrubbed_when_the_preference_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _ConversationStore()
    store.open(TENANT_A, CONV_A)
    session = _session(store, AsyncMock(side_effect=RuntimeError("preferences unavailable")))
    monkeypatch.setattr(chat_endpoints, "_session", session)
    request = SendMessageRequest(conversation_id=CONV_A, content=CUSTOMER_TEXT, idempotency_key=str(uuid.uuid4()))

    await chat_endpoints.send_message(request, ctx=SimpleNamespace(tenant_id=TENANT_A))

    _assert_scrubbed(store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])


async def test_preferences_without_the_field_never_overwrite_a_known_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat routes' fallback document (preferences failed to load) has no pii_scrubbing field."""
    world = _World(monkeypatch, {TENANT_A: True})
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    world.preferences[TENANT_A].pop("pii_scrubbing")

    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    first_reply, second_reply = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    _assert_scrubbed(first_reply)
    _assert_scrubbed(second_reply)
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[-1])


async def test_a_failed_preference_read_keeps_the_last_known_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _ConversationStore()
    store.open(TENANT_A, CONV_A)
    resolver = AsyncMock(side_effect=RuntimeError("preferences unavailable"))
    session = _session(store, resolver)
    session.set_pii_scrubber(True, tenant_id=TENANT_A)
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", 0.0)

    await session.add_customer_message(TENANT_A, SendMessageRequest(conversation_id=CONV_A, content=CUSTOMER_TEXT))

    resolver.assert_awaited_once_with(TENANT_A)
    _assert_scrubbed(store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])


async def test_a_failed_read_uses_the_setting_recorded_while_it_was_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The last known value is the one recorded when the read fails, not when it started."""
    store = _ConversationStore()
    store.open(TENANT_A, CONV_A)
    read_started, fail_read = asyncio.Event(), asyncio.Event()

    async def failing_read(tenant_id: str) -> Any:
        read_started.set()
        await fail_read.wait()
        raise RuntimeError("preferences unavailable")

    session = _session(store, failing_read)
    session.set_pii_scrubber(False, tenant_id=TENANT_A, activated_at=T1)
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", 0.0)

    write = asyncio.create_task(session.add_ai_message(TENANT_A, CONV_A, REPLY_TEXT))
    await _until_held(read_started, write)
    session.set_pii_scrubber(True, tenant_id=TENANT_A, activated_at=T2)
    fail_read.set()
    await write

    _assert_scrubbed(store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])
    assert session._pii_scrubbing[TENANT_A].enabled is True


async def test_a_turn_with_a_newer_configuration_recorded_during_a_read_of_an_older_one_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: False})
    world.store.open(TENANT_A, CONV_A)

    # The read fetches the configuration activated at T1; the turn loaded the one activated at T2.
    await _turn_records_during_a_read(world, monkeypatch, _prefs(TENANT_A, True, activated_at=T2, version=2))

    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])


# ---------------------------------------------------------------------------
# Reading the preference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"pii_scrubbing": True}, True),
        ({"pii_scrubbing": False}, False),
        ({}, False),
        ({"pii_scrubbing": "true"}, False),
        ({"pii_scrubbing": 1}, False),
    ],
    ids=["true", "false", "missing", "string", "one"],
)
def test_only_a_stored_true_turns_scrubbing_on(stored: dict[str, Any], expected: bool) -> None:
    document = PreferencesDocument(
        id="t:v1", tenant_id="t", version=1, created_at="2026-10-01T00:00:00+00:00", **stored
    )

    assert session_module.pii_scrubbing_enabled(stored) is expected
    assert session_module.pii_scrubbing_enabled(document) is expected


async def test_a_tenant_without_an_active_configuration_has_scrubbing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    get_current = AsyncMock(return_value=None)
    monkeypatch.setattr(PreferencesRepository, "get_current", get_current)

    preference = await session_module.resolve_pii_scrubbing_preference(TENANT_A)

    assert (preference.enabled, preference.activated_at) == (False, None)
    get_current.assert_awaited_once_with(TENANT_A)


async def test_the_preference_read_carries_when_its_configuration_was_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_current = AsyncMock(return_value=_prefs(TENANT_A, True, activated_at=T2, version=2))
    monkeypatch.setattr(PreferencesRepository, "get_current", get_current)

    preference = await session_module.resolve_pii_scrubbing_preference(TENANT_A)

    assert (preference.enabled, preference.activated_at) == (True, T2)


@pytest.mark.parametrize(
    ("scrubbing", "assert_stored"), [(True, _assert_scrubbed), (False, _assert_raw)], ids=["on", "off"]
)
async def test_an_initial_message_follows_the_tenants_setting(scrubbing: bool, assert_stored: Any) -> None:
    """POST /api/chat/conversations with initial_message stores it like any customer message."""
    from src.chat.models import ConversationStartRequest

    repo = MagicMock()
    repo.create = AsyncMock()
    session = ConversationSession(conversation_repo=repo, pii_scrubbing_resolver=AsyncMock(return_value=scrubbing))
    session.set_pii_scrubber(not scrubbing, tenant_id=TENANT_B)

    await session.start_conversation(TENANT_A, ConversationStartRequest(initial_message=CUSTOMER_TEXT))

    _, created = repo.create.await_args.args
    assert_stored(created.messages[0]["content"])


# ---------------------------------------------------------------------------
# Settings are ordered by when their configuration was activated
# ---------------------------------------------------------------------------


async def test_a_stale_turn_recorded_during_a_refresh_never_replaces_the_newer_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P1 on #163: the turn loaded its preferences before the tenant turned scrubbing on."""
    world = _World(monkeypatch, {TENANT_A: False})
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    stale = copy.deepcopy(world.preferences[TENANT_A])
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at=T2, version=2)

    await _turn_records_during_a_read(world, monkeypatch, stale)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    first_reply, stale_reply = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    _assert_raw(first)
    _assert_raw(first_reply)
    _assert_scrubbed(second)
    _assert_scrubbed(stale_reply)
    assert world.session._pii_scrubbing[TENANT_A].enabled is True


async def test_a_stale_turn_recorded_after_a_read_of_a_newer_configuration_never_replaces_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: False})
    world.store.open(TENANT_A, CONV_A)
    stale = copy.deepcopy(world.preferences[TENANT_A])
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at=T2, version=2)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A, stale)
    await world.send(TENANT_A, CONV_A)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_scrubbed(first)
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])
    _assert_scrubbed(second)
    assert world.session._pii_scrubbing[TENANT_A].enabled is True


@pytest.mark.parametrize(("before", "after"), [(True, False), (False, True)], ids=["on-to-off", "off-to-on"])
async def test_a_turn_with_a_newer_configuration_replaces_the_cached_setting(
    monkeypatch: pytest.MonkeyPatch, before: bool, after: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: before})
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    world.preferences[TENANT_A] = _prefs(TENANT_A, after, activated_at=T2, version=2)

    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_follows(first, before)
    _assert_follows(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0], after)
    _assert_follows(second, after)
    assert world.preference_reads == [TENANT_A]


async def test_a_restored_configuration_is_newer_than_the_higher_version_it_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """restore_previous makes the previous document (a lower version) active again with a new activated_at."""
    world = _World(monkeypatch, {TENANT_A: False})
    world.preferences[TENANT_A] = _prefs(TENANT_A, False, activated_at=T2, version=3)
    world.store.open(TENANT_A, CONV_A)
    stale = copy.deepcopy(world.preferences[TENANT_A])
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at=T3, version=2)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A, stale)
    await world.send(TENANT_A, CONV_A)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_scrubbed(first)
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])
    _assert_scrubbed(second)


async def test_a_turn_with_a_restored_lower_version_replaces_the_higher_version_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: True})
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at=T2, version=3)
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    world.preferences[TENANT_A] = _prefs(TENANT_A, False, activated_at=T3, version=2)

    await world.turn(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    first, second = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _assert_scrubbed(first)
    _assert_raw(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])
    _assert_raw(second)


@pytest.mark.parametrize(
    ("demoted_on", "active_on"), [(False, True), (True, False)], ids=["active-scrubs", "demoted-scrubs"]
)
async def test_a_read_of_an_older_configuration_than_the_cached_one_takes_over_when_nothing_was_recorded(
    monkeypatch: pytest.MonkeyPatch, demoted_on: bool, active_on: bool
) -> None:
    """The cached configuration is no longer active, and the one get_current returns was activated earlier.

    Save named config activates v3 at T3 and leaves v2 (activated at T2)
    active too; restore_previous then demotes v3, so get_current returns v2.
    The first write after that scrubs if either says so; a turn of v2 then
    applies v2's setting, and the cache is fresh again.
    """
    world = _World(monkeypatch, {TENANT_A: demoted_on})
    world.preferences[TENANT_A] = _prefs(TENANT_A, demoted_on, activated_at=T3, version=3)
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A)
    world.preferences[TENANT_A] = _prefs(TENANT_A, active_on, activated_at=T2, version=2)
    ttl = session_module._PII_PREFERENCE_TTL_SECONDS

    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", 0.0)
    await world.send(TENANT_A, CONV_A)  # the cached v3 has expired: reads v2
    monkeypatch.setattr(session_module, "_PII_PREFERENCE_TTL_SECONDS", ttl)
    await world.turn(TENANT_A, CONV_A)  # a turn of v2
    await world.send(TENANT_A, CONV_A)

    _, restored, later = world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)
    _, reply = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    _assert_scrubbed(restored)
    _assert_follows(reply, active_on)
    _assert_follows(later, active_on)
    assert world.preference_reads == [TENANT_A, TENANT_A]


async def test_activation_times_are_compared_as_times_not_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    world = _World(monkeypatch, {TENANT_A: True})
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at="2026-10-02T09:00:00Z")
    world.store.open(TENANT_A, CONV_A)
    # 08:00 UTC, an hour older, although its text sorts after the newer value.
    older = _prefs(TENANT_A, False, activated_at="2026-10-02T10:00:00+02:00", version=0)

    await world.send(TENANT_A, CONV_A)
    await world.turn(TENANT_A, CONV_A, older)

    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-02T09:00:00Z", datetime(2026, 10, 2, 9, tzinfo=UTC)),
        ("2026-10-02T11:00:00.250000+02:00", datetime(2026, 10, 2, 9, 0, 0, 250000, tzinfo=UTC)),
        ("2026-10-02T09:00:00", datetime(2026, 10, 2, 9, tzinfo=UTC)),
        (datetime(2026, 10, 2, 9, tzinfo=UTC), datetime(2026, 10, 2, 9, tzinfo=UTC)),
        ("", None),
        ("not a time", None),
        (None, None),
        (17, None),
    ],
    ids=["zulu", "offset-and-fraction", "no-offset-is-utc", "datetime", "empty", "unreadable", "none", "number"],
)
def test_an_activation_time_is_read_as_an_aware_time_or_not_at_all(value: Any, expected: datetime | None) -> None:
    assert session_module._activation_time(value) == expected


# ---------------------------------------------------------------------------
# Settings that cannot be ordered: the privacy-safe choice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("read_on", "turn_on"), [(True, False), (False, True)], ids=["read-scrubs", "turn-scrubs"])
async def test_a_read_without_an_activation_time_and_a_turn_during_it_scrub_if_either_says_so(
    monkeypatch: pytest.MonkeyPatch, read_on: bool, turn_on: bool
) -> None:
    world = _World(monkeypatch, {TENANT_A: read_on})
    world.preferences[TENANT_A] = _prefs(TENANT_A, read_on, activated_at=None)
    world.store.open(TENANT_A, CONV_A)

    await _turn_records_during_a_read(world, monkeypatch, _prefs(TENANT_A, turn_on, activated_at=T2, version=2))

    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])
    assert world.session._pii_scrubbing[TENANT_A].enabled is True


async def test_a_record_without_an_activation_time_never_turns_off_a_known_configurations_scrubbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: True})
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)

    world.session.set_pii_scrubber(False, tenant_id=TENANT_A)
    await world.session.add_ai_message(TENANT_A, CONV_A, REPLY_TEXT)

    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.CUSTOMER)[0])
    _assert_scrubbed(world.store.stored(TENANT_A, CONV_A, MessageRole.AI)[0])


async def test_after_the_privacy_safe_choice_the_next_record_of_that_configuration_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World(monkeypatch, {TENANT_A: True})
    world.preferences[TENANT_A] = _prefs(TENANT_A, True, activated_at=None)
    world.store.open(TENANT_A, CONV_A)
    await world.send(TENANT_A, CONV_A)
    world.preferences[TENANT_A] = _prefs(TENANT_A, False, activated_at=T2, version=2)

    await world.turn(TENANT_A, CONV_A)  # cannot be ordered against the cached value: still scrubbed
    await world.turn(TENANT_A, CONV_A)  # the same configuration again: its own setting applies

    first_reply, second_reply = world.store.stored(TENANT_A, CONV_A, MessageRole.AI)
    _assert_scrubbed(first_reply)
    _assert_raw(second_reply)
