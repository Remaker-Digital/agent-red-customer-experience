# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""In-process agents for the RC-11 single-container beta mode.

When SINGLE_CONTAINER_MODE is on (constants.single_container_mode), agents run
inside the gateway process instead of in their own containers. Only the
flagged branches import this module, so default deployments never load it.

It holds:
- in_process_headers: the A2A headers for the current request;
- get_in_process_agent: a lazy registry of agents, one set per pipeline;
- validate_with_in_process_critic: the fail-closed Critic adapter;
- call_in_process and InProcessAgentError: the classified call that IC, KR,
  co-pilot and escalation dispatch use;
- kr_preferences: the retrieval preferences KR receives;
- SINGLE_CONTAINER_STAGE_BUDGETS_MS: the stage budgets a beta turn uses;
- single_container_readiness: the checks /ready runs in beta mode.

Agent classes are imported inside the factory, and only as src.agents.<agent>,
never src.agents.containers.

Design: docs/beta-single-container-design.md, sections 3, 4 and 5.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from src.chat.pipeline.constants import _classify_openai_error
from src.multi_tenant.critic_policy import (
    SAFE_FALLBACK_MESSAGE,
    CriticBlockReason,
    CriticResult,
    CriticVerdict,
)
from src.multi_tenant.pipeline_resilience import STAGE_BUDGETS_MS, ServiceUnavailableError

logger = logging.getLogger(__name__)

CRITIC_AGENT_ID = "critic-supervisor"
ESCALATION_AGENT_ID = "escalation-handler"
ANALYTICS_AGENT_ID = "analytics-collector"
CRITIC_INSTANCE = "in-process"
# Floor for the Critic timeout, so a nearly spent pipeline budget still gives
# the Critic a real chance. The reply fails closed either way.
CRITIC_MIN_TIMEOUT_S = 0.5

# STAGE_BUDGETS_MS has no "co-pilot" key, so execute_with_budget gives co-pilot
# its 1000 ms default, which an in-process co-pilot (embedding, vector search,
# BM25 and a completion) always exceeds. A beta turn uses this overlay instead;
# STAGE_BUDGETS_MS itself is unchanged.
SINGLE_CONTAINER_STAGE_BUDGETS_MS: dict[str, int] = {**STAGE_BUDGETS_MS, "co-pilot": 15_000}

_REGISTRY_KEY = "_in_process_agents"

# The pipeline's own agents (orchestrator._init_agents) that a beta turn uses.
_CORE_AGENT_ATTRS = ("_ic_agent", "_kr_agent", "_rg_agent", "_copilot_agent")
# The agents get_in_process_agent builds; readiness builds them too.
_READINESS_AGENT_IDS = (CRITIC_AGENT_ID, ESCALATION_AGENT_ID, ANALYTICS_AGENT_ID)
# get_sdk_status reports each of these as None when no endpoint is configured.
_TRANSPORT_ENDPOINT_KEYS = ("slim_endpoint", "nats_endpoint")


def in_process_headers(pipeline: Any) -> dict[str, str]:
    """Headers for an in-process agent call, from the current request.

    The pipeline keeps the request's ids in RequestScoped attributes
    (request_state.py), so each concurrent request reads its own. Unset is "".
    """
    tenant_id = getattr(pipeline, "_current_tenant_id", "")
    conversation_id = getattr(pipeline, "_current_conversation_id", "")
    return {
        "x-tenant-id": tenant_id if isinstance(tenant_id, str) else "",
        "x-conversation-id": conversation_id if isinstance(conversation_id, str) else "",
    }


def _build_agent(pipeline: Any, agent_id: str) -> Any | None:
    if agent_id == CRITIC_AGENT_ID:
        from src.agents.critic_supervisor import CriticSupervisorAgent

        return CriticSupervisorAgent(openai_client=getattr(pipeline, "_openai_client", None))
    if agent_id == ESCALATION_AGENT_ID:
        from src.agents.escalation_handler import EscalationHandlerAgent

        return EscalationHandlerAgent(openai_client=getattr(pipeline, "_openai_client", None))
    if agent_id == ANALYTICS_AGENT_ID:
        from src.agents.analytics_collector import AnalyticsCollectorAgent

        return AnalyticsCollectorAgent()
    return None


