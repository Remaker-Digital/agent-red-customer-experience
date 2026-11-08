"""Known pre-existing axe violations, baselined per Playwright suite (WI-3166).

Two suites scan the same nine standalone admin pages. Each has its own map,
because they render the pages from different mock data:

  - ``axe-ci``: tests/accessibility/test_axe_ci.py (ADO job PlaywrightMock).
    ``npm run dev:mock`` serves the admin/standalone/mocks fixtures; 1280x800.
  - ``admin-e2e``: tests/e2e/test_accessibility.py (ADO job PlaywrightUi,
    matrix leg admin-e2e). Plain Vite plus the Playwright route mocks in
    tests/e2e/conftest.py; default 1280x720 viewport.

The Inbox shows why: the dev:mock data leaves a ScrollArea with nothing
focusable in it (scrollable-region-focusable), while the route-mock data shows
an avatar whose initials fail color-contrast. Both are real defects and each
suite records the one it renders. One shared map tuned for one suite broke the
other (PR #187, then PR #189, then ADO build 25).

Baseline semantics (per suite, per page):
  - Maps page name -> {axe_rule_id: max_affected_node_count}
  - Tests FAIL on any NEW critical/serious rule not in that suite's baseline
  - Tests FAIL if an existing rule's node count EXCEEDS the baseline
  - Tests FAIL if a baselined rule NO LONGER fires (forces cleanup)
  - Remove entries as violations are fixed, in the suite that reports them.
    Never copy an entry from the other suite to silence a NEW violation.

Owner acceptance: Mike accepted count-based baseline 2026-04-12 (S282).
Decision cited in bridge/axe-core-ci-enforcement-013.md.
Remediation tracked as follow-up work.

Expiry: All entries must be eliminated before next production release.
Remediation priority: critical rules first, then serious.

Source: axe-ci from the S282 local scan against admin/standalone dev:mock;
both maps re-scanned locally 2026-10-21 (Chromium, axe-playwright-python 0.1.7),
which reproduced the build 25 admin-e2e Inbox failure.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

AXE_CI = "axe-ci"
ADMIN_E2E = "admin-e2e"

ARIA_INPUT_FIELD_NAME = "aria-input-field-name"
ARIA_PROGRESSBAR_NAME = "aria-progressbar-name"
BUTTON_NAME = "button-name"
COLOR_CONTRAST = "color-contrast"
LABEL = "label"
SCROLLABLE_REGION_FOCUSABLE = "scrollable-region-focusable"
SELECT_NAME = "select-name"

_BLOCKING_IMPACTS = frozenset({"critical", "serious"})

# Suite -> page name -> {axe_rule_id: max_affected_element_count}.
# When a violation is fixed, remove it. The test will fail on stale entries.
KNOWN_VIOLATIONS_BY_SUITE: dict[str, dict[str, dict[str, int]]] = {
    AXE_CI: {
        "Dashboard": {
            ARIA_PROGRESSBAR_NAME: 5,  # Mantine Progress/LoadingOverlay
        },
        "Configuration": {
            ARIA_INPUT_FIELD_NAME: 1,  # Mantine input without field name
            BUTTON_NAME: 6,  # Icon-only buttons missing aria-label
            COLOR_CONTRAST: 3,  # Mantine theme color contrast
            LABEL: 6,  # Form inputs without associated labels
        },
        "Inbox": {
            BUTTON_NAME: 2,  # Icon-only action buttons
            SCROLLABLE_REGION_FOCUSABLE: 1,  # ScrollArea viewport with no focusable content
        },
        "Analytics": {
            ARIA_PROGRESSBAR_NAME: 5,  # Mantine Progress/LoadingOverlay
        },
        "Team": {
            COLOR_CONTRAST: 14,  # Mantine theme color contrast
            SELECT_NAME: 4,  # Role select without accessible name, one per member
        },
        "Knowledge Base": {
            BUTTON_NAME: 18,  # Icon-only action buttons on KB entries
            COLOR_CONTRAST: 1,  # Mantine theme color contrast
        },
        "Integrations": {
            COLOR_CONTRAST: 13,  # Mantine theme color contrast
        },
        "Billing": {
            COLOR_CONTRAST: 11,  # Mantine theme color contrast
        },
        "Widget": {
            ARIA_INPUT_FIELD_NAME: 2,  # Mantine input without field name
            COLOR_CONTRAST: 7,  # Mantine theme color contrast
            LABEL: 3,  # Form inputs without associated labels
        },
    },
    # Same defects as axe-ci where the data matches; other counts where the
    # route mocks return a different number of rows (saved configurations,
    # team members, KB entries, integrations).
    ADMIN_E2E: {
        "Dashboard": {
            ARIA_PROGRESSBAR_NAME: 5,  # Mantine Progress/LoadingOverlay
        },
        "Configuration": {
            ARIA_INPUT_FIELD_NAME: 1,  # Mantine input without field name
            BUTTON_NAME: 6,  # Icon-only buttons missing aria-label
            COLOR_CONTRAST: 4,  # Theme contrast; one more saved-config Activate button
            LABEL: 6,  # Form inputs without associated labels
        },
        "Inbox": {
            BUTTON_NAME: 2,  # Icon-only action buttons
            COLOR_CONTRAST: 1,  # Avatar initials on an orange placeholder (2.52:1)
        },
        "Analytics": {
            ARIA_PROGRESSBAR_NAME: 5,  # Mantine Progress/LoadingOverlay
        },
        "Team": {
            COLOR_CONTRAST: 5,  # Mantine theme color contrast
            SELECT_NAME: 2,  # Role select without accessible name, one per member
        },
        "Knowledge Base": {
            BUTTON_NAME: 4,  # Icon-only action buttons on KB entries
            COLOR_CONTRAST: 1,  # Mantine theme color contrast
        },
        "Integrations": {
            COLOR_CONTRAST: 2,  # Mantine theme color contrast
        },
        "Billing": {
            COLOR_CONTRAST: 11,  # Mantine theme color contrast
        },
        "Widget": {
            ARIA_INPUT_FIELD_NAME: 2,  # Mantine input without field name
            COLOR_CONTRAST: 7,  # Mantine theme color contrast
            LABEL: 3,  # Form inputs without associated labels
        },
    },
}


def known_violations_for(suite: str, page_name: str) -> dict[str, int]:
    """Return one suite's baseline for one page.

    An unknown suite raises KeyError, so a new consumer cannot silently fall
    back to another suite's entries.
    """
    return KNOWN_VIOLATIONS_BY_SUITE[suite].get(page_name, {})


def baseline_issues(
    suite: str,
    page_name: str,
    violations: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Compare axe violations for one page with that suite's baseline.

    Returns one line per NEW, REGRESSION or STALE finding; an empty list means
    the page passes. Minor and moderate rules are logged and never fail.
    """
    known = known_violations_for(suite, page_name)
    issues: list[str] = []
    seen: set[str] = set()
    for violation in violations:
        rule_id = violation.get("id", "unknown")
        impact = violation.get("impact", "minor")
        if impact not in _BLOCKING_IMPACTS:
            logger.info("a11y [%s/%s] minor/moderate: %s", suite, page_name, rule_id)
            continue
        node_count = len(violation.get("nodes", []))
        max_count = known.get(rule_id)
        if max_count is None:
            description = violation.get("description", "")
            issues.append(f"  NEW [{impact}] {rule_id}: {description} ({node_count} elements)")
            continue
        seen.add(rule_id)
        if node_count > max_count:
            issues.append(f"  REGRESSION [{impact}] {rule_id}: {node_count} elements (baseline max: {max_count})")
        else:
            logger.warning(
                "a11y [%s/%s] BASELINED %s: %s (%d/%d elements)",
                suite,
                page_name,
                impact,
                rule_id,
                node_count,
                max_count,
            )
    for rule_id in sorted(set(known) - seen):
        issues.append(
            f"  STALE BASELINE {rule_id}: no longer fires on {page_name} in {suite}. "
            f"Remove it from the {suite} map in tests/accessibility/known_violations.py."
        )
    return issues
