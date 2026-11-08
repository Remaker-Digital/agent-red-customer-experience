"""Tests for the coverage.py exclusion patterns in pyproject.toml (F41).

coverage.py treats every [tool.coverage.report] exclude_lines entry as a
regular expression searched anywhere in the source text. When a match lands
on any line of a def, if or other compound statement header, the whole suite
is excluded. A bare "pass" entry therefore matched critic_passed, password
and bypass and silently removed whole functions from the coverage report.
These tests pin the pass and ellipsis entries to bare statements.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _exclude_lines() -> list[str]:
    with PYPROJECT.open("rb") as fh:
        config = tomllib.load(fh)
    return config["tool"]["coverage"]["report"]["exclude_lines"]


def _matching_entries(line: str) -> list[str]:
    """Entries that match `line`, searched the way coverage.py searches them."""
    text = line + "\n"
    return [pattern for pattern in _exclude_lines() if re.search(pattern, text, flags=re.MULTILINE)]


MEASURED_LINES = [
    "        critic_passed: bool | None = None,",
    "def verify_password(",
    "    async def bypass_rate_limit(self) -> None:",
    "    if result.passed:",
    "    return self._passthrough(request)",
    "    x = compute()  # wait for it...",
    "    placeholder = ...",
    '    defaults = {"key": ...}',
    "    deadline: float = ...",
    "        # ... background loop calls process_job(job_id) ...",
]

EXCLUDED_LINES = [
    "pass",
    "    pass",
    "        pass  # Non-fatal, best effort",
    "    ...",
    "    async def up(self, cosmos_manager: Any) -> None: ...",
    "def handler(event): ...  # protocol placeholder",
    "class Repository(Protocol): ...",
    "    x = load()  # pragma: no cover",
    "if TYPE_CHECKING:",
    'if __name__ == "__main__":',
    "        raise NotImplementedError",
]


@pytest.mark.parametrize("line", MEASURED_LINES)
def test_ordinary_code_line_is_not_excluded(line: str) -> None:
    assert _matching_entries(line) == []


@pytest.mark.parametrize("line", EXCLUDED_LINES)
def test_intended_construct_is_excluded(line: str) -> None:
    assert _matching_entries(line) != []


def test_match_does_not_spill_onto_next_line() -> None:
    # coverage.py excludes every line a match spans, so a pattern must not
    # run past the end of the bare statement into the following line.
    text = "x = 1\n\n    pass\n    # trailing note\n"
    for pattern in _exclude_lines():
        for match in re.finditer(pattern, text, flags=re.MULTILINE):
            assert "\n" not in match.group(0), pattern


def test_function_with_pass_in_signature_stays_measured() -> None:
    parser_module = pytest.importorskip("coverage.parser")
    misc = pytest.importorskip("coverage.misc")
    source = (
        "def verify_password(\n"
        "    password: str,\n"
        "    critic_passed: bool | None = None,\n"
        ") -> bool:\n"
        "    if critic_passed:\n"
        "        return True\n"
        "    return bool(password)\n"
        "\n"
        "\n"
        "def stub() -> None:\n"
        "    pass\n"
    )
    parser = parser_module.PythonParser(text=source, exclude=misc.join_regex(_exclude_lines()))
    parser.parse_source()
    assert parser.excluded == {11}
