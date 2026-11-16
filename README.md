# Agent Red Customer Experience

Agent Red Customer Experience automates support conversations for e-commerce stores. The hosted service is in a closed beta.

This source is source-available. It is not open source. The PolyForm Noncommercial License 1.0.0 is not an open source license.

You may use, change, and share this software for a noncommercial purpose, under the terms in [LICENSE](LICENSE).

To ask about commercial use, email support@remakerdigital.com.

Access to the hosted service is by request at the same address. There is no self-serve sign-up. Prices shown in the docs are planned and are not on offer yet.

The closed beta runs seven core roles in one gateway process: intent classification, knowledge retrieval, response generation, a critic, escalation, analytics, and a co-pilot for staff.

The public source repository is [Remaker-Digital/agent-red-customer-experience](https://github.com/Remaker-Digital/agent-red-customer-experience).

The public source snapshot is v2.0.20. Michael Palmeter directed the project, and AI coding agents built it.

## Try visitor chat

Open the chat launcher on the [Remaker Digital Contact page](https://www.remakerdigital.com/contact/). Try **Ways to work with us** or ask a question about Remaker Digital. The live demo follows its own release schedule and may serve a different version from this snapshot.

## Read the source

- [Widget entry point](widget/src/index.ts) mounts the launcher and conversation overlay.
- [Chat endpoints](src/chat/endpoints.py) handle chat requests and responses.
- [Tenant authentication](src/multi_tenant/auth.py) resolves tenant context from the request.
- [Chat pipeline](src/chat/pipeline/orchestrator.py) coordinates intent, retrieval, reply generation, and review.

## Build and test locally

Use Python 3.12 or newer. Run the offline unit tests from the root in an environment without cloud credentials. Tests that need excluded deployment files report why they were skipped.

```sh
python -m pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests/unit -q
```

With Node.js 20.19 or newer, run the widget checks and compile its browser bundle.

```sh
npm --prefix widget ci
npm --prefix widget test
npm --prefix widget run typecheck
npm --prefix widget run build
```

Configuration examples use placeholders. The hosted service uses separate deployment and tenant settings. The container recipes reference production build inputs omitted from this preview.

## Checks

GitHub Actions runs on every push to `main` and on every pull request:

- Ruff, blocking on rules E and F, for `src` and `tests`
- Bandit on `src`
- Semgrep, using the Python, security-audit, and secrets rulesets
- pip-audit, with the one recorded waiver `PYSEC-2026-3740`
- The docs site build, markdown lint, and link check

These checks do not commit corrections. Before you publish a change, run `ruff check --fix src tests` and `ruff format src tests` locally. The private release pipeline is not in this repository.

Required Notice: Copyright Remaker Digital, a DBA of VanDusen & Palmeter, LLC
