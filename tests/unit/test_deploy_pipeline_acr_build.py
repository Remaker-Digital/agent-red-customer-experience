"""Phase 7 ACR build of scripts/deploy_pipeline.py: verify the run this phase submitted (review F32).

Phase 7 used to run ``az acr build --no-logs``, ignore its exit code, and then
read the registry's latest run (``az acr task list-runs --top 1``). A failed
submit, or another registry run finishing in between, let an unrelated
Succeeded run plus a pre-existing tag pass the phase, so a stale image could
be deployed.

What these tests pin down:

- The build is queued with ``--no-wait`` (no log streaming, so no Windows
  encoding crash), the run id comes from that submit's "Queued a build with
  ID: <id>" warning, and only that run is polled with ``az acr task
  show-run``. ``list-runs`` is never used.
- A failed submit, or a submit with no run id, fails the phase.
- A run that ends Failed, Canceled, Error or Timeout fails the phase and
  names the run.
- A run still going after the 600 s budget fails the phase and names the run.
- The image tag check still runs after the run succeeds.
- On a registry in ABAC mode (roleAssignmentMode AbacRepositoryPermissions)
  the quick build passes ``--source-acr-auth-id [caller]``; on a classic
  registry, or when the mode cannot be read, it does not.

Nothing here talks to Azure: ``_run`` and the clock are replaced.

(c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PIPELINE = PROJECT_ROOT / "scripts" / "deploy_pipeline.py"

VERSION = "v1.2.3"
RUN_ID = "cb7"
UNRELATED_RUN_ID = "cb9"
ACR = "exampleacr"
ABAC_MODE = "AbacRepositoryPermissions"
CLASSIC_MODE = "LegacyRegistryPermissions"
SUCCEEDED = "Succeeded"
RUNNING = "Running"
SOURCE_AUTH_FLAG = "--source-acr-auth-id"
CALLER = "[caller]"
BUDGET_SECONDS = 600

QUEUED_LINE = f"Queued a build with ID: {RUN_ID}"

# What az prints for a --no-wait submit with stderr merged into stdout (_stream does that).
# The run id appears only in the "Queued a build" warning: with --no-wait az returns no
# result, so stdout carries no bare run id and --query/-o would print nothing.
SUBMIT_OUTPUT = "\n".join(
    [
        "WARNING: Packing source code into tar to upload...",
        f"WARNING: Sending context (12.3 MiB) to registry: {ACR}...",
        f"WARNING: {QUEUED_LINE}",
        "WARNING: You have 2 update(s) available.",
    ]
)


def _load_deploy_pipeline():
    """Load a private copy, as test_deploy_pipeline_build_context.py does."""
    for p in [str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")]:
        if p not in sys.path:
            sys.path.insert(0, p)
    with patch.object(sys, "platform", "linux"):
        spec = importlib.util.spec_from_file_location("deploy_pipeline_acr_build_test", DEPLOY_PIPELINE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class FakeClock:
    """time.time and time.sleep stand-ins: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.start = 1_000_000.0
        self.now = self.start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    @property
    def elapsed(self) -> float:
        return self.now - self.start


class FakeAz:
    """A scripted az CLI behind deploy_pipeline._run; records every command."""

    def __init__(
        self,
        *,
        mode: tuple[int, str] = (0, CLASSIC_MODE),
        submit: tuple[int, str] = (0, SUBMIT_OUTPUT),
        statuses: list[str] | None = None,
        tags: str = VERSION,
    ) -> None:
        self.mode = mode
        self.submit = submit
        self.statuses = list(statuses if statuses is not None else [SUCCEEDED])
        self.tags = tags
        self.commands: list[list[str]] = []
        self.timeouts: list[int] = []
        self.envs: list[dict | None] = []

    def __call__(self, cmd, cwd=None, timeout=600, env=None):  # noqa: ANN001, ANN204
        self.commands.append(list(cmd))
        self.timeouts.append(timeout)
        self.envs.append(env)
        rc, out = self._answer(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=rc, stdout=out, stderr="")

    def _answer(self, cmd: list[str]) -> tuple[int, str]:
        verb = tuple(cmd[1:3])
        if verb == ("acr", "show"):
            return self.mode
        if verb == ("acr", "build"):
            return self.submit
        if verb == ("acr", "task") and cmd[3] == "show-run":
            # The last scripted status repeats once the script runs out.
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return 0, status
        if verb == ("acr", "task") and cmd[3] == "list-runs":
            # The registry's latest run is an unrelated one that succeeded.
            return 0, UNRELATED_RUN_ID if "runId" in " ".join(cmd) else SUCCEEDED
        if verb == ("acr", "repository"):
            return 0, self.tags
        raise AssertionError(f"unexpected command: {cmd}")

    def matching(self, *words: str) -> list[list[str]]:
        return [c for c in self.commands if all(w in c for w in words)]

    @property
    def build_cmd(self) -> list[str]:
        (cmd,) = self.matching("acr", "build")
        return cmd


