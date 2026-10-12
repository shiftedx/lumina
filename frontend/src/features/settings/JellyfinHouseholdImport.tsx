import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Users } from 'lucide-react';

import { getJellyfinImport, previewJellyfinHousehold, runJellyfinHousehold } from '../../api';
import type { JellyfinHousehold, JellyfinHouseholdMember, JellyfinImportSummary } from '../../types';
import { Button, Checkbox, Dialog, Field, fieldProps, Input, PasswordInput } from '../../ui';
import { errorMessage } from '../../utils';
import { CopyableLink } from '../admin/AdminMembers';

const counts = (summary: JellyfinImportSummary) =>
  `${summary.watched} watched, ${summary.in_progress} in progress, ${summary.favorites} new favorites, ${summary.up_to_date} already up to date, ${summary.unmatched} not in Lumina`;

function landing(member: JellyfinHouseholdMember, done: boolean): string {
  if (member.action === 'skip') return `Skipped: ${member.reason ?? ''}`;
  if (done && member.error) return 'Not brought over';
  return member.action === 'create' ? `Create member ${member.lumina_username}` : `Import into ${member.lumina_username}`;
}

/** Members → Bring members over from Jellyfin. The Jellyfin administrator password lives only in this component's state. */
export function JellyfinHouseholdImport() {
  const [server, setServer] = useState<string | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [preview, setPreview] = useState<JellyfinHousehold | null>(null);
  const [result, setResult] = useState<JellyfinHousehold | null>(null);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previewRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let live = true;
    getJellyfinImport().then(
      (next) => { if (live) setServer(next.server ?? null); },
      (failure: unknown) => { if (live) setLoadError(errorMessage(failure, 'Unable to load Jellyfin import.')); },
    );
    return () => { live = false; };
  }, []);

  if (loadError) return <p className="auth-error" role="alert">{loadError}</p>;
  if (server === undefined) return <p className="g-setting-note" role="status">Loading…</p>;
  if (!server) return <p className="g-setting-note">Set the Jellyfin server address under Media server first.</p>;

  async function check(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const next = await previewJellyfinHousehold(username.trim(), password);
      // Disabled Jellyfin users and unreadable histories start unticked; skipped users cannot be ticked.
      setChosen(new Set(next.members.filter((member) => member.action !== 'skip' && !member.disabled && !member.error).map((member) => member.jellyfin_id)));
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
      setResult(await runJellyfinHousehold(username.trim(), password, [...chosen]));
      setPassword('');
    } catch (failure) {
      // Back to the form with the password kept, so Preview can run again.
      setPreview(null);
      setError(errorMessage(failure, 'Bringing members over could not run.'));
      window.setTimeout(() => previewRef.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  }

  function toggle(id: string) {
    setChosen((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; });
  }

  // After the dialog unmounts; a modal dialog blocks focus outside it while open.
  function close() {
    setPreview(null);
    setResult(null);
    setError(null);
    setPassword('');
    window.setTimeout(() => previewRef.current?.focus(), 0);
  }

  return (
    <form className="g-subrows" onSubmit={(event) => { void check(event); }}>
      <p className="g-setting-note">Jellyfin server: {server}</p>
      <Field label="Jellyfin administrator username">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={256} onChange={(event) => setUsername(event.target.value)} required spellCheck={false} value={username} />}</Field>
      <Field label="Jellyfin administrator password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" maxLength={1024} onChange={(event) => setPassword(event.target.value)} value={password} />}</Field>
      <div className="g-actions"><Button disabled={busy || !username.trim()} icon={<Users />} ref={previewRef} type="submit">Preview</Button></div>
      {error && !preview ? <p className="auth-error" role="alert">{error}</p> : null}
      {/* Portal: a modal belongs in the top layer, outside the settings pane. */}
      {preview ? createPortal(<HouseholdDialog busy={busy} chosen={chosen} error={error} onBringOver={() => { void bringOver(); }} onClose={close} onToggle={toggle} preview={preview} result={result} />, document.body) : null}
    </form>
  );
}

function HouseholdDialog({ preview, result, chosen, busy, error, onToggle, onBringOver, onClose }: {
  preview: JellyfinHousehold; result: JellyfinHousehold | null; chosen: ReadonlySet<string>; busy: boolean; error: string | null;
  onToggle: (id: string) => void; onBringOver: () => void; onClose: () => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (!busy) onClose(); }
  };
  // The TV Back key reaches the footer and header buttons too, so the handler wraps the whole Dialog (React bubbles through the portal).
  return (
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog
        busy={busy}
        footer={result ? <Button onClick={onClose} variant="primary">Done</Button> : (
          <>
            <Button busy={busy} disabled={!chosen.size} onClick={onBringOver} variant="primary">{busy ? 'Bringing members over…' : 'Bring members over'}</Button>
            <Button disabled={busy} onClick={onClose} variant="quiet">Cancel</Button>
          </>
        )}
        onClose={onClose}
        open
        size="md"
        title={result ? 'Members brought over' : 'Bring members over from Jellyfin?'}
      >
          <ul aria-label="Jellyfin users" className="g-list">
            {(result ?? preview).members.map((member) => (
              <li className="g-list-row" key={member.jellyfin_id}>
                {result ? <strong>{member.jellyfin_name}</strong> : (
                  <Checkbox checked={chosen.has(member.jellyfin_id)} disabled={busy || member.action === 'skip'} label={`${member.jellyfin_name}${member.disabled ? ' (disabled in Jellyfin)' : ''}`} onChange={() => onToggle(member.jellyfin_id)} />
                )}
                <span> {landing(member, Boolean(result))}</span>
                {member.summary ? <small> · {counts(member.summary)}</small> : null}
                {member.error ? <p className="auth-error">{member.error}</p> : null}
                {member.reset_url ? <CopyableLink label={`Password link for ${member.lumina_username}`} url={member.reset_url} /> : null}
              </li>
            ))}
          </ul>
          <p>{result
            ? 'New members choose a password with their link before they can sign in. Each link works once, for 24 hours; Create reset link on their row makes a new one.'
            : 'New members join as household members without a password and get a one-time link to choose one. Nothing anyone watched more recently in Lumina changes.'}</p>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>
  );
}
