# © 2026 Michael Palmeter. All rights reserved.
"""v2.0.3 mail header and transcript checks."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.escalation_handler import EscalationHandlerAgent
from src.chat.escalation_email import _format_transcript_html, send_escalation_emails
from src.chat.pipeline.critic_escalation import _lookup_store_name
from src.multi_tenant.login_notification import send_login_notification


def test_transcript_hides_a_customer_verification_code():
    html = _format_transcript_html(
        [
            {"role": "customer", "content": "482913"},
            {"role": "customer", "content": "My order is 482913"},
            {"role": "assistant", "content": "482913"},
        ]
    )
    assert "[verification code]" in html
    assert "My order is 482913" in html
    assert html.count("482913") == 2


@pytest.mark.unit
async def test_escalation_rejects_a_line_break_in_store_name_or_category(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_FROM", "from@example.com")
    monkeypatch.setenv("SMTP_CUSTOMER_FROM", "support@example.com")
    monkeypatch.setenv("SMTP_USERNAME", "user")
    with patch("src.chat.escalation_email._send_smtp_sync") as smtp:
        result = await send_escalation_emails(
            tenant_id="tenant-1",
            conversation_id="conv-12345678",
            customer_email="customer@example.com",
            agent_email="agent@example.com",
            messages=[],
            store_name="Shop\nBcc: bad@example.com",
            reason="help",
            category="support",
            urgency="low",
        )
    assert result["customer"] == "Succeeded"
    assert result["agent"] == "Succeeded"
    subjects = [call.kwargs["subject"] for call in smtp.call_args_list]
    assert "Agent Red: Support Request Escalated (Support)" in subjects
    assert all("\n" not in subject and "Bcc" not in subject for subject in subjects)

    with patch("src.chat.escalation_email._send_smtp_sync") as smtp:
        result = await send_escalation_emails(
            tenant_id="tenant-1",
            conversation_id="conv-12345678",
            customer_email="customer@example.com",
            agent_email="agent@example.com",
            messages=[],
            store_name="Shop",
            reason="help",
            category="support\nBcc: bad@example.com",
            urgency="low",
        )
    assert result == {"customer": "failed", "agent": "failed"}
    smtp.assert_not_called()


@pytest.mark.unit
async def test_login_notification_rejects_a_line_break_in_the_recipient(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    with patch(
        "src.multi_tenant.login_notification._send_login_smtp",
        new_callable=AsyncMock,
    ) as smtp:
        await send_login_notification(
            admin_email="admin@example.com",
            notification_email="mike@example.com\nBcc: bad@example.com",
            client_ip="127.0.0.1",
            user_agent="test",
        )
    smtp.assert_not_called()


@pytest.mark.unit
async def test_unknown_escalation_category_falls_back():
    class _Completions:
        async def create(self, **kwargs):
            body = json.dumps({"category": "billing", "reason": "need a person", "urgency": "low"})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
            )

    agent = EscalationHandlerAgent(openai_client=SimpleNamespace(chat=SimpleNamespace(completions=_Completions())))
    result = await agent.process({"message": "I need a person"}, {})
    assert result["category"] == "general_inquiry"

    class _Allowed:
        async def create(self, **kwargs):
            body = json.dumps({"category": "sales"})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
            )

    agent = EscalationHandlerAgent(openai_client=SimpleNamespace(chat=SimpleNamespace(completions=_Allowed())))
    result = await agent.process({"message": "I want to buy"}, {})
    assert result["category"] == "sales"


@pytest.mark.unit
async def test_store_name_lookup_logs_a_failure(caplog):
    with patch(
        "src.multi_tenant.repository.TenantRepository.read",
        new_callable=AsyncMock,
        side_effect=TypeError("missing document id"),
    ):
        assert await _lookup_store_name("tenant-1") == "Support"
    assert "Store name lookup failed" in caplog.text


@pytest.mark.unit
async def test_store_name_uses_store_name_or_name():
    with patch(
        "src.multi_tenant.repository.TenantRepository.read",
        new_callable=AsyncMock,
        return_value={"display_name": "Provisioned Shop"},
    ) as read:
        assert await _lookup_store_name("tenant-1") == "Provisioned Shop"
    read.assert_awaited_with("tenant-1", "tenant-1")

    with patch(
        "src.multi_tenant.repository.TenantRepository.read",
        new_callable=AsyncMock,
        return_value={"name": "Named Shop"},
    ):
        assert await _lookup_store_name("tenant-1") == "Named Shop"
