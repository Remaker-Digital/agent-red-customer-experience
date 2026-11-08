"""The closed beta note is in the tagged tree, and not in the co-pilot image.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTE = "closed-beta-release-note-v2.0.0.md"

REQUIRED = (
    "+44 (0)",
    "0049",
    "en dash",
    "2-2-2-2-2",
    "test_a_surname_alone_is_not_left_in_clear",
    "test_a_reversed_surname_comma_name_is_not_left_in_clear",
    "test_a_name_with_a_nonbreaking_space_is_not_left_in_clear",
    "test_a_decomposed_accented_name_is_not_left_in_clear",
    "test_a_multi_segment_order_number_is_not_left_in_clear",
    "test_a_greek_email_local_part_is_not_left_in_clear",
    "test_a_cyrillic_email_local_part_is_not_left_in_clear",
    "test_an_internationalized_domain_is_not_left_in_clear",
    "test_a_punycode_domain_is_not_left_in_clear",
    "test_a_decomposed_email_is_not_left_in_clear",
    "test_a_decomposed_domain_is_not_left_in_clear",
    "John To",
    "curly apostrophe",
    "Customer memory is off",
    "co-pilot",
    "peer binding",
    "my name is",
    "A name given only in one of those ways is not stored, so it reaches the model in plain text "
    "in every message, until the customer gives it in a recognized way.",
    "Only the customer's own name is protected. The name of any other person reaches the model in plain text.",
)

WRONG = "The first message that gives a name in one of those ways reaches the model in plain text."


def note_tree_available(root: Path) -> bool:
    """The test-host image copies tests/ and not docs/ or CHANGELOG.md."""
    return (root / "docs" / "operations").is_dir() and (root / "CHANGELOG.md").is_file()


pytestmark = pytest.mark.skipif(
    not note_tree_available(ROOT),
    reason="docs/ directory not available (container environment)",
)


def test_the_closed_beta_note_is_linked_and_kept_out_of_the_docs_site():
    note = ROOT / "docs" / "operations" / NOTE
    text = note.read_text(encoding="utf-8")
    assert WRONG not in text
    for needle in REQUIRED:
        assert needle in text
    assert NOTE in (ROOT / "docs" / "operations" / "CLOSED-BETA-RUNBOOK.md").read_text(encoding="utf-8")
    assert NOTE in (ROOT / "docs" / "operations" / "INDEX.md").read_text(encoding="utf-8")
    assert NOTE in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    site = ROOT / "docs-site"
    assert not any(path.name == NOTE for path in site.rglob("*"))


def test_the_note_check_skips_only_when_the_docs_tree_is_absent(tmp_path):
    assert note_tree_available(ROOT)
    assert not note_tree_available(tmp_path)


NOTE_201 = "closed-beta-release-note-v2.0.1.md"


def test_the_v2_0_1_note_keeps_the_limits_and_names_the_corrections():
    note = ROOT / "docs" / "operations" / NOTE_201
    text = note.read_text(encoding="utf-8")
    assert "v2.0.0" in text
    assert "ACS_SENDER_ADDRESS" in text
    assert "quality" in text
    assert "6-digit" in text
    assert NOTE_201 in (ROOT / "docs" / "operations" / "CLOSED-BETA-RUNBOOK.md").read_text(encoding="utf-8")
    assert NOTE_201 in (ROOT / "docs" / "operations" / "INDEX.md").read_text(encoding="utf-8")
    assert NOTE_201 in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    site = ROOT / "docs-site"
    assert not any(path.name == NOTE_201 for path in site.rglob("*"))
