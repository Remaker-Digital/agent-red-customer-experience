"""The test suite runs with the Azure CLI cut off (tests/conftest.py).

On 2026-10-05 a test sent 22 production `az containerapp update` commands
through the signed-in Azure CLI of the host it ran on. tests/conftest.py now
points AZURE_CONFIG_DIR at an empty temporary folder unless a live-service run
opts in with AGENT_RED_ALLOW_AZURE_CLI=1. This test fails if that guard is
removed or bypassed by accident.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_azure_cli_login_is_unreachable_from_tests() -> None:
    if os.environ.get("AGENT_RED_ALLOW_AZURE_CLI") == "1":
        pytest.skip("live-service run: Azure CLI deliberately allowed")
    config_dir = Path(os.environ["AZURE_CONFIG_DIR"])
    assert config_dir.name.startswith("agent-red-no-azure-"), config_dir
    assert config_dir.resolve() != (Path.home() / ".azure").resolve()
    # Any `az` call (for example a test's `az account show` probe) writes an
    # azureProfile.json; it must hold no signed-in subscription.
    profile = config_dir / "azureProfile.json"
    if profile.exists():
        subscriptions = json.loads(profile.read_text(encoding="utf-8-sig") or "{}").get("subscriptions", [])
        assert subscriptions == [], "the Azure CLI is signed in inside the test run"
    # No token cache may exist in the folder the CLI reads.
    for name in ("msal_token_cache.json", "msal_token_cache.bin", "accessTokens.json"):
        assert not (config_dir / name).exists(), name
