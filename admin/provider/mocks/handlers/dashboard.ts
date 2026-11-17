// @ts-nocheck
/**
 * Mock handlers — Provider dashboard endpoints.
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */
import { GET } from '../router';
import { getStore } from '../store';
import type { MockRequest, MockResponse } from '../router';

export function registerDashboardHandlers(): void {
  GET('/api/superadmin/dashboard', (_req: MockRequest): MockResponse => {
    const d = getStore().dashboard;
    return { status: 200, body: d };
  });
}
