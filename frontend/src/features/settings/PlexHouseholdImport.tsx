import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Users } from '../../luminaIcons';

import { getPlexImport, previewPlexHousehold, runPlexHousehold } from '../../api';
import type { JellyfinImportSummary, PlexHousehold, PlexHouseholdMember } from '../../types';
import { Button, Checkbox, Dialog, Field, fieldProps, PasswordInput } from '../../ui';
import { errorMessage } from '../../utils';
import { CopyableLink } from '../admin/AdminMembers';

const counts = (summary: JellyfinImportSummary) =>
  `${summary.watched} watched, ${summary.in_progress} in progress, ${summary.up_to_date} already up to date, ${summary.unmatched} not in Lumina`;

function landing(member: PlexHouseholdMember, done: boolean): string {
  if (member.action === 'skip') return `Skipped: ${member.reason ?? ''}`;
  if (done && member.error) return 'Not brought over';
  return member.action === 'create' ? `Create member ${member.lumina_username}` : `Import into ${member.lumina_username}`;
}

/** Members → Bring members over from Plex. The server owner's Plex token stays in component state only. */
export function PlexHouseholdImport() {
  const [server, setServer] = useState<string | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [preview, setPreview] = useState<PlexHousehold | null>(null);
  const [result, setResult] = useState<PlexHousehold | null>(null);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    getPlexImport().then(
      (next) => { if (live) setServer(next.server ?? null); },
      (failure: unknown) => { if (live) setLoadError(errorMessage(failure, 'Unable to load Plex import.')); },
    );
    return () => { live = false; };
  }, []);

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (server === undefined) return <p className="g-setting-note" role="status">Loading…</p>;
  if (!server) return <p className="g-setting-note">Set the Plex server address under Media server first.</p>;

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = await previewPlexHousehold(token.trim());
      setChosen(new Set(next.members.filter((member) => member.action !== 'skip' && !member.disabled && !member.error).map((member) => member.plex_id)));
      setPreview(next);
    } catch (failure) {
      setError(errorMessage(failure, 'The preview could not run.'));
    } finally {
      setBusy(false);
    }
  }

  async function bringOver() {
    setBusy(true);
    setError(null);
    try {
      setResult(await runPlexHousehold(token.trim(), [...chosen]));
      setToken('');
    } catch (failure) {
      setPreview(null);
      setToken('');
      setError(errorMessage(failure, 'Bringing members over could not run.'));
      window.setTimeout(() => tokenRef.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setChosen((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; });
  }

  function close() {
    setPreview(null);
    setResult(null);
    setError(null);
    setToken('');
    window.setTimeout(() => tokenRef.current?.focus(), 0);
  }

  return (
    <form className="g-subrows" onSubmit={(event) => { void check(event); }}>
      <p className="g-setting-note">Plex server: {server}</p>
      <Field label="Plex server owner token">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="off" maxLength={4096} onChange={(event) => setToken(event.target.value)} ref={tokenRef} required value={token} />}</Field>
      <p className="g-setting-note">Lumina uses the Plex server owner's token with plex.tv to find the owner and accessible shared users. It uses the token for this import and never saves it. Favorites and watchlists are not transferred.</p>
      <div className="g-actions"><Button disabled={busy || !token.trim()} icon={<Users />} type="submit">Preview</Button></div>
      {error && !preview ? <p className="auth-error" role="alert">{error}</p> : null}
      {preview ? createPortal(<HouseholdDialog busy={busy} chosen={chosen} error={error} onBringOver={() => { void bringOver(); }} onClose={close} onToggle={toggle} preview={preview} result={result} />, document.body) : null}
    </form>
  );
}

function HouseholdDialog({ preview, result, chosen, busy, error, onToggle, onBringOver, onClose }: {
  preview: PlexHousehold; result: PlexHousehold | null; chosen: ReadonlySet<string>; busy: boolean; error: string | null;
  onToggle: (id: string) => void; onBringOver: () => void; onClose: () => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (!busy) onClose(); }
  };
  return (
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog
        busy={busy}
        footer={result ? <Button onClick={onClose} variant="primary">Done</Button> : <><Button busy={busy} disabled={!chosen.size} onClick={onBringOver} variant="primary">{busy ? 'Bringing members over…' : 'Bring members over'}</Button><Button disabled={busy} onClick={onClose} variant="quiet">Cancel</Button></>}
        onClose={onClose}
        open
        size="md"
        title={result ? 'Members brought over' : 'Bring members over from Plex?'}
      >
        <ul aria-label="Plex users" className="g-list">
          {(result ?? preview).members.map((member) => (
            <li className="g-list-row" key={member.plex_id}>
              {result ? <strong>{member.plex_name}</strong> : <Checkbox checked={chosen.has(member.plex_id)} disabled={busy || member.disabled || member.action === 'skip' || Boolean(member.error)} label={`${member.plex_name}${member.disabled ? ' (disabled in Plex)' : ''}`} onChange={() => onToggle(member.plex_id)} />}
              <span> {landing(member, Boolean(result))}</span>
              {member.summary ? <small> · {counts(member.summary)}</small> : null}
              {member.error ? <p className="auth-error">{member.error}</p> : null}
              {member.reset_url ? <CopyableLink label={`Password link for ${member.lumina_username}`} url={member.reset_url} /> : null}
            </li>
          ))}
        </ul>
        <p>{result ? 'New members choose a password with their link before they can sign in. Each link works once, for 24 hours; Create reset link on their row makes a new one.' : 'New members join as household members without a password and get a one-time link to choose one. Nothing anyone watched more recently in Lumina changes.'}</p>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>
  );
}
