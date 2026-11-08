# tests/unit/test_deploy_single_container_profile.py: deploy.py single-container profile (RC-11, PR12)
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 closed beta: scripts/deploy.py gains ``--profile single-container`` for
staging, a strict chat smoke for it, and a staging guard for the full profile
(docs/beta-single-container-design.md, section 7, PR12).

What these tests pin down:

- ``--profile single-container`` with production exits 1 before any az call.
- On staging the profile touches only the staging gateway: one
  ``az containerapp update`` with the image and both environment variables,
  then min/max replicas (min 1 by default, 0 when asked, max 1). No test
  host, agent or SLIM app is named by any command, and only the gateway is
  verified.
- The strict smoke needs a validated event (critic_passed true) and no
  retraction (critic_passed false). The default smoke is unchanged.
- The full profile on staging reads SINGLE_CONTAINER_MODE from the staging
  gateway first and refuses, before any write, when it is on or cannot be read.
  The production full profile never runs that query.

Nothing here talks to Azure or the network: ``_run`` and the HTTP helpers are
replaced with fakes that record what they were asked to do.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from src.chat.models import (
    done_event,
    retracted_event,
    stage_event,
    token_event,
    validated_event,
)

DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy.py"
TAG = "v1.2.3"
STAGING_GATEWAY = "agent-red-staging"
GUARD_QUERY = "properties.template.containers[0].env[?name=='SINGLE_CONTAINER_MODE'].value | [0]"


