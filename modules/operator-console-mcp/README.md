# Agent Red operator console MCP module

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

This standalone package is both a working read-only stdio MCP server and a template for additive modules. It uses the official MCP Python SDK and the separately installed generated `agent-red-core-client` package. It does not import application source, read its database, or make authorization decisions.

The server exposes exactly three tools:

| Tool | Result |
|---|---|
| `list_tenants` | Directory page with optional status filter, `skip` and `limit` |
| `get_tenant_status` | Operational status for one account, found across directory pages |
| `list_recent_signups` | Accounts created at or after a timestamp with timezone, newest first, with pagination |

All calls use the existing directory GET endpoint. Status and signup reads may scan multiple pages. Signup results report `unavailableCreationTimes` when accounts lack a creation timestamp. Invalid timestamps produce a tool error. Concurrent directory changes follow the core's existing offset-pagination semantics.

The credential must be accepted by the core as a platform administrator. The module forwards it as `X-API-Key`. Tools are read-only; the credential itself may have broader core privileges. Errors retain HTTP status but never return or log the token.

## Build and install

Build the client wheel and module wheel from the checkout, then install them together. The installed module has no dependency on the checkout or on relative source paths.

```sh
python -m build --wheel --outdir wheelhouse contracts/client
python -m build --wheel --outdir wheelhouse modules/operator-console-mcp
python -m pip install --find-links wheelhouse agent-red-core-client==0.1.0 agent-red-operator-console-mcp==0.1.0
```

An MCP client launches `agent-red-operator-console-mcp` with stdio transport. Set `AGENT_RED_CORE_BASE_URL` to the intended core origin and `AGENT_RED_OPERATOR_API_KEY` through that client's environment configuration. There is no default live endpoint or credential. The server exits with a configuration error when required live settings are absent.

For synthetic operation, install `agent-red-core-fakes==0.1.0` from its wheel and select `AGENT_RED_CORE_MODE=mock`. Mock mode uses `https://core.example.invalid`, the obviously fake key `dummy-not-a-key`, and an in-memory transport. It never connects to that hostname. `.env.example` documents settings; the server does not load environment files automatically.

## Disconnected verification

Install the pinned maintainer requirements from `contracts/requirements-tools.txt`, then run:

```sh
python tools/verify_operator_module.py
```

The script first builds wheels and acquires dependencies. It then installs those wheels with `--no-index` in a new container and runs tests with Docker networking set to `none`. The checkout is absent from that container. Socket and DNS guards cover both the test process and the stdio server. Tests initialize an MCP client, list all three tools, call them using synthetic data, and cover pagination, missing accounts, invalid arguments, denied roles, service errors and malformed responses. Docker with a Linux engine is required.

For a new module, copy this package to `modules/<name>`, give it a new package name, console command and `agent_red.modules` entry point, and keep its version, tests and declared API dependency independent. Metadata discovery is inert; execution occurs only when an MCP client starts the console command.

Record the public source commit, package version, contract hash and wheel hash before commercial adoption. Install an exact tested artifact. Core changes and contract changes go through their own release review.
