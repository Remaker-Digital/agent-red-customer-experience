# tests/unit/test_orchestrator_customer_name.py: S22, the stored profile name leaves the model text
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""Decision 16. ChatPipeline.execute() is the path under test.

The profile stores the name on asserted_identity, not on customer_name or
first_name. The text handed to the intent classifier and the response
generator must not contain that name. A missing profile, or a profile whose
asserted_identity has no name key, still completes the turn.
"""

from __future__ import annotations

import asyncio
import unicodedata
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.cosmos_schema import CustomerProfileDocument, TenantTier
from src.multi_tenant.system_prompt_builder import AgentRole

STORED_NAME = "Ada Lovelace"
CUSTOMER_MESSAGE = "Please help Ada Lovelace with an order."


def _profile(identity: dict) -> CustomerProfileDocument:
    return CustomerProfileDocument(
        id="tenant-a:customer-a",
        tenant_id="tenant-a",
        customer_id="customer-a",
        created_at="2026-11-02T00:00:00+00:00",
        updated_at="2026-11-02T00:00:00+00:00",
        asserted_identity=identity,
    )


def _pipeline(profile: CustomerProfileDocument | None) -> tuple[ChatPipeline, dict[str, str]]:
    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    session = MagicMock()
    session.add_ai_message = AsyncMock(return_value="message-1")
    session.patch_message_metadata = AsyncMock()
    session.update_conversation_metadata = AsyncMock()
    session.get_conversation = AsyncMock(return_value=SimpleNamespace(turn_count=1))
    profile_service = AsyncMock()
    profile_service.get_profile = AsyncMock(return_value=profile)
    pipeline = ChatPipeline(session=session, prompt_builder=prompt_builder, profile_service=profile_service)
    seen: dict[str, str] = {}

    async def classify(message: str, *args, **kwargs):
        seen["intent"] = message
        return {"intent": "general_inquiry", "confidence": 0.95}

    async def retrieve(message: str, *args, **kwargs):
        seen["knowledge"] = message
        return {"context": "", "sources": []}

    async def reply_stream(*args, **kwargs):
        seen["response"] = kwargs["customer_message"]
        yield "Your order is on the way."

    async def approve(tenant_id: str, conversation_id: str, text: str, *args, **kwargs):
        return True, text, SimpleNamespace(verdict=None, flags=[], latency_ms=1.0)

    pipeline._call_intent_classifier = classify
    pipeline._call_knowledge_retrieval = retrieve
    pipeline._call_response_generator_stream = reply_stream
    pipeline._validate_with_critic = approve
    pipeline._fire_analytics = AsyncMock()
    return pipeline, seen


async def _run(profile: CustomerProfileDocument | None, message: str) -> dict[str, str]:
    pipeline, seen = _pipeline(profile)
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    preferences = SimpleNamespace(
        fine_tuning_enabled=False,
        cite_sources_in_response=False,
        structured_blocks_enabled=False,
        primary_language="en",
    )
    _events = [
        event
        async for event in pipeline.execute(
            "tenant-a",
            "conversation-a",
            message,
            tenant=tenant,
            preferences=preferences,
            customer_id="customer-a",
        )
    ]
    return seen


@pytest.mark.asyncio
async def test_stored_profile_name_is_absent_from_outbound_model_text() -> None:
    """S22 through ChatPipeline.execute. The stored name is not sent in the clear."""
    seen = await _run(_profile({"name": STORED_NAME}), CUSTOMER_MESSAGE)

    for stage in ("intent", "knowledge", "response"):
        assert STORED_NAME not in seen[stage]
        assert "pii-name-" in seen[stage]


@pytest.mark.asyncio
async def test_missing_profile_completes_without_error() -> None:
    """GC-3. No profile means an empty name list, and the turn still finishes."""
    seen = await _run(None, "Hello there")

    assert seen["intent"] == "Hello there"


@pytest.mark.asyncio
async def test_missing_name_key_completes_without_error() -> None:
    """GC-3. asserted_identity without a name key means an empty name list."""
    seen = await _run(_profile({"email": "ada@example.com"}), CUSTOMER_MESSAGE)

    assert seen["intent"] == CUSTOMER_MESSAGE


class _MemoryProfiles:
    """The profile the pipeline loads, and the one a store would persist."""

    def __init__(self, stored: CustomerProfileDocument | None = None) -> None:
        self.stored = stored

    async def get_profile(self, tenant_id: str, customer_id: str) -> CustomerProfileDocument | None:
        return self.stored

    async def extract_and_store_identity(
        self, tenant_id: str, customer_id: str, message_text: str
    ) -> dict[str, str] | None:
        from src.multi_tenant.customer_profile_service import extract_identity_from_text

        extracted = extract_identity_from_text(message_text)
        name = extracted.get("name")
        if not name:
            return None
        existing = dict(self.stored.asserted_identity) if self.stored is not None else {}
        current = existing.get("name") or ""
        if current and len(name) <= len(current):
            return None
        existing["name"] = name
        if self.stored is None:
            self.stored = _profile(existing)
        else:
            self.stored.asserted_identity = existing
        return {"name": name}


def _talking_pipeline(profiles: _MemoryProfiles) -> tuple[ChatPipeline, list[dict[str, str]]]:
    pipeline, _seen = _pipeline(None)
    pipeline._profile_service = profiles
    turns: list[dict[str, str]] = []

    async def classify(message: str, *args, **kwargs):
        turns.append({"intent": message})
        return {"intent": "general_inquiry", "confidence": 0.95}

    async def retrieve(message: str, *args, **kwargs):
        turns[-1]["knowledge"] = message
        return {"context": "", "sources": []}

    async def reply_stream(*args, **kwargs):
        turns[-1]["response"] = kwargs["customer_message"]
        history = kwargs.get("conversation_history") or []
        turns[-1]["history"] = "\n".join(
            f"{item.get('role')}: {item.get('content')}" for item in history
        )
        yield "Your order is on the way."

    pipeline._call_intent_classifier = classify
    pipeline._call_knowledge_retrieval = retrieve
    pipeline._call_response_generator_stream = reply_stream
    return pipeline, turns


async def _two_turns(
    pipeline: ChatPipeline,
    messages: list[str],
    histories: list[list[dict[str, str]] | None] | None = None,
) -> None:
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    preferences = SimpleNamespace(
        fine_tuning_enabled=False,
        cite_sources_in_response=False,
        structured_blocks_enabled=False,
        primary_language="en",
    )
    for index, message in enumerate(messages):
        history = None if histories is None else histories[index]
        _events = [
            event
            async for event in pipeline.execute(
                "tenant-a",
                "conversation-a",
                message,
                tenant=tenant,
                preferences=preferences,
                customer_id="customer-a",
                conversation_history=history,
            )
        ]


def _assert_name_tokenized(turn: dict[str, str]) -> None:
    for stage in ("intent", "knowledge", "response"):
        assert STORED_NAME not in turn[stage]
        assert "pii-name-" in turn[stage]


@pytest.mark.asyncio
async def test_a_new_customers_name_is_absent_on_turn_1_and_turn_2() -> None:
    """Decision 17. One pipeline. Turn 1 states the name. Turn 2 uses it in a plain sentence."""
    pipeline, turns = _talking_pipeline(_MemoryProfiles())
    await _two_turns(
        pipeline,
        [
            "my name is Ada Lovelace",
            "Please help Ada Lovelace with an order.",
        ],
        [
            None,
            [
                {"role": "user", "content": "my name is Ada Lovelace"},
                {"role": "assistant", "content": "Hello Ada Lovelace"},
            ],
        ],
    )

    assert len(turns) == 2
    _assert_name_tokenized(turns[0])
    _assert_name_tokenized(turns[1])
    assert "user: " in turns[1]["history"]
    assert "assistant: " in turns[1]["history"]
    assert STORED_NAME not in turns[1]["history"]
    assert "pii-name-" in turns[1]["history"]


@pytest.mark.asyncio
async def test_a_repeated_name_is_tokenized_when_the_store_does_not_change() -> None:
    """The store returns None when the name is already stored. The message still has it."""
    profiles = _MemoryProfiles(_profile({"name": STORED_NAME}))
    pipeline, turns = _talking_pipeline(profiles)
    pipeline._profile_cache["tenant-a:customer-a"] = _profile({})
    await _two_turns(pipeline, ["my name is Ada Lovelace"])

    _assert_name_tokenized(turns[0])


@pytest.mark.asyncio
async def test_an_ordinary_this_is_sentence_is_not_tokenized_as_a_name() -> None:
    """A support sentence is not a name. The complaint stays in the model text."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["This is not working."])

    assert turns[0]["intent"] == "This is not working."
    assert "pii-name-" not in turns[0]["intent"]
    assert profiles.stored is None


