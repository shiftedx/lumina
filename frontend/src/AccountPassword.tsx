import { useState, type FormEvent } from 'react';

import { changePassword } from './api';
import { Button, Field, fieldProps, PasswordInput } from './ui';

/** Self-service password change: proves the current password; other devices are signed out server-side. */
export function AccountPassword() {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setResult(null);
    try {
      await changePassword(currentPassword, newPassword);
      setCurrentPassword('');
      setNewPassword('');
      setResult({ ok: true, message: 'Password changed. Your other devices were signed out.' });
    } catch (changeError) {
      setResult({ ok: false, message: changeError instanceof Error ? changeError.message : 'Unable to change your password.' });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form aria-label="Change password" className="g-subrows" onSubmit={(event) => { void submit(event); }}>
      <Field label="Current password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" onChange={(event) => setCurrentPassword(event.target.value)} required value={currentPassword} />}</Field>
      <Field hint="At least 12 characters with three of: lowercase, uppercase, number, symbol." label="New password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="new-password" minLength={12} onChange={(event) => setNewPassword(event.target.value)} required value={newPassword} />}</Field>
      <div className="g-actions">
        {result ? <p className={result.ok ? 'g-setting-note' : 'auth-error'} role={result.ok ? 'status' : 'alert'}>{result.message}</p> : null}
        <Button busy={busy} type="submit" variant="secondary">{busy ? 'Changing password…' : 'Change password'}</Button>
      </div>
    </form>
  );
}
