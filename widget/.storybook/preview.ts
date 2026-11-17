/**
 * Storybook preview configuration (SPEC-1845/WI-1501).
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */
import type { Preview } from '@storybook/preact';

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
};

export default preview;
