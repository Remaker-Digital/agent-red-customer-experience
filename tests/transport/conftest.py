"""Shared fixtures and helpers for transport tests.

Provides SSE event parsing, benchmark data structures, and staging
fixtures used across Phase 3 (E2E) and Phase 4 (performance) tests.

© 2026 Michael Palmeter. All rights reserved.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

TEST_HOST_URL = os.environ.get("TEST_HOST_URL", "")
STAGING_WIDGET_KEY = os.environ.get("STAGING_WIDGET_KEY", "")
SELECTED_BENCHMARK_RUN = os.environ.get("TRANSPORT_BENCHMARK_RUN_ID")
BENCHMARK_RUN_ID = SELECTED_BENCHMARK_RUN or os.environ.get("PYTEST_XDIST_TESTRUNUID") or uuid4().hex
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", BENCHMARK_RUN_ID):
    raise ValueError("Invalid transport benchmark run ID.")

PIPELINE_STAGE_NAMES = ("intent-classifier", "knowledge-retrieval", "response-generator", "critic-supervisor")
ESCALATION_STAGE_NAMES = ("intent-classifier", "escalation-handler")
# The escalation workload can finish without invoking the handler.
OPTIONAL_LATENCY_POPULATIONS = frozenset({"escalation-handler"})
GATEWAY_ATTEMPTS = 20
# Step 5 of docs/operations/load-test-procedure.md sets the health endpoint
# distribution target. Step 3's 1000 ms value applies to the warm-up preflight.
READY_P95_REFERENCE_MS = 500
BENCHMARK_SCHEMA_VERSION = 2

requires_test_host = pytest.mark.skipif(
    not TEST_HOST_URL,
    reason="TEST_HOST_URL not set — requires staging test host",
)


# ---------------------------------------------------------------------------
# SSE event parsing (shared with Phase 3 E2E tests)
# ---------------------------------------------------------------------------


def consume_sse_events(raw_text: str) -> list[dict[str, Any]]:
    """Parse SSE text into a list of event dicts."""
    events: list[dict[str, Any]] = []
    for line in raw_text.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                events.append(json.loads(payload))
    return events


def has_stage(events: list[dict], stage_name: str, state: str = "completed") -> bool:
    """Check if events contain a specific stage event."""
    return any(
        e.get("stage") == stage_name and e.get("status") == state
        for e in events
    )


def has_error_event(events: list[dict]) -> bool:
    """Check if events contain an error event."""
    return any(
        e.get("type") == "error"
        or (e.get("code") and e.get("recoverable") is not None)
        for e in events
    )


def get_stage_latency(events: list[dict], stage_name: str) -> float | None:
    """Extract latency_ms for a completed stage, or None if not found."""
    for e in events:
        if e.get("stage") == stage_name and e.get("status") == "completed":
            return e.get("latency_ms")
    return None


def get_total_latency(events: list[dict]) -> float | None:
    """Extract total_latency_ms from the done event, or None."""
    for e in events:
        if is_done_event(e):
            return e.get("total_latency_ms")
    return None


def is_done_event(event: dict) -> bool:
    return bool(event.get("conversation_id")) and type(event.get("turn_count")) is int and "stage" not in event


# ---------------------------------------------------------------------------
# Benchmark data structures
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkResult:
    """Captures a single benchmark run with percentile computation."""

    name: str
    tier: str
    samples: list[float] = field(default_factory=list)
    errors: int = 0
    absent: int = 0

    def record(self, latency: Any) -> None:
        if usable_latency(latency):
            self.samples.append(latency)

    def percentile(self, fraction: float) -> float | None:
        if not self.samples:
            return None
        return sorted(self.samples)[ceil(len(self.samples) * fraction) - 1]

    def to_dict(self, attempts: int | None = None) -> dict:
        if attempts is None:
            attempts = len(self.samples) + self.errors + self.absent
        return {
            "name": self.name,
            "tier": self.tier,
            "sample_count": len(self.samples),
            "samples_ms": self.samples,
            "p50_ms": statistics.median(self.samples) if self.samples else None,
            "p95_ms": self.percentile(0.95),
            "p99_ms": self.percentile(0.99),
            "errors": self.errors,
            "absent_count": self.absent,
            "missing_count": attempts - len(self.samples) - self.errors - self.absent,
        }


def usable_latency(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value < float("inf")


@dataclass
class LatencyReport:
    """Save one benchmark's progress before and after every attempted request."""

    path: Path
    test_name: str
    planned_count: int
    populations: dict[str, BenchmarkResult]
    population_key: str
    attempt_count: int = 0
    completed_count: int = 0
    warmup_status: int | None = None

    def measure(self, workload, observe: Callable) -> None:
        self.write()
        for item in workload:
            self.attempt_count += 1
            self.write()
            observe(item)
            self.completed_count += 1
            self.write()
        self.write(completed=True)

    def write(self, *, completed: bool = False) -> None:
        entries = {name: result.to_dict(self.attempt_count) for name, result in self.populations.items()}
        finished = completed and self.attempt_count == self.completed_count == self.planned_count
        valid = (
            finished
            and any(entry["sample_count"] for entry in entries.values())
            and all(
                entry["errors"] == entry["missing_count"] == 0
                and (entry["sample_count"] > 0 or name in OPTIONAL_LATENCY_POPULATIONS)
                for name, entry in entries.items()
            )
        )
        data = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "run_id": BENCHMARK_RUN_ID,
            "timestamp": datetime.now(UTC).isoformat(),
            "test": self.test_name,
            "planned_count": self.planned_count,
            "attempt_count": self.attempt_count,
            "completed_count": self.completed_count,
            "completed": finished,
            "valid": valid,
            "percentile_method": "median for P50, nearest rank for P95 and P99",
            self.population_key: entries if self.population_key == "stages" else list(entries.values()),
        }
        if self.test_name == "gateway_baseline_latency":
            data["warmup_status"] = self.warmup_status
            data["health_distribution_p95_reference_ms"] = READY_P95_REFERENCE_MS
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def require_measurements(self) -> None:
        if not any(result.samples for result in self.populations.values()):
            pytest.fail("Insufficient evidence: no usable latency measurements.")


