# Developing Agent Red from the public snapshot

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

This project is licensed under the MIT License. See LICENSE.

This repository contains the complete, cleansed Agent Red implementation for testing, evaluation and additive module development. Its core is aligned with the commercial source commit recorded in `.snapshot-provenance.json`.

## Start a piece of work

1. Fetch the current public main and create a separate branch named `hfi/<feature>`.
2. Record the full public starting commit SHA. Use this fixed commit as the import base. Do not use a moving branch name as the base later.
3. Keep new development under `modules/<name>/`, with independent package metadata, tests and documentation. Commit the changes before handing them back.
4. Include the branch name, starting SHA, work commit SHA, changed behavior, test commands and results, and any known issues in the handoff.

The `.snapshot-provenance.json` file records the private source commit used to export this snapshot. It also backfills the known mapping for the original `v2.0.23` public tag. The repositories have separate histories. A public commit SHA is not a private source commit SHA.

## Return work to the commercial development path

The normal adoption unit for a new module is a tested, versioned package with a recorded source commit and artifact checksum. Use supported APIs and extension points rather than commercial internals. Core changes belong on the commercial release path and are brought back here through a source refresh. The module scope CI check rejects core edits on `hfi/*` branches.

An exceptional source import uses the paired import tool beside the private export tooling. It compares the fixed public base with the completed development ref, restores private copyright headers, and excludes public notices, public documentation wrappers, provenance and local tool ignore rules. It applies the patch on an `import/<work-ref>` branch for review.

The importer reports edits whose hunks overlap scrubbed or otherwise adapted source. It does not reconstruct credentials or deployment values. Binary files, files without a supported copyright comment syntax, collisions, and apply failures require maintainer review. They are never silently dropped into the product.

The maintainer reviews the package or import branch and runs the product checks before adoption. A module that becomes customer-driven or requires production hardening graduates to the commercial project and its public version is frozen. A changed license or package build receives a new artifact identity. Importing never merges into private main or deploys anything.

## Refreshing this snapshot

Use the guarded export and publication workflow. It preserves module-only work and all development branch refs while refreshing the core. It refuses a refresh when outstanding work edits the core or when a development worktree has uncommitted changes. Import or otherwise resolve such work before a refresh. The guard does not rebase, delete, or overwrite development branches.

The guard is part of the maintainer tooling. GitHub does not enforce it against a direct push outside that tooling.

Keep local executables in the ignored `.tools/` or `tools-bin/` directory. Keep local caches, credentials, and real personal data out of Git. Review the files being committed even when ignore rules are present.
