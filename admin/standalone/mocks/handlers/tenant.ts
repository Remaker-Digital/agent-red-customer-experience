// @ts-nocheck
/**
 * Tenant handlers - lookup, activation status, whoami, product version.
 * © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
 */
import { GET, POST } from "../router";
import { getStore, resetStore } from "../store";

/** Explicitly rejected API keys for auth UI failure-path tests. */
const INVALID_API_KEYS = new Set([
  "invalid",
  "invalid-key",
  "ar_invalid_key",
  "bad-key",
]);

export function registerTenantHandlers() {
  const s = () => getStore().tenant;

  // SPEC-1644: validate-key replaces lookup for API key auth.
  // Reject known-bad keys so Playwright can assert the login error path;
  // accept session-token auth (post-2FA) which sends X-Session-Token instead.
  POST("/api/tenants/auth/validate-key", (req) => {
    const key = (req.headers?.["x-api-key"] || "").trim();
    const session = (req.headers?.["x-session-token"] || "").trim();
    if (session) {
      return {
        status: 200,
        body: { valid: true, ...s().lookup },
      };
    }
    if (!key || INVALID_API_KEYS.has(key) || key.toLowerCase().includes("invalid")) {
      return { status: 401, body: { detail: "Invalid API key" } };
    }
    return {
      status: 200,
      body: { valid: true, ...s().lookup },
    };
  });

  // Channel lookup (shop/stripe_customer_id) still uses GET
  GET("/api/tenants/lookup", () => ({
    status: 200,
    body: s().lookup,
  }));

  // activation-status is registered in config.ts (authoritative handler for config store)

  GET("/api/admin/team/whoami", () => ({
    status: 200,
    body: s().whoami,
  }));

  GET("/api/admin/product-version", () => ({
    status: 200,
    body: { version: s().productVersion },
  }));

  GET("/api/health", () => ({
    status: 200,
    body: { status: "healthy", product_version: s().productVersion },
  }));

  // Test-only: reset store to initial fixture state (used by e2e_mock tests)
  POST("/api/__test__/reset", () => {
    resetStore();
    return { status: 200, body: { success: true, message: "Store reset to fixture data" } };
  });

  // Test-only: mutate whoami role for ProtectedRoute / nav visibility suites
  POST("/api/__test__/set-whoami", (req) => {
    const body = (req.body || {}) as {
      role?: string;
      email?: string;
      display_name?: string;
    };
    const whoami = getStore().tenant.whoami;
    if (body.role) whoami.role = body.role;
    if (body.email) whoami.email = body.email;
    if (body.display_name) whoami.display_name = body.display_name;
    return { status: 200, body: { ...whoami } };
  });
}
