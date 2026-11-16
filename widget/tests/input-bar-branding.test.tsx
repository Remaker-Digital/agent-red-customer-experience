// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

import { h } from 'preact';
import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { InputBar } from '@/components/InputBar';
import { en } from '@/locale/en';
import { resolveTokens } from '@/theme/tokens';

describe('InputBar branding', () => {
  it('keeps the Agent Red wordmark red when the tenant uses a navy primary color', () => {
    const tokens = resolveTokens({ widget_primary_color: '#18232b' });

    render(
      <InputBar
        tokens={tokens}
        locale={en}
        onSend={() => {}}
        isLoading={false}
        disabled={false}
        fileUploadEnabled={false}
        showBranding={true}
        inputPlaceholder={null}
      />,
    );

    const wordmark = screen.getByText(en.poweredByBrand);
    expect(getComputedStyle(wordmark).color).toBe('#ff3621');
  });
});
