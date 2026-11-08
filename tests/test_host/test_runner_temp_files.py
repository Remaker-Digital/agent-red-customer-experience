# tests/test_host/test_runner_temp_files.py: where TestRunner keeps its temporary files
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
TestRunner's pytest report and Locust CSV files live in a new private
directory per run, which is removed afterwards. Previously the report path came
from ``tempfile.mktemp`` (a name another process could claim before pytest
wrote it) and Locust wrote to a fixed ``/tmp/locust_results`` prefix, so a run
could read an earlier run's statistics as its own.

No subprocess runs here: ``asyncio.create_subprocess_exec`` is replaced by a
fake that writes what pytest or Locust would have written.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from test_host.cosmos_writer import CosmosWriter, RunState
from test_host.runner import TestRunner
from test_host.suites import SUITE_CONFIGS


@pytest.fixture
def mock_writer():
    writer = MagicMock(spec=CosmosWriter)
    writer.state = RunState(run_id="run-temp", environment="staging", suite="unit")
    return writer


@pytest.fixture
def runner(mock_writer):
    return TestRunner(
        run_id="run-temp",
        suite="unit",
        environment="staging",
        target_url="https://staging.example.com",
        cosmos_writer=mock_writer,
    )


class _FakeProcess:
    """Stands in for asyncio.subprocess.Process; ``on_run`` plays the child."""

    def __init__(self, on_run, returncode: int = 0):
        self._on_run = on_run
        self._returncode = returncode
        self.returncode = None
        self.pid = 4242

    async def communicate(self):
        self._on_run()
        self.returncode = self._returncode
        return b"child output", None


def _results(writer) -> list:
    return [result for call in writer.add_results.call_args_list for result in call.args[0]]


def _arg_after(cmd: tuple, flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _private_dir_checks(directory: Path) -> dict:
    """What the child would see of its output directory while it runs."""
    return {"is_dir": directory.is_dir(), "mode": stat.S_IMODE(directory.stat().st_mode)}


class TestPytestReport:
    @pytest.mark.asyncio
    async def test_report_is_written_in_a_private_directory_removed_after_the_run(self, runner, mock_writer):
        seen: dict = {}

        def child():
            seen.update(_private_dir_checks(seen["report"].parent))
            report = {"tests": [{"nodeid": "tests/unit/test_a.py::test_ok", "outcome": "passed", "duration": 0.01}]}
            seen["report"].write_text(json.dumps(report), encoding="utf-8")

        async def fake_exec(*cmd, **kwargs):
            flag = next(arg for arg in cmd if arg.startswith("--json-report-file="))
            seen["report"] = Path(flag.split("=", 1)[1])
            return _FakeProcess(child)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec):
            await runner._run_pytest(SUITE_CONFIGS["unit"])

        report_dir = seen["report"].parent
        assert seen["is_dir"], "the report directory exists before pytest starts"
        assert report_dir != Path(tempfile.gettempdir()), "the report is not a bare name in the shared temp directory"
        if os.name == "posix":
            assert seen["mode"] == 0o700
        assert not report_dir.exists(), "the directory is removed after the run"
        assert [r.status for r in _results(mock_writer)] == ["pass"]

    @pytest.mark.asyncio
    async def test_each_run_gets_its_own_directory(self, runner):
        reports: list[Path] = []

        async def fake_exec(*cmd, **kwargs):
            flag = next(arg for arg in cmd if arg.startswith("--json-report-file="))
            reports.append(Path(flag.split("=", 1)[1]))
            return _FakeProcess(lambda: None)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec):
            await runner._run_pytest(SUITE_CONFIGS["unit"])
            await runner._run_pytest(SUITE_CONFIGS["unit"])

        assert reports[0].parent != reports[1].parent

    @pytest.mark.asyncio
    async def test_missing_report_falls_back_to_one_suite_result(self, runner, mock_writer):
        """No report written: the exit code decides, as before the change."""
        seen: dict = {}

        async def fake_exec(*cmd, **kwargs):
            flag = next(arg for arg in cmd if arg.startswith("--json-report-file="))
            seen["report"] = Path(flag.split("=", 1)[1])
            return _FakeProcess(lambda: None, returncode=1)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec):
            await runner._run_pytest(SUITE_CONFIGS["unit"])

        results = _results(mock_writer)
        assert [(r.name, r.status) for r in results] == [("unit_suite", "fail")]
        assert not seen["report"].parent.exists()


class TestLocustResults:
    STATS = "Name,Request Count,Failure Count,Average Response Time\n/health,120,0,41.5\n"

    @pytest.mark.asyncio
    async def test_csv_files_are_written_in_a_private_directory_removed_after_the_run(self, runner, mock_writer):
        seen: dict = {}

        def child():
            seen.update(_private_dir_checks(seen["prefix"].parent))
            Path(f"{seen['prefix']}_stats.csv").write_text(self.STATS, encoding="utf-8")

        async def fake_exec(*cmd, **kwargs):
            seen["prefix"] = Path(_arg_after(cmd, "--csv"))
            return _FakeProcess(child)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec):
            await runner._run_locust(SUITE_CONFIGS["load"])

        results_dir = seen["prefix"].parent
        assert seen["is_dir"]
        assert results_dir != Path(tempfile.gettempdir())
        if os.name == "posix":
            assert seen["mode"] == 0o700
        assert not results_dir.exists()
        assert [(r.name, r.status) for r in _results(mock_writer)] == [("load__health", "pass")]

    @pytest.mark.asyncio
    async def test_a_run_never_reads_an_earlier_runs_statistics(self, runner, mock_writer):
        """The second run writes nothing, so it reports nothing."""
        writes = iter([True, False])

        async def fake_exec(*cmd, **kwargs):
            prefix = _arg_after(cmd, "--csv")
            write = next(writes)

            def child():
                if write:
                    Path(f"{prefix}_stats.csv").write_text(self.STATS, encoding="utf-8")

            return _FakeProcess(child)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec):
            await runner._run_locust(SUITE_CONFIGS["load"])
            await runner._run_locust(SUITE_CONFIGS["load"])

        assert mock_writer.add_results.call_count == 1

    @pytest.mark.asyncio
    async def test_directory_is_removed_when_locust_times_out(self, runner):
        seen: dict = {}

        def child():
            raise TimeoutError

        async def fake_exec(*cmd, **kwargs):
            seen["prefix"] = Path(_arg_after(cmd, "--csv"))
            return _FakeProcess(child)

        with patch("test_host.runner.asyncio.create_subprocess_exec", fake_exec), pytest.raises(TimeoutError):
            await runner._run_locust(SUITE_CONFIGS["load"])

        assert not seen["prefix"].parent.exists()
