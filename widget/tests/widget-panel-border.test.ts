// © 2026 Michael Palmeter. All rights reserved.

import { afterEach, expect, it, vi } from 'vitest';
import type { Window as HappyWindow } from 'happy-dom';
import type { WidgetConfig } from '../src/theme/tokens';

vi.mock('../src/components/Panel', () => ({ Panel: () => null }));
vi.mock('../src/transport/http', () => ({
  configureTransport: vi.fn(),
  fetchWidgetConfig: vi.fn().mockResolvedValue({
    widget_panel_border_enabled: true,
    widget_panel_border_width: 3,
    widget_panel_border_color: '#87a9cb',
    widget_sound_enabled: false,
  }),
}));

const widgetWindow = window as Window & {
  AgentRed?: {
    open(): void;
    destroy(): void;
    setConfigPartial(overrides: Partial<WidgetConfig>): void;
  };
} & Pick<HappyWindow, 'happyDOM'>;
const frameSettings = widgetWindow.happyDOM.settings;
const originalIframeLoading = frameSettings.enableIframePageLoading;

afterEach(() => {
  widgetWindow.AgentRed?.destroy();
  document.querySelector('script[data-widget-key]')?.remove();
  frameSettings.enableIframePageLoading = originalIframeLoading;
});

it('merges partial border overrides into the existing panel', async () => {
  frameSettings.enableIframePageLoading = true;
  const script = document.createElement('script');
  script.dataset.widgetKey = 'widget-border-fixture';
  script.dataset.apiUrl = 'https://api.example.test';
  document.head.appendChild(script);
  await import('../src/index');
  await vi.waitFor(() => expect(widgetWindow.AgentRed).toBeDefined());

  const widget = widgetWindow.AgentRed!;
  widget.open();
  const panel = document.querySelector<HTMLIFrameElement>('iframe[title="Agent Red Chat"]')!;
  expect(panel.style.border).toBe('3px solid #87a9cb');
  expect(panel.style.boxSizing).toBe('border-box');

  widget.setConfigPartial({ widget_panel_border_width: 5 });
  expect(panel.style.border).toBe('5px solid #87a9cb');

  widget.setConfigPartial({ widget_panel_border_color: '#f0c755' });
  expect(panel.style.border).toBe('5px solid #f0c755');

  widget.setConfigPartial({ widget_panel_border_width: 1, widget_panel_border_color: '#f4f7f829' });
  expect(panel.style.border).toBe('1px solid #f4f7f829');

  widget.setConfigPartial({ widget_panel_border_enabled: false });
  expect(panel.style.borderStyle).toBe('none');
  expect(document.querySelector('iframe[title="Agent Red Chat"]')).toBe(panel);
});
