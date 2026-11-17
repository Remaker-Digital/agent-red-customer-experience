// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';
import { en } from '../src/locale/en';
import { createStore } from '../src/state/store';
import type { WidgetConfig } from '../src/theme/tokens';
import { SSEConnection } from '../src/transport/sse';

describe('SSE reply rendering', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    { stage: 'co-pilot', validated: true },
    { stage: 'co-pilot', validated: false },
    { stage: 'response-generator', validated: true },
  ])('renders one complete reply for $stage with validated=$validated', ({ stage, validated }) => {
    const store = createStore({ widget_header_text: 'Test' } as WidgetConfig, en);
    const source = Object.assign(new EventTarget(), { close: vi.fn() });
    vi.stubGlobal('EventSource', function () { return source; });
    const connection = new SSEConnection({
      apiBaseUrl: 'https://example.invalid',
      widgetKey: 'fixture',
      conversationId: 'conversation-1',
    });
    const emit = (type: string, data: object) => {
      source.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
    };

    connection.connect();
    emit('stage', { stage, status: 'started' });
    emit('stage', { stage, status: 'completed' });
    emit('token', { text: 'Here is the Agent Red installation guide.' });
    if (validated) emit('validated', {});
    emit('done', {});

    expect(store.getState().messages).toMatchObject([
      { role: 'agent', content: 'Here is the Agent Red installation guide.', streaming: false },
    ]);
    expect(store.getState().isStreaming).toBe(false);
    expect(source.close).toHaveBeenCalledOnce();
  });
});
