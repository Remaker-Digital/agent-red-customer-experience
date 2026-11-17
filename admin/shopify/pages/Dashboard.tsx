// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * Dashboard page — Shopify embedded admin.
 *
 * Renders AnalyticsOverview + UsageDashboard shared components
 * wrapped in Polaris Page layout.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import React from 'react';
import { Page, Layout } from '@shopify/polaris';
import { useAppContext } from '../layouts/ShopifyAppLayout';
import { AnalyticsOverview } from '../../shared/AnalyticsOverview';
import { UsageDashboard } from '../../shared/UsageDashboard';

export const DashboardPage: React.FC = () => {
  const { tenantContext, apiFetch, onNotify } = useAppContext();

  if (!tenantContext) return null;

  const baseProps = { tenantContext, apiFetch, onNotify };

  return (
    <Page title="Dashboard">
      <Layout>
        <Layout.Section>
          <AnalyticsOverview {...baseProps} />
        </Layout.Section>
        <Layout.Section>
          <UsageDashboard {...baseProps} />
        </Layout.Section>
      </Layout>
    </Page>
  );
};
