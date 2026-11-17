// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * InputBar — message input area at the bottom of the conversation panel.
 *
 * Handles:
 *   - Text input (auto-growing textarea)
 *   - Send button (enabled only when message is non-empty)
 *   - Enter to send, Shift+Enter for newline
 *   - File attachment button (when enabled)
 *   - Character count (for excessively long messages)
 *   - Loading state (while sending)
 *   - Powered-by branding (when show_branding is true)
 *
 * Visual reference: Zapier (clean input, subtle borders, consistent sizing).
 * Functional reference: Tidio (auto-grow, enter-to-send, attachment).
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import { FunctionComponent } from 'preact';
import { useState, useRef, useCallback, useMemo, useId } from 'preact/hooks';
import type { DesignTokens } from '@/theme/tokens';
import { AGENT_RED_BRAND_COLOR, focusRingColor } from '@/theme/tokens';
import type { Locale } from '@/locale/en';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_MESSAGE_LENGTH = 2000;
const MAX_TEXT_FILE_BYTES = MAX_MESSAGE_LENGTH * 4;
const MIN_TEXTAREA_HEIGHT = 66; // ~3 lines of text (WI #255)
const MAX_TEXTAREA_HEIGHT = 140; // ~5 lines of text

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type AttachmentError = 'unsupported' | 'unreadable' | 'tooLong';

interface TextAttachment {
  name: string;
  text: string;
}

function formatAttachment(label: string, attachment: TextAttachment): string {
  return '[' + label + ': ' + attachment.name + ']\n' + attachment.text;
}

