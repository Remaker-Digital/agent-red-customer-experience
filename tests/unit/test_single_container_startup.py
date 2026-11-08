# tests/unit/test_single_container_startup.py: RC-11 single-container startup handlers
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
RC-11 single-container beta mode, PR3 (docs/beta-single-container-design.md, sections 3 and 6).

When SINGLE_CONTAINER_MODE is on, the gateway's startup handlers in src/app/lifecycle.py:

- skip the NATS connection and the AGNTCY SDK transport setup, which the beta does not use;
- give both gateway vectorizers an Azure OpenAI client even though USE_AGENT_CONTAINERS stays
  True, so in-process retrieval embeds real queries instead of zero vectors;
- log the chat mode as "single-container (in-process)".

With the flag off nothing changes: the flag-off cases below pass before and after this PR.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.app.lifecycle as lifecycle

FLAG = "SINGLE_CONTAINER_MODE"


def _set_flag(monkeypatch: pytest.MonkeyPatch, flag_on: bool) -> None:
    if flag_on:
        monkeypatch.setenv(FLAG, "true")
    else:
        monkeypatch.delenv(FLAG, raising=False)


# ---------------------------------------------------------------------------
# NATS and AGNTCY SDK startup
# ---------------------------------------------------------------------------


@pytest.fixture
def transport_startup(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    """Replace the NATS and SDK initialisers so neither touches the network."""
    init_nats = AsyncMock()
    init_sdk = AsyncMock()
    monkeypatch.setattr(lifecycle, "init_nats_manager", init_nats)
    monkeypatch.setattr("src.multi_tenant.agntcy_sdk_integration.init_agntcy_sdk", init_sdk)
    monkeypatch.setattr(
        "src.multi_tenant.agntcy_sdk_integration.get_sdk_status",
        MagicMock(return_value={"sdk_initialized": True}),
    )
    return init_nats, init_sdk


async def test_nats_and_sdk_startup_skipped_when_on(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    transport_startup: tuple[AsyncMock, AsyncMock],
) -> None:
    init_nats, init_sdk = transport_startup
    _set_flag(monkeypatch, True)
    caplog.set_level(logging.INFO, logger=lifecycle.logger.name)

    await lifecycle._startup_nats()
    await lifecycle._startup_agntcy_sdk()

    init_nats.assert_not_called()
    init_sdk.assert_not_awaited()
    skipped = [r for r in caplog.records if r.levelno == logging.INFO and "single-container" in r.getMessage()]
    assert len(skipped) == 2, [r.getMessage() for r in caplog.records]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_nats_and_sdk_startup_run_when_off(
    monkeypatch: pytest.MonkeyPatch,
    transport_startup: tuple[AsyncMock, AsyncMock],
) -> None:
    """Guard: with the flag off both initialisers are awaited exactly as before."""
    init_nats, init_sdk = transport_startup
    _set_flag(monkeypatch, False)

    await lifecycle._startup_nats()
    await lifecycle._startup_agntcy_sdk()

    init_nats.assert_awaited_once()
    init_sdk.assert_awaited_once()


# ---------------------------------------------------------------------------
# Vectorizer OpenAI clients
# ---------------------------------------------------------------------------


@pytest.fixture
def vectorizers(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, object]:
    """Fake both vectorizers and their repositories; the client factory returns a sentinel."""
    client = object()
    kb_vectorizer = MagicMock(name="kb_vectorizer")
    conversation_vectorizer = MagicMock(name="conversation_vectorizer")
    monkeypatch.setattr(lifecycle, "get_knowledge_vectorizer", MagicMock(return_value=kb_vectorizer))
    monkeypatch.setattr(
        "src.multi_tenant.conversation_vectorizer.get_vectorizer",
        MagicMock(return_value=conversation_vectorizer),
    )
    monkeypatch.setattr("src.multi_tenant.repository.KnowledgeBaseRepository", MagicMock())
    monkeypatch.setattr("src.multi_tenant.repository.MemoryVectorRepository", MagicMock())
    monkeypatch.setattr("src.multi_tenant.gdpr_services.PiiScrubber", MagicMock())
    monkeypatch.setattr("src.chat.pipeline._create_openai_client", MagicMock(return_value=client))
    return kb_vectorizer, conversation_vectorizer, client


@pytest.mark.parametrize(
    ("flag_on", "use_containers", "gets_client"),
    [
        (True, True, True),
        (False, True, False),
        (False, False, True),
        (True, False, True),
    ],
    ids=["flag-on", "flag-off", "no-containers", "flag-on-no-containers"],
)
async def test_vectorizers_get_client_when_on(
    monkeypatch: pytest.MonkeyPatch,
    vectorizers: tuple[MagicMock, MagicMock, object],
    flag_on: bool,
    use_containers: bool,
    gets_client: bool,
) -> None:
    kb_vectorizer, conversation_vectorizer, client = vectorizers
    _set_flag(monkeypatch, flag_on)
    monkeypatch.setattr("src.chat.pipeline.USE_AGENT_CONTAINERS", use_containers)

    await lifecycle._startup_knowledge_vectorizer()
    await lifecycle._startup_conversation_vectorizer()

    expected = client if gets_client else None
    kb_vectorizer.configure.assert_called_once()
    assert kb_vectorizer.configure.call_args.kwargs["openai_client"] is expected
    conversation_vectorizer.configure.assert_called_once()
    assert conversation_vectorizer.configure.call_args.kwargs["openai_client"] is expected


# ---------------------------------------------------------------------------
# Chat mode label
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_services(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Fake everything _startup_chat_services wires, so no global singleton changes."""
    configure_services = MagicMock()
    for target in (
        "src.chat.endpoints.configure_chat_services",
        "src.chat.pipeline.configure_chat_pipeline",
        "src.chat.session.configure_conversation_session",
        "src.multi_tenant.customer_profile_service.get_profile_service",
        "src.multi_tenant.system_prompt_builder.get_prompt_builder",
        "src.multi_tenant.repository.ConversationRepository",
        "src.multi_tenant.repository.KnowledgeBaseRepository",
        "src.multi_tenant.repository.TeamMemberRepository",
        "src.multi_tenant.repositories.customer.CustomerProfileRepository",
        "src.chat.sse_manager.get_sse_manager",
        "src.multi_tenant.conversation_meter.get_conversation_meter",
    ):
        monkeypatch.setattr(target, configure_services if target.endswith("configure_chat_services") else MagicMock())
    monkeypatch.setattr("src.chat.pipeline.constants.get_critic_urls", MagicMock(return_value=[]))
    return configure_services


@pytest.mark.parametrize(
    ("flag_on", "use_containers", "label"),
    [
        (True, True, "single-container (in-process)"),
        (False, True, "agent containers"),
        (False, False, "direct Azure OpenAI"),
    ],
    ids=["flag-on", "flag-off", "no-containers"],
)
async def test_mode_label_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    chat_services: MagicMock,
    flag_on: bool,
    use_containers: bool,
    label: str,
) -> None:
    _set_flag(monkeypatch, flag_on)
    monkeypatch.setattr("src.chat.pipeline.USE_AGENT_CONTAINERS", use_containers)
    caplog.set_level(logging.INFO, logger=lifecycle.logger.name)

    before = asyncio.all_tasks()
    try:
        await lifecycle._startup_chat_services()
    finally:
        # The handler schedules an endless SSE buffer cleanup loop; stop it.
        spawned = asyncio.all_tasks() - before
        for task in spawned:
            task.cancel()
        await asyncio.gather(*spawned, return_exceptions=True)

    chat_services.assert_called_once()
    initialized = [r.getMessage() for r in caplog.records if "Chat API services initialized" in r.getMessage()]
    assert initialized == [f"Chat API services initialized (session + pipeline, 6 endpoints, mode={label})"]
