# tests/unit/test_scripts_run_without_shell.py: the scripts' _run helpers start no shell
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
scripts/build.py, scripts/deploy.py and scripts/deploy_ui.py each run commands
through a ``_run`` helper that used to pass the whole string to a shell
(``shell=True``), so a shell metacharacter in an interpolated value (a tag, an
image, an app name) could start another command. The helpers now split the
string with POSIX quoting rules and run the program directly.

Nothing here talks to Azure, GitHub or npm: the programs are Python itself and
a small fake command that prints the arguments it received.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    """Import scripts/<name>.py as a fresh module (scripts/ is not a package)."""
    module_name = f"{name}_run_without_shell_under_test"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(module_name, None)
    return mod


def _runner(name: str):
    """A uniform (exit_code, stdout) call over each script's _run."""
    mod = _load(name)
    if name == "deploy_ui":

        def run(cmd: str) -> tuple[int, str]:
            done = mod._run(cmd, check=False, capture=True)
            return done.returncode, done.stdout.strip()

        return run
    return mod._run


SCRIPT_NAMES = ["build", "deploy", "deploy_ui"]


@pytest.fixture
def echo_script(tmp_path: Path) -> Path:
    script = tmp_path / "echo_args.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    return script


@pytest.fixture
def fake_cli(tmp_path: Path, echo_script: Path, monkeypatch) -> str:
    """A command named ``fakecli`` on PATH that prints its arguments as JSON.

    On Windows it is a .cmd shim, like npm, az and gh there.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if os.name == "nt":
        (bindir / "fakecli.cmd").write_text(f'@"{sys.executable}" "{echo_script}" %*\r\n', encoding="utf-8")
    else:
        shim = bindir / "fakecli"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{echo_script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return "fakecli"


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_no_shell_is_started(name):
    """subprocess.run gets an argument list, never shell=True."""
    mod = _load(name)
    done = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with (
        patch.object(mod.subprocess, "run", return_value=done) as run,
        patch.object(mod.shutil, "which", return_value="/resolved/az"),
    ):
        mod._run("az containerapp update --name app --image repo:v1")
    args, kwargs = run.call_args
    assert args[0] == ["/resolved/az", "containerapp", "update", "--name", "app", "--image", "repo:v1"]
    assert not kwargs.get("shell")


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_an_interpolated_value_cannot_start_another_command(name, echo_script):
    """A value carrying ``;`` or ``&`` reaches the program as plain text."""
    run = _runner(name)
    value = "v1;echo injected&echo injected2"
    code, out = run(f"{shlex.quote(sys.executable)} {shlex.quote(str(echo_script))} --image repo:{value}")
    assert code == 0
    assert out.splitlines() == [json.dumps(["--image", "repo:v1;echo", "injected&echo", "injected2"])]


@pytest.mark.parametrize("name", SCRIPT_NAMES)
def test_quoted_arguments_reach_the_program_unchanged(name, fake_cli):
    """The quoting the commands already use (az JMESPath queries, paths) still works."""
    run = _runner(name)
    windows_path = r"E:\dir with space\admin\provider"
    cmd = (
        f"{fake_cli} --query \"[?@=='v1.2.3']\""
        f' --query "sort_by(@, &name) | [-1].{{image:image}}"'
        f" --prefix {shlex.quote(windows_path)} -o tsv"
    )
    code, out = run(cmd)
    assert code == 0
    assert json.loads(out) == [
        "--query",
        "[?@=='v1.2.3']",
        "--query",
        "sort_by(@, &name) | [-1].{image:image}",
        "--prefix",
        windows_path,
        "-o",
        "tsv",
    ]


@pytest.mark.parametrize("name", ["build", "deploy"])
def test_a_missing_program_is_a_failed_result_not_an_exception(name):
    """build.py and deploy.py report failures as (1, message), as before."""
    code, _ = _runner(name)("no-such-program-for-agent-red --version")
    assert code == 1


def test_build_frontends_passes_project_paths_intact(tmp_path, monkeypatch):
    """npm's --prefix keeps a path with spaces and backslashes as one argument."""
    build = _load("build")
    root = tmp_path / "dir with space"
    project_dir = root / build.FRONTEND_PROJECTS[0]
    project_dir.mkdir(parents=True)
    (project_dir / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(build, "PROJECT_ROOT", root)
    commands: list[list[str]] = []

    def fake_run(cmd: str, timeout: int = 60) -> tuple[int, str]:
        commands.append(shlex.split(cmd))
        return 0, ""

    monkeypatch.setattr(build, "_run", fake_run)
    monkeypatch.setattr(build, "log", lambda msg: None)
    build.build_frontends()  # stops at the missing dist/ of the first project
    assert commands[:2] == [
        ["npm", "install", "--prefix", str(project_dir)],
        ["npm", "run", "build", "--prefix", str(project_dir)],
    ]
