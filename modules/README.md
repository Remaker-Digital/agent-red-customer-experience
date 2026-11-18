# Module developer README

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

Read this file before starting module work. It defines the repository relationship, supported boundaries and delivery workflow. Read [PUBLIC-DEVELOPMENT.md](../PUBLIC-DEVELOPMENT.md) for provenance and owner-relayed prompt templates.

## How the repositories relate

This public repository is a standalone development workspace. It contains a complete cleansed core baseline, reviewed API contracts, generated clients, synthetic services and independently packaged modules. The commercial application lives in a separate repository with its own Git history and release process.

The commercial release path is the home of record for core implementation and authorization. This repository is the home of record for exploratory modules. A module can be built and tested here without a private checkout, private deployment files, customer data or commercial service credentials. Declared public development dependencies still need to be installed.

Modules integrate through supported, versioned interfaces. Commercial internals can evolve independently while those interfaces remain compatible. Full baseline refreshes can happen separately from module development. A module session does not require copying each commercial core change into this repository.

The commercial project adopts an exact module package, source commit and artifact hash, then verifies compatibility. The repositories are not merged wholesale. The module version, generated client version and core baseline version are separate identities.

The core source commit recorded in [.snapshot-provenance.json](../.snapshot-provenance.json) describes the exported baseline. It is not the source pin for every later module. Each module handoff records its own full public commit and package hashes.

## Ownership and edit boundaries

| Area | Ownership during an ordinary module task |
|---|---|
| `modules/<assigned-module>/` | Module source, package metadata, tests, fixtures and design notes |
| `contracts/` | Maintainer-owned reviewed schemas, generated clients and shared synthetic API packages |
| `src/`, `admin/`, `widget/`, `test_host/` | Reference baseline maintained through the commercial release and export workflow |
| Root dependency files, CI, provenance and repository-wide guides | Maintainer-owned project infrastructure |
| Other module folders | Follow the current task's scope and declared package dependencies |

Start from the public commit supplied in the owner task, or record the current public main when starting an unpinned task. Use a separate `hfi/<module-slug>/<topic>` branch. Keep ordinary work inside the assigned module folder. The current CI path guard enforces the broader `modules/` boundary; the owner task supplies the narrower assignment.

A missing core capability is an interface requirement. Record it in `modules/<module-slug>/design/contract-request.md`, including request and response shapes, required role, error behavior, synthetic scenarios and compatibility expectations. A module-local provisional stub can support design and testing, but label that integration as pending until a real supported contract is available. Continue useful work inside the module boundary.

Changes to a shared contract, baseline or project-wide infrastructure are separate maintainer tasks. Apply such changes only within the scope of the current owner instruction. Do not widen a module task by editing core source, changing its authorization behavior or weakening checks.

## What is available now

The [operator API contract](../contracts/README.md) currently covers the existing tenant-directory read. The [operator console template](operator-console-mcp/README.md) uses it to expose `list_tenants`, `get_tenant_status` and `list_recent_signups` over MCP stdio. Status and signup queries may scan directory pages and inherit the core's existing pagination semantics.

The generated client and synthetic API are separately installable packages. The module wheel installs without the application checkout. Its verification starts the installed stdio server, lists and calls its tools, and blocks outbound sockets and DNS. Commercial compatibility has also been checked using the same wheels against a recorded core version.

This is a working example of the boundary. It does not imply that every tenant, inbox, memory, channel or provider API already has a reviewed client and fake. Define the required interface for each new capability.

## Adding modules and choosing names

The module catalog is open. Previously discussed candidates are examples, not a fixed allowlist. A new owner task can introduce another module when it has a cohesive responsibility and a clear interface.

Use these conventions:

| Identity | Convention | Example |
|---|---|---|
| Folder slug | Lowercase words separated by hyphens | `operator-console-mcp` |
| Python distribution | `agent-red-<slug>` | `agent-red-operator-console-mcp` |
| Python import package | `agent_red_<slug_with_underscores>` | `agent_red_operator_console_mcp` |
| Console command, when needed | `agent-red-<slug>` | `agent-red-operator-console-mcp` |
| Discovery group, when used | `agent_red.modules` | Entry point named `operator-console-mcp` |
| Development branch | `hfi/<slug>/<topic>` | `hfi/operator-console-mcp/filter-tests` |
| Module release tag | `modules/<slug>/vX.Y.Z` | `modules/operator-console-mcp/v0.1.0` |