# ---------------------------------------------------------------------------
# Staging fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staging_base_url() -> str:
    """Staging gateway base URL (requires TEST_HOST_URL)."""
    if not TEST_HOST_URL:
        pytest.skip("TEST_HOST_URL not set")
    return TEST_HOST_URL.rstrip("/")


@pytest.fixture
def widget_headers() -> dict[str, str]:
    """Widget auth headers for chat API (requires STAGING_WIDGET_KEY)."""
    if not STAGING_WIDGET_KEY:
        pytest.skip("STAGING_WIDGET_KEY not set")
    return {
        "X-Widget-Key": STAGING_WIDGET_KEY,
        "Content-Type": "application/json",
        "X-Widget-Origin": "https://test-store.myshopify.com",
    }


@pytest.fixture
def benchmark_output() -> Path:
    """Output directory for benchmark results."""
    out = Path(__file__).parent.parent.parent / "scripts" / "benchmark-results" / BENCHMARK_RUN_ID
    out.mkdir(parents=True, exist_ok=True)
    return out


def run_conversation(base_url: str, headers: dict, message: str) -> list[dict]:
    """Create a conversation and consume SSE events. Returns parsed events."""
    import httpx

    client = httpx.Client(timeout=60.0)
    try:
        resp = client.post(
            f"{base_url}/api/chat/conversations",
            headers=headers,
            json={"initial_message": message},
        )
        if resp.status_code != 201:
            return []
        data = resp.json()
        stream_url = data.get("stream_url", f"/api/chat/stream/{data['conversation_id']}")
        stream = client.get(
            f"{base_url}{stream_url}",
            headers={**headers, "Accept": "text/event-stream"},
            timeout=60.0,
        )
        if stream.status_code != 200:
            return []
        return consume_sse_events(stream.text)
    finally:
        client.close()
