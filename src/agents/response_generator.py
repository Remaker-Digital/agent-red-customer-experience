# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
# Agent Red Customer Experience — Response Generator Agent
#
# Generates AI customer service responses using Azure OpenAI with SSE
# streaming. Receives classified intent, knowledge context, and
# conversation history; produces a natural language response.
#
# Extracted from pipeline.py _call_response_generator_stream_direct().
#
# Input payload:
#   {"message": str, "intent": str, "knowledge_context": str,
#    "system_prompt": str, "model": str, "conversation_history": list,
#    "timeout_seconds": float}
#
# Output payload (non-streaming):
#   {"response": str, "model": str, "tokens_input": int, "tokens_output": int}
#
# Streaming: When invoked via the container's /generate/stream endpoint,
# the FastAPI app yields SSE chunks. The A2A handle_message() path returns
# the full assembled response (non-streaming) for protocol compatibility.
#
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from src.agents.base import AgentRedBaseAgent
from src.agents.model_params import chat_completion_params

logger = logging.getLogger(__name__)

AZURE_RG_MODEL = os.environ.get("AZURE_RG_MODEL", "gpt-4o")


class EmptyReplyError(RuntimeError):
    """The model's stream ended without reply text (for example, reasoning used the whole cap).

    The orchestrator's response-generator error handler sends the customer its fixed
    generation_error message, so an empty reply reaches neither the Critic nor the customer.
    """


class ReplyDeadlineError(TimeoutError):
    """A reasoning reply stream ran past its wall-clock deadline.

    The SDK's timeout bounds each read, not the whole stream, so chunks that keep arriving
    under a large max_completion_tokens could hold the request past the pipeline deadline.
    The orchestrator's mid-stream error handler reports it with the tokens already sent.
    """


_DEADLINE_MESSAGE = "Response stream ran past the pipeline deadline"


async def _within_deadline(stream: Any, deadline: float) -> AsyncGenerator[Any, None]:
    """Yield the stream's chunks until the event-loop time ``deadline``, then stop it and raise.

    The deadline is checked around each read rather than with asyncio.timeout, which must not
    span the yields of an async generator. The stream is closed on every exit.
    """
    loop = asyncio.get_running_loop()
    iterator = stream.__aiter__()
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ReplyDeadlineError(_DEADLINE_MESSAGE)
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                raise ReplyDeadlineError(_DEADLINE_MESSAGE) from exc
            yield chunk
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            await close()


class _ReplyStream:
    """Turns chat completion chunks into reply text and checks the reply when the stream ends.

    Leading whitespace is held until visible text arrives, so a whitespace-only stream sends the
    customer nothing before EmptyReplyError; the text that is sent is unchanged.
    """

    def __init__(self, model: str, *, log_usage: bool) -> None:
        self._model = model
        self._log_usage = log_usage
        self._pending = ""
        self.has_text = False
        self.finish_reason: str | None = None

    def text(self, chunk: Any) -> str:
        """The reply text to send for one chunk, or "" for none yet."""
        content = ""
        if chunk.choices:
            choice = chunk.choices[0]
            reason = getattr(choice, "finish_reason", None)
            if isinstance(reason, str):
                self.finish_reason = reason
            content = choice.delta.content or ""
        self._record_usage(chunk)
        if self.has_text or not content:
            return content
        if not content.strip():
            self._pending += content
            return ""
        self.has_text = True
        text, self._pending = self._pending + content, ""
        return text

    def _record_usage(self, chunk: Any) -> None:
        usage = getattr(chunk, "usage", None)
        if not self._log_usage or usage is None:
            return
        details = getattr(usage, "completion_tokens_details", None)
        logger.info(
            "RG usage: model=%s prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s finish_reason=%s",
            self._model,
            usage.prompt_tokens,
            usage.completion_tokens,
            getattr(details, "reasoning_tokens", None),
            self.finish_reason,
        )

    def finish(self) -> None:
        """Raise EmptyReplyError when no visible text arrived; note a reply cut at its cap."""
        if not self.has_text:
            logger.warning(
                "RG stream ended without reply text: model=%s finish_reason=%s", self._model, self.finish_reason
            )
            raise EmptyReplyError(f"Response stream ended without reply text (finish_reason={self.finish_reason})")
        if self.finish_reason == "length":
            logger.warning("RG reply reached its output cap: model=%s", self._model)