interface InputBarProps {
  tokens: DesignTokens;
  locale: Locale;
  onSend: (content: string) => void;
  isLoading: boolean;
  disabled: boolean;
  fileUploadEnabled: boolean;
  showBranding: boolean;
  inputPlaceholder: string | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const InputBar: FunctionComponent<InputBarProps> = ({
  tokens,
  locale,
  onSend,
  isLoading,
  disabled,
  fileUploadEnabled,
  showBranding,
  inputPlaceholder,
}) => {
  const [text, setText] = useState('');
  const [attachment, setAttachment] = useState<TextAttachment | null>(null);
  const [attachmentError, setAttachmentError] = useState<AttachmentError | null>(null);
  const [readingAttachment, setReadingAttachment] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentHelpId = useId();

  const placeholder = inputPlaceholder || locale.inputPlaceholder;
  const selectedAttachment = fileUploadEnabled ? attachment : null;
  const attachmentUnavailable = disabled || isLoading || readingAttachment;
  const fileMessage = selectedAttachment ? formatAttachment(locale.textAttachment.messageLabel, selectedAttachment) : '';
  const message = [text.trim(), fileMessage].filter(Boolean).join('\n\n');
  const exceedsLimit = message.length > MAX_MESSAGE_LENGTH;
  const visibleAttachmentError = exceedsLimit ? 'tooLong' : attachmentError;
  const canSend = message.length > 0 && !exceedsLimit && !attachmentUnavailable;

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(message);
    setText('');
    setAttachment(null);
    setAttachmentError(null);
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = `${MIN_TEXTAREA_HEIGHT}px`;
    }
  }, [canSend, message, onSend]);

  async function handleFileChange(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file || !fileUploadEnabled || attachmentUnavailable) return;

    setAttachmentError(null);
    if (!/\.txt$/i.test(file.name) || (file.type && file.type !== 'text/plain')) {
      setAttachmentError('unsupported');
      return;
    }
    if (file.size > MAX_TEXT_FILE_BYTES) {
      setAttachmentError('tooLong');
      return;
    }

    setReadingAttachment(true);
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      if (!text.trim() || text.includes('\0')) {
        setAttachmentError('unreadable');
        return;
      }

      const nextAttachment = { name: file.name.replace(/\s+/g, ' ').trim(), text };
      const content = formatAttachment(locale.textAttachment.messageLabel, nextAttachment);
      if (content.length > MAX_MESSAGE_LENGTH) {
        setAttachmentError('tooLong');
        return;
      }

      setAttachment(nextAttachment);
    } catch {
      setAttachmentError('unreadable');
    } finally {
      setReadingAttachment(false);
    }
  }

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  const handleInput = useCallback((e: Event) => {
    const target = e.target as HTMLTextAreaElement;
    let value = target.value;

    // Enforce max length
    if (value.length > MAX_MESSAGE_LENGTH) {
      value = value.substring(0, MAX_MESSAGE_LENGTH);
    }
    setText(value);

    // Auto-grow textarea
    target.style.height = `${MIN_TEXTAREA_HEIGHT}px`;
    const scrollHeight = target.scrollHeight;
    target.style.height = `${Math.min(scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, []);

  const inputBarRingColor = useMemo(() => focusRingColor(tokens.colorInputBarBg), [tokens.colorInputBarBg]);

  return (
    <div
      style={{
        borderTop: `${tokens.borderWidth} solid ${tokens.colorBorder}`,
        backgroundColor: tokens.colorInputBarBg,
        flexShrink: 0,
      }}
    >
      <style>{`
        .ar-input-attach:focus-visible { box-shadow: 0 0 0 2px ${inputBarRingColor} !important; }
        .ar-input-send:focus-visible { box-shadow: 0 0 0 3px ${inputBarRingColor} !important; }
      `}</style>
      {/* Input row — visible container with surface background */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          padding: `${tokens.space2} ${tokens.space3}`,
          marginTop: '5px',
          gap: tokens.space2,
        }}
      >
        {/* File upload button */}
        {fileUploadEnabled && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt"
              onChange={handleFileChange}
              hidden
            />
            <button
              type="button"
              className="ar-input-attach"
              aria-label={locale.attachFile}
              aria-describedby={attachmentHelpId}
              disabled={attachmentUnavailable}
              onClick={() => fileInputRef.current?.click()}
              style={{
                background: 'none',
                border: 'none',
                color: attachmentUnavailable ? tokens.colorTextMuted : tokens.colorTextSecondary,
                cursor: attachmentUnavailable ? 'default' : 'pointer',
                padding: tokens.space1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: tokens.borderRadiusSm,
                transition: `color ${tokens.transitionFast}, box-shadow ${tokens.transitionFast}`,
                outline: 'none',
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                if (!attachmentUnavailable) (e.currentTarget as HTMLElement).style.color = tokens.colorText;
              }}
              onMouseLeave={(e) => {
                if (!attachmentUnavailable) (e.currentTarget as HTMLElement).style.color = tokens.colorTextSecondary;
              }}
            >
              <PaperclipIcon size={18} />
            </button>
          </>
        )}

        {/* Textarea in visible container */}
        <div
          style={{
            flex: 1,
            backgroundColor: tokens.colorSurface,
            borderRadius: '8px',
            overflow: 'hidden',
          }}
        >
          <textarea
            ref={textareaRef}
            value={text}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={3}
            aria-label={placeholder}
            style={{
              width: '100%',
              resize: 'none',
              border: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
              color: tokens.colorText,
              fontSize: tokens.fontSizeSm,
              fontFamily: tokens.fontFamily,
              lineHeight: tokens.lineHeightNormal,
              padding: '8px 14px',
              height: `${MIN_TEXTAREA_HEIGHT}px`,
              maxHeight: `${MAX_TEXTAREA_HEIGHT}px`,
              overflow: 'auto',
              // Remove default textarea styling
              appearance: 'none' as unknown as string,
              WebkitAppearance: 'none',
            }}
          />
        </div>

        {/* Send button */}
        <button
          type="button"
          className="ar-input-send"
          aria-label={locale.sendButton}
          onClick={handleSend}
          disabled={!canSend}
          style={{
            width: '32px',
            height: '32px',
            borderRadius: tokens.borderRadiusFull,
            backgroundColor: tokens.colorPrimary,
            color: tokens.colorPrimaryText,
            border: 'none',
            cursor: canSend ? 'pointer' : 'default',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            transition: `background-color ${tokens.transitionFast}, color ${tokens.transitionFast}, box-shadow ${tokens.transitionFast}`,
            outline: 'none',
            padding: 0,
          }}
        >
          {isLoading ? <SpinnerIcon size={16} /> : <SendIcon size={16} />}
        </button>
      </div>

      {fileUploadEnabled && (
        <div
          style={{
            padding: `0 ${tokens.space3} ${tokens.space1}`,
            fontSize: tokens.fontSizeXs,
            fontFamily: tokens.fontFamily,
            color: tokens.colorTextSecondary,
          }}
        >
          <p id={attachmentHelpId}>{locale.textAttachment.help}</p>
          {readingAttachment && <p role="status">{locale.textAttachment.reading}</p>}
          {selectedAttachment && (
            <div style={{ display: 'flex', alignItems: 'center', gap: tokens.space2 }}>
              <span style={{ overflowWrap: 'anywhere' }}>{selectedAttachment.name}</span>
              <button
                type="button"
                disabled={attachmentUnavailable}
                onClick={() => {
                  setAttachment(null);
                  setAttachmentError(null);
                }}
                style={{ font: 'inherit', color: 'inherit', padding: tokens.space1 }}
              >
                {locale.textAttachment.remove}
              </button>
            </div>
          )}
          {visibleAttachmentError && (
            <p role="alert" style={{ color: tokens.colorError }}>
              {locale.textAttachment[visibleAttachmentError]}
            </p>
          )}
        </div>
      )}

      {/* Character count warning */}
      {message.length > MAX_MESSAGE_LENGTH * 0.9 && (
        <div
          style={{
            fontSize: tokens.fontSizeXs,
            fontFamily: tokens.fontFamily,
            color: message.length >= MAX_MESSAGE_LENGTH ? tokens.colorError : tokens.colorTextMuted,
            textAlign: 'right',
            padding: `0 ${tokens.space3} ${tokens.space1}`,
          }}
        >
          {message.length}/{MAX_MESSAGE_LENGTH}
        </div>
      )}

      {/* Powered by branding */}
      {showBranding && (
        <div
          aria-label={locale.poweredBy}
          style={{
            textAlign: 'center',
            padding: `${tokens.space1} 0 ${tokens.space2}`,
            fontSize: '10px',
            fontFamily: tokens.fontFamily,
            color: tokens.colorTextMuted,
          }}
        >
          <a
            href="https://agentredcx.com"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: 'inherit',
              textDecoration: 'none',
              transition: `color ${tokens.transitionFast}`,
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.color = tokens.colorTextSecondary;
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.color = tokens.colorTextMuted;
            }}
          >
            {locale.poweredByPrefix}{' '}
            <span style={{ fontWeight: 600, color: AGENT_RED_BRAND_COLOR }}>{locale.poweredByBrand}</span>
          </a>
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Icons (inline SVG)
// ---------------------------------------------------------------------------

function SendIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function PaperclipIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  );
}

function SpinnerIcon({ size }: { size: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2.5"
      stroke-linecap="round"
      style={{
        animation: 'ar-spin 0.8s linear infinite',
      }}
    >
      <path d="M12 2a10 10 0 0 1 10 10" />
    </svg>
  );
}
