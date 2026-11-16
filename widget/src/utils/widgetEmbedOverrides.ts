const embedAttributes: Record<string, string> = {
  'data-color': 'widget_primary_color',
  'data-position': 'widget_position',
  'data-auto-open': 'widget_auto_open',
  'data-auto-open-delay': 'widget_auto_open_delay',
  'data-mobile-enabled': 'widget_mobile_enabled',
  'data-mobile-fullscreen': 'widget_mobile_fullscreen',
  'data-sound-enabled': 'widget_sound_enabled',
  'data-greeting': 'widget_greeting_message',
  'data-header-text': 'widget_header_text',
  'data-header-subtitle': 'widget_header_subtitle',
  'data-agent-name': 'widget_agent_display_name',
  'data-agent-avatar-url': 'widget_agent_avatar_url',
  'data-logo-url': 'widget_logo_url',
  'data-context': 'widget_context',
  'data-customer-name': 'widget_customer_name',
};

/** Keep fetched configuration as the default for attributes the embed omits. */
export function readWidgetEmbedOverrides(script: HTMLScriptElement): Record<string, string | boolean> {
  const overrides: Record<string, string | boolean> = {};
  for (const [attribute, configKey] of Object.entries(embedAttributes)) {
    const value = script.getAttribute(attribute);
    if (value === null || value === '') continue;
    if (value === 'true') overrides[configKey] = true;
    else if (value === 'false') overrides[configKey] = false;
    else overrides[configKey] = value;
  }
  return overrides;
}