def _load_deploy_module():
    """Import scripts/deploy.py as a fresh module (scripts/ is not a package)."""
    module_name = "deploy_single_container_under_test"
    spec = importlib.util.spec_from_file_location(module_name, DEPLOY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(module_name, None)
    return mod


@pytest.fixture
def deploy():
    return _load_deploy_module()


def _is_guard_query(cmd: str) -> bool:
    return cmd.startswith("az containerapp show") and "SINGLE_CONTAINER_MODE" in cmd


def _fake_run(commands: list[str], guard_result: tuple[int, str]):
    """A stand-in for deploy._run that records each command and answers like az."""

    def fake_run(cmd: str, timeout: int = 120) -> tuple[int, str]:
        commands.append(cmd)
        if cmd.startswith("az acr repository show-tags"):
            return 0, TAG
        if _is_guard_query(cmd):
            return guard_result
        if cmd.startswith("az containerapp show"):
            return 0, f"example.azurecr.io/api-gateway:{TAG}"
        if cmd.startswith("az containerapp revision list"):
            revision = {
                "image": f"example.azurecr.io/api-gateway:{TAG}",
                "health": "Healthy",
                "running": "Running",
                "name": "rev-1",
            }
            return 0, json.dumps(revision)
        return 0, ""

    return fake_run


def _run_main(
    deploy,
    monkeypatch,
    argv: list[str],
    guard_result: tuple[int, str] = (0, ""),
    *,
    failing_scaling: bool = False,
    chat_result: bool = True,
):
    """Run deploy.main() with every Azure, HTTP and sleep side effect replaced.

    Returns (exit_code, commands, chat_mock, log_lines).
    """
    commands: list[str] = []
    log_lines: list[str] = []
    chat = MagicMock(return_value=chat_result)
    run = _fake_run(commands, guard_result)
    if failing_scaling:
        answer = run

        def run(cmd: str, timeout: int = 120) -> tuple[int, str]:
            code, output = answer(cmd, timeout)
            if cmd.startswith("az containerapp update") and "--max-replicas" in cmd:
                return 1, "simulated az error"
            return code, output

    monkeypatch.setattr(deploy, "_run", run)
    monkeypatch.setattr(deploy, "_init_log", lambda: None)
    monkeypatch.setattr(deploy, "log", log_lines.append)
    monkeypatch.setattr(deploy, "wait_for_healthy", lambda fqdn: True)
    monkeypatch.setattr(deploy, "verify_version", lambda fqdn, expected, api_key=None: TAG.lstrip("v"))
    monkeypatch.setattr(deploy, "verify_chat_conversation", chat)
    monkeypatch.setattr(deploy, "record_deployment_event", lambda **kwargs: None)
    monkeypatch.setattr(
        deploy,
        "time",
        SimpleNamespace(sleep=lambda seconds: None, time=time.time, monotonic=time.monotonic),
    )
    monkeypatch.setattr(sys, "argv", ["deploy.py", *argv])
    code = deploy.main()
    return code, commands, chat, log_lines


def _updates(commands: list[str]) -> list[list[str]]:
    return [shlex.split(c) for c in commands if c.startswith("az containerapp update")]


def _fqdn(deploy, environment: str) -> str:
    return deploy.deploy_config.get_environment(environment)["fqdn"]


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """Every token after ``flag`` up to the next ``--`` option."""
    start = argv.index(flag) + 1
    values = []
    for token in argv[start:]:
        if token.startswith("--"):
            break
        values.append(token)
    return values


# ---------------------------------------------------------------------------
# Production is refused for the single-container profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [[], ["--confirm"], ["--confirm", "--min-replicas", "1"]])
def test_refuses_production_before_any_az_call(deploy, monkeypatch, extra):
    code, commands, chat, _ = _run_main(
        deploy, monkeypatch, ["production", TAG, "--profile", "single-container", *extra]
    )
    assert code == 1
    assert commands == []
    chat.assert_not_called()


def test_min_replicas_is_refused_with_the_full_profile(deploy, monkeypatch):
    code, commands, _, _ = _run_main(deploy, monkeypatch, ["staging", TAG, "--min-replicas", "0"])
    assert code == 1
    assert commands == []


@pytest.mark.parametrize("value", ["2", "-1", "many"])
def test_min_replicas_accepts_only_zero_or_one(deploy, monkeypatch, value):
    with pytest.raises(SystemExit):
        _run_main(deploy, monkeypatch, ["staging", TAG, "--profile", "single-container", "--min-replicas", value])


def test_profile_accepts_only_known_names(deploy, monkeypatch):
    with pytest.raises(SystemExit):
        _run_main(deploy, monkeypatch, ["staging", TAG, "--profile", "beta"])


# ---------------------------------------------------------------------------
# Single-container staging touches only the staging gateway
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "expected_min"),
    [([], "1"), (["--min-replicas", "0"], "0"), (["--min-replicas", "1"], "1")],
)
def test_touches_only_staging_gateway(deploy, monkeypatch, extra, expected_min):
    code, commands, chat, _ = _run_main(deploy, monkeypatch, ["staging", TAG, "--profile", "single-container", *extra])
    assert code == 0

    # No command names a test host, agent or SLIM app, or one of their image repositories.
    forbidden = (
        set(deploy.AGENT_CONTAINER_APPS.values())
        | set(deploy.INFRA_CONTAINER_APPS.values())
        | set(deploy.TEST_HOST_APPS.values())
        | set(deploy.AGENT_CONTAINER_APPS)
        | set(deploy.INFRA_CONTAINER_APPS)
        | {"test-host"}
    )
    for cmd in commands:
        named = forbidden & set(shlex.split(cmd))
        assert not named, f"{cmd!r} names {sorted(named)}"
        assert "/test-host:" not in cmd
        assert "/agent-" not in cmd
        assert "/slim-gateway:" not in cmd

    # Every write is to the staging gateway: one image update with both variables, then scaling.
    updates = _updates(commands)
    assert len(updates) == 2, updates
    assert all(_flag_value(argv, "--name") == STAGING_GATEWAY for argv in updates)

    env_updates = [argv for argv in updates if "--set-env-vars" in argv]
    assert len(env_updates) == 1
    image_update = env_updates[0]
    assert _flag_value(image_update, "--image") == f"{deploy.ACR_LOGIN_SERVER}/api-gateway:{TAG}"
    assert sorted(_flag_values(image_update, "--set-env-vars")) == ["SINGLE_CONTAINER_MODE=true", "UVICORN_WORKERS=1"]

    scaling = [argv for argv in updates if "--min-replicas" in argv]
    assert len(scaling) == 1
    assert _flag_value(scaling[0], "--min-replicas") == expected_min
    assert _flag_value(scaling[0], "--max-replicas") == "1"
    assert "--image" not in scaling[0]
    assert updates.index(image_update) < updates.index(scaling[0])

    # Only the gateway is verified, and the beta profile does not run the full-profile guard.
    revision_queries = [c for c in commands if c.startswith("az containerapp revision list")]
    assert len(revision_queries) == 1
    assert _flag_value(shlex.split(revision_queries[0]), "--name") == STAGING_GATEWAY
    assert not any(_is_guard_query(c) for c in commands)

    # The strict smoke runs.
    chat.assert_called_once()
    assert chat.call_args.kwargs.get("require_validated") is True


