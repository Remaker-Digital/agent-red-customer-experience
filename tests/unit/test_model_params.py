# tests/agents/test_model_params.py: per-role model parameters, F30 and the RG empty-stream guard
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
Chat roles and the deployment each one calls:

- RG: response generator (customer replies, streamed) and co-pilot, AZURE_RG_MODEL
- CR: Critic/supervisor, AZURE_CR_MODEL
- IC: intent classifier and escalation handler, AZURE_IC_MODEL

Every request goes through the real openai SDK into FakeAzureOpenAI, so the tests read the exact
HTTP request (deployment URL, api-version and JSON body).

- Default mode (no new variable): every role sends today's request. The only change is F30, the
  Critic's output cap, raised from 300 so a 'modified' verdict carrying a full reply fits. These
  guards pass before and after the change, except the Critic cap.
- Reasoning mode, per role, only when the owner sets AZURE_<ROLE>_REASONING_EFFORT: the request
  sends max_completion_tokens (AZURE_<ROLE>_MAX_COMPLETION_TOKENS, default 25,000) and
  reasoning_effort, and no temperature or max_tokens. JSON-mode roles keep response_format.
- The RG never hands an empty reply on: an empty stream, or a 'length' finish with no text, raises
  so the orchestrator sends its fixed error message instead of an empty reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents import co_pilot, critic_supervisor, escalation_handler, intent_classifier, response_generator
from src.agents.co_pilot import CoPilotAgent
from src.agents.critic_supervisor import CriticSupervisorAgent
from src.agents.escalation_handler import EscalationHandlerAgent
from src.agents.intent_classifier import IntentClassifierAgent
from src.agents.response_generator import ResponseGeneratorAgent
from src.multi_tenant.critic_policy import SAFE_FALLBACK_MESSAGE
from tests.helpers.fake_azure_openai import DEFAULT_API_VERSION, ENDPOINT, FakeAzureOpenAI

ROLES = ("RG", "CR", "IC")
JSON_MODE = {"type": "json_object"}
FINE_TUNED_DEPLOYMENT = "ft:gpt-4o-mini:agentred:tenant-a:v1"

# F30: a 1,024-token reply (the RG's cap) measured 3,788 characters, and the Critic's 'modified'
# verdict carrying it measured 1,145 o200k tokens (4,132 characters): JSON escaping, flags and
# reasoning add about 12%. The fake counts 3 characters per token, fewer than measured, so it
# overstates the tokens a verdict needs.
FULL_REPLY_CHARS = 3_788
CHARS_PER_TOKEN = 3


