# © 2026 Michael Palmeter. All rights reserved.
"""Values for log lines.

A value from a request, such as an email address or a tenant id, can hold a
carriage return or a line feed. Logged as it is, it starts a new line that
reads like a separate log entry. ``loggable`` removes both characters.
"""

from __future__ import annotations

from typing import Any


def loggable(value: Any) -> str:
    """A value for a log line, with CR and LF removed so it cannot forge log entries."""
    return str(value).replace("\r", "").replace("\n", "")
