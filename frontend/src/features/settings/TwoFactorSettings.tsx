import { type FormEvent, useEffect, useState } from 'react';
import { Copy, Download } from 'lucide-react';

import { disableTwoFactor, enableTwoFactor, getTwoFactor, regenerateRecoveryCodes, setupTwoFactor } from '../../api';
import type { TwoFactorSetup, TwoFactorStatus } from '../../types';
import { errorMessage } from '../../utils';
import { Button, Field, fieldProps, Input, PasswordInput } from '../../ui';
import { CopyValue } from './CopyValue';
import { useSettingsHost } from './settingsHost';

type Step =
  | { kind: 'idle' }
  | { kind: 'password' }
  | { kind: 'scan'; setup: TwoFactorSetup }
  | { kind: 'codes'; codes: string[]; enrolled: boolean }
  | { kind: 'apps' }
  | { kind: 'proof'; action: 'disable' | 'regenerate' };

// Forced light system colours: the code scans in dark theme too.
const QR_STYLE = { padding: 4, colorScheme: 'light', background: 'Canvas', color: 'CanvasText' } as const;
const group = (secret: string) => secret.replace(/\s+/g, '').replace(/(.{4})/g, '$1 ').trim();

/** Shown once: the only copy of the recovery codes. */
function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [status, setStatus] = useState('');
  const text = codes.join('\n');
  async function copy() {
    try { await navigator.clipboard.writeText(text); setStatus('Recovery codes copied.'); } catch { setStatus('Copy is blocked here. Select the codes and copy them.'); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([`Lumina recovery codes\nEach works once.\n\n${text}\n`], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'lumina-recovery-codes.txt';
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="g-subrow g-twofactor-codes">
      <p><strong>Save these recovery codes now. Lumina shows them only once.</strong></p>
      <p className="g-setting-note">If you lose your phone, each code signs you in once in place of the 6-digit code.</p>
      <ul aria-label="Recovery codes" className="g-mono">{codes.map((code) => <li key={code}>{code}</li>)}</ul>
      <div className="g-actions">
        <Button icon={<Copy />} onClick={() => { void copy(); }} variant="secondary">Copy codes</Button>
        <Button icon={<Download />} onClick={download} variant="secondary">Download .txt</Button>
        <Button onClick={onDone} variant="primary">I saved these</Button>
      </div>
      <p aria-live="polite" className="g-setting-note" role="status">{status}</p>
    </div>
  );
}

/** Settings → You → Profile: turn two-step verification on or off, and manage recovery codes. */
export function TwoFactorSettings() {
  const { user, onOpenSection } = useSettingsHost();
  const [status, setStatus] = useState<TwoFactorStatus | null>(null);
  const [step, setStep] = useState<Step>({ kind: 'idle' });
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const reload = () => getTwoFactor().then(setStatus).catch((failure) => setLoadError(errorMessage(failure, 'Lumina could not load your two-step verification.')));
  useEffect(() => { void reload(); }, []);
  const go = (next: Step) => { setStep(next); setPassword(''); setCode(''); setError(null); };

  async function run(task: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError(null);
    try { await task(); } catch (failure) { setError(errorMessage(failure, fallback)); } finally { setBusy(false); }
  }

  const submitPassword = (event: FormEvent) => { event.preventDefault(); void run(async () => { const setup = await setupTwoFactor(password); go({ kind: 'scan', setup }); }, 'Lumina could not start two-step verification.'); };
  const submitCode = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => { const { recovery_codes } = await enableTwoFactor(code.trim()); await reload(); go({ kind: 'codes', codes: recovery_codes, enrolled: true }); }, 'That code did not match.');
  };
  const submitProof = (event: FormEvent) => {
    event.preventDefault();
    if (step.kind !== 'proof') return;
    const action = step.action;
    void run(async () => {
      if (action === 'disable') { await disableTwoFactor(password, code.trim()); await reload(); go({ kind: 'idle' }); setNote('Two-step verification is off.'); }
      else { const { recovery_codes } = await regenerateRecoveryCodes(password, code.trim()); await reload(); go({ kind: 'codes', codes: recovery_codes, enrolled: false }); }
    }, 'Lumina could not confirm that.');
  };

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (!status) return <p className="g-setting-note" role="status">Loading…</p>;
  const errorLine = error ? <p className="auth-error" role="alert">{error}</p> : null;

  return (
    <div className="g-subrows">
      {user.two_factor_setup_required && !status.enabled ? (
        <p className="g-setting-note" role="alert"><strong>This household requires two-step verification for vault owners.</strong> Turn it on to open the server settings again.</p>
      ) : null}
      {step.kind === 'idle' ? (
        <div className="g-subrow">
          <p className="g-setting-note" role="status">{status.enabled ? `On. ${status.recovery_codes_left} recovery ${status.recovery_codes_left === 1 ? 'code' : 'codes'} left.` : 'Off. Signing in takes only your password.'}</p>
          <div className="g-actions">
            {status.enabled ? (
              <>
                <Button onClick={() => go({ kind: 'proof', action: 'regenerate' })} variant="secondary">New recovery codes</Button>
                {status.required ? null : <Button onClick={() => go({ kind: 'proof', action: 'disable' })} variant="quiet">Turn off</Button>}
              </>
            ) : <Button onClick={() => go({ kind: 'password' })} variant="secondary">Turn on</Button>}
          </div>
          {status.enabled && status.required ? <p className="g-setting-note">The household requires two-step verification for vault owners, so it cannot be turned off.</p> : null}
          {note ? <p className="g-setting-note" role="status">{note}</p> : null}
        </div>
      ) : null}
      {step.kind === 'password' ? (
        <form aria-label="Turn on two-step verification" className="g-subrows" onSubmit={submitPassword}>
          <Field error={error} hint="Lumina checks it is really you before showing the setup key." label="Your password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} required value={password} />}</Field>
          <div className="g-actions"><Button busy={busy} disabled={!password} type="submit" variant="primary">Continue</Button><Button onClick={() => go({ kind: 'idle' })} variant="quiet">Cancel</Button></div>
        </form>
      ) : null}
      {step.kind === 'scan' ? (
        <form aria-label="Connect your authenticator app" className="g-subrows" onSubmit={submitCode}>
          <div className="g-subrow">
            <p>Scan this with an authenticator app (such as Google Authenticator, 1Password or Authy), then enter the 6-digit code it shows.</p>
            <svg aria-label="QR code for your authenticator app" role="img" style={QR_STYLE} width="192" viewBox={`0 0 ${step.setup.qr_size} ${step.setup.qr_size}`}><path d={step.setup.qr_path} fill="currentColor" /></svg>
            <CopyValue copyLabel="Copy setup key" label="Setup key (if you cannot scan)" value={group(step.setup.secret)} />
            <a className="g-setting-note" href={step.setup.otpauth_uri}>Open in an authenticator app on this device</a>
          </div>
          <Field error={error} label="6-digit code">{(ids) => <Input {...fieldProps(ids)} autoComplete="one-time-code" inputMode="numeric" maxLength={6} onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))} pattern="[0-9]*" required value={code} />}</Field>
          <div className="g-actions"><Button busy={busy} disabled={code.length !== 6} type="submit" variant="primary">Turn on</Button><Button onClick={() => go({ kind: 'idle' })} variant="quiet">Cancel</Button></div>
        </form>
      ) : null}
      {step.kind === 'codes' ? <RecoveryCodes codes={step.codes} onDone={() => { if (step.enrolled) go({ kind: 'apps' }); else { go({ kind: 'idle' }); setNote('New recovery codes saved. The old ones no longer work.'); } }} /> : null}
      {step.kind === 'apps' ? (
        <div className="g-subrow">
          <p><strong>Two-step verification is on.</strong></p>
          <p className="g-setting-note">Use Infuse or another app? Apps like that cannot ask for your code, so they sign in with an app password instead.</p>
          <div className="g-actions">
            <Button onClick={() => { go({ kind: 'idle' }); onOpenSection?.('apps'); }} variant="primary">Create an app password</Button>
            <Button onClick={() => go({ kind: 'idle' })} variant="quiet">Not now</Button>
          </div>
        </div>
      ) : null}
      {step.kind === 'proof' ? (
        <form aria-label={step.action === 'disable' ? 'Turn off two-step verification' : 'Make new recovery codes'} className="g-subrows" onSubmit={submitProof}>
          <Field label="Your password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} required value={password} />}</Field>
          <Field error={error} hint="A 6-digit code, or one of your recovery codes." label="Code">{(ids) => <Input {...fieldProps(ids)} autoComplete="one-time-code" onChange={(event) => setCode(event.target.value)} required value={code} />}</Field>
          <div className="g-actions"><Button busy={busy} disabled={!password || !code.trim()} type="submit" variant={step.action === 'disable' ? 'danger' : 'primary'}>{step.action === 'disable' ? 'Turn off' : 'Make new codes'}</Button><Button onClick={() => go({ kind: 'idle' })} variant="quiet">Cancel</Button></div>
        </form>
      ) : null}
      {step.kind === 'idle' ? errorLine : null}
    </div>
  );
}