def _run_phase_7(monkeypatch, az: FakeAz, *, dry_run: bool = False):
    pipeline = _load_deploy_pipeline()
    clock = FakeClock()
    monkeypatch.setattr(pipeline, "_run", az)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=clock.time, sleep=clock.sleep))
    args = argparse.Namespace(dry_run=dry_run, version=VERSION, env="staging")
    result = pipeline.phase_7_acr_build(args, "/tmp/agentred-build-ctx")
    return result, clock


def _assert_no_latest_run_lookup(az: FakeAz) -> None:
    assert az.matching("list-runs") == [], "phase 7 must never read the registry's latest run"


# ---------------------------------------------------------------------------
# The submitted run, not the latest one
# ---------------------------------------------------------------------------


def test_success_verifies_the_submitted_run(monkeypatch):
    az = FakeAz(statuses=["Queued", RUNNING, SUCCEEDED])

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS", result.detail
    assert result.detail == f"run={RUN_ID}"
    assert "--no-wait" in az.build_cmd
    # --only-show-errors would hide the "Queued a build with ID" warning that carries the run id.
    assert "--only-show-errors" not in az.build_cmd
    polls = az.matching("task", "show-run")
    assert len(polls) == 3
    for cmd in polls:
        assert cmd[cmd.index("--run-id") + 1] == RUN_ID
        assert cmd[cmd.index("--registry") + 1] == ACR
    _assert_no_latest_run_lookup(az)


def test_unrelated_succeeded_latest_run_cannot_pass(monkeypatch):
    """The submitted run failed; the registry's latest run succeeded and the tag exists from before."""
    az = FakeAz(statuses=["Failed"], tags=VERSION)

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert RUN_ID in result.detail
    assert UNRELATED_RUN_ID not in result.detail
    _assert_no_latest_run_lookup(az)


@pytest.mark.parametrize(
    "submit_output",
    [
        SUBMIT_OUTPUT,
        # The form Microsoft Learn's quickstart output shows, without a WARNING prefix.
        f"Packing source code into tar to upload...\nSending context (1.856 KiB) to registry: {ACR}...\n{QUEUED_LINE}",
        f"\x1b[33mWARNING: {QUEUED_LINE}\x1b[0m",
    ],
    ids=["warning_prefix", "no_prefix", "ansi_colour"],
)
def test_run_id_is_parsed_from_merged_warning_output(monkeypatch, submit_output):
    az = FakeAz(submit=(0, submit_output), statuses=[f"WARNING: an az notice\n{SUCCEEDED}"])

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS", result.detail
    assert result.detail == f"run={RUN_ID}"
    for cmd in az.matching("task", "show-run"):
        assert cmd[cmd.index("--run-id") + 1] == RUN_ID


# ---------------------------------------------------------------------------
# Submit failures
# ---------------------------------------------------------------------------


def test_failed_submit_fails(monkeypatch):
    az = FakeAz(submit=(1, "ERROR: (TasksOperationsNotAllowed) ACR Tasks requests are not permitted"))

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert "submit" in result.detail.lower()
    assert az.matching("task", "show-run") == []
    assert az.matching("repository", "show-tags") == []
    _assert_no_latest_run_lookup(az)


def test_submit_without_run_id_fails(monkeypatch):
    az = FakeAz(submit=(0, "WARNING: Packing source code into tar to upload...\n"))

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert "run id" in result.detail.lower()
    assert az.matching("task", "show-run") == []
    assert az.matching("repository", "show-tags") == []
    _assert_no_latest_run_lookup(az)


# ---------------------------------------------------------------------------
# Run outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal", ["Failed", "Canceled", "Error", "Timeout"])
def test_non_succeeded_run_fails_naming_the_run(monkeypatch, terminal):
    az = FakeAz(statuses=[RUNNING, terminal, SUCCEEDED])

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert RUN_ID in result.detail
    assert terminal in result.detail
    # Polling stops at the first terminal status.
    assert len(az.matching("task", "show-run")) == 2
    assert az.matching("repository", "show-tags") == []


