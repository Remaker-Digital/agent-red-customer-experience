# tests/unit/test_single_container_critic.py: RC-11 flag and fail-closed in-process Critic
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR1 (docs/beta-single-container-design.md, sections 2 and 4).

SINGLE_CONTAINER_MODE is read through ``single_container_mode()`` at call time. When it is on,
``_validate_with_critic`` validates every reply with an in-process CriticSupervisorAgent before
it looks at CriticPolicy, which lifecycle always builds from the default critic URL. The adapter
has its own timeout and fails closed: every outcome other than a clean approval returns
(False, SAFE_FALLBACK_MESSAGE, CriticResult), so the orchestrator retracts the streamed tokens.

With the flag off nothing changes: the guard tests at the end pass before and after.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.models import StreamEventType
from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.critic_policy import (
    SAFE_FALLBACK_MESSAGE,
    CriticBlockReason,
    CriticResult,
    CriticVerdict,
)
from src.multi_tenant.pipeline_resilience import STAGE_BUDGETS_MS, PipelineTimeoutBudget

FLAG = "SINGLE_CONTAINER_MODE"
IN_PROCESS = "src.chat.pipeline.in_process"
IN_PROCESS_FILE = Path(__file__).resolve().parents[2] / "src" / "chat" / "pipeline" / "in_process.py"
REPLY = "Hello there"

APPROVE: dict[str, Any] = {
    "approved": True,
    "verdict": "approved",
    "flags": [],
    "modified_response": None,
    "safe_text": REPLY,
    "block_reason": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeCritic:
    """An in-process critic agent that records its calls."""

    def __init__(self, result: Any = None, *, delay: float = 0.0, exc: BaseException | None = None) -> None:
        self.result = result
        self.delay = delay
        self.exc = exc
        self.calls: list[tuple[dict[str, Any], dict[str, str]]] = []

    async def process(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append((payload, headers))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


def _policy_that_must_not_run() -> MagicMock:
    """Stands in for the CriticPolicy that lifecycle always builds."""
    policy = MagicMock()
    policy.require_critic_approval = AsyncMock(
        side_effect=AssertionError("CriticPolicy consulted in single-container mode"),
    )
    return policy


def _pipeline(*, critic: Any = None, openai_client: Any = None) -> ChatPipeline:
    """A pipeline without its services; only what _validate_with_critic reads."""
    pipeline = object.__new__(ChatPipeline)
    pipeline._critic = critic
    pipeline._openai_client = openai_client
    return pipeline


def _use_critic(pipeline: ChatPipeline, agent: Any) -> None:
    """Place an agent in the per-pipeline in-process registry (design section 3)."""
    pipeline.__dict__["_in_process_agents"] = {"critic-supervisor": agent}


def _openai_client(content: str = '{"verdict": "approved", "flags": []}') -> MagicMock:
    """An AsyncAzureOpenAI stand-in whose completion returns ``content``."""
    client = MagicMock()
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    client.chat.completions.create = AsyncMock(return_value=completion)
    return client


async def _validate(
    pipeline: ChatPipeline,
    *,
    budget: Any = None,
    titles: list[str] | None = None,
) -> tuple[bool, str, CriticResult]:
    return await pipeline._validate_with_critic("t1", "c1", REPLY, "Hi?", budget, knowledge_titles=titles)


def _assert_blocked(outcome: tuple[bool, str, CriticResult], reason: CriticBlockReason) -> CriticResult:
    approved, text, result = outcome
    assert approved is False
    assert text == SAFE_FALLBACK_MESSAGE
    assert result.approved is False
    assert result.block_reason is reason
    assert result.modified_response is None
    assert result.critic_instance == "in-process"
    return result


@pytest.fixture
def beta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")


# ---------------------------------------------------------------------------
# The flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("false", False), ("1", False), ("yes", False), ("true", True), (" TRUE ", True)],
)
def test_flag_parsing(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool) -> None:
    from src.chat.pipeline.constants import single_container_mode

    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    assert single_container_mode() is expected


def test_conftest_keeps_flag_off_for_every_test(request: pytest.FixtureRequest) -> None:
    """tests/conftest.py clears the flag before each test, so a developer shell cannot flip one."""
    assert "_single_container_mode_off" in request.fixturenames
    assert FLAG not in os.environ


# ---------------------------------------------------------------------------
# Precedence, payload and registry
# ---------------------------------------------------------------------------


async def test_precedence_over_policy(beta: None) -> None:
    policy = _policy_that_must_not_run()
    pipeline = _pipeline(critic=policy)
    _use_critic(pipeline, _FakeCritic(APPROVE))

    approved, text, result = await _validate(pipeline, budget=PipelineTimeoutBudget())

    assert approved is True
    assert text == REPLY
    assert result.verdict is CriticVerdict.APPROVED
    assert result.block_reason is None
    assert result.critic_instance == "in-process"
    policy.require_critic_approval.assert_not_awaited()


async def test_forwards_titles_and_headers(beta: None) -> None:
    critic = _FakeCritic(APPROVE)
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, critic)

    await _validate(pipeline, titles=["Returns"])
    await _validate(pipeline, titles=None)

    (first_payload, first_headers), (second_payload, _) = critic.calls
    assert first_payload == {
        "response_text": REPLY,
        "customer_message": "Hi?",
        "knowledge_titles": ["Returns"],
        "conversation_id": "c1",
    }
    assert first_headers == {"x-tenant-id": "t1", "x-conversation-id": "c1"}
    assert second_payload["knowledge_titles"] == []


