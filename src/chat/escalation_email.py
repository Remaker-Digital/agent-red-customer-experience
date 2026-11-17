# © 2026 Michael Palmeter. All rights reserved.
"""Async email-bridge escalation — send transcript emails to customer and agent.

When the AI escalates a conversation, this module sends two emails:

1. **Customer email:** Confirms the escalation, includes the transcript,
   and sets expectations for response time.
2. **Agent email:** Full transcript with Reply-To set to the customer's
   email so the agent can simply hit Reply.

The chat continues after escalation — this is a fork, not a handoff.

WI-3030 / S259 D16/D17.

© 2026 Michael Palmeter. All rights reserved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from html import escape
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transcript formatting
# ---------------------------------------------------------------------------


def _format_transcript_html(messages: list[dict[str, Any]]) -> str:
    """Render conversation messages as an HTML transcript for email."""
    rows: list[str] = []
    for msg in messages:
        role = msg.get("role", "system")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")

        if role == "system":
            continue

        if role in ("user", "customer") and re.fullmatch(r"\d{6}", content.strip()):
            content = "[verification code]"

        label = "Customer" if role in ("user", "customer") else "AI Agent"
        color = "#93c5fd" if role in ("user", "customer") else "#d6d3d1"
        time_str = ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                time_str = dt.strftime("%I:%M %p")
            except (ValueError, TypeError):
                time_str = ""

        rows.append(
            f"<tr>"
            f'<td style="padding:8px 12px;vertical-align:top;color:{color};'
            f'font-weight:600;white-space:nowrap;font-size:13px;">'
            f"{escape(label)}"
            f"{'&nbsp;<span style=&quot;color:#d6d3d1;font-weight:400;&quot;>'
              + escape(time_str) + '</span>' if time_str else ''}"
            f"</td>"
            f'<td style="padding:8px 12px;font-size:13px;color:#f5f5f4;">'
            f"{escape(content)}"
            f"</td>"
            f"</tr>"
        )

    return (
        '<table style="width:100%;border-collapse:collapse;'
        'border:1px solid #44403c;border-radius:8px;overflow:hidden;">'
        f"{''.join(rows)}"
        "</table>"
    )


# ---------------------------------------------------------------------------
# Customer escalation email
# ---------------------------------------------------------------------------


def _build_customer_email_html(
    store_name: str,
    transcript_html: str,
    response_hours: int = 24,
) -> str:
    """Build HTML body for the customer escalation notification."""
    from src.multi_tenant.alert_delivery import format_branded_email

    body = f"""\
<div style="color:#f5f5f4;font-size:14px;line-height:1.6">
  <h2 style="color:#fafaf9;margin:0 0 16px;font-size:20px">{escape(store_name)}</h2>
  <p style="color:#d6d3d1;margin-top:0;">Your support request has been escalated</p>
  <hr style="border:none;border-top:1px solid #44403c;margin:16px 0;">
  <p>A member of our team will review your request and respond directly via email
  within <strong>{response_hours} hours</strong>.</p>
  <p style="color:#d6d3d1;font-size:13px;">If you don't see a reply,
  please check your spam folder.</p>
  <h3 style="color:#e7e5e4;font-size:14px;margin-bottom:8px;">Conversation transcript</h3>
  {transcript_html}
  <hr style="border:none;border-top:1px solid #44403c;margin:16px 0;">
  <p style="color:#d6d3d1;font-size:11px;">This email was sent by
  {escape(store_name)} via Agent Red Customer Experience.</p>
</div>"""
    return format_branded_email(body)


# ---------------------------------------------------------------------------
# Agent escalation email
# ---------------------------------------------------------------------------


def _build_agent_email_html(
    store_name: str,
    customer_email: str,
    transcript_html: str,
    reason: str,
    category: str,
    urgency: str,
    conversation_id: str,
) -> str:
    """Build HTML body for the agent escalation notification."""
    urgency_color = {"high": "#fca5a5", "medium": "#fdba74", "low": "#86efac"}.get(urgency, "#d6d3d1")
    from src.multi_tenant.alert_delivery import format_branded_email

    body = f"""\
