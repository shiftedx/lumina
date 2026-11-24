import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Users } from '../../luminaIcons';

import { getJellyfinImport, previewJellyfinHousehold, runJellyfinHousehold } from '../../api';
import type { JellyfinHousehold } from '../../types';
import { Button, Field, fieldProps, Input, PasswordInput } from '../../ui';
import { errorMessage } from '../../utils';
import { HouseholdImportDialog } from './HouseholdImportDialog';

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
      {preview ? (
        <HouseholdImportDialog
          busy={busy}
          chosen={chosen}
          done={result !== null}
          error={error}
          members={(result ?? preview).members.map(({ jellyfin_id, jellyfin_name, ...member }) => ({
            ...member, source_id: jellyfin_id, source_name: jellyfin_name,
          }))}
          onBringOver={() => { void bringOver(); }}
          onClose={close}
          onToggle={toggle}
          provider="Jellyfin"
        />
      ) : null}
    </form>
  );
}