def test_single_container_fails_when_beta_scaling_cannot_be_enforced(deploy, monkeypatch):
    """max 1 replica is what keeps one cache copy, so a failed scaling update fails the deploy."""
    code, commands, chat, _ = _run_main(
        deploy, monkeypatch, ["staging", TAG, "--profile", "single-container"], failing_scaling=True
    )
    assert code == 1
    assert any("--max-replicas" in c for c in commands)
    chat.assert_called_once()  # verification still runs, so the log shows the whole picture


def test_single_container_honours_strict_smoke_failure(deploy, monkeypatch):
    code, _, chat, _ = _run_main(
        deploy, monkeypatch, ["staging", TAG, "--profile", "single-container"], chat_result=False
    )
    assert code == 1
    assert chat.call_args.kwargs.get("require_validated") is True


# ---------------------------------------------------------------------------
# Staging guard for the full profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guard_result",
    [(0, "true"), (0, "TRUE"), (0, " True \n"), (1, "simulated az error"), (1, "Command timed out")],
    ids=["on", "on-upper", "on-padded", "query-failed", "query-timed-out"],
)
def test_full_profile_refuses_staging_in_beta_or_on_query_failure(deploy, monkeypatch, guard_result):
    code, commands, chat, log_lines = _run_main(deploy, monkeypatch, ["staging", TAG], guard_result=guard_result)
    assert code == 1
    assert sum(_is_guard_query(c) for c in commands) == 1
    assert _updates(commands) == [], "the guard must refuse before any write"
    chat.assert_not_called()
    assert any("SINGLE_CONTAINER_MODE" in line for line in log_lines)


@pytest.mark.parametrize("guard_output", ["", "false", "1", "yes"], ids=["absent", "false", "one", "yes"])
def test_full_profile_proceeds_when_the_variable_is_absent_or_off(deploy, monkeypatch, guard_output):
    code, commands, chat, _ = _run_main(deploy, monkeypatch, ["staging", TAG], guard_result=(0, guard_output))
    assert code == 0

    guard_index = next(i for i, c in enumerate(commands) if _is_guard_query(c))
    first_update = next(i for i, c in enumerate(commands) if c.startswith("az containerapp update"))
    assert guard_index < first_update
    assert shlex.split(commands[guard_index]) == [
        "az",
        "containerapp",
        "show",
        "--name",
        STAGING_GATEWAY,
        "--resource-group",
        deploy.RESOURCE_GROUP,
        "--query",
        GUARD_QUERY,
        "--output",
        "tsv",
    ]

    # The full profile itself is unchanged: no environment variables, every app deployed and scaled.
    updates = _updates(commands)
    assert not any("--set-env-vars" in argv for argv in updates)
    named = {_flag_value(argv, "--name") for argv in updates}
    assert STAGING_GATEWAY in named
    assert set(deploy.AGENT_CONTAINER_APPS.values()) <= named
    assert set(deploy.INFRA_CONTAINER_APPS.values()) <= named
    assert deploy.TEST_HOST_APPS["staging"] in named

    # The default smoke runs, not the strict one.
    chat.assert_called_once()
    assert chat.call_args.args == (_fqdn(deploy, "staging"), "staging")
    assert not chat.call_args.kwargs.get("require_validated")


def test_production_full_profile_never_queries_single_container_state(deploy, monkeypatch):
    code, commands, chat, _ = _run_main(deploy, monkeypatch, ["production", TAG, "--confirm"])
    assert code == 0
    assert not any("SINGLE_CONTAINER_MODE" in c for c in commands)
    assert not any("--set-env-vars" in argv for argv in _updates(commands))
    assert not chat.call_args.kwargs.get("require_validated")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ((0, "true"), True),
        ((0, " TRUE\n"), True),
        ((0, ""), False),
        ((0, "false"), False),
        ((0, "1"), False),
        ((1, ""), None),
        ((1, "true"), None),
    ],
)
def test_single_container_enabled_reads_the_gateway_flag(deploy, monkeypatch, result, expected):
    commands: list[str] = []

    def run(cmd: str, timeout: int = 120) -> tuple[int, str]:
        commands.append(cmd)
        return result

    monkeypatch.setattr(deploy, "_run", run)
    assert deploy.single_container_enabled(STAGING_GATEWAY) is expected
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert argv[:3] == ["az", "containerapp", "show"]
    assert _flag_value(argv, "--name") == STAGING_GATEWAY
    assert _flag_value(argv, "--query") == GUARD_QUERY


