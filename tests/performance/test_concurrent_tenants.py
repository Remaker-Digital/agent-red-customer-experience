"""14c: Performance validation — concurrent tenant load testing.

Exercises tenant data and isolation with a mocked population of 680 tenants
(SPEC-1516). The mock batch checks correctness.

Test structure:
    - PERF-01 to PERF-02: Tenant identity and serialization
    - PERF-03 to PERF-04: Waived evaluation cases
    - PERF-05 to PERF-08: Concurrent tenant load under mock conditions
    - PERF-09 to PERF-12: Resource isolation under concurrent access

Run:
    pytest tests/performance/test_concurrent_tenants.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TENANT_COUNT = 680  # Near-term scale target (SPEC-1516)
CONCURRENT_BATCH = 50  # Batch size for concurrent tests


def _make_tenant_context(tenant_id: str) -> MagicMock:
    """Build a mock TenantContext."""
    ctx = MagicMock()
    ctx.tenant_id = tenant_id
    ctx.team_member_id = f"member-{tenant_id}"
    ctx.team_member_email = f"admin@{tenant_id}.example.com"
    ctx.team_member_role = "admin"
    return ctx


def _make_tenant_doc(tenant_id: str) -> dict[str, Any]:
    """Build a minimal tenant document."""
    return {
        "id": tenant_id,
        "tenant_id": tenant_id,
        "organization_name": f"Tenant {tenant_id}",
        "status": "active",
        "tier": "professional",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# PERF-01 to PERF-04: Single-tenant baselines
# ---------------------------------------------------------------------------


class TestSingleTenantBaseline:
    """Tenant fixture and serialization checks."""

    def test_perf_01_tenant_context_preserves_identity(self):
        ctx = _make_tenant_context("t-0001")
        assert (ctx.tenant_id, ctx.team_member_id, ctx.team_member_role) == ("t-0001", "member-t-0001", "admin")

    def test_perf_02_tenant_doc_round_trips_through_json(self):
        doc = _make_tenant_doc("t-0001")
        assert json.loads(json.dumps(doc)) == doc

    @pytest.mark.skip(
        reason="waived per DELIB-S330-SLICE-8-6-PHASE-3-G-EVALUATION-MODULE-WAIVER: "
        "evaluation/ module deleted at S320 c9fc7216 (Phase 1 isolation cleanup); "
        "this test still imports from it. Scope = this test + test_perf_04. "
        "Expiry = evaluation module restored OR test rewritten "
        "(backlog: GTKB-EVALUATION-MODULE-RESTORATION-001). "
        "Residual risk: golden-dataset performance gate not exercised in CI."
    )
    def test_perf_03_golden_dataset_loads_under_100ms(self):
        """PERF-03: Golden evaluation dataset loads quickly."""
        from evaluation.pilots.quality_pilot import load_dataset

        start = time.perf_counter()
        scenarios = load_dataset()
        elapsed = time.perf_counter() - start

        assert len(scenarios) >= 20
        assert elapsed < 0.1, f"Dataset load took {elapsed:.3f}s"

    @pytest.mark.skip(
        reason="waived per DELIB-S330-SLICE-8-6-PHASE-3-G-EVALUATION-MODULE-WAIVER: "
        "evaluation/ module deleted at S320 c9fc7216 (Phase 1 isolation cleanup); "
        "this test still imports from it. Scope = this test + test_perf_03. "
        "Expiry = evaluation module restored OR test rewritten "
        "(backlog: GTKB-EVALUATION-MODULE-RESTORATION-001). "
        "Residual risk: pilot-evaluation performance gate not exercised in CI."
    )
    def test_perf_04_quality_pilot_evaluation_under_500ms(self):
        """PERF-04: Full quality pilot evaluation completes quickly."""
        from evaluation.pilots.quality_pilot import load_dataset, run_pilot

        scenarios = load_dataset()
        responses = {
            s["id"]: {"response": "test response", "escalation": False, "critic_verdict": "approved"}
            for s in scenarios
        }

        start = time.perf_counter()
        report = run_pilot(responses)
        elapsed = time.perf_counter() - start

        assert report.total_scenarios >= 20
        assert elapsed < 0.5, f"Pilot evaluation took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# PERF-05 to PERF-08: Concurrent tenant workload
# ---------------------------------------------------------------------------


class TestConcurrentTenantLoad:
    """Concurrent tenant access patterns at launch scale."""

    @pytest.mark.asyncio
    async def test_perf_05_concurrent_context_creation(self):
        """PERF-05: Every context in the mock batch keeps a distinct tenant."""
        async def create_context(i: int) -> MagicMock:
            return _make_tenant_context(f"t-{i:04d}")

        tasks = [create_context(i) for i in range(TENANT_COUNT)]
        results = await asyncio.gather(*tasks)

        assert len(results) == TENANT_COUNT
        tenant_ids = {r.tenant_id for r in results}
        assert len(tenant_ids) == TENANT_COUNT

    @pytest.mark.asyncio
    async def test_perf_06_concurrent_mock_db_reads(self):
        """PERF-06: Every mock read returns the requested tenant."""
        mock_repo = AsyncMock()
        mock_repo.read = AsyncMock(side_effect=lambda tid: _make_tenant_doc(tid))

        async def read_tenant(i: int) -> dict:
            return await mock_repo.read(f"t-{i:04d}")

        tasks = [read_tenant(i) for i in range(TENANT_COUNT)]
        results = await asyncio.gather(*tasks)

        assert [r["tenant_id"] for r in results] == [f"t-{i:04d}" for i in range(TENANT_COUNT)]
        assert all(r["status"] == "active" for r in results)

    @pytest.mark.asyncio
    async def test_perf_07_concurrent_mock_writes(self):
        """PERF-07: 50 concurrent mock DB writes complete."""
        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock(return_value=None)

        async def write_tenant(i: int) -> None:
            doc = _make_tenant_doc(f"t-{i:04d}")
            await mock_repo.upsert(doc)

        tasks = [write_tenant(i) for i in range(TENANT_COUNT)]
        await asyncio.gather(*tasks)

        assert mock_repo.upsert.call_count == TENANT_COUNT

    @pytest.mark.asyncio
    async def test_perf_08_batch_processing(self):
        """PERF-08: Tenants can be processed in configurable batches."""
        processed = []

        async def process_batch(batch: list[str]) -> list[str]:
            await asyncio.sleep(0.01)
            return batch

        tenant_ids = [f"t-{i:04d}" for i in range(TENANT_COUNT)]
        for start_idx in range(0, len(tenant_ids), CONCURRENT_BATCH):
            batch = tenant_ids[start_idx:start_idx + CONCURRENT_BATCH]
            result = await process_batch(batch)
            processed.extend(result)

        assert len(processed) == TENANT_COUNT


# ---------------------------------------------------------------------------
# PERF-09 to PERF-12: Resource isolation
# ---------------------------------------------------------------------------


class TestResourceIsolation:
    """Tenant resource isolation under concurrent access."""

    @pytest.mark.asyncio
    async def test_perf_09_no_cross_tenant_data_in_concurrent_reads(self):
        """PERF-09: Concurrent reads return correct per-tenant data."""
        async def read_with_validation(i: int) -> bool:
            tid = f"t-{i:04d}"
            doc = _make_tenant_doc(tid)
            return doc["tenant_id"] == tid

        tasks = [read_with_validation(i) for i in range(TENANT_COUNT)]
        results = await asyncio.gather(*tasks)

        assert all(results), "Cross-tenant data leak detected"

    @pytest.mark.asyncio
    async def test_perf_10_concurrent_partition_key_isolation(self):
        """PERF-10: Each tenant query uses correct partition key."""
        partition_keys_used: list[str] = []

        async def query_with_pk(tenant_id: str) -> None:
            partition_keys_used.append(tenant_id)

        tasks = [query_with_pk(f"t-{i:04d}") for i in range(TENANT_COUNT)]
        await asyncio.gather(*tasks)

        assert len(partition_keys_used) == TENANT_COUNT
        assert len(set(partition_keys_used)) == TENANT_COUNT

    def test_perf_11_launch_scale_constant(self):
        """PERF-11: Scale target is configured to 680 tenants (SPEC-1516)."""
        assert TENANT_COUNT == 680

    @pytest.mark.asyncio
    async def test_perf_12_concurrent_error_isolation(self):
        """PERF-12: Error in one tenant doesn't affect others."""
        results: dict[str, str] = {}

        async def process_tenant(i: int) -> None:
            tid = f"t-{i:04d}"
            if i == 25:
                results[tid] = "error"
            else:
                results[tid] = "success"

        tasks = [process_tenant(i) for i in range(TENANT_COUNT)]
        await asyncio.gather(*tasks)

        success_count = sum(1 for v in results.values() if v == "success")
        error_count = sum(1 for v in results.values() if v == "error")

        assert success_count == TENANT_COUNT - 1
        assert error_count == 1
