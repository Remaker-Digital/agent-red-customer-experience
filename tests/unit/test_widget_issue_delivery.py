import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.chat import endpoints
from src.chat.models import IssueReportRequest
from src.multi_tenant import alert_delivery, repository


@pytest.fixture
def issue_dependencies(monkeypatch):
    session = Mock(
        get_conversation=AsyncMock(),
        conversation_repo=Mock(append_message=AsyncMock()),
    )
    send_alert = AsyncMock()
    monkeypatch.setattr(endpoints, "_get_session", lambda: session)
    monkeypatch.setattr(repository, "AuditLogRepository", lambda: Mock(log_event=AsyncMock()))
    monkeypatch.setattr(alert_delivery, "send_escalation_alert", send_alert)
    return session, send_alert


@pytest.mark.parametrize("channels", [1, 0, None])
async def test_issue_report_status_distinguishes_saved_and_delivered(issue_dependencies, channels):
    session, send_alert = issue_dependencies
    send_alert.return_value = None if channels is None else SimpleNamespace(notification_channels_succeeded=channels)
    ctx = SimpleNamespace(tenant_id="tenant-synthetic")
    issue = IssueReportRequest(issue_type="other", details="Synthetic report before first chat message")

    result = await endpoints.report_issue("synthetic-conversation", issue, ctx)

    assert result.accepted is True
    assert result.notification_delivered is bool(channels)
    session.conversation_repo.append_message.assert_awaited_once()
    send_alert.assert_awaited_once()


async def test_issue_report_returns_failure_when_it_cannot_be_saved(issue_dependencies, caplog):
    session, send_alert = issue_dependencies
    session.conversation_repo.append_message.side_effect = RuntimeError("synthetic storage failure")
    ctx = SimpleNamespace(tenant_id="tenant-synthetic")

    with pytest.raises(HTTPException) as error:
        await endpoints.report_issue("synthetic-conversation", IssueReportRequest(issue_type="other"), ctx)

    assert error.value.status_code == 503
    send_alert.assert_not_awaited()
    assert "Failed to persist issue report" in caplog.text
    assert "synthetic-conversation" not in caplog.text


async def test_issue_report_returns_failure_when_storage_is_unavailable(issue_dependencies):
    session, send_alert = issue_dependencies
    session.conversation_repo = None
    ctx = SimpleNamespace(tenant_id="tenant-synthetic")

    with pytest.raises(HTTPException) as error:
        await endpoints.report_issue("synthetic-conversation", IssueReportRequest(issue_type="other"), ctx)

    assert error.value.status_code == 503
    send_alert.assert_not_awaited()


async def test_issue_report_does_not_count_log_fallback_as_notification(issue_dependencies):
    _, send_alert = issue_dependencies
    service = alert_delivery.AlertDeliveryService()
    channel = Mock(
        name="email",
        deliver=AsyncMock(return_value=alert_delivery.ChannelResult("email", False, "synthetic failure")),
    )
    channel.name = "email"
    service.register_channel(channel)

    async def deliver(**kwargs):
        alert = alert_delivery.create_alert(
            tenant_id=kwargs["tenant_id"],
            alert_type=alert_delivery.AlertType.ESCALATION,
            title="Synthetic issue report",
            message="Synthetic content",
        )
        return await service.deliver_alert(alert)

    send_alert.side_effect = deliver
    result = await endpoints.report_issue(
        "synthetic-conversation",
        IssueReportRequest(issue_type="other"),
        SimpleNamespace(tenant_id="tenant-synthetic"),
    )

    assert result.accepted is True
    assert result.notification_delivered is False

@pytest.fixture
def fast_notification_wait(monkeypatch):
    async def wait_for(task, timeout):
        return await asyncio.wait_for(task, 0.01)

    async def wait(tasks, timeout):
        return await asyncio.wait(tasks, timeout=0.01)

    monkeypatch.setattr(endpoints, "asyncio", SimpleNamespace(
        create_task=asyncio.create_task, wait_for=wait_for, wait=wait,
    ))
    tasks = set()
    monkeypatch.setattr(endpoints, "_background_tasks", tasks)
    return tasks


async def test_issue_report_timeout_keeps_webhook_delivery_running(issue_dependencies, fast_notification_wait):
    _, send_alert = issue_dependencies
    service = alert_delivery.AlertDeliveryService()
    release_email = asyncio.Event()
    webhook_finished = asyncio.Event()

    async def deliver_email(_alert):
        await release_email.wait()
        return alert_delivery.ChannelResult("email", False, "synthetic unavailable email")

    async def deliver_webhook(_alert):
        webhook_finished.set()
        return alert_delivery.ChannelResult("webhook", True)

    webhook = AsyncMock(side_effect=deliver_webhook)
    service.register_channel(SimpleNamespace(name="email", deliver=deliver_email))
    service.register_channel(SimpleNamespace(name="webhook", deliver=webhook))

    async def deliver(**kwargs):
        return await service.deliver_alert(alert_delivery.create_alert(
            tenant_id=kwargs["tenant_id"],
            alert_type=alert_delivery.AlertType.ESCALATION,
            title="Synthetic issue report",
            message="Synthetic content",
        ))

    send_alert.side_effect = deliver
    try:
        result = await endpoints.report_issue(
            "synthetic-conversation",
            IssueReportRequest(issue_type="other"),
            SimpleNamespace(tenant_id="tenant-synthetic"),
        )
        assert result.accepted is True
        assert result.notification_delivered is False
        webhook.assert_not_awaited()
        release_email.set()
        await asyncio.wait_for(webhook_finished.wait(), timeout=1)
        webhook.assert_awaited_once()
    finally:
        release_email.set()
        await asyncio.gather(*fast_notification_wait)


async def test_issue_report_logs_a_late_delivery_error(issue_dependencies, fast_notification_wait, caplog):
    _, send_alert = issue_dependencies
    release_delivery = asyncio.Event()

    async def deliver(**kwargs):
        await release_delivery.wait()
        raise RuntimeError("synthetic-confidential-provider-error")

    send_alert.side_effect = deliver
    try:
        result = await endpoints.report_issue(
            "synthetic-conversation",
            IssueReportRequest(issue_type="other"),
            SimpleNamespace(tenant_id="tenant-synthetic"),
        )
        assert result.accepted is True
        assert result.notification_delivered is False
        assert len(fast_notification_wait) == 1
    finally:
        release_delivery.set()
        await asyncio.gather(*fast_notification_wait)

    assert not fast_notification_wait
    assert "Issue report notification failed" in caplog.text
    assert result.issue_id in caplog.text
    assert "synthetic-confidential-provider-error" not in caplog.text
    assert "synthetic-conversation" not in caplog.text
