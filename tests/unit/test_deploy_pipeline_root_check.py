"""Project-root sanity check of scripts/deploy_pipeline.py.

The check used to require CLAUDE.md, which the repository no longer carries
(agent configuration comes from GT-KB), so a real deploy failed it.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PIPELINE = PROJECT_ROOT / "scripts" / "deploy_pipeline.py"


def _load_deploy_pipeline():
    """Load a private copy, as test_deploy_pipeline_scaling.py does."""
    for p in [str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts")]:
        if p not in sys.path:
            sys.path.insert(0, p)
    with patch.object(sys, "platform", "linux"):
        spec = importlib.util.spec_from_file_location("deploy_pipeline_root_check_test", DEPLOY_PIPELINE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def test_repository_root_passes_without_claude_md():
    mod = _load_deploy_pipeline()
    assert not (PROJECT_ROOT / "CLAUDE.md").exists()
    assert mod._project_root_failures(PROJECT_ROOT) == []


def test_markers_are_files_this_repository_carries():
    mod = _load_deploy_pipeline()
    assert "CLAUDE.md" not in mod.PROJECT_ROOT_MARKERS
    for name in mod.PROJECT_ROOT_MARKERS:
        assert (PROJECT_ROOT / name).is_file(), name


def test_each_missing_marker_is_reported(tmp_path):
    mod = _load_deploy_pipeline()
    assert len(mod._project_root_failures(tmp_path)) == len(mod.PROJECT_ROOT_MARKERS)
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    assert mod._project_root_failures(tmp_path) == ["Not in project root (application.toml not found)"]
    (tmp_path / "application.toml").write_text("", encoding="utf-8")
    assert mod._project_root_failures(tmp_path) == []
