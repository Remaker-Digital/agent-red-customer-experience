# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Exercise the module guard with real Git changes, including renames."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("scope", Path(__file__).parents[1] / "check_module_scope.py")
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)


class ModuleScopeTests(unittest.TestCase):
    def test_module_paths_are_allowed(self):
        self.assertEqual(scope.forbidden_paths(["modules/example/src/main.py", "modules/example/pyproject.toml"]), [])

    def test_core_contract_and_workflow_changes_are_rejected(self):
        paths = ["src/main.py", "contracts/openapi.yaml", ".github/workflows/module-scope.yml", "README.md"]
        self.assertEqual(scope.forbidden_paths(paths), sorted(paths))

    def test_directory_traversal_is_rejected(self):
        self.assertEqual(scope.forbidden_paths(["modules/../src/main.py"]), ["modules/../src/main.py"])

    def test_git_rename_from_core_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="module-scope-") as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args])
            git("init", "--quiet")
            git("config", "user.name", "Synthetic Developer")
            git("config", "user.email", "developer@example.com")
            (root / "src").mkdir()
            (root / "src/core.py").write_text("value = 1\n")
            git("add", ".")
            git("commit", "-qm", "Initial fixture")
            base = git("rev-parse", "HEAD").decode().strip()
            (root / "modules/example").mkdir(parents=True)
            git("mv", "src/core.py", "modules/example/core.py")
            git("commit", "-qm", "Move fixture")
            paths = scope.changed_paths(root, base, "HEAD")
            self.assertEqual(scope.forbidden_paths(paths), ["src/core.py"])


if __name__ == "__main__":
    unittest.main()