async def test_registry_builds_one_critic_per_pipeline_with_its_client(beta: None) -> None:
    from src.agents.critic_supervisor import CriticSupervisorAgent
    from src.chat.pipeline.in_process import get_in_process_agent

    client = _openai_client()
    pipeline = _pipeline(critic=_policy_that_must_not_run(), openai_client=client)

    approved, text, result = await _validate(pipeline, titles=["Returns"])

    assert (approved, text, result.verdict) == (True, REPLY, CriticVerdict.APPROVED)
    client.chat.completions.create.assert_awaited_once()
    agent = get_in_process_agent(pipeline, "critic-supervisor")
    assert isinstance(agent, CriticSupervisorAgent)
    assert agent._openai_client is client
    assert get_in_process_agent(pipeline, "critic-supervisor") is agent
    assert get_in_process_agent(_pipeline(openai_client=client), "critic-supervisor") is not agent
    assert get_in_process_agent(None, "critic-supervisor") is None


async def test_beta_validation_sets_no_dead_agent_attributes(beta: None) -> None:
    """The registry never uses _cr_agent, _esc_agent or _an_agent (test_environment_parity.py)."""
    pipeline = _pipeline(critic=_policy_that_must_not_run(), openai_client=_openai_client())

    await _validate(pipeline)

    for name in ("_cr_agent", "_esc_agent", "_an_agent"):
        assert not hasattr(pipeline, name), name


async def test_in_process_headers_follow_each_request() -> None:
    from src.chat.pipeline.in_process import in_process_headers

    pipeline = object.__new__(ChatPipeline)
    assert in_process_headers(pipeline) == {"x-tenant-id": "", "x-conversation-id": ""}

    a_stored, b_stored = asyncio.Event(), asyncio.Event()

    async def request(tenant: str, conversation: str, mine: asyncio.Event, other: asyncio.Event) -> dict[str, str]:
        pipeline._current_tenant_id = tenant
        pipeline._current_conversation_id = conversation
        mine.set()
        await other.wait()  # the other request stores its ids here
        return in_process_headers(pipeline)

    seen = await asyncio.gather(
        asyncio.create_task(request("tA", "cA", a_stored, b_stored)),
        asyncio.create_task(request("tB", "cB", b_stored, a_stored)),
    )
    assert seen == [
        {"x-tenant-id": "tA", "x-conversation-id": "cA"},
        {"x-tenant-id": "tB", "x-conversation-id": "cB"},
    ]


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


