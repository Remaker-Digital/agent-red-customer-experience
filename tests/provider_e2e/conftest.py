# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""
E2E fixtures for the Provider admin SPA (`admin/provider`).

Mirrors ``tests/e2e``: local Vite on port 3400 + Playwright ``page.route``
interception of ``/api/**``. No backend required. Auth is injected via
``sessionStorage.agentred_provider_key`` so tests exercise authenticated
surfaces; login/MFA remain deferred (see progress note).

Vite is launched with ``npx vite`` (not ``npm run dev``) so Linux ADO agents
skip the Windows-only ``predev`` PowerShell env sync.

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, Route

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROVIDER_VITE_PORT = 3400
PROVIDER_DIR = Path(__file__).resolve().parent.parent.parent / "admin" / "provider"
PROVIDER_BASE = f"http://localhost:{PROVIDER_VITE_PORT}/admin/provider"
MOCK_PROVIDER_KEY = "ar_spa_plat_e2e_test_key"

# Nav must match ProviderLayout.tsx NAV_GROUPS (paths + labels).
NAV_ITEMS: list[tuple[str, str]] = [
    ("Dashboard", "/"),
    ("Tenants", "/tenants"),
    ("Deployments", "/deployments"),
    ("Integrations", "/integrations"),
    ("Status Page", "/status"),
    ("Alerts", "/alerts"),
    ("Diagnostics", "/diagnostics"),
    ("Co-Pilot Knowledge", "/copilot-knowledge"),
    ("Pipeline Observatory", "/pipeline"),
    ("Contact Messages", "/contact-messages"),
    ("Service Messages", "/service-messages"),
    ("Entitlements", "/entitlements"),
    ("Rate Limits", "/rate-limits"),
    ("Blocklists", "/blocklists"),
    ("Alert Thresholds", "/alert-thresholds"),
    ("Notifications", "/notification-channels"),
    ("Test Execution", "/test-execution"),
    ("Maintenance", "/maintenance"),
    ("Compliance", "/compliance"),
    ("Secrets", "/secrets"),
    ("Billing", "/billing"),
    ("Cost Analytics", "/costs"),
    ("SLA Trends", "/sla"),
    ("Abuse Detection", "/abuse"),
    ("User Management", "/users"),
    ("MFA Settings", "/mfa"),
]


# ---------------------------------------------------------------------------
# Mock payloads — shapes match SPA TypeScript interfaces (not Vite mock drift)
# ---------------------------------------------------------------------------

MOCK_DASHBOARD = {
    "timestamp": "2026-03-28T12:00:00Z",
    "systemHealth": {
        "cosmos": {"healthy": True, "status": "connected", "detail": "Latency: 8ms"},
        "redis": {"healthy": True, "status": "connected", "detail": "Memory: 42%"},
        "keyVault": {"healthy": True, "status": "connected", "detail": "Secrets: 4 active"},
        "version": {"api": "1.0.0", "product": "1.98.65-e2e"},
    },
    "tenantSummary": {
        "totalTenants": 3,
        "byStatus": {"active": 2, "trial": 1},
        "byTier": {"professional": 1, "starter": 1, "trial": 1},
        "byBillingChannel": {"stripe": 2, "shopify": 1},
    },
    "recentDeployments": [
        {
            "eventType": "MODEL_DEPLOYED",
            "timestamp": "2026-03-28T10:30:00Z",
            "actor": "ci/cd",
            "payload": {"version": "1.98.65", "environment": "staging", "status": "success"},
        },
    ],
    "slaSummary": {
        "uptime30d": 99.97,
        "p50LatencyMs": 135,
        "p99LatencyMs": 890,
        "incidents30d": 0,
    },
    "usageSummary": {
        "conversations24h": 847,
        "apiRequests24h": 12847,
        "errorRate24h": 0.003,
        "avgLatencyMs": 142,
    },
    "recentAlerts": [],
}

MOCK_TENANTS = [
    {
        "tenantId": "acme-corp-001",
        "displayName": "Acme Corporation",
        "status": "active",
        "tier": "enterprise",
        "billingChannel": "stripe",
        "customerEmail": "admin@acme-corp.com",
        "createdAt": "2025-10-01T09:00:00Z",
        "updatedAt": "2026-03-01T09:00:00Z",
        "deactivatedAt": None,
        "consentStatus": "granted",
        "expiresAt": None,
    },
    {
        "tenantId": "example-store",
        "displayName": "Blanco Storefront",
        "status": "active",
        "tier": "professional",
        "billingChannel": "shopify",
        "customerEmail": "shop@blanco.com",
        "createdAt": "2025-09-20T15:30:00Z",
        "updatedAt": "2026-03-01T09:00:00Z",
        "deactivatedAt": None,
        "consentStatus": "granted",
        "expiresAt": "2026-12-01T00:00:00Z",
    },
    {
        "tenantId": "trial-user-001",
        "displayName": "Trial Shop",
        "status": "trial",
        "tier": "trial",
        "billingChannel": "shopify",
        "customerEmail": "trial@newshop.com",
        "createdAt": "2026-03-01T14:00:00Z",
        "updatedAt": "2026-03-01T14:00:00Z",
        "deactivatedAt": None,
        "consentStatus": "pending",
        "expiresAt": "2026-03-15T14:00:00Z",
    },
]

MOCK_TENANT_SUMMARY = {
    "totalTenants": 3,
    "byStatus": {"active": 2, "trial": 1},
    "byTier": {"enterprise": 1, "professional": 1, "trial": 1},
    "byBillingChannel": {"stripe": 1, "shopify": 2},
}

MOCK_DEPLOYMENTS = {
    "deployments": [
        {
            "deployId": "dep-001",
            "environment": "staging",
            "version": "v1.98.65",
            "action": "full",
            "status": "succeeded",
            "triggeredBy": "spa-console",
            "startedAt": "2026-03-28T10:00:00Z",
            "completedAt": "2026-03-28T10:12:00Z",
            "durationS": 720,
            "steps": [
                {
                    "name": "build_gateway",
                    "status": "succeeded",
                    "startedAt": "2026-03-28T10:00:00Z",
                    "completedAt": "2026-03-28T10:04:00Z",
                },
                {
                    "name": "deploy_gateway",
                    "status": "succeeded",
                    "startedAt": "2026-03-28T10:04:00Z",
                    "completedAt": "2026-03-28T10:10:00Z",
                },
                {
                    "name": "health_check",
                    "status": "succeeded",
                    "startedAt": "2026-03-28T10:10:00Z",
                    "completedAt": "2026-03-28T10:12:00Z",
                },
            ],
            "error": None,
            "previousImage": None,
        },
        {
            "deployId": "dep-002",
            "environment": "staging",
            "version": "v1.98.64",
            "action": "deploy",
            "status": "failed",
            "triggeredBy": "ci",
            "startedAt": "2026-03-27T09:00:00Z",
            "completedAt": "2026-03-27T09:08:00Z",
            "durationS": 480,
            "steps": [
                {
                    "name": "deploy_gateway",
                    "status": "failed",
                    "startedAt": "2026-03-27T09:00:00Z",
                    "completedAt": "2026-03-27T09:08:00Z",
                },
            ],
            "error": "Health check failed",
            "previousImage": "v1.98.63",
        },
    ],
    "total": 2,
}

MOCK_PIPELINE_INFRA = {
    "nodes": [
        {
            "nodeId": "api-gateway",
            "label": "API Gateway",
            "category": "azure",
            "nodeType": "container-app",
            "status": "healthy",
            "metrics": {"invocation_count": 48200, "avg_latency_ms": 45, "error_rate": 0.001},
            "position": {"x": 2, "y": 1},
        },
        {
            "nodeId": "nats",
            "label": "NATS JetStream",
            "category": "azure",
            "nodeType": "nats",
            "status": "degraded",
            "metrics": {},
            "position": {"x": 3.5, "y": 4},
        },
    ],
    "edges": [
        {
            "source": "widget",
            "target": "api-gateway",
            "label": "Chat API + streaming",
            "volume": 28400,
            "avgLatencyMs": 35,
            "errorRate": 0.002,
            "protocol": "HTTPS/SSE",
        },
    ],
    "totalRequests": 48200,
    "period": "24h",
}

MOCK_PIPELINE_TOPOLOGY = {
    "nodes": [
        {
            "agent": "intent-classifier",
            "invocationCount": 12847,
            "avgLatencyMs": 180,
            "p50LatencyMs": 140,
            "p95LatencyMs": 380,
            "p99LatencyMs": 450,
            "errorRate": 0.002,
            "avgTokensIn": 320,
            "avgTokensOut": 45,
            "avgCost": 0.0012,
        },
        {
            "agent": "response-generator",
            "invocationCount": 12847,
            "avgLatencyMs": 1200,
            "p50LatencyMs": 980,
            "p95LatencyMs": 2800,
            "p99LatencyMs": 3200,
            "errorRate": 0.003,
            "avgTokensIn": 1500,
            "avgTokensOut": 420,
            "avgCost": 0.0185,
        },
    ],
    "edges": [
        {
            "source": "intent-classifier",
            "target": "knowledge-retrieval",
            "volume": 8421,
            "avgTransitionLatencyMs": 12,
            "dropOffRate": 0.02,
        },
    ],
    "totalConversations": 12847,
    "period": "24h",
}

MOCK_PIPELINE_TENANTS = {
    "tenants": [
        {
            "tenantId": "acme-corp-001",
            "displayName": "Acme Corporation",
            "tier": "enterprise",
            "totalConversations": 847,
            "billableConversations": 812,
            "avgLatencyMs": 1150,
            "errorRate": 0.002,
            "escalationRate": 0.03,
            "tokenConsumption": 542100,
            "cost": 16.26,
            "estimatedRu": 2965,
            "resolutionRate": 0.87,
        },
        {
            "tenantId": "example-store",
            "displayName": "Blanco Storefront",
            "tier": "professional",
            "totalConversations": 421,
            "billableConversations": 398,
            "avgLatencyMs": 1280,
            "errorRate": 0.003,
            "escalationRate": 0.04,
            "tokenConsumption": 287400,
            "cost": 8.62,
            "estimatedRu": 1474,
            "resolutionRate": 0.82,
        },
    ],
    "total": 2,
    "sortBy": "totalConversations",
    "sortOrder": "desc",
}

MOCK_ENTITLEMENTS = {
    "documents": [
        {
            "configType": "tier",
            "configKey": "tier.professional",
            "value": {"maxAgents": 5, "memoryEnabled": True},
            "version": 3,
            "updatedAt": "2026-03-20T08:00:00Z",
            "updatedBy": "admin@remaker.digital",
        },
        {
            "configType": "pricing",
            "configKey": "pricing.starter",
            "value": {"monthlyUsd": 49},
            "version": 1,
            "updatedAt": "2026-02-01T08:00:00Z",
            "updatedBy": "admin@remaker.digital",
        },
    ],
}

MOCK_ENTITLEMENT_DIFF = {
    "entries": [
        {
            "configKey": "tier.professional",
            "hasLiveDoc": True,
            "frozenKeys": ["maxAgents"],
            "liveKeys": ["maxAgents", "memoryEnabled"],
            "differences": ["live has extra key: memoryEnabled"],
        },
        {
            "configKey": "pricing.starter",
            "hasLiveDoc": True,
            "frozenKeys": ["monthlyUsd"],
            "liveKeys": ["monthlyUsd"],
            "differences": [],
        },
    ],
}

MOCK_ENTITLEMENT_HISTORY = {
    "entries": [
        {
            "id": "hist-001",
            "action": "update",
            "configKey": "tier.professional",
            "actor": "admin@remaker.digital",
            "timestamp": "2026-03-20T08:00:00Z",
            "previousVersion": 2,
            "newVersion": 3,
            "changeReason": "Enable memory for professional",
            "diffSummary": ["memoryEnabled: false -> true"],
        },
    ],
}

MOCK_MAINTENANCE = {
    "isActive": False,
    "version": 1,
    "updatedAt": "2026-03-01T00:00:00Z",
    "updatedBy": "admin@remaker.digital",
    "state": {
        "enabled": False,
        "message": "Scheduled maintenance",
        "retryAfterSeconds": 300,
        "scheduledStart": None,
        "scheduledEnd": None,
        "exemptIps": ["127.0.0.1"],
    },
}

MOCK_INCIDENTS = {
    "incidents": [
        {
            "incidentId": "inc-active-001",
            "title": "Elevated API latency",
            "description": "p99 latency above SLO in East US",
            "status": "investigating",
            "severity": "major",
            "affectedServices": ["API", "Widget"],
            "updates": [
                {
                    "timestamp": "2026-03-28T11:00:00Z",
                    "status": "investigating",
                    "message": "Investigating elevated p99",
                    "author": "ops@remaker.digital",
                },
            ],
            "createdAt": "2026-03-28T11:00:00Z",
            "updatedAt": "2026-03-28T11:00:00Z",
            "resolvedAt": None,
            "createdBy": "ops@remaker.digital",
        },
        {
            "incidentId": "inc-resolved-001",
            "title": "Scheduled database migration",
            "description": "Completed maintenance window",
            "status": "resolved",
            "severity": "minor",
            "affectedServices": ["Cosmos DB"],
            "updates": [
                {
                    "timestamp": "2026-03-20T02:00:00Z",
                    "status": "resolved",
                    "message": "Migration complete",
                    "author": "ci/cd",
                },
            ],
            "createdAt": "2026-03-20T00:00:00Z",
            "updatedAt": "2026-03-20T04:00:00Z",
            "resolvedAt": "2026-03-20T04:00:00Z",
            "createdBy": "ci/cd",
        },
    ],
    "total": 2,
}

MOCK_ALERT_RULES = {
    "rules": [
        {
            "ruleId": "rule-001",
            "ruleType": "sla_breach",
            "name": "High Error Rate",
            "description": "Fires when error rate exceeds 5%",
            "enabled": True,
            "condition": {"metric": "error_rate", "operator": "gt", "threshold": 5},
            "notificationChannels": ["email"],
            "cooldownMinutes": 30,
            "runbookUrl": "",
            "createdAt": "2025-12-01T10:00:00Z",
            "updatedAt": "2025-12-01T10:00:00Z",
        },
        {
            "ruleId": "rule-003",
            "ruleType": "queue_depth",
            "name": "Queue Depth",
            "description": "Fires when queue depth exceeds 1000",
            "enabled": False,
            "condition": {"metric": "queue_depth", "operator": "gt", "threshold": 1000},
            "notificationChannels": [],
            "cooldownMinutes": 60,
            "runbookUrl": "",
            "createdAt": "2025-12-15T08:00:00Z",
            "updatedAt": "2025-12-15T08:00:00Z",
        },
    ],
    "total": 2,
}

MOCK_ALERT_HISTORY = {
    "alerts": [
        {
            "alertId": "alert-ack-pending",
            "alertDate": "2026-03-28",
            "ruleId": "rule-001",
            "ruleName": "High Error Rate",
            "ruleType": "sla_breach",
            "triggeredAt": "2026-03-28T10:00:00Z",
            "resolvedAt": None,
            "severity": "critical",
            "message": "Error rate exceeded 5% threshold (observed: 7.2%)",
            "metricValue": 7.2,
            "thresholdValue": 5,
            "acknowledged": False,
            "acknowledgedBy": None,
        },
        {
            "alertId": "alert-acked",
            "alertDate": "2026-03-10",
            "ruleId": "rule-001",
            "ruleName": "High Error Rate",
            "ruleType": "sla_breach",
            "triggeredAt": "2026-03-10T15:42:00Z",
            "resolvedAt": "2026-03-10T15:58:00Z",
            "severity": "warning",
            "message": "Prior spike acknowledged",
            "metricValue": 6.1,
            "thresholdValue": 5,
            "acknowledged": True,
            "acknowledgedBy": "admin",
        },
    ],
    "total": 2,
}

MOCK_BLOCKLISTS = {
    "lists": [
        {
            "listType": "email_domain",
            "entries": [
                {
                    "value": "spam.example.com",
                    "action": "block",
                    "reason": "Known spam domain",
                    "addedBy": "admin@remaker.digital",
                    "addedAt": "2026-02-01T00:00:00Z",
                },
            ],
            "defaultAction": "allow",
            "version": 2,
            "updatedAt": "2026-02-01T00:00:00Z",
            "updatedBy": "admin@remaker.digital",
        },
        {
            "listType": "ip",
            "entries": [],
            "defaultAction": "allow",
            "version": 1,
            "updatedAt": None,
            "updatedBy": None,
        },
    ],
}

MOCK_PLATFORM_USERS = [
    {
        "adminId": "admin-001",
        "email": "admin@remaker.digital",
        "displayName": "Platform Superadmin",
        "role": "superadmin",
        "isActive": True,
        "createdAt": "2025-09-01T10:00:00Z",
        "lastLoginAt": "2026-03-11T08:00:00Z",
        "notificationEmailAddress": "ops@remaker.digital",
        "backupCodesRemaining": 8,
        "createdBy": None,
    },
    {
        "adminId": "admin-002",
        "email": "ops@remaker.digital",
        "displayName": "Ops Operator",
        "role": "operator",
        "isActive": True,
        "createdAt": "2025-11-15T14:00:00Z",
        "lastLoginAt": "2026-03-10T16:30:00Z",
        "notificationEmailAddress": None,
        "backupCodesRemaining": 0,
        "createdBy": "admin-001",
    },
]

MOCK_DIAGNOSTICS = {
    "tenantId": "acme-corp-001",
    "status": "active",
    "tier": "enterprise",
    "billingChannel": "stripe",
    "createdAt": "2025-10-01T09:00:00Z",
    "lastActivityAt": "2026-03-28T11:00:00Z",
    "generatedAt": "2026-03-28T12:00:00Z",
    "collectionErrors": [],
    "configState": {
        "isActive": True,
        "isConfigured": True,
        "hasPendingChanges": False,
        "activeVersion": 3,
        "activatedAt": "2026-03-01T00:00:00Z",
    },
    "aiConfig": {
        "model": "gpt-4o",
        "brandNamePresent": True,
        "brandVoicePresent": True,
    },
    "knowledgeBase": {
        "totalArticles": 12,
        "activeCount": 10,
        "draftCount": 2,
    },
    "team": {"memberCount": 4, "rolesBreakdown": {"admin": 1, "agent": 3}},
    "conversations": {
        "last24hCount": 42,
        "last7dCount": 280,
        "statusBreakdown": {"open": 3, "resolved": 39},
    },
    "integrations": {
        "shopifyConnected": False,
        "stripeConnected": True,
        "natsDeployed": True,
        "natsConnected": True,
    },
    "widget": {
        "widgetKeyPresent": True,
        "originConfigured": True,
    },
}

MOCK_SERVICE_MESSAGE_RECIPIENTS = {
    "recipients": [
        {
            "tenantId": "acme-corp-001",
            "email": "admin@acme-corp.com",
            "tier": "enterprise",
            "status": "active",
        },
        {
            "tenantId": "example-store",
            "email": "shop@blanco.com",
            "tier": "professional",
            "status": "active",
        },
    ],
}


# ---------------------------------------------------------------------------
# API mocker
# ---------------------------------------------------------------------------


class ProviderApiMocker:
    """Playwright route interceptor for ``/api/superadmin/*`` (+ catch-all)."""

    def __init__(self) -> None:
        self.api_calls: list[dict[str, Any]] = []
        self._overrides: dict[str, dict[str, Any]] = {}
        self._tenant_list: list[dict[str, Any]] = list(MOCK_TENANTS)
        self._deployments: dict[str, Any] = json.loads(json.dumps(MOCK_DEPLOYMENTS))
        self._maintenance: dict[str, Any] = json.loads(json.dumps(MOCK_MAINTENANCE))
        self._incidents: dict[str, Any] = json.loads(json.dumps(MOCK_INCIDENTS))
        self._alert_rules: dict[str, Any] = json.loads(json.dumps(MOCK_ALERT_RULES))
        self._alert_history: dict[str, Any] = json.loads(json.dumps(MOCK_ALERT_HISTORY))
        self._blocklists: dict[str, Any] = json.loads(json.dumps(MOCK_BLOCKLISTS))
        self._platform_users: list[dict[str, Any]] = json.loads(json.dumps(MOCK_PLATFORM_USERS))

    def override(self, endpoint_pattern: str, response: Any, status: int = 200) -> None:
        self._overrides[endpoint_pattern] = {"body": response, "status": status}

    def get_calls(self, method: str | None = None, path_contains: str | None = None) -> list[dict[str, Any]]:
        result = self.api_calls
        if method:
            result = [c for c in result if c["method"] == method]
        if path_contains:
            result = [c for c in result if path_contains in c["url"]]
        return result

    def clear_calls(self) -> None:
        self.api_calls.clear()

    def handle_route(self, route: Route) -> None:
        url = route.request.url
        method = route.request.method
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        call_record: dict[str, Any] = {"url": url, "method": method, "path": path}
        if method in ("POST", "PUT", "PATCH"):
            try:
                call_record["body"] = route.request.post_data
            except Exception:
                call_record["body"] = None
        self.api_calls.append(call_record)

        for pattern, override in self._overrides.items():
            if pattern in url:
                body = override["body"]
                route.fulfill(
                    status=override["status"],
                    content_type="application/json",
                    body=json.dumps(body) if not isinstance(body, str) else body,
                )
                return

        def fulfill(payload: Any, status: int = 200) -> None:
            route.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps(payload),
            )

        # Dashboard
        if path.endswith("/api/superadmin/dashboard") and method == "GET":
            fulfill(MOCK_DASHBOARD)
            return

        # Tenants
        if path.endswith("/api/superadmin/tenants/summary") and method == "GET":
            fulfill(MOCK_TENANT_SUMMARY)
            return

        if path.endswith("/api/superadmin/tenants") and method == "GET":
            filtered = list(self._tenant_list)
            status_q = (query.get("status") or [None])[0]
            tier_q = (query.get("tier") or [None])[0]
            channel_q = (query.get("billing_channel") or [None])[0]
            if status_q:
                filtered = [t for t in filtered if t["status"] == status_q]
            if tier_q:
                filtered = [t for t in filtered if t["tier"] == tier_q]
            if channel_q:
                filtered = [t for t in filtered if t["billingChannel"] == channel_q]
            limit = int((query.get("limit") or ["25"])[0])
            skip = int((query.get("skip") or ["0"])[0])
            fulfill(
                {
                    "tenants": filtered[skip : skip + limit],
                    "total": len(filtered),
                    "skip": skip,
                    "limit": limit,
                }
            )
            return

        if path.endswith("/api/superadmin/tenants") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            tenant_id = "tenant-e2e-created-001"
            fulfill(
                {
                    "tenantId": tenant_id,
                    "status": "active",
                    "tier": body.get("tier", "starter"),
                    "superadminEmail": body.get("superadminEmail", ""),
                    "keysDeliveredViaEmail": True,
                    "warnings": [],
                },
                status=201,
            )
            return

        if "/api/superadmin/tenants/" in path and path.endswith("/resend-welcome-email") and method == "POST":
            tid = path.split("/tenants/")[1].split("/")[0]
            tenant = next((t for t in self._tenant_list if t["tenantId"] == tid), None)
            fulfill(
                {
                    "sent": True,
                    "sentTo": (tenant or {}).get("customerEmail", "unknown@example.com"),
                    "message": "Welcome email resent",
                }
            )
            return

        if "/api/superadmin/tenants/" in path and path.endswith("/expiry") and method == "PATCH":
            fulfill({"expiresAt": json.loads(route.request.post_data or "{}").get("expiresAt")})
            return

        if "/api/superadmin/tenants/" in path and path.endswith("/rate-limit") and method == "GET":
            fulfill({"rateLimitRpm": 300, "effectiveRpm": 300, "burst": 50})
            return

        if "/api/superadmin/tenants/" in path and path.endswith("/rate-limit") and method == "PATCH":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            rpm = body.get("rateLimitRpm", 300)
            fulfill({"rateLimitRpm": rpm, "effectiveRpm": rpm if rpm is not None else 300, "burst": 50})
            return

        # Deployments
        if path.endswith("/api/superadmin/deployments") and method == "GET":
            fulfill(self._deployments)
            return

        if path.endswith("/api/superadmin/deployments/trigger") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            fulfill(
                {
                    "deployId": "dep-triggered-001",
                    "status": "queued",
                    "version": body.get("version"),
                    "action": body.get("action", "full"),
                },
                status=202,
            )
            return

        # Pipeline
        if "/api/superadmin/pipeline/infrastructure" in path and method == "GET":
            payload = dict(MOCK_PIPELINE_INFRA)
            payload["period"] = (query.get("period") or ["24h"])[0]
            fulfill(payload)
            return

        if "/api/superadmin/pipeline/topology" in path and method == "GET":
            payload = dict(MOCK_PIPELINE_TOPOLOGY)
            payload["period"] = (query.get("period") or ["24h"])[0]
            fulfill(payload)
            return

        if path.rstrip("/").endswith("/api/superadmin/pipeline/tenants") and method == "GET":
            fulfill(MOCK_PIPELINE_TENANTS)
            return

        # Entitlements
        if path.endswith("/api/superadmin/entitlements") and method == "GET":
            fulfill(MOCK_ENTITLEMENTS)
            return
        if path.endswith("/api/superadmin/entitlements/diff") and method == "GET":
            fulfill(MOCK_ENTITLEMENT_DIFF)
            return
        if path.endswith("/api/superadmin/entitlements/history") and method == "GET":
            fulfill(MOCK_ENTITLEMENT_HISTORY)
            return
        if "/api/superadmin/entitlements/" in path and method == "PUT":
            fulfill({"ok": True, "version": 4})
            return

        # Maintenance
        if path.endswith("/api/superadmin/maintenance") and method == "GET":
            fulfill(self._maintenance)
            return
        if path.endswith("/api/superadmin/maintenance") and method == "PUT":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            self._maintenance = {
                "isActive": bool(body.get("enabled")),
                "version": int(self._maintenance.get("version") or 1) + 1,
                "updatedAt": "2026-03-28T12:00:00Z",
                "updatedBy": "e2e-operator",
                "state": {
                    "enabled": bool(body.get("enabled")),
                    "message": body.get("message", ""),
                    "retryAfterSeconds": body.get("retryAfterSeconds", 300),
                    "scheduledStart": body.get("scheduledStart"),
                    "scheduledEnd": body.get("scheduledEnd"),
                    "exemptIps": body.get("exemptIps") or [],
                },
            }
            fulfill(self._maintenance)
            return

        # Incidents / status page
        if path.endswith("/api/superadmin/incidents") and method == "GET":
            fulfill(self._incidents)
            return
        if path.endswith("/api/superadmin/incidents") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            new_inc = {
                "incidentId": "inc-created-e2e",
                "title": body.get("title", "untitled"),
                "description": body.get("description", ""),
                "status": "investigating",
                "severity": body.get("severity", "minor"),
                "affectedServices": body.get("affectedServices") or [],
                "updates": [],
                "createdAt": "2026-03-28T12:30:00Z",
                "updatedAt": "2026-03-28T12:30:00Z",
                "resolvedAt": None,
                "createdBy": "e2e-operator",
            }
            self._incidents["incidents"].insert(0, new_inc)
            self._incidents["total"] = len(self._incidents["incidents"])
            fulfill(new_inc, status=201)
            return
        if "/api/superadmin/incidents/" in path and path.endswith("/update") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            iid = path.split("/incidents/")[1].split("/")[0]
            for inc in self._incidents["incidents"]:
                if inc["incidentId"] == iid:
                    inc["status"] = body.get("status", inc["status"])
                    inc["updates"].append(
                        {
                            "timestamp": "2026-03-28T12:45:00Z",
                            "status": body.get("status", "monitoring"),
                            "message": body.get("message", ""),
                            "author": "e2e-operator",
                        }
                    )
                    break
            fulfill({"ok": True, "incidentId": iid})
            return
        if "/api/superadmin/incidents/" in path and path.endswith("/resolve") and method == "POST":
            iid = path.split("/incidents/")[1].split("/")[0]
            for inc in self._incidents["incidents"]:
                if inc["incidentId"] == iid:
                    inc["status"] = "resolved"
                    inc["resolvedAt"] = "2026-03-28T13:00:00Z"
                    break
            fulfill({"ok": True, "incidentId": iid})
            return

        # Alert rules + history
        if path.endswith("/api/superadmin/alerts/rules") and method == "GET":
            fulfill(self._alert_rules)
            return
        if path.endswith("/api/superadmin/alerts/rules") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            rule = {
                "ruleId": "rule-created-e2e",
                "ruleType": body.get("ruleType", "queue_depth"),
                "name": body.get("name", "unnamed"),
                "description": body.get("description", ""),
                "enabled": True,
                "condition": body.get("condition") or {"metric": "queue_depth", "operator": "gt", "threshold": 0},
                "notificationChannels": body.get("notificationChannels") or ["email"],
                "cooldownMinutes": body.get("cooldownMinutes", 60),
                "runbookUrl": body.get("runbookUrl", ""),
                "createdAt": "2026-03-28T12:00:00Z",
                "updatedAt": "2026-03-28T12:00:00Z",
            }
            self._alert_rules["rules"].append(rule)
            self._alert_rules["total"] = len(self._alert_rules["rules"])
            fulfill(rule, status=201)
            return
        if "/api/superadmin/alerts/rules/" in path and method == "PUT":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            rid = path.rstrip("/").split("/")[-1]
            for rule in self._alert_rules["rules"]:
                if rule["ruleId"] == rid:
                    if "enabled" in body and len(body) == 1:
                        rule["enabled"] = bool(body["enabled"])
                    else:
                        rule.update({k: v for k, v in body.items() if k != "condition"})
                        if "condition" in body:
                            rule["condition"] = body["condition"]
                    rule["updatedAt"] = "2026-03-28T12:30:00Z"
                    fulfill(rule)
                    return
            fulfill({"detail": "rule not found"}, status=404)
            return
        if "/api/superadmin/alerts/rules/" in path and method == "DELETE":
            rid = path.rstrip("/").split("/")[-1]
            before = len(self._alert_rules["rules"])
            self._alert_rules["rules"] = [r for r in self._alert_rules["rules"] if r["ruleId"] != rid]
            self._alert_rules["total"] = len(self._alert_rules["rules"])
            fulfill({"ok": True, "deleted": before - len(self._alert_rules["rules"])})
            return
        if path.endswith("/api/superadmin/alerts/history") or (
            "/api/superadmin/alerts/history?" in url and method == "GET"
        ):
            if method == "GET":
                fulfill(self._alert_history)
                return
        if "/api/superadmin/alerts/history/" in path and path.endswith("/acknowledge") and method == "POST":
            aid = path.split("/history/")[1].split("/")[0]
            for item in self._alert_history["alerts"]:
                if item["alertId"] == aid:
                    item["acknowledged"] = True
                    item["acknowledgedBy"] = "e2e-operator"
                    break
            fulfill({"ok": True, "alertId": aid})
            return
        if path.endswith("/api/superadmin/alerts/evaluate") and method == "POST":
            fulfill({"evaluated": True, "message": "Evaluation complete"})
            return

        # Blocklists
        if path.endswith("/api/superadmin/blocklists") and method == "GET":
            fulfill(self._blocklists)
            return
        if "/api/superadmin/blocklists/" in path and path.endswith("/check") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            list_type = path.split("/blocklists/")[1].split("/")[0]
            value = body.get("value", "")
            matched = None
            action = "allow"
            reason = ""
            for lst in self._blocklists["lists"]:
                if lst["listType"] != list_type:
                    continue
                action = lst["defaultAction"]
                for entry in lst["entries"]:
                    if entry["value"] == value or value.endswith(entry["value"]):
                        matched = entry["value"]
                        action = entry["action"]
                        reason = entry.get("reason") or ""
                        break
            fulfill(
                {
                    "listType": list_type,
                    "value": value,
                    "action": action,
                    "matchedEntry": matched,
                    "reason": reason,
                }
            )
            return
        if "/api/superadmin/blocklists/" in path and method == "PUT":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            list_type = path.rstrip("/").split("/")[-1]
            for lst in self._blocklists["lists"]:
                if lst["listType"] == list_type:
                    lst["entries"] = body.get("entries") or []
                    lst["defaultAction"] = body.get("defaultAction", lst["defaultAction"])
                    lst["version"] = int(lst.get("version") or 1) + 1
                    lst["updatedAt"] = "2026-03-28T12:00:00Z"
                    fulfill(lst)
                    return
            fulfill({"detail": "list not found"}, status=404)
            return

        # Platform admin users
        if path.endswith("/api/superadmin/platform-admin/users") and method == "GET":
            fulfill(self._platform_users)
            return
        if path.endswith("/api/superadmin/platform-admin/users") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            created = {
                "adminId": "admin-created-e2e",
                "email": body.get("email", ""),
                "displayName": body.get("displayName", ""),
                "role": "operator",
                "apiKey": "ar_spa_plat_created_e2e_key",
                "message": "Operator created",
            }
            self._platform_users.append(
                {
                    "adminId": created["adminId"],
                    "email": created["email"],
                    "displayName": created["displayName"],
                    "role": "operator",
                    "isActive": True,
                    "createdAt": "2026-03-28T12:00:00Z",
                    "lastLoginAt": None,
                    "notificationEmailAddress": None,
                    "backupCodesRemaining": 0,
                    "createdBy": "admin-001",
                }
            )
            fulfill(created, status=201)
            return
        if path.endswith("/api/superadmin/platform-admin/users/backup-codes") and method == "POST":
            fulfill(
                {
                    "adminId": "admin-001",
                    "codes": ["AAAA-1111", "BBBB-2222", "CCCC-3333"],
                    "count": 3,
                    "message": "Backup codes generated",
                }
            )
            return
        if path.endswith("/api/superadmin/platform-admin/users/notification-email") and method == "PUT":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            if self._platform_users:
                self._platform_users[0]["notificationEmailAddress"] = body.get("email")
            fulfill({"ok": True})
            return
        if "/api/superadmin/platform-admin/users/" in path and method == "DELETE":
            aid = path.rstrip("/").split("/")[-1]
            self._platform_users = [u for u in self._platform_users if u["adminId"] != aid]
            fulfill({"ok": True, "adminId": aid})
            return

        # Support diagnostics
        if "/api/superadmin/diagnostics/" in path and path.endswith("/errors") and method == "GET":
            fulfill(
                {
                    "tenantId": path.split("/diagnostics/")[1].split("/")[0],
                    "entries": [],
                    "total": 0,
                    "generatedAt": "2026-03-28T12:00:00Z",
                }
            )
            return
        if "/api/superadmin/diagnostics/" in path and method == "GET":
            tid = path.rstrip("/").split("/")[-1]
            if tid in ("missing-tenant", "unknown"):
                fulfill({"detail": "Tenant not found"}, status=404)
                return
            payload = dict(MOCK_DIAGNOSTICS)
            payload["tenantId"] = tid
            fulfill(payload)
            return

        # Service messages
        if "/api/superadmin/service-messages/preview" in path and method == "POST":
            fulfill(MOCK_SERVICE_MESSAGE_RECIPIENTS)
            return
        if path.endswith("/api/superadmin/service-messages/send") and method == "POST":
            try:
                body = json.loads(route.request.post_data or "{}")
            except json.JSONDecodeError:
                body = {}
            recipients = MOCK_SERVICE_MESSAGE_RECIPIENTS["recipients"]
            fulfill(
                {
                    "success": True,
                    "sentCount": len(recipients),
                    "failedCount": 0,
                    "totalRecipients": len(recipients),
                    "subject": body.get("subject", ""),
                    "errors": [],
                }
            )
            return

        # Unknown endpoints: 404 (not empty 200). Empty 200 objects crash pages
        # that setData(json) then read `.length` on missing arrays.
        if "/api/" in url:
            fulfill({"detail": "Mock endpoint not registered in provider_e2e"}, status=404)
            return

        route.continue_()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _port_is_open(port: int) -> bool:
    for family, addr in [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((addr, port))
                return True
        except (ConnectionRefusedError, OSError):
            continue
    return False


def navigate_provider(page: Page, path: str, wait_text: str | None = None) -> None:
    """Navigate within the provider SPA and wait for content."""
    url = f"{PROVIDER_BASE}/{path.lstrip('/')}" if path not in ("", "/") else f"{PROVIDER_BASE}/"
    page.goto(url, wait_until="domcontentloaded")
    if wait_text:
        page.wait_for_selector(f"text={wait_text}", timeout=15_000)
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def provider_vite_server():
    """Start Vite for admin/provider on port 3400 (session-scoped)."""
    node_modules = PROVIDER_DIR / "node_modules"
    if not node_modules.exists():
        pytest.skip("admin/provider/node_modules not found — run `cd admin/provider && npm ci`")

    if _port_is_open(PROVIDER_VITE_PORT):
        yield None
        return

    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    env = {
        **os.environ,
        "VITE_API_URL": "",
        "BROWSER": "none",
        "NO_COLOR": "1",
        "CI": "true",
    }
    # Avoid `npm run dev` → predev PowerShell sync on Linux ADO agents.
    proc = subprocess.Popen(
        ["npx", "vite", "--port", str(PROVIDER_VITE_PORT), "--strictPort"],
        cwd=str(PROVIDER_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
        env=env,
    )

    for _ in range(45):
        if _port_is_open(PROVIDER_VITE_PORT):
            break
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"Provider Vite exited with code {proc.returncode}.\nstdout: {stdout}\nstderr: {stderr}")
        time.sleep(1)
    else:
        proc.kill()
        raise RuntimeError(f"Provider Vite did not start on port {PROVIDER_VITE_PORT} within 45 s")

    yield proc

    if sys.platform == "win32":
        try:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            subprocess.run(f"taskkill /F /T /PID {proc.pid}", shell=True, capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def api_mocker() -> ProviderApiMocker:
    return ProviderApiMocker()


@pytest.fixture()
def provider_page(page: Page, provider_vite_server, api_mocker: ProviderApiMocker) -> Page:
    """Authenticated provider SPA page with API interception."""
    page.route("**/api/**", api_mocker.handle_route)
    page.add_init_script(
        f"""
        try {{
            sessionStorage.setItem('agentred_provider_key', '{MOCK_PROVIDER_KEY}');
        }} catch (e) {{}}
        """
    )
    page.goto(f"{PROVIDER_BASE}/", wait_until="domcontentloaded")
    page.wait_for_selector("text=Platform Dashboard", timeout=20_000)
    page._api_mocker = api_mocker  # type: ignore[attr-defined]
    return page


def setup_provider_page(page: Page, api_mocker: ProviderApiMocker, path: str = "/") -> Page:
    """Custom setup for tests that need overrides before first navigation."""
    page.route("**/api/**", api_mocker.handle_route)
    page.add_init_script(
        f"""
        try {{
            sessionStorage.setItem('agentred_provider_key', '{MOCK_PROVIDER_KEY}');
        }} catch (e) {{}}
        """
    )
    target = f"{PROVIDER_BASE}/" if path in ("", "/") else f"{PROVIDER_BASE}/{path.lstrip('/')}"
    page.goto(target, wait_until="domcontentloaded")
    page._api_mocker = api_mocker  # type: ignore[attr-defined]
    return page
