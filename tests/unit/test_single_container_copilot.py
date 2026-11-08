# tests/unit/test_single_container_copilot.py: RC-11 in-process co-pilot with a beta-only 15 s stage budget
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR7 (docs/beta-single-container-design.md, sections 3, 6 and 8).

When SINGLE_CONTAINER_MODE is on:

- ``_call_co_pilot`` runs the gateway's own CoPilotAgent through ``in_process.call_in_process``
  and never tries transport or HTTP. A failure raises InProcessAgentError, which
  ``_handle_co_pilot`` turns into its fixed fallback text.
- ``execute`` builds its budget from ``in_process.SINGLE_CONTAINER_STAGE_BUDGETS_MS``, which gives
  the "co-pilot" stage 15 s. STAGE_BUDGETS_MS has no co-pilot key, so without the overlay
  execute_with_budget falls back to 1000 ms, which an in-process co-pilot (embedding, vector
  search, BM25 and a completion) always exceeds. STAGE_BUDGETS_MS and its 43_000 sum are unchanged.

In every mode, the co-pilot's BM25 scoring over every admin document runs through
asyncio.to_thread, with results identical to the inline loop it replaces.

With the flag off nothing else changes: PipelineTimeoutBudget gets no overlay, and the co-pilot
ladder still ends in 503 (tests/unit/test_single_container_dispatch.py::test_flag_off_ladders_unchanged).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.agents import co_pilot
from src.chat.models import StreamEvent, StreamEventType
from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.pipeline_resilience import (
    STAGE_BUDGETS_MS,
    PipelineTimeoutBudget,
    ServiceUnavailableError,
)

