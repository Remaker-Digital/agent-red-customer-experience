// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
// The 6-digit code and the email link share this decision.
import { applyVerifiedSignIn } from "../../admin/standalone/login/verifiedSignIn.js";

const report = {
  session_token: null,
  second_factor: null,
  direct_session: null,
};

applyVerifiedSignIn(
  {
    requires_2fa: true,
    pending_2fa_token: "pending-2fa-token",
    tenant_id: "tenant-1",
    email: "ada@example.com",
    mfa_methods: ["totp"],
  },
  {
    onSession(sessionToken) {
      report.session_token = sessionToken;
    },
    onSecondFactor(state) {
      report.second_factor = state;
    },
  },
);

applyVerifiedSignIn(
  { session_token: "session-token" },
  {
    onSession(sessionToken) {
      report.direct_session = sessionToken;
    },
    onSecondFactor(state) {
      report.second_factor = state;
    },
  },
);

process.stdout.write(JSON.stringify(report));
