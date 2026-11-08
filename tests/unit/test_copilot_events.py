# tests/unit/test_copilot_events.py: co-pilot turns emit validated and done events with the right arguments
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
_handle_co_pilot called ``validated_event(response, trace_id=...)`` and
``done_event(trace_id=...)``. Neither call matches the factories in src/chat/models.py
(``validated_event(conversation_id, message_id)`` and
``done_event(conversation_id, turn_count, ...)``), so every co-pilot turn raised a
TypeError and the stream ended in an ``internal_error`` event carrying the TypeError text.

The handler now follows the peer agent path: the reply is stored first so the
validated event carries its message id, and the done event carries the conversation id
and the turn count. The fallback emits the fallback token and a done event with turn
count 0.

Design PR6, docs/beta-single-container-design.md (co-pilot event defect, every mode).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.chat.models import StreamEvent, StreamEventType
from src.chat.pipeline.orchestrator import ChatPipeline

TENANT_ID = "t-copilot"
CONVERSATION_ID = "conv-copilot"
TRACE_ID = "trace-copilot"
MESSAGE_ID = "msg-copilot-1"
REPLY = "Open Settings, then Widget, to change the launcher colour."
COPILOT_RESULT = {
    "response": REPLY,
    "sources": [{"title": "Widget settings"}],
    "model": "gpt-4o",
    "tokens_input": 120,
    "tokens_output": 40,
}


async def _collect(gen: Any) -> list[StreamEvent]:
    return [event async for event in gen]


def _session(turn_count: int = 4) -> MagicMock:
    session = MagicMock()
    session.add_ai_message = AsyncMock(return_value=MESSAGE_ID)
    session.get_conversation = AsyncMock(return_value=SimpleNamespace(turn_count=turn_count))
    session._repo.patch = AsyncMock()
    return session


def _pipeline(session: MagicMock) -> ChatPipeline:
    """A ChatPipeline with only what _handle_co_pilot touches."""
    pipeline = ChatPipeline.__new__(ChatPipeline)
    pipeline._session = session
    pipeline._background_tasks = set()
    pipeline._fire_analytics = AsyncMock()
    return pipeline


def _budget() -> MagicMock:
    budget = MagicMock()
    budget.elapsed_ms = 150.0
    budget.stages = []

    async def execute_with_budget(stage: str, coro: Any) -> Any:
        result = await coro
        budget.stages.append(MagicMock(stage=stage, elapsed_ms=100.0, succeeded=True))
        return result

    budget.execute_with_budget = execute_with_budget
    return budget


async def _run_handler(pipeline: ChatPipeline) -> list[StreamEvent]:
    with patch("src.agents.plugins.events.emit_invocation"):
        return await _collect(
            pipeline._handle_co_pilot(
                tenant_id=TENANT_ID,
                conversation_id=CONVERSATION_ID,
                customer_message="How do I change the widget colour?",
                system_prompt="co-pilot prompt",
                conversation_history=[],
                team_member_role="admin",
                budget=_budget(),
                trace=MagicMock(),
                trace_id=TRACE_ID,
            )
        )


async def test_copilot_success_events():
    session = _session(turn_count=4)
    pipeline = _pipeline(session)
    pipeline._call_co_pilot = AsyncMock(return_value=COPILOT_RESULT)

    events = await _run_handler(pipeline)

    assert [e.event for e in events] == [
        StreamEventType.STAGE,
        StreamEventType.STAGE,
        StreamEventType.TOKEN,
        StreamEventType.VALIDATED,
        StreamEventType.DONE,
    ]
    started, completed, token, validated, done = events
    assert (started.data["stage"], started.data["status"]) == ("co-pilot", "started")
    assert (completed.data["stage"], completed.data["status"]) == ("co-pilot", "completed")
    assert token.data == {"text": REPLY, "sequence": 0}
    # The reply is stored before the validated event, so the event carries its message id.
    assert validated.data == {
        "conversation_id": CONVERSATION_ID,
        "message_id": MESSAGE_ID,
        "critic_passed": True,
    }
    assert done.data == {
        "conversation_id": CONVERSATION_ID,
        "turn_count": 4,
        "trace_id": TRACE_ID,
        "total_latency_ms": 150,
    }

    session.add_ai_message.assert_awaited_once()
    stored = session.add_ai_message.await_args.kwargs
    assert stored["content"] == REPLY
    assert stored["agents_invoked"] == ["co-pilot"]
    assert stored["critic_passed"] is None  # co-pilot bypasses the Critic (SPEC-1557)
    session._repo.patch.assert_awaited_once()  # admin_assistance, non-billable (SPEC-1561)
    session.get_conversation.assert_awaited_once_with(TENANT_ID, CONVERSATION_ID)


