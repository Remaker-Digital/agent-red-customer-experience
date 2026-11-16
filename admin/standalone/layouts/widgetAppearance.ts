type ApiFetch = (path: string) => Promise<Response>;

type WidgetSdk = {
  setConfigPartial?: (overrides: Record<string, unknown>) => void;
};

const REMAKER_DIGITAL_MARK = '/admin/standalone/icon-192x192.png';
const ADMIN_ASSISTANT_NAME = 'Agent Red Admin Assistant';
const CONVERSATION_BACKGROUND_KEYS = new Set([
  'widget_background_color',
  'widget_dark_background_color',
]);
const ADMIN_ASSISTANT_APPEARANCE = {
  widget_header_text: ADMIN_ASSISTANT_NAME,
  widget_header_subtitle: 'Help with your Agent Red account',
  widget_agent_display_name: ADMIN_ASSISTANT_NAME,
  widget_agent_avatar_url: REMAKER_DIGITAL_MARK,
  widget_logo_url: REMAKER_DIGITAL_MARK,
  widget_greeting_message: 'How can I help you manage Agent Red?',
};

export function syncWidgetAppearance(apiFetch: ApiFetch, onConfigPage: boolean, onWidgetPage = false) {
  const widgetWindow = window as Window & { AgentRed?: WidgetSdk };
  let cancelled = false;

  async function applyConfig() {
    const sdk = widgetWindow.AgentRed;
    if (cancelled || !sdk?.setConfigPartial) return;
    if (!onConfigPage) sdk.setConfigPartial(ADMIN_ASSISTANT_APPEARANCE);

    try {
      const endpoint = onConfigPage ? '/api/config?state=draft' : '/api/config?page_type=all';
      const resp = await apiFetch(endpoint);
      if (cancelled || !resp.ok) return;
      const data = await resp.json();
      const config = data?.config || {};

      const overrides: Record<string, unknown> = {};
      // The Widget page owns draft backgrounds while its editor is mounted.
      if (!onWidgetPage) {
        for (const key of CONVERSATION_BACKGROUND_KEYS) overrides[key] = null;
      }
      if (onConfigPage) {
        // Clear the admin identity where the visitor draft has no value.
        for (const key of Object.keys(ADMIN_ASSISTANT_APPEARANCE)) overrides[key] = null;
      }
      for (const [key, val] of Object.entries(config)) {
        if (onWidgetPage && CONVERSATION_BACKGROUND_KEYS.has(key)) continue;
        if (key.startsWith('widget_') && val != null) {
          overrides[key] = val;
        }
      }
      if (config.greeting_message != null) {
        overrides.widget_greeting_message = config.greeting_message;
      }
      if (config.brand_name != null) {
        overrides.brand_name = config.brand_name;
      }
      if (!onConfigPage) Object.assign(overrides, ADMIN_ASSISTANT_APPEARANCE);

      if (!cancelled && widgetWindow.AgentRed === sdk) sdk.setConfigPartial(overrides);
    } catch {
      // A failed config fetch leaves the current appearance in place.
    }
  }

  // The script is injected asynchronously and can be replaced on activation.
  window.addEventListener('agentred:ready', applyConfig);
  void applyConfig();

  return () => {
    cancelled = true;
    window.removeEventListener('agentred:ready', applyConfig);
  };
}
