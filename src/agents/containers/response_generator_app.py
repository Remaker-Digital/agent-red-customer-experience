# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
# Agent Red — Response Generator Container Entry Point
#
# Run: uvicorn src.agents.containers.response_generator_app:app --port 8083
#
# This container has an additional SSE streaming endpoint: /generate/stream
#
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from src.agents.containers.agent_app import create_agent_app
from src.agents.response_generator import AZURE_RG_MODEL, ResponseGeneratorAgent


async def _configure(agent: ResponseGeneratorAgent) -> None:
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


def _add_streaming_route(app: FastAPI, agent: ResponseGeneratorAgent) -> None:
    """Add the SSE streaming /generate/stream endpoint."""

    @app.post("/generate/stream")
    async def generate_stream(request: Request) -> StreamingResponse:
        """Stream AI response tokens via SSE."""
        body = await request.json()

        async def _sse_generator():
            async for chunk in agent.generate_stream(
                customer_message=body.get("message", ""),
                intent=body.get("intent", "general_inquiry"),
                knowledge_context=body.get("knowledge_context", ""),
                system_prompt=body.get("system_prompt", ""),
                model=body.get("model", AZURE_RG_MODEL),
                conversation_history=body.get("conversation_history"),
                timeout_seconds=body.get("timeout_seconds", 15.0),
            ):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
        )


app = create_agent_app(
    ResponseGeneratorAgent,
    configure_fn=_configure,
    extra_routes_fn=_add_streaming_route,
)
