# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Legacy widget origin configuration remains readable in saved preferences."""


# ---------------------------------------------------------------------------
# WI-1622: Schema field exists
# ---------------------------------------------------------------------------

class TestApprovedOriginsSchema:
    """approved_widget_origins field in PreferencesDocument."""

    def test_field_exists_in_preferences(self):
        from src.multi_tenant.cosmos_schema import PreferencesDocument
        fields = PreferencesDocument.model_fields
        assert "approved_widget_origins" in fields, \
            "PreferencesDocument must have approved_widget_origins field"

    def test_field_is_list_of_strings(self):
        from src.multi_tenant.cosmos_schema import PreferencesDocument
        # Default should be empty list
        doc = PreferencesDocument(
            id="test", tenant_id="t-001", version=1,
            is_current=True, config_state="active", created_at="2026-01-01T00:00:00Z",
        )
        assert doc.approved_widget_origins == []

    def test_field_accepts_origins(self):
        from src.multi_tenant.cosmos_schema import PreferencesDocument
        doc = PreferencesDocument(
            id="test", tenant_id="t-001", version=1,
            is_current=True, config_state="active", created_at="2026-01-01T00:00:00Z",
            approved_widget_origins=["https://mystore.com", "https://shop.example.com"],
        )
        assert len(doc.approved_widget_origins) == 2
        assert "https://mystore.com" in doc.approved_widget_origins

    def test_field_in_direct_mapping(self):
        """approved_widget_origins must be in config field mapping."""
        from src.multi_tenant.config.field_mapping import _PREFS_DIRECT_FIELDS
        assert "approved_widget_origins" in _PREFS_DIRECT_FIELDS
