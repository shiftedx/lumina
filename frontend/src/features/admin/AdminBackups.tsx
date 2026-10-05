import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { DatabaseBackup } from 'lucide-react';

import { type BackupManifest, type BackupSchedule, createBackup, deleteBackup, downloadBackup, listBackups, updateBackupSchedule, verifyBackup } from '../../api';
import { Button, ConfirmDialog, Field, fieldProps, Input, PasswordInput, StatusText } from '../../ui';
import { SettingSwitch } from '../settings/SettingRow';
import { errorMessage as errorText, formatBytes, formatDateTime as time } from '../../utils';
import { useAdminResource } from './useAdminResource';

type Check = { busy: boolean; ok?: boolean; text?: string };

export function AdminBackups() {
  const [backups, setBackups] = useState<BackupManifest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [deleting, setDeleting] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    try {
      const next = await listBackups();
      if (!mounted.current) return;
      setBackups(next.backups);
      setError(null);
    } catch (loadError) {
      if (mounted.current) setError(errorText(loadError, 'Unable to load backups.'));
    }
  }, []);
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; }; }, [load]);

  async function create() {
    setCreating(true);
    setError(null);
    try {
      const made = await createBackup();
      setChecks((current) => ({ ...current, [made.name]: { busy: false, ok: true, text: 'Created and checksummed' } }));
      await load();
    } catch (createError) {
      setError(errorText(createError, 'Unable to create a backup.'));
    } finally {
      setCreating(false);
    }
  }

  async function verify(name: string) {
    setChecks((current) => ({ ...current, [name]: { busy: true } }));
    try {
      const result = await verifyBackup(name);
      setChecks((current) => ({ ...current, [name]: { busy: false, ok: result.ok, text: result.ok ? 'Verified: intact and restorable' : result.problems.join('; ') } }));
    } catch (verifyError) {
      setChecks((current) => ({ ...current, [name]: { busy: false, ok: false, text: errorText(verifyError, 'Verification failed.') } }));
    }
  }

  async function remove(name: string) {
    setDeleting(null);
    try {
      await deleteBackup(name);
      await load();
    } catch (deleteError) {
      setError(errorText(deleteError, 'Unable to delete the backup.'));
    }
  }

  return (
    <div aria-busy={backups === null} className="admin-overview">
      <div className="admin-toolbar">
        <p role="status">{backups === null ? (error ? 'Backups unavailable' : 'Loading backups…') : `${backups.length} ${backups.length === 1 ? 'backup' : 'backups'} on the server`}</p>
        <Button disabled={creating} icon={<DatabaseBackup />} onClick={() => { void create(); }} variant="primary">{creating ? 'Backing up…' : 'Back up now'}</Button>
      </div>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}

      <section aria-labelledby="admin-backups-title" className="admin-panel">
        <h2 id="admin-backups-title">Database backups</h2>
        <p className="admin-note">A consistent copy of accounts, library records, notes, history and settings, taken while Lumina keeps running. <strong>Media files are not included</strong> — back up your storage roots separately; a restore expects them at the same paths.</p>
        {backups?.length ? (
          <ul aria-label="Backups" className="admin-backups">{backups.map((backup) => {
            const check = checks[backup.name];
            const when = time(backup.created_at);
            return (
              <li aria-label={`${backup.kind === 'scheduled' ? 'Automatic' : 'Manual'} backup from ${when}`} className="g-list-row" key={backup.name}>
                <div>
                  <strong>{when}</strong>
                  <small>{backup.kind === 'scheduled' ? 'Automatic' : 'Manual'} · {formatBytes(backup.size)} · {backup.counts.users ?? 0} members · {backup.counts.library_items ?? 0} library items · {backup.counts.library_notes ?? 0} notes</small>
                  <span aria-live="polite">{check?.busy ? <small>Verifying…</small> : check?.text ? <StatusText tone={check.ok ? 'ok' : 'danger'}>{check.text}</StatusText> : null}</span>
                </div>
                <div className="admin-member-actions">
                  <Button aria-label={`Verify backup from ${when}`} disabled={check?.busy} onClick={() => { void verify(backup.name); }}>Verify</Button>
                  <Button aria-expanded={downloading === backup.name} aria-label={`Download backup from ${when}`} id={`backup-${backup.name}-download`} onClick={() => setDownloading(backup.name)}>Download</Button>
                  <Button aria-label={`Delete backup from ${when}`} id={`backup-${backup.name}-delete`} onClick={() => setDeleting(backup.name)}>Delete</Button>
                </div>
                {downloading === backup.name ? <DownloadConfirm name={backup.name} onClose={() => { setDownloading(null); document.getElementById(`backup-${backup.name}-download`)?.focus(); }} /> : null}
                <ConfirmDialog
                  body={`The copy from ${when} is removed from the server and cannot be restored from.`}
                  confirmLabel="Delete backup"
                  danger
                  onCancel={() => setDeleting(null)}
                  onConfirm={() => { void remove(backup.name); }}
                  open={deleting === backup.name}
                  title="Delete this backup?"
                />
              </li>
            );
          })}</ul>
        ) : backups ? <p className="admin-note">No backups yet. Create one now, or let the daily automatic backup run.</p> : null}
      </section>

      <section aria-labelledby="admin-restore-title" className="admin-panel">
        <h2 id="admin-restore-title">Restoring</h2>
        <p className="admin-note">Restore runs offline so no one is signed in while the database changes. Stop Lumina, download or locate the backup (it sits in <code>backups/</code> in the data directory with its <code>.json</code> manifest), then run:</p>
        <pre className="g-log"><code>python -m app.restore backups/&lt;name&gt;.db</code></pre>
        <p className="admin-note">The backup is verified first and the current database is kept beside it. Everyone signs in again afterwards, unused invitation and reset links stop working, and the AI API key must be re-entered under AI & models (backups never contain it).</p>
      </section>
    </div>
  );
}

