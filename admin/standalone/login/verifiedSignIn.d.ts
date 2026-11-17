// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT

export interface SecondFactorState {
  pendingToken: string;
  tenantId: string;
  email: string;
  mfaMethods: string[];
}

export interface VerifiedSignIn {
  requires_2fa?: boolean;
  pending_2fa_token?: string;
  session_token?: string;
  tenant_id?: string;
  email?: string;
  mfa_methods?: string[];
}

export interface VerifiedSignInActions {
  onSession: (sessionToken: string) => void;
  onSecondFactor: (state: SecondFactorState) => void;
}

export function applyVerifiedSignIn(
  data: VerifiedSignIn,
  actions: VerifiedSignInActions,
): void;
