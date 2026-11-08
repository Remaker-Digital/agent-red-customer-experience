# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""The per-agent container apps build their chat client with the gateway's API version rule.

With no new variable set they keep today's AsyncAzureOpenAI at 2024-10-21. With a reasoning
effort set (and AZURE_OPENAI_CHAT_API_VERSION unset) they use the v1 endpoint, so the reasoning
parameters model_params sends reach an API version that accepts them. An explicit dated version
is used as set.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from openai import AsyncAzureOpenAI, AsyncOpenAI

from tests.helpers.fake_azure_openai import ENDPOINT, TEST_API_KEY, FakeAzureOpenAI

CONTAINER_MODULES = (
    "src.agents.containers.intent_classifier_app",
    "src.agents.containers.critic_supervisor_app",
    "src.agents.containers.escalation_handler_app",
    "src.agents.containers.co_pilot_app",
    "src.agents.containers.response_generator_app",
)
ROLE_VARIABLES = tuple(f"AZURE_{role}_REASONING_EFFORT" for role in ("RG", "CR", "IC"))


@pytest.fixture(autouse=True)
def _azure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", TEST_API_KEY)
    monkeypatch.delenv("AZURE_OPENAI_CHAT_API_VERSION", raising=False)
    for name in ROLE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


async def _configured_client(module_name: str) -> Any:
    agent = MagicMock()
    await importlib.import_module(module_name)._configure(agent)
    agent.configure.assert_called_once()
    return agent.configure.call_args.args[0]


async def _request_url(client: Any) -> str:
    fake = FakeAzureOpenAI()
    routed = client.copy(http_client=fake.http_client(), max_retries=0)
    await routed.chat.completions.create(
        model="deployment", messages=[{"role": "user", "content": "hi"}], temperature=0.0, max_tokens=5
    )
    return str(fake.requests[0].url)


@pytest.mark.parametrize("module_name", CONTAINER_MODULES)
async def test_default_client_is_unchanged(module_name: str) -> None:
    client = await _configured_client(module_name)

    assert isinstance(client, AsyncAzureOpenAI)
    assert await _request_url(client) == (
        f"{ENDPOINT}/openai/deployments/deployment/chat/completions?api-version=2024-10-21"
    )


@pytest.mark.parametrize("module_name", CONTAINER_MODULES)
async def test_a_reasoning_effort_selects_the_v1_endpoint(module_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_CR_REASONING_EFFORT", "low")

    client = await _configured_client(module_name)

    assert isinstance(client, AsyncOpenAI)
    assert not isinstance(client, AsyncAzureOpenAI)
    assert await _request_url(client) == f"{ENDPOINT}/openai/v1/chat/completions"


@pytest.mark.parametrize("module_name", CONTAINER_MODULES)
async def test_an_explicit_dated_version_is_used(module_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_CHAT_API_VERSION", "2025-04-01-preview")

    client = await _configured_client(module_name)

    assert (await _request_url(client)).endswith("?api-version=2025-04-01-preview")


@pytest.mark.parametrize("module_name", CONTAINER_MODULES)
async def test_no_client_without_credentials(module_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    agent = MagicMock()

    await importlib.import_module(module_name)._configure(agent)

    agent.configure.assert_not_called()


def test_the_streaming_route_defaults_to_the_configured_reply_deployment() -> None:
    """The RG container's /generate/stream default follows AZURE_RG_MODEL, like the gateway (F29)."""
    source = (Path(__file__).resolve().parents[2] / "src/agents/containers/response_generator_app.py").read_text(
        encoding="utf-8"
    )
    assert 'body.get("model", AZURE_RG_MODEL)' in source
    assert '"gpt-4o"' not in source
