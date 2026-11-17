// © 2026 Michael Palmeter. All rights reserved.
import { h } from 'preact';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Panel } from '../src/components/Panel';
import { en } from '../src/locale/en';
import { clearTranscript, saveTranscript } from '../src/persistence/transcript';
import { createStore } from '../src/state/store';
import { configureTransport } from '../src/transport/http';
import type { WidgetConfig } from '../src/theme/tokens';

vi.mock('../src/transport/sse', () => ({
  SSEConnection: class {
    connect() {}
    disconnect() {}
  },
}));

const api = 'https://api.agentred.test';
const email = 'visitor@example.test';
const fetchMock = vi.fn();

function renderVisitor(options: Record<string, unknown> = {}, conversationId: string | null = null) {
  const config = { widget_header_text: 'Agent Red Test', ...options } as WidgetConfig;
  const store = createStore(config, en);
  store.setState({ view: 'conversation', conversationId });
  render(<Panel config={config} locale={en} onClose={() => {}} />);
  return store;
}

function sendMessage() {
  fireEvent.input(screen.getByRole('textbox', { name: en.inputPlaceholder }), {
    target: { value: 'Can you help me?' },
  });
  fireEvent.click(screen.getByRole('button', { name: en.sendButton }));
}

function respond(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function submittedBody(path: string) {
  const call = fetchMock.mock.calls.find(([url]) => url === api + path);
  expect(call).toBeDefined();
  return JSON.parse(call![1].body);
}

beforeEach(() => {
  configureTransport({ apiBaseUrl: api, widgetKey: 'pk_test_visitor' });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('direct visitor email verification', () => {
  it('appears before the first message and uses the email code token on the first chat', async () => {
    const store = renderVisitor({}, 'empty-guest-conversation');
    fetchMock
      .mockResolvedValueOnce(respond({ sent: true }))
      .mockResolvedValueOnce(respond({ verified: true, customer_token: 'synthetic-customer-token' }))
      .mockResolvedValueOnce(respond({ conversation_id: 'conv-verified' }))
      .mockResolvedValueOnce(respond({ message_id: 'msg-verified' }));

    fireEvent.input(screen.getByRole('textbox', { name: en.inputPlaceholder }), {
      target: { value: 'Can you help me?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
    expect(screen.queryByRole('textbox', { name: en.inputPlaceholder })).toBeNull();
    fireEvent.input(screen.getByRole('textbox', { name: 'Email' }), { target: { value: email } });
    fireEvent.click(screen.getByRole('button', { name: 'Send code' }));
    await screen.findByRole('textbox', { name: 'Digit 1' });

    for (const [index, digit] of Array.from('321456').entries()) {
      fireEvent.input(screen.getByRole('textbox', { name: 'Digit ' + (index + 1) }), {
        target: { value: digit },
      });
    }
    fireEvent.click(screen.getByRole('button', { name: en.otpVerify }));
    await screen.findByText('Email verified');
    expect(store.getState().customerToken).toBe('synthetic-customer-token');
    expect(screen.queryByRole('button', { name: 'Verify email' })).toBeNull();
    expect(submittedBody('/api/chat/otp/send')).toMatchObject({ email });
    expect(submittedBody('/api/chat/otp/verify')).toEqual({ email, code: '321456' });

    const restoredInput = screen.getByRole('textbox', { name: en.inputPlaceholder }) as HTMLTextAreaElement;
    expect(restoredInput.value).toBe('Can you help me?');
    fireEvent.click(screen.getByRole('button', { name: en.sendButton }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(submittedBody('/api/chat/conversations')).toMatchObject({
      customer_token: 'synthetic-customer-token',
      visitor: { email },
    });
    expect(submittedBody('/api/chat/message')).toMatchObject({
      conversation_id: 'conv-verified',
      content: 'Can you help me?',
    });
  });

  it('lets a guest continue after a code request fails', async () => {
    renderVisitor();
    fetchMock
      .mockResolvedValueOnce(respond({ sent: false }, 400))
      .mockResolvedValueOnce(respond({ conversation_id: 'conv-guest' }))
      .mockResolvedValueOnce(respond({ message_id: 'msg-guest' }));

    fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
    fireEvent.input(screen.getByRole('textbox', { name: 'Email' }), { target: { value: email } });
    fireEvent.click(screen.getByRole('button', { name: 'Send code' }));
    await screen.findByRole('alert');
    expect(screen.queryByRole('textbox', { name: 'Digit 1' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: en.otpSkip }));
    sendMessage();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(submittedBody('/api/chat/conversations')).not.toHaveProperty('customer_token');
    expect(submittedBody('/api/chat/message')).toMatchObject({ conversation_id: 'conv-guest' });
  });

  it('honors the tenant setting that disables email verification', () => {
    renderVisitor({ customer_email_verification: 'disabled' });
    expect(screen.queryByRole('button', { name: 'Verify email' })).toBeNull();
    expect(screen.getByRole('textbox', { name: en.inputPlaceholder })).toBeTruthy();
  });
});


it('keeps Verify email available when restoring a conversation containing only a report', async () => {
  const id = 'report-only-conversation';
  saveTranscript('pk_test_visitor', id, 'session');
  fetchMock.mockResolvedValueOnce(respond({
    conversation_id: id,
    status: 'active',
    messages: [{
      message_id: 'synthetic-report',
      role: 'system',
      content: '[Issue Report] Type: other | Details: Synthetic report',
      timestamp: new Date().toISOString(),
      metadata: { type: 'issue_report' },
    }],
  }));

  try {
    const store = renderVisitor({ widget_transcript_continuity: 'session' });
    await waitFor(() => expect(store.getState().conversationId).toBe(id));
    expect(screen.getByRole('button', { name: 'Verify email' })).toBeTruthy();
    expect(store.getState().messages).toEqual([]);
  } finally {
    clearTranscript('pk_test_visitor', 'session');
  }
});
