// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

import type { FunctionComponent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Locale } from '@/locale/en';
import type { DesignTokens } from '@/theme/tokens';
import { sendOtp, verifyOtp } from '@/transport/http';
import { OtpVerification } from './OtpVerification';

interface VisitorEmailVerificationProps {
  tokens: DesignTokens;
  locale: Locale;
  onVerified: (email: string, customerToken: string) => void;
  onCancel: () => void;
}

export const VisitorEmailVerification: FunctionComponent<VisitorEmailVerificationProps> = ({
  tokens, locale, onVerified, onCancel,
}) => {
  const [email, setEmail] = useState('');
  const [codeRequested, setCodeRequested] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string>();
  const emailInput = useRef<HTMLInputElement>(null);
  const active = useRef(true);
  const busy = useRef(false);

  useEffect(() => {
    emailInput.current?.focus();
    return () => { active.current = false; };
  }, []);

  async function requestCode() {
    if (busy.current) return;
    const address = email.trim();
    if (!address) return;
    busy.current = true;
    setIsLoading(true);
    setError(undefined);
    const sent = await sendOtp(address);
    if (!active.current) return;
    setIsLoading(false);
    busy.current = false;
    if (sent) {
      setEmail(address);
      setCodeRequested(true);
    } else {
      setError(locale.emailSendFailed);
    }
  }

  async function submitCode(code: string) {
    if (busy.current) return;
    busy.current = true;
    setIsLoading(true);
    setError(undefined);
    const result = await verifyOtp(email, code);
    if (!active.current) return;
    busy.current = false;
    setIsLoading(false);
    if (result.verified && result.customerToken) {
      onVerified(email, result.customerToken);
    } else {
      setError(locale.otpInvalid);
    }
  }

  if (codeRequested) {
    return (
      <OtpVerification
        tokens={tokens}
        locale={locale}
        email={email}
        onVerify={submitCode}
        onResend={requestCode}
        onSkip={onCancel}
        isLoading={isLoading}
        error={error}
      />
    );
  }

  return (
    <form
      aria-label={locale.verifyEmail}
      onSubmit={(event) => {
        event.preventDefault();
        void requestCode();
      }}
      style={{
        flex: '1',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: tokens.space6,
        gap: tokens.space3,
        overflow: 'auto',
      }}
    >
      <label style={{ display: 'flex', flexDirection: 'column', gap: tokens.space2 }}>
        {locale.offlineFormEmail}
        <input
          ref={emailInput}
          type="email"
          autoComplete="email"
          maxLength={254}
          required
          disabled={isLoading}
          value={email}
          onInput={(event) => setEmail(event.currentTarget.value)}
          style={{
            padding: tokens.space3,
            font: 'inherit',
            background: tokens.colorBackground,
            color: tokens.colorText,
            border: tokens.borderWidth + ' solid ' + tokens.colorBorder,
            borderRadius: tokens.borderRadius,
          }}
        />
      </label>
      {error && <p role="alert" style={{ color: tokens.colorError }}>{error}</p>}
      <button
        type="submit"
        disabled={isLoading}
        style={{
          padding: tokens.space3,
          font: 'inherit',
          color: tokens.colorPrimaryText,
          background: tokens.colorPrimary,
          border: 'none',
          borderRadius: tokens.borderRadius,
        }}
      >
        {isLoading ? '...' : locale.emailSendCode}
      </button>
      <button
        type="button"
        onClick={onCancel}
        style={{
          padding: tokens.space2,
          font: 'inherit',
          color: tokens.colorText,
          background: 'transparent',
          border: 'none',
          textDecoration: 'underline',
          cursor: 'pointer',
        }}
      >
        {locale.otpSkip}
      </button>
    </form>
  );
};
