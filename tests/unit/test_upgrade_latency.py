"""Offline checks for the upgrade regression latency measurements."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.helpers import upgrade_latency


def test_measure_get_uses_elapsed_time_when_the_wall_clock_moves_backward(monkeypatch):
    wall_ticks = iter((1_000.0, 990.0))
    elapsed_ticks = iter((42.0, 42.125))
    clock = SimpleNamespace(time=lambda: next(wall_ticks), perf_counter=lambda: next(elapsed_ticks))
    monkeypatch.setattr(upgrade_latency, "time", clock)
    client = Mock()

    response, elapsed_ms = upgrade_latency.measure_get(client, "/health")

    client.get.assert_called_once_with("/health")
    assert response is client.get.return_value
    assert elapsed_ms == pytest.approx(125.0)


@pytest.mark.parametrize(("count", "expected"), [(10, 10), (20, 19)])
def test_nearest_rank_p95_uses_ceiling_rank(count, expected):
    samples_ms = list(range(count, 0, -1))

    assert upgrade_latency.nearest_rank_p95(samples_ms) == expected
