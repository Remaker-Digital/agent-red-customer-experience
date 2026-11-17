# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Count stored Decision 94 fields before a cleanup is planned."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import logging
import sys
from collections.abc import Iterable, Mapping
from typing import Protocol, TypedDict

from src.jobs.run_data_encryption_migration import _looks_encrypted

_STAGING_ENDPOINT = "https://example.invalid"
_STAGING_DATABASE = "example-database"
_CONVERSATIONS = (
    "SELECT c.identity_email, c.identity_phone FROM c "
    "WHERE IS_DEFINED(c.identity_email) OR IS_DEFINED(c.identity_phone)"
)
_TENANTS = "SELECT c.customer_email, c.display_name FROM c"


class RawProjectionReader(Protocol):
    async def scan_projection(self, query: str) -> Iterable[Mapping[str, object]]:
        """Return projected fields exactly as stored, across all partitions."""
        ...


class StoredFieldCounts(TypedDict):
    plaintext: int
    ciphertext_shaped: int
    unclassified: int


class LegacyPiiCounts(TypedDict):
    conversation_identity_email: StoredFieldCounts
    conversation_identity_phone: StoredFieldCounts
    tenant_customer_email: StoredFieldCounts
    tenant_email_display_name_candidates: int
    conversation_rows_scanned: int
    tenants_total: int


def _count_field(values: Iterable[object]) -> StoredFieldCounts:
    counts: StoredFieldCounts = {"plaintext": 0, "ciphertext_shaped": 0, "unclassified": 0}
    for value in values:
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            counts["unclassified"] += 1
        elif _looks_encrypted(value):
            counts["ciphertext_shaped"] += 1
        else:
            counts["plaintext"] += 1
    return counts


async def count_legacy_pii(
    conversation_repository: RawProjectionReader,
    tenant_repository: RawProjectionReader,
) -> LegacyPiiCounts:
    """Return aggregate counts from raw rows, including inactive tenants.

    The collections are read sequentially without a common snapshot. The
    conversation denominator counts projected rows with either field defined.
    Ciphertext-shaped values match the repository's stored format. Verifying
    that they decrypt requires the tenant's DEK and is outside this count.
    """
    conversations = list(await conversation_repository.scan_projection(_CONVERSATIONS))
    tenants = list(await tenant_repository.scan_projection(_TENANTS))
    return {
        "conversation_identity_email": _count_field(row.get("identity_email") for row in conversations),
        "conversation_identity_phone": _count_field(row.get("identity_phone") for row in conversations),
        "tenant_customer_email": _count_field(row.get("customer_email") for row in tenants),
        "tenant_email_display_name_candidates": sum(
            isinstance(name, str) and "@" in name for name in (row.get("display_name") for row in tenants)
        ),
        "conversation_rows_scanned": len(conversations),
        "tenants_total": len(tenants),
    }


class _CosmosProjectionReader:
    def __init__(self, container):
        self._container = container

    async def scan_projection(self, query: str) -> Iterable[Mapping[str, object]]:
        return [row async for row in self._container.query_items(query=query)]


async def _open_staging() -> LegacyPiiCounts:
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import AzureCliCredential

    async with AzureCliCredential() as credential:
        async with CosmosClient(
            _STAGING_ENDPOINT, credential=credential, enable_diagnostics_logging=False,
        ) as client:
            database = client.get_database_client(_STAGING_DATABASE)
            return await count_legacy_pii(
                _CosmosProjectionReader(database.get_container_client("conversations")),
                _CosmosProjectionReader(database.get_container_client("tenants")),
            )


class _PreflightArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments. Use --help for usage.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _PreflightArgumentParser(prog="legacy_pii_preflight", description=__doc__, allow_abbrev=False)
    parser.add_argument("--read-staging", action="store_true", required=True, help="Read the fixed staging database")
    parser.parse_args(argv)
    previous_logging_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            counts = asyncio.run(_open_staging())
    except Exception:
        print(json.dumps({"inventory_complete": False, "error": "staging inventory unavailable"}))
        return 2
    finally:
        logging.disable(previous_logging_level)
    print(json.dumps({
        "inventory_complete": True,
        "scan_consistency": "sequential scans without a common snapshot",
        **counts,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
