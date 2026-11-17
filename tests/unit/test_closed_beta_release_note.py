"""The closed beta note is in the tagged tree, and not in the co-pilot image.

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT
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


NOTE_204 = "closed-beta-release-note-v2.0.4.md"


def test_the_v2_0_4_note_names_the_encryption_fixes_and_keeps_tenants_out():
    """v2.0.4 ships the fixes P6 found on v2.0.3 with encryption on."""
    note = ROOT / "docs" / "operations" / NOTE_204
    text = note.read_text(encoding="utf-8")
    assert "v2.0.0" in text
    assert "No real tenant is admitted yet." in text
    assert "retention" in text
    assert "customer-email-lookup-key" in text
    assert "shop/redact" in text
    assert "503" in text
    assert "plain text" in text
    for path in ("CLOSED-BETA-RUNBOOK.md", "INDEX.md"):
        assert NOTE_204 in (ROOT / "docs" / "operations" / path).read_text(encoding="utf-8")
    assert NOTE_204 in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    site = ROOT / "docs-site"
    assert not any(path.name == NOTE_204 for path in site.rglob("*"))


NOTE_205 = "closed-beta-release-note-v2.0.5.md"


def test_the_v2_0_5_note_names_the_p6_fixes_and_keeps_tenants_out():
    """v2.0.5 ships the two fixes P6 found on v2.0.4 (decisions 92 and 93)."""
    note = ROOT / "docs" / "operations" / NOTE_205
    text = note.read_text(encoding="utf-8")
    assert "v2.0.0" in text
    assert "No real tenant is admitted yet." in text
    assert "retention" in text
    assert "identity_email" in text
    assert "identity_phone" in text
    assert "DEK rotation" in text
    assert "display_name" in text
    assert "Support" in text
    assert "plain text" in text
    for path in ("CLOSED-BETA-RUNBOOK.md", "INDEX.md"):
        assert NOTE_205 in (ROOT / "docs" / "operations" / path).read_text(encoding="utf-8")
    assert NOTE_205 in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    site = ROOT / "docs-site"
    assert not any(path.name == NOTE_205 for path in site.rglob("*"))


NOTE_206 = "closed-beta-release-note-v2.0.6.md"


def test_the_v2_0_6_note_requires_first_request_enforcement_on_staging():
    note = ROOT / "docs" / "operations" / NOTE_206
    text = note.read_text(encoding="utf-8")
    assert "GC-7" in text
    assert "first request" in text
    assert "HTTP 403" in text
    assert "HTTP 201" in text
    assert "No real tenant is admitted yet." in text
    assert "retention" in text
    for path in (ROOT / "CHANGELOG.md", *(note.parent / name for name in ("CLOSED-BETA-RUNBOOK.md", "INDEX.md"))):
        assert NOTE_206 in path.read_text(encoding="utf-8")
    assert not any(path.name == NOTE_206 for path in (ROOT / "docs-site").rglob("*"))


def test_the_product_version_is_v2_0_23():
    """The release pipeline refuses a tag whose version differs from PRODUCT_VERSION."""
    from src.multi_tenant.api_versioning import PRODUCT_VERSION

    assert PRODUCT_VERSION == "2.0.23"
