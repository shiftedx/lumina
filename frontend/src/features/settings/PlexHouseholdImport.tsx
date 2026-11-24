import { type FormEvent, useEffect, useRef, useState } from 'react';
import { Users } from '../../luminaIcons';

import { getPlexImport, previewPlexHousehold, runPlexHousehold } from '../../api';
import type { JellyfinImportSummary, PlexHousehold, PlexHouseholdMember } from '../../types';
import { Button, Field, fieldProps, PasswordInput } from '../../ui';
import { errorMessage } from '../../utils';
import { HouseholdImportDialog } from './HouseholdImportDialog';

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
      {preview ? (
        <HouseholdImportDialog
          busy={busy}
          chosen={chosen}
          done={result !== null}
          error={error}
          members={(result ?? preview).members.map(({ plex_id, plex_name, ...member }) => ({
            ...member, source_id: plex_id, source_name: plex_name,
          }))}
          onBringOver={() => { void bringOver(); }}
          onClose={close}
          onToggle={toggle}
          provider="Plex"
        />
      ) : null}
    </form>
  );
}
