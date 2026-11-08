# tests/unit/test_kb_zero_vector_repair.py: RC-11 KB zero-vector repair and cache invalidation
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR4 (docs/beta-single-container-design.md, sections 3 and 6).

In single-container mode knowledge retrieval runs in the gateway, whose KB vectorizer now has an
Azure OpenAI client (PR3). Rows embedded in the past by a client-less vectorizer hold all-zero
vectors, which vector search cannot rank. With in_process_mode on (the flag and a client):

- a stored all-zero embedding counts as missing in embed_entry and embed_batch;
- embed_unembedded also repairs zero-vector rows from a separate repository query, and checks
  the whole vector in Python because the query only looks at the first three elements;
- embed_batch invalidates the tenant search cache once it has embedded at least one entry;
- the startup embed job also finds tenants that have zero-vector rows.

With the mode off (the default, and every deployment without the flag) nothing changes. The
guard tests marked "passes before and after" use only the existing configure signature.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.app.lifecycle as lifecycle
from src.multi_tenant.knowledge_vectorizer import (
    EMBEDDING_DIMENSIONS,
    KnowledgeVectorizer,
    compute_content_hash,
)
from src.multi_tenant.repositories.knowledge import KnowledgeBaseRepository

TENANT = "tenant-zero-repair-0001"
FLAG = "SINGLE_CONTAINER_MODE"
REAL_VECTOR_VALUE = 0.25

STARTUP_QUERY_DEFAULT = (
    "SELECT DISTINCT c.tenant_id FROM c "
    "WHERE c.is_active = true "
    "AND (NOT IS_DEFINED(c.embedding) OR c.embedding = null)"
)
STARTUP_QUERY_IN_PROCESS = (
    "SELECT DISTINCT c.tenant_id FROM c "
    "WHERE c.is_active = true "
    "AND (NOT IS_DEFINED(c.embedding) OR c.embedding = null "
    "OR (IS_ARRAY(c.embedding) AND c.embedding[0] = 0 "
    "AND c.embedding[1] = 0 AND c.embedding[2] = 0))"
)


def _zero_vector() -> list[float]:
    return [0.0] * EMBEDDING_DIMENSIONS


def _zero_head_vector() -> list[float]:
    """Zero in the three elements the query checks, non-zero after them."""
    return [0.0, 0.0, 0.0] + [0.1] * (EMBEDDING_DIMENSIONS - 3)


def _entry(entry_id: str = "kb-1", embedding: list[float] | None = None) -> dict[str, Any]:
    """An active KB row whose stored content_hash matches its title and content."""
    title = f"Title {entry_id}"
    content = f"Content for {entry_id}."
    return {
        "id": entry_id,
        "tenant_id": TENANT,
        "entry_type": "policy",
        "title": title,
        "content": content,
        "tags": [],
        "is_active": True,
        "embedding": embedding,
        "content_hash": compute_content_hash(title, content),
    }


class _FakeEmbeddings:
    """Stands in for AsyncAzureOpenAI.embeddings and returns non-zero vectors."""

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    async def create(self, *, model: str, input: list[str], dimensions: int) -> SimpleNamespace:
        self.inputs.append(list(input))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[REAL_VECTOR_VALUE] * dimensions) for _ in input])


def _client() -> SimpleNamespace:
    return SimpleNamespace(embeddings=_FakeEmbeddings())


def _vectorizer(client: Any = None, **configure_kwargs: Any) -> KnowledgeVectorizer:
    """A configured vectorizer with a fake repository and a private cache mock."""
    repo = MagicMock(name="kb_repo")
    repo.patch = AsyncMock(return_value={"id": "patched"})
    repo.list_unembedded = AsyncMock(return_value=[])
    repo.list_zero_vector_candidates = AsyncMock(return_value=[])
    vectorizer = KnowledgeVectorizer()
    vectorizer.configure(kb_repo=repo, openai_client=client, **configure_kwargs)
    vectorizer._cache = MagicMock(name="semantic_cache")
    return vectorizer


def _patched_ids(vectorizer: KnowledgeVectorizer) -> list[str]:
    return [call.kwargs["document_id"] for call in vectorizer._kb_repo.patch.await_args_list]