async def test_timeout_fail_closed(beta: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{IN_PROCESS}.CRITIC_MIN_TIMEOUT_S", 0.05)
    policy = _policy_that_must_not_run()
    pipeline = _pipeline(critic=policy)
    _use_critic(pipeline, _FakeCritic(APPROVE, delay=10))

    started = time.monotonic()
    outcome = await _validate(pipeline, budget=SimpleNamespace(remaining_ms=10.0))

    assert time.monotonic() - started < 1.0
    result = _assert_blocked(outcome, CriticBlockReason.ERROR)
    assert result.flags == ["in_process_critic_timeout"]
    assert result.verdict is None
    policy.require_critic_approval.assert_not_awaited()


async def test_non_numeric_budget_uses_default(beta: None) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic(APPROVE))

    approved, text, _ = await _validate(pipeline, budget=MagicMock())

    assert (approved, text) == (True, REPLY)


@pytest.mark.parametrize(
    ("budget", "expected_s"),
    [
        (None, 5.0),
        (MagicMock(), 5.0),
        (SimpleNamespace(remaining_ms=True), 5.0),
        (SimpleNamespace(remaining_ms=12_000.0), 5.0),
        (SimpleNamespace(remaining_ms=2_000), 2.0),
        (SimpleNamespace(remaining_ms=100), 0.5),
        (SimpleNamespace(remaining_ms=0), 0.5),
    ],
    ids=["none", "magicmock", "bool", "plenty_left", "two_s_left", "floor", "expired"],
)
def test_critic_timeout_bounds(budget: Any, expected_s: float) -> None:
    """min(critic-supervisor stage budget, remaining pipeline time), floored at 0.5 s."""
    from src.chat.pipeline.in_process import critic_timeout_s

    assert critic_timeout_s(budget) == pytest.approx(expected_s)


# ---------------------------------------------------------------------------
# Failures and mapping
# ---------------------------------------------------------------------------


class _BrokenBudget:
    @property
    def remaining_ms(self) -> float:
        raise RuntimeError("clock unavailable")


class _UnbuildableCritic:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot build critic")


@pytest.mark.parametrize("failure", ["agent_raises", "construction_fails", "timeout_arithmetic_fails"])
async def test_exception_and_construction_failure_return_error(
    beta: None, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    policy = _policy_that_must_not_run()
    pipeline = _pipeline(critic=policy, openai_client=_openai_client())
    budget: Any = PipelineTimeoutBudget()
    if failure == "agent_raises":
        _use_critic(pipeline, _FakeCritic(exc=RuntimeError("boom")))
    elif failure == "construction_fails":
        monkeypatch.setattr("src.agents.critic_supervisor.CriticSupervisorAgent", _UnbuildableCritic)
    else:
        _use_critic(pipeline, _FakeCritic(APPROVE))
        budget = _BrokenBudget()

    result = _assert_blocked(await _validate(pipeline, budget=budget), CriticBlockReason.ERROR)

    assert result.flags == ["in_process_critic_error"]
    assert result.verdict is None
    policy.require_critic_approval.assert_not_awaited()


async def test_no_client_real_agent_maps_unavailable(beta: None) -> None:
    policy = _policy_that_must_not_run()
    pipeline = _pipeline(critic=policy, openai_client=None)

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.UNAVAILABLE)

    assert result.verdict is None  # the agent's "unavailable" is not a CriticVerdict member
    assert result.flags == ["no_openai_client"]
    policy.require_critic_approval.assert_not_awaited()


async def test_rejected_never_returns_agent_text(beta: None) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(
        pipeline,
        _FakeCritic(
            {
                "approved": False,
                "verdict": "rejected",
                "flags": ["pii_detected"],
                "modified_response": "LEAKED modified text",
                "safe_text": "LEAKED agent text",
                "block_reason": "rejected",
            }
        ),
    )

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.REJECTED)

    assert result.verdict is CriticVerdict.REJECTED
    assert result.flags == ["pii_detected"]


@pytest.mark.parametrize("modified_response", [None, ""])
async def test_modified_without_text_rejected(beta: None, modified_response: str | None) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(
        pipeline,
        _FakeCritic({**APPROVE, "verdict": "modified", "modified_response": modified_response}),
    )

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.REJECTED)

    assert result.verdict is CriticVerdict.MODIFIED
    assert "modified_verdict_without_text" in result.flags


