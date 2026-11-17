/**
 * Storybook preview configuration (SPEC-1845/WI-1501).
 *
 * © 2026 Michael Palmeter. All rights reserved.
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
