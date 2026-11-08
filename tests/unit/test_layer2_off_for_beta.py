# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""B6. Layer 2 search and conversation vectorization stay off for the beta."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.chat.pipeline.orchestrator import ChatPipeline
from src.multi_tenant.cosmos_schema import ConsentStatus, TenantTier

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_granted_consent_does_not_search_or_embed_the_message() -> None:
    profile = MagicMock()
    profile.consent_status = ConsentStatus.GRANTED
    profile.name = "Ada Lovelace"
    profile.email = "ada@example.com"
    profile.is_stale = False

    vectorizer = MagicMock()
    vectorizer.search_history = AsyncMock(return_value=[])
    vectorizer._embed_single = AsyncMock(return_value=[0.0])

    profile_service = MagicMock()
    profile_service.get_profile = AsyncMock(return_value=profile)

    pipeline = ChatPipeline(
        session=MagicMock(),
        prompt_builder=MagicMock(),
        profile_service=profile_service,
        vectorizer=vectorizer,
    )

    await pipeline._load_customer_profile(
        "tenant-b6",
        "customer-b6",
        TenantTier.PROFESSIONAL,
        "Where is my order for Ada Lovelace?",
        MagicMock(),
    )

    vectorizer.search_history.assert_not_called()
    vectorizer._embed_single.assert_not_called()


@pytest.mark.asyncio
async def test_scanner_does_not_embed_a_completed_conversation() -> None:
    conversation = {
        "id": "conv-b6",
        "conversation_id": "conv-b6",
        "tenant_id": "tenant-b6",
        "customer_id": "customer-b6",
        "messages": [{"role": "user", "content": "My name is Ada Lovelace"}],
    }
    vectorizer = MagicMock()
    vectorizer._ensure_configured = MagicMock()
    vectorizer.vectorize_conversation = AsyncMock(return_value=["chunk"])
    vectorizer._embed_single = AsyncMock(return_value=[0.0])
    tenant_repo = MagicMock()
    tenant_repo.list_active_tenant_ids = AsyncMock(return_value=["tenant-b6"])
    conv_repo = MagicMock()
    conv_repo.list_unvectorized_ended = AsyncMock(return_value=[conversation])
    conv_repo.patch = AsyncMock()

    from src.app.background import _vectorization_scanner_loop

    calls = 0

    async def fake_sleep(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError()

    with (
        patch("src.multi_tenant.conversation_vectorizer.get_vectorizer", return_value=vectorizer),
        patch("src.multi_tenant.repository.TenantRepository", return_value=tenant_repo),
        patch("src.multi_tenant.repositories.conversation.ConversationRepository", return_value=conv_repo),
        patch("src.app.background.asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _vectorization_scanner_loop()

    vectorizer.vectorize_conversation.assert_not_called()
    vectorizer._embed_single.assert_not_called()
    conv_repo.patch.assert_not_called()


def test_the_eleven_background_loops_stay_registered() -> None:
    source = (REPO_ROOT / "src" / "main.py").read_text(encoding="utf-8")
    names = (
        "register_idle_scanner()",
        "register_sla_snapshots()",
        "register_alert_evaluation()",
        "register_ingestion_processor()",
        "register_archival_sweep()",
        "register_trial_scanner()",
        "register_trial_warning()",
        "register_expiry_scanner()",
        "register_expiry_warning()",
        "register_vectorization_scanner()",
        "register_website_refresh()",
    )
    assert [line.strip() for line in source.splitlines() if line.strip() in names] == list(names)