def _patched_embedding(vectorizer: KnowledgeVectorizer) -> list[float]:
    operations = vectorizer._kb_repo.patch.await_args.kwargs["operations"]
    return next(op["value"] for op in operations if op["path"] == "/embedding")


# ---------------------------------------------------------------------------
# embed_entry and embed_batch: an all-zero vector counts as missing in mode
# ---------------------------------------------------------------------------


async def test_zero_vector_reembedded_in_process_mode() -> None:
    """Fails before: configure() does not accept in_process_mode (TypeError)."""
    single = _vectorizer(_client(), in_process_mode=True)
    result = await single.embed_entry(TENANT, _entry("kb-zero", _zero_vector()))
    assert result == {"id": "patched"}
    assert _patched_ids(single) == ["kb-zero"]
    assert _patched_embedding(single) == [REAL_VECTOR_VALUE] * EMBEDDING_DIMENSIONS

    batch = _vectorizer(_client(), in_process_mode=True)
    count = await batch.embed_batch(TENANT, [_entry("kb-zero", _zero_vector())])
    assert count == 1
    assert _patched_ids(batch) == ["kb-zero"]
    assert _patched_embedding(batch) == [REAL_VECTOR_VALUE] * EMBEDDING_DIMENSIONS


@pytest.mark.parametrize(
    "embedding",
    [[0.1] * EMBEDDING_DIMENSIONS, _zero_head_vector()],
    ids=["non-zero", "zero-head-only"],
)
async def test_nonzero_vector_still_skipped(embedding: list[float]) -> None:
    """In mode, a real vector with an unchanged content_hash is still skipped."""
    vectorizer = _vectorizer(_client(), in_process_mode=True)

    assert await vectorizer.embed_entry(TENANT, _entry("kb-real", embedding)) is None
    assert await vectorizer.embed_batch(TENANT, [_entry("kb-real", embedding)]) == 0

    vectorizer._kb_repo.patch.assert_not_awaited()
    assert vectorizer._openai_client.embeddings.inputs == []
    vectorizer._cache.invalidate_tenant.assert_not_called()


@pytest.mark.parametrize("with_client", [False, True], ids=["no-client", "client"])
async def test_zero_vector_kept_when_mode_off(with_client: bool) -> None:
    """Guard, passes before and after: the default configure keeps today's dev behaviour."""
    vectorizer = _vectorizer(_client() if with_client else None)

    assert await vectorizer.embed_entry(TENANT, _entry("kb-zero", _zero_vector())) is None
    assert await vectorizer.embed_batch(TENANT, [_entry("kb-zero", _zero_vector())]) == 0

    vectorizer._kb_repo.patch.assert_not_awaited()
    vectorizer._cache.invalidate_tenant.assert_not_called()


async def test_in_process_mode_requires_a_client() -> None:
    """Without a client the mode stays off, so repair can never rewrite zeros with zeros."""
    vectorizer = _vectorizer(None, in_process_mode=True)

    assert vectorizer._in_process_mode is False
    assert await vectorizer.embed_entry(TENANT, _entry("kb-zero", _zero_vector())) is None
    vectorizer._kb_repo.patch.assert_not_awaited()


# ---------------------------------------------------------------------------
# embed_unembedded: union with verified zero-vector candidates
# ---------------------------------------------------------------------------


async def test_embed_unembedded_unions_and_verifies() -> None:
    """Fails before: configure() does not accept in_process_mode (TypeError)."""
    vectorizer = _vectorizer(_client(), in_process_mode=True)
    missing = _entry("kb-missing", None)
    zero = _entry("kb-zero", _zero_vector())
    vectorizer._kb_repo.list_unembedded.return_value = [missing]
    vectorizer._kb_repo.list_zero_vector_candidates.return_value = [
        zero,
        dict(zero),  # duplicate row
        _entry("kb-missing", _zero_vector()),  # same id as a null row
        _entry("kb-false-positive", _zero_head_vector()),  # non-zero tail
    ]

    with patch.object(vectorizer, "embed_batch", wraps=vectorizer.embed_batch) as embed_batch:
        count = await vectorizer.embed_unembedded(TENANT)

    vectorizer._kb_repo.list_unembedded.assert_awaited_once_with(TENANT)
    vectorizer._kb_repo.list_zero_vector_candidates.assert_awaited_once_with(TENANT)
    sent = embed_batch.await_args.args[1]
    assert [e["id"] for e in sent] == ["kb-missing", "kb-zero"]
    assert sent[0] is missing
    assert count == 2
    assert _patched_ids(vectorizer) == ["kb-missing", "kb-zero"]


