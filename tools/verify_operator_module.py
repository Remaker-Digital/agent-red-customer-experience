# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Build wheels, acquire dependencies, then install and test without networking."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "python:3.12-slim-bookworm"
DOCKERFILE = """FROM python:3.12-slim-bookworm
COPY wheelhouse/ /wheelhouse/
RUN python -m venv /opt/verify && /opt/verify/bin/pip install --no-index --find-links=/wheelhouse "agent-red-operator-console-mcp[test]==0.1.0"
ENV PATH="/opt/verify/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 AGENT_RED_VERIFY_INDEPENDENT=1 AGENT_RED_TEST_BLOCK_NETWORK=1 PYTHONPATH=/verification
COPY tests/ /verification/
WORKDIR /tmp
CMD ["python", "-m", "pytest", "-q", "/verification/test_installed_module.py", "--disable-socket", "--allow-unix-socket", "-o", "asyncio_mode=auto", "-p", "no:cacheprovider"]
"""


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/operator-module-verification")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Use a new output directory to preserve previous verification evidence")
    output.mkdir(parents=True)
    context = output / "container"
    wheels = context / "wheelhouse"
    wheels.mkdir(parents=True)
    source_commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    epoch = subprocess.check_output(["git", "-C", str(ROOT), "show", "-s", "--format=%ct", "HEAD"], text=True).strip()
    build_env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    run(sys.executable, ROOT / "contracts/generate_client.py", "--check")
    for package in ("contracts/client", "contracts/fakes", "modules/operator-console-mcp"):
        run(sys.executable, "-m", "build", "--wheel", "--outdir", wheels, ROOT / package, env=build_env)
    # Only dependency acquisition has networking. Neither the checkout nor core
    # source is present in the subsequent disconnected verification container.
    run(
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={wheels},target=/wheelhouse",
        IMAGE,
        "python",
        "-m",
        "pip",
        "download",
        "--dest",
        "/wheelhouse",
        "--find-links",
        "/wheelhouse",
        "agent-red-operator-console-mcp[test]==0.1.0",
    )
    shutil.copytree(
        ROOT / "modules/operator-console-mcp/tests", context / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    (context / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    with tempfile.NamedTemporaryFile(prefix="operator-image-", suffix=".txt", delete=False) as temp:
        identity_file = Path(temp.name)
    try:
        run("docker", "build", "--network=none", "--iidfile", identity_file, context)
        image = identity_file.read_text().strip()
    finally:
        identity_file.unlink(missing_ok=True)
    with (output / "test-output.txt").open("w", encoding="utf-8") as stream:
        run("docker", "run", "--rm", "--network=none", image, stdout=stream, stderr=subprocess.STDOUT)
    print((output / "test-output.txt").read_text(encoding="utf-8"))
    record = {
        "public_source_commit": source_commit,
        "contract_sha256": hashlib.sha256((ROOT / "contracts/openapi.yaml").read_bytes()).hexdigest(),
        "network_mode": "none",
        "socket_guard": "parent and stdio subprocess",
        "repository_mounted_in_runtime": False,
        "verification_image": image,
        "wheels": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(wheels.glob("agent_red*.whl"))},
    }
    (output / "verification.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