FLAG = "SINGLE_CONTAINER_MODE"
PIPELINE_DIR = Path(__file__).resolve().parents[2] / "src" / "chat" / "pipeline"
COPILOT_BUDGET_MS = 15_000
DEFAULT_STAGE_BUDGET_MS = 1_000  # execute_with_budget's fallback for a stage with no key
MESSAGE_ID = "msg-copilot-beta"
COPILOT_RESULT = {
    "response": "Open Settings, then Widget, to change the launcher colour.",
    "sources": [{"id": "doc-widget", "title": "Widget settings", "category": "widget"}],
    "model": "gpt-4o",
    "tokens_input": 120,
    "tokens_output": 40,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgent:
    """An in-process agent that records its calls, optionally after a delay."""

    def __init__(self, result: Any = None, *, exc: BaseException | None = None, delay_s: float = 0.0) -> None:
        self.result = result
        self.exc = exc
        self.delay_s = delay_s
        self.calls: list[tuple[dict[str, Any], dict[str, str]]] = []

    async def process(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        self.calls.append((payload, headers))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.exc is not None:
            raise self.exc
        return self.result


def _pipeline(copilot: Any = None) -> ChatPipeline:
    """A pipeline without its services; only what co-pilot dispatch reads."""
    pipeline = object.__new__(ChatPipeline)
    pipeline._copilot_agent = copilot
    pipeline._agent_urls = {"co-pilot": "http://co-pilot.invalid"}
    pipeline._current_tenant_id = "t1"
    pipeline._current_conversation_id = "c1"
    return pipeline


def _block_other_tiers(pipeline: ChatPipeline) -> dict[str, Any]:
    """Transport is up but every transport and HTTP call raises, so only an in-process call can succeed."""

    def failing(name: str) -> AsyncMock:
        return AsyncMock(side_effect=httpx.ConnectError(f"{name} must not be used"))

    mocks = {"transport": failing("transport"), "http_client": failing("HTTP client")}
    pipeline._transport_available = MagicMock(return_value=True)
    pipeline._call_via_transport = mocks["transport"]
    pipeline._get_http_client = mocks["http_client"]
    return mocks


def _assert_not_awaited(mocks: dict[str, AsyncMock]) -> None:
    for name, mock in mocks.items():
        assert not mock.await_count, f"{name} was used in single-container mode"


@pytest.fixture
def beta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")


@pytest.fixture
def span() -> Any:
    """The span co-pilot dispatch opens, as a MagicMock."""
    fake = MagicMock()
    with patch("src.multi_tenant.otel_tracing.trace_agent_operation", return_value=fake):
        yield fake


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_copilot_in_process_only(beta: None, span: MagicMock) -> None:
    agent = _FakeAgent(COPILOT_RESULT)
    pipeline = _pipeline(agent)
    tiers = _block_other_tiers(pipeline)
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]

    result = await pipeline._call_co_pilot(
        "How do I change the widget colour?",
        "co-pilot prompt",
        conversation_history=history,
        team_member_role="viewer",
    )

    assert result == COPILOT_RESULT
    assert agent.calls == [
        (
            {
                "message": "How do I change the widget colour?",
                "system_prompt": "co-pilot prompt",
                "conversation_history": history,
                "team_member_role": "viewer",
                "tenant_id": "t1",
            },
            {"x-tenant-id": "t1", "x-conversation-id": "c1"},
        )
    ]
    _assert_not_awaited(tiers)
    pipeline._transport_available.assert_not_called()
    span.set_attribute.assert_any_call("dispatch.mode", "in_process")
    span.end.assert_called_once()


@pytest.mark.parametrize(
    ("agent", "code"),
    [
        (_FakeAgent(exc=Exception("Error code: 429 - Rate limit exceeded")), "rate_limited"),
        (None, "agent_missing"),
        (_FakeAgent("Open Settings"), "invalid_result"),
    ],
    ids=["rate_limited", "agent_missing", "invalid_result"],
)
async def test_copilot_error_classified(beta: None, span: MagicMock, agent: Any, code: str) -> None:
    from src.chat.pipeline.in_process import InProcessAgentError

    pipeline = _pipeline(agent)
    tiers = _block_other_tiers(pipeline)

    with pytest.raises(InProcessAgentError) as info:
        await pipeline._call_co_pilot("Hi", "co-pilot prompt")

    assert isinstance(info.value, ServiceUnavailableError)
    assert info.value.service_name == "co-pilot"
    assert info.value.code == code
    span.set_attribute.assert_any_call("dispatch.error_code", code)
    span.set_status.assert_called_once()
    span.end.assert_called_once()
    _assert_not_awaited(tiers)


# ---------------------------------------------------------------------------
# Budget overlay
# ---------------------------------------------------------------------------


class _BudgetBuilt(Exception):
    """Stops execute() right after it builds its budget."""


async def _budget_call(pipeline: ChatPipeline) -> Any:
    """The call execute() makes to PipelineTimeoutBudget, patched in the orchestrator namespace."""
    budget_cls = MagicMock(side_effect=_BudgetBuilt)
    with patch("src.chat.pipeline.orchestrator.PipelineTimeoutBudget", budget_cls):
        events = pipeline.execute("t1", "c1", "Hi", tenant=MagicMock(), preferences=MagicMock())
        with pytest.raises(_BudgetBuilt):
            await events.__anext__()
    budget_cls.assert_called_once()
    return budget_cls.call_args


async def test_budget_overlay_only_when_on(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _pipeline()

    for off_value in (None, "1", "false"):
        if off_value is None:
            monkeypatch.delenv(FLAG, raising=False)
        else:
            monkeypatch.setenv(FLAG, off_value)
        call = await _budget_call(pipeline)
        assert call.args == (), off_value
        assert call.kwargs.get("stage_budgets_ms") is None, off_value

    monkeypatch.setenv(FLAG, "true")
    call = await _budget_call(pipeline)
    overlay = call.kwargs["stage_budgets_ms"]
    assert overlay == {**STAGE_BUDGETS_MS, "co-pilot": COPILOT_BUDGET_MS}
    assert overlay is not STAGE_BUDGETS_MS

    # The default budgets, and the sum tests in test_pipeline_resilience.py and
    # test_performance.py, are unchanged.
    assert "co-pilot" not in STAGE_BUDGETS_MS
    assert sum(STAGE_BUDGETS_MS.values()) == 43_000

    # A real budget built the same way gives co-pilot 15 s and every other stage its default.
    beta_budget = PipelineTimeoutBudget(*call.args, **call.kwargs)
    default_budget = PipelineTimeoutBudget()
    assert beta_budget._stage_budgets.get("co-pilot", DEFAULT_STAGE_BUDGET_MS) == COPILOT_BUDGET_MS
    assert default_budget._stage_budgets.get("co-pilot", DEFAULT_STAGE_BUDGET_MS) == DEFAULT_STAGE_BUDGET_MS
    for stage, budget_ms in STAGE_BUDGETS_MS.items():
        assert beta_budget._stage_budgets[stage] == budget_ms


# ---------------------------------------------------------------------------
# End to end: a beta co-pilot turn slower than the 1000 ms default completes
# ---------------------------------------------------------------------------


async def _collect(gen: Any) -> list[StreamEvent]:
    return [event async for event in gen]


async def test_execute_beta_copilot_turn_runs_in_process_with_15s_budget(beta: None) -> None:
    from src.multi_tenant.cosmos_schema import TenantTier
    from src.multi_tenant.system_prompt_builder import AgentRole

    session = AsyncMock()
    session.set_pii_scrubber = MagicMock()
    session.add_ai_message.return_value = MESSAGE_ID
    session.get_conversation.return_value = SimpleNamespace(turn_count=3)
    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    pipeline = ChatPipeline(session=session, prompt_builder=prompt_builder, profile_service=AsyncMock())

    # Longer than the 1000 ms default stage budget, well inside the 15 s overlay.
    agent = _FakeAgent(COPILOT_RESULT, delay_s=(DEFAULT_STAGE_BUDGET_MS + 150) / 1000)
    pipeline._copilot_agent = agent
    tiers = _block_other_tiers(pipeline)
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
                "t-beta",
                "conv-beta",
                "How do I change the widget colour?",
                tenant=tenant,
                preferences=preferences,
                trace_id="trace-beta",
                team_member_role="admin",
            )
        )

    assert [e for e in events if e.event == StreamEventType.ERROR] == []
    tokens = [e.data["text"] for e in events if e.event == StreamEventType.TOKEN]
    assert tokens == [COPILOT_RESULT["response"]]
    validated = [e for e in events if e.event == StreamEventType.VALIDATED]
    assert [e.data["message_id"] for e in validated] == [MESSAGE_ID]
    assert events[-1].event == StreamEventType.DONE
    assert events[-1].data["turn_count"] == 3

    [(payload, headers)] = agent.calls
    assert payload["tenant_id"] == "t-beta"
    assert payload["team_member_role"] == "admin"
    assert headers == {"x-tenant-id": "t-beta", "x-conversation-id": "conv-beta"}
    assert session.add_ai_message.await_args.kwargs["critic_passed"] is None  # co-pilot still bypasses the Critic
    _assert_not_awaited(tiers)