async def test_embed_unembedded_repairs_zero_rows_without_null_rows() -> None:
    """Zero-vector rows are repaired even when no row has a null embedding."""
    vectorizer = _vectorizer(_client(), in_process_mode=True)
    vectorizer._kb_repo.list_zero_vector_candidates.return_value = [_entry("kb-zero", _zero_vector())]

    assert await vectorizer.embed_unembedded(TENANT) == 1
    assert _patched_ids(vectorizer) == ["kb-zero"]


async def test_embed_unembedded_mode_off_reads_null_rows_only() -> None:
    """Guard, passes before and after: no zero-vector query without the mode."""
    vectorizer = _vectorizer(_client())
    vectorizer._kb_repo.list_unembedded.return_value = [_entry("kb-missing", None)]

    assert await vectorizer.embed_unembedded(TENANT) == 1

    vectorizer._kb_repo.list_zero_vector_candidates.assert_not_awaited()
    assert _patched_ids(vectorizer) == ["kb-missing"]


# ---------------------------------------------------------------------------
# embed_batch: tenant cache invalidation
# ---------------------------------------------------------------------------


async def test_embed_batch_invalidates_tenant_cache_in_mode() -> None:
    """Fails before: configure() does not accept in_process_mode (TypeError)."""
    vectorizer = _vectorizer(_client(), in_process_mode=True)

    count = await vectorizer.embed_batch(TENANT, [_entry("kb-a"), _entry("kb-b", _zero_vector())])

    assert count == 2
    vectorizer._cache.invalidate_tenant.assert_called_once_with(TENANT)


async def test_embed_batch_does_not_invalidate_when_mode_off() -> None:
    """Guard, passes before and after: bulk embedding leaves the cache alone by default."""
    vectorizer = _vectorizer(_client())

    assert await vectorizer.embed_batch(TENANT, [_entry("kb-a"), _entry("kb-b")]) == 2

    vectorizer._cache.invalidate_tenant.assert_not_called()


@pytest.mark.parametrize("case", ["nothing-to-embed", "every-write-fails"])
async def test_embed_batch_does_not_invalidate_when_nothing_embedded(case: str) -> None:
    vectorizer = _vectorizer(_client(), in_process_mode=True)
    if case == "nothing-to-embed":
        entries = [_entry("kb-real", [0.1] * EMBEDDING_DIMENSIONS)]
    else:
        entries = [_entry("kb-a")]
        vectorizer._kb_repo.patch.side_effect = RuntimeError("cosmos write failed")

    assert await vectorizer.embed_batch(TENANT, entries) == 0

    vectorizer._cache.invalidate_tenant.assert_not_called()


# ---------------------------------------------------------------------------
# Repository query
# ---------------------------------------------------------------------------


async def test_list_zero_vector_candidates_query_and_limit() -> None:
    """Fails before: the repository has no list_zero_vector_candidates (AttributeError)."""
    repo = KnowledgeBaseRepository()
    repo.query = AsyncMock(return_value=[{"id": "kb-zero"}])

    assert await repo.list_zero_vector_candidates(TENANT, limit=7) == [{"id": "kb-zero"}]
    kwargs = repo.query.await_args.kwargs
    assert kwargs["tenant_id"] == TENANT
    assert kwargs["query_text"] == (
        "SELECT * FROM c "
        "WHERE c.is_active = true "
        "AND IS_ARRAY(c.embedding) "
        "AND c.embedding[0] = 0 "
        "AND c.embedding[1] = 0 "
        "AND c.embedding[2] = 0 "
        "OFFSET 0 LIMIT @limit"
    )
    assert kwargs["parameters"] == [{"name": "@limit", "value": 7}]

    await repo.list_zero_vector_candidates(TENANT)
    assert repo.query.await_args.kwargs["parameters"] == [{"name": "@limit", "value": 50}]


