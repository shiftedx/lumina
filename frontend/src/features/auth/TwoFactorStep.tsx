import { type FormEvent, useEffect, useRef, useState } from 'react';
import { ApiRequestError, verifyTwoFactor } from '../../api';
import { Button, Checkbox, Field, fieldProps, Input } from '../../ui';

export const TWO_FACTOR_EXPIRED = 'Your sign-in timed out. Enter your password again.';

export interface TwoFactorStepProps {
  challenge: string;
  /** The session cookie is set; continue exactly like a successful password sign-in. */
  onVerified: () => void | Promise<void>;
  /** Back to the password form; `message` says why when the challenge ran out. */
  onBack: (message?: string) => void;
}

/** Second step of sign-in for an account with two-step verification (a code from the authenticator app, or a recovery code). */
export function TwoFactorStep({ challenge, onVerified, onBack }: TwoFactorStepProps) {
  const [value, setValue] = useState('');
  const [recovery, setRecovery] = useState(false);
  const [trust, setTrust] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; n: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const sent = useRef('');
  useEffect(() => { input.current?.focus(); }, [recovery]);

  async function submit(raw: string) {
    const code = raw.trim();
    if (busy || !code) return;
    setBusy(true);
    try {
      await verifyTwoFactor({ challenge, ...(recovery ? { recovery_code: code } : { code }), trust_device: trust });
    } catch (failure) {
      if (failure instanceof ApiRequestError && failure.status === 410) { onBack(TWO_FACTOR_EXPIRED); return; }
      const text = failure instanceof ApiRequestError && failure.status === 429
        ? failure.message || 'Too many attempts. Wait a minute and try again.'
        : failure instanceof Error ? failure.message : 'Unable to verify that code.';
      setError({ text, n: (error?.n ?? 0) + 1 });
      setValue('');
      sent.current = '';
      setBusy(false);
      input.current?.focus();
      return;
    }
    await onVerified();
  }

  function change(next: string) {
    const clean = recovery ? next : next.replace(/\D/g, '').slice(0, 6);
    setValue(clean);
    if (!recovery && clean.length === 6 && sent.current !== clean) { sent.current = clean; void submit(clean); }
  }

  return (
    <form aria-labelledby="two-factor-title" className="g-auth-form" noValidate onSubmit={(event: FormEvent) => { event.preventDefault(); void submit(value); }}>
      <h2 className="g-picker-name" id="two-factor-title">Two-step verification</h2>
      <p className="g-setting-note">{recovery ? 'Enter one of the recovery codes you saved. Each works once.' : 'Enter the 6-digit code from your authenticator app.'}</p>
      <Field label={recovery ? 'Recovery code' : 'Code'}>
        {(ids) => recovery
          ? <Input {...fieldProps(ids)} autoCapitalize="none" autoComplete="off" onChange={(event) => change(event.target.value)} placeholder="abcd-efgh-jkmn-pqrs" ref={input} spellCheck={false} value={value} />
          : <Input {...fieldProps(ids)} autoComplete="one-time-code" inputMode="numeric" maxLength={6} onChange={(event) => change(event.target.value)} pattern="[0-9]*" ref={input} value={value} />}
      </Field>
      <div role="alert">{error ? <p className="g-field-error" key={error.n}>{error.text}</p> : null}</div>
      <Checkbox checked={trust} label="Don't ask again on this device for 30 days" onChange={(event) => setTrust(event.target.checked)} />
      <Button busy={busy} disabled={!value.trim()} type="submit" variant="primary" wide>Verify</Button>
      <Button onClick={() => { setRecovery(!recovery); setValue(''); setError(null); }} variant="quiet" wide>{recovery ? 'Use my authenticator code instead' : 'Use a recovery code instead'}</Button>
      <Button onClick={() => onBack()} variant="quiet" wide>Back</Button>
    </form>
  );
}
