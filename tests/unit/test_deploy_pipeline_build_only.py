# tests/unit/test_deploy_pipeline_build_only.py: deploy_pipeline --build-only and the staging guard (RC-11, PR13)
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 closed beta: scripts/deploy_pipeline.py gains ``--build-only`` (the beta
build path), and its staging deploy phases refuse while staging runs the
single-container beta (docs/beta-single-container-design.md, sections 3 and 7,
PR13).

What these tests pin down:

- ``--build-only`` runs phases 0 to 7 and stops: no snapshot, no phase 8
  deploy, no phase 15 scaling, no verification, no az query, exit 0.
- ``--env staging`` without ``--dry-run`` and without ``--build-only`` reads
  SINGLE_CONTAINER_MODE from agent-red-staging (deploy.single_container_enabled,
  PR12) after phase 7 and before phase 8. On (True) or unknown (None, the query
  failed) refuses with exit 1 and nothing deployed; off or absent proceeds.
- Dry runs and production deploys never run that query.

Nothing here talks to Azure or the network: every phase, deploy._run, the
rollback capture, the defect writer and sleeps are replaced, and the log and
result files go to a temporary directory.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PIPELINE = PROJECT_ROOT / "scripts" / "deploy_pipeline.py"
DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "deploy.py"
VERSION = "v1.2.3"
STAGING_GATEWAY = "agent-red-staging"

# Every phase function main() calls, with the number its PhaseResult carries.
PHASES: dict[str, int] = {
    "phase_0_validate_environment": 0,
    "phase_1_protected_behaviors": 1,
    "phase_2_clear_vite_api_url": 2,
    "phase_3_build_artifacts": 3,
    "phase_4_freshness_gate": 4,
    "phase_5_restore_env_local": 5,
    "phase_6_create_build_context": 6,
    "phase_7_acr_build": 7,
    "phase_10a_pre_deploy_snapshot": 8,
    "phase_8_deploy": 9,
    "phase_10_startup_and_version": 10,
    "phase_15_enforce_scaling": 15,
    "phase_13_upgrade_verification": 11,
    "phase_14_config_pipeline": 12,
    "phase_13_seed_test_tenant": 13,
    "phase_14_verify_initialized_state": 14,
    "phase_11_production_verification": 11,
}
BUILD_PHASES = (
    "phase_0_validate_environment",
    "phase_1_protected_behaviors",
    "phase_2_clear_vite_api_url",
    "phase_3_build_artifacts",
    "phase_4_freshness_gate",
    "phase_5_restore_env_local",
    "phase_6_create_build_context",
    "phase_7_acr_build",
)
DEPLOY_PHASES = (
    "phase_10a_pre_deploy_snapshot",
    "phase_8_deploy",
    "phase_10_startup_and_version",
    "phase_15_enforce_scaling",
)
STAGING_VERIFY_PHASES = (
    "phase_13_upgrade_verification",
    "phase_14_config_pipeline",
    "phase_13_seed_test_tenant",
    "phase_14_verify_initialized_state",
)


def _load_pipeline():
    """Import scripts/deploy_pipeline.py as a fresh module (scripts/ also holds deploy.py)."""
    for path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location("deploy_pipeline_build_only_under_test", DEPLOY_PIPELINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _phase(pipeline, calls: list[str], name: str, number: int):
    """A phase stand-in that records its name and passes."""

    def run(_args, *_rest):
        calls.append(name)
        result = pipeline.PhaseResult(number, name, "PASS", 0.0)
        return (result, "") if name == "phase_6_create_build_context" else result

    return run


def _run_main(monkeypatch, tmp_path: Path, argv: list[str], guard_answer: tuple[int, str] = (0, "")):
    """Run deploy_pipeline.main() with every phase and side effect replaced.

    guard_answer is what deploy._run returns to the SINGLE_CONTAINER_MODE
    query, as (exit_code, stdout). calls lists the phases in call order, with
    "guard" where deploy._run ran.
    """
    pipeline = _load_pipeline()
    deploy = sys.modules["deploy"]
    assert Path(deploy.__file__).resolve() == DEPLOY_SCRIPT.resolve()

    calls: list[str] = []
    az_commands: list[str] = []

    def fake_deploy_run(cmd: str, timeout: int = 120) -> tuple[int, str]:
        calls.append("guard")
        az_commands.append(cmd)
        return guard_answer

    def no_pipeline_subprocess(cmd, *_args, **_kwargs):
        raise AssertionError(f"unexpected pipeline subprocess: {cmd}")

    monkeypatch.setattr(deploy, "_run", fake_deploy_run)
    for name, number in PHASES.items():
        monkeypatch.setattr(pipeline, name, _phase(pipeline, calls, name, number))
    monkeypatch.setattr(pipeline, "_stream", no_pipeline_subprocess)
    rollback_capture = MagicMock(return_value=f"example.azurecr.io/api-gateway:{VERSION}-prev")
    monkeypatch.setattr("scripts.deploy_config.get_current_image", rollback_capture)
    monkeypatch.setattr(pipeline, "_create_defect_work_item", MagicMock(return_value=None))
    monkeypatch.setattr(pipeline, "PROJECT_ROOT", tmp_path)
    fake_time = MagicMock()
    fake_time.time.side_effect = time.time
    monkeypatch.setattr(pipeline, "time", fake_time)
    monkeypatch.setattr(sys, "argv", ["deploy_pipeline.py", *argv])

    code = pipeline.main()

    (manifest_path,) = (tmp_path / "logs").glob("deploy-result-*.json")
    return SimpleNamespace(
        code=code,
        calls=calls,
        az_commands=az_commands,
        manifest=json.loads(manifest_path.read_text()),
        rollback_capture=rollback_capture,
        log_lines=list(pipeline._log_lines),
    )


def _assert_guard_query(cmd: str) -> None:
    """The one read-only query: az containerapp show on the staging gateway."""
    assert cmd.startswith(f"az containerapp show --name {STAGING_GATEWAY} "), cmd
    assert "SINGLE_CONTAINER_MODE" in cmd
    assert " update " not in cmd


# ---------------------------------------------------------------------------
# --build-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_args",
    [["--env", "staging"], ["--env", "production", "--approved"]],
    ids=["staging", "production"],
)
def test_build_only_stops_after_phase_7(monkeypatch, tmp_path, env_args):
    """Phases 0 to 7 run; phase 8 deploy, phase 15 scaling and everything after never do."""
    result = _run_main(monkeypatch, tmp_path, [*env_args, "--version", VERSION, "--build-only"])

    assert result.code == 0
    assert result.calls == list(BUILD_PHASES)
    assert result.az_commands == []
    result.rollback_capture.assert_not_called()
    assert result.manifest["status"] == "SUCCESS"
    assert [phase["phase"] for phase in result.manifest["phases"]] == list(range(8))


