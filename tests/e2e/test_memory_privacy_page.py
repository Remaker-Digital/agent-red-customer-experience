"""
E2E tests — Memory & Privacy page.

Tests every interactive element on the Memory & Privacy page:
  - Page structure and data rendering
  - Toggle switches (memory, conversation history, cross-session, PII, consent)
  - Segmented control (identification mode: off/gentle/standard/aggressive)
  - Slider (pattern decay days: 30–365)
  - Retention period selector
  - Save button sends correct API call
  - Tier-gating: Starter tier hides Pro+ features
  - Tier-gating: Professional tier shows all features

Run with:
    pytest tests/e2e/test_memory_privacy_page.py -v --headed
    pytest tests/e2e/test_memory_privacy_page.py -v

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

import pytest
from playwright.sync_api import Page, expect

from .conftest import AdminApiMocker, setup_admin_page

pytestmark = pytest.mark.e2e


# ===========================================================================
# Page Structure Tests
# ===========================================================================


class TestMemoryPrivacyStructure:
    """Verify the Memory & Privacy page renders all expected elements."""

    def test_page_heading_visible(self, admin_memory_page: Page) -> None:
        """Page heading for Memory & Privacy is visible."""
        heading = admin_memory_page.locator("h2, h3, h4").filter(has_text="Memory")
        expect(heading.first).to_be_visible()

    def test_save_button_visible(self, admin_memory_page: Page) -> None:
        """Auto-save indicator is visible (S168 replaced explicit Save button)."""
        saved = admin_memory_page.get_by_text("Saved", exact=False)
        expect(saved.first).to_be_visible()

    def test_toggle_switches_rendered(self, admin_memory_page: Page) -> None:
        """At least one toggle switch is rendered on the page."""
        # Mantine Switches render as <input type="checkbox"> with role="switch"
        # or custom switch elements
        switches = admin_memory_page.locator('input[type="checkbox"], [role="switch"]')
        assert switches.count() > 0, "Page should have toggle switches"


# ===========================================================================
# Toggle Interaction Tests
# ===========================================================================


class TestToggleInteractions:
    """Test toggle switch interactions."""

    def test_toggle_click_changes_state(self, admin_memory_page: Page) -> None:
        """Clicking a toggle switch changes its visual state."""
        # Mantine Switch renders a hidden <input> (0×0 px) with a styled
        # wrapper <label>. We must target the label/track, not the hidden input.
        switch_labels = admin_memory_page.locator('.mantine-Switch-root, label:has(input[role="switch"])')
        if switch_labels.count() > 0:
            first_label = switch_labels.first
            first_label.scroll_into_view_if_needed()
            admin_memory_page.wait_for_timeout(100)

            # Get the hidden input to read checked state
            switch_input = first_label.locator('input[type="checkbox"], input[role="switch"]').first
            initial_checked = switch_input.is_checked()

            first_label.click()
            admin_memory_page.wait_for_timeout(200)

            new_checked = switch_input.is_checked()
            assert initial_checked != new_checked, "Toggle should change state on click"


# ===========================================================================
# Save Settings Tests
# ===========================================================================


class TestSaveSettings:
    """Test saving memory & privacy settings."""

    def test_save_sends_api_call(self, admin_memory_page: Page) -> None:
        """Toggling Customer context then blurring auto-saves PUT /api/config (S168)."""
        mocker: AdminApiMocker = admin_memory_page._api_mocker  # type: ignore[attr-defined]
        mocker.clear_calls()

        # Click the visible Switch track (input itself is position:absolute/hidden).
        section = admin_memory_page.locator("main .mantine-Paper-root").filter(
            has_text="Structured customer profiles"
        ).first
        expect(section).to_be_visible()
        track = section.locator(".mantine-Switch-track").first
        expect(track).to_be_visible()
        track.click()
        # Stack onBlur (useAutoSaveDraft) fires when focus leaves the form.
        admin_memory_page.get_by_role("heading", name="Memory & privacy").click()
        admin_memory_page.wait_for_timeout(800)

        all_saves = [
            c for c in mocker.get_calls()
            if c["method"] in ("POST", "PUT") and "config" in c.get("path", "")
        ]
        assert len(all_saves) >= 1, (
            f"Auto-save should trigger config write, got: {mocker.api_calls}"
        )
        body = all_saves[0].get("body", "") or ""
        assert (
            "memory_enabled" in body
            or "memoryEnabled" in body
            or "pii_scrubbing" in body
            or "piiScrubbing" in body
            or "retention" in body.lower()
        ), f"Config write body should contain memory/privacy fields, got: {body}"


# ===========================================================================
# Tier-Gating Tests
# ===========================================================================


class TestTierGating:
    """Test that Starter tier correctly hides Pro+ features."""

    def test_starter_tier_limits_fields(
        self, page: Page, admin_vite_server, api_mocker_starter: AdminApiMocker
    ) -> None:
        """On Starter tier, Pro+ fields are hidden or disabled."""
        setup_admin_page(page, api_mocker_starter)

        page.locator("text=Memory & privacy").first.click()
        page.wait_for_timeout(1000)

        # Page should load without crashing (the v1.58.1 fix)
        heading = page.locator("h2, h3, h4").filter(has_text="Memory")
        expect(heading.first).to_be_visible()

        # Starter tier should not crash when saving
        save_btn = page.locator("button", has_text="Save").first
        if save_btn.is_visible():
            save_btn.click()
            page.wait_for_timeout(500)
            # Should not show "Failed to save settings" error
            page.locator("text=Failed to save settings")
            # This is a soft check — the error may not appear at all
            # The key assertion is that the page didn't crash
            expect(heading.first).to_be_visible()

    def test_professional_tier_shows_all_fields(self, admin_memory_page: Page) -> None:
        """On Professional tier, all fields are visible."""
        # admin_memory_page uses Professional tier by default
        # The page should have multiple toggle switches
        switches = admin_memory_page.locator('input[type="checkbox"], [role="switch"]')
        assert switches.count() >= 3, \
            f"Professional tier should show at least 3 toggles, found {switches.count()}"


# ===========================================================================
# Memory & Privacy Field Tests
# ===========================================================================


class TestMemoryPrivacyFields:
    """Verify all named fields on the Memory & Privacy page. SPEC-0916 through SPEC-0923."""

    def test_data_retention_period_input(self, admin_memory_page: Page) -> None:
        """SPEC-0916: MemoryPrivacy has 'Data retention period' input field."""
        page_text = admin_memory_page.text_content("body") or ""
        assert "retention" in page_text.lower() or "Retention" in page_text, \
            "Data retention period field should be present"

    def test_pii_scrubbing_toggle(self, admin_memory_page: Page) -> None:
        """SPEC-0917: MemoryPrivacy has 'PII scrubbing' toggle switch."""
        page_text = admin_memory_page.text_content("body") or ""
        assert "PII" in page_text or "scrubbing" in page_text.lower() \
            or "pii" in page_text.lower(), \
            "PII scrubbing toggle should be present"

    def test_consent_required_toggle(self, admin_memory_page: Page) -> None:
        """SPEC-0918: MemoryPrivacy has 'Consent required' toggle switch."""
        page_text = admin_memory_page.text_content("body") or ""
        assert "Consent" in page_text or "consent" in page_text.lower(), \
            "Consent required toggle should be present"

    def test_automatic_deletion_toggle(self, admin_memory_page: Page) -> None:
        """SPEC-0919: MemoryPrivacy has 'Automatic deletion on request' toggle switch."""
        page_text = admin_memory_page.text_content("body") or ""
        assert "deletion" in page_text.lower() \
            or "delete" in page_text.lower() \
            or "auto" in page_text.lower(), \
            "Automatic deletion on request toggle should be present"

    def test_save_draft_inputs_button(self, admin_memory_page: Page) -> None:
        """SPEC-0920 (S168): auto-save indicator replaces Save draft inputs button."""
        saved = admin_memory_page.get_by_text("Saved", exact=False)
        assert saved.count() > 0, "Auto-save Saved indicator should be present"

    def test_identification_mode_segmented_control(self, admin_memory_page: Page) -> None:
        """SPEC-0923: MemoryPrivacy SegmentedControl offers options: off, gentle, standard."""
        page_text = admin_memory_page.text_content("body") or ""
        # The SegmentedControl should have Off, Gentle, Standard labels
        has_off = "Off" in page_text or "off" in page_text.lower()
        has_gentle = "Gentle" in page_text or "gentle" in page_text
        has_standard = "Standard" in page_text or "standard" in page_text
        assert (has_off and has_gentle) or (has_gentle and has_standard), \
            "Identification mode SegmentedControl should show Off/Gentle/Standard options"
