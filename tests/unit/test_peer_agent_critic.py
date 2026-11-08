# tests/unit/test_peer_agent_critic.py: peer-agent replies go through the Critic
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
The peer-agent route stored and showed the agent's reply with ``critic_passed=None``
and a ``validated`` event, although critic_policy states that no response skips
validation. A tenant with peer-agent bindings sent customers unvalidated replies.

The reply is now validated before it is shown; on rejection only the Critic's fallback
is sent. Architecture review S8 (docs/agent-red-architecture-review.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.plugins.dispatch import PluginDispatchResult
from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant import pii_tokenizer as pii_tokenizer_module

AGENT_REPLY = "Your refund is approved; reply with your card number to receive it."
FALLBACK = "I'm sorry, I can't help with that here. A team member will follow up."


@pytest.fixture(autouse=True)
def _fresh_token_store() -> Iterator[None]:
    """PII token stores are module-level and keyed by tenant and conversation, so a token
    issued in one test would be reused by the next test that tokenizes the same value."""
    pii_tokenizer_module.PiiTokenizer.clear_conversation("conv-1", "t-1")
    yield
    pii_tokenizer_module.PiiTokenizer.clear_conversation("conv-1", "t-1")


def _Dispatch(result: dict[str, Any] | None = None, error: str | None = None) -> PluginDispatchResult:
    """The real dispatch result: the payload is ``content`` (Codex review of #142)."""
    return PluginDispatchResult(
        tool_name="refunds", agent_id="orders-agent", success=error is None, content=result, error=error or ""
    )


def _pipeline(approved: bool) -> ChatPipeline:
    pipeline = object.__new__(ChatPipeline)
    pipeline._session = MagicMock()
    pipeline._session.add_ai_message = AsyncMock(return_value="msg-1")
    pipeline._session.update_conversation_metadata = AsyncMock()
    pipeline._session.get_conversation = AsyncMock(return_value=MagicMock(turn_count=1))
    pipeline._background_tasks = set()
    pipeline._create_background_task = lambda coro, name="": coro.close()
    pipeline._fire_analytics = AsyncMock()
    verdict = MagicMock(block_reason=MagicMock(value="policy_violation") if not approved else None)
    pipeline._critic = MagicMock()
    pipeline._critic.require_critic_approval = AsyncMock(
        side_effect=lambda **kw: (approved, kw["response_text"] if approved else FALLBACK, verdict)
    )
    return pipeline


def _budget():
    budget = MagicMock(elapsed_ms=10.0, stages=[])

    async def execute_with_budget(agent_id, coro):
        result = await coro
        budget.stages.append(MagicMock(stage=agent_id, elapsed_ms=5.0, succeeded=True))
        return result

    budget.execute_with_budget = execute_with_budget
    return budget


async def _run(
    pipeline: ChatPipeline, dispatch: _Dispatch, critic_message: str | None = None, pii_tokenizer=None
) -> list:
    with (
        patch("src.agents.plugins.dispatch.PluginDispatcher") as dispatcher,
        patch("src.agents.plugins.events.emit_invocation"),
    ):
        dispatcher.return_value.dispatch_with_binding = AsyncMock(return_value=dispatch)
        events = [
            event
            async for event in pipeline._handle_peer_agent(
                tenant_id="t-1",
                conversation_id="conv-1",
                customer_message="Where is my refund?",
                agent_id="orders-agent",
                skill_id="refunds",
                budget=_budget(),
                trace=MagicMock(),
                trace_id="trace-1",
                critic_message=critic_message,
                pii_tokenizer=pii_tokenizer,
            )
        ]
    return events


def _kinds(events) -> list[str]:
    return [getattr(e.event, "value", str(e.event)) for e in events]


def _text(events) -> str:
    return " ".join(str(e.data) for e in events)


async def test_an_approved_reply_is_validated_and_shown():
    pipeline = _pipeline(approved=True)
    events = await _run(pipeline, _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    pipeline._critic.require_critic_approval.assert_awaited_once()
    assert pipeline._critic.require_critic_approval.call_args.kwargs["response_text"] == AGENT_REPLY
    assert AGENT_REPLY in _text(events)
    assert "validated" in _kinds(events)
    assert pipeline._session.add_ai_message.call_args.kwargs["critic_passed"] is True


async def test_a_rejected_reply_is_never_shown():
    pipeline = _pipeline(approved=False)
    events = await _run(pipeline, _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    assert AGENT_REPLY not in _text(events)
    kinds = _kinds(events)
    assert "retracted" in kinds and "validated" not in kinds  # never reported as approved
    assert FALLBACK in _text(events)
    stored = pipeline._session.add_ai_message.call_args.kwargs
    assert stored["critic_passed"] is False
    assert stored["content"] == FALLBACK
    assert stored["metadata"] == {"retraction_reason": "policy_violation"}


async def test_the_connection_failure_notice_needs_no_critic():
    pipeline = _pipeline(approved=True)
    await _run(pipeline, _Dispatch(error="dispatch failed"))
    pipeline._critic.require_critic_approval.assert_not_awaited()


async def test_the_critic_gets_the_tokenized_message():
    """The core path keeps raw PII away from the Critic's model prompt; so does this route."""
    pipeline = _pipeline(approved=True)
    await _run(
        pipeline,
        _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}),
        critic_message="Where is my refund? [EMAIL_1]",
    )
    assert pipeline._critic.require_critic_approval.call_args.kwargs["customer_message"] == (
        "Where is my refund? [EMAIL_1]"
    )


async def test_the_pipeline_trace_records_the_verdict():
    pipeline = _pipeline(approved=False)
    await _run(pipeline, _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    persisted = str(pipeline._session.update_conversation_metadata.call_args)
    assert "'critic_passed': False" in persisted


async def test_the_critic_gets_the_tokenized_reply_and_the_customer_the_original():
    """A peer can echo PII from the raw message; the Critic's model prompt must not see it (Codex review of #142)."""
    from src.multi_tenant.pii_tokenizer import PiiTokenizer

    reply = "I've sent the refund details to jo.customer@example.com."
    pipeline = _pipeline(approved=True)
    events = await _run(
        pipeline, _Dispatch(result={"response": reply, "model": "gpt-4o"}), pii_tokenizer=PiiTokenizer()
    )
    sent_to_critic = pipeline._critic.require_critic_approval.call_args.kwargs["response_text"]
    assert "jo.customer@example.com" not in sent_to_critic
    assert "jo.customer@example.com" in _text(events)  # restored for the customer
    assert pipeline._session.add_ai_message.call_args.kwargs["content"] == reply


async def test_the_customer_gets_the_original_when_the_token_reads_as_a_phone_number(monkeypatch):
    """ADO build 25: the email's token id was all digits, the phone pass tokenized it again,
    and the customer got ``pii-email-836890970487`` back (tests/unit/test_pii_tokenizer.py)."""
    ids = iter(uuid.UUID(hex_id.ljust(32, "0")) for hex_id in ("836890970487", "abcdefabcdef"))
    monkeypatch.setattr(pii_tokenizer_module, "uuid", SimpleNamespace(uuid4=lambda: next(ids)))
    reply = "I've sent the refund details to jo.customer@example.com."
    pipeline = _pipeline(approved=True)
    events = await _run(
        pipeline,
        _Dispatch(result={"response": reply, "model": "gpt-4o"}),
        pii_tokenizer=pii_tokenizer_module.PiiTokenizer(),
    )
    assert pipeline._critic.require_critic_approval.call_args.kwargs["response_text"] == (
        "I've sent the refund details to pii-email-836890970487."
    )
    assert "jo.customer@example.com" in _text(events)
    assert pipeline._session.add_ai_message.call_args.kwargs["content"] == reply


def _bubble_opens_first(events) -> bool:
    """The widget only creates a reply bubble on the response-generator start event (widget/src/transport/sse.ts)."""
    for event in events:
        kind = getattr(event.event, "value", str(event.event))
        if kind == "stage" and (event.data["stage"], event.data["status"]) == ("response-generator", "started"):
            return True
        if kind in ("token", "retracted"):
            return False
    return False


async def test_a_rejected_reply_retracts_this_turns_bubble():
    """Without a bubble for this turn, the widget's retraction replaced the previous reply (Codex review of #142)."""
    events = await _run(_pipeline(approved=False), _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    assert _bubble_opens_first(events)


async def test_an_approved_reply_has_a_bubble_to_appear_in():
    events = await _run(_pipeline(approved=True), _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    assert _bubble_opens_first(events)


async def test_the_peer_critic_reports_its_stage():
    """Admin pipeline views and metrics must see the Critic run, as on the core path (Codex review of #142)."""
    pipeline = _pipeline(approved=True)
    trace = MagicMock()
    with (
        patch("src.agents.plugins.dispatch.PluginDispatcher") as dispatcher,
        patch("src.agents.plugins.events.emit_invocation"),
    ):
        dispatcher.return_value.dispatch_with_binding = AsyncMock(
            return_value=_Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"})
        )
        events = [
            event
            async for event in pipeline._handle_peer_agent(
                tenant_id="t-1",
                conversation_id="conv-1",
                customer_message="Where is my refund?",
                agent_id="orders-agent",
                skill_id="refunds",
                budget=_budget(),
                trace=trace,
                trace_id="trace-1",
            )
        ]
    stages = [(e.data["stage"], e.data["status"]) for e in events if getattr(e.event, "value", "") == "stage"]
    assert ("critic-supervisor", "started") in stages and ("critic-supervisor", "completed") in stages
    assert stages.index(("critic-supervisor", "completed")) < stages.index(("response-generator", "started"))
    trace.set_critic_result.assert_called_once()
    persisted = str(pipeline._session.update_conversation_metadata.call_args)
    assert "critic_latency_ms" in persisted


def _mcp_result(**fields):
    from mcp.types import CallToolResult, TextContent

    fields.setdefault("content", [TextContent(type="text", text='{"balance": 1200}')])
    return CallToolResult(**fields)


async def _run_external(pipeline: ChatPipeline, content) -> list:
    """An external (MCP) peer: dispatch succeeds and ``content`` is the MCP SDK's CallToolResult."""
    return await _run(
        pipeline, PluginDispatchResult(tool_name="stripe", agent_id="orders-agent", success=True, content=content)
    )


async def test_an_external_peers_explicit_reply_is_validated_and_shown():
    pipeline = _pipeline(approved=True)
    events = await _run_external(pipeline, _mcp_result(structuredContent={"response": AGENT_REPLY}))
    assert pipeline._critic.require_critic_approval.call_args.kwargs["response_text"] == AGENT_REPLY
    assert AGENT_REPLY in _text(events)


@pytest.mark.parametrize(
    "content",
    [
        "raw-text-blocks",  # tool data, not a customer reply (Codex review of #142)
        "error-result",
        {"mock": True, "tool": "refunds"},  # the dispatcher's no-client mock
        {"response": "   "},
        {"response": None},
        None,
    ],
    ids=["mcp-text-only", "mcp-error", "mock-dict", "blank", "none-reply", "no-content"],
)
async def test_an_unusable_peer_reply_gets_the_fixed_notice_not_a_blank_message(content):
    """A blank or non-reply payload must not be validated, stored and shown as an empty answer."""
    if content == "raw-text-blocks":
        content = _mcp_result()
    elif content == "error-result":
        content = _mcp_result(isError=True)
    pipeline = _pipeline(approved=True)
    events = await (
        _run_external(pipeline, content)
        if not isinstance(content, dict | type(None))
        else _run(pipeline, _Dispatch(result=content))
    )
    pipeline._critic.require_critic_approval.assert_not_awaited()
    stored = pipeline._session.add_ai_message.call_args.kwargs["content"]
    assert stored.startswith("I wasn't able to connect to a specialized agent")
    assert "balance" not in _text(events)


async def test_the_peer_critic_is_a_recorded_pipeline_stage():
    """The persisted trace and analytics enumerate budget.stages, so the Critic must be one (Codex review of #142)."""
    pipeline = _pipeline(approved=True)
    await _run(pipeline, _Dispatch(result={"response": AGENT_REPLY, "model": "gpt-4o"}))
    persisted = str(pipeline._session.update_conversation_metadata.call_args)
    assert "'stage': 'critic-supervisor'" in persisted
