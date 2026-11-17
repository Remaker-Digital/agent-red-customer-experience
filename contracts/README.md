# Operator API contract and client

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

This reviewed contract covers the existing `GET /api/superadmin/tenants` operation. The HTTP behavior is unchanged from the commercial source recorded in the snapshot provenance. The client operation has a stable generated name, `list_tenants`. Authentication uses `X-API-Key`, with authorization enforced by the core's platform-admin guard.

The three MCP tools are built on this one existing operation. Account status is looked up across directory pages; recent signups are selected by aware creation timestamps and sorted newest first. These compatibility reads can scan the directory and do not provide a transactional snapshot while accounts are changing. No new core endpoint is needed for adoption.

`client/` builds the independently versioned `agent-red-core-client` wheel. `fakes/` builds the test-only `agent-red-core-fakes` wheel. The fake implements the HTTP contract with synthetic accounts, checks success responses against its bundled schema, and creates no sockets. Runtime modules never need a sibling source checkout.

Maintainers regenerate with the pinned tooling:

```sh
python -m pip install -r contracts/requirements-tools.txt
python contracts/export_contract.py --output contracts/openapi.yaml
python contracts/generate_client.py
python contracts/generate_client.py --check
```

Contract generation imports the application routers without starting their services and rejects DNS or socket connections. The client generator is pinned to version 0.28.4 and uses the standard HTTPX 0.28 transport. The contract, generator configuration and generated source are export-owned. Development branches change only `modules/`.

The contract is a reviewed operator view, not a client for every Agent Red endpoint. Tenant administration and visitor APIs require separately reviewed views and role coverage.
