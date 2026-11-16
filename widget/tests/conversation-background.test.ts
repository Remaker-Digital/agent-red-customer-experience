import { describe, expect, it } from 'vitest';
import { resolveTokens } from '@/theme/tokens';

describe('configured conversation backgrounds', () => {
  it('keeps an existing light background independent of dark mode', () => {
    const config = { widget_background_color: '#f0f3fa' };
    expect(resolveTokens({ ...config, widget_color_mode: 'light' }).colorBackground).toBe('#f0f3fa');
    expect(resolveTokens({ ...config, widget_color_mode: 'dark' }).colorBackground).toBe('#1c1917');
  });

  it('applies the dark background and derives matching surfaces', () => {
    const tokens = resolveTokens({
      widget_color_mode: 'dark',
      widget_background_color: '#ffffff',
      widget_dark_background_color: '#0e151a',
    });

    expect(tokens.colorBackground).toBe('#0e151a');
    expect(tokens.colorSurface).toBe('#1b2227');
    expect(tokens.colorAgentBubble).toBe(tokens.colorSurface);
    expect(tokens.colorInputBarBg).toBe('#0e151a');
    expect(tokens.colorText).toBe('#f5f5f4');
  });

  it.each([
    { background: '#757575', text: '#FFFFFF', secondary: '#FFFFFF' },
    { background: '#777777', text: '#000000', secondary: '#000000' },
    { background: '#444444', text: '#f5f5f4', secondary: '#b7c3cc' },
  ])('keeps composer and secondary text readable on $background', ({ background, text, secondary }) => {
    const tokens = resolveTokens({
      widget_color_mode: 'dark',
      widget_dark_background_color: background,
    });

    expect(tokens.colorText).toBe(text);
    expect(tokens.colorTextSecondary).toBe(secondary);
    expect(tokens.colorSurface).toBe(background);
    expect(tokens.colorInputBarBg).toBe(background);
  });

  it.each([
    ['#888888', '#000000', '#FFFFFF'],
    ['#0e151a', '#ffffff', '#000000'],
  ])('contrasts agent text with its bubble on dark background %s', (background, bubble, text) => {
    const tokens = resolveTokens({
      widget_color_mode: 'dark',
      widget_dark_background_color: background,
      widget_agent_bubble_color: bubble,
    });

    expect(tokens.colorAgentBubble).toBe(bubble);
    expect(tokens.colorAgentBubbleText).toBe(text);
  });
});
