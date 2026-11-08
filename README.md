# Agent Red Customer Experience

Agent Red Customer Experience automates support conversations for e-commerce stores. The hosted service is in a closed beta.

This source is source-available. It is not open source. The PolyForm Noncommercial License 1.0.0 is not an open source license.

You may use, change, and share this software for a noncommercial purpose, under the terms in [LICENSE](LICENSE).

To ask about commercial use, email support@remakerdigital.com.

Access to the hosted service is by request at the same address. There is no self-serve sign-up. Prices shown in the docs are planned and are not on offer yet.

The closed beta runs seven core roles in one gateway process: intent classification, knowledge retrieval, response generation, a critic, escalation, analytics, and a co-pilot for staff.

The public source repository is [Remaker-Digital/agent-red-customer-experience](https://github.com/Remaker-Digital/agent-red-customer-experience).

## Checks

GitHub Actions runs on every push to `main` and on every pull request:

- Ruff, blocking on rules E and F, for `src` and `tests`
- Bandit on `src`
- Semgrep, using the Python, security-audit, and secrets rulesets
- pip-audit, with the one recorded waiver `PYSEC-2026-3740`
- The docs site build, markdown lint, and link check

These checks do not commit corrections. Before you publish a change, run `ruff check --fix src tests` and `ruff format src tests` locally. The private release pipeline is not in this repository.

Required Notice: Copyright Remaker Digital, a DBA of VanDusen & Palmeter, LLC