<div style="color:#f5f5f4;font-size:14px;line-height:1.6">
  <h2 style="color:#fafaf9;margin:0 0 16px;font-size:20px">Escalation: {escape(store_name)}</h2>
  <p style="color:#d6d3d1;margin-top:0;">A customer conversation has been escalated to you.</p>
  <hr style="border:none;border-top:1px solid #44403c;margin:16px 0;">

  <table style="font-size:13px;margin-bottom:16px;">
    <tr><td style="padding:2px 12px 2px 0;color:#d6d3d1;">Customer</td>
        <td><strong>{escape(customer_email)}</strong></td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#d6d3d1;">Category</td>
        <td>{escape(category)}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#d6d3d1;">Urgency</td>
        <td style="color:{urgency_color};font-weight:600;">{escape(urgency)}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#d6d3d1;">Reason</td>
        <td>{escape(reason)}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#d6d3d1;">Conversation</td>
        <td style="font-family:monospace;font-size:12px;">{escape(conversation_id[:12])}…</td></tr>
  </table>

  <p><strong>Reply directly to this email</strong> to respond to the customer.</p>

  <h3 style="color:#e7e5e4;font-size:14px;margin-bottom:8px;">Conversation transcript</h3>
  {transcript_html}

  <hr style="border:none;border-top:1px solid #44403c;margin:16px 0;">
  <p style="color:#d6d3d1;font-size:11px;">Agent Red Customer Experience: {escape(store_name)}</p>
</div>"""
    return format_branded_email(body)


# ---------------------------------------------------------------------------
# Delivery. SMTP first, then ACS. The SMTP block is local to this file.
# ---------------------------------------------------------------------------


def _header_value_is_safe(value: str) -> bool:
    """A CR or LF in an address or subject can add another email header."""
    return "\r" not in value and "\n" not in value


def _send_smtp_sync(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    sender: str,
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: str | None,
    from_name: str | None = None,
    envelope_from: str | None = None,
) -> None:
    """Blocking SMTP send. Callers run it with asyncio.to_thread.

    A customer email passes from_name and envelope_from so the visible From
    can differ from the login Titan accepts. Other callers leave both unset.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    from src.multi_tenant.smtp_tls import open_verified_smtp

    message = MIMEMultipart("alternative")
    if from_name is None:
        message["From"] = f"Agent Red <{sender}>"
    else:
        message["From"] = formataddr((from_name, sender))
        if envelope_from:
            message["Sender"] = envelope_from
    message["To"] = to_email
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.attach(MIMEText(html_body, "html"))

    def _deliver(server: smtplib.SMTP) -> None:
        if user and password:
            server.login(user, password)
        if envelope_from:
            server.send_message(message, from_addr=envelope_from)
        else:
            server.send_message(message)

    with open_verified_smtp(host, port, timeout=10, username=user, password=password) as server:
        _deliver(server)


async def _send_acs_with_reply(
    conn_str: str,
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: str,
) -> str:
    """ACS send that keeps Reply-To set to the customer."""
    from azure.communication.email import EmailClient
    from azure.core.pipeline.policies import RetryPolicy

    from src.multi_tenant.alert_delivery import acs_sender_address

    retry_policy = RetryPolicy(
        retry_total=2,
        retry_backoff_factor=1,
        retry_backoff_max=5,
    )
    client = EmailClient.from_connection_string(conn_str, retry_policy=retry_policy)
    message = {
        "senderAddress": acs_sender_address(),
        "recipients": {"to": [{"address": to_email}]},
        "content": {"subject": subject, "html": html_body},
        "replyTo": [{"address": reply_to}],
    }

    def _send() -> str:
        poller = client.begin_send(message)
        result = poller.result(timeout=60)
        return getattr(result, "status", "unknown")

    return await asyncio.to_thread(_send)