def _assert_lowercase_name_tokenized(turn: dict[str, str]) -> None:
    for stage in ("intent", "knowledge", "response"):
        assert "ada lovelace" not in turn[stage]
        assert "pii-name-" in turn[stage]


@pytest.mark.asyncio
async def test_a_lowercase_my_name_is_absent_on_this_turn_and_the_next() -> None:
    """Decision 20. 'my name is' still accepts a lowercase name, on this turn and the next."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(
        pipeline,
        [
            "my name is ada lovelace",
            "Please help ada lovelace with an order.",
        ],
    )

    assert len(turns) == 2
    _assert_lowercase_name_tokenized(turns[0])
    _assert_lowercase_name_tokenized(turns[1])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "ada lovelace"


def _assert_exact_name_tokenized(turn: dict[str, str], name: str) -> None:
    for stage in ("intent", "knowledge", "response"):
        assert name not in turn[stage]
        assert "pii-name-" in turn[stage]


@pytest.mark.asyncio
async def test_a_product_name_sentence_is_not_stored_as_a_customer_name() -> None:
    """Decision 21. 'the product name is wrong' stores no name and reaches the model unchanged."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["the product name is wrong"])

    assert turns[0]["intent"] == "the product name is wrong"
    assert "pii-name-" not in turns[0]["intent"]
    assert profiles.stored is None