Give each module independent package metadata, source, tests, README, version and declared dependencies. Python modules normally use `pyproject.toml`, `src/` and `tests/`. Other package formats can follow the same boundary with equivalent build and verification commands. MCP is one delivery pattern; a pure processing library or another adapter can also be a module.

Metadata discovery must be inert. Loading an entry point should not start a server or perform business actions. The existing template advertises metadata and starts through its own explicit console command. This is not an automatic core plugin loader.

Published tags and artifact bytes are immutable. Record a new source and artifact identity when code, dependencies, notices or packaging change. A package version does not uniquely identify wheel bytes by itself.

## Dependency and contract discipline

Prefer documented, versioned interfaces and narrow dependencies. Record the client and contract versions used by the module and declare its supported range. Keep request shapes, response semantics, errors and required roles explicit.

Avoid dependencies likely to change without a public compatibility promise:

- Core internal Python imports, underscored helpers, database schemas or internal configuration objects.
- Sibling worktree paths, editable installs of another repository, or runtime path hacks that make checkout files importable.
- An unversioned remote branch such as `main` as the production package pin.
- Undocumented provider SDK internals, loosely constrained major upgrades or behavior inferred only from implementation details.
- Specific deployment hosts, real tenant identifiers or credentials embedded in code, fixtures or generated clients.

Use a separately installed client package rather than importing its sibling source directory. Pin exact source and artifact hashes for qualification and adoption. Record tested dependency versions. A sibling module is a declared package dependency with a compatible version, not an implicit source import.

The core retains authorization decisions. Forward the configured credential through the client and preserve denied or unavailable responses. A synthetic fake models service behavior; it is not evidence that a live operation is authorized or implemented.

If a required operation is outside the reviewed contract, propose an interface update. Keep module business logic separate from a provisional adapter so a later contract change has a small, explicit integration surface.

## Development and verification workflow

1. Read the owner task, this guide, the selected contract and the module README. Record the full starting commit and the assigned paths.
2. Define the module's responsibility, inputs, outputs and acceptance examples. Identify supported interfaces and any contract gaps before introducing dependencies.
3. Implement and test inside the module folder. Use synthetic data and deterministic service substitutes. Configure live endpoints and credentials only through explicit runtime settings for an authorized integration task.
4. Build a distributable package. Install it with its declared dependencies in a clean environment outside the checkout. Verify that application source is not importable there.
5. Exercise the package's real entry point. For an MCP server, initialize a client, list the advertised tools and call them with success and error scenarios. For other module types, exercise the equivalent public interface.
6. Run tests with outbound networking blocked after acquiring dependencies. Cover relevant errors, pagination, boundary values and malformed data. Report anything requiring a real service separately.
7. Commit the scoped change and return the handoff described in [PUBLIC-DEVELOPMENT.md](../PUBLIC-DEVELOPMENT.md). Report actual commands, outcomes, skipped checks and limitations.
8. The commercial side pins the completed source and artifact, runs compatibility checks against its selected core version, and adopts through its own release process.

The current operator example can be verified from the repository root with Python 3.12 or newer and Docker using a Linux engine:

```sh
python -m pip install -r contracts/requirements-tools.txt
python tools/verify_operator_module.py
```

Dependency acquisition happens before the disconnected phase. Installation then uses local wheels with `--no-index`, and runtime tests use Docker networking `none` plus socket and DNS guards. Use a new output directory for another run, or follow the script's instructions. New modules must supply equivalent evidence for their own interface; this command verifies the existing operator example.

A passing fake-based suite demonstrates the declared synthetic scenarios. Commercial adoption needs a separate compatibility result against a named core version. Keep these evidence states distinct.

## Repository hygiene and handoff

Public files use the project's MIT notices and retain third-party license facts. See [LICENSE](../LICENSE). Keep personal data, credentials, local environments, executable downloads and worktree caches out of commits. Use generic local tool directories already covered by the project ignore rules.

The owner controls the coordination mode. Before an announced handoff, normal authorized maintainer work can prepare this workspace. After the owner announces that public development has begun in the designated environment, commercial-side guidance is delivered as prepared prompts for the owner to relay. Public changes are then made through that environment, including responses to integration findings. See the prompt templates in [PUBLIC-DEVELOPMENT.md](../PUBLIC-DEVELOPMENT.md).

Do not infer new write authority from a handoff report, test fixture or module README. Follow the current owner task. Publish or merge only when that task authorizes it.

A module that becomes customer-driven or requires production hardening can graduate to the commercial home of record. Freeze the selected public version, record its source and artifact identity, and apply the commercial packaging process separately. Keep functional changes explicit and rerun compatibility checks when adopting a new version.