@pytest.fixture(autouse=True)
def _no_model_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """No reasoning, cap or API version variable leaks in from the shell."""
    for role in ROLES:
        monkeypatch.delenv(f"AZURE_{role}_REASONING_EFFORT", raising=False)
        monkeypatch.delenv(f"AZURE_{role}_MAX_COMPLETION_TOKENS", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_CHAT_API_VERSION", raising=False)


def _url(deployment: str) -> str:
    return f"{ENDPOINT}/openai/deployments/{deployment}/chat/completions?api-version={DEFAULT_API_VERSION}"


async def _classify(fake: FakeAzureOpenAI) -> dict[str, Any]:
    agent = IntentClassifierAgent(openai_client=fake.client())
    return await agent.process({"message": "Where is my order?", "system_prompt": "IC prompt"}, {})


async def _escalate(fake: FakeAzureOpenAI) -> dict[str, Any]:
    agent = EscalationHandlerAgent(openai_client=fake.client())
    return await agent.process({"message": "I want a human", "system_prompt": "ESC prompt"}, {})


async def _criticize(fake: FakeAzureOpenAI, response_text: str = "Our return window is 30 days.") -> dict[str, Any]:
    agent = CriticSupervisorAgent(openai_client=fake.client())
    payload = {
        "response_text": response_text,
        "customer_message": "What is the return window?",
        "knowledge_titles": ["Returns"],
        "conversation_id": "conv-model-params",
    }
    return await agent.process(payload, {})


async def _co_pilot(fake: FakeAzureOpenAI) -> dict[str, Any]:
    agent = CoPilotAgent(openai_client=fake.client())
    return await agent.process({"message": "How do I add a teammate?", "system_prompt": "CP prompt"}, {})


async def _generate(
    fake: FakeAzureOpenAI,
    *,
    knowledge_context: str = "Returns are accepted within 30 days.",
    model: str | None = None,
) -> list[str]:
    agent = ResponseGeneratorAgent(openai_client=fake.client())
    chunks: list[str] = []
    async for chunk in agent.generate_stream(
        customer_message="What is the return window?",
        intent="return_request",
        knowledge_context=knowledge_context,
        system_prompt="RG prompt",
        model=model or response_generator.AZURE_RG_MODEL,
        timeout_seconds=12.5,
    ):
        chunks.append(chunk)
    return chunks


def _long_reply() -> str:
    paragraph = (
        'Thanks for reaching out! Here is what our "Pro" plan includes:\n\n'
        "- **Unlimited projects** and 25 GB of storage ($29/month)\n"
        "- Priority support with a 4-hour response time\n"
        "- See our [pricing page](https://example.com/pricing?plan=pro&ref=chat) for details.\n\n"
    )
    return (paragraph * (FULL_REPLY_CHARS // len(paragraph) + 1))[:FULL_REPLY_CHARS]


def _modified_verdict(reply: str) -> str:
    return json.dumps(
        {
            "verdict": "modified",
            "flags": ["policy_contradiction"],
            "modified_response": reply,
            "reasoning": "The storage figure contradicted the plan article, so the corrected text replaces it.",
        }
    )


# ---------------------------------------------------------------------------
# Default mode: today's requests, byte for byte (F30 is the one intended change)
# ---------------------------------------------------------------------------


class TestDefaultModeRequests:
    """With no new variable set, each role sends today's URL and body."""

    async def test_intent_classifier_request_is_unchanged(self) -> None:
        fake = FakeAzureOpenAI(content='{"intent": "order_status", "confidence": 0.9}')
        result = await _classify(fake)

        model = intent_classifier.AZURE_IC_MODEL
        assert str(fake.requests[0].url) == _url(model)
        assert fake.params() == {"model": model, "temperature": 0.0, "max_tokens": 150, "response_format": JSON_MODE}
        assert result["intent"] == "order_status"

    async def test_escalation_handler_request_is_unchanged(self) -> None:
        fake = FakeAzureOpenAI(content='{"reason": "Wants a person", "urgency": "high"}')
        result = await _escalate(fake)

        model = escalation_handler.AZURE_IC_MODEL
        assert str(fake.requests[0].url) == _url(model)
        assert fake.params() == {"model": model, "temperature": 0.0, "max_tokens": 200, "response_format": JSON_MODE}
        assert result["urgency"] == "high"

    async def test_critic_request_is_unchanged_except_the_f30_cap(self) -> None:
        fake = FakeAzureOpenAI(content='{"verdict": "approved", "flags": []}')
        result = await _criticize(fake)

        model = critic_supervisor.AZURE_CR_MODEL
        assert str(fake.requests[0].url) == _url(model)
        assert fake.params() == {"model": model, "temperature": 0.0, "max_tokens": 2048, "response_format": JSON_MODE}
        assert result["approved"] is True

    async def test_co_pilot_request_is_unchanged(self) -> None:
        fake = FakeAzureOpenAI(content="Open Settings, then Team.")
        result = await _co_pilot(fake)

        model = co_pilot.AZURE_RG_MODEL
        assert str(fake.requests[0].url) == _url(model)
        assert fake.params() == {"model": model, "temperature": 0.3, "max_tokens": 1024}
        assert result["response"] == "Open Settings, then Team."

    @pytest.mark.parametrize(("knowledge_context", "temperature"), [("Returns: 30 days.", 0.3), ("", 0.7)])
    async def test_response_generator_request_is_unchanged(self, knowledge_context: str, temperature: float) -> None:
        fake = FakeAzureOpenAI(stream_text=["Our window ", "is 30 days."])
        chunks = await _generate(fake, knowledge_context=knowledge_context)

        model = response_generator.AZURE_RG_MODEL
        assert str(fake.requests[0].url) == _url(model)
        assert fake.params() == {"model": model, "temperature": temperature, "max_tokens": 1024, "stream": True}
        assert chunks == ["Our window ", "is 30 days."]

    async def test_a_cap_variable_alone_changes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AZURE_<ROLE>_MAX_COMPLETION_TOKENS applies only once the role has a reasoning effort."""
        for role in ROLES:
            monkeypatch.setenv(f"AZURE_{role}_MAX_COMPLETION_TOKENS", "4000")
        fake = FakeAzureOpenAI(content='{"verdict": "approved", "flags": []}')

        await _classify(fake)
        await _criticize(fake)
        await _generate(fake)

        assert [body.get("max_tokens") for body in fake.bodies()] == [150, 2048, 1024]
        assert not any("max_completion_tokens" in body or "reasoning_effort" in body for body in fake.bodies())


# ---------------------------------------------------------------------------
# F30: a 'modified' verdict with a full-length reply is not a false block
# ---------------------------------------------------------------------------


class TestCriticOutputCap:
    """F30: the Critic's cap fits a 'modified' verdict that repeats a full reply."""

    async def test_modified_verdict_with_a_full_length_reply_is_delivered(self) -> None:
        reply = _long_reply()
        fake = FakeAzureOpenAI(content=_modified_verdict(reply), chars_per_token=CHARS_PER_TOKEN)

        result = await _criticize(fake, response_text=reply)

        assert result["verdict"] == "modified"
        assert result["approved"] is True
        assert result["safe_text"] == reply

    async def test_the_cap_covers_the_response_generators_cap_with_room_for_json(self) -> None:
        fake = FakeAzureOpenAI(content='{"verdict": "approved", "flags": []}')
        await _generate(fake)
        await _criticize(fake)

        rg_cap = fake.bodies()[0]["max_tokens"]
        critic_cap = fake.bodies()[1]["max_tokens"]
        # Measured overhead is about 12%; require 25% so escaping and a longer rewrite still fit.
        assert critic_cap >= rg_cap * 1.25

    async def test_a_verdict_cut_off_at_the_cap_still_blocks(self) -> None:
        """Fail-closed is kept: a truncated verdict cannot be parsed and the reply is blocked."""
        reply = _long_reply() * 3
        fake = FakeAzureOpenAI(content=_modified_verdict(reply), chars_per_token=CHARS_PER_TOKEN)

        result = await _criticize(fake, response_text=reply)

        assert result["approved"] is False
        assert result["safe_text"] == SAFE_FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# Reasoning mode, per role, opt-in
# ---------------------------------------------------------------------------


class TestReasoningMode:
    """AZURE_<ROLE>_REASONING_EFFORT switches only that role to reasoning-model parameters."""

    async def test_response_generator_sends_reasoning_parameters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        fake = FakeAzureOpenAI(stream_text=["Our window is 30 days."])

        chunks = await _generate(fake)

        assert fake.params() == {
            "model": response_generator.AZURE_RG_MODEL,
            "max_completion_tokens": 25_000,
            "reasoning_effort": "low",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        assert chunks == ["Our window is 30 days."]

    async def test_response_generator_uses_the_configured_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "none")
        monkeypatch.setenv("AZURE_RG_MAX_COMPLETION_TOKENS", "12000")
        fake = FakeAzureOpenAI()

        await _generate(fake)

        assert fake.params()["max_completion_tokens"] == 12_000
        assert fake.params()["reasoning_effort"] == "none"

    async def test_a_fine_tuned_deployment_keeps_todays_parameters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A tenant's fine-tuned deployment is not the configured RG deployment (it is a gpt-4o-mini)."""
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        fake = FakeAzureOpenAI()

        await _generate(fake, model=FINE_TUNED_DEPLOYMENT)

        assert fake.params() == {"model": FINE_TUNED_DEPLOYMENT, "temperature": 0.3, "max_tokens": 1024, "stream": True}

    async def test_response_generator_logs_token_usage(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        fake = FakeAzureOpenAI(
            usage={
                "prompt_tokens": 3210,
                "completion_tokens": 654,
                "total_tokens": 3864,
                "completion_tokens_details": {"reasoning_tokens": 321},
            }
        )

        with caplog.at_level(logging.INFO, logger="src.agents.response_generator"):
            await _generate(fake)

        messages = [r.getMessage() for r in caplog.records if r.name == "src.agents.response_generator"]
        usage = [m for m in messages if "usage" in m]
        assert len(usage) == 1, messages
        for figure in ("3210", "654", "321", "stop"):
            assert figure in usage[0]

    async def test_co_pilot_follows_the_rg_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "medium")
        fake = FakeAzureOpenAI(content="Open Settings, then Team.")

        result = await _co_pilot(fake)

        assert fake.params() == {
            "model": co_pilot.AZURE_RG_MODEL,
            "max_completion_tokens": 25_000,
            "reasoning_effort": "medium",
        }
        assert result["response"] == "Open Settings, then Team."

    async def test_critic_sends_reasoning_parameters_and_keeps_json_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CR_REASONING_EFFORT", "none")
        fake = FakeAzureOpenAI(content='{"verdict": "approved", "flags": []}')

        result = await _criticize(fake)

        assert fake.params() == {
            "model": critic_supervisor.AZURE_CR_MODEL,
            "max_completion_tokens": 25_000,
            "reasoning_effort": "none",
            "response_format": JSON_MODE,
        }
        assert result["approved"] is True

    async def test_critic_fails_closed_when_reasoning_leaves_no_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CR_REASONING_EFFORT", "low")
        fake = FakeAzureOpenAI(content="", finish_reason="length")

        result = await _criticize(fake)

        assert result["approved"] is False
        assert result["safe_text"] == SAFE_FALLBACK_MESSAGE

    async def test_intent_classifier_and_escalation_share_the_ic_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_IC_REASONING_EFFORT", "none")
        monkeypatch.setenv("AZURE_IC_MAX_COMPLETION_TOKENS", "2000")
        fake = FakeAzureOpenAI(content='{"intent": "order_status", "confidence": 0.9}')

        await _classify(fake)
        await _escalate(fake)

        expected = {
            "model": intent_classifier.AZURE_IC_MODEL,
            "max_completion_tokens": 2000,
            "reasoning_effort": "none",
            "response_format": JSON_MODE,
        }
        assert fake.params(0) == expected
        assert fake.params(1) == {**expected, "model": escalation_handler.AZURE_IC_MODEL}

    async def test_one_role_does_not_move_the_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        fake = FakeAzureOpenAI(content='{"verdict": "approved", "flags": []}')

        await _classify(fake)
        await _escalate(fake)
        await _criticize(fake)

        assert [body["max_tokens"] for body in fake.bodies()] == [150, 200, 2048]
        assert all(body["temperature"] == 0.0 for body in fake.bodies())
        assert not any("reasoning_effort" in body for body in fake.bodies())

    def test_effort_is_trimmed_and_lowercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.agents.model_params import chat_completion_params

        monkeypatch.setenv("AZURE_CR_REASONING_EFFORT", "  None ")

        assert chat_completion_params("CR", temperature=0.0, max_tokens=2048) == {
            "max_completion_tokens": 25_000,
            "reasoning_effort": "none",
        }

    @pytest.mark.parametrize("raw", ["abc", "0", "-5", ""])
    def test_an_unusable_cap_falls_back_to_the_default(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        from src.agents.model_params import DEFAULT_MAX_COMPLETION_TOKENS, chat_completion_params

        monkeypatch.setenv("AZURE_IC_REASONING_EFFORT", "none")
        monkeypatch.setenv("AZURE_IC_MAX_COMPLETION_TOKENS", raw)

        params = chat_completion_params("IC", temperature=0.0, max_tokens=150)

        assert DEFAULT_MAX_COMPLETION_TOKENS == 25_000
        assert params["max_completion_tokens"] == DEFAULT_MAX_COMPLETION_TOKENS

    def test_a_blank_effort_means_a_non_reasoning_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.agents.model_params import chat_completion_params

        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "   ")

        assert chat_completion_params("RG", temperature=0.7, max_tokens=1024) == {
            "temperature": 0.7,
            "max_tokens": 1024,
        }


# ---------------------------------------------------------------------------
# RG empty-stream guard
# ---------------------------------------------------------------------------


class TestResponseGeneratorEmptyStream:
    """An empty reply never leaves the response generator."""

    async def test_a_length_finish_with_no_text_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reasoning used the whole cap before any visible output."""
        from src.agents.response_generator import EmptyReplyError

        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "high")
        fake = FakeAzureOpenAI(stream_text=[], finish_reason="length")

        with pytest.raises(EmptyReplyError, match="finish_reason=length"):
            await _generate(fake)

    async def test_an_empty_stream_raises_in_default_mode(self) -> None:
        from src.agents.response_generator import EmptyReplyError

        fake = FakeAzureOpenAI(stream_text=[], finish_reason="stop")

        with pytest.raises(EmptyReplyError):
            await _generate(fake)

    async def test_a_whitespace_only_stream_raises(self) -> None:
        from src.agents.response_generator import EmptyReplyError

        fake = FakeAzureOpenAI(stream_text=["  ", "\n"], finish_reason="stop")

        with pytest.raises(EmptyReplyError):
            await _generate(fake)

    async def test_a_cut_off_reply_with_text_is_still_delivered(self) -> None:
        """Text that reached the cap goes on to the Critic, which judges it."""
        fake = FakeAzureOpenAI(stream_text=["Our return window is"], finish_reason="length")

        assert await _generate(fake) == ["Our return window is"]

    async def test_a_whitespace_only_stream_sends_the_customer_nothing(self) -> None:
        from src.agents.response_generator import EmptyReplyError

        agent = ResponseGeneratorAgent(openai_client=FakeAzureOpenAI(stream_text=["  ", "\n"]).client())
        sent: list[str] = []

        with pytest.raises(EmptyReplyError):
            async for chunk in agent.generate_stream(**_stream_args()):
                sent.append(chunk)

        assert sent == []

    async def test_leading_whitespace_goes_out_with_the_first_text(self) -> None:
        fake = FakeAzureOpenAI(stream_text=["\n", " ", "Our window", " is 30 days."])

        chunks = await _generate(fake)

        assert chunks == ["\n Our window", " is 30 days."]

    async def test_process_raises_on_an_empty_reply(self) -> None:
        """The A2A path assembles the stream, so it cannot return an empty response either."""
        from src.agents.response_generator import EmptyReplyError

        agent = ResponseGeneratorAgent(openai_client=FakeAzureOpenAI(stream_text=[]).client())

        with pytest.raises(EmptyReplyError):
            await agent.process({"message": "Hi", "system_prompt": "RG prompt"}, {})


def _stream_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "customer_message": "What is the return window?",
        "intent": "return_request",
        "knowledge_context": "Returns are accepted within 30 days.",
        "system_prompt": "RG prompt",
        "model": response_generator.AZURE_RG_MODEL,
        "timeout_seconds": 12.5,
    }
    args.update(overrides)
    return args


class _SlowStream:
    """A reply stream that sends one chunk every ``delay`` seconds and records close()."""

    def __init__(self, delay: float, count: int) -> None:
        self.delay = delay
        self.count = count
        self.closed = False

    def __aiter__(self) -> Any:
        return self._chunks()

    async def _chunks(self) -> Any:
        for _ in range(self.count):
            await asyncio.sleep(self.delay)
            choice = SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="x"))
            yield SimpleNamespace(choices=[choice], usage=None)

    async def close(self) -> None:
        self.closed = True


async def _drain(stream: _SlowStream, timeout_seconds: float) -> list[str]:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=stream)
    agent = ResponseGeneratorAgent(openai_client=client)
    return [chunk async for chunk in agent.generate_stream(**_stream_args(timeout_seconds=timeout_seconds))]


class TestReasoningReplyDeadline:
    """The SDK timeout bounds each read; a reasoning reply is also bounded in wall-clock time."""

    async def test_a_reasoning_stream_stops_at_the_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.agents.response_generator import ReplyDeadlineError

        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        stream = _SlowStream(delay=0.05, count=200)

        with pytest.raises(ReplyDeadlineError):
            await _drain(stream, timeout_seconds=0.3)

        assert stream.closed

    async def test_a_stalled_reasoning_stream_stops_at_the_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.agents.response_generator import ReplyDeadlineError

        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        stream = _SlowStream(delay=30, count=1)

        with pytest.raises(ReplyDeadlineError):
            await asyncio.wait_for(_drain(stream, timeout_seconds=0.2), timeout=5)

        assert stream.closed

    async def test_a_reasoning_stream_within_the_deadline_is_delivered_and_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        stream = _SlowStream(delay=0, count=3)

        assert await _drain(stream, timeout_seconds=5) == ["x", "x", "x"]
        assert stream.closed

    async def test_default_mode_keeps_todays_behavior(self) -> None:
        """No wall-clock bound without a reasoning effort: max_tokens bounds the reply, as today."""
        stream = _SlowStream(delay=0.03, count=5)

        assert await _drain(stream, timeout_seconds=0.05) == ["x"] * 5

    def test_the_deadline_takes_the_mid_stream_error_path(self) -> None:
        """Not a PipelineTimeoutError, which the orchestrator re-raises without tokens_sent."""
        from src.agents.response_generator import ReplyDeadlineError
        from src.multi_tenant.pipeline_resilience import PipelineTimeoutError

        assert issubclass(ReplyDeadlineError, TimeoutError)
        assert not issubclass(ReplyDeadlineError, PipelineTimeoutError)
