import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { History } from '../../luminaIcons';

import { getPlexImport, previewPlexImport, runPlexImport } from '../../api';
import type { JellyfinImportSummary, PlexImportStatus } from '../../types';
import { Button, Dialog, Field, fieldProps, PasswordInput } from '../../ui';
import { errorMessage } from '../../utils';
import { useSettingsHost } from './settingsHost';

type Count = 'watched' | 'in_progress' | 'up_to_date' | 'unmatched';
const COUNTS: Array<[Count, string]> = [['watched', 'Watched'], ['in_progress', 'In progress'], ['up_to_date', 'Already up to date'], ['unmatched', 'Not in Lumina']];

/** Privacy & data → Watch history from Plex. The token lives only in this component's state. */
export function PlexHistoryImport() {
  const { user } = useSettingsHost();
  const [status, setStatus] = useState<PlexImportStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [preview, setPreview] = useState<JellyfinImportSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const tokenRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    getPlexImport().then(
      (next) => { if (live) setStatus(next); },
      (failure: unknown) => { if (live) setLoadError(errorMessage(failure, 'Unable to load Plex import.')); },
    );
    return () => { live = false; };
  }, []);

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (!status) return <p className="g-setting-note" role="status">Loading…</p>;
  if (!status.server) return <p className="g-setting-note">{user.role === 'admin' ? 'Set the Plex server address under Media server first.' : 'Ask a vault owner to set the Plex server address.'}</p>;

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPreview(await previewPlexImport(token.trim()));
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
      const done = await runPlexImport(token.trim());
      setToken('');
      setNotice(`Imported from Plex: ${done.watched} watched, ${done.in_progress} in progress.`);
      closeDialog();
    } catch (failure) {
      setError(errorMessage(failure, 'The import could not run.'));
      setToken('');
      closeDialog();
    } finally {
      setBusy(false);
    }
  }

  function closeDialog() {
    setPreview(null);
    window.setTimeout(() => tokenRef.current?.focus(), 0);
  }

  function cancel() {
    setError(null);
    setToken('');
    closeDialog();
  }

  return (
    <form className="g-subrows" onSubmit={(event) => { void check(event); }}>
      <p className="g-setting-note">Plex server: {status.server}</p>
      <Field label="Plex token">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="off" maxLength={4096} onChange={(event) => setToken(event.target.value)} ref={tokenRef} required value={token} />}</Field>
      <p className="g-setting-note">Use a token for the Plex account whose history you want to bring over. Lumina uses it for this import and never saves it. <a href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/" rel="noreferrer" target="_blank">How to find your Plex token</a></p>
      <p className="g-setting-note">Favorites and watchlists are not transferred from Plex.</p>
      <div className="g-actions"><Button disabled={busy || !token.trim()} icon={<History />} type="submit">Preview</Button></div>
      {error && !preview ? <p className="auth-error" role="alert">{error}</p> : null}
      {notice ? <p className="g-setting-note" role="status">{notice}</p> : null}
      {preview ? createPortal(<ImportDialog busy={busy} error={error} onCancel={cancel} onImport={() => { void importHistory(); }} preview={preview} />, document.body) : null}
    </form>
  );
}

function ImportDialog({ preview, busy, error, onImport, onCancel }: {
  preview: JellyfinImportSummary; busy: boolean; error: string | null; onImport: () => void; onCancel: () => void;
}) {
  const changes = preview.watched + preview.in_progress;
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (!busy) onCancel(); }
  };
  return (
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog busy={busy} footer={<><Button busy={busy} disabled={!changes} onClick={onImport} variant="primary">{busy ? 'Importing…' : 'Import history'}</Button><Button disabled={busy} onClick={onCancel} variant="quiet">Cancel</Button></>} onClose={onCancel} open size="md" title="Import from Plex?">
        <ul aria-label="What would change" className="admin-counts">
          {COUNTS.map(([key, label]) => <li key={key}><span>{label}</span><strong className="g-tabular">{preview[key]}</strong></li>)}
        </ul>
        {preview.unmatched_names.length ? <details className="admin-panel"><summary>Not in Lumina ({preview.unmatched})</summary><ul className="g-list">{preview.unmatched_names.map((name, index) => <li className="g-list-row" key={index}>{name}</li>)}</ul></details> : null}
        <p>{changes ? 'Nothing you watched more recently in Lumina changes.' : 'Everything Lumina has is already up to date.'}</p>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>
  );
}