class ResponseGeneratorAgent(AgentRedBaseAgent):
    """Generate AI customer service responses.

    Uses Azure OpenAI (GPT-4o by default, or fine-tuned model for
    Enterprise tenants) with multi-turn conversation history and
    injected knowledge context.
    """

    agent_type = "response-generator"

    def __init__(self, openai_client: Any = None) -> None:
        super().__init__()
        self._openai_client = openai_client

    def configure(self, openai_client: Any) -> None:
        """Inject the Azure OpenAI client after construction."""
        self._openai_client = openai_client
        self._configured = True

    async def process(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Generate a response (non-streaming, for A2A protocol).

        For SSE streaming, use generate_stream() directly from the
        container's FastAPI endpoint.

        Args:
            payload: {"message": str, "intent": str, "knowledge_context": str,
                      "system_prompt": str, "model": str,
                      "conversation_history": list, "timeout_seconds": float}
            headers: A2A headers.

        Returns:
            {"response": str, "model": str, "tokens_input": int, "tokens_output": int}
        """
        message = payload.get("message", "")
        intent = payload.get("intent", "general_inquiry")
        knowledge_context = payload.get("knowledge_context", "")
        system_prompt = payload.get("system_prompt", "")
        model = payload.get("model", AZURE_RG_MODEL)
        conversation_history = payload.get("conversation_history") or []
        timeout_seconds = payload.get("timeout_seconds", 15.0)

        # Assemble full response from streaming chunks
        full_response = ""
        async for chunk in self.generate_stream(
            customer_message=message,
            intent=intent,
            knowledge_context=knowledge_context,
            system_prompt=system_prompt,
            model=model,
            conversation_history=conversation_history,
            timeout_seconds=timeout_seconds,
        ):
            full_response += chunk

        return {
            "response": full_response,
            "model": model,
            "tokens_input": 0,  # Token counts available only from streaming response
            "tokens_output": 0,
        }

    async def generate_stream(
        self,
        *,
        customer_message: str,
        intent: str,
        knowledge_context: str,
        system_prompt: str,
        model: str = "gpt-4o",
        conversation_history: list[dict[str, str]] | None = None,
        timeout_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """Stream response tokens from Azure OpenAI.

        This is the primary generation method, used by both the A2A
        handle_message() path (assembled into full response) and the
        container's SSE /generate/stream endpoint (yielded as chunks).

        Args:
            customer_message: The customer's message text.
            intent: Classified intent (from IntentClassifierAgent).
            knowledge_context: Retrieved KB context (from KnowledgeRetrievalAgent).
            system_prompt: Assembled system prompt (from SystemPromptBuilder).
            model: Azure OpenAI model deployment name.
            conversation_history: Prior turns as [{role, content}] dicts.
            timeout_seconds: Maximum time for the streaming call.

        Yields:
            Response text chunks.
        """
        if not self._openai_client:
            logger.warning(
                "ResponseGenerator: no OpenAI client — yielding fallback"
            )
            yield (
                "I'm sorry, but I'm unable to generate a response right now. "
                "Please try again shortly."
            )
            return

        # Build effective system prompt with knowledge context
        effective_system = system_prompt
        if knowledge_context and intent != "greeting":
            effective_system = (
                f"{system_prompt}\n\n"
                "═══════════════════════════════════════════\n"
                "VERIFIED KNOWLEDGE BASE — USE THIS DATA\n"
                "═══════════════════════════════════════════\n"
                "The articles below contain VERIFIED, ACCURATE information.\n"
                "You MUST incorporate specific details from them into your "
                "response: exact dollar amounts, tier names, feature lists, "
                "quantities, percentages, and policy terms.\n"
                "Include ALL relevant items — if there are multiple tiers, "
                "list ALL of them.  If there are multiple features, list ALL.\n"
                "NEVER say \"check our website\" or \"contact sales\" when the "
                "answer is right here.\n"
                "When a knowledge article includes a 'Source:' URL, include a "
                "clickable link in your response using markdown format: "
                "[descriptive text](url). Place links naturally in context, "
                "e.g. 'You can find our [return policy](https://...) here.' "
                "Do NOT list raw URLs.\n\n"
                f"{knowledge_context}\n\n"
                "═══════════════════════════════════════════\n"
                "END OF KNOWLEDGE BASE\n"
                "═══════════════════════════════════════════"
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": effective_system},
        ]

        # Include multi-turn conversation history
        if conversation_history:
            messages.extend(conversation_history)

        # Build current user message
        if intent == "greeting":
            user_content = (
                f"{customer_message}\n\n"
                "[Respond warmly and naturally to this greeting. "
                "Be friendly and conversational. Ask how you can help.]"
            )
        elif intent == "clarification_needed":
            # B2: IC confidence was below the tenant's threshold.
            # Ask a focused follow-up question to disambiguate intent.
            user_content = (
                f"{customer_message}\n\n"
                "[The customer's intent was unclear. DO NOT attempt to answer "
                "the question directly. Instead, ask ONE concise, friendly "
                "clarifying question to understand what they need. Keep the "
                "follow-up specific — reference their words and offer 2-3 "
                "concrete options when possible. Stay in the brand voice.]"
            )
        else:
            user_content = customer_message

        messages.append({"role": "user", "content": user_content})

        # Use lower temperature for knowledge-grounded queries
        temperature = 0.3 if knowledge_context else 0.7

        # Reasoning parameters go only to the configured RG deployment; a tenant's
        # fine-tuned deployment (a gpt-4o-mini) keeps today's parameters.
        params = chat_completion_params(
            "RG" if model == AZURE_RG_MODEL else None, temperature=temperature, max_tokens=1024,
        )
        reasoning = "reasoning_effort" in params
        if reasoning:
            params["stream_options"] = {"include_usage": True}

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        stream = await self._openai_client.chat.completions.create(
            model=model,
            messages=messages,
            **params,
            stream=True,
            timeout=timeout_seconds,
        )

        # A reasoning reply's cap (default 25,000 tokens) could outlast the pipeline deadline,
        # so its stream is bounded in wall-clock time; a non-reasoning reply keeps today's
        # behavior, bounded by max_tokens.
        chunks = _within_deadline(stream, deadline) if reasoning else stream
        reply = _ReplyStream(model, log_usage=reasoning)
        async for chunk in chunks:
            text = reply.text(chunk)
            if text:
                yield text
        reply.finish()
