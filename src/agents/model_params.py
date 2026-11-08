# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Azure OpenAI chat parameters per role, with opt-in reasoning-model support.

Roles and the deployment each one calls:
- "RG": response generator (customer replies, streamed) and co-pilot, AZURE_RG_MODEL
- "CR": Critic/supervisor, AZURE_CR_MODEL
- "IC": intent classifier and escalation handler, AZURE_IC_MODEL

With none of the variables below set, every call sends today's temperature and max_tokens.

Reasoning models (GPT-5.x, GPT-6, o-series) reject temperature and max_tokens on Chat
Completions; they take max_completion_tokens, which covers reasoning and visible tokens, and
reasoning_effort (https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning). The owner
opts a role in explicitly; nothing is inferred from a deployment name, because names are
free-form.

- AZURE_<ROLE>_REASONING_EFFORT: empty (default) means a non-reasoning model. Otherwise a value
  the model accepts on Chat Completions: none, minimal, low, medium, high or xhigh.
- AZURE_<ROLE>_MAX_COMPLETION_TOKENS: the reasoning-mode output cap, default 25,000, the reserve
  Microsoft suggests while learning a workload (same page). Only generated tokens are billed, but
  Azure counts the cap toward the deployment's tokens-per-minute rate limit
  (https://learn.microsoft.com/azure/foundry/openai/how-to/quota).
- AZURE_OPENAI_CHAT_API_VERSION: the chat client's API version. "v1" selects the v1 endpoint,
  <endpoint>/openai/v1/ with no api-version
  (https://learn.microsoft.com/azure/foundry/openai/api-version-lifecycle). Unset, it is today's
  2024-10-21, or v1 when any role has a reasoning effort: 2024-10-21 predates reasoning_effort
  (added in 2024-12-01-preview), so every call for that role would be refused.

The variables are read at call time. JSON-mode roles keep response_format json_object in
reasoning mode; Microsoft documents Structured Outputs for GPT-5.x and GPT-6 but not JSON mode,
so a staging call confirms it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ROLES = ("RG", "CR", "IC")
DEFAULT_MAX_COMPLETION_TOKENS = 25_000
CHAT_API_VERSION_ENV = "AZURE_OPENAI_CHAT_API_VERSION"
DEFAULT_CHAT_API_VERSION = "2024-10-21"
V1_API = "v1"
# reasoning_effort first appears in api-version 2024-12-01-preview (v1 API changelog).
FIRST_REASONING_API_DATE = "2024-12-01"


def reasoning_effort(role: str | None) -> str:
    """The role's configured reasoning effort, or "" for a non-reasoning model."""
    if not role:
        return ""
    return os.environ.get(f"AZURE_{role}_REASONING_EFFORT", "").strip().lower()


def max_completion_tokens(role: str) -> int:
    """The role's reasoning-mode output cap; an unset or unusable value gives the default."""
    name = f"AZURE_{role}_MAX_COMPLETION_TOKENS"
    raw = os.environ.get(name, "").strip()
    if not raw:
        return DEFAULT_MAX_COMPLETION_TOKENS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning("%s=%r is not a positive integer; using %d", name, raw, DEFAULT_MAX_COMPLETION_TOKENS)
        return DEFAULT_MAX_COMPLETION_TOKENS
    return value


def chat_completion_params(role: str | None, *, temperature: float, max_tokens: int) -> dict[str, Any]:
    """Sampling and length parameters for one chat completion call.

    A role with no reasoning effort (or role None) gets today's temperature and max_tokens. A
    reasoning role gets max_completion_tokens and reasoning_effort, and no temperature: Microsoft
    lists temperature as unsupported on reasoning models other than GPT-6 Astra.
    """
    effort = reasoning_effort(role)
    if role is None or not effort:
        return {"temperature": temperature, "max_tokens": max_tokens}
    return {"max_completion_tokens": max_completion_tokens(role), "reasoning_effort": effort}


def build_chat_client(endpoint: str, api_key: str, api_version: str) -> Any:
    """An Azure OpenAI chat client for api_version: a dated version, or "v1" for the v1 endpoint.

    v1 uses the OpenAI client with base_url <endpoint>/openai/v1/, as Microsoft documents it;
    model= stays the deployment name. Raises ImportError when the openai package is missing.
    """
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    if api_version.lower() == V1_API:
        return AsyncOpenAI(base_url=f"{endpoint.rstrip('/')}/openai/v1/", api_key=api_key)
    return AsyncAzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)


def chat_api_version() -> str:
    """The chat client's API version: a dated version, or "v1" for the v1 endpoint.

    Unset, it is today's 2024-10-21 unless a role has a reasoning effort; then it is v1, since
    2024-10-21 would refuse every reasoning call for that role. An explicit dated version older
    than reasoning_effort is used as set, with a warning.
    """
    configured = os.environ.get(CHAT_API_VERSION_ENV, "").strip()
    reasoning_roles = [role for role in ROLES if reasoning_effort(role)]
    if not configured:
        if reasoning_roles:
            logger.info(
                "%s is unset and a reasoning effort is set for %s; using %s",
                CHAT_API_VERSION_ENV,
                ", ".join(reasoning_roles),
                V1_API,
            )
            return V1_API
        return DEFAULT_CHAT_API_VERSION
    if reasoning_roles and configured.lower() != V1_API and configured[:10] < FIRST_REASONING_API_DATE:
        logger.warning(
            "A reasoning effort is set for %s but %s=%s predates reasoning_effort; set %s=%s",
            ", ".join(reasoning_roles),
            CHAT_API_VERSION_ENV,
            configured,
            CHAT_API_VERSION_ENV,
            V1_API,
        )
    return configured