async def test_copilot_failure_events():
    session = _session()
    pipeline = _pipeline(session)
    pipeline._call_co_pilot = AsyncMock(side_effect=RuntimeError("co-pilot unreachable"))

    events = await _run_handler(pipeline)

    assert [e.event for e in events] == [
        StreamEventType.STAGE,
        StreamEventType.TOKEN,
        StreamEventType.DONE,
    ]
    assert events[0].data["status"] == "started"
    assert "Co-pilot" in events[1].data["text"]
    assert "co-pilot unreachable" not in events[1].data["text"]
    assert events[2].data == {
        "conversation_id": CONVERSATION_ID,
        "turn_count": 0,
        "trace_id": TRACE_ID,
    }
    session.add_ai_message.assert_not_awaited()


async def test_execute_copilot_route_has_no_internal_error():
    """A team member's turn routes to the co-pilot and the stream ends cleanly."""
    from src.multi_tenant.cosmos_schema import TenantTier
    from src.multi_tenant.system_prompt_builder import AgentRole

    session = AsyncMock()
    session.set_pii_scrubber = MagicMock()
    session.add_ai_message.return_value = MESSAGE_ID
    session.get_conversation.return_value = SimpleNamespace(turn_count=2)

    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}

    pipeline = ChatPipeline(
        session=session,
        prompt_builder=prompt_builder,
        profile_service=AsyncMock(),
    )
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    preferences = MagicMock()
    preferences.fine_tuning_enabled = False
    preferences.intent_confidence_threshold = 0.0

    with (
        patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
        patch.object(
            pipeline,
            "_call_intent_classifier",
            new_callable=AsyncMock,
            return_value={"intent": "admin_assistance", "confidence": 0.95},
        ),
        patch.object(
            pipeline,
            "_call_knowledge_retrieval",
            new_callable=AsyncMock,
            return_value={"context": "", "sources": []},
        ),
        patch.object(pipeline, "_call_co_pilot", new_callable=AsyncMock, return_value=COPILOT_RESULT) as call_co_pilot,
        patch.object(pipeline, "_fire_analytics", new_callable=AsyncMock),
        patch(
            "src.multi_tenant.superadmin_api._agent_overlays._get_tenant_overlays",
            new=AsyncMock(return_value={}),
        ),
        patch("src.agents.plugins.bindings.SkillBindingService.load_tenant_bindings", new=AsyncMock()),
        patch("src.agents.plugins.events.emit_invocation"),
    ):
        events = await _collect(
            pipeline.execute(
                TENANT_ID,
                CONVERSATION_ID,
                "How do I change the widget colour?",
                tenant=tenant,
                preferences=preferences,
                trace_id=TRACE_ID,
                team_member_role="admin",
            )
        )

    errors = [e for e in events if e.event == StreamEventType.ERROR]
    assert errors == []
    assert not any("An unexpected error occurred" in str(e.data.get("message", "")) for e in events)
    call_co_pilot.assert_awaited_once()

    validated = [e for e in events if e.event == StreamEventType.VALIDATED]
    assert [e.data["message_id"] for e in validated] == [MESSAGE_ID]
    assert events[-1].event == StreamEventType.DONE
    assert events[-1].data["conversation_id"] == CONVERSATION_ID
    assert events[-1].data["turn_count"] == 2
