# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Three read-only tools; all requests use the separately installed API client."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import os
from typing import Annotated

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, Field

from agent_red_core_client import AuthenticatedClient
from agent_red_core_client.api.superadmin import list_tenants as tenants_api
from agent_red_core_client.models.tenant_directory_response import TenantDirectoryResponse


@dataclass(frozen=True, repr=False)
class Settings:
    base_url: str
    operator_key: str
    mode: str = "live"
    scenario: str = "normal"

    @classmethod
    def from_environment(cls) -> Settings:
        mode = os.environ.get("AGENT_RED_CORE_MODE", "live")
        if mode not in {"live", "mock"}:
            raise ValueError("AGENT_RED_CORE_MODE must be live or mock")
        if mode == "mock":
            return cls(
                "https://core.example.invalid",
                os.environ.get("AGENT_RED_OPERATOR_API_KEY", "dummy-not-a-key"),
                mode,
                os.environ.get("AGENT_RED_MOCK_SCENARIO", "normal"),
            )
        base = os.environ.get("AGENT_RED_CORE_BASE_URL", "").rstrip("/")
        key = os.environ.get("AGENT_RED_OPERATOR_API_KEY", "")
        if not base or not key:
            raise ValueError(
                "Set AGENT_RED_CORE_BASE_URL and AGENT_RED_OPERATOR_API_KEY, or explicitly select mock mode"
            )
        url = httpx.URL(base)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError("The core base URL must be an HTTP origin without credentials, query or fragment")
        return cls(base, key)


def make_client(settings: Settings) -> AuthenticatedClient:
    client = AuthenticatedClient(
        base_url=settings.base_url,
        token=settings.operator_key,
        prefix="",
        auth_header_name="X-API-Key",
        timeout=httpx.Timeout(15.0),
        httpx_args={"trust_env": False},
    )
    if settings.mode == "mock":
        from agent_red_core_fakes import make_transport

        client.set_async_httpx_client(
            httpx.AsyncClient(
                base_url=settings.base_url,
                headers={"X-API-Key": settings.operator_key},
                transport=make_transport(scenario=settings.scenario),
                trust_env=False,
            )
        )
    return client


def create_server(settings: Settings) -> FastMCP:
    @asynccontextmanager
    async def lifespan(server):
        async with make_client(settings) as client:
            yield client

    server = FastMCP("Agent Red operator console", lifespan=lifespan, log_level="WARNING")
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    async def invoke(ctx: Context, endpoint, expected, **kwargs):
        try:
            response = await endpoint.asyncio_detailed(client=ctx.request_context.lifespan_context, **kwargs)
            if response.status_code != 200:
                raise ValueError(f"Core API returned HTTP {int(response.status_code)}")
            if not isinstance(response.parsed, expected):
                raise ValueError("Core API returned an invalid response")
            return response.parsed.to_dict()
        except httpx.HTTPError:
            raise ValueError("Core API request failed") from None
        except (KeyError, TypeError):
            raise ValueError("Core API returned an invalid response") from None

    async def directory_rows(ctx):
        """Compatibility with the unchanged paginated commercial directory API."""
        skip = 0
        seen = set()
        while True:
            page = await invoke(ctx, tenants_api, TenantDirectoryResponse, skip=skip, limit=500)
            for row in page["tenants"]:
                if row["tenantId"] not in seen:
                    seen.add(row["tenantId"])
                    yield row
            skip += 500
            if skip >= page["total"]:
                break

    @server.tool(annotations=read_only)
    async def list_tenants(
        ctx: Context,
        skip: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        status: str | None = None,
    ) -> dict:
        """List accounts using core pagination and an optional status filter."""
        return await invoke(ctx, tenants_api, TenantDirectoryResponse, skip=skip, limit=limit, status=status)

    @server.tool(annotations=read_only)
    async def get_tenant_status(ctx: Context, tenant_id: str) -> dict:
        """Read operational state for one account, subject to core authorization."""
        async for row in directory_rows(ctx):
            if row["tenantId"] == tenant_id:
                return {
                    name: row[name]
                    for name in ("tenantId", "status", "tier", "createdAt", "updatedAt", "expiresAt")
                    if name in row
                }
        raise ValueError("Account not found in the core directory")

    @server.tool(annotations=read_only)
    async def list_recent_signups(
        ctx: Context,
        since: AwareDatetime,
        skip: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict:
        """List accounts created at or after an aware timestamp, newest first."""
        matches = []
        unavailable = 0
        async for row in directory_rows(ctx):
            if not row.get("createdAt"):
                unavailable += 1
                continue
            try:
                created = datetime.fromisoformat(row["createdAt"].replace("Z", "+00:00"))
                if created.tzinfo is None:
                    raise ValueError("Timezone missing")
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Core directory returned an invalid creation timestamp") from None
            if created >= since:
                matches.append((created, row))
        matches.sort(key=lambda item: (item[0], item[1]["tenantId"]), reverse=True)
        return {
            "tenants": [row for _, row in matches[skip : skip + limit]],
            "total": len(matches),
            "skip": skip,
            "limit": limit,
            "unavailableCreationTimes": unavailable,
        }

    return server


def main() -> None:
    create_server(Settings.from_environment()).run(transport="stdio")


if __name__ == "__main__":
    main()
