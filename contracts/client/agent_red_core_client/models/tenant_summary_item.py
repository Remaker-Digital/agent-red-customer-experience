# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TenantSummaryItem")


@_attrs_define
class TenantSummaryItem:
    """Single tenant in the directory listing.

    SPEC-1843 v6 boundary: ``customer_email`` and ``shopify_shop_domain``
    are TENANCY MANAGEMENT data (SPEC-1637: required for tenancy setup,
    billing, and technical communication).  These are superadmin contact
    and storefront identifiers, NOT end-customer PII.

    WI-1641: fields restored after S137 audit found WI-1611 over-applied
    the ZK mandate.

        Attributes:
            tenant_id (str):
            status (str):
            display_name (None | str | Unset):
            tier (None | str | Unset):
            billing_channel (None | str | Unset):
            customer_email (None | str | Unset):
            shopify_shop_domain (None | str | Unset):
            created_at (None | str | Unset):
            updated_at (None | str | Unset):
            deactivated_at (None | str | Unset):
            consent_status (None | str | Unset):
            expires_at (None | str | Unset):
    """

    tenant_id: str
    status: str
    display_name: None | str | Unset = UNSET
    tier: None | str | Unset = UNSET
    billing_channel: None | str | Unset = UNSET
    customer_email: None | str | Unset = UNSET
    shopify_shop_domain: None | str | Unset = UNSET
    created_at: None | str | Unset = UNSET
    updated_at: None | str | Unset = UNSET
    deactivated_at: None | str | Unset = UNSET
    consent_status: None | str | Unset = UNSET
    expires_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        tenant_id = self.tenant_id

        status = self.status

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        tier: None | str | Unset
        if isinstance(self.tier, Unset):
            tier = UNSET
        else:
            tier = self.tier

        billing_channel: None | str | Unset
        if isinstance(self.billing_channel, Unset):
            billing_channel = UNSET
        else:
            billing_channel = self.billing_channel

        customer_email: None | str | Unset
        if isinstance(self.customer_email, Unset):
            customer_email = UNSET
        else:
            customer_email = self.customer_email

        shopify_shop_domain: None | str | Unset
        if isinstance(self.shopify_shop_domain, Unset):
            shopify_shop_domain = UNSET
        else:
            shopify_shop_domain = self.shopify_shop_domain

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        else:
            created_at = self.created_at

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = self.updated_at

        deactivated_at: None | str | Unset
        if isinstance(self.deactivated_at, Unset):
            deactivated_at = UNSET
        else:
            deactivated_at = self.deactivated_at

        consent_status: None | str | Unset
        if isinstance(self.consent_status, Unset):
            consent_status = UNSET
        else:
            consent_status = self.consent_status

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        else:
            expires_at = self.expires_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "tenantId": tenant_id,
                "status": status,
            }
        )
        if display_name is not UNSET:
            field_dict["displayName"] = display_name
        if tier is not UNSET:
            field_dict["tier"] = tier
        if billing_channel is not UNSET:
            field_dict["billingChannel"] = billing_channel
        if customer_email is not UNSET:
            field_dict["customerEmail"] = customer_email
        if shopify_shop_domain is not UNSET:
            field_dict["shopifyShopDomain"] = shopify_shop_domain
        if created_at is not UNSET:
            field_dict["createdAt"] = created_at
        if updated_at is not UNSET:
            field_dict["updatedAt"] = updated_at
        if deactivated_at is not UNSET:
            field_dict["deactivatedAt"] = deactivated_at
        if consent_status is not UNSET:
            field_dict["consentStatus"] = consent_status
        if expires_at is not UNSET:
            field_dict["expiresAt"] = expires_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        tenant_id = d.pop("tenantId")

        status = d.pop("status")

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("displayName", UNSET))

        def _parse_tier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tier = _parse_tier(d.pop("tier", UNSET))

        def _parse_billing_channel(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        billing_channel = _parse_billing_channel(d.pop("billingChannel", UNSET))

        def _parse_customer_email(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        customer_email = _parse_customer_email(d.pop("customerEmail", UNSET))

        def _parse_shopify_shop_domain(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        shopify_shop_domain = _parse_shopify_shop_domain(
            d.pop("shopifyShopDomain", UNSET)
        )

        def _parse_created_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        created_at = _parse_created_at(d.pop("createdAt", UNSET))

        def _parse_updated_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        updated_at = _parse_updated_at(d.pop("updatedAt", UNSET))

        def _parse_deactivated_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deactivated_at = _parse_deactivated_at(d.pop("deactivatedAt", UNSET))

        def _parse_consent_status(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        consent_status = _parse_consent_status(d.pop("consentStatus", UNSET))

        def _parse_expires_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        expires_at = _parse_expires_at(d.pop("expiresAt", UNSET))

        tenant_summary_item = cls(
            tenant_id=tenant_id,
            status=status,
            display_name=display_name,
            tier=tier,
            billing_channel=billing_channel,
            customer_email=customer_email,
            shopify_shop_domain=shopify_shop_domain,
            created_at=created_at,
            updated_at=updated_at,
            deactivated_at=deactivated_at,
            consent_status=consent_status,
            expires_at=expires_at,
        )

        tenant_summary_item.additional_properties = d
        return tenant_summary_item

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
