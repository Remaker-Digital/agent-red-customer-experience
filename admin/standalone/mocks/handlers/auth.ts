// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
// @ts-nocheck
/**
 * Auth handlers — magic-link request/verify and 2FA challenge endpoints for mock mode.
 *
 * Used by Playwright auth / role-gating suites so login and MFA UI paths are
 * exercised against the Vite mock backend (not sessionStorage injection alone).
 *
 * © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
 */
import { GET, POST } from "../router";

/** Tokens / codes accepted by the mock MFA surfaces. */
export const MOCK_VALID_TOTP = "123456";
export const MOCK_VALID_BACKUP = "ABCD1234";
export const MOCK_VALID_SMS = "654321";
export const MOCK_2FA_PENDING_TOKEN = "pending-2fa-token";
export const MOCK_SESSION_TOKEN = "mock-session-token-after-2fa";

export function registerAuthHandlers() {
  // Magic-link request — always 200 (anti-enumeration), same as production.
  POST("/api/auth/magic-link/request", () => ({
    status: 200,
    body: { message: "If an account exists, a sign-in link has been sent." },
  }));

  // Magic-link verify (URL token path used by App mount effect).
  GET("/api/auth/magic-link/verify", (req) => {
    const token = (req.query.token || "").trim();
    if (token === "requires-2fa" || token === MOCK_2FA_PENDING_TOKEN) {
      return {
        status: 200,
        body: {
          requires_2fa: true,
          pending_2fa_token: MOCK_2FA_PENDING_TOKEN,
          tenant_id: "mock-tenant-001",
          email: "admin@mockstore.com",
          mfa_methods: ["totp", "sms"],
        },
      };
    }
    if (token === "valid-session" || token.startsWith("session-")) {
      return {
        status: 200,
        body: {
          session_token: MOCK_SESSION_TOKEN,
          tenant_id: "mock-tenant-001",
          email: "admin@mockstore.com",
        },
      };
    }
    return {
      status: 401,
      body: { message: "This sign-in link is invalid or has expired." },
    };
  });

  // 6-digit sign-in code (alternative to clicking the email link).
  POST("/api/auth/magic-link/verify-code", (req) => {
    const body = (req.body || {}) as { code?: string; tenant?: string };
    const code = (body.code || "").trim();
    if (code === "222222") {
      return {
        status: 200,
        body: {
          requires_2fa: true,
          pending_2fa_token: MOCK_2FA_PENDING_TOKEN,
          tenant_id: body.tenant || "mock-tenant-001",
          email: "admin@mockstore.com",
          mfa_methods: ["totp", "sms"],
        },
      };
    }
    if (code === "111111") {
      return {
        status: 200,
        body: { session_token: MOCK_SESSION_TOKEN },
      };
    }
    return {
      status: 401,
      body: { message: "Invalid or expired code. Please request a new one." },
    };
  });

  POST("/api/auth/2fa/totp/verify", (req) => {
    const body = (req.body || {}) as { pending_token?: string; code?: string };
    if ((body.code || "").trim() === MOCK_VALID_TOTP) {
      return { status: 200, body: { session_token: MOCK_SESSION_TOKEN } };
    }
    return { status: 401, body: { message: "Invalid code. Please try again." } };
  });

  POST("/api/auth/2fa/totp/backup-verify", (req) => {
    const body = (req.body || {}) as { pending_token?: string; code?: string };
    if ((body.code || "").trim().toUpperCase() === MOCK_VALID_BACKUP) {
      return { status: 200, body: { session_token: MOCK_SESSION_TOKEN } };
    }
    return { status: 401, body: { message: "Invalid backup code. Please try again." } };
  });

  POST("/api/auth/2fa/sms/request", () => ({
    status: 200,
    body: { phone_hint: "***-***-1234" },
  }));

  POST("/api/auth/2fa/sms/verify", (req) => {
    const body = (req.body || {}) as { pending_token?: string; code?: string };
    if ((body.code || "").trim() === MOCK_VALID_SMS) {
      return { status: 200, body: { session_token: MOCK_SESSION_TOKEN } };
    }
    return { status: 401, body: { message: "Invalid code. Please try again." } };
  });

  // API key reset — always 200 (anti-enumeration).
  POST("/api/admin/api-keys/reset", () => ({
    status: 200,
    body: { message: "If an account exists, a new API key has been sent." },
  }));
}
