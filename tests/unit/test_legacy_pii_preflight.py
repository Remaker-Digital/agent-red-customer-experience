# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Decision 94 preflight reads stored fields and returns aggregate counts."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, call

import pytest

from src.jobs.legacy_pii_preflight import count_legacy_pii

pytestmark = pytest.mark.unit


def read_repository(rows):
    repository = Mock()
    repository.scan_projection = AsyncMock(return_value=rows)
    return repository


async def test_counts_stored_values_without_writing_or_exposing_them(capsys, caplog):
    ciphertext_shaped = base64.b64encode(bytes(range(29))).decode("ascii")
    short_base64 = base64.b64encode(bytes(range(28))).decode("ascii")
    conversations = [
        {"identity_email": "customer@example.com", "identity_phone": "+15555550101"},
        {"identity_email": ciphertext_shaped, "identity_phone": ciphertext_shaped},
        {"identity_email": short_base64, "identity_phone": None},
        {"identity_phone": 15555550102},
        {"identity_email": ""},
    ]
    tenants = [
        {"customer_email": "owner@example.com", "display_name": "owner@example.com"},
        {"customer_email": ciphertext_shaped, "display_name": "Acme"},
        {"customer_email": "", "display_name": None},
        {"display_name": ["owner@example.com"]},
    ]
    conversation_repository = read_repository(conversations)
    tenant_repository = read_repository(tenants)
    original_rows = deepcopy((conversations, tenants))

    result = await count_legacy_pii(conversation_repository, tenant_repository)

    assert result == {
        "conversation_identity_email": {"plaintext": 2, "ciphertext_shaped": 1, "unclassified": 0},
        "conversation_identity_phone": {"plaintext": 1, "ciphertext_shaped": 1, "unclassified": 1},
        "tenant_customer_email": {"plaintext": 1, "ciphertext_shaped": 1, "unclassified": 0},
        "tenant_email_display_name_candidates": 1,
        "conversation_rows_scanned": 5,
        "tenants_total": 4,
    }
    conversation_query = (
        "SELECT c.identity_email, c.identity_phone FROM c "
        "WHERE IS_DEFINED(c.identity_email) OR IS_DEFINED(c.identity_phone)"
    )
    tenant_query = "SELECT c.customer_email, c.display_name FROM c"
    conversation_repository.scan_projection.assert_awaited_once_with(conversation_query)
    tenant_repository.scan_projection.assert_awaited_once_with(tenant_query)
    assert conversation_repository.mock_calls == [call.scan_projection(conversation_query)]
    assert tenant_repository.mock_calls == [call.scan_projection(tenant_query)]
    assert (conversations, tenants) == original_rows
    assert capsys.readouterr() == ("", "")
    assert caplog.text == ""


async def test_empty_collections_return_zero_counts():
    zero = {"plaintext": 0, "ciphertext_shaped": 0, "unclassified": 0}

    assert await count_legacy_pii(read_repository([]), read_repository([])) == {
        "conversation_identity_email": zero,
        "conversation_identity_phone": zero,
        "tenant_customer_email": zero,
        "tenant_email_display_name_candidates": 0,
        "conversation_rows_scanned": 0,
        "tenants_total": 0,
    }


def test_staging_read_requires_opt_in_before_initializing_client(monkeypatch, capsys):
    from src.jobs import legacy_pii_preflight as preflight

    open_staging = Mock()
    monkeypatch.setattr(preflight, "_open_staging", open_staging, raising=False)
    with pytest.raises(SystemExit) as error:
        preflight.main([])
    assert error.value.code == 2
    open_staging.assert_not_called()
    assert capsys.readouterr().out == ""


def test_staging_cli_does_not_echo_unexpected_arguments(monkeypatch, capsys):
    from src.jobs import legacy_pii_preflight as preflight

    open_staging = Mock()
    monkeypatch.setattr(preflight, "_open_staging", open_staging)
    with pytest.raises(SystemExit) as error:
        preflight.main(["--read-staging", "unexpected-private-value"])

    assert error.value.code == 2
    open_staging.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unexpected-private-value" not in captured.err
    assert "Invalid arguments. Use --help for usage." in captured.err


def test_staging_success_labels_sequential_scans(monkeypatch, capsys):
    from src.jobs import legacy_pii_preflight as preflight

    monkeypatch.setattr(preflight, "_open_staging", AsyncMock(return_value={"conversation_rows_scanned": 2}))
    assert preflight.main(["--read-staging"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "inventory_complete": True,
        "scan_consistency": "sequential scans without a common snapshot",
        "conversation_rows_scanned": 2,
    }


def test_staging_query_failure_does_not_print_diagnostics(monkeypatch, capsys):
    from src.jobs import legacy_pii_preflight as preflight

    open_staging = Mock(side_effect=RuntimeError("private diagnostic"))
    monkeypatch.setattr(preflight, "_open_staging", open_staging, raising=False)
    assert preflight.main(["--read-staging"]) == 2
    captured = capsys.readouterr()
    assert captured.out == '{"inventory_complete": false, "error": "staging inventory unavailable"}\n'
    assert captured.err == ""
