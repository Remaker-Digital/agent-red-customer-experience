# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""
Scheduled job: Archival pipeline (Cosmos DB → Blob Parquet).

Invoked by Azure Container App Job (cron: daily at 04:00 UTC).
Runs ArchivalPipelineService.run_archival_scan() across all tenants,
then exits with code 0 (success) or 1 (errors encountered).

Usage:
    python -m src.jobs.run_archival

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> int:
    """Run archival pipeline and return exit code."""
    from src.multi_tenant.archival_pipeline import ArchivalPipelineService
    from src.multi_tenant.cosmos_client import get_cosmos_manager
    from src.multi_tenant.repository import (
        AuditLogRepository,
        ConversationRepository,
        CustomerProfileRepository,
        MemoryVectorRepository,
        TenantRepository,
    )

    logger.info("Archival pipeline job starting")

    # Wire Blob Storage client if connection string available
    blob_client = None
    blob_conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if blob_conn:
        try:
            from azure.storage.blob.aio import BlobServiceClient

            blob_client = BlobServiceClient.from_connection_string(blob_conn)
            logger.info("Azure Blob Storage client configured")
        except Exception:
            logger.warning("Blob Storage client creation failed — running in dry-run mode")

    try:
        async with AsyncExitStack() as clients:
            if blob_client is not None:
                await clients.enter_async_context(blob_client)
            await clients.enter_async_context(get_cosmos_manager().connected())
            service = ArchivalPipelineService(
                tenant_repo=TenantRepository(),
                conversation_repo=ConversationRepository(),
                profile_repo=CustomerProfileRepository(),
                vector_repo=MemoryVectorRepository(),
                audit_repo=AuditLogRepository(),
                blob_client=blob_client,
            )
            result = await service.run_archival_scan()
    except Exception as exc:
        mode = "dry run" if blob_client is None else "upload"
        error_type = type(exc).__name__
        # SDK exception messages can contain connection details.
        safe_error = RuntimeError(error_type)
        logger.error(
            "Archival %s could not finish (%s)",
            mode,
            error_type,
            exc_info=(RuntimeError, safe_error, exc.__traceback__),
        )
        return 1

    if blob_client is None:
        logger.info(
            "Archival pipeline job complete: mode=dry_run tenants=%d tenants_skipped=%d "
            "candidate_documents=%d errors=%d",
            result.tenants_scanned,
            result.tenants_skipped,
            result.candidate_documents,
            result.errors,
        )
    else:
        logger.info(
            "Archival pipeline job complete: mode=upload tenants=%d documents=%d bytes=%d errors=%d",
            result.tenants_scanned,
            result.documents_archived,
            result.bytes_written,
            result.errors,
        )

    return 1 if result.errors > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
