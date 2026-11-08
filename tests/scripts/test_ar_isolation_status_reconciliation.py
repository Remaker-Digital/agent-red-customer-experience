"""Current application registry facts; historical measurements are not authority."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_REGISTRY = REPO_ROOT / ".gtkb-app-isolation.json"


def _registry():
    return json.loads(APP_REGISTRY.read_text(encoding="utf-8"))


def _artifact_entry(name):
    matches = [e for e in _registry()["top_level_artifacts"] if e["name"] == name]
    assert len(matches) == 1
    return matches[0]


# Owner principle (2026-10-06): agents working within GT-KB use GT-KB's rules,
# hooks, skills and configuration, whether the session's subject is GT-KB or an
# application. Agent Red carries no agent instructions or harness configuration
# of its own. These replace the earlier checks that .claude and .codex were
# "application-owned" configuration.
AGENT_CONFIGURATION = {".claude", ".codex", ".agents", "CLAUDE.md", "AGENTS.md"}


def test_registry_lists_no_application_agent_configuration():
    names = {e["name"] for e in _registry()["top_level_artifacts"]}
    assert not (AGENT_CONFIGURATION | {"CLAUDE-ARCHITECTURE.md", "CLAUDE-REFERENCE.md", "CLAUDE_ARCHIVE.md"}) & names


def test_agent_red_registry_uses_current_classifications_without_retired_authority_claims():
    registry = _registry()
    assert registry["application"] == "Agent_Red" and registry["schema_version"] == "2.0"
    assert registry["isolation_contract_adr"] == "ADR-APPLICATION-ISOLATION-CONTRACT-001"
    assert registry["minimization_principle_dcl"] == "DCL-APP-ROOT-MINIMIZATION-001"
    entries = registry["top_level_artifacts"]
    for entry in entries:
        assert entry["name"] and entry["type"] in {"FILE", "DIR"} and entry["purpose"]
        assert entry["classification"] in {
            "authoritative_input",
            "generated_output",
            "runtime_data",
            "bounded_temporary_output",
        }
        assert "bucket" not in entry
    assert "terraform" in {e["name"] for e in entries}
    assert not {"assets", ".vscode", "harness-state"} & {e["name"] for e in entries}
    assert _artifact_entry("terraform")["classification"] == "authoritative_input"
    assert (
        not {
            "established_by",
            "last_updated",
            "out_of_scope_for_sub_slice_1",
            "known_limitations",
            "validator_contract",
        }
        & registry.keys()
    )


def test_repository_tracks_no_application_agent_configuration():
    import shutil
    import subprocess

    import pytest

    git = shutil.which("git")
    if git is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = subprocess.run(
        [git, "ls-files", "--", *sorted(AGENT_CONFIGURATION)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked == [], tracked