@pytest.mark.asyncio
async def test_a_capitalized_name_label_is_absent_from_model_text() -> None:
    """Decision 21. 'Name: Ada Lovelace' is stored and does not reach the model."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["Name: Ada Lovelace"])

    _assert_exact_name_tokenized(turns[0], "Ada Lovelace")
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada Lovelace"


@pytest.mark.asyncio
async def test_call_me_tomorrow_is_not_stored_as_a_name() -> None:
    """Decision 22. 'please call me tomorrow' stores no name and reaches the model unchanged."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["please call me tomorrow"])

    assert turns[0]["intent"] == "please call me tomorrow"
    assert "pii-name-" not in turns[0]["intent"]
    assert profiles.stored is None


@pytest.mark.asyncio
async def test_call_me_ada_is_absent_from_model_text() -> None:
    """Decision 22. 'Call me Ada' is stored and does not reach the model."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["Call me Ada"])

    _assert_exact_name_tokenized(turns[0], "Ada")
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"


ACCENTED_NAME = "Jos\u00e9 Garc\u00eda"


def _assert_accented_name_whole(turn: dict[str, str]) -> None:
    for stage in ("intent", "knowledge", "response"):
        assert ACCENTED_NAME not in turn[stage]
        assert "\u00e9 Garc\u00eda" not in turn[stage]
        assert "pii-name-" in turn[stage]


@pytest.mark.asyncio
async def test_an_accented_my_name_is_absent_from_model_text() -> None:
    """Decision 19. 'my name is José García' is absent, with no leftover accented part."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {ACCENTED_NAME}"])

    _assert_accented_name_whole(turns[0])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == ACCENTED_NAME


