// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * Configuration page — Shopify embedded admin.
 *
 * Shows ConfigEditor for agent configuration. Changes are saved as DRAFT
 * and go live via the ActivationBanner/Dialog flow.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import React from 'react';
import { Page } from '@shopify/polaris';
import { useAppContext } from '../layouts/ShopifyAppLayout';
import { ConfigEditor } from '../../shared/ConfigEditor';

export const ConfigurationPage: React.FC = () => {
  const { tenantContext, apiFetch, onNotify } = useAppContext();

  if (!tenantContext) return null;

  return (
    <Page title="Agent configuration">
      <ConfigEditor tenantContext={tenantContext} apiFetch={apiFetch} onNotify={onNotify} />
    </Page>
  );
};
