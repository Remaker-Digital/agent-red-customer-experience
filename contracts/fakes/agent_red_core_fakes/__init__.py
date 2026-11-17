# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Deterministic synthetic core API with no sockets or real account data."""

from __future__ import annotations

from datetime import datetime
from importlib.resources import files
import json

import httpx
from jsonschema import Draft202012Validator

DUMMY_OPERATOR_KEY = "dummy-not-a-key"
BASE_URL = "https://core.example.invalid"
SYNTHETIC_TENANTS = (
    {
        "tenantId": "synthetic-003",
        "displayName": "Example Workshop",
        "status": "active",
        "tier": "starter",
        "createdAt": "2026-11-17T12:00:00Z",
    },
    {
        "tenantId": "synthetic-002",
        "displayName": "Example Studio",
        "status": "trial",
        "tier": "starter",
        "createdAt": "2026-11-15T12:00:00+00:00",
    },
    {
        "tenantId": "synthetic-001",
        "displayName": "Example Archive",
        "status": "deactivated",
        "tier": "starter",
        "createdAt": "2026-11-01T12:00:00Z",
    },
)


def _aware(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A timezone is required")
    return result


def make_transport(*, scenario: str = "normal") -> httpx.MockTransport:
    """Return an in-memory HTTP transport, validating success against the contract.

    Supported scenarios: normal, large, undated, forbidden, unavailable and malformed.
    Authentication here models a synthetic service response; the module itself
    delegates authorization to whichever core handles the HTTP request.
    """
    if scenario not in {"normal", "large", "undated", "forbidden", "unavailable", "malformed"}:
        raise ValueError("Unknown synthetic scenario")
    contract = json.loads(files(__package__).joinpath("openapi.json").read_text(encoding="utf-8"))

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("X-API-Key") != DUMMY_OPERATOR_KEY:
            return httpx.Response(401, json={"detail": "Synthetic credentials rejected"})
        if scenario == "forbidden":
            return httpx.Response(403, json={"detail": "Synthetic role is not an operator"})
        if scenario == "unavailable":
            return httpx.Response(503, json={"detail": "Synthetic service unavailable"})
        if request.method != "GET":
            return httpx.Response(405)
        path = request.url.path
        prefix = "/api/superadmin/tenants"
        if path == prefix:
            route = path
            try:
                skip = int(request.url.params.get("skip", "0"))
                limit = int(request.url.params.get("limit", "50"))
                if skip < 0 or not 1 <= limit <= 500:
                    raise ValueError("Invalid page")
                rows = list(SYNTHETIC_TENANTS)
                if scenario == "large":
                    rows = [
                        {
                            "tenantId": f"synthetic-paged-{index:04d}",
                            "status": "active",
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                        for index in range(503)
                    ]
                if scenario == "undated":
                    rows = [{**rows[0], "createdAt": None}, *rows[1:]]
                for query, field in (("status", "status"), ("tier", "tier"), ("billing_channel", "billingChannel")):
                    if query in request.url.params:
                        rows = [row for row in rows if row.get(field) == request.url.params[query]]
            except (ValueError, KeyError):
                return httpx.Response(422, json={"detail": []})
            payload = {"tenants": rows[skip : skip + limit], "total": len(rows), "skip": skip, "limit": limit}
        else:
            return httpx.Response(404)
        if scenario == "malformed":
            return httpx.Response(200, json={"invalid": "synthetic malformed response"})
        schema = contract["paths"][route]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator({**schema, "components": contract["components"]}).validate(payload)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(respond)