@pytest.mark.asyncio
async def test_an_accented_name_label_is_stored_whole() -> None:
    """Decision 19. 'Name: José García' is captured whole and does not reach the model."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"Name: {ACCENTED_NAME}"])

    _assert_accented_name_whole(turns[0])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == ACCENTED_NAME


def _assert_extended_name(turn: dict[str, str], name: str, leftover: str) -> None:
    for stage in ("intent", "knowledge", "response"):
        assert name not in turn[stage]
        assert leftover not in turn[stage]
        assert "pii-name-" in turn[stage]


@pytest.mark.asyncio
async def test_erdogan_yilmaz_is_extracted_whole() -> None:
    """Decision 19. Latin Extended-A letters stay with the name."""
    name = "Erdo\u011fan Y\u0131lmaz"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    _assert_extended_name(turns[0], name, "\u011fan")
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name


@pytest.mark.asyncio
async def test_nguyen_van_is_extracted_whole() -> None:
    """Decision 19. Latin Extended Additional letters stay with the name."""
    name = "Nguy\u1ec5n V\u0103n"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    _assert_extended_name(turns[0], name, "\u1ec5n")
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name


@pytest.mark.asyncio
async def test_lukasz_walesa_is_extracted_whole() -> None:
    """Decision 19. A name that starts in Latin Extended-A is extracted."""
    name = "\u0141ukasz Wa\u0142\u0119sa"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    _assert_extended_name(turns[0], name, name)
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name


@pytest.mark.asyncio
async def test_a_name_label_and_this_is_keep_extended_latin() -> None:
    """Decision 19. 'Name:' and 'this is' keep letters past U+00FF."""
    labeled = "Erdo\u011fan"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"Name: {labeled}"])
    _assert_extended_name(turns[0], labeled, labeled)
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == labeled

    spoken = "Dvo\u0159\u00e1k"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"this is {spoken}"])
    _assert_extended_name(turns[0], spoken, spoken)
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == spoken


@pytest.mark.asyncio
async def test_a_decomposed_accented_name_is_extracted_whole() -> None:
    """Decision 19. Combining marks stay with the letters they follow."""
    name = unicodedata.normalize("NFD", "Jos\u00e9 Garc\u00eda")
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    _assert_extended_name(turns[0], name, "Garc")
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name


@pytest.mark.asyncio
async def test_a_title_phrase_keeps_a_final_combining_mark() -> None:
    """I'm, this is, call me, and Name: keep a mark on the last letter."""
    name = unicodedata.normalize("NFD", "Jos\u00e9")
    assert name.endswith("\u0301")
    for message in (f"I'm {name}", f"this is {name}", f"call me {name}", f"Name: {name}"):
        profiles = _MemoryProfiles()
        pipeline, turns = _talking_pipeline(profiles)
        await _two_turns(pipeline, [message])
        assert profiles.stored is not None
        assert profiles.stored.asserted_identity.get("name") == name
        assert name not in turns[0]["intent"]


@pytest.mark.asyncio
async def test_a_one_letter_word_is_not_part_of_the_stored_name() -> None:
    """'My name is Ada I need help' stores Ada. The next turn still tokenizes Ada."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(
        pipeline,
        [
            "My name is Ada I need help with my order",
            "Please help Ada with an order.",
        ],
    )

    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"
    assert "I need help" in turns[0]["intent"]
    assert "Ada" not in turns[1]["intent"]
    assert "pii-name-" in turns[1]["intent"]


@pytest.mark.asyncio
async def test_an_internal_capital_is_not_stored_as_a_short_prefix() -> None:
    """Name: McDonald stores McDonald. It does not store the prefix Mc."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["Name: McDonald"])

    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "McDonald"
    assert "McDonald" not in turns[0]["intent"]
    assert "pii-name-" in turns[0]["intent"]


@pytest.mark.asyncio
async def test_an_apostrophe_name_is_stored_whole() -> None:
    """Decision 29. Mary O'Brien is stored with the apostrophe."""
    name = "Mary O'Brien"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name
    assert name not in turns[0]["intent"]
    assert "pii-name-" in turns[0]["intent"]


@pytest.mark.asyncio
async def test_a_hyphenated_name_is_stored_whole() -> None:
    """Decision 29. Jean-Luc Picard is stored with the hyphen."""
    name = "Jean-Luc Picard"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name
    assert name not in turns[0]["intent"]


@pytest.mark.asyncio
async def test_a_three_word_name_is_stored_whole() -> None:
    """Decision 29. Three name words are stored."""
    name = "Ada King Lovelace"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name
    assert "King" not in turns[0]["intent"]


