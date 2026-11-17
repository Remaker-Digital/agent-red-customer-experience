"""
Tenant repository — tenants collection CRUD + lookup by channel ID.

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import logging
from datetime import UTC, datetime
from typing import Any

from src.multi_tenant.cosmos_schema import (
    COLLECTION_TENANTS,
    TenantStatus,
)
from src.multi_tenant.repositories.base import TenantScopedRepository

logger = logging.getLogger(__name__)

# Platform Key Vault secret. Michael stores it. A missing key falls back
# to the plaintext email query, which cannot match ciphertext.
CUSTOMER_EMAIL_LOOKUP_KEY = "customer-email-lookup-key"


async def decrypt_tenant_row(repo: Any, item: dict[str, Any]) -> dict[str, Any] | None:
    """Decrypt one tenant row. A stand-in repo is left as it is.

    Returns None when a real decrypt fails, so one bad row does not stop
    the caller.
    """
    tenant_id = item.get("tenant_id") or ""
    decrypt = getattr(repo, "_decrypt_copy", None)
    if not callable(decrypt):
        return item
    try:
        decrypted = decrypt(dict(item), tenant_id)
        if inspect.isawaitable(decrypted):
            decrypted = await decrypted
    except Exception:
        logger.warning(
            "Could not decrypt %s/%s for tenant=%s. The row was left out.",
            getattr(repo, "_collection_name", "tenants"),
            item.get("id", ""),
            tenant_id,
        )
        return None
    return decrypted if isinstance(decrypted, dict) else item


class OwnerEmailLookupKeyUnavailableError(Exception):
    """The lookup key could not be read. A missing key is not this error."""


async def _lookup_key() -> str | None:
    """The lookup key, or None when it is absent.

    A vault that was never opened is the same as an absent key. A failed
    read of an opened vault raises. Nothing creates or writes the secret.
    """
    from src.multi_tenant.tenant_secret_service import get_secret_service

    service = get_secret_service()
    if service is None or not getattr(service, "_initialized", False):
        logger.warning("Owner email lookup key is not stored")
        return None
    try:
        key = await service.get_secret_raw(CUSTOMER_EMAIL_LOOKUP_KEY)
    except Exception as exc:
        raise OwnerEmailLookupKeyUnavailableError(CUSTOMER_EMAIL_LOOKUP_KEY) from exc
    if not key:
        logger.warning("Owner email lookup key is not stored")
        return None
    return key


async def customer_email_lookup_hash(email: str) -> str | None:
    """HMAC-SHA256 of the normalized email. None when the key is absent."""
    key = await _lookup_key()
    if key is None:
        return None
    normalized = email.strip().lower().encode("utf-8")
    return hmac.new(key.encode("utf-8"), normalized, hashlib.sha256).hexdigest()


# The domain hash uses the same key with a prefix, so it never equals an email hash.
_SHOP_DOMAIN_HASH_PREFIX = b"shopify_shop_domain:"


async def shop_domain_lookup_hash(domain: str) -> str | None:
    """HMAC-SHA256 of the normalized Shopify shop domain. None when the key is absent.

    ``shopify_shop_domain`` is encrypted, so a plaintext query cannot match it.
    The hash is stored next to the ciphertext and is what the lookup queries.
    """
    key = await _lookup_key()
    if key is None:
        return None
    normalized = _SHOP_DOMAIN_HASH_PREFIX + domain.strip().lower().encode("utf-8")
    return hmac.new(key.encode("utf-8"), normalized, hashlib.sha256).hexdigest()


class TenantRepository(TenantScopedRepository):
    """Repository for the tenants collection.

    Provides tenant CRUD operations and lookup by channel identifiers
    (Stripe customer ID, Shopify shop domain).
    """

    # SPEC-1843 / WI-1627: Fields encrypted at rest with tenant DEK
    # Per architecture plan section 4.1.3: contact PII + merchant identifiers
    _encryption_fields = frozenset(
        {
            "customer_email",
            "shopify_shop_domain",
            "brand_name",
        }
    )

    def __init__(self) -> None:
        super().__init__(COLLECTION_TENANTS)

    async def _pre_write(self, body: dict[str, Any], tenant_id: str) -> dict[str, Any]:
        """Store keyed hashes of a plaintext owner email and shop domain before encryption.

        A cleared or empty value drops its hash so the old value no longer
        finds the tenant.
        """
        domain = body.get("shopify_shop_domain")
        if isinstance(domain, str) and "." in domain:
            domain_digest = await shop_domain_lookup_hash(domain)
            if domain_digest is not None:
                body["shopify_shop_domain_hash"] = domain_digest
            else:
                body.pop("shopify_shop_domain_hash", None)
        else:
            body.pop("shopify_shop_domain_hash", None)
        email = body.get("customer_email")
        if isinstance(email, str) and "@" in email:
            digest = await customer_email_lookup_hash(email)
            if digest is not None:
                body["customer_email_hash"] = digest
            else:
                body.pop("customer_email_hash", None)
        else:
            body.pop("customer_email_hash", None)
        return await super()._pre_write(body, tenant_id)

    async def _decrypt_row(self, item: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a tenant row found by a field that is not itself encrypted."""
        tenant_id = item.get("tenant_id") or item.get("id") or ""
        if not isinstance(tenant_id, str) or not tenant_id:
            return item
        return await self._decrypt_copy(item, tenant_id)

    async def get_by_stripe_customer_id(
        self,
        tenant_id: str,
        stripe_customer_id: str,
    ) -> dict[str, Any] | None:
        """Find a tenant by Stripe customer ID within a partition.

        Note: This queries within a known tenant partition. For
        cross-partition lookup by Stripe ID (e.g., webhook processing),
        use find_by_stripe_customer_id() which performs a cross-partition query.
        """
        results = await self.query(
            tenant_id=tenant_id,
            query_text=("SELECT * FROM c WHERE c.stripe_customer_id = @stripe_id"),
            parameters=[{"name": "@stripe_id", "value": stripe_customer_id}],
            max_items=1,
        )
        return results[0] if results else None

    async def find_by_stripe_customer_id(
        self,
        stripe_customer_id: str,
    ) -> dict[str, Any] | None:
        """Find a tenant by Stripe customer ID (cross-partition).

        Used during webhook processing where tenant_id is unknown.
        This performs a cross-partition query — use sparingly.

        Args:
            stripe_customer_id: Stripe cus_... identifier.

        Returns:
            The tenant document, or None if not found.
        """
        items: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query="SELECT * FROM c WHERE c.stripe_customer_id = @stripe_id",
            parameters=[{"name": "@stripe_id", "value": stripe_customer_id}],
            # Cross-partition query (default in SDK v4+)
            max_item_count=1,
        ):
            items.append(await self._decrypt_row(item))
            break

        return items[0] if items else None

    async def find_by_shopify_domain(
        self,
        shopify_shop_domain: str,
    ) -> dict[str, Any] | None:
        """Find a tenant by Shopify shop domain (cross-partition).

        Used during Shopify webhook/auth processing where tenant_id
        is unknown.

        Args:
            shopify_shop_domain: *.myshopify.com domain.

        The domain is encrypted at rest, so the keyed hash is queried first.
        A row written before encryption has the plain domain and no hash,
        so the plaintext query follows. A lookup key that cannot be read
        raises, so a caller does not mistake the failure for "not found".

        Returns:
            The tenant document, or None if not found.
        """
        digest = await shop_domain_lookup_hash(shopify_shop_domain)
        if digest is not None:
            found = await self._first_email_match(
                "SELECT * FROM c WHERE c.shopify_shop_domain_hash = @domain_hash",
                "@domain_hash",
                digest,
            )
            if found is not None:
                return found
        return await self._first_email_match(
            "SELECT * FROM c WHERE c.shopify_shop_domain = @domain",
            "@domain",
            shopify_shop_domain,
        )

    async def verify_key_hash(
        self,
        tenant_id: str,
        api_key_hash: str,
    ) -> dict[str, Any] | None:
        """Verify an API key hash against a known tenant (partition-scoped).

        SPEC-1644: API keys authenticate users, they do NOT identify tenants.
        The tenant_id must be known in advance (from the URL).  This method
        reads the tenant document by partition key and compares the stored
        hash locally — no cross-partition query is ever performed.

        Args:
            tenant_id: The tenant to validate against (from URL).
            api_key_hash: SHA-256 hex digest of the API key.

        Returns:
            The tenant document if the hash matches, None otherwise.
        """
        try:
            doc = await self.read(tenant_id=tenant_id, document_id=tenant_id)
        except Exception:
            return None
        if doc and doc.get("api_key_hash") == api_key_hash:
            return doc
        return None

    async def _first_email_match(
        self,
        query: str,
        parameter_name: str,
        value: str,
    ) -> dict[str, Any] | None:
        async for item in self._container.query_items(
            query=query,
            parameters=[{"name": parameter_name, "value": value}],
            max_item_count=1,
        ):
            return await self._decrypt_row(item)
        return None

    async def find_by_customer_email(
        self,
        email: str,
    ) -> dict[str, Any] | None:
        """Find a tenant by customer email (cross-partition).

        Used during API key reset workflow where the merchant provides
        their email but has no valid API key for authentication.
        This performs a cross-partition query -- use sparingly.

        Args:
            email: The merchant's registered email address.

        Returns:
            The tenant document, or None if not found.
        """
        try:
            digest = await customer_email_lookup_hash(email)
        except OwnerEmailLookupKeyUnavailableError:
            logger.warning(
                "Owner email lookup key could not be read. Using the plaintext email."
            )
            digest = None
            key_unreadable = True
        else:
            key_unreadable = False
        if digest is not None:
            found = await self._first_email_match(
                "SELECT * FROM c WHERE c.customer_email_hash = @email_hash",
                "@email_hash",
                digest,
            )
            if found is not None:
                return found
        elif not key_unreadable:
            logger.warning("Owner email lookup key is not stored. Using the plaintext email.")
        return await self._first_email_match(
            "SELECT * FROM c WHERE c.customer_email = @email",
            "@email",
            email,
        )

    async def find_by_widget_key_hash(
        self,
        widget_key_hash: str,
    ) -> dict[str, Any] | None:
        """Find a tenant by widget key hash (cross-partition).

        Used during publishable widget key authentication (Decision UI-6)
        where tenant_id is unknown. Scoped to /api/chat/* endpoints only.

        Args:
            widget_key_hash: SHA-256 hex digest of the widget key.

        Returns:
            The tenant document, or None if not found.
        """
        items: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query="SELECT * FROM c WHERE c.widget_key_hash = @hash",
            parameters=[{"name": "@hash", "value": widget_key_hash}],
            # Cross-partition query (default in SDK v4+)
            max_item_count=1,
        ):
            items.append(await self._decrypt_row(item))
            break

        return items[0] if items else None

    async def list_by_status(
        self,
        tenant_id: str,
        status: TenantStatus,
    ) -> list[dict[str, Any]]:
        """List tenants with a specific status (within partition)."""
        return await self.query(
            tenant_id=tenant_id,
            query_text="SELECT * FROM c WHERE c.status = @status",
            parameters=[{"name": "@status", "value": status.value}],
        )

    async def list_active_tenant_ids(self) -> list[str]:
        """List all active tenant IDs (cross-partition).

        Used by the idle conversation scanner to enumerate tenants.
        Performs a cross-partition query — call infrequently.
        """
        ids: list[str] = []
        async for item in self._container.query_items(
            query="SELECT c.tenant_id FROM c WHERE c.status = 'active'",
            max_item_count=100,
        ):
            tid = item.get("tenant_id")
            if tid and tid not in ids:
                ids.append(tid)
        return ids

    async def list_archival_tenants(self) -> list[dict[str, Any]]:
        """List active and past-due tenant IDs and tiers for data lifecycle jobs."""
        return [
            item
            async for item in self._container.query_items(
                query="SELECT c.tenant_id, c.tier FROM c WHERE c.status IN ('active', 'past_due')",
                max_item_count=100,
            )
        ]

    async def list_active_contactless_tenants(self) -> list[dict[str, Any]]:
        """List active tenants missing both superadmin contact fields.

        Used by the SPEC-1882 recovery scanner to deactivate legacy active
        tenants that cannot receive superadministrator credentials.
        Performs a cross-partition query — call infrequently.
        """
        results: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'active' "
                "AND (NOT IS_DEFINED(c.customer_email) OR IS_NULL(c.customer_email) OR c.customer_email = '') "
                "AND (NOT IS_DEFINED(c.customer_phone) OR IS_NULL(c.customer_phone) OR c.customer_phone = '')"
            ),
            max_item_count=100,
        ):
            row = await self._take_listed_row(
                item,
                item.get("tenant_id") or item.get("id") or "",
            )
            if row is not None:
                results.append(row)
        return results

    async def list_expired_trials(self) -> list[dict[str, Any]]:
        """List active trial tenants whose trial period has expired (cross-partition).

        Returns tenant documents where:
            - status = 'active'
            - billing_channel = 'trial'
            - trial_expires_at < current UTC time (ISO 8601 comparison)

        Used by the trial expiry scanner background task.
        Performs a cross-partition query — call infrequently (hourly).
        """
        now_iso = datetime.now(UTC).isoformat()
        results: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'active' "
                "AND c.billing_channel = 'trial' "
                "AND c.trial_expires_at < @now"
            ),
            parameters=[{"name": "@now", "value": now_iso}],
            max_item_count=100,
        ):
            row = await self._take_listed_row(
                item,
                item.get("tenant_id") or item.get("id") or "",
            )
            if row is not None:
                results.append(row)
        return results

    async def list_expiring_trials(self, within_iso: str) -> list[dict[str, Any]]:
        """List active trial tenants expiring before the given timestamp (cross-partition).

        Returns tenant documents where:
            - status = 'active'
            - billing_channel = 'trial'
            - trial_expires_at is between now and within_iso

        Used by the trial expiry warning background task.
        Performs a cross-partition query — call infrequently (every 12 hours).

        Args:
            within_iso: ISO 8601 upper-bound timestamp. Tenants expiring
                before this time (but still in the future) are returned.
        """
        now_iso = datetime.now(UTC).isoformat()
        results: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'active' "
                "AND c.billing_channel = 'trial' "
                "AND c.trial_expires_at > @now "
                "AND c.trial_expires_at <= @within"
            ),
            parameters=[
                {"name": "@now", "value": now_iso},
                {"name": "@within", "value": within_iso},
            ],
            max_item_count=100,
        ):
            row = await self._take_listed_row(
                item,
                item.get("tenant_id") or item.get("id") or "",
            )
            if row is not None:
                results.append(row)
        return results

    # ------------------------------------------------------------------
    # General access expiry queries (WI-EXPIRY-1)
    # ------------------------------------------------------------------

    async def list_expired_tenants(self) -> list[dict[str, Any]]:
        """List active tenants whose general access has expired (cross-partition).

        Returns tenant documents where:
            - status = 'active'
            - expires_at is set (not null)
            - expires_at < current UTC time (ISO 8601 comparison)

        No billing_channel filter — works for ANY channel.
        Used by the access expiry scanner background task.
        Performs a cross-partition query — call infrequently (hourly).
        """
        now_iso = datetime.now(UTC).isoformat()
        results: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'active' "
                "AND IS_DEFINED(c.expires_at) "
                "AND c.expires_at != null "
                "AND c.expires_at < @now"
            ),
            parameters=[{"name": "@now", "value": now_iso}],
            max_item_count=100,
        ):
            row = await self._take_listed_row(
                item,
                item.get("tenant_id") or item.get("id") or "",
            )
            if row is not None:
                results.append(row)
        return results

    async def list_expiring_tenants(self, within_iso: str) -> list[dict[str, Any]]:
        """List active tenants expiring before the given timestamp (cross-partition).

        Returns tenant documents where:
            - status = 'active'
            - expires_at is set (not null)
            - expires_at is between now and within_iso

        No billing_channel filter — works for ANY channel.
        Used by the access expiry warning background task.
        Performs a cross-partition query — call infrequently (every 12 hours).

        Args:
            within_iso: ISO 8601 upper-bound timestamp. Tenants expiring
                before this time (but still in the future) are returned.
        """
        now_iso = datetime.now(UTC).isoformat()
        results: list[dict[str, Any]] = []
        async for item in self._container.query_items(
            query=(
                "SELECT * FROM c WHERE c.status = 'active' "
                "AND IS_DEFINED(c.expires_at) "
                "AND c.expires_at != null "
                "AND c.expires_at > @now "
                "AND c.expires_at <= @within"
            ),
            parameters=[
                {"name": "@now", "value": now_iso},
                {"name": "@within", "value": within_iso},
            ],
            max_item_count=100,
        ):
            row = await self._take_listed_row(
                item,
                item.get("tenant_id") or item.get("id") or "",
            )
            if row is not None:
                results.append(row)
        return results

    async def update_status(
        self,
        tenant_id: str,
        status: TenantStatus,
    ) -> dict[str, Any]:
        """Update a tenant's lifecycle status."""
        now = datetime.now(UTC).isoformat()
        operations = [
            {"op": "set", "path": "/status", "value": status.value},
            {"op": "set", "path": "/updated_at", "value": now},
        ]

        if status == TenantStatus.GRACE_PERIOD:
            operations.append({"op": "set", "path": "/deactivated_at", "value": now})

        return await self.patch(tenant_id, tenant_id, operations)

    # ------------------------------------------------------------------
    # SPEC-1677: Tenant account recovery address
    # ------------------------------------------------------------------

    async def update_recovery_address(
        self,
        tenant_id: str,
        recovery_email: str | None,
        enabled: bool,
        activated_by: str,
        activated_at: str,
    ) -> dict[str, Any]:
        """Set or clear the tenant's recovery address (SPEC-1677)."""
        operations = [
            {"op": "set", "path": "/recovery_address", "value": recovery_email},
            {"op": "set", "path": "/recovery_address_enabled", "value": enabled},
            {"op": "set", "path": "/recovery_address_activated_by", "value": activated_by},
            {"op": "set", "path": "/recovery_address_activated_at", "value": activated_at},
            {"op": "set", "path": "/updated_at", "value": activated_at},
        ]
        return await self.patch(tenant_id, tenant_id, operations)

    async def get_recovery_address(
        self,
        tenant_id: str,
    ) -> dict[str, Any] | None:
        """Read the tenant's recovery address fields (SPEC-1677).

        Returns dict with recovery fields, or None if tenant not found.
        """
        try:
            doc = await self.read(tenant_id, tenant_id)
            return {
                "recovery_address": doc.get("recovery_address"),
                "recovery_address_enabled": doc.get("recovery_address_enabled", False),
                "recovery_address_activated_by": doc.get("recovery_address_activated_by"),
                "recovery_address_activated_at": doc.get("recovery_address_activated_at"),
            }
        except Exception:
            return None
