// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

import { afterEach, expect, it, vi } from 'vitest';
import type { Window as HappyWindow } from 'happy-dom';

vi.mock('../src/components/Panel', () => ({ Panel: () => null }));
vi.mock('../src/transport/http', () => ({
  configureTransport: vi.fn(),
  fetchWidgetConfig: vi.fn().mockResolvedValue({ widget_sound_enabled: false }),
}));

const widgetWindow = window as Window & {
  AgentRed?: { open(): void; close(): void; destroy(): void };
} & Pick<HappyWindow, 'happyDOM'>;
const frameSettings = widgetWindow.happyDOM.settings;
const originalIframeLoading = frameSettings.enableIframePageLoading;

afterEach(() => {
  widgetWindow.AgentRed?.destroy();
  document.querySelector('script[data-widget-key]')?.remove();
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  frameSettings.enableIframePageLoading = originalIframeLoading;
});

it('refits the existing dragged iframe when reopened in a narrower viewport', async () => {
  frameSettings.enableIframePageLoading = true;
  vi.stubGlobal('innerWidth', 1265);
  vi.stubGlobal('innerHeight', 712);
  vi.spyOn(HTMLIFrameElement.prototype, 'offsetWidth', 'get').mockReturnValue(380);
  vi.spyOn(HTMLIFrameElement.prototype, 'offsetHeight', 'get').mockReturnValue(520);
  sessionStorage.setItem('ar_panel_position', JSON.stringify({ left: 807, top: 91 }));

  const script = document.createElement('script');
  script.dataset.widgetKey = 'widget-viewport-fixture';
  script.dataset.apiUrl = 'https://api.example.test';
  document.head.appendChild(script);
  await import('../src/index');
  await vi.waitFor(() => expect(widgetWindow.AgentRed).toBeDefined());

  const widget = widgetWindow.AgentRed!;
  widget.open();
  const panel = document.querySelector('iframe[title="Agent Red Chat"]') as HTMLIFrameElement;
  expect(panel.style.left).toBe('807px');

  widget.close();
  vi.stubGlobal('innerWidth', 576);
  vi.stubGlobal('innerHeight', 600);
  window.dispatchEvent(new Event('resize'));
  widget.open();

  expect(document.querySelector('iframe[title="Agent Red Chat"]')).toBe(panel);
  expect(panel.style.left).toBe('196px');
  expect(panel.style.top).toBe('80px');
});