async def test_modified_with_text_is_delivered(beta: None) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(
        pipeline,
        _FakeCritic(
            {**APPROVE, "verdict": "modified", "modified_response": "Hello, friend", "safe_text": "Hello, friend"}
        ),
    )

    approved, text, result = await _validate(pipeline)

    assert (approved, text) == (True, "Hello, friend")
    assert result.verdict is CriticVerdict.MODIFIED
    assert result.modified_response == "Hello, friend"


@pytest.mark.parametrize("safe_text", [None, 123, "", ["Hello"]], ids=["none", "int", "empty", "list"])
async def test_approved_non_string_text_fails_closed(beta: None, safe_text: Any) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic({**APPROVE, "safe_text": safe_text}))

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.ERROR)

    assert "in_process_critic_invalid_text" in result.flags


@pytest.mark.parametrize("output", [None, "approved", ["approved"]], ids=["none", "str", "list"])
async def test_non_dict_result_fails_closed(beta: None, output: Any) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic(output))

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.ERROR)

    assert result.flags == ["in_process_critic_bad_result"]
    assert result.verdict is None


@pytest.mark.parametrize(
    ("output", "verdict"),
    [
        ({"approved": "yes", "verdict": "approved"}, CriticVerdict.APPROVED),
        ({"approved": True, "verdict": "unavailable"}, None),
        ({"approved": True, "verdict": "rejected"}, CriticVerdict.REJECTED),
        ({"approved": True}, None),
    ],
    ids=["approved_not_true", "unknown_verdict", "rejected_verdict", "no_verdict"],
)
async def test_approval_needs_true_and_an_approving_verdict(
    beta: None, output: dict[str, Any], verdict: CriticVerdict | None
) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic({**output, "safe_text": REPLY}))

    result = _assert_blocked(await _validate(pipeline), CriticBlockReason.REJECTED)

    assert result.verdict is verdict


@pytest.mark.parametrize(
    ("block_reason", "expected"),
    [(None, CriticBlockReason.REJECTED), ("timeout", CriticBlockReason.TIMEOUT), ("bogus", CriticBlockReason.ERROR)],
    ids=["missing", "known", "unknown"],
)
async def test_block_reason_mapping(beta: None, block_reason: str | None, expected: CriticBlockReason) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic({"approved": False, "verdict": "rejected", "block_reason": block_reason}))

    _assert_blocked(await _validate(pipeline), expected)


@pytest.mark.parametrize(
    ("flags", "expected"),
    [(None, []), ("pii", ["pii"]), (["a", 1], ["a", "1"]), (("b",), ["b"])],
    ids=["missing", "str", "mixed_list", "tuple"],
)
async def test_flags_normalised_to_list_of_str(beta: None, flags: Any, expected: list[str]) -> None:
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, _FakeCritic({**APPROVE, "flags": flags}))

    approved, _, result = await _validate(pipeline)

    assert approved is True
    assert result.flags == expected


@pytest.mark.parametrize("outcome", ["approved", "rejected", "error"])
async def test_all_result_fields_populated(beta: None, outcome: str) -> None:
    """PR #142 reads latency_ms and block_reason.value; every field must be set and typed."""
    agents = {
        "approved": _FakeCritic(APPROVE),
        "rejected": _FakeCritic({"approved": False, "verdict": "rejected", "block_reason": "rejected"}),
        "error": _FakeCritic(exc=RuntimeError("boom")),
    }
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, agents[outcome])

    approved, _, result = await _validate(pipeline)

    assert isinstance(result, CriticResult)
    assert {f.name for f in dataclasses.fields(result)} == {
        "approved",
        "verdict",
        "block_reason",
        "flags",
        "modified_response",
        "latency_ms",
        "critic_instance",
        "request_id",
    }
    assert result.approved is approved
    assert isinstance(result.latency_ms, float)
    assert isinstance(result.request_id, str)
    assert result.request_id.startswith("critic-c1-")
    assert result.critic_instance == "in-process"
    assert isinstance(result.flags, list)
    assert all(isinstance(flag, str) for flag in result.flags)
    if approved:
        assert result.block_reason is None
    else:
        assert isinstance(result.block_reason.value, str)


