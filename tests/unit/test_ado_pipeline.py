"""Static checks of the Azure DevOps pipeline, azure-pipelines.yml (closed beta, RC-11).

The pipeline is parsed with yaml.safe_load and nothing runs: every
`python scripts/...` command it holds must name a script that exists and pass
only the flags and choices that script's argparse definitions declare (read
from the script's AST, so no script is imported), the stages must keep their
order and guards, and no credential may appear in the file.

Every Semgrep command, here and in .github/workflows/security-scan.yml, must
exclude docs/owner-messages-*, which hold real credentials, so no match from
them reaches a CI log or report artifact. The flag must be one quoted word,
--exclude="docs/owner-messages-*": unquoted, the shell expands the pattern into
those files, and as a separate word Semgrep's Windows binary expands it the
same way; either way the files become explicit targets and are scanned.

Owner steps: docs/operations/CLOSED-BETA-RUNBOOK.md. Its `az containerapp`
commands must not print the whole container app (plain env values included),
and until PR8 adds the beta `/ready` report, every use of that report's names
must be labeled pending PR8.

(c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from functools import cache, lru_cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / "azure-pipelines.yml"
RUNBOOK = REPO_ROOT / "docs" / "operations" / "CLOSED-BETA-RUNBOOK.md"

SCRIPT_KEYS = {"script", "bash", "pwsh", "powershell", "inlineScript"}
PYTHON_SCRIPT_COMMAND = re.compile(r"\bpython\s+(scripts/[\w./-]+\.py)([^\n]*)")
LIVE_SUITES = ("tests/e2e_live", "tests/live_api", "test_upgrade_regression")
PRODUCTION_ONLY_EXCLUSIONS = REPO_ROOT / "docs" / "operations" / "ADO-NONPROD-TEST-EXCLUSIONS.md"
EXPECTED_TEST_JOBS = {
    "ValidateRelease",
    "NonFunctionalGates",
    "FrontendVitest",
    "DocsQuality",
    "PytestShards",
    "PlaywrightMock",
    "PlaywrightUi",
    "CoverageGate",
}
EXPECTED_PYTEST_SHARDS = {
    "unit",
    "core",
    "agents-chat",
    "integrations",
    "regression-offline",
    "extended",
    "transport-flows",
}
SECRET_PATTERNS = {
    "Agent Red or Stripe key": re.compile(
        r"\b(?:ar_(?:live|spa|user)|pk_live|sk_live|sk_test|rk_live)_[A-Za-z0-9_]{6,}"
    ),
    "OpenAI key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "connection string part": re.compile(
        r"AccountKey=|SharedAccessSignature=|SharedAccessKey=|InstrumentationKey=|DefaultEndpointsProtocol=",
        re.IGNORECASE,
    ),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "long base64 or hex literal": re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    "assigned credential": re.compile(
        r"(?i)\b[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)\s*[:=]\s*['\"]?(?!\$)[A-Za-z0-9+/_\-]{8,}"
    ),
}
CREDENTIAL_ENV_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CONNECTION_STRING")
MACRO = re.compile(r"^\$\(([A-Za-z0-9_.]+)\)$")
CONTAINERAPP_COMMAND = re.compile(r"\baz containerapp\s+([a-z-]+(?:\s+[a-z-]+)?)")
QUIET_OUTPUT = re.compile(r"--query\b|--output none\b|-o none\b")
SECURITY_SCAN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security-scan.yml"
OWNER_MESSAGES_GLOB = "docs/owner-messages-*"
# A line that starts with `semgrep`, with its backslash continuation lines.
SEMGREP_COMMAND = re.compile(r"^[ \t]*semgrep\b(?:.*\\\n)*.*", re.MULTILINE)
OWNER_MESSAGES_EXCLUDE = re.compile(r"--exclude=([\"'])" + re.escape(OWNER_MESSAGES_GLOB) + r"\1")


@lru_cache(maxsize=1)
def _pipeline() -> dict:
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def _stage(name: str) -> dict:
    stages = {stage["stage"]: stage for stage in _pipeline()["stages"]}
    return stages[name]


def _walk(node, key=None):
    """Yield (key, value) for every mapping entry below node, depth first."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v, k)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def _scripts(node) -> list[str]:
    return [v for k, v in _walk(node) if k in SCRIPT_KEYS and isinstance(v, str)]


def _env_maps(node) -> list[dict]:
    return [v for k, v in _walk(node) if k == "env" and isinstance(v, dict)]


