// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * Inbox page — Shopify embedded admin.
 *
 * Renders ConversationInbox shared component.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import React from 'react';
import { Page } from '@shopify/polaris';
import { useAppContext } from '../layouts/ShopifyAppLayout';
import { ConversationInbox } from '../../shared/ConversationInbox';

export const InboxPage: React.FC = () => {
  const { tenantContext, apiFetch, onNotify } = useAppContext();

  if (!tenantContext) return null;

  return (
    <Page title="Conversation Inbox" fullWidth>
      <ConversationInbox
        tenantContext={tenantContext}
        apiFetch={apiFetch}
        onNotify={onNotify}
      />
    </Page>
  );
};
