# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""A 6-digit code that still needs a second factor must not become a session."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_code_starts_the_second_factor() -> None:
    script = Path("tests/unit/signin_code_second_factor.mjs")
    completed = subprocess.run(
        ["node", str(script)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report["session_token"] is None
    assert report["second_factor"] == {
        "pendingToken": "pending-2fa-token",
        "tenantId": "tenant-1",
        "email": "ada@example.com",
        "mfaMethods": ["totp"],
    }
    assert report["direct_session"] == "session-token"
