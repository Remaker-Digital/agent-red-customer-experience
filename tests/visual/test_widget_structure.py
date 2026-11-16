"""
Layer 1 — Widget structural presence tests.

Verifies that the widget's key DOM elements exist and are visible in the
correct configuration.  These tests catch structural regressions: a component
removed by refactoring, a renamed aria-label, a missing greeting message.

Each test is deterministic because the /api/config response is mocked via
the ``widget_page`` fixture (see conftest.py).

Run with:
    pytest tests/visual/test_widget_structure.py -v --headed   # visual
    pytest tests/visual/test_widget_structure.py -v             # headless

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import FrameLocator, Page, expect

from tests.visual.conftest import VITE_PORT


# ---------------------------------------------------------------------------
# Marker: all tests in this module require the "visual" marker
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.visual


def test_dev_storefront_boots_without_browser_mocks(page: Page, vite_server) -> None:
    """The manual Vite preview must load the widget in an ordinary browser."""
    page.goto(f"http://localhost:{VITE_PORT}/dev.html", wait_until="domcontentloaded")
    expect(page.get_by_role("status")).to_have_text("Widget ready.")
    expect(page.locator("#agent-red-widget")).to_be_attached(timeout=5_000)

    open_button = page.get_by_role("button", name="Open Widget")
    expect(open_button).to_be_enabled()
    open_button.click()
    panel = page.locator('iframe[title="Agent Red Chat"]')
    expect(panel).to_have_css("opacity", "1")
    check_state = page.get_by_role("button", name="Check isOpen")
    check_state.click()
    expect(page.get_by_role("status")).to_have_text("The widget is open.")

    page.get_by_role("button", name="Close Widget").click()
    expect(panel).to_have_css("opacity", "0")
    check_state.click()
    expect(page.get_by_role("status")).to_have_text("The widget is closed.")

    page.get_by_role("button", name="Destroy").click()
    expect(page.locator("#agent-red-widget")).to_have_count(0)
    expect(open_button).to_be_disabled()
    expect(page.get_by_role("status")).to_contain_text("Widget destroyed.")


# ===========================================================================
# Widget Lifecycle Tests
# ===========================================================================


class TestWidgetLifecycle:
    """Test the widget open/close/toggle lifecycle via the SDK."""

    def test_sdk_available(self, widget_page: Page) -> None:
        """The window.AgentRed SDK must be defined after widget init."""
        result = widget_page.evaluate("typeof window.AgentRed")
        assert result == "object", "AgentRed SDK not exposed on window"

    def test_sdk_methods(self, widget_page: Page) -> None:
        """The SDK must expose all documented methods."""
        methods = widget_page.evaluate("""
            const sdk = window.AgentRed;
            ['open', 'close', 'toggle', 'isOpen', 'setUnreadCount', 'hide', 'show', 'destroy']
                .map(m => typeof sdk[m])
        """)
        for i, method_type in enumerate(methods):
            assert method_type == "function", f"SDK method at index {i} is not a function"

    def test_widget_initially_closed(self, widget_page: Page) -> None:
        """Widget starts in 'closed' state — no iframe in the DOM."""
        iframe_count = widget_page.evaluate(
            "document.querySelectorAll('iframe[title=\"Agent Red Chat\"]').length"
        )
        assert iframe_count == 0, "Panel iframe should not exist when widget is closed"

    def test_open_creates_iframe(self, widget_page: Page) -> None:
        """Calling AgentRed.open() creates the panel iframe."""
        widget_page.evaluate("window.AgentRed.open()")
        iframe = widget_page.wait_for_selector(
            'iframe[title="Agent Red Chat"]',
            state="attached",
            timeout=5_000,
        )
        assert iframe is not None, "Panel iframe was not created after open()"

    @pytest.mark.parametrize(("position", "saved_width"), [("bottom-right", None), ("bottom-left", "440px")])
    def test_panel_fits_a_narrow_viewport(
        self, widget_page: Page, mock_config: dict, position: str, saved_width: str | None,
    ) -> None:
        if saved_width:
            mock_config["widget_position"] = position
            widget_page.evaluate("(width) => sessionStorage.setItem('ar_panel_width', width)", saved_width)
            widget_page.reload(wait_until="domcontentloaded")
            widget_page.wait_for_function("!!window.AgentRed")
        widget_page.set_viewport_size({"width": 390, "height": 844})
        widget_page.evaluate("window.AgentRed.open()")
        panel = widget_page.get_by_title("Agent Red Chat", exact=True)
        expect(panel).to_have_css("opacity", "1")
        narrow = panel.bounding_box()
        assert narrow is not None
        assert narrow["x"] >= -0.5
        assert narrow["x"] + narrow["width"] <= 390.5

        widget_page.set_viewport_size({"width": 800, "height": 844})
        expect(panel).to_have_css("width", saved_width or "380px")

    def test_close_hides_iframe(self, widget_page: Page) -> None:
        """Calling AgentRed.close() sets iframe opacity to 0."""
        widget_page.evaluate("window.AgentRed.open()")
        widget_page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)
        widget_page.wait_for_timeout(300)  # transition

        widget_page.evaluate("window.AgentRed.close()")
        widget_page.wait_for_timeout(300)  # close transition

        opacity = widget_page.evaluate("""
            document.querySelector('iframe[title="Agent Red Chat"]').style.opacity
        """)
        assert opacity == "0", f"Expected opacity '0' after close, got '{opacity}'"

    def test_toggle_round_trip(self, widget_page: Page) -> None:
        """Toggle open → closed → open produces correct isOpen() states."""
        assert widget_page.evaluate("window.AgentRed.isOpen()") is False

        widget_page.evaluate("window.AgentRed.toggle()")
        widget_page.wait_for_timeout(100)
        assert widget_page.evaluate("window.AgentRed.isOpen()") is True

        widget_page.evaluate("window.AgentRed.toggle()")
        widget_page.wait_for_timeout(100)
        assert widget_page.evaluate("window.AgentRed.isOpen()") is False

    def test_destroy_removes_iframe(self, widget_page: Page) -> None:
        """Destroying the widget removes the iframe from the DOM."""
        widget_page.evaluate("window.AgentRed.open()")
        widget_page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)

        widget_page.evaluate("window.AgentRed.destroy()")
        widget_page.wait_for_timeout(200)

        iframe_count = widget_page.evaluate(
            "document.querySelectorAll('iframe[title=\"Agent Red Chat\"]').length"
        )
        assert iframe_count == 0, "Panel iframe should be removed after destroy()"


@pytest.mark.parametrize("launcher_position", ["bottom-right", "bottom-left"])
def test_resize_handle_tracks_left_edge_after_repositioning(
    widget_page: Page, mock_config: dict, launcher_position: str,
) -> None:
    mock_config["widget_position"] = launcher_position
    widget_page.reload()
    widget_page.wait_for_function("window.AgentRed !== undefined")
    widget_page.evaluate("window.AgentRed.open()")

    panel = widget_page.locator('iframe[title="Agent Red Chat"]')
    handle = widget_page.locator(".ar-panel-resize-handle")
    header = widget_page.frame_locator('iframe[title="Agent Red Chat"]').locator("#ar-panel-heading")

    def bounds(locator):
        rect = locator.bounding_box()
        assert rect is not None
        return rect

    def assert_handle_alignment():
        panel_bounds = bounds(panel)
        handle_bounds = bounds(handle)
        for axis in ("x", "y", "height"):
            assert handle_bounds[axis] == pytest.approx(panel_bounds[axis], abs=0.5)

    widget_page.wait_for_function("""
        () => {
          const panel = document.querySelector('iframe[title="Agent Red Chat"]');
          return panel?.style.opacity === '1' && panel.getAnimations().length === 0;
        }
    """)
    assert_handle_alignment()

    original = bounds(panel)
    title = bounds(header)
    start_x = title["x"] + 10
    start_y = title["y"] + title["height"] / 2
    drag_x = -180 if launcher_position == "bottom-right" else 180
    widget_page.mouse.move(start_x, start_y)
    widget_page.mouse.down()
    widget_page.wait_for_function("""
        document.querySelector('iframe[title="Agent Red Chat"]').style.transition === 'none'
    """)
    widget_page.evaluate(
        """([x, y]) => {
          document.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y, buttons: 1 }));
          document.dispatchEvent(new MouseEvent('mouseup', { clientX: x, clientY: y }));
        }""",
        [start_x + drag_x, start_y - 24],
    )
    widget_page.mouse.up()
    moved = bounds(panel)
    assert abs(moved["x"] - original["x"]) > 100
    assert moved["y"] != original["y"]
    assert_handle_alignment()

    for delta in (-64, 48):
        before = bounds(panel)
        grip = bounds(handle)
        start_x = grip["x"] + grip["width"] / 2
        start_y = grip["y"] + grip["height"] / 2
        widget_page.mouse.move(start_x, start_y)
        widget_page.mouse.down()
        widget_page.mouse.move(start_x + delta, start_y, steps=5)
        widget_page.mouse.up()
        after = bounds(panel)
        assert after["x"] == pytest.approx(before["x"] + delta, abs=1)
        assert after["x"] + after["width"] == pytest.approx(before["x"] + before["width"], abs=1)
        assert_handle_alignment()

    widget_page.reload()
    widget_page.wait_for_function("window.AgentRed !== undefined")
    widget_page.evaluate("window.AgentRed.open()")
    widget_page.wait_for_function("""
        () => {
          const panel = document.querySelector('iframe[title="Agent Red Chat"]');
          return panel?.style.opacity === '1' && panel.getAnimations().length === 0;
        }
    """)
    assert_handle_alignment()
    assert bounds(panel) == pytest.approx(after, abs=1)
    widget_page.evaluate("window.AgentRed.hide()")
    expect(handle).to_be_hidden()
    widget_page.evaluate("window.AgentRed.show()")
    assert_handle_alignment()
    widget_page.evaluate("window.AgentRed.destroy()")
    expect(handle).to_have_count(0)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_close_button_colors_on_open_reopen_and_keyboard_focus(
    widget_page: Page, mock_config: dict, mode: str,
) -> None:
    mock_config["widget_color_mode"] = mode
    widget_page.reload()
    widget_page.wait_for_function("window.AgentRed !== undefined")

    panel_element = widget_page.locator('iframe[title="Agent Red Chat"]')
    panel = widget_page.frame_locator('iframe[title="Agent Red Chat"]')
    dialog = panel.get_by_role("dialog")
    close = panel.get_by_role("button", name="Close chat")
    open_button = widget_page.get_by_role("button", name="Open Widget", exact=True)

    for keyboard in (False, True):
        if keyboard:
            widget_page.mouse.move(0, 0)
            open_button.press("Enter")
        else:
            open_button.click()
        expect(panel_element).to_have_css("opacity", "1")
        expect(dialog).to_be_focused()
        expect(close).to_have_css("background-color", "rgba(0, 0, 0, 0)")
        expect(close).to_have_css("color", "rgb(255, 255, 255)")

        if keyboard:
            widget_page.keyboard.press("Tab")
            expect(close).to_be_focused()
            expect(close).to_have_css("outline-style", "solid")
            expect(close).to_have_css("background-color", "rgba(0, 0, 0, 0)")
            widget_page.keyboard.press("Escape")
        else:
            close.hover()
            expect(close).to_have_css("color", "rgb(255, 255, 255)")
            expect(close).to_have_css("background-color", "rgba(28, 25, 23, 0.22)")
            expect(close).to_have_css("border-top-color", "rgb(255, 255, 255)")
            widget_page.mouse.down()
            expect(close).to_have_css("color", "rgb(255, 255, 255)")
            expect(close).to_have_css("background-color", "rgba(28, 25, 23, 0.34)")
            widget_page.mouse.up()
        expect(panel_element).to_have_css("opacity", "0")


# ===========================================================================
# Panel Header Tests
# ===========================================================================


class TestPanelHeader:
    """Verify the panel header renders all expected elements."""

    def test_header_title(self, widget_panel: FrameLocator) -> None:
        """Default header title is 'Chat with us' (from en.ts locale)."""
        expect(widget_panel.locator("text=Chat with us")).to_be_visible()

    def test_agent_name_visible(self, widget_panel: FrameLocator) -> None:
        """Agent name from config is displayed in the header."""
        # Use exact text match — "Agent Red" appears in multiple places
        expect(widget_panel.get_by_text("Agent Red", exact=True)).to_be_visible()

    def test_agent_title_visible(self, widget_panel: FrameLocator) -> None:
        """Header title row shows locale default 'Chat with us'.

        The header uses headerText (from config) or locale.headerTitle.
        When neither is set in mock config, the English locale default
        'Chat with us' is displayed.
        """
        expect(widget_panel.get_by_text("Chat with us")).to_be_visible()

    def test_close_button_exists(self, widget_panel: FrameLocator) -> None:
        """Close button with correct aria-label is present."""
        close_btn = widget_panel.locator('button[aria-label="Close chat"]')
        expect(close_btn).to_be_visible()

    def test_close_button_has_svg(self, widget_panel: FrameLocator) -> None:
        """Close button contains an SVG icon (X mark)."""
        svg = widget_panel.locator('button[aria-label="Close chat"] svg')
        expect(svg).to_be_attached()

    def test_online_status_dot(self, widget_panel: FrameLocator) -> None:
        """A green status dot (6×6px circle) with 'Online' text is visible.

        The header's third row renders a green dot + 'Online' span.
        We verify the 'Online' text is present as a proxy for the status dot.
        """
        expect(
            widget_panel.get_by_text("Online", exact=True)
        ).to_be_visible()


# ===========================================================================
# Panel Header — Custom Config Tests
# ===========================================================================


class TestPanelHeaderCustomConfig:
    """Verify header customization via WidgetConfig overrides."""

    def test_custom_header_text(self, page: Page, vite_server, mock_config: dict) -> None:
        """Setting widget_header_text overrides the default 'Chat with us'."""
        import json
        mock_config["widget_header_text"] = "Need help?"

        page.route("**/api/config*", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"config": mock_config}),
        ))
        page.route("**/api/conversations*", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"conversation_id": "test-conv-custom"}),
        ))

        page.goto(f"http://localhost:{VITE_PORT}/dev.html", wait_until="domcontentloaded")
        page.wait_for_function("!!window.AgentRed", timeout=10_000)
        page.evaluate("window.AgentRed.open()")
        page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)
        page.wait_for_timeout(400)

        frame = page.frame_locator('iframe[title="Agent Red Chat"]')
        expect(frame.locator("text=Need help?")).to_be_visible()
        # Default should NOT appear
        expect(frame.locator("text=Chat with us")).not_to_be_visible()


# ===========================================================================
# Greeting / Conversation Area Tests
# ===========================================================================


class TestConversationArea:
    """Verify the conversation area, greeting, and input bar."""

    def test_greeting_visible(self, widget_panel: FrameLocator) -> None:
        """The configured greeting message is displayed."""
        expect(
            widget_panel.locator("text=Hi there! How can I help you today?")
        ).to_be_visible()

    def test_input_textarea_exists(self, widget_panel: FrameLocator) -> None:
        """A textarea for message input is present."""
        textarea = widget_panel.locator("textarea")
        expect(textarea).to_be_visible()

    def test_input_placeholder(self, widget_panel: FrameLocator) -> None:
        """Textarea has the default placeholder text."""
        textarea = widget_panel.locator("textarea")
        placeholder = textarea.get_attribute("placeholder")
        assert placeholder == "Type your message...", f"Expected default placeholder, got: {placeholder}"

    def test_send_button_exists(self, widget_panel: FrameLocator) -> None:
        """A send button is present in the input bar."""
        # Send button uses locale.sendButton = "Send" as its aria-label
        send_btn = widget_panel.locator('button[aria-label="Send"]')
        expect(send_btn).to_be_attached()

    def test_branding_visible(self, widget_panel: FrameLocator) -> None:
        """'Powered by Agent Red' branding is visible when enabled."""
        expect(widget_panel.locator("text=Powered by Agent Red")).to_be_visible()


# ===========================================================================
# Panel Dimensions Tests
# ===========================================================================


class TestPanelDimensions:
    """Verify the panel iframe has correct sizing from config."""

    def test_panel_width_standard(self, widget_page: Page) -> None:
        """Standard panel width is 380px."""
        widget_page.evaluate("window.AgentRed.open()")
        widget_page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)

        width = widget_page.evaluate("""
            document.querySelector('iframe[title="Agent Red Chat"]').style.width
        """)
        assert width == "380px", f"Expected panel width '380px', got '{width}'"

    def test_panel_height(self, widget_page: Page) -> None:
        """Panel height is 520px."""
        widget_page.evaluate("window.AgentRed.open()")
        widget_page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)

        height = widget_page.evaluate("""
            document.querySelector('iframe[title="Agent Red Chat"]').style.height
        """)
        assert height == "520px", f"Expected panel height '520px', got '{height}'"

    def test_panel_border_radius(self, widget_page: Page) -> None:
        """Panel iframe has rounded corners (12px borderRadius)."""
        widget_page.evaluate("window.AgentRed.open()")
        widget_page.wait_for_selector('iframe[title="Agent Red Chat"]', timeout=5_000)

        radius = widget_page.evaluate("""
            document.querySelector('iframe[title="Agent Red Chat"]').style.borderRadius
        """)
        assert radius == "12px", f"Expected border-radius '12px', got '{radius}'"


# ===========================================================================
# Iframe Accessibility Tests
# ===========================================================================


class TestIframeAccessibility:
    """Verify accessibility attributes on the panel iframe."""

    def test_iframe_has_title(self, widget_page: Page) -> None:
        """The iframe must have a title for screen readers."""
        widget_page.evaluate("window.AgentRed.open()")
        iframe = widget_page.wait_for_selector(
            'iframe[title="Agent Red Chat"]', timeout=5_000
        )
        assert iframe is not None
        title = iframe.get_attribute("title")
        assert title == "Agent Red Chat"

    def test_iframe_allows_media(self, widget_page: Page) -> None:
        """The iframe allows microphone and camera (for future features)."""
        widget_page.evaluate("window.AgentRed.open()")
        iframe = widget_page.wait_for_selector(
            'iframe[title="Agent Red Chat"]', timeout=5_000
        )
        allow = iframe.get_attribute("allow")
        assert "microphone" in (allow or ""), "iframe should allow microphone"
        assert "camera" in (allow or ""), "iframe should allow camera"
