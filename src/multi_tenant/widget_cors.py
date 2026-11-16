# Copyright 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Select public widget CORS separately from trusted browser clients."""

from typing import Any

from starlette.datastructures import Headers, QueryParams
from starlette.middleware.cors import CORSMiddleware, SAFELISTED_HEADERS
from starlette.types import ASGIApp, Receive, Scope, Send

from src.multi_tenant.auth import WIDGET_KEY_HEADER, is_widget_key_allowed_path

_WIDGET_METHODS = ("GET", "HEAD", "POST")
_WIDGET_HEADER = WIDGET_KEY_HEADER.lower()
_WIDGET_HEADERS = frozenset({_WIDGET_HEADER, *(header.lower() for header in SAFELISTED_HEADERS)})
_ACCOUNT_HEADERS = ("authorization", "cookie", "x-api-key", "x-session-token", "x-verification-token")
_ACCOUNT_QUERY_PARAMS = ("api_key", "session_token")


def _is_public_widget_request(scope: Scope, headers: Headers) -> bool:
    """Identify public assets and requests that can only use widget authentication."""
    method = scope["method"]
    preflight = method == "OPTIONS" and "access-control-request-method" in headers
    if preflight:
        method = headers["access-control-request-method"]
    if method not in _WIDGET_METHODS:
        return False

    path = scope["path"]
    asset = method in ("GET", "HEAD") and (path == "/widget.js" or path.startswith("/widget/"))
    if not asset and not is_widget_key_allowed_path(path, method):
        return False

    query = QueryParams(scope.get("query_string", b""))
    if any(name in headers for name in _ACCOUNT_HEADERS) or any(name in query for name in _ACCOUNT_QUERY_PARAMS):
        return False

    if preflight:
        requested = {
            header.strip().lower()
            for header in headers.get("access-control-request-headers", "").split(",")
        }
        requested.discard("")
        return requested <= _WIDGET_HEADERS and (asset or _WIDGET_HEADER in requested)

    # EventSource sends its publishable key in the stream URL.
    stream = method == "GET" and path.startswith("/api/chat/stream/")
    return asset or bool(headers.get(_WIDGET_HEADER)) or (stream and bool(query.get("widget_key") or query.get("key")))


class WidgetCORSMiddleware(CORSMiddleware):
    """Allow public widget traffic from any website using the widget credential."""

    def __init__(self, app: ASGIApp, **account_policy: Any) -> None:
        super().__init__(app, **account_policy)
        self.public_widget = CORSMiddleware(
            app,
            allow_origins=["*"],
            allow_methods=_WIDGET_METHODS,
            allow_headers=[WIDGET_KEY_HEADER],
            expose_headers=account_policy.get("expose_headers", ()),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "origin" in headers and _is_public_widget_request(scope, headers):
                await self.public_widget(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
