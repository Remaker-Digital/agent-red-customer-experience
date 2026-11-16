"""Verify the transport benchmark evidence with simulated responses."""

import json

import httpx
import pytest

from tests.transport import test_transport_performance as benchmark
from tests.transport import test_production_gate as gate
from tests.transport.conftest import BenchmarkResult, consume_sse_events


def test_empty_population_serializes_as_unmeasured():
    data = BenchmarkResult(name="empty", tier="test").to_dict()
    assert data["sample_count"] == 0
    assert data["p50_ms"] is None
    assert data["p95_ms"] is None
    assert data["p99_ms"] is None


def test_percentiles_distinguish_p95_from_the_maximum():
    result = BenchmarkResult(name="nearest-rank", tier="test")
    for latency in [*range(1, 21), True, -1, float("nan"), None]:
        result.record(latency)

    data = result.to_dict()
    assert data["sample_count"] == 20
    assert data["p50_ms"] == 10.5
    assert data["p95_ms"] == 19
    assert data["p99_ms"] == 20


def test_per_hop_distinguishes_absent_stages_from_missing_observations(monkeypatch, tmp_path):
    events = [
        {"stage": "intent-classifier", "status": "completed", "latency_ms": 12},
        {"stage": "knowledge-retrieval", "status": "started"},
        {"conversation_id": "synthetic", "turn_count": 1, "total_latency_ms": 20},
    ]
    monkeypatch.setattr(benchmark, "run_conversation", lambda *_: events)

    benchmark.TestPerHopStageLatency().test_per_hop_stage_latency("https://test.invalid", {}, tmp_path)

    report = json.loads((tmp_path / "per-hop-latency.json").read_text(encoding="utf-8"))
    assert report["attempt_count"] == 20
    assert report["completed_count"] == 20
    assert report["completed"] is True
    assert report["valid"] is False
    assert report["stages"]["intent-classifier"]["sample_count"] == 20
    assert report["stages"]["knowledge-retrieval"]["sample_count"] == 0
    assert report["stages"]["knowledge-retrieval"]["p95_ms"] is None
    assert report["stages"]["knowledge-retrieval"]["missing_count"] == 20
    assert report["stages"]["knowledge-retrieval"]["absent_count"] == 0
    assert report["stages"]["response-generator"]["absent_count"] == 20
    assert report["stages"]["response-generator"]["missing_count"] == 0


def test_total_latency_records_provisional_overage_as_measurement(monkeypatch, tmp_path):
    events = [{"conversation_id": "synthetic", "turn_count": 1, "total_latency_ms": 9000}]
    monkeypatch.setattr(benchmark, "run_conversation", lambda *_: events)

    benchmark.TestPerHopStageLatency().test_total_pipeline_latency("https://test.invalid", {}, tmp_path)

    report = json.loads((tmp_path / "pipeline-latency.json").read_text(encoding="utf-8"))
    assert report["attempt_count"] == 20
    assert report["benchmarks"][0]["p95_ms"] == 9000


def test_total_latency_keeps_artifact_when_transport_fails(monkeypatch, tmp_path):
    def fail(*_):
        raise httpx.ReadTimeout("simulated timeout")

    monkeypatch.setattr(benchmark, "run_conversation", fail)

    with pytest.raises(pytest.fail.Exception, match="Insufficient"):
        benchmark.TestPerHopStageLatency().test_total_pipeline_latency("https://test.invalid", {}, tmp_path)

    report = json.loads((tmp_path / "pipeline-latency.json").read_text(encoding="utf-8"))
    assert report["attempt_count"] == 20
    assert report["benchmarks"][0]["errors"] == 20
    assert report["benchmarks"][0]["p95_ms"] is None


def test_gateway_writes_the_failed_readiness_measurement(monkeypatch, tmp_path):
    class UnavailableGateway:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, _):
            return httpx.Response(503)

    monkeypatch.setattr(benchmark.httpx, "Client", UnavailableGateway)

    with pytest.raises(AssertionError, match="/ready failed on 20"):
        benchmark.TestPerHopStageLatency().test_gateway_baseline_latency("https://test.invalid", tmp_path)

    report = json.loads((tmp_path / "gateway-baseline.json").read_text(encoding="utf-8"))
    assert report["warmup_status"] == 503
    assert report["attempt_count"] == 20
    assert report["benchmarks"][0]["sample_count"] == 0
    assert report["benchmarks"][0]["errors"] == 20


