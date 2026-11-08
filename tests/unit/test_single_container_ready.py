# tests/unit/test_single_container_ready.py: RC-11 /ready rule for single-container mode
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR8 (docs/beta-single-container-design.md, section 5).

When SINGLE_CONTAINER_MODE is on, /ready no longer requires SLIM or NATS transport. Instead
``in_process.single_container_readiness`` reports eight checks under ``single_container`` and
/ready answers 503 not_ready when any of them fails:

- pipeline_configured: src.chat.endpoints._pipeline is set;
- openai_client: the pipeline has an Azure OpenAI client;
- core_agents: the pipeline built its IC, KR, RG and co-pilot agents;
- in_process_agents: the critic, escalation and analytics agents build (readiness builds them,
  so a fresh replica passes before any chat traffic);
- critic_has_client: the in-process critic has a client (without one it blocks every reply);
- kb_embeddings: the KB vectorizer is configured with a client;
- transport_endpoints_unset: get_sdk_status reports no SLIM and no NATS endpoint;
- single_worker: UVICORN_WORKERS is "1".

agntcy_sdk is reported exactly as get_sdk_status returns it: transport_active stays False.

With the flag off nothing changes: the guard tests below pass before and after this PR. The
tests use a bare FastAPI app with register_health_endpoints (the test_s175_scaling_680.py
pattern), except two that show how tests/conftest.py's app_client keeps the checks out of
default-mode tests.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import src.chat.endpoints as chat_endpoints
import src.chat.pipeline.in_process as in_process
import src.multi_tenant.knowledge_vectorizer as kv_mod
from src.agents.critic_supervisor import CriticSupervisorAgent
from src.app.health import register_health_endpoints
from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.knowledge_vectorizer import KnowledgeVectorizer

FLAG = "SINGLE_CONTAINER_MODE"
WORKERS = "UVICORN_WORKERS"
HEALTH_FILE = Path(__file__).resolve().parents[2] / "src" / "app" / "health.py"

CHECK_NAMES = [
    "pipeline_configured",
    "openai_client",
    "core_agents",
    "in_process_agents",
    "critic_has_client",
    "kb_embeddings",
    "transport_endpoints_unset",
    "single_worker",
]
REGISTRY_KEY = "_in_process_agents"
IN_PROCESS_AGENT_IDS = {"critic-supervisor", "escalation-handler", "analytics-collector"}
# Default for the helpers' openai_client argument: a fresh mock client.
_CLIENT = object()


def _sdk_status(**overrides: Any) -> dict[str, Any]:
    """What get_sdk_status returns on a beta gateway: SDK skipped, no transport, no endpoints."""
    status: dict[str, Any] = {
        "sdk_initialized": False,
        "transport_type": None,
        "transport_active": False,
        "active_tier": "http_failure_mode",
        "slim_endpoint": None,
        "nats_endpoint": None,
        "nats_protocol": None,
        "available_protocols": [],
        "available_transports": [],
        "agent_topics": [],
        "directory": {"directory_available": False},
    }
    status.update(overrides)
    return status


def _fresh_pipeline(openai_client: Any = _CLIENT) -> ChatPipeline:
    """A real ChatPipeline as lifecycle builds it; its in-process registry is still empty."""
    client = MagicMock(name="AsyncAzureOpenAI") if openai_client is _CLIENT else openai_client
    return ChatPipeline(
        session=MagicMock(),
        prompt_builder=MagicMock(),
        profile_service=AsyncMock(),
        openai_client=client,
    )


def _configured_vectorizer(openai_client: Any = _CLIENT) -> KnowledgeVectorizer:
    client = MagicMock(name="AsyncAzureOpenAI") if openai_client is _CLIENT else openai_client
    vectorizer = KnowledgeVectorizer()
    vectorizer.configure(kb_repo=MagicMock(), openai_client=client, in_process_mode=client is not None)
    return vectorizer


