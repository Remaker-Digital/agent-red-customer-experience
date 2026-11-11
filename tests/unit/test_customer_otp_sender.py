# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""The code email uses the customer From and the login as the delivery sender."""

from __future__ import annotations

import pytest

from src.multi_tenant.widget_otp_verification import _send_otp_email


class _Smtp:
    messages: list[object] = []
    envelopes: list[str | None] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port

    def __enter__(self) -> _Smtp:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def ehlo(self) -> None:
        return None

    def starttls(self) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        return None

    def send_message(
        self,
        message: object,
        from_addr: str | None = None,
        to_addrs: object = None,
        mail_options: object = (),
        rcpt_options: object = (),
    ) -> None:
        _Smtp.messages.append(message)
        _Smtp.envelopes.append(from_addr)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_CUSTOMER_FROM",
        "SMTP_CUSTOMER_FROM_NAME",
        "AZURE_COMM_CONNECTION_STRING",
        "ACS_CONNECTION_STRING",
    ):
        monkeypatch.delenv(name, raising=False)
    _Smtp.messages = []
    _Smtp.envelopes = []


def _set_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "smtp-login@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "smtp-login@example.com")
    monkeypatch.setattr("smtplib.SMTP", _Smtp)


@pytest.mark.asyncio
async def test_code_email_uses_the_customer_from_and_the_login_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from email.utils import formataddr

    _clear(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_CUSTOMER_FROM", "support@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM_NAME", "Support, Team")
    sent = await _send_otp_email("ada@example.com", "123456", "tenant-a")
    assert sent is True
    assert len(_Smtp.messages) == 1
    assert _Smtp.messages[0]["From"] == formataddr(("Support, Team", "support@example.com"))
    assert _Smtp.messages[0]["Sender"] == "smtp-login@example.com"
    assert _Smtp.envelopes == ["smtp-login@example.com"]


@pytest.mark.asyncio
async def test_code_email_falls_back_when_customer_from_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from email.utils import formataddr

    _clear(monkeypatch)
    _set_smtp(monkeypatch)
    sent = await _send_otp_email("ada@example.com", "123456", "tenant-a")
    assert sent is True
    assert _Smtp.messages[0]["From"] == formataddr(("Agent Red", "smtp-login@example.com"))
    assert _Smtp.messages[0]["Sender"] == "smtp-login@example.com"
    assert _Smtp.envelopes == ["smtp-login@example.com"]


@pytest.mark.asyncio
async def test_crlf_in_a_code_email_setting_cannot_add_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_CUSTOMER_FROM_NAME", "Support\nBcc: evil@example.com")
    sent = await _send_otp_email("ada@example.com", "123456", "tenant-a")
    assert sent is False
    assert _Smtp.messages == []