async def test_cancelled_error_propagates(beta: None) -> None:
    """A client disconnect cancels the turn: nothing is converted into a verdict."""
    critic = _FakeCritic(APPROVE, delay=10)
    pipeline = _pipeline(critic=_policy_that_must_not_run())
    _use_critic(pipeline, critic)

    task = asyncio.create_task(_validate(pipeline, budget=PipelineTimeoutBudget()))
    await asyncio.sleep(0.05)
    assert critic.calls, "the in-process critic should be awaiting the agent"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _use_critic(pipeline, _FakeCritic(exc=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await _validate(pipeline, budget=PipelineTimeoutBudget())


# ---------------------------------------------------------------------------
# End to end: a hanging Critic retracts the streamed reply
# ---------------------------------------------------------------------------


def _streaming_pipeline(session: AsyncMock, *, critic: Any, openai_client: Any) -> ChatPipeline:
    """A ChatPipeline with mocked services (tests/chat/test_sse_error_handling.py pattern)."""
    from src.multi_tenant.system_prompt_builder import AgentRole

    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    return ChatPipeline(
        session=session,
        prompt_builder=prompt_builder,
        profile_service=AsyncMock(),
        critic=critic,
        openai_client=openai_client,
    )


async def test_execute_retracts_on_inprocess_critic_timeout(beta: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import patch

    from src.multi_tenant.cosmos_schema import TenantTier

    monkeypatch.setattr(f"{IN_PROCESS}.CRITIC_MIN_TIMEOUT_S", 0.05)
    monkeypatch.setitem(STAGE_BUDGETS_MS, "critic-supervisor", 50)

    session = AsyncMock()
    session.get_conversation.return_value = MagicMock(turn_count=1, customer_id=None)
    session.add_ai_message.return_value = "msg-001"
    session.set_pii_scrubber = MagicMock()
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    preferences = MagicMock()
    preferences.fine_tuning_enabled = False
    preferences.fine_tuning_active_model_id = None
    preferences.fine_tuning_ab_experiment_id = None

    async def hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=hang)
    policy = _policy_that_must_not_run()
    pipeline = _streaming_pipeline(session, critic=policy, openai_client=client)

    async def stream(*args: Any, **kwargs: Any) -> Any:
        yield "Hello "
        yield "world"

    started = time.monotonic()
    with (
        patch.object(
            pipeline,
            "_call_intent_classifier",
            new_callable=AsyncMock,
            return_value={"intent": "general_inquiry", "confidence": 0.95},
        ),
        patch.object(
            pipeline,
            "_call_knowledge_retrieval",
            new_callable=AsyncMock,
            return_value={"context": "some context", "sources": []},
        ),
        patch.object(pipeline, "_call_response_generator_stream", side_effect=stream),
        patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
        patch.object(pipeline, "_fire_analytics", new_callable=AsyncMock),
    ):
        events = [
            event
            async for event in pipeline.execute("t-001", "conv-001", "Hello?", tenant=tenant, preferences=preferences)
        ]

    assert time.monotonic() - started < 5.0
    kinds = [event.event for event in events]
    assert StreamEventType.ERROR not in kinds
    assert kinds.count(StreamEventType.TOKEN) == 2
    assert StreamEventType.VALIDATED not in kinds
    retracted = [event for event in events if event.event == StreamEventType.RETRACTED]
    assert len(retracted) == 1
    assert retracted[0].data["fallback_text"] == SAFE_FALLBACK_MESSAGE
    assert retracted[0].data["reason"] == "error"
    assert StreamEventType.DONE in kinds

    session.add_ai_message.assert_awaited_once()
    stored = session.add_ai_message.await_args.kwargs
    assert stored["critic_passed"] is False
    assert stored["content"] == SAFE_FALLBACK_MESSAGE
    assert stored["metadata"]["retraction_reason"] == "error"
    client.chat.completions.create.assert_awaited_once()
    policy.require_critic_approval.assert_not_awaited()


async def test_execute_retracts_when_the_critic_rejects(beta: None) -> None:
    """P1-W2. A rejecting critic, through execute, stores the fallback."""
    from unittest.mock import patch

    from src.multi_tenant.cosmos_schema import TenantTier

    session = AsyncMock()
    session.get_conversation.return_value = MagicMock(turn_count=1, customer_id=None)
    session.add_ai_message.return_value = "msg-001"
    session.set_pii_scrubber = MagicMock()
    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    preferences = MagicMock()
    preferences.fine_tuning_enabled = False
    preferences.fine_tuning_active_model_id = None
    preferences.fine_tuning_ab_experiment_id = None

    policy = _policy_that_must_not_run()
    pipeline = _streaming_pipeline(session, critic=policy, openai_client=MagicMock())
    _use_critic(
        pipeline,
        _FakeCritic(
            {
                "approved": False,
                "verdict": "rejected",
                "flags": ["pii_detected"],
                "modified_response": "LEAKED modified text",
                "safe_text": "LEAKED agent text",
                "block_reason": "rejected",
            }
        ),
    )

    async def stream(*args: Any, **kwargs: Any) -> Any:
        yield "Hello "
        yield "world"

    with (
        patch.object(
            pipeline,
            "_call_intent_classifier",
            new_callable=AsyncMock,
            return_value={"intent": "general_inquiry", "confidence": 0.95},
        ),
        patch.object(
            pipeline,
            "_call_knowledge_retrieval",
            new_callable=AsyncMock,
            return_value={"context": "some context", "sources": []},
        ),
        patch.object(pipeline, "_call_response_generator_stream", side_effect=stream),
        patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
        patch.object(pipeline, "_fire_analytics", new_callable=AsyncMock),
    ):
        events = [
            event
            async for event in pipeline.execute(
                "t-001", "conv-001", "Hello?", tenant=tenant, preferences=preferences
            )
        ]

    kinds = [event.event for event in events]
    assert StreamEventType.ERROR not in kinds
    assert kinds.count(StreamEventType.TOKEN) == 2
    assert StreamEventType.VALIDATED not in kinds
    retracted = [event for event in events if event.event == StreamEventType.RETRACTED]
    assert len(retracted) == 1
    assert retracted[0].data["fallback_text"] == SAFE_FALLBACK_MESSAGE
    assert retracted[0].data["reason"] == "rejected"
    assert "LEAKED" not in retracted[0].data["fallback_text"]
    session.add_ai_message.assert_awaited_once()
    stored = session.add_ai_message.await_args.kwargs
    assert stored["critic_passed"] is False
    assert stored["content"] == SAFE_FALLBACK_MESSAGE
    assert "LEAKED" not in stored["content"]
    policy.require_critic_approval.assert_not_awaited()


# ---------------------------------------------------------------------------
# Guards: flag off is unchanged
# ---------------------------------------------------------------------------


async def test_flag_off_policy_gets_exactly_four_kwargs() -> None:
    sentinel = (True, "policy text", MagicMock())
    policy = MagicMock()
    policy.require_critic_approval = AsyncMock(return_value=sentinel)
    critic = _FakeCritic(APPROVE)
    pipeline = _pipeline(critic=policy)
    _use_critic(pipeline, critic)

    outcome = await _validate(pipeline, budget=PipelineTimeoutBudget(), titles=["Returns"])

    assert outcome is sentinel
    policy.require_critic_approval.assert_awaited_once_with(
        tenant_id="t1",
        conversation_id="c1",
        response_text=REPLY,
        customer_message="Hi?",
    )
    assert critic.calls == []


async def test_flag_off_without_policy_is_unavailable_fallback() -> None:
    critic = _FakeCritic(APPROVE)
    pipeline = _pipeline(critic=None)
    _use_critic(pipeline, critic)

    approved, text, result = await _validate(pipeline)

    assert (approved, text) == (False, SAFE_FALLBACK_MESSAGE)
    assert result.block_reason is CriticBlockReason.UNAVAILABLE
    assert result.critic_instance == "none"
    assert critic.calls == []


def test_in_process_module_never_imports_containers() -> None:
    """Agents load only as src.agents.<agent>, inside factories (design section 3)."""
    tree = ast.parse(IN_PROCESS_FILE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported
    assert not [name for name in imported if name.startswith("src.agents.containers")]

    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module)
    assert not [name for name in module_level if name.startswith("src.agents")]
