# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Escalation mail reads one sender, and ACS_SENDER_ADDRESS overrides it."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.chat.escalation_email import send_escalation_emails
from src.multi_tenant.alert_delivery import acs_sender_address

_LINKED = "DoNotReply@00000000-0000-0000-0000-000000000000.azurecomm.net"


def test_default_sender_is_the_linked_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACS_SENDER_ADDRESS", raising=False)
    sender = acs_sender_address()
    assert ".us1" not in sender
    assert sender == _LINKED
    source = Path("src/multi_tenant/alert_delivery.py").read_text(encoding="utf-8")
    assert source.count("00000000-0000-0000-0000-000000000000") == 1
    assert ".us1.azurecomm.net" not in source


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
async def test_setting_overrides_customer_and_agent_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ACS_CONNECTION_STRING",
        "endpoint=https://example.communication.azure.com/;accesskey=fake",
    )
    monkeypatch.setenv("ACS_SENDER_ADDRESS", "DoNotReply@example.azurecomm.net")
    sent: list[str] = []

    class _Poller:
        def result(self, timeout: int = 60) -> object:
            return type("Result", (), {"status": "Succeeded"})()

    class _Client:
        def begin_send(self, message: dict[str, object]) -> _Poller:
            sent.append(str(message["senderAddress"]))
            return _Poller()

    monkeypatch.setattr(
        "azure.communication.email.EmailClient.from_connection_string",
        lambda *args, **kwargs: _Client(),
    )

    result = await send_escalation_emails(**_kwargs())

    assert result == {"customer": "Succeeded", "agent": "Succeeded"}
    assert sent == [
        "DoNotReply@example.azurecomm.net",
        "DoNotReply@example.azurecomm.net",
    ]
