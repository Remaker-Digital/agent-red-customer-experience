# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""
Verification token repository — short-lived tokens for email verification
and magic links.

Tokens are stored with Cosmos DB TTL so they auto-expire. The collection
uses ``token_type`` as partition key (e.g. "email_verification",
"magic_link") to separate token families.

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from math import ceil
from secrets import compare_digest
from typing import Any

from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosAccessConditionFailedError,
    CosmosResourceExistsError,
    CosmosResourceNotFoundError,
)

from src.multi_tenant.cosmos_client import get_cosmos_manager
from src.multi_tenant.cosmos_schema import (
    COLLECTION_VERIFICATION_TOKENS,
    TTL_VERIFICATION_TOKEN,
)

logger = logging.getLogger(__name__)

# Cosmos DB item ids are at most 1,023 bytes and may not contain "/" or a backslash.
# https://learn.microsoft.com/azure/cosmos-db/concepts-limits#per-item-limits
_MAX_ID_BYTES = 1023
_MAX_CODE_ATTEMPTS = 3


def _is_storable_id(token_id: str) -> bool:
    """True if Cosmos DB could hold an item with this id.

    Token ids reach this repository from public links and request bodies. The
    SDK puts the id into the request path without escaping "/", so an id
    Cosmos cannot store would address some other resource path instead of
    simply not being found. No stored token can fail this check.
    """
    return (
        bool(token_id)
        and "/" not in token_id
        and "\\" not in token_id
        and len(token_id.encode("utf-8")) <= _MAX_ID_BYTES
    )


