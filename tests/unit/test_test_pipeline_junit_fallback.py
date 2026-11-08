# tests/unit/test_test_pipeline_junit_fallback.py: test_pipeline's JUnit XML fallback
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
When a pytest phase is killed before printing its summary, scripts/test_pipeline.py
reads the counts from the JUnit XML file pytest writes as it goes. It now parses
that file with defusedxml, which refuses DTD entity declarations that the standard
library's ElementTree would expand (the mechanism behind entity-expansion attacks).

No pytest subprocess runs here: stream_subprocess is replaced by a fake that writes
the report and returns output with no summary line, as a timed-out run would.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import test_pipeline as tp

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def run_with_report(tmp_path, monkeypatch):
    """Run tp._run_pytest with a fake subprocess that leaves ``xml`` as the report."""
    monkeypatch.setattr(tp, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tp, "_log_lines", [])
    monkeypatch.setattr(tp, "log", lambda level, msg: tp._log_lines.append(f"[{level}] {msg}"))

    def run(xml: str):
        def fake_stream(cmd, **kwargs):
            report = next(arg for arg in cmd if arg.startswith("--junitxml=")).split("=", 1)[1]
            Path(report).write_text(xml, encoding="utf-8")
            return SimpleNamespace(stdout="killed before the summary line\n")

        monkeypatch.setattr(tp, "stream_subprocess", fake_stream)
        passed, failed, errors, xfailed, _, _ = tp._run_pytest("tests/unit/", prefix="  [unit] ")
        return passed, failed, errors, xfailed

    return run


def test_a_normal_report_still_gives_the_counts(run_with_report):
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="5" errors="1" failures="1" skipped="1"/></testsuites>'
    )
    assert run_with_report(xml) == (2, 1, 1, 0)
    assert any("JUnit XML fallback - 2P/1F/1E" in line for line in tp._log_lines)


def test_entity_declarations_are_refused(run_with_report):
    """ElementTree would expand &n; and report 5 passes; defusedxml refuses the file."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE testsuites [<!ENTITY n "5">]>'
        '<testsuites><testsuite name="pytest" tests="&n;" errors="0" failures="0" skipped="0"/></testsuites>'
    )
    assert run_with_report(xml) == (0, 0, 0, 0)
    assert any(line.startswith("[WARN]") and "JUnit XML fallback failed" in line for line in tp._log_lines)


def test_defusedxml_is_a_declared_test_dependency():
    """The fallback swallows an ImportError, so a missing defusedxml would disable it silently."""
    lines = (REPO_ROOT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.split("#")[0].strip().startswith("defusedxml") for line in lines)