function DownloadConfirm({ name, onClose }: { name: string; onClose: () => void }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const url = URL.createObjectURL(await downloadBackup(name, password));
      const link = document.createElement('a');
      link.href = url;
      link.download = `${name}.db`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onClose();
    } catch (downloadError) {
      setError(errorText(downloadError, 'Unable to download the backup.'));
      setBusy(false);
    }
  }

  return (
    <form aria-labelledby={`${name}-download-confirm`} className="admin-download" onSubmit={(event) => { void submit(event); }}>
      <p id={`${name}-download-confirm`}><strong>Confirm your password to download.</strong> The copy holds every account&apos;s password hash; keep it somewhere private.</p>
      <Field label="Your password" required>
        {(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" autoFocus onChange={(event) => setPassword(event.target.value)} required value={password} />}
      </Field>
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      <div className="admin-member-actions">
        <Button disabled={busy} type="submit" variant="primary">{busy ? 'Downloading…' : 'Download backup'}</Button>
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </form>
  );
}

function ScheduleForm({ initial }: { initial: BackupSchedule }) {
  const [daily, setDaily] = useState(initial.daily);
  const [keep, setKeep] = useState(String(initial.keep));
  const [status, setStatus] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setStatus(null);
    try {
      await updateBackupSchedule({ daily, keep: Number(keep) });
      setStatus({ tone: 'ok', text: 'Saved.' });
    } catch (saveError) {
      setStatus({ tone: 'error', text: errorText(saveError, 'Unable to save the schedule.') });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label="Automatic backups" className="g-subrows" onSubmit={(event) => { void save(event); }}>
      <SettingSwitch checked={daily} label="Back up the database every day" onChange={setDaily} />
      <Field hint="Older automatic backups are removed. Manual backups are kept until you delete them." label="Automatic backups to keep">
        {(ids) => <Input {...fieldProps(ids)} inputMode="numeric" max={90} min={1} onChange={(event) => setKeep(event.target.value)} required type="number" value={keep} />}
      </Field>
      <div className="g-actions">
        {status ? <div role={status.tone === 'error' ? 'alert' : 'status'}><StatusText tone={status.tone === 'error' ? 'danger' : 'ok'}>{status.text}</StatusText></div> : null}
        <Button busy={saving} type="submit" variant="primary">{saving ? 'Saving…' : 'Save schedule'}</Button>
      </div>
    </form>
  );
}

/** The automatic-backup schedule as its own Settings row; it reads the schedule from the backup list. */
export function BackupSchedule() {
  const { data, error } = useAdminResource(listBackups, 'Unable to load the backup schedule.');
  if (error && !data) return <p className="auth-error" role="alert">{error}</p>;
  return data ? <ScheduleForm initial={data.schedule} /> : <p className="admin-note" role="status">Loading the schedule…</p>;
}
