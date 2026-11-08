# tests/unit/test_single_container_dispatch.py: RC-11 in-process IC, KR, escalation and analytics
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR2 (docs/beta-single-container-design.md, sections 3 and 8).

When SINGLE_CONTAINER_MODE is on, the intent classifier, knowledge retrieval, escalation handler
and analytics collector run inside the gateway process and never try transport or HTTP:

- IC and KR go through ``in_process.call_in_process``. A failure raises InProcessAgentError, a
  ServiceUnavailableError, so the orchestrator's existing handler sends the customer the fixed
  service_unavailable message, never the raw exception text. The classified code goes to logs
  and spans only.
- KR keeps the WI-4014 binding hydration and the SPEC-1860 gate, and adds tenant_id,
  preferences and the request headers to the payload.
- Escalation returns the default context on any failure; analytics drops silently.

With the flag off nothing changes: the guard tests at the end pass before and after.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from src.chat.models import StreamEventType
from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.pipeline_resilience import ServiceUnavailableError

FLAG = "SINGLE_CONTAINER_MODE"
PIPELINE_DIR = Path(__file__).resolve().parents[2] / "src" / "chat" / "pipeline"
TIERS_EXHAUSTED = "All transport tiers exhausted"
FIXED_SERVICE_MESSAGE = "A required service is temporarily unavailable. Please try again shortly."