def get_in_process_agent(pipeline: Any, agent_id: str) -> Any | None:
    """Return the pipeline's in-process agent for agent_id, building it on first use.

    Agents live in pipeline.__dict__["_in_process_agents"], never as _cr_agent,
    _esc_agent or _an_agent (test_environment_parity.py). They hold
    construction-time state only, so one instance serves every request.
    Returns None for a None pipeline or an agent id with no in-process factory.
    """
    if pipeline is None:
        return None
    agents = pipeline.__dict__.setdefault(_REGISTRY_KEY, {})
    agent = agents.get(agent_id)
    if agent is None:
        agent = _build_agent(pipeline, agent_id)
        if agent is not None:
            agents[agent_id] = agent
    return agent


class InProcessAgentError(ServiceUnavailableError):
    """An in-process agent call failed.

    It is a ServiceUnavailableError, so the orchestrator's existing handler
    sends the customer the fixed service_unavailable message, with the agent
    id as the stage, and never the exception text. code and recoverable come
    from _classify_openai_error, or are "agent_missing" or "invalid_result";
    they reach logs and spans only.
    """

    def __init__(self, service_name: str, code: str, recoverable: bool) -> None:
        # Not ServiceUnavailableError.__init__: its message says "circuit breaker open".
        Exception.__init__(self, f"In-process agent {service_name} failed ({code})")
        self.service_name = service_name
        self.code = code
        self.recoverable = recoverable


def _call_failed(
    agent_id: str,
    code: str,
    recoverable: bool,
    span: Any,
    error_type: str,
) -> InProcessAgentError:
    """Log and record a failed in-process call, and build the error to raise.

    Only the classified code and the exception type are logged: an agent
    exception can carry customer text or an SDK request body, so neither its
    message nor its traceback goes to the logs.
    """
    logger.warning("In-process %s call failed: code=%s error=%s", agent_id, code, error_type)
    if span is not None:
        span.set_attribute("dispatch.error_code", code)
    return InProcessAgentError(agent_id, code, recoverable)


async def call_in_process(
    pipeline: Any,
    agent_id: str,
    agent: Any,
    payload: dict[str, Any],
    *,
    span: Any = None,
) -> dict[str, Any]:
    """Await agent.process with the current request's headers and return its result dict.

    Every failure raises InProcessAgentError: code "agent_missing" for a None
    agent, "invalid_result" for a result that is not a dict, and otherwise the
    _classify_openai_error code, chained from the original exception. When a
    span is given, the code is set on it as dispatch.error_code. CancelledError
    (a BaseException) propagates, so a stage timeout or a client disconnect
    still cancels the call.
    """
    if agent is None:
        raise _call_failed(agent_id, "agent_missing", False, span, "None")
    try:
        result = await agent.process(dict(payload), in_process_headers(pipeline))
    except Exception as exc:
        code, _message, recoverable = _classify_openai_error(exc)
        raise _call_failed(agent_id, code, recoverable, span, type(exc).__name__) from exc
    if not isinstance(result, dict):
        raise _call_failed(agent_id, "invalid_result", False, span, type(result).__name__)
    return result


def kr_preferences(prefs: Any) -> dict[str, Any]:
    """The retrieval preferences the KR agent reads, from the tenant's preferences.

    A copy of the dict _call_knowledge_retrieval_direct builds
    (agent_dispatch.py), not an extraction, so that method and its tests stay
    unchanged. Unset fields are left out.
    """
    prefs_dict: dict[str, Any] = {}
    if prefs:
        if prefs.retrieval_top_k is not None:
            prefs_dict["retrieval_top_k"] = prefs.retrieval_top_k
        if prefs.retrieval_vector_weight is not None:
            prefs_dict["retrieval_vector_weight"] = prefs.retrieval_vector_weight
        if prefs.retrieval_bm25_weight is not None:
            prefs_dict["retrieval_bm25_weight"] = prefs.retrieval_bm25_weight
        if prefs.retrieval_min_score is not None:
            prefs_dict["retrieval_min_score"] = prefs.retrieval_min_score
        if prefs.intent_source_mapping:
            prefs_dict["intent_source_mapping"] = prefs.intent_source_mapping
    return prefs_dict


