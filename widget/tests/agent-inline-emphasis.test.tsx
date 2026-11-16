import { cleanup, render, screen } from '@testing-library/preact';
import { afterEach, expect, it } from 'vitest';
import { MessageBubble } from '@/components/MessageBubble';
import { en } from '@/locale/en';
import type { Message } from '@/state/store';
import { resolveTokens } from '@/theme/tokens';

afterEach(cleanup);

function renderMessage(role: Message['role'], content: string, streaming = false) {
  return render(
    <MessageBubble
      tokens={resolveTokens({})}
      locale={en}
      message={{ id: 'inline-formatting-test', role, content, streaming, timestamp: 0 }}
      agentAvatarUrl={null}
      agentName="Agent Red"
      showAvatar={false}
    />,
  );
}

it('renders agent emphasis and keeps the services link clickable', () => {
  const href = 'https://example.invalid/services';
  const { container } = renderMessage(
    'agent',
    'We offer **AI consulting and engineering**. Visit [our services](' + href + ').',
  );

  expect(screen.getByText('AI consulting and engineering').tagName).toBe('STRONG');
  expect(screen.getByRole('link', { name: 'our services' }).getAttribute('href')).toBe(href);
  expect(container.textContent).not.toContain('**');
});

it('keeps visitor emphasis markers as typed', () => {
  const { container } = renderMessage('customer', 'Keep **these markers**.');
  expect(container.textContent).toContain('Keep **these markers**.');
  expect(container.querySelector('strong')).toBeNull();
});

it('preserves an incomplete emphasis sequence during streaming', () => {
  const { container } = renderMessage('agent', 'The **partial phrase', true);
  expect(container.textContent).toContain('The **partial phrase');
  expect(container.querySelector('strong')).toBeNull();
});

it.each(['https://example.invalid/services', 'https://example.invalid/a**b'])(
  'preserves the whole emphasized phrase around a link to %s',
  (href) => {
    const { container } = renderMessage('agent', '**See [services](' + href + ')** and **pricing**');
    const link = screen.getByRole<HTMLAnchorElement>('link', { name: 'services' });
    const spans = Array.from(container.querySelectorAll('strong'), (element) => element.textContent);

    expect(spans).toEqual(['See services', 'pricing']);
    expect(container.textContent).toContain('See services and pricing');
    expect(container.textContent).not.toContain('**');
    expect(link.closest('strong')?.textContent).toBe('See services');
    expect(link.getAttribute('href')).toBe(href);
    expect(link.style.fontWeight).toBe('inherit');
  },
);
