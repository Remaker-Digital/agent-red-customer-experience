# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Exercise the installed wheels and the actual stdio protocol without networking."""

from contextlib import asynccontextmanager
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from pytest_socket import SocketBlockedError

from agent_red_core_client import AuthenticatedClient
from agent_red_core_client.api.superadmin import list_tenants
from agent_red_core_fakes import BASE_URL, DUMMY_OPERATOR_KEY, make_transport
from agent_red_operator_console_mcp import module_metadata
from agent_red_operator_console_mcp.server import Settings

TOOLS = {"list_tenants", "get_tenant_status", "list_recent_signups"}


def subprocess_environment(scenario="normal", key=DUMMY_OPERATOR_KEY):
    return {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(Path(__file__).parent),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AGENT_RED_TEST_BLOCK_NETWORK": "1",
        "AGENT_RED_CORE_MODE": "mock",
        "AGENT_RED_OPERATOR_API_KEY": key,
        "AGENT_RED_MOCK_SCENARIO": scenario,
    }


@asynccontextmanager
async def running_session(scenario="normal", key=DUMMY_OPERATOR_KEY):
    command = shutil.which("agent-red-operator-console-mcp")
    assert command, "The wheel must install its console command"
    params = StdioServerParameters(command=command, env=subprocess_environment(scenario, key))
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        errors.seek(0)
        assert key not in errors.read(), "Credentials must not appear in server diagnostics"


def payload(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


def test_packages_are_installed_without_the_application():
    for name in ("agent-red-core-client", "agent-red-core-fakes", "agent-red-operator-console-mcp"):
        dist = importlib.metadata.distribution(name)
        assert dist.version == "0.1.0"
        assert "site-packages" in str(dist.locate_file(""))
    if os.environ.get("AGENT_RED_VERIFY_INDEPENDENT") == "1":
        assert importlib.util.find_spec("src") is None
        assert not (Path.cwd() / "src").exists()
        assert not (Path.cwd() / "contracts").exists()
    assert set(module_metadata()["tools"]) == TOOLS
    point = next(
        ep for ep in importlib.metadata.entry_points(group="agent_red.modules") if ep.name == "operator-console-mcp"
    )
    assert point.load()() == module_metadata()


def test_parent_and_child_block_internet_and_dns():
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET)
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.com", 443)
    code = "import socket; socket.getaddrinfo('example.com', 443)"
    result = subprocess.run([sys.executable, "-c", code], env=subprocess_environment(), capture_output=True, text=True)
    assert result.returncode != 0
    assert "DNS is blocked" in result.stderr
    result = subprocess.run(
        [sys.executable, "-c", "import socket; socket.socket(socket.AF_INET)"],
        env=subprocess_environment(),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "INET sockets are blocked" in result.stderr


def test_live_configuration_requires_explicit_credentials(monkeypatch):
    for name in ("AGENT_RED_CORE_MODE", "AGENT_RED_CORE_BASE_URL", "AGENT_RED_OPERATOR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="explicitly select mock"):
        Settings.from_environment()


@pytest.mark.asyncio
async def test_stdio_lists_and_calls_all_three_tools():
    async with running_session() as session:
        tools = (await session.list_tools()).tools
        assert {tool.name for tool in tools} == TOOLS
        assert all(tool.annotations.readOnlyHint for tool in tools)
        page = payload(await session.call_tool("list_tenants", {"limit": 1, "skip": 1}))
        assert page["total"] == 3 and page["skip"] == 1 and page["limit"] == 1
        assert [row["tenantId"] for row in page["tenants"]] == ["synthetic-002"]
        state = payload(await session.call_tool("get_tenant_status", {"tenant_id": "synthetic-002"}))
        assert state["status"] == "trial"
        assert "displayName" not in state
        recent = payload(await session.call_tool("list_recent_signups", {"since": "2026-11-15T12:00:00Z"}))
        assert recent["total"] == 2
        assert [row["tenantId"] for row in recent["tenants"]] == ["synthetic-003", "synthetic-002"]


@pytest.mark.asyncio
async def test_mcp_argument_validation_and_not_found():
    async with running_session() as session:
        for tool, args in (
            ("list_tenants", {"limit": 0}),
            ("list_tenants", {"skip": -1}),
            ("list_recent_signups", {"since": "2026-11-15T12:00:00"}),
        ):
            assert (await session.call_tool(tool, args)).isError
        result = await session.call_tool("get_tenant_status", {"tenant_id": "synthetic-missing"})
        assert result.isError and "not found" in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,key,status",
    [
        ("normal", "dummy-denied", "401"),
        ("forbidden", DUMMY_OPERATOR_KEY, "403"),
        ("unavailable", DUMMY_OPERATOR_KEY, "503"),
    ],
)
async def test_core_errors_remain_tool_errors_without_tokens(scenario, key, status):
    async with running_session(scenario, key) as session:
        result = await session.call_tool("list_tenants", {})
        assert result.isError and status in result.content[0].text
        assert key not in result.content[0].text


@pytest.mark.asyncio
async def test_malformed_response_is_reported():
    async with running_session("malformed") as session:
        result = await session.call_tool("list_tenants", {})
        assert result.isError and "invalid response" in result.content[0].text


@pytest.mark.asyncio
async def test_directory_compatibility_reads_across_multiple_pages():
    async with running_session("large") as session:
        state = payload(await session.call_tool("get_tenant_status", {"tenant_id": "synthetic-paged-0502"}))
        assert state["status"] == "active"
        page = payload(
            await session.call_tool("list_recent_signups", {"since": "2025-12-31T00:00:00Z", "skip": 500, "limit": 5})
        )
        assert page["total"] == 503 and len(page["tenants"]) == 3


@pytest.mark.asyncio
async def test_accounts_without_a_creation_time_are_reported():
    async with running_session("undated") as session:
        page = payload(await session.call_tool("list_recent_signups", {"since": "2026-11-01T00:00:00Z"}))
        assert page["total"] == 2 and page["unavailableCreationTimes"] == 1


@pytest.mark.asyncio
async def test_generated_client_uses_http_contract_and_filters():
    client = AuthenticatedClient(base_url=BASE_URL, token=DUMMY_OPERATOR_KEY, auth_header_name="X-API-Key", prefix="")
    client.set_async_httpx_client(
        httpx.AsyncClient(base_url=BASE_URL, headers={"X-API-Key": DUMMY_OPERATOR_KEY}, transport=make_transport())
    )
    async with client:
        page = await list_tenants.asyncio(client=client, status="active", limit=1)
        assert page.total == 1 and page.tenants[0].tenant_id == "synthetic-003"
        page = await list_tenants.asyncio(client=client, skip=2, limit=1)
        assert page.total == 3 and page.tenants[0].tenant_id == "synthetic-001"
