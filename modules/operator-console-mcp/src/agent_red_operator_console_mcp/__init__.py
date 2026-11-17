# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Read-only MCP adapter, independent from the Agent Red application checkout."""


def module_metadata() -> dict:
    """Discovery only: no service startup, credentials, or network calls."""
    return {
        "name": "operator-console-mcp",
        "version": "0.1.0",
        "contract_version": "0.1.0",
        "transport": "stdio",
        "required_role": "platform_admin",
        "tools": ["list_tenants", "get_tenant_status", "list_recent_signups"],
    }