def _tasks(node, prefix: str) -> list[dict]:
    found = []
    for _, value in _walk(node):
        if isinstance(value, list):
            found.extend(
                step for step in value if isinstance(step, dict) and str(step.get("task", "")).startswith(prefix)
            )
    return found


def _python_commands(node) -> list[tuple[str, list[str]]]:
    commands = []
    for text in _scripts(node):
        for match in PYTHON_SCRIPT_COMMAND.finditer(text):
            commands.append((match.group(1), shlex.split(match.group(2))))
    return commands


@cache
def _argparse_spec(script: str) -> tuple[dict, list]:
    """Options and positionals declared by add_argument calls, read from the script's AST."""
    tree = ast.parse((REPO_ROOT / script).read_text(encoding="utf-8"))
    options: dict[str, dict] = {}
    positionals: list[tuple[int, object]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "add_argument":
            continue
        names = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        choices = ast.literal_eval(keywords["choices"]) if "choices" in keywords else None
        action = keywords.get("action")
        flag_only = isinstance(action, ast.Constant) and action.value in {"store_true", "store_false", "count"}
        required = isinstance(keywords.get("required"), ast.Constant) and keywords["required"].value is True
        if names and names[0].startswith("-"):
            for name in names:
                options[name] = {"takes_value": not flag_only, "choices": choices, "required": required}
        elif names:
            positionals.append((node.lineno, choices))
    positionals.sort(key=lambda item: item[0])
    return options, [choices for _, choices in positionals]


def _is_variable(value: str) -> bool:
    return value.startswith("$")


def _check_command(script: str, tokens: list[str]) -> None:
    assert (REPO_ROOT / script).is_file(), f"{script} does not exist"
    options, positionals = _argparse_spec(script)
    seen: set[str] = set()
    position = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            name, has_inline, inline = token.partition("=")
            assert name in options, f"{script} defines no flag {name}"
            seen.add(name)
            option = options[name]
            if option["takes_value"]:
                if has_inline:
                    value = inline
                    index += 1
                else:
                    assert index + 1 < len(tokens), f"{script} {name} needs a value"
                    value = tokens[index + 1]
                    index += 2
                if option["choices"] is not None and not _is_variable(value):
                    allowed = [str(choice) for choice in option["choices"]]
                    assert value in allowed, f"{script} {name} {value!r} is not one of {allowed}"
            else:
                assert not has_inline, f"{script} {name} takes no value"
                index += 1
        else:
            assert position < len(positionals), f"{script} takes no positional argument {token!r}"
            choices = positionals[position]
            if choices is not None and not _is_variable(token):
                assert token in [str(choice) for choice in choices], f"{script} {token!r} is not one of {choices}"
            position += 1
            index += 1
    assert position == len(positionals), f"{script} needs {len(positionals)} positional arguments, got {position}"
    missing = sorted(name for name, option in options.items() if option["required"] and name not in seen)
    assert not missing, f"{script} needs {missing}"


def test_runs_only_by_hand_and_leaves_pull_requests_to_github_actions():
    pipeline = _pipeline()
    assert pipeline["trigger"] == "none"
    assert pipeline["pr"] == "none"
    assert re.search(r"\$\(Date:yyyyMMdd\)\$\(Rev:\.r\)", pipeline["name"])


def test_stages_in_order_with_their_dependencies():
    stages = _pipeline()["stages"]
    assert [stage["stage"] for stage in stages] == ["Test", "Build", "Staging", "Production"]
    assert "dependsOn" not in stages[0]
    assert _stage("Build")["dependsOn"] == "Test"
    assert _stage("Staging")["dependsOn"] == "Build"


def test_parameters():
    parameters = {parameter["name"]: parameter for parameter in _pipeline()["parameters"]}
    assert list(parameters) == ["version", "deployStaging", "minReplicas", "deployProduction"]
    assert parameters["version"]["type"] == "string"
    assert "default" not in parameters["version"], "the version has no default, so every run must name one"
    assert parameters["deployStaging"]["type"] == "boolean" and parameters["deployStaging"]["default"] is False
    assert parameters["minReplicas"]["values"] == [0, 1] and parameters["minReplicas"]["default"] == 1
    production = parameters["deployProduction"]
    assert production["type"] == "boolean" and production["default"] is False
    assert "not used" in production["displayName"].lower() and "closed beta" in production["displayName"].lower()


def test_variables():
    variables = _pipeline()["variables"]
    assert {"group": "agent-red-azure"} in variables
    named = {entry["name"]: entry["value"] for entry in variables if "name" in entry}
    assert named["azureServiceConnection"] == "agent-red-azure"
    assert named["pythonVersion"] == "3.12"
    assert named["nodeVersion"].startswith("22")
    assert named["RELEASE_VERSION"] == "${{ parameters.version }}"
    assert named["MIN_REPLICAS"] == "${{ parameters.minReplicas }}"


def test_the_version_is_validated_in_a_step():
    texts = " ".join(_scripts(_stage("Test")))
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in texts
    assert "src/multi_tenant/api_versioning.py" in texts


def test_every_python_script_command_matches_its_argparse_definitions():
    commands = _python_commands(_pipeline())
    assert {script for script, _ in commands} == {
        "scripts/deploy_pipeline.py",
        "scripts/deploy.py",
        "scripts/detect_import_cycles.py",
    }
    for script, tokens in commands:
        if script == "scripts/detect_import_cycles.py":
            # Positional package root only (no argparse in that script).
            assert tokens == ["src"]
            continue
        _check_command(script, tokens)


def test_the_command_checker_refuses_unknown_flags_and_choices():
    with pytest.raises(AssertionError):
        _check_command("scripts/deploy.py", ["staging", "$V", "--profile", "beta"])
    with pytest.raises(AssertionError):
        _check_command("scripts/deploy.py", ["staging", "$V", "--build-only"])
    with pytest.raises(AssertionError):
        _check_command("scripts/deploy_pipeline.py", ["--env", "staging", "--build-only"])
    with pytest.raises(AssertionError):
        _check_command("scripts/deploy.py", ["qa", "$V"])


def test_build_runs_deploy_pipeline_build_only_on_windows_with_the_service_connection():
    build = _stage("Build")
    commands = _python_commands(build)
    assert commands == [
        ("scripts/deploy_pipeline.py", ["--env", "staging", "--version", "$env:RELEASE_VERSION", "--build-only"])
    ]
    # Phase 5 starts `powershell`, which Linux agents do not have.
    assert build["jobs"][0]["pool"]["vmImage"].startswith("windows")
    azure_cli = _tasks(build, "AzureCLI@2")
    assert len(azure_cli) == 1
    installs = " ".join(_scripts(build))
    for folder in ("'admin'", "'admin/standalone'", "'admin/provider'", "'admin/shopify'", "'widget'"):
        assert folder in installs
    assert "npm ci" in installs


def test_staging_deploys_the_single_container_profile_on_request_only():
    staging = _stage("Staging")
    condition = staging["condition"]
    assert "succeeded()" in condition
    assert "eq('${{ parameters.deployStaging }}', true)" in condition
    assert "ne(variables['Build.Reason'], 'PullRequest')" in condition
    job = staging["jobs"][0]
    assert job["deployment"] and job["environment"] == "agent-red-staging"
    commands = _python_commands(staging)
    assert len(commands) == 1
    script, tokens = commands[0]
    assert script == "scripts/deploy.py"
    assert tokens[0] == "staging"
    assert tokens[tokens.index("--profile") + 1] == "single-container"
    assert tokens[tokens.index("--min-replicas") + 1] == "$MIN_REPLICAS"
    azure_cli = _tasks(staging, "AzureCLI@2")
    assert len(azure_cli) == 2


def test_every_azure_task_names_the_service_connection_at_compile_time():
    """A service connection must be known when the pipeline compiles; a $( ) macro is runtime."""
    tasks = _tasks(_pipeline(), "AzureCLI@2")
    assert len(tasks) == 6
    for task in tasks:
        assert task["inputs"]["azureSubscription"] == "${{ variables.azureServiceConnection }}"


def test_staging_requires_the_new_revision_ready_healthy_and_on_the_tag():
    steps = _stage("Staging")["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
    scripts = [step.get("inputs", {}).get("inlineScript", "") for step in steps]
    deploy = next(i for i, text in enumerate(scripts) if "scripts/deploy.py staging" in text)
    check = next(i for i, text in enumerate(scripts) if "latestReadyRevisionName" in text)
    assert check > deploy
    text = scripts[check]
    assert f"app={_deploy_py_constant('CONTAINER_APPS')['staging']}" in text
    assert f"--resource-group {_deploy_py_constant('RESOURCE_GROUP')} " in text
    assert '"$latest" == "$ready"' in text and '"$health" == "Healthy"' in text
    assert '":$RELEASE_VERSION"' in text and text.rstrip().endswith("exit 1")


def test_production_depends_on_staging_and_requires_deploy_production():
    production = _stage("Production")
    assert "Staging" in production["dependsOn"]
    condition = production["condition"]
    assert "succeeded()" in condition
    assert "eq('${{ parameters.deployProduction }}', true)" in condition
    assert "eq('${{ parameters.deployStaging }}', true)" in condition
    job = production["jobs"][0]
    assert job["deployment"] and job["environment"] == "agent-red-production"
    commands = _python_commands(production)
    assert len(commands) == 1
    script, tokens = commands[0]
    assert script == "scripts/deploy.py" and tokens[0] == "production" and "--confirm" in tokens
    assert "--profile" not in tokens, "production runs the full profile"


def _deploy_py_constant(name: str):
    """A literal assigned in scripts/deploy.py, read from its AST (never imported:
    importing it loads .env.local through deploy_config)."""
    tree = ast.parse((REPO_ROOT / "scripts" / "deploy.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = (
            node.target
            if isinstance(node, ast.AnnAssign)
            else (node.targets[0] if isinstance(node, ast.Assign) else None)
        )
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in scripts/deploy.py")


def test_production_requires_every_full_profile_image_before_deploying():
    steps = _stage("Production")["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
    scripts = [step.get("inputs", {}).get("inlineScript", "") for step in steps]
    preflight = next(i for i, text in enumerate(scripts) if "show-tags" in text)
    deploy = next(i for i, text in enumerate(scripts) if "scripts/deploy.py production" in text)
    assert preflight < deploy, "the image preflight must run before the deploy"
    text = scripts[preflight]
    repos = set(re.search(r"for repo in ([^;]+); do", text).group(1).split())
    expected = {"api-gateway", "test-host"}
    expected |= set(_deploy_py_constant("AGENT_CONTAINER_APPS")) | set(_deploy_py_constant("INFRA_CONTAINER_APPS"))
    assert repos == expected
    assert f"--name {_deploy_py_constant('ACR_NAME')} " in text
    assert "set -euo pipefail" in text and "exit 1" in text


def test_production_requires_every_full_profile_app_on_the_release_tag_after_deploying():
    steps = _stage("Production")["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
    scripts = [step.get("inputs", {}).get("inlineScript", "") for step in steps]
    deploy = next(i for i, text in enumerate(scripts) if "scripts/deploy.py production" in text)
    verify = next(i for i, text in enumerate(scripts) if "latestRevisionName" in text)
    assert verify > deploy, "the app check must run after the deploy"
    text = scripts[verify]
    entries = re.search(r'apps="([^"]+)"', text).group(1).split()
    scaled = {entry.split(":", 1)[0]: entry.split(":", 1)[1] for entry in entries if ":" in entry}
    unscaled = {entry for entry in entries if ":" not in entry}
    targets = {_deploy_py_constant("CONTAINER_APPS")["production"]}
    targets |= set(_deploy_py_constant("AGENT_CONTAINER_APPS").values())
    targets |= set(_deploy_py_constant("INFRA_CONTAINER_APPS").values())
    assert unscaled == {_deploy_py_constant("TEST_HOST_APPS")["production"]}
    assert set(scaled) == targets
    config = _deploy_py_constant("SCALING_CONFIG")
    assert scaled == {app: f"{config[app]['min_replicas']}:{config[app]['max_replicas']}" for app in targets}
    assert text.count(f"--resource-group {_deploy_py_constant('RESOURCE_GROUP')} ") == 4
    assert '":$RELEASE_VERSION"' in text and '"Healthy"' in text and '"$replicas" == "$scale"' in text
    # One clean pass over every app, not apps dropped as they first pass.
    assert "for entry in $apps; do" in text and "pending" not in text
    assert "set -euo pipefail" in text and text.rstrip().endswith("exit 1")


def test_production_job_timeout_covers_the_full_profile_slow_path():
    job = _stage("Production")["jobs"][0]
    # 9 x 180 s image updates + 8 x 120 s scaling updates = 43 min, plus checks.
    assert job["timeoutInMinutes"] >= 60


def test_build_runs_only_from_the_release_tag():
    steps = _stage("Build")["jobs"][0]["steps"]
    gate = steps[0]
    assert gate["displayName"] == "Require the release tag"
    assert '$env:BUILD_SOURCEBRANCH -cne "refs/tags/$env:RELEASE_VERSION"' in gate["pwsh"]
    assert "exit 1" in gate["pwsh"]
    assert "the tag `vX.Y.Z` (required" in RUNBOOK.read_text(encoding="utf-8")


def test_runbook_removes_both_rate_limit_bypasses():
    text = RUNBOOK.read_text(encoding="utf-8")
    removal = next(line for line in text.splitlines() if line.startswith("$remove = "))
    inspection = next(line for line in text.splitlines() if line.startswith("$show = "))
    for name in ("DISABLE_RATE_LIMITING", "RATE_LIMIT_DISABLED"):
        assert name in removal and f"'{name}'" in inspection


def test_test_stage_jobs_cover_full_nonprod_matrix():
    jobs = {job["job"] for job in _stage("Test")["jobs"]}
    assert jobs == EXPECTED_TEST_JOBS


def test_pytest_shards_cover_ci_safe_directories():
    pytest_job = next(job for job in _stage("Test")["jobs"] if job["job"] == "PytestShards")
    shards = {entry["SHARD"] for entry in pytest_job["strategy"]["matrix"].values()}
    assert shards == EXPECTED_PYTEST_SHARDS
    command = next(text for text in _scripts(pytest_job) if "pytest" in text)
    for required in (
        "tests/unit",
        "tests/multi_tenant",
        "tests/agents",
        "tests/integrations",
        "tests/regression/test_migration_compat.py",
        "tests/test_host",
        "tests/transport",
    ):
        assert required in command
    assert "--ignore=tests/e2e_live" in command
    assert "--ignore=tests/live_api" in command
    assert "--ignore=tests/regression/test_upgrade_regression.py" in command
    assert "--ignore=tests/integration" in command
    assert '-m "not local_env"' in command
    test_args = "\n".join(re.findall(r'TEST_ARGS="([^"]*)"', command))
    assert "tests/e2e_live" not in test_args
    assert "tests/live_api" not in test_args
    assert "test_upgrade_regression" not in test_args


def test_tests_gate_the_build_without_live_suites():
    commands = [text for text in _scripts(_stage("Test")) if "pytest" in text]
    assert len(commands) >= 3  # matrix shards + playwright-mock + playwright-ui
    joined = "\n".join(commands)
    for command in commands:
        test_args = "\n".join(re.findall(r'TEST_ARGS="([^"]*)"', command))
        # Playwright jobs have no TEST_ARGS; they name paths on the pytest line.
        collected = test_args if test_args else command.split("--ignore=")[0]
        assert "tests/e2e_live" not in collected
        assert "tests/live_api" not in collected
        assert "test_upgrade_regression" not in collected
    assert "ruff check src/ tests/ test_host/ --select E,F" in " ".join(_scripts(_stage("Test")))
    assert "ruff format --check" in " ".join(_scripts(_stage("Test")))
    for node_id in re.findall(r"--deselect (\S+)", joined):
        assert (REPO_ROOT / node_id.split("::")[0]).is_file(), f"stale deselect {node_id}"


def test_playwright_ui_job_covers_mocked_spa_suites():
    ui_job = next(job for job in _stage("Test")["jobs"] if job["job"] == "PlaywrightUi")
    matrix = ui_job["strategy"]["matrix"]
    paths = {entry["PYTEST_PATH"] for entry in matrix.values()}
    assert paths == {
        "tests/e2e",
        "tests/visual",
        "tests/provider_visual",
        "tests/provider_e2e",
        "tests/shopify_e2e",
    }
    shopify = matrix["shopify-e2e"]
    assert shopify["NPM_DIRS"] == "admin admin/shopify"
    assert shopify["SUITE"] == "shopify_e2e"
    command = next(text for text in _scripts(ui_job) if "pytest" in text)
    assert "$(PYTEST_PATH)" in command
    assert "--ignore=tests/e2e_live" in command
    assert "playwright install" in " ".join(_scripts(ui_job))
    exclusions = PRODUCTION_ONLY_EXCLUSIONS.read_text(encoding="utf-8")
    assert "tests/helpers/" in exclusions
    assert "tests/rag-documents-upload/" in exclusions
    assert "playwright-ui" in exclusions
    assert "tests/shopify_e2e" in exclusions


def test_nonfunctional_gates_match_gha_lint_and_security():
    texts = " ".join(_scripts(_stage("Test")))
    for needle in (
        "scripts/detect_import_cycles.py",
        "pip-audit --ignore-vuln PYSEC-2026-3740",
        "bandit -r src/ test_host/",
        "semgrep --config p/python",
        "radon cc src/",
        "vulture src/",
    ):
        assert needle in texts


def _semgrep_commands(scripts: list[str]) -> list[str]:
    """Every semgrep command in the scripts, continuation lines included."""
    return [match.group(0) for text in scripts for match in SEMGREP_COMMAND.finditer(text)]


def _commands_scanning_owner_messages(commands: list[str]) -> list[str]:
    """The semgrep commands that lack --exclude="docs/owner-messages-*" as one quoted word."""
    return [command for command in commands if not OWNER_MESSAGES_EXCLUDE.search(command)]


def test_semgrep_in_the_pipeline_never_scans_owner_messages():
    commands = _semgrep_commands(_scripts(_pipeline()))
    assert len(commands) == 2, f"expected the report run and the log run, found {commands}"
    assert _commands_scanning_owner_messages(commands) == []


def test_semgrep_in_the_security_scan_workflow_never_scans_owner_messages():
    workflow = yaml.safe_load(SECURITY_SCAN_WORKFLOW.read_text(encoding="utf-8"))
    runs = [value for key, value in _walk(workflow) if key == "run" and isinstance(value, str)]
    commands = _semgrep_commands(runs)
    assert len(commands) == 2, f"expected the report run and the log run, found {commands}"
    assert _commands_scanning_owner_messages(commands) == []


def test_the_owner_messages_check_catches_missing_unquoted_and_separate_excludes():
    missing = "semgrep --config p/secrets \\\n  --exclude .git \\\n  ."
    unquoted = f"semgrep --config p/secrets --exclude {OWNER_MESSAGES_GLOB} ."
    separate = f'semgrep --config p/secrets --exclude "{OWNER_MESSAGES_GLOB}" .'
    one_word = f'semgrep --config p/secrets \\\n  --exclude="{OWNER_MESSAGES_GLOB}" \\\n  . || true'
    scripts = [f"{missing}\n{unquoted}", separate, one_word, "python -m pip install semgrep"]
    commands = _semgrep_commands(scripts)
    assert commands == [missing, unquoted, separate, one_word]
    assert _commands_scanning_owner_messages(commands) == [missing, unquoted, separate]


def test_frontend_vitest_and_docs_quality_jobs_exist():
    texts = " ".join(_scripts(_stage("Test")))
    assert "admin/provider" in texts and "widget" in texts and "npm test" in texts
    assert "docs-site" in texts and "npm run build" in texts and "lint:md" in texts


def test_coverage_gate_enforces_seventy_five_percent():
    coverage_job = next(job for job in _stage("Test")["jobs"] if job["job"] == "CoverageGate")
    assert coverage_job["dependsOn"] == "PytestShards"
    texts = " ".join(_scripts(coverage_job))
    assert "pct < 75" in texts or "below 75%" in texts


def test_production_only_exclusions_document_exists_and_is_cited():
    assert PRODUCTION_ONLY_EXCLUSIONS.is_file()
    pipeline = PIPELINE.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    exclusions = PRODUCTION_ONLY_EXCLUSIONS.read_text(encoding="utf-8")
    assert "ADO-NONPROD-TEST-EXCLUSIONS.md" in pipeline
    assert "ADO-NONPROD-TEST-EXCLUSIONS.md" in runbook
    for name in ("tests/e2e_live", "tests/live_api", "test_upgrade_regression", "SonarCloud"):
        assert name in exclusions


def test_no_secret_literal_in_the_pipeline():
    text = PIPELINE.read_text(encoding="utf-8")
    for label, pattern in SECRET_PATTERNS.items():
        match = pattern.search(text)
        assert match is None, f"{label} in azure-pipelines.yml: {match.group(0)[:12]}..."
    for env in _env_maps(_pipeline()):
        for name, value in env.items():
            if CREDENTIAL_ENV_NAME.search(name):
                assert MACRO.match(str(value)), f"{name} must come from a variable-group macro, not a literal"
    for entry in _pipeline()["variables"]:
        if "name" in entry:
            assert not CREDENTIAL_ENV_NAME.search(entry["name"]), f"{entry['name']} belongs in the variable group"


def test_mapped_variables_are_read_by_the_scripts_and_documented_in_the_runbook():
    deploy_source = (REPO_ROOT / "scripts" / "deploy.py").read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")
    for name in ("agent-red-azure", "agent-red-staging", "agent-red-production", "azure-pipelines.yml"):
        assert name in runbook
    for env in _env_maps(_pipeline()):
        for name, value in env.items():
            if name == "PYTHONIOENCODING":
                continue
            assert f'"{name}"' in deploy_source, f"scripts/deploy.py does not read {name}"
            group_variable = MACRO.match(str(value))
            assert group_variable, f"{name} is not mapped from a variable"
            assert group_variable.group(1) in runbook, f"the runbook does not document {group_variable.group(1)}"


def _containerapp_commands(markdown: str) -> tuple[int, list[str]]:
    """Count `az containerapp` commands in code blocks; return those that would print the whole app.

    Start, stop, update and secret set print the container app, plain env values
    included, unless the output is suppressed or narrowed with --query.
    """
    count, noisy, inside = 0, [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if not inside:
            continue
        for match in CONTAINERAPP_COMMAND.finditer(line):
            count += 1
            quiet = "Select-String" in line if match.group(1) == "logs show" else QUIET_OUTPUT.search(line)
            if not quiet:
                noisy.append(line.strip())
    return count, noisy


def test_runbook_containerapp_commands_never_print_the_whole_app():
    count, noisy = _containerapp_commands(RUNBOOK.read_text(encoding="utf-8"))
    assert count >= 10
    assert noisy == [], f"needs --output none or --query (logs: Select-String): {noisy}"


def test_the_quiet_output_check_catches_bare_commands():
    for command in (
        "az containerapp stop --name agent-red-staging --resource-group Agent-Red",
        "az containerapp update --name agent-red-staging --remove-env-vars SINGLE_CONTAINER_MODE",
        "az containerapp logs show --name agent-red-staging --tail 300",
    ):
        assert _containerapp_commands(f"```powershell\n{command}\n```\n")[1] == [command]
    assert _containerapp_commands("Prose: `az containerapp start` is not a command.\n") == (0, [])


def test_runbook_assigns_the_release_branch_and_keeps_the_tag():
    """Decision 10. The release branch and the merges are an assigned agent. The tag stays Owner-only."""
    text = RUNBOOK.read_text(encoding="utf-8")
    intro = text.split("## Contents", 1)[0]
    assert "assigned agent" in intro
    section = text.split("### 5.1 Set the version", 1)[1].split("### 5.2", 1)[0]
    agent_part, owner_part = section.split("**Owner-only.** Tag that merge commit", 1)
    assert "assigned agent" in agent_part
    assert "Owner-only" not in agent_part
    assert "git tag vX.Y.Z" in owner_part
    pipeline = text.split("### 5.3 First run", 1)[1].split("### 5.4", 1)[0]
    assert pipeline.lstrip().startswith("**Owner-only.**")


def test_the_application_registry_declares_the_pipeline():
    registry = json.loads((REPO_ROOT / ".gtkb-app-isolation.json").read_text(encoding="utf-8"))
    entries = [entry for entry in registry["top_level_artifacts"] if entry["name"] == "azure-pipelines.yml"]
    assert len(entries) == 1
    assert entries[0]["type"] == "FILE" and entries[0]["classification"] == "authoritative_input"


def test_the_release_gate_tools_are_pinned():
    """The gate installs name one exact version. A later edit can still unpin them; this is a string check."""
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert (
        'python -m pip install ruff==0.16.10 "bandit[toml]==1.9.4" '
        "pip-audit==2.10.1 radon==6.0.1 vulture==2.16"
    ) in pipeline
    assert "python -m pip install semgrep==1.182.0" in pipeline
    assert "python -m pip install ruff==0.16.10 bandit[toml] pip-audit radon vulture" not in pipeline
    assert "python -m pip install semgrep\n" not in pipeline
    scan = SECURITY_SCAN_WORKFLOW.read_text(encoding="utf-8")
    assert "pip install semgrep==1.182.0\n" in scan
    assert 'pip install "bandit[toml]==1.9.4"\n' in scan
    assert "python -m pip install pip-audit==2.10.1\n" in scan
    assert "pip install semgrep\n" not in scan
    assert "pip install bandit[toml]\n" not in scan
    assert "python -m pip install pip-audit\n" not in scan