async def test_list_unembedded_query_unchanged() -> None:
    """Guard, passes before and after: the null-embedding query is byte-identical."""
    repo = KnowledgeBaseRepository()
    repo.query = AsyncMock(return_value=[])

    await repo.list_unembedded(TENANT)

    kwargs = repo.query.await_args.kwargs
    assert kwargs["query_text"] == (
        "SELECT * FROM c "
        "WHERE c.is_active = true "
        "AND (NOT IS_DEFINED(c.embedding) OR c.embedding = null) "
        "OFFSET 0 LIMIT @limit"
    )
    assert kwargs["parameters"] == [{"name": "@limit", "value": 50}]


# ---------------------------------------------------------------------------
# Startup: configure the mode, and the tenant query predicate
# ---------------------------------------------------------------------------


@pytest.fixture
def kb_startup(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, object]:
    """Fake the KB vectorizer, its repository and the client factory."""
    client = object()
    vectorizer = MagicMock(name="kb_vectorizer")
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(lifecycle, "get_knowledge_vectorizer", MagicMock(return_value=vectorizer))
    monkeypatch.setattr("src.multi_tenant.repository.KnowledgeBaseRepository", MagicMock())
    monkeypatch.setattr("src.chat.pipeline._create_openai_client", factory)
    return vectorizer, factory, client


@pytest.mark.parametrize(
    ("flag_on", "use_containers", "client_fails", "expected_mode"),
    [
        (True, True, False, True),
        (True, True, True, False),
        (False, True, False, False),
        (False, False, False, False),
    ],
    ids=["flag-and-client", "flag-without-client", "flag-off", "flag-off-no-containers"],
)
async def test_startup_configures_mode_only_with_flag_and_client(
    monkeypatch: pytest.MonkeyPatch,
    kb_startup: tuple[MagicMock, MagicMock, object],
    flag_on: bool,
    use_containers: bool,
    client_fails: bool,
    expected_mode: bool,
) -> None:
    """Fails before: configure is not passed in_process_mode (KeyError)."""
    vectorizer, factory, _client_sentinel = kb_startup
    if flag_on:
        monkeypatch.setenv(FLAG, "true")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr("src.chat.pipeline.USE_AGENT_CONTAINERS", use_containers)
    if client_fails:
        factory.side_effect = RuntimeError("no Azure OpenAI configuration")

    await lifecycle._startup_knowledge_vectorizer()

    vectorizer.configure.assert_called_once()
    assert vectorizer.configure.call_args.kwargs["in_process_mode"] is expected_mode


class _RecordingContainer:
    """A Cosmos container fake that records each query and yields fixed rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query_items(self, query: str, **kwargs: Any) -> Any:
        self.queries.append(query)
        rows = self.rows

        async def _iterate() -> Any:
            for row in rows:
                yield row

        return _iterate()


@pytest.mark.parametrize(
    ("in_process_mode", "expected_query"),
    [(True, STARTUP_QUERY_IN_PROCESS), (False, STARTUP_QUERY_DEFAULT)],
    ids=["mode-on", "mode-off"],
)
async def test_startup_embed_query_adds_zero_predicate_only_in_mode(
    monkeypatch: pytest.MonkeyPatch,
    in_process_mode: bool,
    expected_query: str,
) -> None:
    """Fails before for mode-on (the predicate is missing); mode-off passes before and after."""
    vectorizer = MagicMock(name="kb_vectorizer")
    vectorizer._configured = True
    vectorizer._in_process_mode = in_process_mode
    vectorizer.embed_unembedded = AsyncMock(return_value=1)
    container = _RecordingContainer([{"tenant_id": "tenant-b"}, {"tenant_id": "tenant-a"}])
    cosmos = MagicMock(name="cosmos")
    cosmos.get_container.return_value = container
    monkeypatch.setattr(lifecycle, "get_knowledge_vectorizer", MagicMock(return_value=vectorizer))
    monkeypatch.setattr("src.multi_tenant.cosmos_client.get_cosmos_manager", MagicMock(return_value=cosmos))

    await lifecycle._startup_embed_unembedded_kb()

    assert container.queries == [expected_query]
    assert [call.args[0] for call in vectorizer.embed_unembedded.await_args_list] == ["tenant-a", "tenant-b"]
