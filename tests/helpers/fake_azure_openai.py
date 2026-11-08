# tests/helpers/fake_azure_openai.py: a recording stand-in for the Azure OpenAI service
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
FakeAzureOpenAI answers chat completion requests the way Azure OpenAI does, through an httpx
MockTransport, so tests drive the real openai SDK and can read the exact HTTP request each call
sends: method, URL (deployment path and api-version) and JSON body.

Usage::

    fake = FakeAzureOpenAI(content='{"intent": "greeting"}')
    agent = IntentClassifierAgent(openai_client=fake.client())
    await agent.process({...}, {})
    assert fake.params() == {"model": ..., "temperature": 0.0, ...}

A streamed request (``"stream": true``) is answered with one server-sent event per item of
``stream_text``, a final event carrying ``finish_reason``, and, when the request asks for it with
``stream_options.include_usage``, a usage event. With ``chars_per_token`` set, the reply is cut at
the request's output cap (``max_tokens`` or ``max_completion_tokens``) times that many characters
and the finish reason becomes "length", as the service does when a reply reaches its cap.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from openai import AsyncAzureOpenAI

ENDPOINT = "https://example.openai.azure.com"
TEST_API_KEY = "test-key"
DEFAULT_API_VERSION = "2024-10-21"


class FakeAzureOpenAI:
    """Records chat completion requests and answers them like Azure OpenAI."""

    def __init__(
        self,
        *,
        content: str = "{}",
        stream_text: list[str] | None = None,
        finish_reason: str = "stop",
        usage: dict[str, Any] | None = None,
        chars_per_token: int | None = None,
    ) -> None:
        self.content = content
        self.stream_text = ["Hello"] if stream_text is None else stream_text
        self.finish_reason = finish_reason
        self.usage = usage or {
            "prompt_tokens": 120,
            "completion_tokens": 48,
            "total_tokens": 168,
            "completion_tokens_details": {"reasoning_tokens": 32},
        }
        self.chars_per_token = chars_per_token
        self.requests: list[httpx.Request] = []

    def client(self, api_version: str = DEFAULT_API_VERSION) -> AsyncAzureOpenAI:
        """An AsyncAzureOpenAI client whose requests reach this fake."""
        return AsyncAzureOpenAI(
            azure_endpoint=ENDPOINT,
            api_key=TEST_API_KEY,
            api_version=api_version,
            max_retries=0,
            http_client=self.http_client(),
        )

    def http_client(self) -> httpx.AsyncClient:
        """An httpx client whose requests reach this fake (for ``client.copy(http_client=...)``)."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def bodies(self) -> list[dict[str, Any]]:
        """The JSON body of every request, in order."""
        return [json.loads(request.content) for request in self.requests]

    def params(self, index: int = -1) -> dict[str, Any]:
        """A request's JSON body without its messages: the model and every parameter."""
        body = dict(self.bodies()[index])
        body.pop("messages", None)
        return body

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            return self._stream_response(body)
        content, finish_reason = self._capped(self.content, body)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    def _capped(self, content: str, body: dict[str, Any]) -> tuple[str, str]:
        if self.chars_per_token is None:
            return content, self.finish_reason
        cap = body.get("max_tokens") or body.get("max_completion_tokens") or 0
        limit = cap * self.chars_per_token
        if len(content) > limit:
            return content[:limit], "length"
        return content, self.finish_reason

    def _stream_response(self, body: dict[str, Any]) -> httpx.Response:
        model = body.get("model", "")

        def chunk(choices: list[dict[str, Any]], **extra: Any) -> str:
            event = {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": choices,
                **extra,
            }
            return f"data: {json.dumps(event)}\n\n"

        events = [chunk([{"index": 0, "delta": {"content": text}, "finish_reason": None}]) for text in self.stream_text]
        events.append(chunk([{"index": 0, "delta": {}, "finish_reason": self.finish_reason}]))
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append(chunk([], usage=self.usage))
        events.append("data: [DONE]\n\n")
        return httpx.Response(200, text="".join(events), headers={"content-type": "text/event-stream"})
