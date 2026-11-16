import re
from email.utils import parseaddr
from html import unescape
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.app import standalone_auth
from src.multi_tenant import smtp_tls


@pytest.mark.parametrize(
    "send_mail",
    [standalone_auth._send_admin_reset_email, standalone_auth._send_admin_password_changed_email],
)
@pytest.mark.parametrize(
    ("current", "legacy", "username", "expected"),
    [
        ("support@example.com", None, "smtp@example.com", "support@example.com"),
        ("support@example.com", "old@example.com", "smtp@example.com", "support@example.com"),
        (None, "old@example.com", "smtp@example.com", "old@example.com"),
        (None, None, "smtp@example.com", "smtp@example.com"),
        (None, None, "", "noreply@agentred.com"),
    ],
)
def test_standalone_recovery_uses_configured_sender(monkeypatch, send_mail, current, legacy, username, expected):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", username)
    monkeypatch.setenv("SMTP_PASSWORD", "synthetic")
    for name, value in (("SMTP_FROM", current), ("SMTP_FROM_ADDRESS", legacy)):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    transport = Mock()
    monkeypatch.setattr(smtp_tls, "send_verified_smtp", transport)

    assert send_mail("owner@example.com", "https://example.com/recovery") is True

    transport.assert_called_once()
    message = transport.call_args.args[0]
    assert parseaddr(message["From"])[1] == expected
    assert message["To"] == "owner@example.com"
    parts = [part.get_payload(decode=True).decode("utf-8") for part in message.get_payload()]
    assert all("The configured admin password is unchanged." in part for part in parts)
    html = message.get_payload()[1].get_payload(decode=True).decode("utf-8")
    assert 'alt="Agent Red Customer Experience"' in html
    assert "To unsubscribe" not in html
    assert all("Email generated:" in part for part in parts)
    assert "Or paste this link into your browser:" in html
    if send_mail is standalone_auth._send_admin_password_changed_email:
        assert message["Subject"] == "Agent Red: Admin Access Recovered"
    else:
        assert message["Subject"] == "Agent Red: Recover Your Admin Access"
        assert all("temporary admin access" in part.lower() for part in parts)


@pytest.mark.parametrize(
    ("requested_query", "expected_tenant"),
    [
        ("?tenant=17dc6c80-5de3-4d94-b10f-58c285e91fe6", "17dc6c80-5de3-4d94-b10f-58c285e91fe6"),
        ("?tenant=%2F%2Fexample.invalid&next=https%3A%2F%2Fexample.invalid", None),
        ("", None),
    ],
)
def test_standalone_recovery_returns_to_the_requested_tenant(tmp_path, monkeypatch, requested_query, expected_tenant):
    admin_path = "/admin/standalone/"
    expected_query = {"tenant": [expected_tenant]} if expected_tenant else {}
    admin_url = f"{admin_path}?tenant={expected_tenant}" if expected_tenant else admin_path
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("Standalone SPA", encoding="utf-8")
    monkeypatch.setattr(standalone_auth, "_admin_standalone_dist", tmp_path)
    monkeypatch.setattr(standalone_auth, "_ADMIN_RESET_EMAIL", "owner@example.com")
    monkeypatch.setattr(standalone_auth, "_ADMIN_HMAC_KEY", "synthetic-test-signing-key")
    password_hash = standalone_auth._hash_password("synthetic existing password")
    monkeypatch.setattr(standalone_auth, "_admin_password_hash", password_hash)
    monkeypatch.setattr(standalone_auth, "_admin_used_reset_nonces", set())
    send_reset = Mock()
    send_confirmation = Mock()
    monkeypatch.setattr(standalone_auth, "_send_admin_reset_email", send_reset)
    monkeypatch.setattr(standalone_auth, "_send_admin_password_changed_email", send_confirmation)
    app = FastAPI()
    standalone_auth.mount_standalone_admin(app)

    with TestClient(app, base_url="https://agentred.example") as client:
        login_path = admin_path.rstrip("/") if expected_tenant else admin_path
        login = client.get(login_path + requested_query)
        forgot_link = re.search(r'href="([^"]+)"[^>]*>Recover admin access', login.text)
        assert forgot_link is not None
        forgot = unescape(forgot_link[1])
        assert parse_qs(urlsplit(forgot).query) == expected_query
        forgot_form = client.get(forgot)
        forgot_action = unescape(re.search(r'<form method="POST" action="([^"]+)"', forgot_form.text)[1])
        invalid_request = client.post(forgot_action, data={"email": "owner@example.com"})
        assert invalid_request.status_code == 403
        assert f'action="{forgot_action}"' in invalid_request.text
        request = client.post(
            forgot_action,
            data={"email": "owner@example.com", "csrf_token": client.cookies["agentred_csrf"]},
        )
        assert "Check your email" in request.text
        assert "temporary access link" in request.text
        assert f'href="{admin_url}"' in request.text
        send_reset.assert_called_once()
        reset_url = urlsplit(send_reset.call_args.args[1])
        assert reset_url.netloc == "agentred.example"
        token_query = parse_qs(reset_url.query)
        token = token_query.pop("token")[0]
        assert token_query == expected_query
        reset_form = client.get(reset_url.geturl())
        assert "<h1>Recover admin access</h1>" in reset_form.text
        assert "The configured admin password is unchanged." in reset_form.text
        assert "Set new password" not in reset_form.text
        reset_action = unescape(re.search(r'<form method="POST" action="([^"]+)"', reset_form.text)[1])
        invalid_submit = client.post(reset_action, data={"token": token})
        assert invalid_submit.status_code == 400
        assert f'href="{forgot}"' in invalid_submit.text
        invalid_link = client.get(f"{reset_action}{'&' if expected_tenant else '?'}token=invalid")
        assert invalid_link.status_code == 400
        assert f'href="{forgot}"' in invalid_link.text
        recovery_data = {"token": token, "csrf_token": client.cookies["agentred_csrf"]}
        submit = client.post(reset_action, data=recovery_data, follow_redirects=False)
        assert submit.status_code == 303
        assert submit.headers["location"] == admin_url
        assert '<button type="submit">Restore admin access</button>' in reset_form.text
        assert 'type="password"' not in reset_form.text
        assert 'name="new_password"' not in reset_form.text
        assert 'name="confirm_password"' not in reset_form.text
        assert "temporary admin access" in reset_form.text
        assert client.get(submit.headers["location"]).text == "Standalone SPA"
        assert standalone_auth._admin_password_hash == password_hash
        send_confirmation.assert_called_once_with("owner@example.com", f"https://agentred.example{forgot}")
