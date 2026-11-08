// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
// The email link and the 6-digit code share this next step.
// Plain JavaScript so the unit test runs on Node 20.

export function applyVerifiedSignIn(data, actions) {
  if (data.requires_2fa && data.pending_2fa_token) {
    actions.onSecondFactor({
      pendingToken: data.pending_2fa_token,
      tenantId: data.tenant_id || "",
      email: data.email || "",
      mfaMethods: data.mfa_methods || ["totp"],
    });
    return;
  }
  if (data.session_token) {
    actions.onSession(data.session_token);
  }
}
