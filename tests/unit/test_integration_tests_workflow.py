# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Static checks of .github/workflows/integration-tests.yml.

The workflow runs the "integrations" pytest shard of the Azure DevOps release
pipeline on every pull request. The v2.0.3 release run (build 50) first ran
these suites after the merge and the tag, and four stale tests stopped it. The
paths, markers and ignores are read from azure-pipelines.yml here, so the pull
request check and the release shard cannot drift apart. Nothing is run.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "integration-tests.yml"
PIPELINE = REPO_ROOT / "azure-pipelines.yml"


@lru_cache(maxsize=1)
def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers() -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    workflow = _workflow()
    return workflow["on"] if "on" in workflow else workflow[True]


def _pytest_steps() -> list[dict]:
    return [
        step for job in _workflow()["jobs"].values() for step in job["steps"] if "pytest" in str(step.get("run", ""))
    ]


@lru_cache(maxsize=1)
def _shard_command() -> str:
    pipeline = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    test_stage = next(stage for stage in pipeline["stages"] if stage["stage"] == "Test")
    job = next(job for job in test_stage["jobs"] if job["job"] == "PytestShards")
    return next(step["script"] for step in job["steps"] if "pytest" in str(step.get("script", "")))


def _shard_paths() -> list[str]:
    match = re.search(r'integrations\)\s*\n\s*TEST_ARGS="([^"]*)"', _shard_command())
    assert match, "azure-pipelines.yml has no integrations shard"
    return match.group(1).split()


def _pytest_paths(command: str) -> list[str]:
    """The positional test paths on the pytest line, in order."""
    words = command.replace("\\\n", " ").split()
    start = words.index("pytest") + 1
    paths = []
    for word in words[start:]:
        if word.startswith("-"):
            break
        paths.append(word)
    return paths


def test_runs_on_every_pull_request_without_filters():
    triggers = _triggers()
    assert "pull_request" in triggers
    # A filtered workflow that does not run never reports its check.
    assert triggers["pull_request"] is None


def test_runs_the_same_paths_as_the_release_pipeline_shard():
    steps = _pytest_steps()
    assert len(steps) == 1
    assert _pytest_paths(steps[0]["run"]) == _shard_paths()
    assert "tests/integrations" in _shard_paths()


def test_keeps_the_release_shard_marker_and_live_suite_ignores():
    command = _pytest_steps()[0]["run"]
    assert '-m "not local_env"' in command
    assert "-n auto" in command
    ignores = re.findall(r"--ignore=\S+", _shard_command())
    assert ignores, "azure-pipelines.yml shard lists no ignores"
    for ignore in ignores:
        assert ignore in command
    for live_suite in ("tests/e2e_live", "tests/live_api", "test_upgrade_regression"):
        assert live_suite not in " ".join(_pytest_paths(command))


def test_a_failing_test_fails_the_check():
    step = _pytest_steps()[0]
    assert "set -euo pipefail" in step["run"]
    assert "|| true" not in step["run"]
    assert not step.get("continue-on-error")
    for job in _workflow()["jobs"].values():
        assert not job.get("continue-on-error")


def test_needs_no_secrets_and_reads_only():
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")
    assert _workflow()["permissions"] == {"contents": "read"}


def test_python_version_matches_the_release_pipeline():
    pipeline = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    version = next(
        v["value"] for v in pipeline["variables"] if isinstance(v, dict) and v.get("name") == "pythonVersion"
    )
    setup = [
        step
        for job in _workflow()["jobs"].values()
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert len(setup) == 1
    assert str(setup[0]["with"]["python-version"]) == str(version)
