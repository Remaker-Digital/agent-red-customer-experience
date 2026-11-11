# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Escalation mail and login notifications try SMTP, then ACS."""

from __future__ import annotations

import logging

import pytest

from src.chat.escalation_email import send_escalation_emails
from src.multi_tenant.login_notification import send_login_notification


def _kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "tenant_id": "tenant-e1",
        "conversation_id": "conv-e1-0001",
        "customer_email": "ada@example.com",
        "agent_email": "agent@example.com",
        "messages": [{"role": "user", "content": "Please help"}],
        "store_name": "Example",
        "reason": "The customer asked for a person",
        "category": "support",
        "urgency": "normal",
    }
    base.update(overrides)
    return base


class _Smtp:
    messages: list[object] = []
    envelopes: list[str | None] = []
    fail = False

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

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
        if _Smtp.fail:
            raise OSError("smtp down")
        _Smtp.messages.append(message)
        _Smtp.envelopes.append(from_addr)


def _clear_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
        "SMTP_CUSTOMER_FROM",
        "SMTP_CUSTOMER_FROM_NAME",
        "ACS_CONNECTION_STRING",
    ):
        monkeypatch.delenv(name, raising=False)
    _Smtp.messages = []
    _Smtp.envelopes = []
    _Smtp.fail = False


def _set_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "mailer@example.com")
    monkeypatch.setattr("smtplib.SMTP", _Smtp)


@pytest.mark.asyncio
async def test_escalation_sender_is_smtp_from_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The From address is SMTP_FROM. The SMTP login is not a sender."""
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "login@example.com")
    monkeypatch.setenv("SMTP_FROM", "support@example.com")
    result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    assert _Smtp.messages[0]["From"] == "Agent Red <support@example.com>"
    assert _Smtp.messages[1]["From"] == "Agent Red <support@example.com>"


@pytest.mark.asyncio
async def test_missing_smtp_from_does_not_send_as_the_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset SMTP_FROM does not fall back to SMTP_USERNAME."""
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.delenv("SMTP_FROM")
    monkeypatch.setenv("SMTP_USERNAME", "login@example.com")
    result = await send_escalation_emails(**_kwargs())
    assert _Smtp.messages == []
    assert result == {"customer": "failed", "agent": "failed"}


@pytest.mark.asyncio
async def test_smtp_sends_both_escalation_emails(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    with caplog.at_level(logging.INFO):
        result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    assert len(_Smtp.messages) == 2
    agent = _Smtp.messages[1]
    assert agent["Reply-To"] == "ada@example.com"
    assert "provider=SMTP" in caplog.text


@pytest.mark.asyncio
async def test_smtp_failure_falls_back_to_acs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    _Smtp.fail = True
    monkeypatch.setenv("ACS_CONNECTION_STRING", "endpoint=https://example.communication.azure.com/;accesskey=fake")
    sent: dict[str, object] = {}

    async def send_customer(**kwargs: object) -> str:
        sent["customer"] = kwargs
        return "Succeeded"

    class _Poller:
        def result(self, timeout: int = 60) -> object:
            return type("Result", (), {"status": "Succeeded"})()

    class _Client:
        def begin_send(self, message: dict[str, object]) -> _Poller:
            sent["agent"] = message
            return _Poller()

    monkeypatch.setattr("src.multi_tenant.alert_delivery.send_acs_email", send_customer)
    monkeypatch.setattr(
        "azure.communication.email.EmailClient.from_connection_string",
        lambda *args, **kwargs: _Client(),
    )
    with caplog.at_level(logging.INFO):
        result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    assert sent["agent"]["replyTo"] == [{"address": "ada@example.com"}]
    assert "provider=ACS" in caplog.text


@pytest.mark.asyncio
async def test_both_providers_failing_records_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    _Smtp.fail = True
    monkeypatch.setenv("ACS_CONNECTION_STRING", "endpoint=https://example.communication.azure.com/;accesskey=fake")

    async def send_customer(**kwargs: object) -> str:
        raise RuntimeError("acs down")

    class _Client:
        def begin_send(self, message: dict[str, object]) -> object:
            raise RuntimeError("acs down")

    monkeypatch.setattr("src.multi_tenant.alert_delivery.send_acs_email", send_customer)
    monkeypatch.setattr(
        "azure.communication.email.EmailClient.from_connection_string",
        lambda *args, **kwargs: _Client(),
    )
    result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "failed", "agent": "failed"}