def critic_timeout_s(budget: Any) -> float:
    """Seconds the in-process Critic may take.

    The "critic-supervisor" stage budget, capped by the pipeline's remaining
    time when that is a real number, and never below CRITIC_MIN_TIMEOUT_S.
    A None or MagicMock budget, as test harnesses pass, gets the stage budget.
    """
    timeout_s = STAGE_BUDGETS_MS[CRITIC_AGENT_ID] / 1000
    remaining_ms = getattr(budget, "remaining_ms", None)
    if isinstance(remaining_ms, (int, float)) and not isinstance(remaining_ms, bool):
        timeout_s = min(timeout_s, remaining_ms / 1000)
    return max(CRITIC_MIN_TIMEOUT_S, timeout_s)


async def validate_with_in_process_critic(
    pipeline: Any,
    tenant_id: str,
    conversation_id: str,
    response_text: str,
    customer_message: str,
    budget: Any,
    knowledge_titles: list[str] | None = None,
) -> tuple[bool, str, CriticResult]:
    """Validate a reply with the in-process Critic, failing closed.

    Returns what CriticPolicy.require_critic_approval returns: (approved,
    text to deliver, CriticResult). A reply is approved only when the agent
    says approved is True with an APPROVED or MODIFIED verdict and the text to
    deliver is a non-empty str. Every other outcome, including a timeout, an
    exception or a malformed result, returns (False, SAFE_FALLBACK_MESSAGE,
    result), so the orchestrator retracts the streamed tokens. It never
    raises, except CancelledError (a BaseException), which propagates so a
    client disconnect still cancels the turn.
    """
    start = time.monotonic()
    request_id = f"critic-{conversation_id}-{int(time.time() * 1000)}"
    timeout_s: float | None = None
    try:
        timeout_s = critic_timeout_s(budget)
        agent = get_in_process_agent(pipeline, CRITIC_AGENT_ID)
        if agent is None:
            raise RuntimeError("no in-process critic agent")
        payload = {
            "response_text": response_text,
            "customer_message": customer_message,
            "knowledge_titles": knowledge_titles or [],
            "conversation_id": conversation_id,
        }
        headers = {"x-tenant-id": tenant_id, "x-conversation-id": conversation_id}
        output = await asyncio.wait_for(agent.process(payload, headers), timeout=timeout_s)
        return _adapt(output, response_text, start, request_id)
    except TimeoutError:
        logger.warning(
            "In-process critic timed out after %ss: conv=%s, reply blocked (fail-closed)",
            timeout_s,
            conversation_id,
        )
        return _blocked(CriticBlockReason.ERROR, ["in_process_critic_timeout"], None, start, request_id)
    except Exception as exc:
        logger.warning(
            "In-process critic failed: conv=%s error=%s, reply blocked (fail-closed)",
            conversation_id,
            type(exc).__name__,
        )  # no exc_info: the exception text can carry the reply or customer data
        return _blocked(CriticBlockReason.ERROR, ["in_process_critic_error"], None, start, request_id)


def _adapt(output: Any, response_text: str, start: float, request_id: str) -> tuple[bool, str, CriticResult]:
    """Map the agent's result dict onto the CriticPolicy return contract."""
    if not isinstance(output, dict):
        return _blocked(CriticBlockReason.ERROR, ["in_process_critic_bad_result"], None, start, request_id)

    verdict = _verdict(output.get("verdict"))
    flags = _flags(output.get("flags"))
    modified_response = output.get("modified_response")
    approved = output.get("approved") is True and verdict in (CriticVerdict.APPROVED, CriticVerdict.MODIFIED)

    if not approved:
        return _blocked(_block_reason(output.get("block_reason")), flags, verdict, start, request_id)

    if verdict is CriticVerdict.MODIFIED and not modified_response:
        # As critic_policy does: a MODIFIED verdict without the text is a rejection.
        flags.append("modified_verdict_without_text")
        return _blocked(CriticBlockReason.REJECTED, flags, verdict, start, request_id)

    safe_text = output.get("safe_text", modified_response or response_text)
    if not isinstance(safe_text, str) or not safe_text:
        # A non-string would raise in pii_tokenizer.detokenize after the tokens streamed.
        flags.append("in_process_critic_invalid_text")
        return _blocked(CriticBlockReason.ERROR, flags, verdict, start, request_id)

    result = CriticResult(
        approved=True,
        verdict=verdict,
        block_reason=None,
        flags=flags,
        modified_response=modified_response if isinstance(modified_response, str) else None,
        latency_ms=_elapsed_ms(start),
        critic_instance=CRITIC_INSTANCE,
        request_id=request_id,
    )
    return True, safe_text, result


