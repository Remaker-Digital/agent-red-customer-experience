# Additive modules

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

SPDX-License-Identifier: MIT

This directory is the development boundary for new Agent Red modules. The complete application elsewhere in the repository is a cleansed projection of the commercial core identified in `.snapshot-provenance.json`.

Create each module in `modules/<name>/`, with its own package metadata, source, tests and README. Give it an independent version and declare its dependencies and supported core API version. Use API calls and supported extension points rather than importing core internals or accessing its database.

Develop on `hfi/<feature>` branches. The module scope check rejects changes outside `modules/`. Core changes belong on the commercial release path and return here through a source refresh.

Record the tested package version, source commit and artifact checksum before private adoption. A module that becomes customer-driven or requires production hardening graduates to the commercial project; its public version is frozen. Existing tagged package artifacts retain their original metadata and content hashes.

The initial template is `operator-console-mcp/`, backed by the independently packaged client and synthetic API in `contracts/`. It exposes three read-only tools using the existing directory API. No module package is installed or enabled by this directory alone.
