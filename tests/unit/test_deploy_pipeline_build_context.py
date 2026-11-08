"""Phase 6 build context of scripts/deploy_pipeline.py.

The Dockerfile COPYs docs-site/docs into the image, so the ACR build context
that phase 6 assembles must carry it. Phase 6 used to copy only src, config
and the admin and widget dist directories, so an ACR build from it could not
satisfy that COPY (review F24, design PR11).

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import argparse
import importlib.util
import sys
import tempfile
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
        spec = importlib.util.spec_from_file_location("deploy_pipeline_build_context_test", DEPLOY_PIPELINE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_source_tree(root: Path, *, with_docs: bool) -> None:
    """A minimal repository tree holding everything phase 6 copies."""
    _write(root / "Dockerfile", (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8"))
    _write(root / "requirements.txt", "fastapi\n")
    for rel in (
        "src/main.py",
        "src/multi_tenant/cosmos_schema.py",
        "src/multi_tenant/auth.py",
        "src/multi_tenant/middleware.py",
        "src/chat/pipeline/__init__.py",
    ):
        _write(root / rel, "")
    _write(root / "config" / "settings.toml", "")
    for name in ("standalone", "shopify", "provider"):
        _write(root / "admin" / name / "dist" / "index.html", "<html></html>")
    _write(root / "widget" / "dist" / "agent-red-widget.iife.js", "")
    if with_docs:
        _write(root / "docs-site" / "docs" / "intro.md", "# Intro\n")
        _write(root / "docs-site" / "docs" / "getting-started" / "setup.md", "# Setup\n")


def _run_phase_6(mod, monkeypatch, tmp_path: Path, *, with_docs: bool):
    """Run phase 6 against a tmp tree; the context is created under tmp_path/out."""
    src_root = tmp_path / "repo"
    _make_source_tree(src_root, with_docs=with_docs)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setattr(mod, "PROJECT_ROOT", src_root)
    monkeypatch.setattr(
        mod,
        "ADMIN_SPAS",
        [(name, src_root / "admin" / name) for name in ("standalone", "shopify", "provider")],
    )
    monkeypatch.setattr(mod, "WIDGET_DIR", src_root / "widget")
    monkeypatch.setattr(tempfile, "tempdir", str(out_dir))
    result, ctx = mod.phase_6_create_build_context(argparse.Namespace(dry_run=False))
    return result, ctx, out_dir


def _dockerfile_copy_sources(dockerfile: Path) -> list[str]:
    """Source paths of each COPY instruction that reads the build context."""
    sources = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0].upper() != "COPY" or any(p.startswith("--from") for p in parts):
            continue
        sources.extend(p.rstrip("/") for p in parts[1:-1] if not p.startswith("--"))
    return sources


def test_build_context_includes_docs_site_docs(monkeypatch, tmp_path):
    mod = _load_deploy_pipeline()

    result, ctx, _ = _run_phase_6(mod, monkeypatch, tmp_path, with_docs=True)

    assert result.status == "PASS", result.detail
    ctx_path = Path(ctx)
    docs_dst = ctx_path / "docs-site" / "docs"
    assert (docs_dst / "intro.md").is_file()
    assert (docs_dst / "intro.md").read_text(encoding="utf-8") == "# Intro\n"
    assert (docs_dst / "getting-started" / "setup.md").is_file()
    # Every COPY source in the real Dockerfile is present, so an ACR build of this context can satisfy it.
    sources = _dockerfile_copy_sources(ctx_path / "Dockerfile")
    assert "docs-site/docs" in sources
    missing = [s for s in sources if not (ctx_path / s).exists()]
    assert missing == []
    assert "docs-site/docs" in mod.BUILD_CONTEXT_CRITICAL_FILES


def test_build_context_fails_when_docs_missing(monkeypatch, tmp_path):
    mod = _load_deploy_pipeline()

    result, ctx, out_dir = _run_phase_6(mod, monkeypatch, tmp_path, with_docs=False)

    assert result.status == "FAIL"
    assert "docs-site/docs" in result.detail
    assert ctx == ""
    # The partial context is removed, as for a missing widget/dist.
    assert list(out_dir.iterdir()) == []
