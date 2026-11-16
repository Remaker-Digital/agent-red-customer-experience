import { afterEach, expect, it, vi } from 'vitest';
import { syncWidgetAppearance } from '../../admin/standalone/layouts/widgetAppearance';

const response = (config: Record<string, string>) =>
  new Response(JSON.stringify({ config }), { headers: { 'Content-Type': 'application/json' } });

let stop: (() => void) | undefined;

afterEach(() => {
  stop?.();
  stop = undefined;
  Reflect.deleteProperty(window, 'AgentRed');
});

function loadSdk() {
  const setConfigPartial = vi.fn();
  Object.assign(window, { AgentRed: { setConfigPartial } });
  window.dispatchEvent(new CustomEvent('agentred:ready'));
  return setConfigPartial;
}

it('applies tenant draft appearance when the widget loads after a direct configuration visit', async () => {
  const apiFetch = vi.fn(async () => response({ widget_header_text: 'Visitor draft' }));
  stop = syncWidgetAppearance(apiFetch, true);
  expect(apiFetch).not.toHaveBeenCalled();

  const setConfigPartial = loadSdk();
  await vi.waitFor(() => expect(setConfigPartial).toHaveBeenLastCalledWith(expect.objectContaining({
    widget_header_text: 'Visitor draft',
    widget_logo_url: null,
  })));
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith('/api/config?state=draft');
});

it('applies admin branding when the widget loads after the dashboard', async () => {
  const apiFetch = vi.fn(async () => response({ widget_header_text: 'Visitor active' }));
  stop = syncWidgetAppearance(apiFetch, false);
  const setConfigPartial = loadSdk();

  await vi.waitFor(() => expect(setConfigPartial).toHaveBeenLastCalledWith(expect.objectContaining({
    widget_header_text: 'Agent Red Admin Assistant',
    widget_logo_url: '/admin/standalone/icon-192x192.png',
  })));
  expect(apiFetch).toHaveBeenCalledExactlyOnceWith('/api/config?page_type=all');
});

it('drops a draft response when the admin has left that route', async () => {
  const setConfigPartial = loadSdk();
  let completeDraft: (value: Response) => void = () => {};
  const pendingDraft = new Promise<Response>((resolve) => { completeDraft = resolve; });
  const apiFetch = vi.fn(async (path: string) =>
    path.endsWith('state=draft') ? pendingDraft : response({ widget_header_text: 'Visitor active' }));

  syncWidgetAppearance(apiFetch, true)?.();
  stop = syncWidgetAppearance(apiFetch, false);
  completeDraft(response({ widget_header_text: 'Outdated draft' }));

  await vi.waitFor(() => expect(setConfigPartial).toHaveBeenCalledTimes(2));
  expect(setConfigPartial).not.toHaveBeenCalledWith(expect.objectContaining({
    widget_header_text: 'Outdated draft',
  }));
});

it('keeps editor draft backgrounds when the active config response arrives later', async () => {
  const setConfigPartial = loadSdk();
  let completeActive: (value: Response) => void = () => {};
  const active = new Promise<Response>((resolve) => { completeActive = resolve; });
  const apiFetch = vi.fn(() => active);
  stop = syncWidgetAppearance(apiFetch, false, true);

  const draft = { widget_background_color: '#eaf0ff', widget_dark_background_color: null };
  setConfigPartial(draft);
  completeActive(response({ widget_background_color: '#ffffff', widget_dark_background_color: '#101010' }));

  await vi.waitFor(() => expect(setConfigPartial).toHaveBeenCalledTimes(3));
  const preview = Object.assign({}, ...setConfigPartial.mock.calls.map(([fields]) => fields));
  expect(preview).toMatchObject(draft);
  expect(preview.widget_header_text).toBe('Agent Red Admin Assistant');
});

it('restores active colors and clears missing defaults after leaving the editor', async () => {
  const setConfigPartial = loadSdk();
  setConfigPartial({ widget_background_color: '#eaf0ff', widget_dark_background_color: '#101010' });
  stop = syncWidgetAppearance(async () => response({ widget_background_color: '#ffffff' }), false);

  await vi.waitFor(() => expect(setConfigPartial).toHaveBeenLastCalledWith(expect.objectContaining({
    widget_background_color: '#ffffff',
    widget_dark_background_color: null,
  })));
});
