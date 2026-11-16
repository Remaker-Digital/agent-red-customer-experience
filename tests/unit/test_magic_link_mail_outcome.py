import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.multi_tenant import alert_delivery, magic_link_auth, repositories, security_hardening


@pytest.mark.parametrize("role", ["owner", "member"])
@pytest.mark.parametrize("accepted", [True, False])
async def test_magic_link_request_logs_transport_outcome(monkeypatch, caplog, role, accepted):
    tenant = "tenant-test"
    email = "owner@example.com"
    owner = {"id": tenant, "customer_email": email, "display_name": "Example <Studio> & Co"}
    member = {"id": "member-test", "tenant_id": tenant} if role == "member" else None
    tokens = SimpleNamespace(create_token=AsyncMock())
    monkeypatch.setattr(
        repositories, "TeamMemberRepository", Mock(return_value=SimpleNamespace(find_by_email=AsyncMock(return_value=member)))
    )
    monkeypatch.setattr(
        repositories, "TenantRepository", Mock(return_value=SimpleNamespace(read=AsyncMock(return_value=owner)))
    )
    monkeypatch.setattr(repositories, "VerificationTokenRepository", Mock(return_value=tokens))
    send = AsyncMock(return_value=accepted)
    monkeypatch.setattr(magic_link_auth, "_send_magic_link_email", send)
    request = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.1"),
        headers={},
        url=SimpleNamespace(scheme="https", hostname="staging.example.com"),
    )

    with caplog.at_level(logging.INFO, logger=magic_link_auth.__name__):
        response = await magic_link_auth.request_magic_link(
            magic_link_auth.MagicLinkRequest(email=email, tenant=tenant), request
        )

    assert response.message == magic_link_auth.MagicLinkRequestResponse().message
    send.assert_awaited_once()
    body = send.await_args.args[1]
    assert "Account: <strong>Example &lt;Studio&gt; &amp; Co</strong>" in body
    assert "Or paste this link into your browser:" in body
    tokens.create_token.assert_awaited_once()
    assert tokens.create_token.await_args.kwargs["code_challenge_id"] == response.challenge_id
    assert tokens.create_token.await_args.kwargs["token_id"] != response.challenge_id
    if accepted:
        assert f"Magic link sent ({role})" in caplog.text
        assert f"Magic link email failed ({role})" not in caplog.text
    else:
        assert f"Magic link sent ({role})" not in caplog.text
        assert f"Magic link email failed ({role})" in caplog.text
        assert email not in caplog.text


async def test_sign_in_code_verification_uses_the_issued_challenge_without_an_ip_counter(monkeypatch):
    tenant = "tenant-test"
    email = "owner@example.invalid"
    owner = {"id": tenant, "customer_email": email, "display_name": "Example Studio"}
    tokens = SimpleNamespace(create_token=AsyncMock(), consume_token_by_code=AsyncMock(return_value=None))
    monkeypatch.setattr(repositories, "VerificationTokenRepository", lambda: tokens)
    monkeypatch.setattr(
        repositories, "TeamMemberRepository", lambda: SimpleNamespace(find_by_email=AsyncMock(return_value=None))
    )
    monkeypatch.setattr(repositories, "TenantRepository", lambda: SimpleNamespace(read=AsyncMock(return_value=owner)))
    monkeypatch.setattr(security_hardening, "get_rate_limit_backend", lambda: pytest.fail("unexpected IP limiter"))
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(magic_link_auth, "_send_magic_link_email", send)
    request = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.1"),
        headers={},
        url=SimpleNamespace(scheme="https", hostname="staging.example.com"),
    )

    responses = [
        await magic_link_auth.request_magic_link(magic_link_auth.MagicLinkRequest(email=email, tenant=tenant), request)
        for _ in range(4)
    ]

    for response in responses:
        code = magic_link_auth.MagicLinkCodeVerifyRequest(tenant=tenant, code="123456", challenge_id=response.challenge_id)
        assert (await magic_link_auth.verify_magic_link_code(code)).status_code == 400
        assert tokens.consume_token_by_code.await_args.kwargs["challenge_id"] == response.challenge_id

    assert send.await_count == tokens.create_token.await_count == 4
    assert len({response.challenge_id for response in responses}) == 4


async def test_old_sign_in_page_without_challenge_must_request_a_fresh_code(monkeypatch):
    tokens = SimpleNamespace(consume_token_by_code=AsyncMock())
    monkeypatch.setattr(repositories, "VerificationTokenRepository", lambda: tokens)

    request = magic_link_auth.MagicLinkCodeVerifyRequest(tenant="tenant-test", code="123456")
    response = await magic_link_auth.verify_magic_link_code(request)

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "missing_challenge",
        "message": "Request a new sign-in code to continue.",
    }
    tokens.consume_token_by_code.assert_not_awaited()


async def test_magic_link_transport_sends_the_branded_subject(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("AZURE_COMM_CONNECTION_STRING", "synthetic-test-value")
    deliver = AsyncMock(return_value="Succeeded")
    monkeypatch.setattr(alert_delivery, "send_acs_email", deliver)

    assert await magic_link_auth._send_magic_link_email("member@example.invalid", "<p>Sign in</p>")

    deliver.assert_awaited_once_with(
        "synthetic-test-value", "member@example.invalid", "Agent Red: Sign In Link", "<p>Sign in</p>",
    )