@pytest.mark.asyncio
async def test_mcdonald_is_stored_whole() -> None:
    """Decision 29. Name: McDonald stores McDonald, not a short prefix."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["Name: McDonald"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "McDonald"
    assert "McDonald" not in turns[0]["intent"]
    assert "pii-name-" in turns[0]["intent"]


@pytest.mark.asyncio
async def test_a_connecting_word_is_not_stored_as_a_surname() -> None:
    """A later and or from is not part of the name, so it stays readable."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(
        pipeline,
        ["my name is Ada and I need help", "Please help and send the order."],
    )
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"
    assert "and" in turns[0]["intent"]
    assert "and" in turns[1]["intent"]
    assert "pii-name-" not in turns[1]["intent"]

    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["my name is Ada from Boston"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"


@pytest.mark.asyncio
async def test_an_ordinary_word_does_not_extend_the_name() -> None:
    """A sentence after the name is not stored, and a false name stores nothing."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(
        pipeline,
        ["my name is Ada my order is late", "The order is still late."],
    )
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"
    assert "order" in turns[0]["intent"]
    assert "order" in turns[1]["intent"]
    assert "pii-name-" not in turns[1]["intent"]

    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["my name is wrong on the invoice"])
    assert profiles.stored is None
    assert "wrong" in turns[0]["intent"]
    assert "pii-name-" not in turns[0]["intent"]


@pytest.mark.asyncio
async def test_a_common_word_that_is_a_real_name_is_stored() -> None:
    """Will and An stay part of a name. A following stop word still ends it."""
    samples = (
        ("my name is will smith", "will smith"),
        ("my name is an nguyen", "an nguyen"),
        ("My name is Jane Will", "Jane Will"),
        ("My name is James An", "James An"),
    )
    for message, name in samples:
        profiles = _MemoryProfiles()
        pipeline, turns = _talking_pipeline(profiles)
        await _two_turns(pipeline, [message])
        assert profiles.stored is not None
        assert profiles.stored.asserted_identity.get("name") == name
        assert name not in turns[0]["intent"]

    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(
        pipeline,
        ["my name is Ada will you help", "I will wait."],
    )
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada"
    assert "will" in turns[1]["intent"]
    assert "pii-name-" not in turns[1]["intent"]


@pytest.mark.asyncio
async def test_a_contraction_does_not_extend_the_name() -> None:
    """I'm, I'd, and I'll, straight or curly, are not part of the name."""
    messages = (
        "My name is Ada I'm looking for an order",
        "My name is Ada I'd like help",
        "My name is Ada I'll wait",
        "My name is Ada I\u2019m looking for an order",
        "My name is Ada I\u2019d like help",
        "My name is Ada I\u2019ll wait",
    )
    for message in messages:
        profiles = _MemoryProfiles()
        pipeline, turns = _talking_pipeline(profiles)
        await _two_turns(pipeline, [message, "I am still looking for it."])
        assert profiles.stored is not None
        assert profiles.stored.asserted_identity.get("name") == "Ada"
        assert "looking" in turns[1]["intent"]
        assert "pii-name-" not in turns[1]["intent"]


@pytest.mark.asyncio
async def test_a_curly_apostrophe_is_understood() -> None:
    """A phone apostrophe works in I'm and inside O'Brien."""
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, ["I\u2019m Ada Lovelace"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == "Ada Lovelace"
    assert "Ada Lovelace" not in turns[0]["intent"]

    name = "Mary O\u2019Brien"
    profiles = _MemoryProfiles()
    pipeline, turns = _talking_pipeline(profiles)
    await _two_turns(pipeline, [f"my name is {name}"])
    assert profiles.stored is not None
    assert profiles.stored.asserted_identity.get("name") == name
    assert name not in turns[0]["intent"]


def _preferences() -> SimpleNamespace:
    return SimpleNamespace(
        fine_tuning_enabled=False,
        cite_sources_in_response=False,
        structured_blocks_enabled=False,
        primary_language="en",
    )


def _tenant() -> MagicMock:
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    return tenant


@pytest.mark.asyncio
async def test_history_sent_to_the_model_has_no_clear_pii_and_the_reply_is_restored() -> None:
    """Decision 23. Earlier turns are tokenized. A repeated token is restored for the customer."""
    profiles = _MemoryProfiles(_profile({"name": STORED_NAME}))
    pipeline, turns = _talking_pipeline(profiles)
    seen: dict[str, str] = {}

    async def reply_stream(*args, **kwargs):
        history = kwargs.get("conversation_history") or []
        history_text = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in history)
        turns[-1]["history"] = history_text
        turns[-1]["response"] = kwargs["customer_message"]
        token = ""
        for piece in history_text.replace(",", " ").split():
            if piece.startswith("pii-name-"):
                token = piece
                break
        yield f"Thanks, {token or STORED_NAME}"

    async def approve(tenant_id: str, conversation_id: str, text: str, *args, **kwargs):
        seen["critic"] = text
        return True, text, SimpleNamespace(verdict=None, flags=[], latency_ms=1.0)

    pipeline._call_response_generator_stream = reply_stream
    pipeline._validate_with_critic = approve
    history = [
        {
            "role": "user",
            "content": "I am Ada Lovelace, email ada@example.com, phone 415-555-0134, order #12345678",
        },
        {"role": "assistant", "content": "Hello Ada Lovelace, I see order #12345678"},
    ]
    kept = [dict(item) for item in history]
    events = [
        event
        async for event in pipeline.execute(
            "tenant-a",
            "conversation-a",
            "Where is my order?",
            tenant=_tenant(),
            preferences=_preferences(),
            customer_id="customer-a",
            conversation_history=history,
        )
    ]

    assert history == kept
    model_history = turns[0]["history"]
    for clear in (STORED_NAME, "ada@example.com", "415-555-0134", "#12345678"):
        assert clear not in model_history
    assert "user: " in model_history
    assert "assistant: " in model_history
    assert "pii-name-" in model_history
    assert "pii-email-" in model_history
    assert "pii-phone-" in model_history
    assert "pii-order_number-" in model_history

    shown = "".join(event.data.get("text", "") for event in events if event.event.value == "token")
    stored = pipeline._session.add_ai_message.await_args.kwargs["content"]
    assert STORED_NAME in shown
    assert "pii-name-" not in shown
    assert STORED_NAME in stored
    assert "pii-name-" not in stored
    assert STORED_NAME not in seen["critic"]
    assert "pii-name-" in seen["critic"]


@pytest.mark.asyncio
async def test_an_escalating_turn_sends_no_clear_name_and_the_email_summary_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 25. The escalation model sees a token. The staff email shows the name."""
    pipeline, _turns = _pipeline(_profile({"name": STORED_NAME}))
    customer_text = "Please help Ada Lovelace"
    pipeline._session._get_conversation = AsyncMock(
        return_value={
            "identity_email": "ada@example.com",
            "messages": [{"role": "user", "content": customer_text}],
            "escalation_sent": False,
        }
    )
    pipeline._session.find_best_agent_for_category = AsyncMock(return_value=None)
    pipeline._session.find_superadmin_email = AsyncMock(return_value="agent@example.com")
    pipeline._session.escalate_conversation = AsyncMock()
    pipeline._session.patch_conversation = AsyncMock()

    seen: dict[str, str] = {}

    async def classify(message: str, *args, **kwargs):
        return {"intent": "escalation", "confidence": 0.95}

    async def handler(message: str, system_prompt: str) -> dict[str, str]:
        seen["model"] = message
        token = ""
        for piece in message.split():
            if piece.startswith("pii-name-"):
                token = piece
                break
        echoed = token or STORED_NAME
        return {
            "reason": f"Needs help about {echoed}",
            "urgency": "medium",
            "context_summary": f"Summary {echoed}",
            "category": "support",
            "model": "fake",
        }

    pipeline._call_intent_classifier = classify
    pipeline._call_escalation_handler = handler
    sent: dict[str, object] = {}

    async def send_emails(**kwargs: object) -> dict[str, str]:
        sent["email"] = kwargs
        return {"customer": "sent", "agent": "sent"}

    async def send_alert(**kwargs: object) -> None:
        sent["alert"] = kwargs

    monkeypatch.setattr("src.chat.escalation_email.send_escalation_emails", send_emails)
    monkeypatch.setattr("src.multi_tenant.alert_delivery.send_escalation_alert", send_alert)

    _events = [
        event
        async for event in pipeline.execute(
            "tenant-a",
            "conversation-a",
            customer_text,
            tenant=_tenant(),
            preferences=_preferences(),
            customer_id="customer-a",
        )
    ]

    assert STORED_NAME not in seen["model"]
    assert "pii-name-" in seen["model"]
    email = sent["email"]
    assert isinstance(email, dict)
    assert STORED_NAME in str(email["reason"])
    assert "pii-name-" not in str(email["reason"])
    transcript = email["messages"]
    assert isinstance(transcript, list)
    assert STORED_NAME in transcript[0]["content"]
    await asyncio.sleep(0)
    stored_reason = pipeline._session.escalate_conversation.await_args.kwargs["escalation_reason"]
    assert STORED_NAME in stored_reason
    assert "pii-name-" not in stored_reason
    alert = sent["alert"]
    assert isinstance(alert, dict)
    assert STORED_NAME in str(alert["context_summary"])
    assert "pii-name-" not in str(alert["context_summary"])


def _shown_text(events: list) -> str:
    return "".join(event.data.get("text", "") for event in events if event.event.value == "token")


def _name_token(history_text: str) -> str:
    for piece in history_text.replace(",", " ").split():
        if piece.startswith("pii-name-"):
            return piece[: len("pii-name-") + 12]
    raise AssertionError(history_text)


async def _events_for(pipeline: ChatPipeline, message: str, history: list[dict] | None) -> list:
    return [
        event
        async for event in pipeline.execute(
            "tenant-a",
            "conversation-a",
            message,
            tenant=_tenant(),
            preferences=_preferences(),
            customer_id="customer-a",
            conversation_history=history,
        )
    ]


@pytest.mark.asyncio
async def test_a_token_split_after_a_letter_is_restored_whole() -> None:
    """A placeholder may start immediately after a letter and still be split across chunks."""
    pipeline, _turns = _talking_pipeline(_MemoryProfiles(_profile({"name": STORED_NAME})))

    async def reply_stream(*args, **kwargs):
        history = kwargs.get("conversation_history") or []
        history_text = "\n".join(item.get("content", "") for item in history)
        token = _name_token(history_text)
        yield "Dear" + token[0]
        yield token[1:]

    pipeline._call_response_generator_stream = reply_stream
    events = await _events_for(
        pipeline,
        "Where is my order?",
        [{"role": "user", "content": "I am Ada Lovelace"}],
    )

    shown = _shown_text(events)
    assert shown == "DearAda Lovelace"
    assert "Dearp" not in shown


@pytest.mark.asyncio
async def test_a_word_ending_in_p_is_still_shown() -> None:
    """Holding a trailing p does not drop the last letter of an ordinary word."""
    pipeline, _turns = _talking_pipeline(_MemoryProfiles())

    async def reply_stream(*args, **kwargs):
        yield "help"

    pipeline._call_response_generator_stream = reply_stream
    events = await _events_for(pipeline, "Hello there", [])

    assert _shown_text(events) == "help"


@pytest.mark.asyncio
async def test_an_incomplete_token_is_not_shown_until_it_finishes() -> None:
    """The customer does not see a partial placeholder."""
    pipeline, _turns = _talking_pipeline(_MemoryProfiles(_profile({"name": STORED_NAME})))

    async def reply_stream(*args, **kwargs):
        history = kwargs.get("conversation_history") or []
        history_text = "\n".join(item.get("content", "") for item in history)
        token = _name_token(history_text)
        yield "Hello " + token[:6]
        yield token[6:]

    pipeline._call_response_generator_stream = reply_stream
    events = await _events_for(
        pipeline,
        "Where is my order?",
        [{"role": "user", "content": "I am Ada Lovelace"}, {"role": "assistant"}],
    )

    assert _shown_text(events) == "Hello Ada Lovelace"