async def _deliver_escalation(
    *,
    audience: str,
    tenant_id: str,
    conversation_id: str,
    to_email: str,
    subject: str,
    html_body: str,
    reply_to: str | None,
    conn_str: str,
) -> str:
    """Try SMTP, then ACS. Return Succeeded, an ACS status, or failed."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_from = os.environ.get("SMTP_FROM", "")
    customer_address = ""
    customer_name = ""
    customer_login = ""
    if audience == "customer":
        customer_address = os.environ.get("SMTP_CUSTOMER_FROM", "") or smtp_from
        customer_name = os.environ.get("SMTP_CUSTOMER_FROM_NAME", "") or "Agent Red"
        customer_login = os.environ.get("SMTP_USERNAME", "")
        customer_ready = bool(
            customer_address
            and customer_login
            and _header_value_is_safe(customer_address)
            and _header_value_is_safe(customer_name)
            and _header_value_is_safe(customer_login)
        )
        if smtp_host and not customer_ready:
            logger.warning(
                "SMTP customer escalation email skipped: tenant=%s conv=%s",
                tenant_id,
                conversation_id[:8],
            )
        smtp_ready = bool(smtp_host and customer_ready)
    else:
        if smtp_host and not smtp_from:
            logger.warning(
                "SMTP escalation email skipped because SMTP_FROM is not set: tenant=%s conv=%s audience=%s",
                tenant_id,
                conversation_id[:8],
                audience,
            )
        smtp_ready = bool(smtp_host and smtp_from)
    if smtp_ready:
        try:
            await asyncio.to_thread(
                _send_smtp_sync,
                host=smtp_host,
                port=int(os.environ.get("SMTP_PORT", "587")),
                user=customer_login if audience == "customer" else os.environ.get("SMTP_USERNAME", ""),
                password=os.environ.get("SMTP_PASSWORD", ""),
                sender=customer_address if audience == "customer" else smtp_from,
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                reply_to=reply_to,
                from_name=customer_name if audience == "customer" else None,
                envelope_from=customer_login if audience == "customer" else None,
            )
            logger.info(
                "Escalation email sent: tenant=%s conv=%s audience=%s provider=SMTP status=Succeeded",
                tenant_id,
                conversation_id[:8],
                audience,
            )
            return "Succeeded"
        except Exception:
            logger.warning(
                "SMTP escalation email failed: tenant=%s conv=%s audience=%s",
                tenant_id,
                conversation_id[:8],
                audience,
                exc_info=True,
            )

    if conn_str:
        try:
            if reply_to:
                status = await _send_acs_with_reply(conn_str, to_email, subject, html_body, reply_to)
            else:
                from src.multi_tenant.alert_delivery import send_acs_email

                status = await send_acs_email(
                    conn_str=conn_str,
                    to_email=to_email,
                    subject=subject,
                    html_body=html_body,
                )
            logger.info(
                "Escalation email sent: tenant=%s conv=%s audience=%s provider=ACS status=%s",
                tenant_id,
                conversation_id[:8],
                audience,
                status,
            )
            return status
        except Exception:
            logger.warning(
                "ACS escalation email failed: tenant=%s conv=%s audience=%s",
                tenant_id,
                conversation_id[:8],
                audience,
                exc_info=True,
            )
            return "failed"
    return "failed"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_escalation_emails(
    *,
    tenant_id: str,
    conversation_id: str,
    customer_email: str,
    agent_email: str,
    messages: list[dict[str, Any]],
    store_name: str,
    reason: str,
    category: str,
    urgency: str,
    response_hours: int = 24,
) -> dict[str, str]:
    """Send escalation emails to both customer and agent.

    Returns dict with delivery status for each: {"customer": "...", "agent": "..."}.
    Failures are logged but do not raise — escalation should not fail because
    email delivery is down.
    """
    smtp_host = os.environ.get("SMTP_HOST", "")
    conn_str = os.environ.get("ACS_CONNECTION_STRING", "")
    if not smtp_host and not conn_str:
        logger.warning("No email provider configured. Escalation emails skipped")
        return {"customer": "skipped", "agent": "skipped"}

    if not _header_value_is_safe(customer_email) or not _header_value_is_safe(category):
        logger.warning(
            "Rejected escalation emails because a header contains a line break: tenant=%s conv=%s",
            tenant_id,
            conversation_id[:8],
        )
        return {"customer": "failed", "agent": "failed"}
    if not _header_value_is_safe(store_name):
        logger.warning(
            "Escalation store name contained a line break. Using Support. tenant=%s conv=%s",
            tenant_id,
            conversation_id[:8],
        )
        store_name = "Support"

    transcript_html = _format_transcript_html(messages)
    results: dict[str, str] = {}

    # Customer email. A failure here does not stop the agent email.
    try:
        customer_html = _build_customer_email_html(
            store_name=store_name,
            transcript_html=transcript_html,
            response_hours=response_hours,
        )
        results["customer"] = await _deliver_escalation(
            audience="customer",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            to_email=customer_email,
            subject=f"Agent Red: Support Request Escalated ({store_name})",
            html_body=customer_html,
            reply_to=None,
            conn_str=conn_str,
        )
    except Exception:
        results["customer"] = "failed"
        logger.warning(
            "Failed to send customer escalation email: tenant=%s conv=%s",
            tenant_id,
            conversation_id[:8],
            exc_info=True,
        )

    # Agent email. Reply-To stays the customer on SMTP and on ACS.
    try:
        if not _header_value_is_safe(agent_email):
            logger.warning(
                "Rejected the agent escalation email because the address contains a line break: tenant=%s conv=%s",
                tenant_id,
                conversation_id[:8],
            )
            results["agent"] = "failed"
        else:
            agent_html = _build_agent_email_html(
                store_name=store_name,
                customer_email=customer_email,
                transcript_html=transcript_html,
                reason=reason,
                category=category,
                urgency=urgency,
                conversation_id=conversation_id,
            )
            results["agent"] = await _deliver_escalation(
                audience="agent",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                to_email=agent_email,
                subject=f"Agent Red: Escalation [{category}] for {customer_email}",
                html_body=agent_html,
                reply_to=customer_email,
                conn_str=conn_str,
            )
    except Exception:
        results["agent"] = "failed"
        logger.warning(
            "Failed to send agent escalation email: tenant=%s conv=%s",
            tenant_id,
            conversation_id[:8],
            exc_info=True,
        )

    return results
