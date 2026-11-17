"""Phase 4 — Transport performance benchmark matrix.

Measures the actual deployed architecture using per-hop stage latency
from SSE events. All benchmarks use the real chat API contract
(POST /conversations + GET /stream/{id}), authenticated via X-Widget-Key.

Per-hop attribution uses latency_ms from completed stage events (server-side
wall-clock from PipelineTimeoutBudget), not elapsed_ms subtraction.

Latency targets are provisional measurement thresholds, not hard gates.
Hard enforcement deferred to Phase 5 after baseline stabilization.

Analytics is explicitly excluded: fire-and-forget, non-customer-facing.

Recovery plan reference: the original transport analysis Phase 4.
Codex GO: the original transport analysis.
Governing decisions: ADR-001, DCL-002 v4.

© 2026 Michael Palmeter. All rights reserved.
"""

from __future__ import annotations

import json
import time
from functools import partial
from pathlib import Path

import httpx
import pytest

from tests.transport.conftest import (
    BenchmarkResult,
    ESCALATION_STAGE_NAMES,
    GATEWAY_ATTEMPTS,
    LatencyReport,
    PIPELINE_STAGE_NAMES,
    get_stage_latency,
    get_total_latency,
    has_error_event,
    is_done_event,
    requires_test_host,
    run_conversation,
)

# Sample messages — varied to avoid caching effects
SAMPLE_MESSAGES = [
    "What are your store hours?",
    "Do you offer free shipping?",
    "What is your return policy?",
    "Do you have any sales right now?",
    "How can I track my order?",
    "What payment methods do you accept?",
    "Do you ship internationally?",
    "Can I cancel my order?",
    "What products do you recommend?",
    "How do I contact support?",
    "Do you have a loyalty program?",
    "What are your best sellers?",
    "How long does delivery take?",
    "Do you offer gift cards?",
    "What is your warranty policy?",
    "Can I change my shipping address?",
    "Do you price match?",
    "What sizes are available?",
    "How do I apply a discount code?",
    "When will my item be back in stock?",
]

ESCALATION_MESSAGES = [
    "I need to speak with a human agent right now",
    "Let me talk to a real person please",
    "I want to escalate this issue",
    "Connect me to a manager",
    "This is urgent, I need human help",
]


# ---------------------------------------------------------------------------
# Category A: Per-Hop Stage Latency
# ---------------------------------------------------------------------------


def _measure_conversations(base_url, headers, messages, report, readers):
    def observe(message):
        try:
            events = run_conversation(base_url, headers, message)
        except (httpx.HTTPError, ValueError, KeyError):
            events = []
        if (
            any(not isinstance(event, dict) for event in events)
            or has_error_event(events)
            or not any(map(is_done_event, events))
        ):
            for result in report.populations.values():
                result.errors += 1
            return
        for name, result in report.populations.items():
            if report.population_key == "stages":
                stage_statuses = [event.get("status") for event in events if event.get("stage") == name]
                if all(status == "skipped" for status in stage_statuses):
                    result.absent += 1
                    continue
            result.record(readers[name](events))

    report.measure(messages, observe)
    report.require_measurements()


class TestPerHopStageLatency:
    """Measure latency on the transport test host."""

    @requires_test_host
    def test_per_hop_stage_latency(self, staging_base_url, widget_headers, benchmark_output):
        report = LatencyReport(
            benchmark_output / "per-hop-latency.json", "per_hop_stage_latency", len(SAMPLE_MESSAGES),
            {name: BenchmarkResult(name, "per-interface") for name in PIPELINE_STAGE_NAMES}, "stages",
        )
        readers = {name: partial(get_stage_latency, stage_name=name) for name in PIPELINE_STAGE_NAMES}
        _measure_conversations(staging_base_url, widget_headers, SAMPLE_MESSAGES, report, readers)

    @requires_test_host
    def test_total_pipeline_latency(self, staging_base_url, widget_headers, benchmark_output):
        result = BenchmarkResult("total-pipeline", "end-to-end")
        report = LatencyReport(
            benchmark_output / "pipeline-latency.json", "total_pipeline_latency",
            len(SAMPLE_MESSAGES), {result.name: result}, "benchmarks",
        )
        _measure_conversations(
            staging_base_url, widget_headers, SAMPLE_MESSAGES, report, {result.name: get_total_latency},
        )

    @requires_test_host
    def test_gateway_baseline_latency(self, staging_base_url, benchmark_output):
        """Measure /ready separately from agent dispatch."""
        result = BenchmarkResult("gateway-baseline", "http")
        report = LatencyReport(
            benchmark_output / "gateway-baseline.json", "gateway_baseline_latency",
            GATEWAY_ATTEMPTS, {result.name: result}, "benchmarks",
        )
        report.write()
        with httpx.Client(timeout=30.0) as client:
            try:
                report.warmup_status = client.get(f"{staging_base_url}/ready").status_code
            except httpx.HTTPError:
                pass

            def observe(_):
                start = time.monotonic()
                try:
                    response = client.get(f"{staging_base_url}/ready")
                except httpx.HTTPError:
                    result.errors += 1
                    return
                if response.status_code == 200:
                    result.record((time.monotonic() - start) * 1000)
                else:
                    result.errors += 1

            report.measure(range(GATEWAY_ATTEMPTS), observe)

        assert result.errors == 0, f"/ready failed on {result.errors} of {GATEWAY_ATTEMPTS} measured requests"
        report.require_measurements()

    @requires_test_host
    def test_escalation_path_latency(self, staging_base_url, widget_headers, benchmark_output):
        report = LatencyReport(
            benchmark_output / "escalation-latency.json", "escalation_path_latency", len(ESCALATION_MESSAGES),
            {name: BenchmarkResult(name, "per-interface") for name in ESCALATION_STAGE_NAMES}, "stages",
        )
        readers = {name: partial(get_stage_latency, stage_name=name) for name in ESCALATION_STAGE_NAMES}
        _measure_conversations(staging_base_url, widget_headers, ESCALATION_MESSAGES, report, readers)

    @requires_test_host
    def test_widget_pipeline_latency(self, staging_base_url, widget_headers, benchmark_output):
        result = BenchmarkResult("widget-pipeline", "widget")
        report = LatencyReport(
            benchmark_output / "widget-latency.json", "widget_pipeline_latency",
            len(SAMPLE_MESSAGES), {result.name: result}, "benchmarks",
        )
        _measure_conversations(
            staging_base_url, widget_headers, SAMPLE_MESSAGES, report, {result.name: get_total_latency},
        )