def test_gate_does_not_accept_legacy_nonzero_stage_counts(monkeypatch, tmp_path):
    output = tmp_path / "scripts" / "benchmark-results"
    output.mkdir(parents=True)
    (output / "per-hop-latency.json").write_text(json.dumps({
        "stages": {stage: {"sample_count": 1} for stage in (
            "intent-classifier", "knowledge-retrieval", "response-generator", "critic-supervisor",
        )}
    }), encoding="utf-8")
    monkeypatch.setattr(gate, "PROJECT_ROOT", tmp_path)

    with pytest.raises(pytest.fail.Exception, match="diagnostic"):
        gate.TestGate3PerformanceBaselines().test_per_hop_latency_data_present()


def test_interrupted_run_replaces_previous_artifact_and_retains_progress(monkeypatch, tmp_path):
    artifact = tmp_path / "pipeline-latency.json"
    artifact.write_text('{"valid": true}', encoding="utf-8")
    attempts = iter([{"conversation_id": "synthetic", "turn_count": 1, "total_latency_ms": 20}])

    def interrupt(*_):
        events = next(attempts, None)
        if events is None:
            raise KeyboardInterrupt
        return [events]

    monkeypatch.setattr(benchmark, "run_conversation", interrupt)
    with pytest.raises(KeyboardInterrupt):
        benchmark.TestPerHopStageLatency().test_total_pipeline_latency("https://test.invalid", {}, tmp_path)

    report = json.loads(artifact.read_text(encoding="utf-8"))
    assert report["completed"] is False
    assert report["valid"] is False
    assert report["attempt_count"] == 2
    assert report["completed_count"] == 1
    assert report["benchmarks"][0]["sample_count"] == 1
    assert report["benchmarks"][0]["missing_count"] == 1


@pytest.mark.parametrize("damage", [None, "partial", "sample-count", "completed-count", "different-run"])
def test_gate_validates_the_selected_run_and_sample_accounting(monkeypatch, tmp_path, damage):
    output = tmp_path / "scripts" / "benchmark-results" / gate.BENCHMARK_RUN_ID
    output.mkdir(parents=True)
    attempts = iter(range(len(benchmark.SAMPLE_MESSAGES)))

    def stream(*_):
        stage_names = gate.PIPELINE_STAGE_NAMES
        if next(attempts) % 2 == 0:
            stage_names = stage_names[:2]
        return [
            *({"stage": stage, "status": "completed", "latency_ms": 12} for stage in stage_names),
            {"conversation_id": "synthetic", "turn_count": 1},
        ]

    monkeypatch.setattr(benchmark, "run_conversation", stream)
    benchmark.TestPerHopStageLatency().test_per_hop_stage_latency("https://test.invalid", {}, output)
    monkeypatch.setattr(gate, "PROJECT_ROOT", tmp_path)
    verify = gate.TestGate3PerformanceBaselines().test_per_hop_latency_data_present
    artifact = output / "per-hop-latency.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["stages"]["response-generator"]["sample_count"] == 10
    assert data["stages"]["response-generator"]["absent_count"] == 10

    if damage is None:
        verify()
        return

    if damage == "partial":
        data["completed"] = False
    elif damage == "sample-count":
        data["stages"]["knowledge-retrieval"]["sample_count"] = 9
    elif damage == "completed-count":
        data["completed_count"] = 19
    else:
        data["run_id"] = "other-run"
    artifact.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        verify()


