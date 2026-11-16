"""Offline checks for progressive load report validity."""

import csv
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import load_test_progressive as runner


AGGREGATE = {
    "Name": "Aggregated",
    "Request Count": "25",
    "Failure Count": "1",
    "Average Response Time": "10",
    "50%": "5",
    "75%": "8",
    "90%": "12",
    "95%": "20",
    "99%": "30",
    "Max Response Time": "30",
    "Requests/s": "2.5",
}


@pytest.mark.parametrize(
    "scenario",
    ["timeout", "failed-exit", "missing", "stale", "zero", "malformed", "valid"],
)
def test_round_reports_only_attributable_measurements(monkeypatch, tmp_path, scenario):
    cfg = runner.RoundConfig("probe", 10, 5, "1m", ["HealthProbeUser"])
    stats = tmp_path / "probe_stats.csv"

    def write_stats(row):
        with stats.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=AGGREGATE)
            writer.writeheader()
            writer.writerow(row)

    if scenario == "stale":
        write_stats(AGGREGATE)

    def run(cmd, **kwargs):
        assert cmd[:3] == [runner.sys.executable, "-m", "locust"]
        if scenario == "timeout":
            raise subprocess.TimeoutExpired(cmd, 600)
        if scenario not in {"missing", "stale"}:
            row = AGGREGATE.copy()
            if scenario == "zero":
                row["Request Count"] = row["Failure Count"] = "0"
            if scenario == "malformed":
                row["95%"] = "NaN"
            write_stats(row)
        return subprocess.CompletedProcess(cmd, 1 if scenario == "failed-exit" else 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", run)
    result = runner._run_round(cfg, "https://test.invalid", tmp_path, tmp_path)
    summary = runner._print_summary_table([result])
    report = json.loads(json.dumps(asdict(result), allow_nan=False))
    if scenario == "valid":
        assert result.valid is True
        assert report["total_requests"] == 25
        assert report["p95"] == 20
        assert report["error_rate_pct"] == 4
    else:
        assert result.valid is False
        assert report["invalid_reason"]
        assert report["p95"] is None
        assert "INVALID" in summary
        assert "Peak throughput" not in summary


def test_latency_inflection_compares_matching_workloads():
    def measured(name, latency, classes, duration):
        return runner.RoundResult(
            name=name, users=25, total_requests=25, total_failures=0, error_rate_pct=0,
            avg_response_time=latency, p50=latency, p95=latency, p99=latency,
            max_response_time=latency, rps=1, invalid_reason="", user_classes=classes, duration=duration,
        )

    health = measured("health", 10, ("HealthProbeUser",), "1m")
    mixed = measured("mixed", 100, (), "2m")
    short_mixed = measured("short-mixed", 500, (), "1m")
    slow_mixed = measured("slow-mixed", 250, (), "2m")

    summary = runner._print_summary_table([health, mixed, short_mixed, slow_mixed])

    assert summary.count("Latency inflection:") == 1
    assert "Latency inflection: 25 users (slow-mixed)" in summary


def test_runner_saves_invalid_round_before_returning_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(runner, "FULL_PLAN", [runner.RoundConfig("probe", 10, 5, "1m")])
    monkeypatch.setattr(runner.sys, "argv", ["load_test_progressive.py", "--env", "local"])
    monkeypatch.setattr(
        runner, "_run_round", lambda *_: runner.RoundResult("probe", 10, invalid_reason="Locust timed out"),
    )
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    report_path, = tmp_path.glob("*/results.json")
    result, = json.loads(report_path.read_text(encoding="utf-8"))
    assert result["valid"] is False
    assert result["invalid_reason"] == "Locust timed out"
    assert result["p95"] is None


@pytest.mark.parametrize("timeout", [False, True], ids=["startup-failure", "timeout"])
def test_invalid_round_preserves_locust_stderr(monkeypatch, tmp_path, timeout):
    fake_key = "synthetic-test-value"
    monkeypatch.setattr(runner, "STAGING_API_KEY", fake_key)
    message = f"Locust startup configuration error ({fake_key})"

    def run(cmd, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(cmd, 600, stderr=message.encode())
        return subprocess.CompletedProcess(cmd, 1, "", message)

    monkeypatch.setattr(runner.subprocess, "run", run)
    cfg = runner.RoundConfig("probe", 10, 5, "1m")
    result = runner._run_round(cfg, "https://test.invalid", tmp_path, tmp_path)

    assert not result.valid
    diagnostic = Path(result.stderr_log).read_text(encoding="utf-8")
    assert diagnostic == "Locust startup configuration error ([redacted])"
    assert result.stderr_log in runner._print_summary_table([result])
