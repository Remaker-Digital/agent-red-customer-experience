# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.tenant_directory_response import TenantDirectoryResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    status: None | str | Unset = UNSET,
    tier: None | str | Unset = UNSET,
    billing_channel: None | str | Unset = UNSET,
    skip: int | Unset = 0,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    else:
        json_status = status
    params["status"] = json_status

    json_tier: None | str | Unset
    if isinstance(tier, Unset):
        json_tier = UNSET
    else:
        json_tier = tier
    params["tier"] = json_tier

    json_billing_channel: None | str | Unset
    if isinstance(billing_channel, Unset):
        json_billing_channel = UNSET
    else:
        json_billing_channel = billing_channel
    params["billing_channel"] = json_billing_channel

    params["skip"] = skip

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/superadmin/tenants",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | TenantDirectoryResponse | None:
    if response.status_code == 200:
        response_200 = TenantDirectoryResponse.from_dict(response.json())

        return response_200

    if response.status_code == 401:
        response_401 = cast(Any, None)
        return response_401

    if response.status_code == 403:
        response_403 = cast(Any, None)
        return response_403

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if response.status_code == 503:
        response_503 = cast(Any, None)
        return response_503

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | HTTPValidationError | TenantDirectoryResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    status: None | str | Unset = UNSET,
    tier: None | str | Unset = UNSET,
    billing_channel: None | str | Unset = UNSET,
    skip: int | Unset = 0,
    limit: int | Unset = 50,
) -> Response[Any | HTTPValidationError | TenantDirectoryResponse]:
    """List all tenants (cross-partition)

     Provider-only: lists all tenants with filtering by status, tier, and billing channel. Performs a
    cross-partition Cosmos DB query.

    Args:
        status (None | str | Unset): Filter by tenant status
        tier (None | str | Unset): Filter by subscription tier
        billing_channel (None | str | Unset): Filter by billing channel
        skip (int | Unset): Pagination offset Default: 0.
        limit (int | Unset): Page size Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | TenantDirectoryResponse]
    """

    kwargs = _get_kwargs(
        status=status,
        tier=tier,
        billing_channel=billing_channel,
        skip=skip,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    status: None | str | Unset = UNSET,
    tier: None | str | Unset = UNSET,
    billing_channel: None | str | Unset = UNSET,
    skip: int | Unset = 0,
    limit: int | Unset = 50,
) -> Any | HTTPValidationError | TenantDirectoryResponse | None:
    """List all tenants (cross-partition)

     Provider-only: lists all tenants with filtering by status, tier, and billing channel. Performs a
    cross-partition Cosmos DB query.

    Args:
        status (None | str | Unset): Filter by tenant status
        tier (None | str | Unset): Filter by subscription tier
        billing_channel (None | str | Unset): Filter by billing channel
        skip (int | Unset): Pagination offset Default: 0.
        limit (int | Unset): Page size Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | TenantDirectoryResponse
    """

    return sync_detailed(
        client=client,
        status=status,
        tier=tier,
        billing_channel=billing_channel,
        skip=skip,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    status: None | str | Unset = UNSET,
    tier: None | str | Unset = UNSET,
    billing_channel: None | str | Unset = UNSET,
    skip: int | Unset = 0,
    limit: int | Unset = 50,
) -> Response[Any | HTTPValidationError | TenantDirectoryResponse]:
    """List all tenants (cross-partition)

     Provider-only: lists all tenants with filtering by status, tier, and billing channel. Performs a
    cross-partition Cosmos DB query.

    Args:
        status (None | str | Unset): Filter by tenant status
        tier (None | str | Unset): Filter by subscription tier
        billing_channel (None | str | Unset): Filter by billing channel
        skip (int | Unset): Pagination offset Default: 0.
        limit (int | Unset): Page size Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | TenantDirectoryResponse]
    """

    kwargs = _get_kwargs(
        status=status,
        tier=tier,
        billing_channel=billing_channel,
        skip=skip,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    status: None | str | Unset = UNSET,
    tier: None | str | Unset = UNSET,
    billing_channel: None | str | Unset = UNSET,
    skip: int | Unset = 0,
    limit: int | Unset = 50,
) -> Any | HTTPValidationError | TenantDirectoryResponse | None:
    """List all tenants (cross-partition)

     Provider-only: lists all tenants with filtering by status, tier, and billing channel. Performs a
    cross-partition Cosmos DB query.

    Args:
        status (None | str | Unset): Filter by tenant status
        tier (None | str | Unset): Filter by subscription tier
        billing_channel (None | str | Unset): Filter by billing channel
        skip (int | Unset): Pagination offset Default: 0.
        limit (int | Unset): Page size Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | TenantDirectoryResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            status=status,
            tier=tier,
            billing_channel=billing_channel,
            skip=skip,
            limit=limit,
        )
    ).parsed
