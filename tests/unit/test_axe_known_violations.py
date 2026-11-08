"""Per-suite axe baseline (tests/accessibility/known_violations.py).

Two Playwright suites scan the same nine standalone admin pages with axe:

- ``tests/accessibility/test_axe_ci.py`` (PlaywrightMock job) renders them
  through ``npm run dev:mock`` and the ``admin/standalone/mocks`` fixtures.
- ``tests/e2e/test_accessibility.py`` (admin-e2e job) renders them through
  plain Vite and the Playwright route mocks in ``tests/e2e/conftest.py``.

The mock data differs, so the violations differ. While both suites read one
shared map, PR #187 (c8fc50b6) tuned the Inbox entry for admin-e2e and PR #189
(44a165ba) tuned it back for the PlaywrightMock suite, and ADO build 25 failed
admin-e2e with "NEW color-contrast" plus "STALE scrollable-region-focusable".
These tests keep each suite on its own baseline. Whether an entry is stale can
only be seen by running axe, which the two Playwright suites do on every run.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.accessibility import known_violations as kv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Consumer module -> (suite constant it must pass, name of its scanned-page list).
CONSUMERS = {
    "tests/accessibility/test_axe_ci.py": ("AXE_CI", "PROVIDER_CONSOLE_PAGES"),
    "tests/e2e/test_accessibility.py": ("ADMIN_E2E", "STANDALONE_PAGES"),
}
BASELINE_READERS = {"baseline_issues", "known_violations_for"}
AXE_RULE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INBOX = "Inbox"
BUTTON = "button-name"
CONTRAST = "color-contrast"
SCROLL_REGION = "scrollable-region-focusable"
CRITICAL = "critical"


def _tree(rel_path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def _suite_args(tree: ast.Module) -> set[str]:
    """Names passed as the first argument to the baseline readers."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        first = node.args[0]
        if name in BASELINE_READERS and isinstance(first, ast.Name):
            found.add(first.id)
    return found


def _scanned_pages(tree: ast.Module, list_name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == list_name for t in node.targets):
            return {entry[1] for entry in ast.literal_eval(node.value)}
    raise AssertionError(f"{list_name} not found")


def _violation(rule_id: str, nodes: int, impact: str = "serious") -> dict:
    return {
        "id": rule_id,
        "impact": impact,
        "description": f"{rule_id} description",
        "nodes": [{"target": [f"#n{i}"]} for i in range(nodes)],
    }


@pytest.fixture()
def synthetic_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build 25 in miniature: the Inbox differs between the suites."""
    monkeypatch.setattr(
        kv,
        "KNOWN_VIOLATIONS_BY_SUITE",
        {
            kv.AXE_CI: {INBOX: {BUTTON: 2, SCROLL_REGION: 1}},
            kv.ADMIN_E2E: {INBOX: {BUTTON: 2, CONTRAST: 1}},
        },
    )


class TestBaselineStructure:
    def test_one_map_per_suite(self) -> None:
        assert kv.AXE_CI != kv.ADMIN_E2E
        assert set(kv.KNOWN_VIOLATIONS_BY_SUITE) == {kv.AXE_CI, kv.ADMIN_E2E}

    def test_no_shared_map_left_to_import(self) -> None:
        assert not hasattr(kv, "KNOWN_VIOLATIONS"), "a single map shared by both suites is what broke build 25"

    @pytest.mark.parametrize("rel_path", sorted(CONSUMERS))
    def test_each_consumer_reads_only_its_own_suite(self, rel_path: str) -> None:
        expected, _ = CONSUMERS[rel_path]
        tree = _tree(rel_path)
        assert _suite_args(tree) == {expected}
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        assert "KNOWN_VIOLATIONS" not in imported

    @pytest.mark.parametrize("rel_path", sorted(CONSUMERS))
    def test_baselined_pages_are_pages_the_suite_scans(self, rel_path: str) -> None:
        suite_const, list_name = CONSUMERS[rel_path]
        suite = getattr(kv, suite_const)
        scanned = _scanned_pages(_tree(rel_path), list_name)
        unscanned = set(kv.KNOWN_VIOLATIONS_BY_SUITE[suite]) - scanned
        assert not unscanned, f"{suite} entries for pages it never scans: {unscanned}"

    def test_entries_are_positive_counts_of_axe_rule_ids(self) -> None:
        for suite, pages in kv.KNOWN_VIOLATIONS_BY_SUITE.items():
            for page_name, rules in pages.items():
                assert rules, f"{suite}/{page_name}: drop empty page entries"
                for rule_id, max_count in rules.items():
                    assert AXE_RULE_ID.match(rule_id), (suite, page_name, rule_id)
                    assert type(max_count) is int and max_count > 0, (suite, page_name, rule_id)

    def test_unknown_suite_fails_closed(self) -> None:
        with pytest.raises(KeyError):
            kv.known_violations_for("visual", INBOX)
        with pytest.raises(KeyError):
            kv.baseline_issues("visual", INBOX, [])

    def test_page_without_entries_has_empty_baseline(self) -> None:
        assert kv.known_violations_for(kv.AXE_CI, "No Such Page") == {}


@pytest.mark.usefixtures("synthetic_baseline")
class TestBaselineIssues:
    def test_scan_matching_own_suite_passes(self) -> None:
        scan = [_violation(BUTTON, 2, CRITICAL), _violation(CONTRAST, 1)]
        assert kv.baseline_issues(kv.ADMIN_E2E, INBOX, scan) == []

    def test_fewer_nodes_than_baseline_max_passes(self) -> None:
        scan = [_violation(BUTTON, 1, CRITICAL), _violation(SCROLL_REGION, 1)]
        assert kv.baseline_issues(kv.AXE_CI, INBOX, scan) == []

    def test_other_suites_entries_do_not_apply(self) -> None:
        """The admin-e2e Inbox scan from build 25, judged by the PlaywrightMock map."""
        scan = [_violation(BUTTON, 2, CRITICAL), _violation(CONTRAST, 1)]
        issues = kv.baseline_issues(kv.AXE_CI, INBOX, scan)
        assert len(issues) == 2
        assert "NEW [serious] color-contrast" in issues[0]
        assert "STALE BASELINE scrollable-region-focusable" in issues[1]
        assert kv.AXE_CI in issues[1] and INBOX in issues[1]

    def test_more_nodes_than_baseline_is_a_regression(self) -> None:
        scan = [_violation(BUTTON, 3, CRITICAL), _violation(CONTRAST, 1)]
        issues = kv.baseline_issues(kv.ADMIN_E2E, INBOX, scan)
        assert issues == ["  REGRESSION [critical] button-name: 3 elements (baseline max: 2)"]

    def test_minor_and_moderate_rules_never_fail(self) -> None:
        scan = [
            _violation(BUTTON, 2, CRITICAL),
            _violation(CONTRAST, 1),
            _violation("page-has-heading-one", 1, "moderate"),
            _violation("region", 4, "minor"),
        ]
        assert kv.baseline_issues(kv.ADMIN_E2E, INBOX, scan) == []

    def test_unlisted_page_fails_on_any_serious_rule(self) -> None:
        issues = kv.baseline_issues(kv.ADMIN_E2E, "Billing", [_violation(CONTRAST, 1)])
        assert len(issues) == 1 and "NEW [serious] color-contrast" in issues[0]