class VerificationTokenRepository:
    """Repository for the verification_tokens collection.

    Platform-scoped (not tenant-scoped). Partition key is ``token_type``.
    Documents auto-expire via Cosmos DB TTL.
    """

    def __init__(self) -> None:
        self._collection_name = COLLECTION_VERIFICATION_TOKENS

    @property
    def _container(self) -> Any:
        return get_cosmos_manager().get_container(self._collection_name)

    async def create_token(
        self,
        token_id: str,
        token_type: str,
        tenant_id: str,
        email: str,
        ttl: int = TTL_VERIFICATION_TOKEN,
        *,
        member_id: str | None = None,
        sign_in_code: str | None = None,
        code_challenge_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a verification token document.

        Args:
            token_id: Unique token identifier (URL-safe random string).
            token_type: Partition key value (e.g. "email_verification").
            tenant_id: Tenant this token belongs to.
            email: Email address being verified.
            ttl: Time-to-live in seconds (default: 10 minutes).
            member_id: Optional team member document ID. When present,
                the verification flow can carry member identity through
                to JWT issuance without an additional DB lookup.

        Returns:
            Created document dict.

        Raises:
            DocumentConflictError: If token_id already exists.
        """
        now = datetime.now(UTC)
        doc = {
            "id": token_id,
            "token_type": token_type,
            "tenant_id": tenant_id,
            "email": email,
            "used": False,
            "created_at": now.isoformat(),
            "ttl": ttl,
        }
        if member_id is not None:
            doc["member_id"] = member_id
        if sign_in_code is not None:
            doc["sign_in_code"] = sign_in_code
        if code_challenge_id is not None:
            doc["code_challenge_id"] = code_challenge_id
            doc["code_attempts"] = 0
            doc["code_expires_at"] = (now + timedelta(seconds=ttl)).isoformat()
        try:
            result = await self._container.create_item(body=doc)
            logger.info(
                "Verification token created: type=%s tenant=%s",
                token_type, tenant_id,
            )
            return result
        except CosmosResourceExistsError:
            logger.warning("Token conflict: id=%s", token_id)
            raise

    async def _mark_used(self, doc: dict[str, Any], token_type: str) -> bool:
        """Set ``used=True`` only if the token is unchanged since it was read.

        The patch carries the read document's ETag with an If-Match
        condition, so when two requests redeem one token at once Cosmos
        applies exactly one patch and rejects the other with HTTP 412.
        Returns False, and the caller must not hand out the token, when the
        write is rejected or fails. A document without an ETag cannot be
        written conditionally and is refused; Cosmos returns ``_etag`` on
        every read, so this only happens with an incomplete test double.
        Log lines name the token type, never the token id or code.
        """
        etag = doc.get("_etag")
        if not etag:
            logger.warning("Token has no ETag, not consumed: type=%s", token_type)
            return False
        try:
            await self._container.patch_item(
                item=doc["id"],
                partition_key=token_type,
                patch_operations=[
                    {"op": "set", "path": "/used", "value": True},
                ],
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except CosmosAccessConditionFailedError:
            logger.info(
                "Token changed since read (already used by a concurrent "
                "request): type=%s", token_type,
            )
            return False
        except Exception:
            logger.warning("Failed to mark token as used: type=%s", token_type)
            return False
        return True

    async def consume_token(
        self,
        token_id: str,
        token_type: str,
    ) -> dict[str, Any] | None:
        """Read and mark a token as used (single-use).

        Returns the token document if valid and unused, or None if
        the token does not exist, is already used, or has expired.
        ``used=True`` is set by a patch conditional on the read ETag, so
        of two concurrent requests for one token exactly one receives it.
        """
        if not _is_storable_id(token_id):
            return None
        try:
            doc = await self._container.read_item(
                item=token_id,
                partition_key=token_type,
            )
        except CosmosResourceNotFoundError:
            return None

        if doc.get("used") or not await self._mark_used(doc, token_type):
            return None

        return doc

    async def consume_token_by_code(
        self,
        tenant_id: str,
        sign_in_code: str,
        token_type: str,
        *,
        challenge_id: str,
    ) -> dict[str, Any] | None:
        """Spend one of this challenge's three attempts and consume a matching code."""
        query = (
            "SELECT TOP 1 * FROM c WHERE c.tenant_id = @tid "
            "AND c.code_challenge_id = @challenge AND c.used = false"
        )
        params = [{"name": "@tid", "value": tenant_id}, {"name": "@challenge", "value": challenge_id}]
        # An ETag conflict can mean a concurrent attempt spent a slot or the link won redemption.
        for _ in range(_MAX_CODE_ATTEMPTS):
            try:
                doc = await anext(aiter(self._container.query_items(
                    query=query, parameters=params, partition_key=token_type, max_item_count=1,
                )), None)
            except Exception:
                logger.exception("Error looking up sign-in challenge: tenant=%s", tenant_id)
                return None

            if not doc or doc.get("used") or not doc.get("_etag") or doc["code_attempts"] >= _MAX_CODE_ATTEMPTS:
                return None
            remaining = (datetime.fromisoformat(doc["code_expires_at"]) - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return None
            correct = compare_digest(sign_in_code.encode(), doc["sign_in_code"].encode())
            operations = [
                {"op": "set", "path": "/code_attempts", "value": doc["code_attempts"] + 1},
                # Cosmos resets TTL whenever a document is patched.
                {"op": "set", "path": "/ttl", "value": ceil(remaining)},
            ]
            if correct:
                operations.append({"op": "set", "path": "/used", "value": True})
            try:
                await self._container.patch_item(
                    item=doc["id"], partition_key=token_type, patch_operations=operations,
                    etag=doc["_etag"], match_condition=MatchConditions.IfNotModified,
                )
            except CosmosAccessConditionFailedError:
                logger.info("Sign-in challenge already changed since read")
                continue
            except Exception:
                logger.warning("Failed to record sign-in code attempt")
                return None
            return doc if correct else None
        return None

    async def delete_token(
        self,
        token_id: str,
        token_type: str,
    ) -> bool:
        """Delete a token document. Returns True if deleted."""
        if not _is_storable_id(token_id):
            return False
        try:
            await self._container.delete_item(
                item=token_id,
                partition_key=token_type,
            )
            return True
        except CosmosResourceNotFoundError:
            return False