DEFAULT_ESCALATION_CONTEXT = {
    "reason": "Customer requested human agent",
    "urgency": "medium",
    "context_summary": "Escalation handler unavailable: in-process call failed",
    "category": "general_inquiry",
    "model": "default",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgent:
    """An in-process agent that records its calls."""

    def __init__(self, result: Any = None, *, exc: BaseException | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[dict[str, Any], dict[str, str]]] = []

    async def process(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append((payload, headers))
        if self.exc is not None:
            raise self.exc
        return self.result


class _Unbuildable:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot build agent")


@dataclasses.dataclass
class _LegacyMcpConfig:
    server_name: str


def _pipeline(
    *,
    ic: Any = None,
    kr: Any = None,
    copilot: Any = None,
    registry: dict[str, Any] | None = None,
    openai_client: Any = None,
) -> ChatPipeline:
    """A pipeline without its services; only what dispatch reads."""
    pipeline = object.__new__(ChatPipeline)
    pipeline._ic_agent = ic
    pipeline._kr_agent = kr
    pipeline._copilot_agent = copilot
    pipeline._openai_client = openai_client
    pipeline._agent_urls = {}
    if registry is not None:
        pipeline.__dict__["_in_process_agents"] = dict(registry)
    return pipeline


def _block_other_tiers(pipeline: ChatPipeline, *, transport_up: bool = True) -> dict[str, AsyncMock]:
    """Every transport and HTTP call raises, so only an in-process call can succeed."""

    def failing(name: str) -> AsyncMock:
        return AsyncMock(side_effect=httpx.ConnectError(f"{name} must not be used"))

    mocks = {
        "transport": failing("transport"),
        "ic_http": failing("IC HTTP"),
        "kr_http": failing("KR HTTP"),
        "esc_http": failing("escalation HTTP"),
        "http_client": failing("HTTP client"),
    }
    pipeline._transport_available = MagicMock(return_value=transport_up)
    pipeline._call_via_transport = mocks["transport"]
    pipeline._call_intent_classifier_http = mocks["ic_http"]
    pipeline._call_knowledge_retrieval_http = mocks["kr_http"]
    pipeline._call_escalation_handler_http = mocks["esc_http"]
    pipeline._get_http_client = mocks["http_client"]
    return mocks


def _assert_not_awaited(mocks: dict[str, AsyncMock]) -> None:
    for name, mock in mocks.items():
        assert not mock.await_count, f"{name} was used in single-container mode"


def _set_request(pipeline: ChatPipeline, tenant_id: str = "t1", conversation_id: str = "c1") -> None:
    pipeline._current_tenant_id = tenant_id
    pipeline._current_conversation_id = conversation_id


def _preferences(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "retrieval_top_k": 7,
        "retrieval_vector_weight": 0.6,
        "retrieval_bm25_weight": 0.4,
        "retrieval_min_score": 0.2,
        "intent_source_mapping": {"order_status": "faq"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _budget() -> SimpleNamespace:
    return SimpleNamespace(
        stages=[SimpleNamespace(stage="intent-classifier", elapsed_ms=12.0, succeeded=True)],
        elapsed_ms=50.0,
    )


async def _fire_analytics(pipeline: ChatPipeline) -> None:
    await pipeline._fire_analytics(
        tenant_id="t1",
        conversation_id="c1",
        intent="order_status",
        budget=_budget(),
        trace=MagicMock(),
    )


@pytest.fixture
def beta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")


@pytest.fixture
def span() -> Any:
    """The span IC and KR dispatch open, as a MagicMock."""
    fake = MagicMock()
    with patch("src.multi_tenant.otel_tracing.trace_agent_operation", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def bindings() -> Any:
    """A binding service that never reaches Cosmos (WI-4014 hydration runs in KR dispatch)."""
    service = SimpleNamespace(_loaded_tenants=set(), load_tenant_bindings=AsyncMock())
    with patch("src.agents.plugins.bindings.SkillBindingService.get_instance", return_value=service):
        yield service


@pytest.fixture
def mcp_gate(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Legacy MCP configs exist; the SPEC-1860 gate (resolve_kr_with_fallback) decides what KR gets."""
    monkeypatch.setattr(
        "src.multi_tenant.mcp_client.resolve_mcp_configs",
        MagicMock(return_value=[_LegacyMcpConfig(server_name="legacy")]),
    )
    gate = MagicMock()
    monkeypatch.setattr("src.agents.plugins.kr_migration.resolve_kr_with_fallback", gate)
    return gate


# ---------------------------------------------------------------------------
# Intent classifier
# ---------------------------------------------------------------------------


async def test_ic_in_process_only(beta: None, span: MagicMock) -> None:
    ic = _FakeAgent({"intent": "order_status", "confidence": 0.9})
    pipeline = _pipeline(ic=ic)
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)

    result = await pipeline._call_intent_classifier("Where is my order?", "ic prompt")

    assert result == {"intent": "order_status", "confidence": 0.9}
    assert ic.calls == [
        (
            {"message": "Where is my order?", "system_prompt": "ic prompt"},
            {"x-tenant-id": "t1", "x-conversation-id": "c1"},
        )
    ]
    _assert_not_awaited(tiers)
    pipeline._transport_available.assert_not_called()
    span.set_attribute.assert_any_call("dispatch.mode", "in_process")
    span.end.assert_called_once()


@pytest.mark.parametrize(
    ("exc", "code", "recoverable"),
    [
        (Exception("Error code: 429 - Rate limit exceeded"), "rate_limited", True),
        (TimeoutError("read timed out"), "generation_timeout", True),
        (RuntimeError("customer text that must not leak"), "generation_error", True),
    ],
    ids=["rate_limited", "agent_timeout", "generic"],
)
async def test_ic_error_classified(beta: None, span: MagicMock, exc: Exception, code: str, recoverable: bool) -> None:
    from src.chat.pipeline.in_process import InProcessAgentError

    pipeline = _pipeline(ic=_FakeAgent(exc=exc))
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)

    with pytest.raises(InProcessAgentError) as info:
        await pipeline._call_intent_classifier("Hi", "ic prompt")

    error = info.value
    assert isinstance(error, ServiceUnavailableError)
    assert error.service_name == "intent-classifier"
    assert error.code == code
    assert error.recoverable is recoverable
    assert error.__cause__ is exc
    message = str(error)
    assert "intent-classifier" in message
    assert code in message
    assert "circuit breaker" not in message
    assert str(exc) not in message
    span.set_attribute.assert_any_call("dispatch.error_code", code)
    span.set_status.assert_called_once()
    _assert_not_awaited(tiers)


@pytest.mark.parametrize(
    ("agent", "code"),
    [(None, "agent_missing"), (_FakeAgent("intent: order_status"), "invalid_result")],
    ids=["agent_missing", "invalid_result"],
)
async def test_ic_missing_agent_or_bad_result_is_classified(beta: None, span: MagicMock, agent: Any, code: str) -> None:
    from src.chat.pipeline.in_process import InProcessAgentError

    pipeline = _pipeline(ic=agent)
    tiers = _block_other_tiers(pipeline)

    with pytest.raises(InProcessAgentError) as info:
        await pipeline._call_intent_classifier("Hi", "ic prompt")

    assert info.value.service_name == "intent-classifier"
    assert info.value.code == code
    span.set_attribute.assert_any_call("dispatch.error_code", code)
    _assert_not_awaited(tiers)


async def test_in_process_cancellation_propagates(beta: None) -> None:
    """A client disconnect or a stage timeout cancels the call; nothing converts it into an error."""
    pipeline = _pipeline(ic=_FakeAgent(exc=asyncio.CancelledError()))
    _block_other_tiers(pipeline)

    with pytest.raises(asyncio.CancelledError):
        await pipeline._call_intent_classifier("Hi", "ic prompt")


# ---------------------------------------------------------------------------
# End to end: the customer sees the fixed message, not the exception
# ---------------------------------------------------------------------------


def _streaming_pipeline(session: AsyncMock) -> ChatPipeline:
    """A ChatPipeline with mocked services (tests/chat/test_sse_error_handling.py pattern)."""
    from src.multi_tenant.system_prompt_builder import AgentRole

    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    return ChatPipeline(session=session, prompt_builder=prompt_builder, profile_service=AsyncMock())


async def test_execute_emits_fixed_service_unavailable(beta: None) -> None:
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

    pipeline = _streaming_pipeline(session)
    ic = _FakeAgent(exc=Exception("Error code: 429 - Rate limit exceeded"))
    pipeline._ic_agent = ic

    started = time.monotonic()
    with (
        patch.object(pipeline, "_transport_available", return_value=False),
        patch.object(
            pipeline,
            "_call_intent_classifier_http",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("no IC container"),
        ),
        patch.object(
            pipeline,
            "_call_knowledge_retrieval",
            new_callable=AsyncMock,
            return_value={"context": "some context", "sources": []},
        ),
        patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
        patch.object(pipeline, "_fire_analytics", new_callable=AsyncMock),
    ):
        events = [
            event
            async for event in pipeline.execute("t-001", "conv-001", "Hello?", tenant=tenant, preferences=preferences)
        ]

    assert time.monotonic() - started < 5.0
    errors = [event for event in events if event.event == StreamEventType.ERROR]
    assert len(errors) == 1
    assert errors[0].data["code"] == "service_unavailable"
    assert errors[0].data["stage"] == "intent-classifier"
    assert errors[0].data["message"] == FIXED_SERVICE_MESSAGE
    for event in events:
        text = str(event.data)
        assert "An unexpected error occurred" not in text
        assert "429" not in text
    assert events[-1].event == StreamEventType.DONE
    assert len(ic.calls) == 1
    session.add_ai_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Knowledge retrieval
# ---------------------------------------------------------------------------


async def test_kr_in_process_gate_hydration_payload(
    beta: None, span: MagicMock, bindings: SimpleNamespace, mcp_gate: MagicMock
) -> None:
    mcp_gate.return_value = {"source": "legacy", "mcp_configs": [{"server_name": "gated"}]}
    kr = _FakeAgent({"context": "Returns within 30 days", "sources": []})
    pipeline = _pipeline(kr=kr)
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)
    pipeline._current_preferences = _preferences()
    pipeline._current_tenant = SimpleNamespace(shopify_shop_domain="shop.myshopify.com")

    result = await pipeline._call_knowledge_retrieval("Return policy?", "general_inquiry", "kr prompt")

    assert result == {"context": "Returns within 30 days", "sources": []}
    bindings.load_tenant_bindings.assert_awaited_once_with("t1")
    mcp_gate.assert_called_once_with("t1", [{"server_name": "legacy"}])
    [(payload, headers)] = kr.calls
    assert payload == {
        "message": "Return policy?",
        "intent": "general_inquiry",
        "system_prompt": "kr prompt",
        "mcp_configs": [{"server_name": "gated"}],
        "tenant_shop_domain": "shop.myshopify.com",
        "tenant_id": "t1",
        "preferences": {
            "retrieval_top_k": 7,
            "retrieval_vector_weight": 0.6,
            "retrieval_bm25_weight": 0.4,
            "retrieval_min_score": 0.2,
            "intent_source_mapping": {"order_status": "faq"},
        },
    }
    assert headers == {"x-tenant-id": "t1", "x-conversation-id": "c1"}
    span.set_attribute.assert_any_call("dispatch.mode", "in_process")
    span.set_attribute.assert_any_call("kr.mcp_source", "legacy")
    _assert_not_awaited(tiers)
    pipeline._transport_available.assert_not_called()


async def test_kr_blocked_sends_no_mcp_configs(beta: None, span: MagicMock, mcp_gate: MagicMock) -> None:
    mcp_gate.return_value = {"source": "error", "error": "binding_enforced tenant has no KR binding"}
    kr = _FakeAgent({"context": "", "sources": []})
    pipeline = _pipeline(kr=kr)
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)
    pipeline._current_preferences = _preferences()
    pipeline._current_tenant = SimpleNamespace(shopify_shop_domain="shop.myshopify.com")

    await pipeline._call_knowledge_retrieval("Return policy?", "general_inquiry", "kr prompt")

    mcp_gate.assert_called_once()
    [(payload, headers)] = kr.calls
    assert "mcp_configs" not in payload
    assert "tenant_shop_domain" not in payload
    assert payload["tenant_id"] == "t1"
    assert headers["x-tenant-id"] == "t1"
    span.set_attribute.assert_any_call("kr.mcp_source", "none")
    _assert_not_awaited(tiers)


async def test_kr_error_classified(beta: None) -> None:
    from src.chat.pipeline.in_process import InProcessAgentError

    pipeline = _pipeline(kr=_FakeAgent(exc=Exception("Error code: 503 - Service Unavailable")))
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)

    with pytest.raises(InProcessAgentError) as info:
        await pipeline._call_knowledge_retrieval("Hi", "general_inquiry", "kr prompt")

    assert info.value.service_name == "knowledge-retrieval"
    assert info.value.code == "model_overloaded"
    _assert_not_awaited(tiers)


@pytest.mark.parametrize(
    "prefs",
    [
        None,
        _preferences(),
        _preferences(retrieval_top_k=None, retrieval_min_score=None, intent_source_mapping={}),
        _preferences(retrieval_vector_weight=0.0, retrieval_bm25_weight=None, intent_source_mapping=None),
    ],
    ids=["none", "all_set", "some_unset", "zero_weight"],
)
async def test_kr_preferences_match_the_direct_path(prefs: Any) -> None:
    """kr_preferences is a copy of the dict _call_knowledge_retrieval_direct builds, not an extraction."""
    from src.chat.pipeline.in_process import kr_preferences

    kr = _FakeAgent({"context": "", "sources": []})
    pipeline = _pipeline(kr=kr)
    _set_request(pipeline)
    pipeline._current_preferences = prefs

    await pipeline._call_knowledge_retrieval_direct("Hi", "general_inquiry", "kr prompt")

    [(direct_payload, _)] = kr.calls
    assert kr_preferences(prefs) == direct_payload["preferences"]


async def test_concurrent_tasks_keep_their_tenant_headers(beta: None) -> None:
    """One pipeline serves every request; each task sends KR its own tenant (request_state.py)."""
    a_inside, b_inside = asyncio.Event(), asyncio.Event()
    entered = {"tA": a_inside, "tB": b_inside}

    class _InterleavingKr:
        async def process(self, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
            entered[payload["tenant_id"]].set()
            await a_inside.wait()
            await b_inside.wait()  # both requests are inside the agent before either returns
            return {"header": headers["x-tenant-id"], "payload": payload["tenant_id"]}

    pipeline = _pipeline(kr=_InterleavingKr())
    _block_other_tiers(pipeline)

    async def request(tenant_id: str) -> dict[str, Any]:
        _set_request(pipeline, tenant_id, f"conv-{tenant_id}")
        return await pipeline._call_knowledge_retrieval("Hi", "general_inquiry", "kr prompt")

    seen = await asyncio.wait_for(
        asyncio.gather(asyncio.create_task(request("tA")), asyncio.create_task(request("tB"))),
        timeout=5,
    )

    assert seen == [{"header": "tA", "payload": "tA"}, {"header": "tB", "payload": "tB"}]


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


async def test_escalation_in_process_and_default_on_failure(beta: None) -> None:
    context = {
        "reason": "Wants a refund",
        "urgency": "high",
        "context_summary": "Order 1001 arrived damaged",
        "category": "refund_request",
        "model": "gpt-4o-mini",
    }
    escalation = _FakeAgent(context)
    pipeline = _pipeline(registry={"escalation-handler": escalation})
    tiers = _block_other_tiers(pipeline)
    _set_request(pipeline)

    assert await pipeline._call_escalation_handler("I want a human", "esc prompt") == context
    assert escalation.calls == [
        (
            {"message": "I want a human", "system_prompt": "esc prompt"},
            {"x-tenant-id": "t1", "x-conversation-id": "c1"},
        )
    ]

    pipeline.__dict__["_in_process_agents"]["escalation-handler"] = _FakeAgent(exc=RuntimeError("boom"))
    assert await pipeline._call_escalation_handler("I want a human", "esc prompt") == DEFAULT_ESCALATION_CONTEXT

    _assert_not_awaited(tiers)
    pipeline._transport_available.assert_not_called()


@pytest.mark.parametrize("failure", ["construction_fails", "invalid_result", "rate_limited"])
async def test_escalation_never_raises(beta: None, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    if failure == "construction_fails":
        monkeypatch.setattr("src.agents.escalation_handler.EscalationHandlerAgent", _Unbuildable)
        pipeline = _pipeline()
    elif failure == "invalid_result":
        pipeline = _pipeline(registry={"escalation-handler": _FakeAgent(["not", "a", "dict"])})
    else:
        pipeline = _pipeline(registry={"escalation-handler": _FakeAgent(exc=Exception("Error code: 429"))})
    tiers = _block_other_tiers(pipeline)

    assert await pipeline._call_escalation_handler("I want a human", "esc prompt") == DEFAULT_ESCALATION_CONTEXT
    _assert_not_awaited(tiers)


async def test_registry_builds_escalation_and_analytics_agents(beta: None) -> None:
    from src.agents.analytics_collector import AnalyticsCollectorAgent
    from src.agents.escalation_handler import EscalationHandlerAgent
    from src.chat.pipeline.in_process import get_in_process_agent

    client = MagicMock()
    pipeline = _pipeline(openai_client=client)

    escalation = get_in_process_agent(pipeline, "escalation-handler")
    analytics = get_in_process_agent(pipeline, "analytics-collector")

    assert isinstance(escalation, EscalationHandlerAgent)
    assert escalation._openai_client is client
    assert isinstance(analytics, AnalyticsCollectorAgent)
    assert get_in_process_agent(pipeline, "escalation-handler") is escalation
    assert get_in_process_agent(pipeline, "analytics-collector") is analytics
    assert get_in_process_agent(None, "escalation-handler") is None
    for name in ("_cr_agent", "_esc_agent", "_an_agent"):
        assert not hasattr(pipeline, name), name


async def test_escalation_with_real_agent_and_no_client_gives_its_default(beta: None) -> None:
    """The registry's EscalationHandlerAgent without a client answers itself and never raises."""
    from src.agents.escalation_handler import EscalationHandlerAgent
    from src.chat.pipeline.in_process import get_in_process_agent

    pipeline = _pipeline(openai_client=None)
    tiers = _block_other_tiers(pipeline)

    result = await pipeline._call_escalation_handler("I want a human", "esc prompt")

    assert result["reason"] == "Customer requested human agent"
    assert result["context_summary"] == ""  # the agent's own answer, not the unavailable default
    assert result["model"] != "default"
    assert isinstance(get_in_process_agent(pipeline, "escalation-handler"), EscalationHandlerAgent)
    _assert_not_awaited(tiers)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


async def test_analytics_in_process_never_raises(beta: None) -> None:
    analytics = _FakeAgent({"collected": True, "event_count": 1})
    pipeline = _pipeline(registry={"analytics-collector": analytics})
    tiers = _block_other_tiers(pipeline)

    with patch("src.multi_tenant.agntcy_sdk_integration._transport", None):
        await _fire_analytics(pipeline)

        assert analytics.calls == [
            (
                {
                    "tenant_id": "t1",
                    "conversation_id": "c1",
                    "intent": "order_status",
                    "stages": [{"stage": "intent-classifier", "elapsed_ms": 12.0, "succeeded": True}],
                    "total_latency_ms": 50.0,
                },
                {"x-tenant-id": "t1", "x-conversation-id": "c1"},
            )
        ]

        pipeline.__dict__["_in_process_agents"]["analytics-collector"] = _FakeAgent(exc=RuntimeError("boom"))
        assert await _fire_analytics(pipeline) is None

        real = _pipeline()
        real_tiers = _block_other_tiers(real)
        await _fire_analytics(real)

    _assert_not_awaited(tiers)
    _assert_not_awaited(real_tiers)
    assert type(real.__dict__["_in_process_agents"]["analytics-collector"]).__name__ == "AnalyticsCollectorAgent"


# ---------------------------------------------------------------------------
# Guards: flag off is unchanged
# ---------------------------------------------------------------------------


async def test_flag_off_ladders_unchanged() -> None:
    """IC, KR and co-pilot still end in 503, escalation in its default, analytics in a silent drop."""
    in_process = {name: _FakeAgent({"unused": True}) for name in ("ic", "kr", "copilot", "esc", "an")}
    pipeline = _pipeline(
        ic=in_process["ic"],
        kr=in_process["kr"],
        copilot=in_process["copilot"],
        registry={"escalation-handler": in_process["esc"], "analytics-collector": in_process["an"]},
    )
    tiers = _block_other_tiers(pipeline, transport_up=False)

    calls = {
        "intent-classifier": lambda: pipeline._call_intent_classifier("Hi", "prompt"),
        "knowledge-retrieval": lambda: pipeline._call_knowledge_retrieval("Hi", "general_inquiry", "prompt"),
        "co-pilot": lambda: pipeline._call_co_pilot("Hi", "prompt"),
    }
    for agent_name, call in calls.items():
        with pytest.raises(HTTPException) as info:
            await call()
        assert info.value.status_code == 503, agent_name
        assert TIERS_EXHAUSTED in info.value.detail, agent_name
        assert agent_name in info.value.detail

    escalation = await pipeline._call_escalation_handler("I want a human", "prompt")
    assert escalation["model"] == "default"
    assert escalation["context_summary"].startswith("Escalation handler unavailable")
    assert "in-process" not in escalation["context_summary"]

    with patch("src.multi_tenant.agntcy_sdk_integration._transport", None):
        assert await _fire_analytics(pipeline) is None

    tiers["ic_http"].assert_awaited_once()
    tiers["kr_http"].assert_awaited_once()
    tiers["esc_http"].assert_awaited_once()
    tiers["http_client"].assert_awaited_once()  # the analytics HTTP tier, still tried
    tiers["transport"].assert_not_awaited()
    for name, agent in in_process.items():
        assert agent.calls == [], f"{name} ran in-process with the flag off"


def test_in_process_module_imported_only_inside_beta_branches() -> None:
    """Default deployments never load in_process.py: no module-level import in the dispatch mixins."""
    for filename in ("agent_dispatch.py", "critic_escalation.py", "analytics.py"):
        tree = ast.parse((PIPELINE_DIR / filename).read_text(encoding="utf-8"))
        module_level = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("in_process")
        ]
        module_level += [
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name.endswith("in_process")
        ]
        assert module_level == [], filename


async def test_in_process_failures_log_no_exception_text(caplog):
    """An agent exception can carry customer text or an SDK request body; logs get the code and type only (Codex review of #152)."""
    import logging

    from src.chat.pipeline.in_process import InProcessAgentError, call_in_process, validate_with_in_process_critic

    secret = "card 4111 1111 1111 1111 for jo.customer@example.com"

    class _Leaky:
        async def process(self, payload, headers):
            raise RuntimeError(secret)

    pipeline = MagicMock()
    pipeline.__dict__["_in_process_agents"] = {"critic-supervisor": _Leaky()}
    with caplog.at_level(logging.DEBUG, logger="src.chat.pipeline.in_process"):
        with pytest.raises(InProcessAgentError):
            await call_in_process(pipeline, "intent-classifier", _Leaky(), {"message": "hi"})
        approved, _text, _result = await validate_with_in_process_critic(pipeline, "t1", "c1", "reply", "msg", None)
    assert approved is False
    records = [r for r in caplog.records if r.name == "src.chat.pipeline.in_process"]
    assert records, "the failures should still be logged"
    for record in records:
        assert record.exc_info is None
        assert secret not in record.getMessage()
