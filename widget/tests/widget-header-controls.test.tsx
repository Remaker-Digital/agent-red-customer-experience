
import { render, screen } from '@testing-library/preact';
import { expect, it } from 'vitest';
import { Header } from '@/components/Header';
import { Launcher } from '@/components/Launcher';
import { getLocalePack } from '@/locale';
import { resolveTokens } from '@/theme/tokens';
import { readWidgetEmbedOverrides } from '@/utils/widgetEmbedOverrides';

const tokens = resolveTokens({ widget_primary_color: '#ff3621' });
const locale = getLocalePack('en');

it('renders the assistant identity from its embed settings', () => {
  const script = document.createElement('script');
  const logo = '/admin/standalone/icon-192x192.png';
  script.dataset.headerText = 'Agent Red Admin Assistant';
  script.dataset.headerSubtitle = 'Help with your Agent Red account';
  script.dataset.agentName = 'Agent Red Admin Assistant';
  script.dataset.agentAvatarUrl = logo;
  script.dataset.logoUrl = logo;
  const appearance = readWidgetEmbedOverrides(script);

  expect(appearance).toMatchObject({
    widget_agent_display_name: 'Agent Red Admin Assistant',
    widget_agent_avatar_url: logo,
    widget_logo_url: logo,
  });
  render(<Header tokens={tokens} locale={locale} agentName={String(appearance.widget_agent_display_name)} agentTitle=""
    logoUrl={String(appearance.widget_logo_url)} agentAvatarUrl={String(appearance.widget_agent_avatar_url)}
    headerText={String(appearance.widget_header_text)} headerSubtitle={String(appearance.widget_header_subtitle)}
    gradientEnd={null} onClose={() => {}} />);

  expect(screen.getByRole('heading', { name: 'Agent Red Admin Assistant' })).toBeTruthy();
  expect(screen.getByText('Help with your Agent Red account')).toBeTruthy();
  expect(screen.getByRole('img', { name: locale.logoAlt }).getAttribute('src')).toBe(logo);
  expect(screen.queryByText('AI', { exact: true })).toBeNull();
});

it('leaves visitor appearance to configuration when branding attributes are absent', () => {
  const script = document.createElement('script');
  script.dataset.agentAvatarUrl = '';
  script.dataset.autoOpen = 'false';
  expect(readWidgetEmbedOverrides(script)).toEqual({ widget_auto_open: false });
});

it('uses the configured logo in the leading header slot', () => {
  render(<Header tokens={tokens} locale={locale} agentName="AI Assistant" agentTitle=""
    logoUrl="/merchant-logo.png" agentAvatarUrl={null} headerText={null}
    headerSubtitle={null} gradientEnd={null} onClose={() => {}} />);

  const logo = screen.getByRole('img', { name: locale.logoAlt });
  expect(screen.queryByText('AI', { exact: true })).toBeNull();
  expect(logo.parentElement?.nextElementSibling?.querySelector('h1')).not.toBeNull();
  expect(logo.parentElement?.style.borderWidth).toBe('1px');
});

it('defines themed hover and pressed states for the panel close button', () => {
  const { container } = render(<Header tokens={tokens} locale={locale} agentName="AI Assistant"
    agentTitle="" logoUrl={null} agentAvatarUrl={null} headerText={null}
    headerSubtitle={null} gradientEnd={null} onClose={() => {}} />);
  const styles = container.querySelector('style')?.textContent;
  expect(styles).toContain('.ar-header-close:hover');
  expect(styles).toContain('.ar-header-close:active');
});

it('uses an inset keyboard focus indicator on the borderless launcher', () => {
  const { container } = render(<Launcher tokens={tokens} locale={locale} position="bottom-right"
    offsetX={20} offsetY={20} isOpen={false} unreadCount={0}
    launcherIcon="chat" onClick={() => {}} />);
  expect(screen.getByRole('button').style.border).toContain('none');
  expect(container.querySelector('style')?.textContent).toContain('inset 0 0 0');
});
