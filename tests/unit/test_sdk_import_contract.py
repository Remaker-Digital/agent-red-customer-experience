"""Import contract for the AGNTCY, A2A and SLIM SDKs that src/ depends on.

src/ imports from agntcy-app-sdk, a2a-sdk and slim-bindings, often lazily
inside the function that needs them, so a breaking upgrade only shows when
that code path runs. ADO build 25 is the example: agntcy-app-sdk 0.6.0 pulled
in a2a-sdk 1.1.0, which removed MessageSendParams from a2a.types, and the
agentsChat shard failed with an ImportError raised inside
AgentDispatchMixin._call_via_transport. These tests find every SDK symbol
src/ imports, import each one up front, and check that requirements.txt keeps
the three packages on the releases the code is written for.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import ast
import importlib
import re
import tomllib
from pathlib import Path
from typing import NamedTuple

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
REQUIREMENTS = REPO_ROOT / "requirements.txt"

SDK_ROOTS = frozenset({"a2a", "agntcy_app_sdk", "slim_bindings"})

# Every SDK symbol src/ uses, by module; None is a plain "import module". For
# "import slim_bindings as sb" the names are the sb.<attr> attributes the code
# reads. Most of these are lazy imports inside functions (MessageSendParams,
# A2AClientFactory, ClientConfig). Update this when src/ adds or drops one.
KNOWN_SYMBOLS_BY_MODULE = {
    "a2a.types": [
        "AgentCapabilities",
        "AgentCard",
        "AgentSkill",
        "Message",
        "MessageSendParams",
        "Role",
        "SendMessageRequest",
        "TextPart",
    ],
    "agntcy_app_sdk.factory": ["AgntcyFactory", "ProtocolTypes", "TransportTypes"],
    "agntcy_app_sdk.semantic.a2a.client.config": ["ClientConfig", "NatsTransportConfig", "SlimTransportConfig"],
    "agntcy_app_sdk.semantic.a2a.client.factory": ["A2AClientFactory"],
    "agntcy_app_sdk.semantic.base": ["BaseAgentProtocol"],
    "agntcy_app_sdk.semantic.message": ["Message"],
    "agntcy_app_sdk.transport.base": ["BaseTransport"],
    "slim_bindings": [
        None,
        "create_service",
        "get_build_info",
        "get_version",
        "initialize_with_defaults",
        "new_insecure_server_config",
    ],
}
KNOWN_SYMBOLS = {(module, name) for module, names in KNOWN_SYMBOLS_BY_MODULE.items() for name in names}

# src/ imports these inside try/except ImportError and falls back to a local
# stub or None. agntcy-app-sdk 0.5.0 removed them, so only their modules have
# to import.
OPTIONAL_SYMBOLS_BY_MODULE = {
    "agntcy_app_sdk.factory": ["ProtocolTypes", "TransportTypes"],
    "agntcy_app_sdk.semantic.base": ["BaseAgentProtocol"],
}
OPTIONAL_SYMBOLS = {(module, name) for module, names in OPTIONAL_SYMBOLS_BY_MODULE.items() for name in names}

# (package, releases the code runs on, releases that break it). agntcy-app-sdk
# 0.4.x has no semantic.a2a.client package; 0.6.0 requires a2a-sdk 1.x and
# slim-bindings 2.x.
SUPPORTED_RELEASES = [
    ("agntcy-app-sdk", ["0.5.0", "0.5.4", "0.5.5"], ["0.4.7", "0.6.0"]),
    ("a2a-sdk", ["0.3.0", "0.3.20"], ["1.0.0", "1.1.0"]),
    ("slim-bindings", ["1.0.0", "1.1.1"], ["2.0.0", "2.2.0"]),
]
SDK_PACKAGES = frozenset(package for package, _, _ in SUPPORTED_RELEASES)


class SdkSymbol(NamedTuple):
    module: str
    name: str | None
    where: str

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.module, self.name)

    @property
    def label(self) -> str:
        return self.module if self.name is None else f"{self.module}.{self.name}"


def _is_sdk(module: str) -> bool:
    return module.split(".", 1)[0] in SDK_ROOTS


def _import_symbols(tree: ast.AST, rel: str) -> tuple[list[SdkSymbol], dict[str, str]]:
    """SDK imports in one file, plus the names "import x [as y]" binds."""
    symbols: list[SdkSymbol] = []
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and _is_sdk(node.module or ""):
            symbols += [SdkSymbol(node.module, alias.name, f"{rel}:{node.lineno}") for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in (a for a in node.names if _is_sdk(a.name)):
                symbols.append(SdkSymbol(alias.name, None, f"{rel}:{node.lineno}"))
                name = alias.asname or alias.name.split(".", 1)[0]
                bound[name] = alias.name if alias.asname else name
    return symbols, bound


def _attribute_symbols(tree: ast.AST, rel: str, bound: dict[str, str]) -> list[SdkSymbol]:
    """sb.<attr> reads where sb was bound by "import slim_bindings as sb"."""
    return [
        SdkSymbol(bound[node.value.id], node.attr, f"{rel}:{node.lineno}")
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound
    ]


def _scan_src() -> list[SdkSymbol]:
    """Every SDK symbol src/ uses, including lazy imports inside functions."""
    found: list[SdkSymbol] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(REPO_ROOT).as_posix()
        symbols, bound = _import_symbols(tree, rel)
        found += symbols + _attribute_symbols(tree, rel, bound)
    return found


def _resolve(module_name: str, name: str | None) -> None:
    """Import the way "from module import name" does, attribute or submodule."""
    module = importlib.import_module(module_name)
    if name is None or hasattr(module, name):
        return
    try:
        importlib.import_module(f"{module_name}.{name}")
    except ModuleNotFoundError:
        raise ImportError(f"cannot import name {name!r} from {module_name!r}") from None


def _requirements(path: Path) -> dict[str, Requirement]:
    """Requirement lines of a requirements file, keyed by canonical name."""
    found: dict[str, Requirement] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith(("#", "-")):
            requirement = Requirement(line)
            found[canonicalize_name(requirement.name)] = requirement
    return found


def test_scan_finds_exactly_the_known_sdk_symbols() -> None:
    found = {symbol.key for symbol in _scan_src()}
    assert found - KNOWN_SYMBOLS == set(), "src/ uses SDK symbols missing from KNOWN_SYMBOLS"
    assert KNOWN_SYMBOLS - found == set(), "KNOWN_SYMBOLS lists SDK symbols src/ no longer uses"


def test_every_sdk_symbol_src_uses_imports() -> None:
    failures = []
    for symbol in _scan_src():
        name = None if symbol.key in OPTIONAL_SYMBOLS else symbol.name
        try:
            _resolve(symbol.module, name)
        except Exception as exc:  # report every broken symbol, not just the first
            failures.append(f"{symbol.label} ({symbol.where}): {type(exc).__name__}: {exc}")
    assert failures == [], "SDK symbols src/ uses no longer import:\n" + "\n".join(failures)


@pytest.mark.parametrize(
    ("package", "supported", "breaking"),
    SUPPORTED_RELEASES,
    ids=[package for package, _, _ in SUPPORTED_RELEASES],
)
def test_requirements_keep_sdk_on_supported_releases(package: str, supported: list[str], breaking: list[str]) -> None:
    specifier = _requirements(REQUIREMENTS)[package].specifier
    assert [v for v in supported if not specifier.contains(v)] == [], (
        f"{package}{specifier} rejects a supported release"
    )
    assert [v for v in breaking if specifier.contains(v)] == [], f"{package}{specifier} allows a breaking release"


def test_sdk_versions_are_declared_only_in_requirements_txt() -> None:
    """Other requirement files, pyproject.toml and Dockerfiles take them from requirements.txt."""
    stray = [
        f"{path.name}: {name}"
        for path in sorted(REPO_ROOT.glob("requirements*.txt"))
        if path != REQUIREMENTS
        for name in sorted(SDK_PACKAGES & _requirements(path).keys())
    ]
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        project_dependencies = tomllib.load(fh)["project"].get("dependencies", [])
    stray += [
        f"pyproject.toml: {dep}"
        for dep in project_dependencies
        if canonicalize_name(Requirement(dep).name) in SDK_PACKAGES
    ]
    mention = re.compile(r"\b(" + "|".join(sorted(SDK_PACKAGES)) + r")\b")
    for dockerfile in sorted([*REPO_ROOT.glob("Dockerfile*"), *SRC_DIR.rglob("Dockerfile*")]):
        lines = dockerfile.read_text(encoding="utf-8").splitlines()
        stray += [
            f"{dockerfile.name}: {line.strip()}"
            for line in lines
            if not line.lstrip().startswith("#") and mention.search(line)
        ]
    assert stray == []
