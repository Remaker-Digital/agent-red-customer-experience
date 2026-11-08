"""Shared GOV-18 display-value helpers for admin Playwright e2e.

Card-scoped assertions bind visible KPI text to mock API payloads so bare
``get_by_text("142")`` presence checks cannot rubber-stamp decorative numbers.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import math
from typing import Any

from playwright.sync_api import Locator, Page, expect


def agent_display_label(agent: str) -> str:
    """Mirror ``agentDisplayLabel`` title-case fallback (hyphen → space)."""
    return " ".join(part.capitalize() for part in agent.replace("-", " ").split())


def format_locale_int(value: int | float) -> str:
    """Match ``Number#toLocaleString()`` for typical CI en-US integers."""
    return f"{int(value):,}"


def js_round(value: float) -> int:
    """Match JavaScript ``Math.round`` (half rounds toward +∞)."""
    return int(math.floor(value + 0.5))


def expected_stat_displays(summary: dict[str, Any]) -> dict[str, str]:
    """Derive Dashboard/Analytics StatCard strings from an analytics summary dict."""
    total = int(summary["totalConversations"])
    resolution = float(summary["resolutionRate"])
    escalation = float(summary["escalationRate"])
    return {
        "total_conversations": format_locale_int(total),
        "avg_response_time": f"{summary['avgResponseTime']}s",
        "resolution_rate": f"{resolution * 100:.1f}%",
        "resolved_detail": f"{format_locale_int(js_round(total * resolution))} resolved",
        "customer_satisfaction": f"{summary['customerSatisfaction']}/5",
        "escalation_rate": f"{escalation * 100:.1f}%",
        "escalated_detail": f"{format_locale_int(summary['escalationCount'])} escalated",
    }


def stat_card(page: Page, label: str) -> Locator:
    """Locate a Mantine Paper StatCard that contains the given label text."""
    return page.locator("main .mantine-Paper-root").filter(has_text=label).first


def expect_stat_card(
    page: Page,
    *,
    label: str,
    value: str,
    detail: str | None = None,
) -> None:
    """Assert label, value, and optional detail appear together on one card."""
    card = stat_card(page, label)
    expect(card).to_be_visible()
    expect(card.get_by_text(label, exact=False).first).to_be_visible()
    expect(card.get_by_text(value, exact=True).first).to_be_visible()
    if detail is not None:
        expect(card.get_by_text(detail, exact=True).first).to_be_visible()


def topic_row(page: Page, label: str) -> Locator:
    """Locate a topic/intent row (table or compact Group) by display label."""
    table_row = page.locator("main table tbody tr").filter(has_text=label)
    if table_row.count() > 0:
        return table_row.first
    return page.locator("main .mantine-Group-root, main [class*='Group']").filter(
        has_text=label
    ).first


def expect_topic_row(
    page: Page,
    *,
    agent: str,
    invocation_count: int,
    percentage: float | None = None,
) -> None:
    """Assert topic label is paired with its mock count (and optional %)."""
    label = agent_display_label(agent)
    row = topic_row(page, label)
    expect(row).to_be_visible()
    expect(row.get_by_text(label, exact=True).first).to_be_visible()
    expect(row.get_by_text(format_locale_int(invocation_count), exact=True).first).to_be_visible()
    if percentage is not None:
        # Match JS template `${n}%` coercion (26.0 → "26%", 45.6 → "45.6%").
        as_float = float(percentage)
        pct = (
            f"{int(as_float)}%"
            if as_float == int(as_float)
            else f"{as_float}%"
        )
        expect(row.get_by_text(pct, exact=True).first).to_be_visible()


def conversation_card(page: Page, customer_name: str) -> Locator:
    """Locate a conversation card/row by customer name (Dashboard Paper or Inbox item)."""
    paper = page.locator("main .mantine-Paper-root").filter(has_text=customer_name)
    if paper.count() > 0:
        return paper.first
    item = page.locator("main [role='button']").filter(has_text=customer_name)
    if item.count() > 0:
        return item.first
    return page.locator("main div").filter(has_text=customer_name).first


def expect_conversation_card(
    page: Page,
    *,
    customer_name: str,
    message_count: int,
    status: str | None = None,
) -> None:
    """Assert customer name is paired with message count (and optional status)."""
    card = conversation_card(page, customer_name)
    expect(card).to_be_visible()
    expect(card.get_by_text(customer_name, exact=True).first).to_be_visible()
    expect(
        card.get_by_text(f"{message_count} messages", exact=True).first
    ).to_be_visible()
    if status is not None:
        expect(card.get_by_text(status, exact=True).first).to_be_visible()


def labeled_textbox(page: Page, label: str) -> Locator:
    """Locate a Mantine text/number input by accessible name (not bare label text).

    ``get_by_label(label).first`` can resolve to a non-input when the visible
    label contains nested controls (SuggestionBadge, HelpTooltip). Prefer the
    textbox role so assertions target the real form control.
    """
    return page.get_by_role("textbox", name=label).first


def expect_number_input_value(
    page: Page,
    *,
    label: str,
    value: int | str,
    suffix: str | None = None,
) -> None:
    """Assert a Mantine NumberInput shows ``value`` plus optional ``suffix``.

    Mantine includes ``suffix`` (e.g. ``" minutes"``, ``" days"``) in the
    input's displayed value — that is intentional UX, not test noise.
    """
    expected = f"{value}{suffix}" if suffix is not None else str(value)
    expect(labeled_textbox(page, label)).to_have_value(expected)