# ---------------------------------------------------------------------------
# Staging guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "guard_answer",
    [(0, "true"), (0, " TRUE \n"), (1, "ERROR: the az query failed")],
    ids=["single_container_on", "single_container_on_any_case", "query_failed"],
)
def test_staging_refused_when_single_container_or_unknown(monkeypatch, tmp_path, guard_answer):
    """On or unknown: the image is built, then exit 1 with no phase 8 call and no write."""
    result = _run_main(monkeypatch, tmp_path, ["--env", "staging", "--version", VERSION], guard_answer)

    assert result.code == 1
    assert result.calls == [*BUILD_PHASES, "guard"]
    assert len(result.az_commands) == 1
    _assert_guard_query(result.az_commands[0])

    assert result.manifest["status"] == "FAILED"
    failed = [phase for phase in result.manifest["phases"] if phase["status"] == "FAIL"]
    assert len(failed) == 1
    assert "SINGLE_CONTAINER_MODE" in failed[0]["detail"]
    assert STAGING_GATEWAY in failed[0]["detail"]

    log_text = "\n".join(result.log_lines)
    assert "--profile single-container" in log_text
    assert "--remove-env-vars SINGLE_CONTAINER_MODE UVICORN_WORKERS" in log_text


@pytest.mark.parametrize(
    "guard_answer",
    [(0, ""), (0, "false")],
    ids=["variable_absent", "variable_false"],
)
def test_staging_proceeds_when_single_container_off(monkeypatch, tmp_path, guard_answer):
    """Off or absent: one query after phase 7 and before phase 8, then the full staging track."""
    result = _run_main(monkeypatch, tmp_path, ["--env", "staging", "--version", VERSION], guard_answer)

    assert result.code == 0
    assert result.calls == [*BUILD_PHASES, "guard", *DEPLOY_PHASES, *STAGING_VERIFY_PHASES]
    assert len(result.az_commands) == 1
    _assert_guard_query(result.az_commands[0])
    assert result.manifest["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# Runs that never query az
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [[], ["--build-only"]], ids=["dry_run", "dry_run_build_only"])
def test_dry_run_never_queries_az(monkeypatch, tmp_path, extra):
    """A staging dry run never runs the SINGLE_CONTAINER_MODE query."""
    result = _run_main(monkeypatch, tmp_path, ["--env", "staging", "--version", VERSION, "--dry-run", *extra])

    assert result.code == 0
    assert result.az_commands == []
    assert "guard" not in result.calls
    if not extra:
        # The dry run reached the deploy phases, so the missing query is the dry-run rule.
        assert result.calls == [*BUILD_PHASES, *DEPLOY_PHASES, *STAGING_VERIFY_PHASES]


def test_production_deploy_never_queries_single_container(monkeypatch, tmp_path):
    """The guard is staging only: a production deploy runs as before, with no query."""
    result = _run_main(monkeypatch, tmp_path, ["--env", "production", "--version", VERSION, "--approved"])

    assert result.code == 0
    assert result.az_commands == []
    assert result.calls == [*BUILD_PHASES, *DEPLOY_PHASES, "phase_11_production_verification"]
    result.rollback_capture.assert_called_once_with("production")
