# Agent Red — Critic/Supervisor Container Entry Point
#
# Run: uvicorn src.agents.containers.critic_supervisor_app:app --port 8086
#
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

from __future__ import annotations

import os

from src.agents.containers.agent_app import create_agent_app
from src.agents.critic_supervisor import CriticSupervisorAgent


async def _configure(agent: CriticSupervisorAgent) -> None:
    """Inject Azure OpenAI client."""
    try:
        from src.agents.model_params import build_chat_client, chat_api_version

        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        if endpoint and api_key:
            # Same API version rule as the gateway (AZURE_OPENAI_CHAT_API_VERSION; v1 when a
            # reasoning effort is set), so reasoning parameters reach a version that takes them.
            agent.configure(build_chat_client(endpoint, api_key, chat_api_version()))
    except ImportError:
        pass


app = create_agent_app(CriticSupervisorAgent, configure_fn=_configure)
