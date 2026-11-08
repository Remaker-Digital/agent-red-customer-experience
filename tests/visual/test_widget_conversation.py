"""
Widget conversational journey — Strong Playwright (mocked transport).

Covers open → send → SSE reply → multi-turn, plus loading/error paths.
Assertions verify UI→API payloads (method/path/body/headers), not mere
DOM presence. All backends are Playwright route mocks — no live prod.

ADO: lands under ``tests/visual`` (PlaywrightUi / widget-visual matrix in
PR #173). No azure-pipelines.yml change required on develop tip.

Run:
    pytest tests/visual/test_widget_conversation.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import FrameLocator, expect

from tests.visual.conftest import (
    DEV_WIDGET_KEY,
    WidgetChatMocker,
    send_panel_message,
)

pytestmark = pytest.mark.visual


def _msg_log(panel: FrameLocator):
    """Conversation message log — avoids aria-live duplicate text matches."""
    return panel.get_by_role("log", name="Conversation messages")


def _assert_widget_key(call: dict) -> None:
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert headers.get("x-widget-key") == DEV_WIDGET_KEY, (
        f"Expected X-Widget-Key={DEV_WIDGET_KEY!r}, got headers={headers}"
    )


class TestConversationHappyPath:
    """Open widget, send message, receive mocked SSE reply, multi-turn."""

    def test_first_message_posts_conversation_and_message_payload(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """First send must POST /api/chat/conversations then /api/chat/message."""
        user_text = "Where is my order #12345?"
        send_panel_message(conversation_panel, user_text)

        log = _msg_log(conversation_panel)
        expect(log.get_by_text(user_text, exact=True)).to_be_visible(timeout=5_000)
        expect(log.get_by_text("Mocked agent reply for turn one.")).to_be_visible(
            timeout=8_000
        )

        starts = chat_mocker.get_calls(method="POST", path_contains="/api/chat/conversations")
        messages = chat_mocker.get_calls(method="POST", path_contains="/api/chat/message")
        assert len(starts) == 1, f"expected 1 conversation start, got {len(starts)}"
        assert len(messages) == 1, f"expected 1 message POST, got {len(messages)}"

        _assert_widget_key(starts[0])
        _assert_widget_key(messages[0])

        msg_body = messages[0]["body"]
        assert isinstance(msg_body, dict)
        assert msg_body["conversation_id"] == chat_mocker.conversation_id
        assert msg_body["content"] == user_text
        assert isinstance(msg_body.get("idempotency_key"), str)
        assert len(msg_body["idempotency_key"]) > 0

    def test_sse_stream_request_targets_conversation(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """After send, GET /api/chat/stream/{conversation_id} must fire with widget_key."""
        send_panel_message(conversation_panel, "Hello agent")
        expect(
            _msg_log(conversation_panel).get_by_text("Mocked agent reply for turn one.")
        ).to_be_visible(timeout=8_000)

        streams = [
            c
            for c in chat_mocker.calls
            if "/api/chat/stream/" in c["path"] and c["method"] == "GET"
        ]
        assert len(streams) >= 1, "expected at least one SSE stream GET"
        stream = streams[0]
        assert chat_mocker.conversation_id in stream["path"]
        assert "widget_key=" in stream["path"]

    def test_multi_turn_second_message_payload_and_reply(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """Second turn reuses conversation_id and renders a distinct SSE reply."""
        log = _msg_log(conversation_panel)
        send_panel_message(conversation_panel, "First question")
        expect(log.get_by_text("Mocked agent reply for turn one.")).to_be_visible(
            timeout=8_000
        )

        send_panel_message(conversation_panel, "Follow-up question")
        expect(log.get_by_text("Follow-up question", exact=True)).to_be_visible(
            timeout=5_000
        )
        expect(log.get_by_text("Mocked agent reply for turn two.")).to_be_visible(
            timeout=8_000
        )

        starts = chat_mocker.get_calls(method="POST", path_contains="/api/chat/conversations")
        messages = chat_mocker.get_calls(method="POST", path_contains="/api/chat/message")
        assert len(starts) == 1, "conversation should start once for multi-turn"
        assert len(messages) == 2, f"expected 2 message POSTs, got {len(messages)}"

        assert messages[0]["body"]["content"] == "First question"
        assert messages[1]["body"]["content"] == "Follow-up question"
        assert messages[1]["body"]["conversation_id"] == chat_mocker.conversation_id
        assert messages[0]["body"]["idempotency_key"] != messages[1]["body"]["idempotency_key"]

        streams = [c for c in chat_mocker.calls if "/api/chat/stream/" in c["path"]]
        assert len(streams) >= 2, f"expected >=2 SSE connects, got {len(streams)}"


class TestConversationLoadingAndErrors:
    """Loading gate during start; start/send failure surfaces error banner."""

    def test_loading_disables_send_while_conversation_starts(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """While POST /conversations is held pending, Send stays disabled even with text."""
        chat_mocker.hold_conversation_start()

        send_panel_message(conversation_panel, "Pending start")

        # Type again while start is in flight — isLoading must keep Send disabled
        conversation_panel.locator("textarea").fill("typed during load")
        send_btn = conversation_panel.locator('button[aria-label="Send"]')
        expect(send_btn).to_be_disabled(timeout=3_000)

        assert chat_mocker.get_calls(method="POST", path_contains="/api/chat/message") == []
        assert chat_mocker.pending_start_count == 1

        chat_mocker.release_conversation_start()
        expect(
            _msg_log(conversation_panel).get_by_text("Mocked agent reply for turn one.")
        ).to_be_visible(timeout=10_000)

        messages = chat_mocker.get_calls(method="POST", path_contains="/api/chat/message")
        assert len(messages) == 1
        assert messages[0]["body"]["content"] == "Pending start"

    def test_conversation_start_failure_shows_error_and_skips_message(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """500 on conversation start shows locale error; no message POST."""
        chat_mocker.fail_conversation_start = True

        send_panel_message(conversation_panel, "Should not send")

        expect(
            conversation_panel.get_by_text("Failed to start conversation")
        ).to_be_visible(timeout=5_000)

        starts = chat_mocker.get_calls(method="POST", path_contains="/api/chat/conversations")
        messages = chat_mocker.get_calls(method="POST", path_contains="/api/chat/message")
        assert len(starts) == 1
        assert messages == [], "message must not POST when conversation start fails"
        _assert_widget_key(starts[0])

    def test_message_send_failure_shows_error_on_second_turn(
        self,
        conversation_panel: FrameLocator,
        chat_mocker: WidgetChatMocker,
    ) -> None:
        """Second-turn 500 on /api/chat/message surfaces 'Failed to send message'.

        First-turn send failures are not checked by Panel.handleSend (residual);
        the existing-conversation path does check result.ok — cover that path.
        """
        log = _msg_log(conversation_panel)
        send_panel_message(conversation_panel, "Bootstrap turn")
        expect(log.get_by_text("Mocked agent reply for turn one.")).to_be_visible(
            timeout=8_000
        )

        chat_mocker.fail_message_send = True
        send_panel_message(conversation_panel, "This will fail")

        expect(log.get_by_text("This will fail", exact=True)).to_be_visible(timeout=5_000)
        expect(conversation_panel.get_by_text("Failed to send message")).to_be_visible(
            timeout=5_000
        )

        messages = chat_mocker.get_calls(method="POST", path_contains="/api/chat/message")
        assert len(messages) == 2
        assert messages[1]["body"]["content"] == "This will fail"
