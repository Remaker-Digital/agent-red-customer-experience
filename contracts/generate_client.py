# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Regenerate the independently packaged client with pinned local tooling."""

from __future__ import annotations

import argparse
from importlib.metadata import version
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
HEADER = "# Copyright (c) 2026 Michael Palmeter\n# SPDX-License-Identifier: MIT\n"


def generate(check: bool = False) -> None:
    for package, expected in (("openapi-python-client", "0.28.4"), ("ruff", "0.17.5")):
        if version(package) != expected:
            raise ValueError(f"Install {package}=={expected} for reproducible generation")
    with tempfile.TemporaryDirectory(prefix="core-client-") as directory:
        output = Path(directory) / "generated"
        env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        subprocess.run(
            [
                sys.executable,
                "-m",
                "openapi_python_client",
                "generate",
                "--path",
                str(HERE / "openapi.yaml"),
                "--config",
                str(HERE / "generator-config.yaml"),
                "--meta",
                "none",
                "--output-path",
                str(output),
                "--fail-on-warning",
            ],
            check=True,
            env=env,
        )
        for path in output.rglob("*.py"):
            text = path.read_text(encoding="utf-8").replace("\u2014", ":").replace("\u2013", "-")
            path.write_text(HEADER + text, encoding="utf-8")
        subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--isolated", "--select", "I,F", "--fix", str(output)], check=True
        )
        subprocess.run([sys.executable, "-m", "ruff", "format", "--isolated", str(output)], check=True)
        destination = HERE / "client/agent_red_core_client"
        generated = {path.relative_to(output).as_posix(): path.read_bytes().replace(b"\r\n", b"\n") for path in output.rglob("*.py")}
        existing = {path.relative_to(destination).as_posix(): path for path in destination.rglob("*.py")}
        stale = sorted(existing.keys() - generated.keys())
        if stale:
            raise ValueError("Review stale generated files before removing them: " + ", ".join(stale))
        changed = []
        for name, data in generated.items():
            path = destination / name
            if not path.exists() or path.read_bytes() != data:
                changed.append(name)
                if not check:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
        if check and changed:
            raise ValueError("Generated client is out of date: " + ", ".join(changed))
        print(f"PASS: {len(generated)} client files checked; {len(changed)} changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    generate(parser.parse_args().check)
