# tests/unit/test_static_path_containment.py: admin SPA routes serve only files inside their dist folder
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
The admin SPA catch-all routes (``/admin/shopify/{path}``, ``/admin/provider/{path}``
and the password-gated ``/admin/standalone/{path}``) served ``dist / full_path`` when
it was a file and ``".." not in full_path``. pathlib discards ``dist`` when
``full_path`` is absolute, so ``GET /admin/shopify//etc/passwd`` (or a drive path on
Windows) served any file the process could read, on an auth-exempt prefix.

Architecture review S2 (docs/agent-red-architecture-review.md). The route tests run
through the composed application (``app_client``).
"""

from __future__ import annotations

import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MARKER = "agent-red-path-containment-marker"


def dist_file(root, full_path):
    from src.app.static_serving import dist_file as helper  # imported here so the route tests run on any version

    return helper(root, full_path)


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (root / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("js", encoding="utf-8")
    (tmp_path / "outside.txt").write_text(MARKER, encoding="utf-8")
    return root


@pytest.mark.parametrize(("full_path", "expected"), [("logo.svg", "logo.svg"), ("assets/app.js", "assets/app.js")])
def test_files_inside_dist_are_served(dist, full_path, expected):
    assert dist_file(dist, full_path) == (dist / expected).resolve()


@pytest.mark.parametrize("full_path", ["missing.svg", "assets", "", "../outside.txt", "assets/../../outside.txt"])
def test_missing_files_directories_and_traversal_are_refused(dist, full_path):
    assert dist_file(dist, full_path) is None


def test_an_absolute_path_outside_dist_is_refused(dist):
    outside = (dist.parent / "outside.txt").resolve()
    assert outside.is_file()
    assert dist_file(dist, str(outside)) is None
    assert dist_file(dist, outside.as_posix()) is None


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_a_symlink_leading_out_of_dist_is_refused(dist):
    (dist / "link.txt").symlink_to(dist.parent / "outside.txt")
    assert dist_file(dist, "link.txt") is None


def _absolute_route_path(prefix: str, target: pathlib.Path) -> str:
    """The request path that asks the catch-all route for an absolute file path."""
    absolute = target.resolve().as_posix()  # "/tmp/..." on Linux, "E:/..." on Windows
    return f"{prefix}/{absolute}"


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param(
            "/admin/shopify",
            marks=pytest.mark.skipif(not (REPO_ROOT / "admin/shopify/dist").is_dir(), reason="no Shopify admin build"),
        ),
        pytest.param(
            "/admin/provider",
            marks=pytest.mark.skipif(not (REPO_ROOT / "admin/provider/dist").is_dir(), reason="no provider admin build"),
        ),
        pytest.param(
            "/admin/standalone",
            marks=pytest.mark.skipif(
                not (REPO_ROOT / "admin/standalone/dist").is_dir(), reason="no standalone admin build"
            ),
        ),
    ],
)
def test_admin_routes_do_not_serve_files_outside_their_build(app_client, tmp_path, prefix):
    secret = tmp_path / "secret.txt"
    secret.write_text(MARKER, encoding="utf-8")
    response = app_client.get(_absolute_route_path(prefix, secret))
    assert MARKER not in response.text


def test_admin_routes_still_serve_their_own_files(app_client):
    if not (REPO_ROOT / "admin/provider/dist/icon-master.svg").is_file():
        pytest.skip("no provider admin build")
    response = app_client.get("/admin/provider/icon-master.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]
