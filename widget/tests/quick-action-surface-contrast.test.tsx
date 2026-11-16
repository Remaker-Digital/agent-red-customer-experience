import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import { afterEach, expect, it } from 'vitest';
import { QuickActions } from '@/components/QuickActions';
import { resolveTokens } from '@/theme/tokens';

afterEach(cleanup);

function browserColor(value: string): string {
  const style = document.createElement('span').style;
  style.color = value;
  return style.color;
}

it.each([
  { background: '#0e151a', hoverText: '#FFFFFF' },
  { background: '#626262', hoverText: '#000000' },
])('uses readable quick-action text on $background and its hover surface', ({ background, hoverText }) => {
  const tokens = resolveTokens({
    widget_color_mode: 'dark',
    widget_dark_background_color: background,
    widget_agent_bubble_color: '#ffffff',
  });
  render(
    <QuickActions
      tokens={tokens}
      actions={[{ id: 'services', label: 'Explore services', prompt_template: 'What services are available?' }]}
      onAction={() => {}}
      disabled={false}
    />,
  );
  const action = screen.getByRole<HTMLButtonElement>('button', { name: 'Explore services' });

  expect(action.style.backgroundColor).toBe(browserColor(tokens.colorSurface));
  expect(action.style.color).toBe(browserColor('#FFFFFF'));

  fireEvent.mouseEnter(action);
  expect(action.style.backgroundColor).toBe(browserColor(tokens.colorSurfaceHover));
  expect(action.style.color).toBe(browserColor(hoverText));
});