def _blocked(
    reason: CriticBlockReason,
    flags: list[str],
    verdict: CriticVerdict | None,
    start: float,
    request_id: str,
) -> tuple[bool, str, CriticResult]:
    """A not-approved result: the text is always SAFE_FALLBACK_MESSAGE."""
    result = CriticResult(
        approved=False,
        verdict=verdict,
        block_reason=reason,
        flags=flags,
        modified_response=None,
        latency_ms=_elapsed_ms(start),
        critic_instance=CRITIC_INSTANCE,
        request_id=request_id,
    )
    return False, SAFE_FALLBACK_MESSAGE, result


def _verdict(value: Any) -> CriticVerdict | None:
    """A CriticVerdict member, or None (for example the agent's "unavailable")."""
    try:
        return CriticVerdict(value)
    except (ValueError, TypeError):
        return None


def _block_reason(value: Any) -> CriticBlockReason:
    """A missing reason on a block is REJECTED; an unknown one is ERROR."""
    if value is None or value == "":
        return CriticBlockReason.REJECTED
    try:
        return CriticBlockReason(value)
    except (ValueError, TypeError):
        return CriticBlockReason.ERROR


def _flags(value: Any) -> list[str]:
    """Normalise the agent's flags to a new list of str."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(flag) for flag in value]
    return [str(value)]


def _elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


def single_container_readiness(pipeline: Any, sdk_status: dict[str, Any]) -> dict[str, Any]:
    """The beta-mode readiness report that /ready returns as "single_container".

    Returns {"enabled": True, "ready": all checks pass, "checks": {name: bool}} with:
    - pipeline_configured: the chat pipeline is set;
    - openai_client: the pipeline has an Azure OpenAI client (singleton.py returns
      None, with only a warning, when the Azure settings are missing);
    - core_agents: the pipeline's IC, KR, RG and co-pilot agents exist;
    - in_process_agents: the critic, escalation and analytics agents build through
      get_in_process_agent. Readiness builds them itself, so a fresh replica passes
      before any chat traffic;
    - critic_has_client: the in-process critic has a client (without one it blocks
      every reply);
    - kb_embeddings: the KB vectorizer is configured with a client;
    - transport_endpoints_unset: sdk_status reports no SLIM and no NATS endpoint;
    - single_worker: UVICORN_WORKERS is "1", so the in-process caches and agents
      exist exactly once.

    It never raises: an agent that fails to build fails its checks instead.
    """
    agents_built, critic_has_client = _build_readiness_agents(pipeline)
    checks = {
        "pipeline_configured": pipeline is not None,
        "openai_client": getattr(pipeline, "_openai_client", None) is not None,
        "core_agents": pipeline is not None
        and all(getattr(pipeline, attr, None) is not None for attr in _CORE_AGENT_ATTRS),
        "in_process_agents": agents_built,
        "critic_has_client": critic_has_client,
        "kb_embeddings": _kb_embeddings_ready(),
        # An endpoint key that sdk_status does not report fails the check (fail-closed).
        "transport_endpoints_unset": all(
            key in sdk_status and sdk_status[key] is None for key in _TRANSPORT_ENDPOINT_KEYS
        ),
        "single_worker": os.environ.get("UVICORN_WORKERS") == "1",
    }
    return {"enabled": True, "ready": all(checks.values()), "checks": checks}


def _build_readiness_agents(pipeline: Any) -> tuple[bool, bool]:
    """Build the in-process agents; return (every agent built, the critic has a client)."""
    if pipeline is None:
        return False, False
    built: dict[str, Any] = {}
    for agent_id in _READINESS_AGENT_IDS:
        try:
            built[agent_id] = get_in_process_agent(pipeline, agent_id)
        except Exception as exc:
            logger.warning("Readiness: in-process %s failed to build: error=%s", agent_id, type(exc).__name__)
            built[agent_id] = None
    agents_built = all(agent is not None for agent in built.values())
    critic_has_client = getattr(built[CRITIC_AGENT_ID], "_openai_client", None) is not None
    return agents_built, critic_has_client


def _kb_embeddings_ready() -> bool:
    """The KB vectorizer is configured with a client, so in-process KR embeds real queries."""
    from src.multi_tenant.knowledge_vectorizer import get_knowledge_vectorizer

    vectorizer = get_knowledge_vectorizer()
    configured = getattr(vectorizer, "_configured", False) is True
    return configured and getattr(vectorizer, "_openai_client", None) is not None
