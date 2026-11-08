# tests/unit/test_pipeline_request_state.py: concurrent chat requests keep their own tenant state
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
One ChatPipeline instance serves every chat request. ``execute`` stored the request's
trace id, conversation id, tenant id, tenant document and preferences on ``self`` and
then awaited; the agent dispatch read them back later. A second request arriving during
those awaits overwrote them, so tenant A's knowledge retrieval could run with tenant B's
MCP servers, shop domain and preferences.

These five attributes are now ``RequestScoped``: held per asyncio context, so each
request (Starlette runs each in its own task) sees only its own values.

Architecture review S7 (docs/agent-red-architecture-review.md).
"""

from __future__ import annotations

import asyncio

from src.chat.pipeline.orchestrator import ChatPipeline

FIELDS = (
    "_current_trace_id",
    "_current_conversation_id",
    "_current_tenant_id",
    "_current_tenant",
    "_current_preferences",
)


def _bare_pipeline() -> ChatPipeline:
    """A pipeline without its services; only the per-request attributes are used."""
    return object.__new__(ChatPipeline)


async def test_concurrent_requests_keep_their_own_state():
    pipeline = _bare_pipeline()
    a_stored, b_stored = asyncio.Event(), asyncio.Event()

    async def request_a():
        for field in FIELDS:
            setattr(pipeline, field, f"A{field}")
        a_stored.set()
        await b_stored.wait()  # request B runs here, as during execute's awaits
        return {field: getattr(pipeline, field, None) for field in FIELDS}

    async def request_b():
        await a_stored.wait()
        for field in FIELDS:
            setattr(pipeline, field, f"B{field}")
        b_stored.set()
        return {field: getattr(pipeline, field, None) for field in FIELDS}

    seen_a, seen_b = await asyncio.gather(asyncio.create_task(request_a()), asyncio.create_task(request_b()))
    assert seen_a == {field: f"A{field}" for field in FIELDS}
    assert seen_b == {field: f"B{field}" for field in FIELDS}


async def test_unset_values_fall_back_to_getattr_defaults():
    pipeline = _bare_pipeline()
    assert getattr(pipeline, "_current_tenant_id", "none") == "none"


async def test_tasks_started_by_a_request_see_its_values():
    pipeline = _bare_pipeline()
    pipeline._current_tenant_id = "t-request"

    async def helper():
        return pipeline._current_tenant_id

    assert await asyncio.create_task(helper()) == "t-request"


async def test_two_pipelines_do_not_share_values():
    first, second = _bare_pipeline(), _bare_pipeline()
    first._current_tenant_id = "t-first"
    assert getattr(second, "_current_tenant_id", None) is None
