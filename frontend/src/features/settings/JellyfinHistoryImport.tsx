import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { History } from 'lucide-react';

import { getJellyfinImport, previewJellyfinImport, runJellyfinImport } from '../../api';
import type { JellyfinImportStatus, JellyfinImportSummary } from '../../types';
import { Button, Field, fieldProps, Input, PasswordInput, Dialog } from '../../ui';
import { errorMessage } from '../../utils';
import { useSettingsHost } from './settingsHost';

type Count = Exclude<keyof JellyfinImportSummary, 'unmatched_names'>;
const COUNTS: Array<[Count, string]> = [['watched', 'Watched'], ['in_progress', 'In progress'], ['favorites', 'New favorites'], ['up_to_date', 'Already up to date'], ['unmatched', 'Not in Lumina']];

/** Privacy & data → Watch history from Jellyfin. The password lives only in this component's state, for Preview and Import. */
export function JellyfinHistoryImport() {
  const { user } = useSettingsHost();
  const [status, setStatus] = useState<JellyfinImportStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [preview, setPreview] = useState<JellyfinImportSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const previewRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let live = true;
    getJellyfinImport().then(
      (next) => { if (live) setStatus(next); },
      (failure: unknown) => { if (live) setLoadError(errorMessage(failure, 'Unable to load Jellyfin import.')); },
    );
    return () => { live = false; };
  }, []);

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (!status) return <p className="g-setting-note" role="status">Loading…</p>;
  if (!status.server) return <p className="g-setting-note">{user.role === 'admin' ? 'Set the Jellyfin server address under Media server first.' : 'Ask a vault owner to set the Jellyfin server address.'}</p>;

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPreview(await previewJellyfinImport(username.trim(), password));
    } catch (failure) {
      setError(errorMessage(failure, 'The preview could not run.'));
    } finally {
      setBusy(false);
    }
  }

  async function importHistory() {
    setBusy(true);
    setError(null);
    try {
      const done = await runJellyfinImport(username.trim(), password);
      setPassword('');
      setNotice(`Imported from Jellyfin: ${done.watched} watched, ${done.in_progress} in progress, ${done.favorites} new favorites.`);
      closeDialog();
    } catch (failure) {
      setError(errorMessage(failure, 'The import could not run.'));
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  // After the dialog unmounts; a modal dialog blocks focus outside it while open (matches EnableDialog's host in aiFeatures.tsx).
  function closeDialog() {
    setPreview(null);
    window.setTimeout(() => previewRef.current?.focus(), 0);
  }

  function cancel() {
    setError(null);
    setPassword('');
    closeDialog();
  }

  return (
    <form className="g-subrows" onSubmit={(event) => { void check(event); }}>
      <p className="g-setting-note">Jellyfin server: {status.server}</p>
      <Field label="Jellyfin username">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={256} onChange={(event) => setUsername(event.target.value)} required spellCheck={false} value={username} />}</Field>
      <Field label="Jellyfin password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" maxLength={1024} onChange={(event) => setPassword(event.target.value)} value={password} />}</Field>
      <div className="g-actions"><Button disabled={busy || !username.trim()} icon={<History />} ref={previewRef} type="submit">Preview</Button></div>
      {error && !preview ? <p className="auth-error" role="alert">{error}</p> : null}
      {notice ? <p className="g-setting-note" role="status">{notice}</p> : null}
      {/* Portal: a modal belongs in the top layer, outside the settings pane. */}
      {preview ? createPortal(<ImportDialog busy={busy} error={error} onCancel={cancel} onImport={() => { void importHistory(); }} preview={preview} />, document.body) : null}
    </form>
  );
}

function ImportDialog({ preview, busy, error, onImport, onCancel }: {
  preview: JellyfinImportSummary; busy: boolean; error: string | null; onImport: () => void; onCancel: () => void;
}) {
  const changes = preview.watched + preview.in_progress + preview.favorites;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (!busy) onCancel(); }
  };
  // The TV Back key reaches the footer and header buttons too, so the handler wraps the whole Dialog (React bubbles through the portal).
  return (
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog
        busy={busy}
        footer={<><Button busy={busy} disabled={!changes} onClick={onImport} variant="primary">{busy ? 'Importing…' : 'Import history'}</Button><Button disabled={busy} onClick={onCancel} variant="quiet">Cancel</Button></>}
        onClose={onCancel}
        open
        size="md"
        title="Import from Jellyfin?"
      >
          <ul aria-label="What would change" className="admin-counts">
            {COUNTS.map(([key, label]) => <li key={key}><span>{label}</span><strong className="g-tabular">{preview[key]}</strong></li>)}
          </ul>
          {preview.unmatched_names.length ? (
            <details className="admin-panel">
              <summary>Not in Lumina ({preview.unmatched})</summary>
              <ul className="g-list">{preview.unmatched_names.map((name, index) => <li className="g-list-row" key={index}>{name}</li>)}</ul>
            </details>
          ) : null}
          <p>{changes ? 'Nothing you watched more recently in Lumina changes, and favorites are only added.' : 'Everything Lumina has is already up to date.'}</p>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>
  );
}
