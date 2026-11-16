"""Latency measurements for the upgrade regression suite."""

import math
import time


def measure_get(client, path):
    """Return the response and elapsed milliseconds from a monotonic clock."""
    start = time.perf_counter()
    response = client.get(path)
    return response, (time.perf_counter() - start) * 1000


def nearest_rank_p95(samples_ms):
    """Return the sample at the one-based rank ceil(0.95 * n)."""
    return sorted(samples_ms)[math.ceil(len(samples_ms) * 0.95) - 1]