@pytest.fixture
def ready_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A beta gateway where every check passes; each test breaks exactly what it names.

    Every external dependency of /ready is replaced, so nothing touches the network.
    Returns the mutable pieces: the sdk status dict and the bare app's client.
    """
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(WORKERS, "1")

    pipeline = _fresh_pipeline()
    monkeypatch.setattr(chat_endpoints, "_pipeline", pipeline)
    monkeypatch.setattr(kv_mod, "_knowledge_vectorizer", _configured_vectorizer())

    sdk_status = _sdk_status()
    monkeypatch.setattr(
        "src.multi_tenant.agntcy_sdk_integration.get_sdk_status",
        MagicMock(side_effect=lambda: sdk_status),
    )
    nats = MagicMock()
    nats.is_connected = False
    monkeypatch.setattr("src.app.health.get_nats_manager", MagicMock(return_value=nats))
    secrets = MagicMock()
    secrets.health_check = AsyncMock(return_value={"status": "healthy"})
    monkeypatch.setattr("src.app.health.get_secret_service", MagicMock(return_value=secrets))
    monkeypatch.setattr(
        "src.multi_tenant.cosmos_readiness.check_cosmos_ready",
        AsyncMock(return_value={"status": "healthy"}),
    )

    app = FastAPI()
    register_health_endpoints(app)
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "pipeline": pipeline,
        "sdk_status": sdk_status,
    }


def _failing(data: dict[str, Any]) -> set[str]:
    return {name for name, ok in data["single_container"]["checks"].items() if not ok}


# ---------------------------------------------------------------------------
# A ready beta gateway
# ---------------------------------------------------------------------------


def test_ready_200_in_beta(ready_env: dict[str, Any]) -> None:
    response = ready_env["client"].get("/ready")

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == "ready"
    assert data["single_container"]["enabled"] is True
    assert data["single_container"]["ready"] is True
    assert data["single_container"]["checks"] == dict.fromkeys(CHECK_NAMES, True)
    # transport_active is never faked: agntcy_sdk is get_sdk_status's dict, unchanged.
    assert data["agntcy_sdk"]["transport_active"] is False
    assert data["agntcy_sdk"] == ready_env["sdk_status"]
    assert "transport_enforcement" not in data
    # A ready beta carries the tail of the default response too.
    assert "version" in data
    assert "redis_pubsub" in data["cache_invalidation"]


def test_fresh_pipeline_builds_agents_and_passes(ready_env: dict[str, Any]) -> None:
    """A replica with no chat traffic yet (min 0 or a /ready probe) still becomes ready."""
    pipeline = ready_env["pipeline"]
    assert REGISTRY_KEY not in pipeline.__dict__

    first = ready_env["client"].get("/ready")

    assert first.status_code == 200, first.json()
    registry = dict(pipeline.__dict__[REGISTRY_KEY])
    assert set(registry) == IN_PROCESS_AGENT_IDS
    assert all(agent is not None for agent in registry.values())
    assert registry["critic-supervisor"]._openai_client is pipeline._openai_client

    # Later probes and chat turns reuse the same instances.
    second = ready_env["client"].get("/ready")
    assert second.status_code == 200
    assert pipeline.__dict__[REGISTRY_KEY] == registry
    for agent_id, agent in registry.items():
        assert in_process.get_in_process_agent(pipeline, agent_id) is agent


# ---------------------------------------------------------------------------
# Each failing check gives 503 not_ready
# ---------------------------------------------------------------------------


def _no_pipeline(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_endpoints, "_pipeline", None)


def _no_client(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    # lifecycle passes _create_openai_client(), which returns None without the Azure settings.
    monkeypatch.setattr(chat_endpoints, "_pipeline", _fresh_pipeline(openai_client=None))


def _critic_without_client(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    env["pipeline"].__dict__[REGISTRY_KEY] = {"critic-supervisor": CriticSupervisorAgent(openai_client=None)}


def _escalation_build_fails(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    real_build = in_process._build_agent

    def build(pipeline: Any, agent_id: str) -> Any:
        if agent_id == "escalation-handler":
            raise ImportError("escalation agent unavailable")
        return real_build(pipeline, agent_id)

    monkeypatch.setattr(in_process, "_build_agent", build)


def _critic_build_fails(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    real_build = in_process._build_agent

    def build(pipeline: Any, agent_id: str) -> Any:
        if agent_id == "critic-supervisor":
            raise RuntimeError("critic agent unavailable")
        return real_build(pipeline, agent_id)

    monkeypatch.setattr(in_process, "_build_agent", build)


def _analytics_not_built(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    real_build = in_process._build_agent
    monkeypatch.setattr(
        in_process,
        "_build_agent",
        lambda pipeline, agent_id: None if agent_id == "analytics-collector" else real_build(pipeline, agent_id),
    )


def _core_agent_missing(attr: str):
    def breaker(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        setattr(env["pipeline"], attr, None)

    return breaker


def _kb_without_client(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kv_mod, "_knowledge_vectorizer", _configured_vectorizer(openai_client=None))


def _kb_not_configured(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kv_mod, "_knowledge_vectorizer", KnowledgeVectorizer())


def _slim_endpoint_set(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    env["sdk_status"]["slim_endpoint"] = "http://slim.invalid:46357"


def _nats_endpoint_set(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    env["sdk_status"]["nats_endpoint"] = "nats://nats.invalid:4222"
    env["sdk_status"]["nats_protocol"] = "tcp"


def _endpoints_not_reported(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    del env["sdk_status"]["slim_endpoint"]
    del env["sdk_status"]["nats_endpoint"]


def _workers(value: str | None):
    def breaker(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        if value is None:
            monkeypatch.delenv(WORKERS, raising=False)
        else:
            monkeypatch.setenv(WORKERS, value)

    return breaker


FAILING_CASES = [
    pytest.param(
        _no_pipeline,
        {"pipeline_configured", "openai_client", "core_agents", "in_process_agents", "critic_has_client"},
        id="no-pipeline",
    ),
    pytest.param(_no_client, {"openai_client", "critic_has_client"}, id="no-openai-client"),
    pytest.param(_critic_without_client, {"critic_has_client"}, id="critic-without-client"),
    pytest.param(_escalation_build_fails, {"in_process_agents"}, id="escalation-build-raises"),
    pytest.param(_critic_build_fails, {"in_process_agents", "critic_has_client"}, id="critic-build-raises"),
    pytest.param(_analytics_not_built, {"in_process_agents"}, id="analytics-not-built"),
    pytest.param(_core_agent_missing("_ic_agent"), {"core_agents"}, id="no-ic-agent"),
    pytest.param(_core_agent_missing("_kr_agent"), {"core_agents"}, id="no-kr-agent"),
    pytest.param(_core_agent_missing("_rg_agent"), {"core_agents"}, id="no-rg-agent"),
    pytest.param(_core_agent_missing("_copilot_agent"), {"core_agents"}, id="no-copilot-agent"),
    pytest.param(_kb_without_client, {"kb_embeddings"}, id="kb-vectorizer-without-client"),
    pytest.param(_kb_not_configured, {"kb_embeddings"}, id="kb-vectorizer-not-configured"),
    pytest.param(_slim_endpoint_set, {"transport_endpoints_unset"}, id="slim-endpoint-set"),
    pytest.param(_nats_endpoint_set, {"transport_endpoints_unset"}, id="nats-endpoint-set"),
    pytest.param(_endpoints_not_reported, {"transport_endpoints_unset"}, id="endpoints-not-reported"),
    pytest.param(_workers(None), {"single_worker"}, id="workers-unset"),
    pytest.param(_workers("4"), {"single_worker"}, id="workers-4"),
    pytest.param(_workers("2"), {"single_worker"}, id="workers-2"),
    pytest.param(_workers(""), {"single_worker"}, id="workers-empty"),
]


@pytest.mark.parametrize(("breaker", "expected_failing"), FAILING_CASES)
def test_ready_503_per_failing_check(
    ready_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    breaker: Any,
    expected_failing: set[str],
) -> None:
    breaker(ready_env, monkeypatch)

    response = ready_env["client"].get("/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert data["single_container"]["enabled"] is True
    assert data["single_container"]["ready"] is False
    assert list(data["single_container"]["checks"]) == CHECK_NAMES
    assert _failing(data) == expected_failing
    # The beta 503 is the readiness checks' verdict, not the transport gate's.
    assert "transport_enforcement" not in data
    assert data["agntcy_sdk"]["transport_active"] is False
    assert "version" not in data


# ---------------------------------------------------------------------------
# single_container_readiness itself
# ---------------------------------------------------------------------------


def test_readiness_shape_and_check_names(ready_env: dict[str, Any]) -> None:
    """The field and check names the closed-beta runbook reads from /ready."""
    report = in_process.single_container_readiness(ready_env["pipeline"], ready_env["sdk_status"])

    assert set(report) == {"enabled", "ready", "checks"}
    assert report["enabled"] is True
    assert report["ready"] is True
    assert list(report["checks"]) == CHECK_NAMES
    assert all(value is True for value in report["checks"].values())


def test_readiness_never_raises_without_a_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKERS, "1")
    monkeypatch.setattr(kv_mod, "_knowledge_vectorizer", _configured_vectorizer())

    report = in_process.single_container_readiness(None, _sdk_status())

    assert report["enabled"] is True
    assert report["ready"] is False
    assert report["checks"]["pipeline_configured"] is False
    assert report["checks"]["in_process_agents"] is False
    assert report["checks"]["kb_embeddings"] is True


# ---------------------------------------------------------------------------
# Guards: flag off, /ready is unchanged (pass before and after)
# ---------------------------------------------------------------------------


def test_flag_off_without_transport_keeps_transport_gate(
    ready_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag off and transport inactive: the SPEC-1802 503 with transport_enforcement, as before."""
    monkeypatch.delenv(FLAG, raising=False)

    response = ready_env["client"].get("/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert "SPEC-1802 requires SLIM or NATS transport in ALL environments." in data["transport_enforcement"]
    assert "single_container" not in data


@pytest.mark.parametrize("value", ["1", "yes", "", "false"])
def test_flag_values_other_than_true_keep_transport_gate(
    ready_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(FLAG, value)

    response = ready_env["client"].get("/ready")

    assert response.status_code == 503
    assert "transport_enforcement" in response.json()
    assert "single_container" not in response.json()


def test_flag_off_with_transport_never_runs_beta_checks(
    ready_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    ready_env["sdk_status"].update(transport_active=True, active_tier="slim", slim_endpoint="http://slim.invalid")
    readiness = MagicMock(side_effect=AssertionError("beta readiness ran with the flag off"))
    monkeypatch.setattr(in_process, "single_container_readiness", readiness, raising=False)

    response = ready_env["client"].get("/ready")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "single_container" not in data
    assert "version" in data
    readiness.assert_not_called()


def test_health_module_imports_no_chat_code_at_module_level() -> None:
    """health.py loads in_process.py and the chat endpoints only inside the beta branch."""
    tree = ast.parse(HEALTH_FILE.read_text(encoding="utf-8"))
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module)
    assert module_level
    assert not [name for name in module_level if name.startswith("src.chat")]


# ---------------------------------------------------------------------------
# tests/conftest.py: app_client stays in default mode
# ---------------------------------------------------------------------------


def test_app_client_ready_stays_default_mode(app_client: TestClient) -> None:
    """The autouse _single_container_mode_off fixture keeps the beta checks out of app_client."""
    response = app_client.get("/ready")

    assert response.status_code == 200
    assert "single_container" not in response.json()


def test_app_client_beta_test_patches_readiness_explicitly(
    app_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The design's pattern for a beta test on app_client: set the flag, patch the readiness."""
    monkeypatch.setenv(FLAG, "true")
    report = {"enabled": True, "ready": True, "checks": dict.fromkeys(CHECK_NAMES, True)}

    with patch.object(in_process, "single_container_readiness", return_value=report) as readiness:
        response = app_client.get("/ready")

    assert response.status_code == 200, response.json()
    assert response.json()["single_container"] == report
    readiness.assert_called_once()
    pipeline_arg, sdk_status_arg = readiness.call_args.args
    # The pipeline is read from src.chat.endpoints at request time.
    assert pipeline_arg is chat_endpoints._pipeline
    assert sdk_status_arg == response.json()["agntcy_sdk"]