# ---------------------------------------------------------------------------
# Category B: Architecture Verification
# ---------------------------------------------------------------------------


class TestArchitectureVerification:
    """Verify transport tier and RG placement match architecture decisions."""

    @requires_test_host
    def test_transport_tier_identification(self, staging_base_url, benchmark_output):
        """Query /ready and record the active transport tier."""
        import httpx
        client = httpx.Client(timeout=30.0)
        try:
            resp = client.get(f"{staging_base_url}/ready")
            assert resp.status_code == 200
            sdk = resp.json().get("agntcy_sdk", {})
            tier = sdk.get("active_tier", "unknown")
            active = sdk.get("transport_active", False)
            slim = sdk.get("slim_endpoint")
            nats = sdk.get("nats_endpoint")
        finally:
            client.close()

        print(f"\n  Active tier: {tier}")
        print(f"  Transport active: {active}")
        print(f"  SLIM: {slim or 'None'}")
        print(f"  NATS: {nats or 'None'}")

        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "test": "transport_tier_identification",
            "active_tier": tier,
            "transport_active": active,
            "slim_endpoint": slim,
            "nats_endpoint": nats,
        }
        (benchmark_output / "transport-tier.json").write_text(json.dumps(report, indent=2))

    def test_rg_placement_verification(self):
        """DCL-002 v4 in KB records in-process RG as intended production path.

        This is architecture metadata verification, not latency inference.

        RC-1: skip when ``tools/knowledge-db`` is absent from Agent Red.
        """
        import sys
        tools_path = Path(__file__).resolve().parents[2] / "tools" / "knowledge-db"
        if not tools_path.is_dir():
            pytest.skip("RC-1: tools/knowledge-db not present in Agent Red")
        sys.path.insert(0, str(tools_path))
        try:
            import db as kb_db
        except ImportError as exc:
            pytest.skip(f"RC-1: KnowledgeDB unavailable ({exc})")
        kdb = kb_db.KnowledgeDB()

        spec = kdb.get_spec("DCL-002")
        assert spec is not None, "DCL-002 not found in KB"
        assert spec["version"] >= 4, (
            f"DCL-002 version {spec['version']} — expected >=4 (per-interface policy)"
        )

        desc = spec.get("description", "")
        assert "in-process" in desc.lower() or "gateway" in desc.lower(), (
            "DCL-002 v4 should document in-process RG streaming as intended path"
        )
        assert "response" in desc.lower() or "rg" in desc.lower(), (
            "DCL-002 v4 should reference response generator"
        )


# ---------------------------------------------------------------------------
# Category C: Failure Injection
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Verify transport and critic failure responses."""

    def test_503_on_transport_exhaustion(self):
        """_require_transport_or_fail() raises HTTPException 503."""
        from fastapi import HTTPException
        from src.chat.pipeline.agent_dispatch import AgentDispatchMixin

        mixin = AgentDispatchMixin.__new__(AgentDispatchMixin)
        with pytest.raises(HTTPException) as exc_info:
            mixin._require_transport_or_fail("test-agent")

        assert exc_info.value.status_code == 503

    def test_critic_unavailable_returns_safe_fallback(self):
        """Verify the safe fallback when the critic is unavailable."""
        import asyncio
        from unittest.mock import patch

        from src.chat.pipeline.critic_escalation import CriticEscalationMixin
        from src.multi_tenant.critic_policy import SAFE_FALLBACK_MESSAGE

        mixin = CriticEscalationMixin.__new__(CriticEscalationMixin)
        mixin._critic = None
        mixin._agent_urls = {}

        with patch("src.multi_tenant.agntcy_sdk_integration._transport", None), \
             patch("src.multi_tenant.agntcy_sdk_integration._transport_setup_ok", False):
            approved, message, _ = asyncio.run(
                mixin._validate_with_critic(
                    tenant_id="test-tenant",
                    conversation_id="test-conv",
                    response_text="Test response",
                    customer_message="Test question",
                    budget=None,
                    knowledge_titles=None,
                )
            )

        assert approved is False
        assert message == SAFE_FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# Category D: Benchmark Output
# ---------------------------------------------------------------------------


class TestBenchmarkOutput:
    """Generate consolidated benchmark report."""

    def test_benchmark_report_generation(self, benchmark_output):
        """Summarize saved observations for the selected run."""
        artifacts = {}
        for f in benchmark_output.glob("*.json"):
            if f.stem.startswith("transport-benchmark-"):
                continue  # Skip prior consolidated reports
            try:
                artifacts[f.stem] = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        if not artifacts:
            pytest.skip("No benchmark artifacts to summarize.")
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "phase": "4",
            "artifacts": artifacts,
        }

        report_path = benchmark_output / f"transport-benchmark-{int(time.time())}.json"
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\n  Benchmark report: {report_path}")
        assert report_path.exists()