def test_run_still_going_after_budget_fails_naming_the_run(monkeypatch):
    az = FakeAz(statuses=[RUNNING])

    result, clock = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert RUN_ID in result.detail
    assert RUNNING in result.detail
    assert str(BUDGET_SECONDS) in result.detail
    assert clock.sleeps, "the phase polls with sleeps between status reads"
    assert clock.elapsed <= BUDGET_SECONDS
    assert len(az.matching("task", "show-run")) > 1
    assert az.matching("repository", "show-tags") == []
    _assert_no_latest_run_lookup(az)


# ---------------------------------------------------------------------------
# Tag check
# ---------------------------------------------------------------------------


def test_tag_check_runs_after_the_run_succeeds(monkeypatch):
    az = FakeAz(statuses=[SUCCEEDED], tags=VERSION)

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS", result.detail
    tag_checks = az.matching("repository", "show-tags")
    assert len(tag_checks) == 1
    assert az.commands.index(tag_checks[0]) > az.commands.index(az.matching("task", "show-run")[-1])


def test_missing_tag_after_succeeded_run_fails(monkeypatch):
    az = FakeAz(statuses=[SUCCEEDED], tags="")

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "FAIL"
    assert VERSION in result.detail


# ---------------------------------------------------------------------------
# ABAC registries
# ---------------------------------------------------------------------------


def test_abac_registry_builds_with_caller_identity(monkeypatch):
    az = FakeAz(mode=(0, ABAC_MODE))

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS", result.detail
    cmd = az.build_cmd
    assert cmd[cmd.index(SOURCE_AUTH_FLAG) + 1] == CALLER
    (mode_query,) = az.matching("acr", "show")
    assert mode_query[mode_query.index("--name") + 1] == ACR
    assert mode_query[mode_query.index("--query") + 1] == "roleAssignmentMode"
    assert az.commands.index(mode_query) < az.commands.index(cmd)


@pytest.mark.parametrize(
    "mode",
    [(0, CLASSIC_MODE), (0, ""), (1, "ERROR: the az query failed")],
    ids=["classic", "mode_absent", "mode_query_failed"],
)
def test_non_abac_registry_builds_without_source_auth_flag(monkeypatch, mode):
    az = FakeAz(mode=mode)

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS", result.detail
    assert SOURCE_AUTH_FLAG not in az.build_cmd


def test_dry_run_runs_no_az(monkeypatch):
    az = FakeAz()

    result, _ = _run_phase_7(monkeypatch, az, dry_run=True)

    assert result.status == "PASS"
    assert az.commands == []


def test_mode_query_and_run_polls_are_capped_well_inside_the_budget(monkeypatch):
    """One slow read cannot use the whole 600 s build budget."""
    az = FakeAz(statuses=["Queued", "Running", SUCCEEDED])

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS"
    reads = [t for c, t in zip(az.commands, az.timeouts, strict=True) if c[1:3] == ["acr", "show"] or "show-run" in c]
    assert len(reads) == 4
    assert all(t <= 60 for t in reads)


def test_the_submit_cannot_have_its_run_id_warning_suppressed(monkeypatch):
    """core.only_show_errors would hide "Queued a build with ID"; the submit clears it."""
    monkeypatch.setenv("AZURE_CORE_ONLY_SHOW_ERRORS", "true")
    az = FakeAz()

    result, _ = _run_phase_7(monkeypatch, az)

    assert result.status == "PASS"
    submit_env = az.envs[az.commands.index(az.build_cmd)]
    assert submit_env is not None and submit_env["AZURE_CORE_ONLY_SHOW_ERRORS"] == "false"
    others = [env for cmd, env in zip(az.commands, az.envs, strict=True) if cmd is not az.build_cmd]
    assert all(env is None for env in others)


def test_no_poll_starts_or_runs_past_the_deadline(monkeypatch):
    pipeline = _load_deploy_pipeline()
    clock = FakeClock()
    az = FakeAz(statuses=["Running"])
    monkeypatch.setattr(pipeline, "_run", az)
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(time=clock.time, sleep=clock.sleep))

    status = pipeline._wait_for_acr_run("cb7", clock.now + 20)

    assert status == "Running"
    # Polls at 0 s (20 s left) and 15 s (5 s left); none at the 20 s deadline.
    assert [t for c, t in zip(az.commands, az.timeouts, strict=True) if "show-run" in c] == [20, 5]
    assert clock.elapsed == 20
