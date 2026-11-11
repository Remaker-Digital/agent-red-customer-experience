# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""An escalating turn tells the reply step that a handoff is under way.

The assertion is the system prompt the response generator receives, not the
words a model might write.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.cosmos_schema import TenantTier
from src.multi_tenant.system_prompt_builder import AgentRole


def _pipeline(conversation: dict) -> tuple[ChatPipeline, dict[str, str]]:
    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    session = MagicMock()
    session.add_ai_message = AsyncMock(return_value="message-1")
    session.patch_message_metadata = AsyncMock()
    session.update_conversation_metadata = AsyncMock()
    session.get_conversation = AsyncMock(return_value=SimpleNamespace(turn_count=1))
    session._get_conversation = AsyncMock(return_value=conversation)
    profile_service = AsyncMock()
    profile_service.get_profile = AsyncMock(return_value=None)
    pipeline = ChatPipeline(session=session, prompt_builder=prompt_builder, profile_service=profile_service)
    seen: dict[str, str] = {}

    async def retrieve(message: str, *args, **kwargs):
        return {"context": "", "sources": []}

    async def reply_stream(*args, **kwargs):
        seen["system_prompt"] = kwargs["system_prompt"]
        yield "Your order is on the way."

    async def approve(tenant_id: str, conversation_id: str, text: str, *args, **kwargs):
        return True, text, SimpleNamespace(verdict=None, flags=[], latency_ms=1.0)

    async def side_effects(*args, **kwargs):
        return {
            "email_required": False,
            "email_prompt": "",
            "escalation_msg": "A person will follow up.",
            "continuation_msg": "Is there anything else?",
        }

    pipeline._call_knowledge_retrieval = retrieve
    pipeline._call_response_generator_stream = reply_stream
    pipeline._validate_with_critic = approve
    pipeline._run_escalation_side_effects = side_effects
    pipeline._fire_analytics = AsyncMock()
    return pipeline, seen


async def _prompt(conversation: dict, intent: str) -> str:
    pipeline, seen = _pipeline(conversation)

    async def classify(message: str, *args, **kwargs):
        return {"intent": intent, "confidence": 0.95}

    pipeline._call_intent_classifier = classify
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
            "I need a person",
            tenant=tenant,
            preferences=preferences,
        )
    ]
    return seen["system_prompt"]


@pytest.mark.asyncio
async def test_escalating_turn_with_email_tells_the_reply_step_about_the_handoff() -> None:
    prompt = await _prompt({"escalation_sent": False, "identity_email": "ada@example.com"}, "escalation")
    assert "handed to a person" in prompt
    assert "hear back by email" in prompt
    assert "cannot connect the customer to a person" in prompt


@pytest.mark.asyncio
async def test_escalating_turn_with_a_verified_phone_names_phone() -> None:
    prompt = await _prompt(
        {"escalation_sent": False, "identity_phone": "+15551212", "phone_verified": True},
        "escalation",
    )
    assert "hear back by phone" in prompt
    assert "hear back by email" not in prompt


@pytest.mark.asyncio
async def test_escalating_turn_without_a_contact_asks_for_one() -> None:
    prompt = await _prompt({"escalation_sent": False}, "escalation")
    assert "email address or a phone number" in prompt
    assert "hear back by email" not in prompt


@pytest.mark.asyncio
async def test_a_normal_turn_does_not_add_the_handoff_note() -> None:
    prompt = await _prompt({"escalation_sent": False, "identity_email": "ada@example.com"}, "general_inquiry")
    assert prompt == "prompt"


@pytest.mark.asyncio
async def test_an_already_escalated_turn_does_not_add_the_handoff_note() -> None:
    prompt = await _prompt({"escalation_sent": True, "identity_email": "ada@example.com"}, "escalation")
    assert prompt == "prompt"
