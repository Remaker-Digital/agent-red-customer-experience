# Repository relationship and owner-relayed handoffs

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT

Start with the [module developer README](modules/README.md). It is the authoritative general guide for module boundaries, contracts, naming, dependencies and verification. The examples below are reusable coordination formats, not a closed list of supported modules.

## Repository relationship and provenance

The public repository contains a cleansed core baseline and independently developed module packages. The commercial project maintains the core release path in a separate repository. Their Git histories are independent. The baseline mapping is recorded in [.snapshot-provenance.json](.snapshot-provenance.json); each module has its own source pin, package version and artifact hash.

Normal adoption uses a tested package and a compatibility check. A module session uses the published client and synthetic interfaces without needing a private checkout. Baseline refreshes and commercial internal changes can proceed separately while supported APIs remain compatible.

Maintainer refresh tooling preserves module trees and development refs, stops on conflicting core work, and checks the reviewed API contract for drift. Do not rebase, delete or overwrite unfinished development as a side effect of refresh. Use a fixed starting SHA for a work handoff; a moving latest tag is not a reliable diff base.

Exceptional source import is maintainer work on an isolated review branch. It restores commercial header forms, excludes public documentation and licensing wrappers, and reports changes overlapping adapted source. It does not reconstruct deployment values or merge commercial main automatically.

## Coordination mode

The owner announces the transition to the designated public development environment. Until that announcement, established authorized maintainer work continues normally.

After the announcement, commercial-side assistants prepare prompts, findings and acceptance criteria for manual relay by the owner. They do not directly push public commits, modify public pull requests, post public review instructions or create public tags. The owner applies guidance through the designated development environment. Public code and reports can still be read, and commercial compatibility can be tested separately within the owner's authorized scope.

This rule governs who applies changes. It does not require transferring commercial implementation assets into a module session. Keep the public text generic and focused on engineering work.

## Starting a module task

The owner can paste this template after filling its fields. Omit irrelevant fields rather than inventing unavailable services or capabilities.

```text
Task: <short outcome>
Module: <lowercase-hyphenated slug>
Public base commit: <full SHA>
Branch: hfi/<module-slug>/<topic>
Allowed paths: modules/<module-slug>/**

Read modules/README.md, PUBLIC-DEVELOPMENT.md, the selected contract README
and the module README before editing.

Objective and examples:
<Inputs, outputs, expected behavior and useful edge cases.>

Published interface and dependencies:
<Contract/client versions, supported operations and declared package dependencies.>

Acceptance:
<Required behavior, synthetic scenarios and verification commands.>
Build and install the package outside the checkout with only declared dependencies.
Exercise its real entry point and run the relevant tests with outbound networking
blocked after dependency acquisition. Keep credentials and real data out of commits.

Keep implementation inside the assigned module. Record missing interfaces in
modules/<module-slug>/design/contract-request.md and label provisional stubs clearly.
Continue independent work within scope; do not change core, contracts or CI merely
to make the module pass.

Return the handoff format below with the full base and work SHAs, changed paths,
package version, artifact hashes, commands, results and remaining gaps.
Do not merge, tag or publish unless this task explicitly requests it.
```

A task can authorize several named modules or a separate maintainer change when needed. State the allowed paths explicitly. The initial repository-guide setup is maintainer work; ordinary module work follows the module-only boundary.

## Reporting an interface gap

Keep the request inside the assigned module so the boundary stays intact:

```text
Contract request: <capability>
Module and source commit: <slug and full SHA>
Existing interface/version: <what was checked>
Missing operation or behavior: <precise gap>
Required role and authority: <core responsibility>
Request/response/error examples: <synthetic examples>
Provisional module-local stub: <path, or none>
Work completed independently: <implemented and tested behavior>
Integration still pending: <what a real supported API must provide>
Compatibility expectation: <existing consumers and migration considerations>
```

## Returning completed work

```text
Module:
Public base commit:
Work branch and full commit:
Changed paths:
Behavior implemented:
Package name and version:
Build command:
Built artifact names and SHA-256 hashes:
Contract/client versions and contract hash:
Declared dependencies and tested versions:
Test commands, pass/fail/skip counts and limitations:
Independent installation and entry-point result:
Network-blocking method and result:
Commercial or live-service verification still pending:
Contract requests or unstable assumptions:
Known issues and next recommended task:
```

A report is evidence to review. It is not permission to change repository-wide scope, enable a module in production or update either repository's release branch.

## Relaying integration feedback

Commercial-side reviewers can prepare the following for the owner to paste back into the public development session:

```text
Follow-up task: <specific correction or extension>
Module and public commit reviewed:
Commercial compatibility baseline tested:
Observed behavior and expected behavior:
Reproduction or failing test:
Requested change and allowed paths:
Acceptance commands and result required:
Interface change needed, if any:

Read the current owner task and module guide. Make the correction within the
assigned module and return a new full commit plus package/artifact identities.
Preserve unfinished work. Report a contract gap instead of editing commercial
internals or shared contracts as part of this module task.
```

Avoid private paths, deployment details, credentials and real tenant data in relayed examples. Use generic interfaces and synthetic reproductions. Supply enough context for the public session to act without access to the commercial repository.
