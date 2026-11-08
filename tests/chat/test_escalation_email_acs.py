# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""E1. With ACS configured, the escalation email is attempted, not skipped."""

from __future__ import annotations

import pytest

from src.chat.escalation_email import send_escalation_emails


def _kwargs() -> dict[str, object]:
    return {
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


@pytest.mark.asyncio
async def test_escalation_email_is_skipped_without_acs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACS_CONNECTION_STRING", raising=False)
    result = await send_escalation_emails(**_kwargs())
    assert result == {"customer": "skipped", "agent": "skipped"}


@pytest.mark.asyncio
async def test_configured_acs_attempts_the_escalation_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ACS_CONNECTION_STRING",
        "endpoint=https://example.communication.azure.com/;accesskey=fake",
    )
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

    result = await send_escalation_emails(**_kwargs())

    assert result["customer"] != "skipped"
    assert result["agent"] != "skipped"
    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    assert sent["customer"]["to_email"] == "ada@example.com"
    assert sent["agent"]["replyTo"] == [{"address": "ada@example.com"}]
