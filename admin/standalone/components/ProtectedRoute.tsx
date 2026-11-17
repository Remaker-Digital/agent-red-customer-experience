// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * ProtectedRoute — Role-based route guard for standalone admin.
 *
 * Wraps child routes and redirects non-matching roles to /inbox.
 * Uses the AppContext userRole resolved from /api/admin/team/whoami.
 *
 * Usage:
 *   <ProtectedRoute allowedRoles={['superadmin', 'admin']}>
 *     <ConfigurationPage />
 *   </ProtectedRoute>
 *
 * If userRole is null (still loading), renders nothing to avoid flash.
 * If userRole doesn't match allowedRoles, redirects to /inbox.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import React from 'react';
import type { TeamRole } from '../../shared/types';
import { useAppContext } from '../layouts/StandaloneLayout';
import { NavigateWithQuery } from './NavigateWithQuery';

interface ProtectedRouteProps {
  /** Roles permitted to view this route. */
  allowedRoles: TeamRole[];
  children: React.ReactNode;
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({
  allowedRoles,
  children,
}) => {
  const { userRole, loading } = useAppContext();

  // Still resolving auth — render nothing to prevent flash
  if (loading || userRole === null) {
    return null;
  }

  // Role doesn't match — redirect to inbox (accessible to all roles)
  if (!allowedRoles.includes(userRole)) {
    return <NavigateWithQuery to="/inbox" replace />;
  }

  return <>{children}</>;
};