# ---------------------------------------------------------------------------
# Helpers keep their default behaviour
# ---------------------------------------------------------------------------


def test_deploy_container_default_command_is_unchanged(deploy, monkeypatch):
    commands: list[str] = []
    monkeypatch.setattr(deploy, "_run", lambda cmd, timeout=120: (commands.append(cmd), (0, ""))[1])
    monkeypatch.setattr(deploy, "log", lambda msg: None)
    assert deploy.deploy_container("agent-red-staging", "repo/api-gateway:v1.2.3") is True
    assert commands == [
        "az containerapp update --name agent-red-staging --resource-group Agent-Red "
        "--image repo/api-gateway:v1.2.3 --output none"
    ]


def test_verify_all_containers_default_checks_every_app(deploy, monkeypatch):
    commands: list[str] = []
    monkeypatch.setattr(deploy, "_run", _fake_run(commands, (0, "")))
    monkeypatch.setattr(deploy, "log", lambda msg: None)
    deploy.verify_all_containers(TAG)
    names = {_flag_value(shlex.split(c), "--name") for c in commands}
    expected = {STAGING_GATEWAY, *deploy.AGENT_CONTAINER_APPS.values(), *deploy.INFRA_CONTAINER_APPS.values()}
    assert names == expected


# ---------------------------------------------------------------------------
# Strict smoke
# ---------------------------------------------------------------------------

_REQUIRED_STAGES = ["intent-classifier", "knowledge-retrieval", "response-generator", "critic-supervisor"]


def _stream(*events) -> str:
    """An SSE body built with the gateway's own event factories."""
    stages = [stage_event(s, "completed", latency_ms=5) for s in _REQUIRED_STAGES]
    body = [stage_event("intent-classifier", "started"), token_event("Hello there", 1), *stages, *events]
    return "".join(e.to_sse() for e in body)


_VALIDATED = validated_event("conv-1", "msg-1")
_RETRACTED = retracted_event("I'm sorry, I can't help with that.", "critic_blocked")
_DONE = done_event("conv-1", 1, trace_id="t-1", total_latency_ms=1200)


class _Response:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


@pytest.fixture
def smoke(deploy, monkeypatch):
    """Point verify_chat_conversation at a canned SSE body; returns a setter."""
    monkeypatch.setenv("DEPLOY_SMOKE_WIDGET_KEY", "wk_unit_test_placeholder")
    monkeypatch.setattr(deploy, "log", lambda msg: None)
    created = _Response(201, {"conversation_id": "conv-1", "stream_url": "/api/chat/stream/conv-1"})
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: created)

    def use(body: str) -> None:
        monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _Response(200, text=body))

    return use


def test_strict_smoke_passes_on_validated(deploy, smoke):
    smoke(_stream(_VALIDATED, _DONE))
    assert deploy.verify_chat_conversation("example.test", "staging", require_validated=True) is True


def test_strict_smoke_fails_on_retracted(deploy, smoke):
    smoke(_stream(_RETRACTED, _DONE))
    assert deploy.verify_chat_conversation("example.test", "staging", require_validated=True) is False


def test_strict_smoke_fails_on_missing_validated(deploy, smoke):
    smoke(_stream(_DONE))
    assert deploy.verify_chat_conversation("example.test", "staging", require_validated=True) is False


def test_strict_smoke_fails_when_a_retraction_follows_a_validated_event(deploy, smoke):
    smoke(_stream(_VALIDATED, _RETRACTED, _DONE))
    assert deploy.verify_chat_conversation("example.test", "staging", require_validated=True) is False


def test_strict_smoke_still_applies_the_default_checks(deploy, smoke):
    smoke(_stream(_VALIDATED))  # no done event
    assert deploy.verify_chat_conversation("example.test", "staging", require_validated=True) is False


@pytest.mark.parametrize(
    "events",
    [(_VALIDATED, _DONE), (_RETRACTED, _DONE), (_DONE,)],
    ids=["validated", "retracted", "no-validated"],
)
def test_default_smoke_is_unchanged(deploy, smoke, events):
    """The default smoke ignores validated and retracted events, as before."""
    smoke(_stream(*events))
    assert deploy.verify_chat_conversation("example.test", "staging") is True


def test_default_smoke_still_fails_without_done(deploy, smoke):
    smoke(_stream(_VALIDATED))
    assert deploy.verify_chat_conversation("example.test", "staging") is False
