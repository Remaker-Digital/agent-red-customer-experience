// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * Widget page — Shopify embedded admin.
 *
 * Renders WidgetConfigurator shared component.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import React from 'react';
import { Page } from '@shopify/polaris';
import { useAppContext } from '../layouts/ShopifyAppLayout';
import { WidgetConfigurator } from '../../shared/WidgetConfigurator';

export const WidgetPage: React.FC = () => {
  const { tenantContext, apiFetch, onNotify } = useAppContext();

  if (!tenantContext) return null;

  return (
    <Page title="Widget configuration">
      <WidgetConfigurator
        tenantContext={tenantContext}
        apiFetch={apiFetch}
        onNotify={onNotify}
      />
    </Page>
  );
};
