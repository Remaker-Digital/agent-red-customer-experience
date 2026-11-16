// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/preact';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Panel } from '@/components/Panel';
import { en } from '@/locale/en';
import { de } from '@/locale/de';
import { createStore, getStore } from '@/state/store';
import { configureTransport } from '@/transport/http';

const baseUrl = 'https://visitor-attach.test';
const conversationId = 'conversation-synthetic';

function openVisitorChat(enabled: boolean) {
  const config = { widget_file_upload_enabled: enabled };
  const store = createStore(config, en);
  store.setState({ view: 'conversation' });
  return render(<Panel config={config} locale={en} onClose={() => {}} />);
}

function fileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error('The attachment chooser is missing');
  return input;
}

const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>();
const streams: EventTarget[] = [];

beforeEach(() => {
  configureTransport({ apiBaseUrl: baseUrl, widgetKey: 'pk_test_synthetic' });
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url) => {
    if (url === baseUrl + '/api/chat/conversations') {
      return Response.json({ conversation_id: conversationId });
    }
    if (url === baseUrl + '/api/chat/message') {
      return Response.json({ message_id: 'visitor-message' });
    }
    throw new Error('Unexpected request ' + url);
  });
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('EventSource', class extends EventTarget {
    constructor() {
      super();
      streams.push(this);
    }

    close() {}
  });
});

afterEach(() => {
  cleanup();
  streams.length = 0;
  vi.unstubAllGlobals();
});

function sentMessage() {
  const messageCall = fetchMock.mock.calls.find(([url]) => url === baseUrl + '/api/chat/message');
  return JSON.parse(String(messageCall?.[1].body));
}

it('selects a supported file and sends its contents through the visitor chat on explicit Send', async () => {
  const { container } = openVisitorChat(true);
  const paperclip = screen.getByRole('button', { name: en.attachFile });
  const input = fileInput(container);
  const choose = vi.spyOn(input, 'click');
  fireEvent.click(paperclip);
  expect(choose).toHaveBeenCalledOnce();
  expect(input.accept).toContain('.txt');

  const fixture = new File(['Synthetic attachment contains desk count 17.'], 'desk-count.txt', {
    type: 'text/plain',
  });
  fireEvent.change(input, { target: { files: [fixture] } });
  await screen.findByText('desk-count.txt');
  expect(fetchMock).not.toHaveBeenCalled();

  fireEvent.input(screen.getByRole('textbox'), { target: { value: 'Please review the note.' } });
  fireEvent.click(screen.getByRole('button', { name: en.sendButton }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    baseUrl + '/api/chat/conversations',
    baseUrl + '/api/chat/message',
  ]);
  expect(sentMessage()).toMatchObject({
    conversation_id: conversationId,
    content: 'Please review the note.\n\n[Text attachment: desk-count.txt]\nSynthetic attachment contains desk count 17.',
  });

  await waitFor(() => expect(streams).toHaveLength(1));
  act(() => {
    streams[0].dispatchEvent(new MessageEvent('stage', {
      data: JSON.stringify({ stage: 'response-generator', status: 'started' }),
    }));
    streams[0].dispatchEvent(new MessageEvent('token', { data: JSON.stringify({ text: 'The note says 17.' }) }));
    streams[0].dispatchEvent(new MessageEvent('done', { data: '{}' }));
  });
  await within(screen.getByRole('log')).findByText('The note says 17.');
});

it('allows an attachment-only message and keeps over-limit drafts from sending', async () => {
  const { container } = openVisitorChat(true);
  const send = screen.getByRole('button', { name: en.sendButton }) as HTMLButtonElement;
  const input = fileInput(container);
  const attachmentText = 'Count: 17.';
  const attachmentMessage = '[Text attachment: count.txt]\n' + attachmentText;
  const text = new File([attachmentText], 'count.txt', { type: 'text/plain' });
  fireEvent.change(input, { target: { files: [text] } });
  await screen.findByText('count.txt');

  const textbox = screen.getByRole('textbox');
  const belowWarning = 'a'.repeat(1790);
  fireEvent.input(textbox, { target: { value: belowWarning } });
  const warningLength = [belowWarning, attachmentMessage].join('\n\n').length;
  expect(screen.getByText(warningLength + '/2000')).toBeTruthy();

  const typedText = 'a'.repeat(2000);
  fireEvent.input(textbox, { target: { value: typedText } });
  const outgoingLength = [typedText, attachmentMessage].join('\n\n').length;
  expect(screen.getByText(outgoingLength + '/2000')).toBeTruthy();
  expect(send.disabled).toBe(true);
  expect(screen.getByRole('alert').textContent).toContain('2,000 characters');
  expect(fetchMock).not.toHaveBeenCalled();

  fireEvent.input(textbox, { target: { value: '' } });
  fireEvent.click(send);
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(sentMessage().content).toBe(attachmentMessage);
});

it('shows a readable error for unsupported files without posting them', async () => {
  const { container } = openVisitorChat(true);
  const file = new File(['synthetic fixture'], 'not-supported.pdf', { type: 'application/pdf' });
  fireEvent.change(fileInput(container), { target: { files: [file] } });
  expect((await screen.findByRole('alert')).textContent).toContain('.txt');
  expect(fetchMock).not.toHaveBeenCalled();
});

it('removes an attachment without sending it or removing the typed draft', async () => {
  const { container } = openVisitorChat(true);
  const input = fileInput(container);
  const fixture = new File(['Do not send this attachment.'], 'draft.txt', { type: 'text/plain' });
  fireEvent.change(input, { target: { files: [fixture] } });
  await screen.findByText('draft.txt');

  fireEvent.input(screen.getByRole('textbox'), { target: { value: 'Keep this guest message.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove attachment' }));
  expect(screen.queryByText('draft.txt')).toBeNull();
  expect(fetchMock).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: en.sendButton }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(sentMessage().content).toBe('Keep this guest message.');
});

it('leaves the ordinary guest text path available with attachments disabled', async () => {
  const { container } = openVisitorChat(false);
  expect(screen.queryByRole('button', { name: en.attachFile })).toBeNull();
  expect(container.querySelector('input[type="file"]')).toBeNull();
  fireEvent.input(screen.getByRole('textbox'), { target: { value: 'Ordinary guest question' } });
  fireEvent.click(screen.getByRole('button', { name: en.sendButton }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  expect(sentMessage()).toMatchObject({ conversation_id: conversationId, content: 'Ordinary guest question' });
});

it('resolves attachment help and validation errors after a runtime locale change', async () => {
  const { container } = openVisitorChat(true);
  expect(screen.getByText('Attach a UTF-8 .txt file. Its contents are sent as part of your message.')).toBeTruthy();

  act(() => getStore().setState({ locale: de }));
  expect(screen.getByText(
    'Fügen Sie eine UTF-8-Textdatei (.txt) hinzu. Ihr Inhalt wird als Teil Ihrer Nachricht gesendet.',
  )).toBeTruthy();

  const file = new File(['synthetic fixture'], 'not-supported.pdf', { type: 'application/pdf' });
  fireEvent.change(fileInput(container), { target: { files: [file] } });
  expect((await screen.findByRole('alert')).textContent).toBe('Wählen Sie eine UTF-8-Textdatei (.txt).');
  act(() => getStore().setState({ locale: en }));
  expect(screen.getByRole('alert').textContent).toBe('Choose a UTF-8 .txt file.');
  expect(fetchMock).not.toHaveBeenCalled();
});
