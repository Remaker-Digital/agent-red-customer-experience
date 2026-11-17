# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Enforce the additive module boundary on development branches."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import subprocess


def forbidden_paths(paths: list[str]) -> list[str]:
    return sorted({path for path in paths if not path.startswith("modules/")
                   or ".." in PurePosixPath(path).parts})


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    resolved = []
    for ref in (base, head):
        resolved.append(subprocess.check_output([
            "git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options", ref + "^{commit}",
        ], text=True).strip())
    data = subprocess.check_output([
        "git", "-C", str(repo), "diff", "--name-only", "--no-renames", "-z", *resolved,
    ])
    return [path.decode("utf-8") for path in data.split(b"\0") if path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    blocked = forbidden_paths(changed_paths(args.repo, args.base, args.head))
    if blocked:
        print("Development branch changes must stay inside modules/:")
        for path in blocked:
            print("  " + path)
        return 1
    print("PASS: all development changes are inside modules/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
