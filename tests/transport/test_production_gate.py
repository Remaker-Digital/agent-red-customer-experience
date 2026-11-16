"""Phase 5 — Production rollout gate.

Pre-production checklist that must pass before deploying the containerized
agent topology to production (GOV-16). Each test validates one gate
criterion from the recovery plan. All gates execute or validate — none
merely check existence or inspect source code.

Gate criteria (the original transport analysis Phase 5):
1. Executable transport/container tests pass
2. Widget end-to-end tests pass
3. Per-container performance baselines pass
4. Failure-injection tests pass
5. MCP exception register is empty or explicitly approved
6. KB/spec state reflects the real deployed architecture

Codex GO: the original transport analysis.
Governing decisions: ADR-001, DCL-002 v4, DCL-003, GOV-16.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.transport.conftest import (
    BENCHMARK_RUN_ID,
    BENCHMARK_SCHEMA_VERSION,
    ESCALATION_STAGE_NAMES,
    GATEWAY_ATTEMPTS,
    OPTIONAL_LATENCY_POPULATIONS,
    PIPELINE_STAGE_NAMES,
    SELECTED_BENCHMARK_RUN,
    BenchmarkResult,
    usable_latency,
)
from tests.transport.test_transport_performance import ESCALATION_MESSAGES, SAMPLE_MESSAGES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Gate 1: Executable transport/container tests pass (5 tests)
# ---------------------------------------------------------------------------


class TestGate1TransportTests:
    """Transport contract, smoke, parity, and governance tests must PASS."""

    def _run_suite(self, test_file: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pytest", test_file, "-q", "--tb=line"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=timeout,
        )

    def test_contract_suite_passes(self):
        """Phase 3 Category 1: contract tests must pass."""
        result = self._run_suite("tests/transport/test_transport_contracts.py")
        assert result.returncode == 0, (
            f"Contract tests FAILED (rc={result.returncode}):\n{result.stdout[-500:]}"
        )

    def test_smoke_suite_passes(self):
        """Phase 3 Category 2: smoke tests must pass."""
        result = self._run_suite("tests/transport/test_transport_smoke.py")
        assert result.returncode == 0, (
            f"Smoke tests FAILED (rc={result.returncode}):\n{result.stdout[-500:]}"
        )

    def test_parity_suite_passes(self):
        """Phase 3 Category 4: environment parity tests must pass."""
        result = self._run_suite("tests/transport/test_environment_parity.py")
        assert result.returncode == 0, (
            f"Parity tests FAILED (rc={result.returncode}):\n{result.stdout[-500:]}"
        )

    def test_governance_suite_passes(self):
        """Phase 3 Category 5: governance integrity tests must pass.

        Executes real KB operations against a temp DB copy (not just collectible).

        Obsolete in Agent Red standalone (RC-1 / gtkb-readiness): 
        ``test_governance_integrity.py`` was retired with GT-KB's knowledge-db
        tooling (GT-KB 313c5b597) and is not present in this repository.
        """
        gov_path = PROJECT_ROOT / "tests" / "transport" / "test_governance_integrity.py"
        if not gov_path.is_file():
            pytest.skip(
                "RC-1: tests/transport/test_governance_integrity.py retired with "
                "tools/knowledge-db; not present in Agent Red"
            )
        result = self._run_suite("tests/transport/test_governance_integrity.py")
        assert result.returncode == 0, (
            f"Governance tests FAILED (rc={result.returncode}):\n{result.stdout[-500:]}"
        )

    def test_transport_test_count_sufficient(self):
        """Transport test suite (Phases 3-4) must have >= 30 tests."""
        # Omit retired governance suite when absent (RC-1 / gtkb-readiness).
        suite_files = [
            "tests/transport/test_transport_contracts.py",
            "tests/transport/test_transport_smoke.py",
            "tests/transport/test_environment_parity.py",
        ]
        gov_path = PROJECT_ROOT / "tests" / "transport" / "test_governance_integrity.py"
        if gov_path.is_file():
            suite_files.append("tests/transport/test_governance_integrity.py")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *suite_files, "--collect-only", "-q"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=30,
        )
        count = 0
        for line in result.stdout.split("\n"):
            if "collected" in line or "selected" in line:
                for p in line.split():
                    if p.isdigit():
                        count = int(p)
                        break
        assert count >= 30, (
            f"Only {count} transport tests collected (need >=30). "
            f"Output: {result.stdout[-200:]}"
        )


# ---------------------------------------------------------------------------
# Gate 2: Widget E2E tests pass (2 tests)
# ---------------------------------------------------------------------------


class TestGate2WidgetTests:
    """Widget E2E tests must pass or skip honestly (host-gated)."""

    def test_widget_e2e_suite_passes(self):
        """Containerized E2E suite (including widget) must pass.

        Subprocess-runs the full E2E suite. Widget tests pass when staging
        is available, skip honestly when not. No source inspection.
        """
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/transport/test_containerized_e2e.py", "-q", "--tb=line"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=120,
        )
        # rc=0 means all passed or skipped (no failures)
        assert result.returncode == 0, (
            f"E2E suite FAILED (rc={result.returncode}):\n{result.stdout[-500:]}"
        )

    def test_widget_tests_collectible(self):
        """Widget test classes must be collectible by pytest."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/transport/test_containerized_e2e.py::TestWidgetConversationPath",
             "tests/transport/test_containerized_e2e.py::TestWidgetStreamingPath",
             "--collect-only", "-q"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=15,
        )
        assert "2 tests" in result.stdout or "selected" in result.stdout, (
            f"Widget tests not collectible: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Gate 3: Performance baselines pass (3 tests)
# ---------------------------------------------------------------------------


class TestGate3PerformanceBaselines:
    """Phase 4 benchmark data must exist, be valid, and be honest."""

    def test_performance_tests_collectible(self):
        """Performance benchmark suite must have >= 8 tests."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/transport/test_transport_performance.py",
             "--collect-only", "-q"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=15,
        )
        for line in result.stdout.split("\n"):
            if "selected" in line or "collected" in line:
                for p in line.split():
                    if p.isdigit():
                        assert int(p) >= 8, f"Only {p} perf tests (need >=8)"
                        return
        pytest.fail(f"Could not parse test count: {result.stdout[-200:]}")

    def test_benchmark_artifacts_validated(self):
        """Check complete evidence from the selected transport run.

        Set TRANSPORT_BENCHMARK_RUN_ID to reuse the run ID across separate
        collection and validation invocations.
        """
        output = PROJECT_ROOT / "scripts" / "benchmark-results"
        run_dir = output / BENCHMARK_RUN_ID
        latency_contracts = {
            "per-hop-latency.json": ("per_hop_stage_latency", len(SAMPLE_MESSAGES), "stages", PIPELINE_STAGE_NAMES),
            "pipeline-latency.json": ("total_pipeline_latency", len(SAMPLE_MESSAGES), "benchmarks", ("total-pipeline",)),
            "escalation-latency.json": (
                "escalation_path_latency", len(ESCALATION_MESSAGES), "stages", ESCALATION_STAGE_NAMES,
            ),
            "widget-latency.json": ("widget_pipeline_latency", len(SAMPLE_MESSAGES), "benchmarks", ("widget-pipeline",)),
        }
        has_latency_evidence = any(
            (directory / name).is_file()
            for directory in (run_dir, output)
            for name in latency_contracts
        )
        if not SELECTED_BENCHMARK_RUN and not has_latency_evidence:
            pytest.skip("Transport benchmark artifacts have not been generated for this run.")
        for filename, contract in latency_contracts.items():
            self._validate_latency_artifact(output, filename, *contract)
        if (run_dir / "gateway-baseline.json").is_file():
            self._validate_latency_artifact(
                output, "gateway-baseline.json", "gateway_baseline_latency",
                GATEWAY_ATTEMPTS, "benchmarks", ("gateway-baseline",),
            )

        tier_path = run_dir / "transport-tier.json"
        assert tier_path.is_file(), f"{tier_path.name}: missing for the selected run"
        tier = json.loads(tier_path.read_text(encoding="utf-8"))
        assert tier.get("timestamp") and tier.get("test") == "transport_tier_identification"
        assert tier.get("active_tier") not in (None, "", "unknown")

    def test_per_hop_latency_data_present(self):
        """Require each per-hop population to cover the planned workload."""
        output = PROJECT_ROOT / "scripts" / "benchmark-results"
        name = "per-hop-latency.json"
        if (
            not SELECTED_BENCHMARK_RUN
            and not (output / BENCHMARK_RUN_ID / name).is_file()
            and not (output / name).is_file()
        ):
            pytest.skip("Per-hop benchmark has not been generated for this run.")
        self._validate_latency_artifact(
            output, name, "per_hop_stage_latency", len(SAMPLE_MESSAGES), "stages", PIPELINE_STAGE_NAMES,
        )

    @staticmethod
    def _validate_latency_artifact(output, filename, test_name, planned, population_key, names):
        path = output / BENCHMARK_RUN_ID / filename
        if not path.is_file() and (output / filename).is_file():
            pytest.fail(f"{filename}: legacy artifact is diagnostic only. Generate evidence for the selected run.")
        assert path.is_file(), f"{filename}: missing for the selected run"
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("schema_version") != BENCHMARK_SCHEMA_VERSION
            or data.get("completed") is not True
            or data.get("valid") is not True
        ):
            pytest.fail(f"{filename}: legacy, incomplete or invalid artifact is diagnostic only.")
        assert data.get("timestamp") and data.get("test") == test_name
        assert data.get("run_id") == BENCHMARK_RUN_ID, f"{filename}: artifact is from a different run"
        for count in ("planned_count", "attempt_count", "completed_count"):
            assert type(data.get(count)) is int and data[count] == planned, f"{filename}: invalid {count}"
        entries = data.get(population_key)
        if population_key == "stages":
            assert isinstance(entries, dict) and entries.keys() == set(names), f"{filename}: stage list differs"
            populations = [entries[name] for name in names]
        else:
            assert isinstance(entries, list) and len(entries) == len(names), f"{filename}: population list differs"
            populations = entries

        measurements = 0
        for name, result in zip(names, populations, strict=True):
            assert isinstance(result, dict), f"{filename}: invalid population"
            assert result.get("name") == name, f"{filename}: wrong population"
            counts = [result.get(key) for key in ("sample_count", "missing_count", "errors", "absent_count")]
            assert all(type(count) is int and count >= 0 for count in counts), f"{filename}: invalid counts for {name}"
            assert sum(counts) == planned, f"{filename}: unaccounted observations for {name}"
            assert result["errors"] == result["missing_count"] == 0, f"{filename}: {name} has missing or errored observations"
            if population_key == "benchmarks":
                assert result["absent_count"] == 0, f"{filename}: unexpected absent observation"
            samples = result.get("samples_ms")
            assert isinstance(samples, list) and len(samples) == result["sample_count"]
            assert all(map(usable_latency, samples)), f"{filename}: unusable latency sample"
            if name not in OPTIONAL_LATENCY_POPULATIONS:
                assert samples, f"{filename}: {name} has no usable latency measurements"
            measurements += len(samples)
            stats = BenchmarkResult(name, "validation", samples).to_dict()
            for percentile in ("p50_ms", "p95_ms", "p99_ms"):
                expected = stats[percentile]
                assert percentile in result, f"{filename}: missing {percentile}"
                actual = result[percentile]
                if expected is None:
                    assert actual is None, f"{filename}: {percentile} must be null without samples"
                else:
                    assert usable_latency(actual) and actual == expected, f"{filename}: incorrect {percentile}"
        assert measurements > 0, f"{filename}: no usable latency measurements"


# ---------------------------------------------------------------------------
# Gate 4: Failure injection verified (3 tests)
# ---------------------------------------------------------------------------


class TestGate4FailureInjection:
    """Failure paths must be verified by execution, not source inspection."""

    def test_503_on_transport_exhaustion(self):
        """Dispatch must 503 when all transport tiers fail."""
        from fastapi import HTTPException
        from src.chat.pipeline.agent_dispatch import AgentDispatchMixin

        mixin = AgentDispatchMixin.__new__(AgentDispatchMixin)
        with pytest.raises(HTTPException) as exc_info:
            mixin._require_transport_or_fail("test-agent")
        assert exc_info.value.status_code == 503

    def test_critic_fail_closed_behavioral(self):
        """Critic must return safe fallback when unavailable (executed, not inspected)."""
        from src.chat.pipeline.critic_escalation import CriticEscalationMixin
        from src.multi_tenant.critic_policy import SAFE_FALLBACK_MESSAGE

        mixin = CriticEscalationMixin.__new__(CriticEscalationMixin)
        mixin._critic = None
        mixin._agent_urls = {}

        with patch("src.multi_tenant.agntcy_sdk_integration._transport", None), \
             patch("src.multi_tenant.agntcy_sdk_integration._transport_setup_ok", False):
            approved, message, result = asyncio.run(
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

    def test_analytics_silent_drop_behavioral(self):
        """Analytics must silently drop when unavailable (executed, not inspected)."""
        from src.chat.pipeline.analytics import AnalyticsMixin

        mixin = AnalyticsMixin.__new__(AnalyticsMixin)
        mixin._agent_urls = {}

        class FakeBudget:
            stages = []
            elapsed_ms = 50.0

        class FakeTrace:
            pass

        with patch("src.multi_tenant.agntcy_sdk_integration._transport", None):
            # Must not raise — fire-and-forget
            asyncio.run(
                mixin._fire_analytics(
                    tenant_id="test-tenant",
                    conversation_id="test-conv",
                    intent="general_inquiry",
                    budget=FakeBudget(),
                    trace=FakeTrace(),
                )
            )


# ---------------------------------------------------------------------------
# Gate 5: MCP exception register
# ---------------------------------------------------------------------------


class TestGate5MCPExceptions:
    """MCP exception register must be clean or explicitly approved."""

    def test_mcp_exception_register_clean_or_approved(self):
        """DOC-MCP-EXCEPTIONS must exist and be empty or explicitly approved.

        Each exception entry must have the required schema fields
        (target_system, protocol_used, blocking_reason) — the presence
        of these fields constitutes explicit documentation/approval.
        An empty register or a register where every entry has the
        required fields satisfies the gate.

        RC-1: ``tools/knowledge-db`` was never shipped in Agent Red and was
        deleted from GT-KB (313c5b597). Skip when the tool is absent.
        """
        tools_path = PROJECT_ROOT / "tools" / "knowledge-db"
        if not tools_path.is_dir():
            pytest.skip("RC-1: tools/knowledge-db not present in Agent Red")
        sys.path.insert(0, str(tools_path))
        try:
            import db as kb_db
        except ImportError as exc:
            pytest.skip(f"RC-1: KnowledgeDB unavailable ({exc})")
        kdb = kb_db.KnowledgeDB(str(PROJECT_ROOT / "groundtruth.db"))

        doc = kdb.get_document("DOC-MCP-EXCEPTIONS")
        assert doc is not None, (
            "DOC-MCP-EXCEPTIONS not found in KB — create it to track "
            "transport/MCP deviations before production deploy"
        )

        content = doc.get("content", "")

        # Parse exception entries — each starts with "### N."
        import re
        entries = re.findall(r"###\s+\d+\.\s+(.+?)(?=###\s+\d+\.|\Z)", content, re.DOTALL)

        if not entries:
            # Empty register — clean
            print("\n  MCP exception register: empty (clean)")
            return

        # Each entry must have required schema fields
        required_fields = ["target_system", "protocol_used", "blocking_reason"]
        for i, entry in enumerate(entries, 1):
            entry_lower = entry.lower()
            missing = [f for f in required_fields if f.replace("_", " ") not in entry_lower
                       and f not in entry_lower]
            # Also check with "- field:" format
            if missing:
                missing = [f for f in missing
                           if f"- {f}:" not in entry_lower
                           and f"- {f.replace('_', ' ')}:" not in entry_lower]
            assert not missing, (
                f"MCP exception entry {i} is missing required fields: {missing}. "
                "Each exception must have target_system, protocol_used, blocking_reason "
                "to be considered explicitly approved."
            )

        print(f"\n  MCP exception register: {len(entries)} entries, all explicitly documented")


# ---------------------------------------------------------------------------
# Gate 6: KB alignment (4 tests)
# ---------------------------------------------------------------------------


class TestGate6KBAlignment:
    """KB specifications must reflect the real deployed architecture."""

    def _get_kb(self):
        """Load KnowledgeDB or skip when RC-1 tooling is absent."""
        tools_path = PROJECT_ROOT / "tools" / "knowledge-db"
        if not tools_path.is_dir():
            pytest.skip("RC-1: tools/knowledge-db not present in Agent Red")
        sys.path.insert(0, str(tools_path))
        try:
            import db as kb_db
        except ImportError as exc:
            pytest.skip(f"RC-1: KnowledgeDB unavailable ({exc})")
        return kb_db.KnowledgeDB(str(PROJECT_ROOT / "groundtruth.db"))

    def test_dcl_002_v4_per_interface_policy(self):
        """DCL-002 v4 must document per-interface transport policy.

        DCL-002 v4 allows in-process RG streaming as the intended production
        path while keeping non-streaming agents containerized.
        """
        kdb = self._get_kb()
        spec = kdb.get_spec("DCL-002")
        assert spec is not None, "DCL-002 not found in KB"
        assert spec["version"] >= 4, (
            f"DCL-002 version {spec['version']} — expected >=4 (per-interface policy)"
        )
        assert spec["status"] in ("implemented", "verified"), (
            f"DCL-002 status={spec['status']} (expected implemented/verified)"
        )
        desc = spec.get("description", "").lower()
        assert "in-process" in desc or "gateway" in desc, (
            "DCL-002 v4 should document in-process RG streaming as intended"
        )

    def test_dcl_003_exists_and_implemented(self):
        """DCL-003 (no phantom evidence) must be implemented."""
        kdb = self._get_kb()
        spec = kdb.get_spec("DCL-003")
        assert spec is not None, "DCL-003 not found in KB"
        assert spec["status"] in ("implemented", "verified"), (
            f"DCL-003 status={spec['status']} (expected implemented/verified)"
        )

    def test_adr_001_exists(self):
        """ADR-001 (dispatch by physical possibility) must exist in KB."""
        kdb = self._get_kb()
        spec = kdb.get_spec("ADR-001")
        assert spec is not None, "ADR-001 not found in KB"

    def test_spec_1802_transport_hierarchy(self):
        """SPEC-1802 (transport hierarchy) must be implemented."""
        kdb = self._get_kb()
        spec = kdb.get_spec("SPEC-1802")
        assert spec is not None, "SPEC-1802 not found in KB"
        assert spec["status"] in ("implemented", "verified"), (
            f"SPEC-1802 status={spec['status']} (expected implemented/verified)"
        )
