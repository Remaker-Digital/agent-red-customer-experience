# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""A platform admin gets at most one login notification an hour.

The clock is injected. A capped request still authenticates, and the audit
record is still written.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.responses import JSONResponse

from src.multi_tenant.api_key_audit import get_api_key_audit_buffer
from src.multi_tenant.login_notification import (
    _login_notification_attempts,
    claim_login_notification,
)
from src.multi_tenant.middleware import TenantAuthMiddleware


def _context(email: str) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=True,
        platform_admin_email=email,
        platform_admin_notification_email=None,
        tenant_id="platform-admin",
        auth_method="spa_key",
        team_member_id=None,
    )


def _request() -> MagicMock:
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/api/admin/conversations"
    request.headers = {"user-agent": "TestAgent"}
    request.query_params = {}
    request.scope = {"client": ("203.0.113.9", 1)}
    return request


async def _ok(_request: object) -> JSONResponse:
    return JSONResponse({"ok": True})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    _login_notification_attempts.clear()
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(
        "src.multi_tenant.login_notification._notification_clock",
        lambda: now["t"],
    )
    return now


async def _dispatch(context: SimpleNamespace) -> JSONResponse:
    middleware = TenantAuthMiddleware(app=MagicMock())

    async def authenticate(_request: object) -> SimpleNamespace:
        return context

    middleware._authenticate = authenticate  # type: ignore[method-assign]
    return await middleware.dispatch(_request(), _ok)


@pytest.mark.asyncio
async def test_second_request_inside_the_hour_sends_nothing(
    clock: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def send(**kwargs: object) -> None:
        sent.append(str(kwargs["admin_email"]))

    monkeypatch.setattr("src.multi_tenant.login_notification.send_login_notification", send)
    context = _context("ada@example.com")
    await _dispatch(context)
    await _dispatch(context)
    await asyncio.sleep(0)
    assert sent == ["ada@example.com"]
    assert claim_login_notification("ada@example.com") is False


@pytest.mark.asyncio
async def test_a_failed_send_does_not_retry_inside_the_hour(
    clock: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def send(**kwargs: object) -> None:
        sent.append(str(kwargs["admin_email"]))
        raise RuntimeError("smtp down")

    monkeypatch.setattr("src.multi_tenant.login_notification.send_login_notification", send)
    context = _context("ada@example.com")
    first = await _dispatch(context)
    await asyncio.gather(*[task for task in asyncio.all_tasks() if task is not asyncio.current_task()], return_exceptions=True)
    second = await _dispatch(context)
    await asyncio.sleep(0)
    assert first.status_code == 200
    assert second.status_code == 200
    assert sent == ["ada@example.com"]


@pytest.mark.asyncio
async def test_a_request_after_the_hour_sends(
    clock: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def send(**kwargs: object) -> None:
        sent.append(str(kwargs["admin_email"]))

    monkeypatch.setattr("src.multi_tenant.login_notification.send_login_notification", send)
    context = _context("ada@example.com")
    await _dispatch(context)
    clock["t"] += 3600
    await _dispatch(context)
    await asyncio.sleep(0)
    assert sent == ["ada@example.com", "ada@example.com"]


@pytest.mark.asyncio
async def test_two_admins_are_independent(
    clock: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def send(**kwargs: object) -> None:
        sent.append(str(kwargs["admin_email"]))

    monkeypatch.setattr("src.multi_tenant.login_notification.send_login_notification", send)
    await _dispatch(_context("ada@example.com"))
    await _dispatch(_context("bea@example.com"))
    await asyncio.sleep(0)
    assert sent == ["ada@example.com", "bea@example.com"]


@pytest.mark.asyncio
async def test_a_capped_request_still_authenticates(
    clock: dict[str, float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send(**kwargs: object) -> None:
        return None

    monkeypatch.setattr("src.multi_tenant.login_notification.send_login_notification", send)
    before = len(get_api_key_audit_buffer()._buffer)
    context = _context("ada@example.com")
    first = await _dispatch(context)
    second = await _dispatch(context)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.body == second.body
    assert len(get_api_key_audit_buffer()._buffer) >= before + 2
