import { Mail } from 'lucide-react';
import { useState } from 'react';

import { Button, Field, fieldProps, Input, PasswordInput, SegmentedControl, StatusText } from '../../../ui';
import { errorMessage } from '../../../utils';
import { type RequestsSettings, type RequestsSettingsInput, sendSmtpTest, type SmtpSecurity, updateRequestsSettings } from '../requestsSettingsApi';
import { type FormStatus, SectionForm } from '../SectionForm';
import { SettingSwitch } from '../SettingRow';
import { useSettingsHost } from '../settingsHost';
import { useRequestsForm } from './requestsContext';

type Draft = { host: string; port: string; security: SmtpSecurity; username: string; password: string; from: string };
const draftOf = (settings: RequestsSettings): Draft => ({
  host: settings.smtp?.host ?? '', port: settings.smtp?.port == null ? '' : String(settings.smtp.port), security: settings.smtp?.security ?? 'starttls',
  username: settings.smtp?.username ?? '', from: settings.smtp?.from ?? '', password: '',
});

/** The whole settings object, so the on/off switch and the SMTP form never overwrite each other. The password is sent only when typed. */
export function settingsPayload(requests_enabled: boolean, draft: Draft): RequestsSettingsInput {
  return { requests_enabled, smtp: { host: draft.host.trim() || null, port: draft.port.trim() === '' ? null : Number(draft.port), security: draft.security, username: draft.username.trim() || null, from: draft.from.trim() || null, ...(draft.password ? { password: draft.password } : {}) } };
}

export function RequestsEnabled() {
  const { settings } = useRequestsForm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const data = settings.data;
  if (!data) return settings.error ? <p className="auth-error" role="alert">{settings.error}</p> : <p className="admin-note" role="status">Loading…</p>;
  async function toggle(next: boolean) {
    setBusy(true);
    setError('');
    try {
      settings.setData(await updateRequestsSettings(settingsPayload(next, draftOf(data!))));
    } catch (failure) {
      setError(errorMessage(failure, 'Unable to change Requests.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SettingSwitch busy={busy} checked={data.requests_enabled} onChange={(next) => { void toggle(next); }} />
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
    </>
  );
}

export function SmtpForm() {
  const { settings } = useRequestsForm();
  const showAdvanced = useSettingsHost().showAdvanced ?? true;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<FormStatus>(null);
  const data = settings.data;
  if (!data) return settings.error ? <p className="auth-error" role="alert">{settings.error}</p> : <p className="admin-note" role="status">Loading…</p>;
  const current = draft ?? draftOf(data);
  const dirty = JSON.stringify(settingsPayload(data.requests_enabled, current)) !== JSON.stringify(settingsPayload(data.requests_enabled, draftOf(data)));
  const edit = (patch: Partial<Draft>) => { setStatus(null); setDraft({ ...current, ...patch }); };
  const text = (label: string, key: 'host' | 'username' | 'from', hint?: string) => (
    <Field hint={hint} label={label}>{(ids) => <Input {...fieldProps(ids)} autoComplete="off" onChange={(event) => edit({ [key]: event.currentTarget.value })} value={current[key]} />}</Field>
  );

  async function save() {
    if (current.port.trim() !== '' && (!Number.isInteger(Number(current.port)) || Number(current.port) < 1 || Number(current.port) > 65535)) { setStatus({ tone: 'error', text: 'Port must be a number from 1 to 65535.' }); return; }
    setSaving(true);
    setStatus(null);
    try {
      settings.setData(await updateRequestsSettings(settingsPayload(data!.requests_enabled, current)));
      setDraft(null);
      setStatus({ tone: 'ok', text: 'Email settings saved.' });
    } catch (error) {
      setStatus({ tone: 'error', text: errorMessage(error, 'Unable to save the email settings.') });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="g-req">
      <SectionForm dirty={dirty} label="Email (SMTP)" onDiscard={() => { setDraft(null); setStatus(null); }} onSave={() => { void save(); }} saveLabel="Save email settings" saving={saving} status={status}>
        <div className="g-subrows">
          {text('SMTP host', 'host')}
          {showAdvanced ? (
            <>
              <Field label="SMTP port">{(ids) => <Input {...fieldProps(ids)} inputMode="numeric" onChange={(event) => edit({ port: event.currentTarget.value })} value={current.port} />}</Field>
              <SegmentedControl legend="Connection security" onChange={(security) => edit({ security })} options={[{ value: 'starttls', label: 'STARTTLS' }, { value: 'ssl', label: 'SSL' }, { value: 'none', label: 'None' }]} value={current.security} />
            </>
          ) : null}
          {text('SMTP username', 'username')}
          <Field hint={data.smtp?.password_set ? 'A password is saved. Type a new one to replace it.' : undefined} label="SMTP password">
            {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" onChange={(event) => edit({ password: event.currentTarget.value })} placeholder={data.smtp?.password_set ? 'Saved password hidden' : ''} value={current.password} />}
          </Field>
          {text('From address', 'from', 'For example Lumina <lumina@home.example>')}
        </div>
      </SectionForm>
    </div>
  );
}

export function SmtpTest() {
  const [to, setTo] = useState('');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  async function send() {
    setSending(true);
    setResult(null);
    try {
      const reply = await sendSmtpTest(to.trim());
      setResult(reply && reply.ok === false ? { ok: false, text: reply.error ?? 'The test email could not be sent.' } : { ok: true, text: `Test email sent to ${to.trim()}.` });
    } catch (error) {
      setResult({ ok: false, text: errorMessage(error, 'The test email could not be sent.') });
    } finally {
      setSending(false);
    }
  }
  return (
    <form className="g-subrows" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <Field hint="Uses the saved email settings, so save them first." label="Send test email to">{(ids) => <Input {...fieldProps(ids)} autoComplete="email" inputMode="email" onChange={(event) => { setResult(null); setTo(event.currentTarget.value); }} type="email" value={to} />}</Field>
      <div className="g-actions">
        {result ? <div role={result.ok ? 'status' : 'alert'}><StatusText tone={result.ok ? 'ok' : 'danger'}>{result.text}</StatusText></div> : null}
        <Button busy={sending} disabled={!to.trim()} icon={<Mail />} type="submit">{sending ? 'Sending…' : 'Send test email'}</Button>
      </div>
    </form>
  );
}
