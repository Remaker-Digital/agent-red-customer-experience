# tests/unit/test_kb_event_loop_offload.py: KB BM25 and the semantic-cache scan run off the event loop
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
Design PR5 (docs/beta-single-container-design.md, sections 3 and 6). KB BM25 scoring
and the semantic-cache cosine scan are pure CPU work. They run through asyncio.to_thread
so a large tenant corpus or a full semantic index (up to 200 vectors of 3072 dimensions)
does not stall the event loop. Results must be identical to the synchronous code, and
cache mutation, the LRU get and the metrics stay on the loop.
"""

from __future__ import annotations

import asyncio
import copy
import random
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.multi_tenant import knowledge_vectorizer as kv
from src.multi_tenant import semantic_cache as sc
from src.multi_tenant.semantic_cache import SemanticCache

TENANT_ID = "tenant-offload-test-0001"


def _corpus() -> list[dict[str, Any]]:
    """A small KB corpus whose BM25 ranking is unambiguous."""
    rows = [
        (
            "kb-001",
            "Return Policy",
            "Items can be returned within 30 days. Refunds go to the original card.",
            ["returns", "refund"],
        ),
        ("kb-002", "Wireless Headphones", "Noise cancelling headphones with a 40 hour battery.", ["audio"]),
        ("kb-003", "Shipping Information", "Free shipping over 50 dollars. Express delivery in 2 days.", ["shipping"]),
        ("kb-004", "Refund Timing", "A refund reaches your card within 5 business days of the return.", ["refund"]),
        ("kb-005", "USB-C Cable", "Six foot cable that supports fast charging.", ["accessories"]),
    ]
    return [
        {"id": eid, "title": title, "content": content, "tags": tags, "is_active": True}
        for eid, title, content, tags in rows
    ]


def _vectorizer(corpus: list[dict[str, Any]]) -> tuple[kv.KnowledgeVectorizer, MagicMock, SemanticCache]:
    """A configured vectorizer with a private semantic cache and a mocked repository."""
    repo = MagicMock()
    repo.list_active_lightweight = AsyncMock(return_value=corpus)
    repo.vector_search = AsyncMock(return_value=[])
    vectorizer = kv.KnowledgeVectorizer()
    vectorizer.configure(kb_repo=repo, openai_client=None)
    cache = SemanticCache()
    vectorizer._cache = cache
    return vectorizer, repo, cache


def _unit(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec]


def _index_vectors(count: int = 20, dims: int = 64) -> list[list[float]]:
    rng = random.Random(1234)
    return [_unit([rng.uniform(-1.0, 1.0) for _ in range(dims)]) for _ in range(count)]


def _filled_cache(vectors: list[list[float]]) -> SemanticCache:
    cache = SemanticCache(search_max_entries=50, similarity_threshold=0.95)
    for i, emb in enumerate(vectors):
        cache.put_semantic_search_results(
            TENANT_ID,
            f"query {i}",
            emb,
            [{"id": f"kb-{i}", "rrf_score": 0.5}],
        )
    return cache


async def test_bm25_runs_off_loop_with_identical_results() -> None:
    corpus = _corpus()
    query = "refund for a return"
    expected = kv.compute_bm25_scores(query, copy.deepcopy(corpus))
    assert expected[0][1] > 0  # the query matches, so the ranking is meaningful

    loop_thread = threading.get_ident()
    seen_threads: list[int] = []
    real_scores = kv.compute_bm25_scores

    def recording_scores(q: str, entries: list[dict[str, Any]]) -> list[tuple[str, float]]:
        seen_threads.append(threading.get_ident())
        return real_scores(q, entries)

    # vector_search returns nothing, so search() takes the BM25-only branch and
    # exposes the raw BM25 scores in rank order.
    vectorizer, repo, _cache = _vectorizer(corpus)
    with patch.object(kv, "compute_bm25_scores", recording_scores):
        results = await vectorizer.search(TENANT_ID, query, top_k=3)

    repo.list_active_lightweight.assert_awaited_once()
    assert len(seen_threads) == 1
    assert seen_threads[0] != loop_thread, "BM25 scoring ran on the event loop thread"
    assert [(r["id"], r["bm25_score"]) for r in results] == expected[:3]


async def test_async_semantic_lookup_matches_sync_and_scans_off_loop() -> None:
    vectors = _index_vectors()
    rng = random.Random(99)
    near_query = _unit([x + rng.uniform(-0.01, 0.01) for x in vectors[7]])
    far_query = _unit([rng.uniform(-1.0, 1.0) for _ in range(len(vectors[0]))])
    loop_thread = threading.get_ident()

    for query, expect_hit in ((near_query, True), (far_query, False)):
        sync_cache = _filled_cache(vectors)
        async_cache = _filled_cache(vectors)
        expected = sync_cache.get_semantic_search_results(TENANT_ID, query)
        if expect_hit:
            assert expected == [{"id": "kb-7", "rrf_score": 0.5}]
        else:
            assert expected is None

        cosine_threads: list[int] = []
        get_threads: list[int] = []
        real_cosine = sc.cosine_similarity
        real_get = async_cache._search_cache.get

        def recording_cosine(a: list[float], b: list[float]) -> float:
            cosine_threads.append(threading.get_ident())
            return real_cosine(a, b)

        def recording_get(key: str) -> Any:
            get_threads.append(threading.get_ident())
            return real_get(key)

        async_cache._search_cache.get = recording_get
        with patch.object(sc, "cosine_similarity", recording_cosine):
            got = await async_cache.aget_semantic_search_results(TENANT_ID, query)

        assert got == expected
        assert async_cache.summary()["search_cache"] == sync_cache.summary()["search_cache"]
        assert len(cosine_threads) == len(vectors)
        assert all(t != loop_thread for t in cosine_threads), "cosine scan ran on the event loop thread"
        assert all(t == loop_thread for t in get_threads), "LRU get left the event loop thread"
        if expect_hit:
            assert get_threads, "a hit must read the search cache"


async def test_index_append_during_scan_does_not_change_result() -> None:
    query = [1.0, 0.0, 0.0, 0.0]
    near_results = [{"id": "kb-near", "rrf_score": 0.8}]
    exact_results = [{"id": "kb-exact", "rrf_score": 0.9}]
    cache = SemanticCache(search_max_entries=50, similarity_threshold=0.95)
    cache.put_semantic_search_results(TENANT_ID, "near", [0.99, 0.1, 0.0, 0.0], near_results)
    cache.put_semantic_search_results(TENANT_ID, "far one", [0.0, 1.0, 0.0, 0.0], [{"id": "kb-f1"}])
    cache.put_semantic_search_results(TENANT_ID, "far two", [0.0, 0.0, 1.0, 0.0], [{"id": "kb-f2"}])
    index_size = len(cache._semantic_index[TENANT_ID])
    assert cache.get_semantic_search_results(TENANT_ID, query) == near_results

    loop = asyncio.get_running_loop()
    appended = threading.Event()
    append_seen_during_scan: list[bool] = []
    scanned: list[list[float]] = []
    real_cosine = sc.cosine_similarity

    def append_on_loop() -> None:
        # A better match lands in the live index while the worker is scanning.
        cache.put_semantic_search_results(TENANT_ID, "exact", list(query), exact_results)
        appended.set()

    def cosine_during_scan(a: list[float], b: list[float]) -> float:
        if not scanned:
            loop.call_soon_threadsafe(append_on_loop)
            append_seen_during_scan.append(appended.wait(timeout=5))
        scanned.append(b)
        return real_cosine(a, b)

    with patch.object(sc, "cosine_similarity", cosine_during_scan):
        got = await cache.aget_semantic_search_results(TENANT_ID, query)

    assert append_seen_during_scan == [True], "the append did not run on the loop during the scan"
    assert len(cache._semantic_index[TENANT_ID]) == index_size + 1
    assert len(scanned) == index_size
    assert got == near_results
    # The appended entry is a better match, so the next lookup sees it.
    assert await cache.aget_semantic_search_results(TENANT_ID, query) == exact_results


async def test_search_semantic_cache_hit_scans_off_loop() -> None:
    query = "how do refunds work"
    embedding = _unit([1.0, 0.5, 0.25, 0.0])
    cached_results = [{"id": "kb-001", "rrf_score": 0.7}]
    vectorizer, repo, cache = _vectorizer(_corpus())
    cache.put_embedding(TENANT_ID, query, embedding)
    cache.put_semantic_search_results(TENANT_ID, "what is the refund policy", embedding, cached_results)

    loop_thread = threading.get_ident()
    cosine_threads: list[int] = []
    real_cosine = sc.cosine_similarity

    def recording_cosine(a: list[float], b: list[float]) -> float:
        cosine_threads.append(threading.get_ident())
        return real_cosine(a, b)

    with patch.object(sc, "cosine_similarity", recording_cosine):
        results = await vectorizer.search(TENANT_ID, query)

    assert results == cached_results
    assert vectorizer.metrics._recent_queries[-1]["mode"] == "semantic_cache_hit"
    repo.vector_search.assert_not_awaited()
    repo.list_active_lightweight.assert_not_awaited()
    assert cosine_threads and all(t != loop_thread for t in cosine_threads)