@pytest.mark.parametrize("claim_valid", [False, True], ids=["writer", "reader"])
def test_entirely_unobserved_required_stage_is_diagnostic(monkeypatch, tmp_path, claim_valid):
    output = tmp_path / "scripts" / "benchmark-results" / gate.BENCHMARK_RUN_ID
    output.mkdir(parents=True)
    events = [
        *(
            {"stage": stage, "status": "completed", "latency_ms": 12}
            for stage in gate.PIPELINE_STAGE_NAMES[:-1]
        ),
        {"conversation_id": "synthetic", "turn_count": 1},
    ]
    monkeypatch.setattr(benchmark, "run_conversation", lambda *_: events)
    benchmark.TestPerHopStageLatency().test_per_hop_stage_latency("https://test.invalid", {}, output)
    artifact = output / "per-hop-latency.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["completed"] is True
    assert data["stages"]["critic-supervisor"]["absent_count"] == len(benchmark.SAMPLE_MESSAGES)
    if not claim_valid:
        assert data["valid"] is False
        return

    data["valid"] = True
    artifact.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(gate, "PROJECT_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="critic-supervisor has no usable latency measurements"):
        gate.TestGate3PerformanceBaselines().test_per_hop_latency_data_present()


def test_escalation_handler_may_be_absent_across_the_run(monkeypatch, tmp_path):
    output = tmp_path / gate.BENCHMARK_RUN_ID
    output.mkdir()
    events = [
        {"stage": "intent-classifier", "status": "completed", "latency_ms": 12},
        {"conversation_id": "synthetic", "turn_count": 1},
    ]
    monkeypatch.setattr(benchmark, "run_conversation", lambda *_: events)
    benchmark.TestPerHopStageLatency().test_escalation_path_latency("https://test.invalid", {}, output)
    artifact = output / "escalation-latency.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    attempts = len(benchmark.ESCALATION_MESSAGES)
    assert data["valid"] is True
    assert data["stages"]["escalation-handler"]["absent_count"] == attempts
    gate.TestGate3PerformanceBaselines._validate_latency_artifact(
        tmp_path, artifact.name, "escalation_path_latency", attempts, "stages", gate.ESCALATION_STAGE_NAMES,
    )


def test_truncated_stream_is_not_counted_as_a_completed_measurement(monkeypatch, tmp_path):
    events = [{"stage": "intent-classifier", "status": "completed", "latency_ms": 12}]
    monkeypatch.setattr(benchmark, "run_conversation", lambda *_: events)
    with pytest.raises(pytest.fail.Exception, match="no usable latency"):
        benchmark.TestPerHopStageLatency().test_per_hop_stage_latency("https://test.invalid", {}, tmp_path)
    report = json.loads((tmp_path / "per-hop-latency.json").read_text(encoding="utf-8"))
    assert report["valid"] is False
    assert report["stages"]["intent-classifier"]["errors"] == 20


def test_invalid_sse_payload_is_not_silently_discarded():
    with pytest.raises(json.JSONDecodeError):
        consume_sse_events("data: {invalid json}\n\ndata: {\"conversation_id\":\"c\",\"turn_count\":1}")


@pytest.mark.parametrize("gap", ["errors", "missing_count"])
def test_one_sample_with_gaps_stays_diagnostic(monkeypatch, tmp_path, gap):
    output = tmp_path / "scripts" / "benchmark-results" / gate.BENCHMARK_RUN_ID
    output.mkdir(parents=True)
    attempts = iter(range(20))
    done = {"conversation_id": "synthetic", "turn_count": 1}

    def stream(*_):
        if next(attempts) == 0:
            return [{**done, "total_latency_ms": 20}]
        if gap == "errors":
            raise httpx.ReadTimeout("simulated timeout")
        return [done]

    monkeypatch.setattr(benchmark, "run_conversation", stream)
    benchmark.TestPerHopStageLatency().test_total_pipeline_latency("https://test.invalid", {}, output)
    path = output / "pipeline-latency.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["completed"] is True
    assert report["completed_count"] == 20
    assert report["benchmarks"][0]["samples_ms"] == [20]
    assert report["benchmarks"][0][gap] == 19
    assert report["valid"] is False

    validate = gate.TestGate3PerformanceBaselines._validate_latency_artifact
    root = output.parent
    contract = (root, path.name, "total_pipeline_latency", 20, "benchmarks", ("total-pipeline",))
    with pytest.raises(pytest.fail.Exception, match="diagnostic"):
        validate(*contract)

    report["valid"] = True
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(AssertionError, match="missing or errored"):
        validate(*contract)


def test_readiness_distribution_target_is_reported_without_grading(monkeypatch, tmp_path):
    class ReadyGateway:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, _):
            return httpx.Response(200)

    clock = iter(range(40))
    monkeypatch.setattr(benchmark.httpx, "Client", ReadyGateway)
    monkeypatch.setattr(benchmark, "time", type("Clock", (), {"monotonic": staticmethod(clock.__next__)})())
    benchmark.TestPerHopStageLatency().test_gateway_baseline_latency("https://test.invalid", tmp_path)
    report = json.loads((tmp_path / "gateway-baseline.json").read_text(encoding="utf-8"))
    assert report["valid"] is True
    assert report["health_distribution_p95_reference_ms"] == 500
    assert report["benchmarks"][0]["p95_ms"] == 1000