# ---------------------------------------------------------------------------
# Co-pilot BM25 off the event loop
# ---------------------------------------------------------------------------


def _admin_docs() -> list[dict[str, Any]]:
    """Admin documents with an unambiguous ranking, a tie and a non-matching document."""
    rows = [
        ("doc-widget", "Widget appearance", "Change the widget colour, launcher icon and position.", "widget"),
        ("doc-billing", "Billing and invoices", "Download invoices and update the card on file.", "billing"),
        ("doc-colour", "Brand colour", "The widget uses your brand colour by default.", "widget"),
        ("doc-team", "Team members", "Invite team members and change their role.", "team"),
        ("doc-tie-a", "Launcher", "Widget launcher settings.", "widget"),
        ("doc-tie-b", "Launcher", "Widget launcher settings.", "widget"),
        ("doc-kb", "Knowledge base", "Upload articles to the knowledge base.", "kb"),
    ]
    return [
        {"id": doc_id, "title": title, "content": content, "document_category": category}
        for doc_id, title, content, category in rows
    ]


def _reference_ranking(query: str, all_docs: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """A copy of the scoring loop _bm25_search ran inline before this change (co_pilot.py).

    _tokenize and _bm25_score are unchanged by it, so the copy calls them.
    """
    query_tokens = co_pilot._tokenize(query)
    if not query_tokens:
        return []
    doc_token_lists = [co_pilot._tokenize(f"{doc.get('title', '')} {doc.get('content', '')}") for doc in all_docs]
    total_docs = len(all_docs)
    avg_doc_len = sum(len(t) for t in doc_token_lists) / max(total_docs, 1)
    df: dict[str, int] = {}
    for tokens in doc_token_lists:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    scored = []
    for doc, tokens in zip(all_docs, doc_token_lists):
        score = co_pilot._bm25_score(query_tokens, tokens, df, total_docs, avg_doc_len)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]


async def test_copilot_bm25_off_loop_identical_results() -> None:
    docs = _admin_docs()
    repo = MagicMock()
    repo.list_all_lightweight = AsyncMock(side_effect=lambda: copy.deepcopy(docs))
    agent = co_pilot.CoPilotAgent(openai_client=None, admin_doc_repo=repo)

    loop_thread = threading.get_ident()
    score_threads: list[int] = []
    real_score = co_pilot._bm25_score

    def recording_score(*args: Any) -> float:
        score_threads.append(threading.get_ident())
        return real_score(*args)

    cases = [
        ("How do I change the widget colour?", 3),
        ("widget launcher", 10),  # the tied documents keep their input order
        ("invoices", 5),
        ("the and of", 5),  # only stop words: no query tokens
        ("nothing matches zzz", 5),
    ]
    with patch.object(co_pilot, "_bm25_score", recording_score):
        results = {query: await agent._bm25_search(query, top_k=top_k) for query, top_k in cases}

    for query, top_k in cases:
        assert results[query] == _reference_ranking(query, docs, top_k), query
    assert [d["id"] for d in results["How do I change the widget colour?"]] == [
        "doc-widget",
        "doc-colour",
        "doc-team",
    ]
    assert [d["id"] for d in results["widget launcher"]][:2] == ["doc-tie-a", "doc-tie-b"]
    assert [d["id"] for d in results["invoices"]] == ["doc-billing"]
    assert results["the and of"] == []
    assert results["nothing matches zzz"] == []

    assert repo.list_all_lightweight.await_count == len(cases)  # still awaited on the loop
    assert score_threads, "no document was scored"
    assert all(t != loop_thread for t in score_threads), "co-pilot BM25 scoring ran on the event loop thread"


async def test_copilot_bm25_no_documents() -> None:
    repo = MagicMock()
    repo.list_all_lightweight = AsyncMock(return_value=[])
    agent = co_pilot.CoPilotAgent(openai_client=None, admin_doc_repo=repo)

    assert await agent._bm25_search("widget colour") == []
    repo.list_all_lightweight.assert_awaited_once()


# ---------------------------------------------------------------------------
# Guard: default deployments never load in_process.py
# ---------------------------------------------------------------------------


def test_orchestrator_imports_in_process_only_inside_beta_branch() -> None:
    tree = ast.parse((PIPELINE_DIR / "orchestrator.py").read_text(encoding="utf-8"))
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
    assert module_level == []
