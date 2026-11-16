import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { afterEach, expect, it, vi } from 'vitest';
import { Panel } from '@/components/Panel';
import { en } from '@/locale/en';
import { createStore } from '@/state/store';
import { configureTransport } from '@/transport/http';

vi.mock('@/transport/sse', () => ({
  SSEConnection: class {
    connect() {}
    disconnect() {}
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mountWidget(notificationDelivered: boolean, reportStatus = 200) {
  const config = {};
  const store = createStore(config, en);
  store.setState({ view: 'conversation' });
  configureTransport({ apiBaseUrl: 'https://widget.test', widgetKey: 'synthetic-widget-key' });

  const fetchMock = vi.fn(async (url: string, _options?: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === '/api/chat/conversations') {
      return Response.json({ conversation_id: 'synthetic-conversation' });
    }
    if (path === '/api/chat/conversations/synthetic-conversation/issue') {
      return Response.json({
        accepted: reportStatus === 200,
        notification_delivered: notificationDelivered,
      }, { status: reportStatus });
    }
    if (path === '/api/chat/message') {
      return Response.json({ message_id: 'synthetic-message' });
    }
    throw new Error('Unexpected widget request');
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<Panel config={config} locale={en} onClose={() => {}} />);
  return { store, fetchMock };
}

async function submitIssue() {
  fireEvent.click(screen.getByRole('button', { name: en.reportIssue }));
  fireEvent.click(await screen.findByRole('button', { name: en.issueTypeOther }));
  fireEvent.input(screen.getByPlaceholderText(en.issueDetailsPlaceholder), {
    target: { value: 'A synthetic problem before chat' },
  });
  fireEvent.click(screen.getByRole('button', { name: en.issueSubmit }));
}

it('submits from an empty guest widget and preserves ordinary guest chat', async () => {
  const { store, fetchMock } = mountWidget(true);
  expect(store.getState().messages).toEqual([]);

  await submitIssue();

  await screen.findByText(en.issueSubmitSuccess);
  expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
    '/api/chat/conversations',
    '/api/chat/conversations/synthetic-conversation/issue',
  ]);
  fireEvent.click(screen.getByRole('button', { name: en.issueDone }));
  const input = await screen.findByPlaceholderText(en.inputPlaceholder);
  fireEvent.input(input, { target: { value: 'What services do you offer?' } });
  fireEvent.click(screen.getByRole('button', { name: en.sendButton }));
  await waitFor(() => expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
    '/api/chat/conversations',
    '/api/chat/conversations/synthetic-conversation/issue',
    '/api/chat/message',
  ]));
});

it('keeps the report available for retry when submission fails', async () => {
  mountWidget(false, 503);

  await submitIssue();

  const error = await screen.findByRole('alert');
  expect(error.textContent).toContain('We could not submit your report');
  expect((screen.getByPlaceholderText(en.issueDetailsPlaceholder) as HTMLTextAreaElement).value)
    .toBe('A synthetic problem before chat');
  expect(screen.queryByText(en.issueSubmitSuccess)).toBeNull();
});

it('distinguishes a saved report from a failed notification', async () => {
  mountWidget(false);

  await submitIssue();

  const warning = await screen.findByRole('alert');
  expect(warning.textContent).toContain('Your report was saved');
  expect(screen.getByRole('button', { name: en.issueDone })).toBeTruthy();
  expect(screen.queryByText(en.issueSubmitSuccess)).toBeNull();
});


it('reuses a conversation that already exists when the report opens', async () => {
  const { store, fetchMock } = mountWidget(true);
  store.setState({ conversationId: 'synthetic-conversation' });

  await submitIssue();

  await screen.findByText(en.issueSubmitSuccess);
  expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
    '/api/chat/conversations/synthetic-conversation/issue',
  ]);
});

it('links the first report conversation to an already verified visitor', async () => {
  const { store, fetchMock } = mountWidget(true);
  store.setState({
    customerEmail: 'verified@example.test',
    customerToken: 'synthetic-customer-token',
  });

  await submitIssue();

  await screen.findByText(en.issueSubmitSuccess);
  const firstRequest = fetchMock.mock.calls[0]?.[1];
  expect(JSON.parse(String(firstRequest?.body))).toMatchObject({
    customer_token: 'synthetic-customer-token',
    visitor: { email: 'verified@example.test' },
  });
});
