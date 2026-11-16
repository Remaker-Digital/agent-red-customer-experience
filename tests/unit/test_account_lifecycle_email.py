"""Verify branded account lifecycle emails at the transport boundary.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.multi_tenant import access_expiry_email, alert_delivery, smtp_tls, trial_expiry_email, welcome_email
from src.multi_tenant import email_verification, repositories


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "mailer@example.invalid")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM", "mailer@example.invalid")
    monkeypatch.delenv("AZURE_COMM_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("STANDALONE_ADMIN_URL", "https://example.invalid/admin/standalone/?source=email")
    send = Mock()
    monkeypatch.setattr(smtp_tls, "send_verified_smtp", send)
    return send


@pytest.mark.parametrize(
    ("send_warning", "subject"),
    [
        (access_expiry_email.send_access_expiry_warning, "Agent Red: Your access expires tomorrow"),
        (trial_expiry_email.send_trial_expiry_warning, "Agent Red: Your trial ends tomorrow"),
    ],
)
async def test_expiry_warning_keeps_the_scoped_admin_link_in_both_places(smtp, send_warning, subject):
    assert await send_warning("merchant@example.invalid", "demo-account", "1d") is True

    smtp.assert_called_once()
    message = smtp.call_args.args[0]
    assert message["Subject"] == subject
    assert message["To"] == "merchant@example.invalid"
    html = message.get_payload()[0].get_payload(decode=True).decode()
    url = "https://example.invalid/admin/standalone/?source=email&amp;tenant=demo-account"
    assert f'href="{url}"' in html
    assert f"{url}</span>" in html
    assert "Or paste this link into your browser:" in html
    assert html.count("Email generated:") == 1


async def test_welcome_email_uses_the_same_account_link_on_smtp_and_provider_fallback(monkeypatch, smtp):
    recipient = "merchant@example.invalid"
    assert await welcome_email.send_welcome_email(recipient, "demo-account", tier="professional") is True

    smtp.assert_called_once()
    message = smtp.call_args.args[0]
    assert message["Subject"] == "Agent Red: Your Account Is Ready"
    assert message["To"] == recipient
    html = message.get_payload()[0].get_payload(decode=True).decode()
    url = "https://example.invalid/admin/standalone/?source=email&amp;tenant=demo-account"
    assert f'href="{url}"' in html
    assert f"{url}</span>" in html
    assert "Account ID:" in html
    assert "Professional" in html

    smtp.side_effect = OSError("test mail transport unavailable")
    monkeypatch.setenv("AZURE_COMM_CONNECTION_STRING", "synthetic-test-value")
    fallback = AsyncMock(return_value="Succeeded")
    monkeypatch.setattr(alert_delivery, "send_acs_email", fallback)
    assert await welcome_email.send_welcome_email(recipient, "demo-account", tier="professional") is True

    fallback.assert_awaited_once()
    _, fallback_recipient, fallback_subject, fallback_html = fallback.await_args.args
    assert fallback_recipient == recipient
    assert fallback_subject == message["Subject"]
    assert f'href="{url}"' in fallback_html
    assert f"{url}</span>" in fallback_html
    assert fallback_html.count("Email generated:") == 1


async def test_verification_email_has_a_branded_subject_and_copyable_link(monkeypatch):
    tenant_id = "demo-account"
    recipient = "merchant@example.invalid"
    create_token = AsyncMock()
    tenant_read = AsyncMock(return_value={"id": tenant_id})
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(repositories, "VerificationTokenRepository", lambda: SimpleNamespace(create_token=create_token))
    monkeypatch.setattr(repositories, "TenantRepository", lambda: SimpleNamespace(read=tenant_read))
    monkeypatch.setattr(email_verification, "_is_rate_limited", lambda _: False)
    monkeypatch.setattr(email_verification, "_send_verification_email", deliver)
    request = SimpleNamespace(
        client=None, headers={}, url=SimpleNamespace(scheme="https", hostname="example.invalid"),
    )

    await email_verification.request_verification(
        email_verification.VerifyEmailRequest(tenant_id=tenant_id, email=recipient), request,
    )

    create_token.assert_awaited_once()
    deliver.assert_awaited_once()
    token = create_token.await_args.kwargs["token_id"]
    to_email, subject, html = deliver.await_args.args
    assert to_email == recipient
    assert subject == "Agent Red: Verify Your Email Address"
    url = f"https://example.invalid/api/auth/verify-email/confirm?token={token}"
    assert f'href="{url}"' in html
    assert f"{url}</span>" in html
    assert html.count("Email generated:") == 1
