"""SMTP connections check the server certificate and never send a password in clear text.

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import logging
import re
import ssl
import smtplib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
HELPER = SRC / "multi_tenant" / "smtp_tls.py"
CALL = re.compile(r"\b(SMTP_SSL|starttls)\s*\(")


def _call_text(source: str, open_paren: int) -> str:
    depth = 0
    for index in range(open_paren, len(source)):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren : index + 1]
    return source[open_paren:]


def test_every_smtp_tls_call_passes_a_verified_context() -> None:
    """A new SMTP_SSL or starttls call in src fails unless it passes a context."""
    problems: list[str] = []
    for path in SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in CALL.finditer(source):
            call = _call_text(source, match.end() - 1)
            relative = path.relative_to(ROOT).as_posix()
            if relative != "src/multi_tenant/smtp_tls.py":
                problems.append(f"{relative} calls {match.group(1)} outside the helper")
            elif "context=" not in call:
                problems.append(f"{relative} calls {match.group(1)} without context")
    helper = HELPER.read_text(encoding="utf-8")
    assert "ssl.create_default_context()" in helper
    assert problems == []


def test_port_465_uses_the_default_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.multi_tenant.smtp_tls import open_verified_smtp

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    server = smtplib.SMTP()
    constructor = pytest.MonkeyPatch()
    calls: list[tuple[object, ...]] = []

    def smtp_ssl(*args: object, **kwargs: object) -> smtplib.SMTP:
        calls.append((args, kwargs))
        return server

    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)
    monkeypatch.setattr(server, "login", lambda *args: None)
    monkeypatch.setattr(server, "quit", lambda: None)
    with open_verified_smtp("smtp.example", 465, timeout=10, username="u", password="p"):
        pass
    assert calls[0][0][:2] == ("smtp.example", 465)
    assert calls[0][1]["context"] is context
    constructor.undo()


def test_port_587_starts_tls_with_the_default_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.multi_tenant.smtp_tls import open_verified_smtp

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(ssl, "create_default_context", lambda: context)
    seen: dict[str, object] = {}

    class _Server(smtplib.SMTP):
        def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
            self.host = host
            self.port = port

        def ehlo(self, name: str = "") -> tuple[int, bytes]:
            return (250, b"ok")

        def starttls(self, *, context: ssl.SSLContext | None = None) -> tuple[int, bytes]:
            seen["context"] = context
            return (220, b"ready")

        def quit(self) -> None:
            return None

    monkeypatch.setattr(smtplib, "SMTP", _Server)
    with open_verified_smtp("smtp.example", 587, timeout=10, username="u", password="p"):
        pass
    assert seen["context"] is context


def test_port_25_does_not_send_the_password(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from src.multi_tenant.smtp_tls import open_verified_smtp

    class _Server(smtplib.SMTP):
        def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
            self.logged_in = False

        def ehlo(self, name: str = "") -> tuple[int, bytes]:
            return (250, b"ok")

        def starttls(self, *, context: ssl.SSLContext | None = None) -> tuple[int, bytes]:
            raise AssertionError("port 25 must not start TLS after refusing it")

        def login(self, user: str, password: str) -> None:
            self.logged_in = True
            raise AssertionError("port 25 must not send the password")

        def quit(self) -> None:
            return None

    monkeypatch.setattr(smtplib, "SMTP", _Server)
    with caplog.at_level(logging.WARNING), pytest.raises(smtplib.SMTPException):
        with open_verified_smtp("smtp.example", 25, timeout=10, username="u", password="p"):
            pass
    assert "port 25" in caplog.text


def test_a_failed_certificate_check_is_not_retried_without_one(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.multi_tenant.smtp_tls import open_verified_smtp

    calls: list[object] = []

    def smtp_ssl(*args: object, **kwargs: object) -> smtplib.SMTP:
        calls.append(kwargs.get("context"))
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unverified")))
    with caplog.at_level(logging.WARNING), pytest.raises(ssl.SSLError):
        with open_verified_smtp("smtp.example", 465, timeout=10, username="u", password="p"):
            pass
    assert len(calls) == 1
    assert calls[0] is not None
    assert "certificate" in caplog.text.lower()



# One send path for the SMTP senders (SonarCloud duplication on PR 264).
SENDERS = [
    "src/app/standalone_auth.py",
    "src/multi_tenant/access_expiry_email.py",
    "src/multi_tenant/admin_contact_api.py",
    "src/multi_tenant/email_change.py",
    "src/multi_tenant/email_verification.py",
    "src/multi_tenant/trial_expiry_email.py",
    "src/multi_tenant/welcome_email.py",
]


class _RecordingServer(smtplib.SMTP):
    """A server that records login and send calls instead of using a socket."""

    last: _RecordingServer | None = None

    def __init__(self, host: str = "", port: int = 0, timeout: float | None = None) -> None:
        self.timeout = timeout
        self.events: list[tuple[object, ...]] = []
        _RecordingServer.last = self

    def ehlo(self, name: str = "") -> tuple[int, bytes]:
        return (250, b"ok")

    def starttls(self, *, context: ssl.SSLContext | None = None) -> tuple[int, bytes]:
        self.events.append(("starttls",))
        return (220, b"ready")

    def login(self, user: str, password: str) -> tuple[int, bytes]:
        self.events.append(("login", user, password))
        return (235, b"ok")

    def send_message(self, msg: object, *args: object, **kwargs: object) -> dict[str, object]:
        self.events.append(("send", msg))
        return {}

    def quit(self) -> None:
        self.events.append(("quit",))


def test_send_verified_smtp_logs_in_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    from email.message import EmailMessage

    from src.multi_tenant.smtp_tls import send_verified_smtp

    monkeypatch.setattr(smtplib, "SMTP", _RecordingServer)
    msg = EmailMessage()
    send_verified_smtp(msg, "smtp.example", 587, timeout=15, username="u", password="p")
    server = _RecordingServer.last
    assert server.timeout == 15
    assert server.events == [("starttls",), ("login", "u", "p"), ("send", msg), ("quit",)]


def test_send_verified_smtp_needs_both_credentials_to_log_in(monkeypatch: pytest.MonkeyPatch) -> None:
    from email.message import EmailMessage

    from src.multi_tenant.smtp_tls import send_verified_smtp

    monkeypatch.setattr(smtplib, "SMTP", _RecordingServer)
    msg = EmailMessage()
    send_verified_smtp(msg, "smtp.example", 587, timeout=10, username="u", password="")
    assert ("send", msg) in _RecordingServer.last.events
    assert not [event for event in _RecordingServer.last.events if event[0] == "login"]


def test_send_verified_smtp_on_port_25_with_a_password_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from email.message import EmailMessage

    from src.multi_tenant.smtp_tls import send_verified_smtp

    _RecordingServer.last = None
    monkeypatch.setattr(smtplib, "SMTP", _RecordingServer)
    with pytest.raises(smtplib.SMTPException):
        send_verified_smtp(EmailMessage(), "smtp.example", 25, timeout=10, username="u", password="p")
    assert _RecordingServer.last is None


@pytest.mark.parametrize("module_path", SENDERS)
def test_each_sender_sends_through_the_shared_path(module_path: str) -> None:
    source = (ROOT / module_path).read_text(encoding="utf-8")
    assert "send_verified_smtp(" in source
    assert "with open_verified_smtp(" not in source