@pytest.mark.asyncio
async def test_neither_provider_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail(monkeypatch)
    result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "skipped", "agent": "skipped"}


@pytest.mark.asyncio
async def test_crlf_in_customer_email_cannot_add_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    result = await send_escalation_emails(
        **_kwargs(customer_email="ada@example.com\r\nBcc: evil@example.com"),
    )
    assert result == {"customer": "failed", "agent": "failed"}
    assert _Smtp.messages == []


@pytest.mark.asyncio
async def test_customer_escalation_uses_the_customer_from_and_the_login_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case C: customers see support@, and Titan delivers as the login."""
    from email.utils import formataddr

    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "smtp-login@example.com")
    monkeypatch.setenv("SMTP_FROM", "smtp-login@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM", "support@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM_NAME", "Support, Team")
    result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    customer, agent = _Smtp.messages
    assert customer["From"] == formataddr(("Support, Team", "support@example.com"))
    assert customer["Sender"] == "smtp-login@example.com"
    assert _Smtp.envelopes[0] == "smtp-login@example.com"
    assert agent["From"] == "Agent Red <smtp-login@example.com>"
    assert agent["Sender"] is None
    assert _Smtp.envelopes[1] is None


@pytest.mark.asyncio
async def test_customer_escalation_falls_back_when_customer_from_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from email.utils import formataddr

    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_USERNAME", "smtp-login@example.com")
    monkeypatch.setenv("SMTP_FROM", "smtp-login@example.com")
    result = await send_escalation_emails(**_kwargs())
    assert result["customer"] == "Succeeded"
    assert _Smtp.messages[0]["From"] == formataddr(("Agent Red", "smtp-login@example.com"))
    assert _Smtp.messages[0]["Sender"] == "smtp-login@example.com"
    assert _Smtp.envelopes[0] == "smtp-login@example.com"


@pytest.mark.asyncio
async def test_crlf_in_customer_from_cannot_add_a_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    monkeypatch.setenv("SMTP_FROM", "smtp-login@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM", "support@example.com\r\nBcc: evil@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM_NAME", "Support")
    result = await send_escalation_emails(**_kwargs())
    assert result["customer"] == "failed"
    assert result["agent"] == "Succeeded"
    assert len(_Smtp.messages) == 1
    assert "evil@example.com" not in _Smtp.messages[0].as_string()
    assert _Smtp.messages[0]["To"] == "agent@example.com"


@pytest.mark.asyncio
async def test_login_notification_uses_smtp_before_acs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    called = {"acs": 0}

    async def send_acs(**kwargs: object) -> str:
        called["acs"] += 1
        return "Succeeded"

    monkeypatch.setattr("src.multi_tenant.alert_delivery.send_acs_email", send_acs)
    with caplog.at_level(logging.INFO):
        await send_login_notification(
            admin_email="admin@example.com",
            notification_email=None,
            client_ip="10.0.0.1",
            user_agent="Test",
        )
    assert called["acs"] == 0
    assert len(_Smtp.messages) == 1
    assert _Smtp.messages[0]["To"] == "admin@example.com"
    assert "provider=SMTP" in caplog.text


@pytest.mark.asyncio
async def test_login_notification_falls_back_to_acs(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mail(monkeypatch)
    _set_smtp(monkeypatch)
    _Smtp.fail = True
    monkeypatch.setenv("ACS_CONNECTION_STRING", "endpoint=https://example.communication.azure.com/;accesskey=fake")
    sent: dict[str, object] = {}

    async def send_acs(**kwargs: object) -> str:
        sent["kwargs"] = kwargs
        return "Succeeded"

    monkeypatch.setattr("src.multi_tenant.alert_delivery.send_acs_email", send_acs)
    await send_login_notification(
        admin_email="admin@example.com",
        notification_email="alerts@example.com",
        client_ip="10.0.0.1",
        user_agent="Test",
    )
    assert sent["kwargs"]["to_email"] == "alerts@example.com"
