# tests/chat/test_model_config_pipeline.py: F29, the empty-reply guard and the chat API version
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
Pipeline-level model configuration.

- F29: the customer reply path calls the AZURE_RG_MODEL deployment (default still "gpt-4o"),
  also after a failed fine-tuning lookup; a tenant's fine-tuned model still takes precedence.
- An empty RG stream never reaches the Critic or the customer: the turn ends with the existing
  fixed generation_error message.
- AZURE_OPENAI_CHAT_API_VERSION sets the chat client's API version (default 2024-10-21, today's);
  "v1" selects the v1 endpoint, <endpoint>/openai/v1/, also chosen when it is unset and a role has
  a reasoning effort. The KB and memory vectorizer clients keep 2024-10-21.

Default-mode guards (default deployment, default API version, embedding client) pass before and
after the change.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI

from src.chat.models import StreamEvent, StreamEventType
from src.chat.pipeline import constants, orchestrator, singleton
from src.multi_tenant.system_prompt_builder import AgentRole
from tests.helpers.fake_azure_openai import ENDPOINT, TEST_API_KEY, FakeAzureOpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGURED_RG = "gpt-6-sol-staging"
FINE_TUNED = "ft:gpt-4o-mini:agentred:tenant-a:v1"


@pytest.fixture(autouse=True)
def _no_model_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for role in ("RG", "CR", "IC"):
        monkeypatch.delenv(f"AZURE_{role}_REASONING_EFFORT", raising=False)
        monkeypatch.delenv(f"AZURE_{role}_MAX_COMPLETION_TOKENS", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_CHAT_API_VERSION", raising=False)


@pytest.fixture
def session() -> AsyncMock:
    session = AsyncMock()
    session.get_conversation.return_value = MagicMock(turn_count=1, customer_id=None)
    session.add_ai_message.return_value = "msg-001"
    session.set_pii_scrubber = MagicMock()
    return session


@pytest.fixture
def preferences() -> MagicMock:
    prefs = MagicMock()
    prefs.fine_tuning_enabled = False
    prefs.fine_tuning_active_model_id = None
    prefs.fine_tuning_ab_experiment_id = None
    return prefs


@pytest.fixture
def tenant() -> MagicMock:
    from src.multi_tenant.cosmos_schema import TenantTier

    tenant = MagicMock()
    tenant.tier = TenantTier.STARTER
    return tenant


def _pipeline(session: AsyncMock, openai_client: Any = None) -> orchestrator.ChatPipeline:
    prompt_builder = MagicMock()
    prompt_builder.build_all.return_value = {role: "prompt" for role in AgentRole}
    prompt_builder.explain.return_value = {}
    return orchestrator.ChatPipeline(
        session=session,
        prompt_builder=prompt_builder,
        profile_service=AsyncMock(),
        openai_client=openai_client,
    )


async def _run(pipeline: orchestrator.ChatPipeline, tenant: MagicMock, preferences: MagicMock) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in pipeline.execute(
        "t-001", "conv-001", "What is the return window?", tenant=tenant, preferences=preferences
    ):
        events.append(event)
    return events


async def _reply_deployment(pipeline: orchestrator.ChatPipeline, tenant: MagicMock, preferences: MagicMock) -> str:
    """The deployment the turn hands to the response generator."""
    seen: dict[str, str] = {}

    async def fake_stream(*args: Any, **kwargs: Any) -> Any:
        seen["model"] = kwargs["model"]
        raise RuntimeError("stop after model selection")
        yield ""  # pragma: no cover

    with (
        patch.object(
            pipeline,
            "_call_intent_classifier",
            new_callable=AsyncMock,
            return_value={"intent": "return_request", "confidence": 0.9},
        ),
        patch.object(
            pipeline, "_call_knowledge_retrieval", new_callable=AsyncMock, return_value={"context": "", "sources": []}
        ),
        patch.object(pipeline, "_call_response_generator_stream", side_effect=fake_stream),
        patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
    ):
        await _run(pipeline, tenant, preferences)
    return seen["model"]


# ---------------------------------------------------------------------------
# F29: the reply path follows AZURE_RG_MODEL
# ---------------------------------------------------------------------------


class TestReplyDeployment:
    async def test_the_reply_uses_the_configured_rg_deployment(
        self, monkeypatch: pytest.MonkeyPatch, session: AsyncMock, tenant: MagicMock, preferences: MagicMock
    ) -> None:
        monkeypatch.setattr(orchestrator, "AZURE_RG_MODEL", CONFIGURED_RG, raising=False)

        assert await _reply_deployment(_pipeline(session), tenant, preferences) == CONFIGURED_RG

    async def test_the_default_deployment_is_unchanged(
        self, session: AsyncMock, tenant: MagicMock, preferences: MagicMock
    ) -> None:
        model = await _reply_deployment(_pipeline(session), tenant, preferences)

        assert model == constants.AZURE_RG_MODEL
        if "AZURE_RG_MODEL" not in os.environ:
            assert model == "gpt-4o"

    async def test_a_failed_fine_tuning_lookup_falls_back_to_the_configured_deployment(
        self, monkeypatch: pytest.MonkeyPatch, session: AsyncMock, tenant: MagicMock, preferences: MagicMock
    ) -> None:
        monkeypatch.setattr(orchestrator, "AZURE_RG_MODEL", CONFIGURED_RG, raising=False)
        preferences.fine_tuning_enabled = True
        preferences.fine_tuning_active_model_id = FINE_TUNED

        with patch(
            "src.multi_tenant.fine_tuning_pipeline.get_fine_tuning_service",
            side_effect=RuntimeError("fine-tuning store unavailable"),
        ):
            model = await _reply_deployment(_pipeline(session), tenant, preferences)

        assert model == CONFIGURED_RG

    async def test_a_tenant_fine_tuned_model_still_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch, session: AsyncMock, tenant: MagicMock, preferences: MagicMock
    ) -> None:
        monkeypatch.setattr(orchestrator, "AZURE_RG_MODEL", CONFIGURED_RG, raising=False)
        preferences.fine_tuning_enabled = True
        preferences.fine_tuning_active_model_id = FINE_TUNED

        with patch("src.multi_tenant.fine_tuning_pipeline.get_fine_tuning_service", return_value=MagicMock()):
            model = await _reply_deployment(_pipeline(session), tenant, preferences)

        assert model == FINE_TUNED

    def test_the_environment_variable_reaches_the_reply_path(self) -> None:
        """AZURE_RG_MODEL, read at import like the other model variables, is what the orchestrator uses."""
        env = {**os.environ, "AZURE_RG_MODEL": CONFIGURED_RG}
        result = subprocess.run(
            [sys.executable, "-c", "from src.chat.pipeline import orchestrator; print(orchestrator.AZURE_RG_MODEL)"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )

        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.strip().splitlines()[-1] == CONFIGURED_RG


# ---------------------------------------------------------------------------
# An empty RG stream ends the turn with the fixed error message
# ---------------------------------------------------------------------------


class TestEmptyReplyGuard:
    @pytest.mark.parametrize("effort", ["", "low"], ids=["default-mode", "reasoning-mode"])
    async def test_an_empty_stream_reaches_neither_the_critic_nor_the_customer(
        self,
        monkeypatch: pytest.MonkeyPatch,
        session: AsyncMock,
        tenant: MagicMock,
        preferences: MagicMock,
        effort: str,
    ) -> None:
        if effort:
            monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", effort)
        fake = FakeAzureOpenAI(stream_text=[], finish_reason="length")
        pipeline = _pipeline(session, openai_client=fake.client())
        critic = AsyncMock(return_value=(True, "", MagicMock()))

        with (
            patch.object(
                pipeline,
                "_call_intent_classifier",
                new_callable=AsyncMock,
                return_value={"intent": "return_request", "confidence": 0.9},
            ),
            patch.object(
                pipeline,
                "_call_knowledge_retrieval",
                new_callable=AsyncMock,
                return_value={"context": "", "sources": []},
            ),
            patch.object(pipeline, "_load_customer_profile", new_callable=AsyncMock, return_value=None),
            patch.object(pipeline, "_validate_with_critic", critic),
        ):
            events = await _run(pipeline, tenant, preferences)

        assert len(fake.requests) == 1
        critic.assert_not_awaited()
        session.add_ai_message.assert_not_awaited()
        assert not [e for e in events if e.event == StreamEventType.TOKEN]
        errors = [e for e in events if e.event == StreamEventType.ERROR]
        assert len(errors) == 1
        assert errors[0].data["code"] == "generation_error"
        assert errors[0].data["stage"] == "response-generator"
        assert errors[0].data["tokens_sent"] == 0
        assert [e for e in events if e.event == StreamEventType.DONE]


# ---------------------------------------------------------------------------
# Chat API version
# ---------------------------------------------------------------------------


class TestChatApiVersion:
    @pytest.fixture(autouse=True)
    def _azure_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", TEST_API_KEY)
        monkeypatch.setattr(singleton, "_pipeline", None)

    @staticmethod
    def _pipeline_client() -> Any:
        pipeline = singleton.configure_chat_pipeline(
            session=MagicMock(), prompt_builder=MagicMock(), profile_service=MagicMock()
        )
        return pipeline._openai_client

    @staticmethod
    async def _first_request_url(client: Any, deployment: str = "gpt-4o-mini") -> tuple[str, FakeAzureOpenAI]:
        fake = FakeAzureOpenAI()
        routed = client.copy(http_client=fake.http_client(), max_retries=0)
        await routed.chat.completions.create(
            model=deployment, messages=[{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=5
        )
        return str(fake.requests[0].url), fake

    async def test_the_pipeline_client_defaults_to_todays_version(self) -> None:
        client = self._pipeline_client()

        url, _ = await self._first_request_url(client)

        assert isinstance(client, AsyncAzureOpenAI)
        assert url == f"{ENDPOINT}/openai/deployments/gpt-4o-mini/chat/completions?api-version=2024-10-21"

    async def test_the_pipeline_client_uses_a_configured_dated_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", "2025-04-01-preview")
        client = self._pipeline_client()

        url, _ = await self._first_request_url(client)

        assert url == f"{ENDPOINT}/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-04-01-preview"

    @pytest.mark.parametrize("endpoint", [ENDPOINT, ENDPOINT + "/"], ids=["no-slash", "trailing-slash"])
    async def test_v1_selects_the_v1_endpoint(self, monkeypatch: pytest.MonkeyPatch, endpoint: str) -> None:
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
        monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", "v1")
        client = self._pipeline_client()

        url, fake = await self._first_request_url(client, deployment="gpt-6-sol")

        assert isinstance(client, AsyncOpenAI)
        assert not isinstance(client, AsyncAzureOpenAI)
        assert str(client.base_url) == f"{ENDPOINT}/openai/v1/"
        assert url == f"{ENDPOINT}/openai/v1/chat/completions"
        assert fake.params()["model"] == "gpt-6-sol"
        assert fake.requests[0].headers["authorization"] == f"Bearer {TEST_API_KEY}"

    async def test_the_embedding_client_keeps_todays_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """lifecycle builds the KB and memory vectorizer clients with _create_openai_client()."""
        monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", "v1")

        client = singleton._create_openai_client()

        assert isinstance(client, AsyncAzureOpenAI)
        url, _ = await self._first_request_url(client, deployment="text-embedding-3-large")
        assert url.endswith("?api-version=2024-10-21")

    async def test_reasoning_with_the_version_unset_uses_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2024-10-21 predates reasoning_effort, so keeping it would refuse every reasoning call."""
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        client = self._pipeline_client()

        url, _ = await self._first_request_url(client, deployment="gpt-6-sol")

        assert isinstance(client, AsyncOpenAI)
        assert not isinstance(client, AsyncAzureOpenAI)
        assert url == f"{ENDPOINT}/openai/v1/chat/completions"

    def test_a_warning_names_the_setting_for_an_older_explicit_version(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("AZURE_CR_REASONING_EFFORT", "low")
        monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", "2024-10-21")

        with caplog.at_level(logging.WARNING):
            client = self._pipeline_client()

        assert isinstance(client, AsyncAzureOpenAI)
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("AZURE_OPENAI_CHAT_API_VERSION=2024-10-21" in message and "CR" in message for message in warnings)

    @pytest.mark.parametrize("version", ["2025-04-01-preview", "v1", "V1"])
    def test_no_warning_for_a_version_with_reasoning_effort(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, version: str
    ) -> None:
        monkeypatch.setenv("AZURE_RG_REASONING_EFFORT", "low")
        monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", version)

        with caplog.at_level(logging.WARNING):
            self._pipeline_client()

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "predates" in r.getMessage()]

    def test_no_warning_in_default_mode(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            self._pipeline_client()

        assert not [r for r in caplog.records if "AZURE_OPENAI_CHAT_API_VERSION" in r.getMessage()]
