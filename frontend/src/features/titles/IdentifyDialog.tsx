import { type FormEvent, useEffect, useState } from 'react';
import { Film } from 'lucide-react';

import { ApiRequestError, identifyTitle, refreshTitleMetadata, searchTitleMatches, unmatchTitle } from '../../api';
import type { IdentifyCandidate, TitleSummary } from '../../types';
import { Artwork } from '../../Artwork';
import { errorMessage } from '../../utils';
import { Button, Dialog, Field, fieldProps, Input } from '../../ui';
import './titles.css';

// the backend answers TMDB-not-configured with a raw 409 detail code
// ("tmdb_not_configured"); show the spec's plain-English copy instead.
const describeFailure = (failure: unknown, fallback: string): string =>
  failure instanceof ApiRequestError && failure.status === 409
    ? 'TMDB is not set up. Add a key in Settings › Media server.'
    : errorMessage(failure, fallback);

/** Admin "Fix match": a native modal dialog over TMDB candidates. Member and NFO edits are never overwritten. */
export function IdentifyDialog({ title, onClose, onChanged }: {
  title: Pick<TitleSummary, 'id' | 'name' | 'year' | 'type'>;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const [name, setName] = useState(title.name);
  const [year, setYear] = useState(title.year ? String(title.year) : '');
  const [results, setResults] = useState<IdentifyCandidate[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { void search(); }, []); // eslint-disable-line react-hooks/exhaustive-deps -- search once with the prefilled name

  async function search(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      setResults(await searchTitleMatches(title.id, name.trim(), year ? Number(year) : null));
    } catch (failure) {
      setResults([]);
      setStatus(describeFailure(failure, 'TMDB could not be searched. Check the key in Media server settings.'));
    } finally {
      setBusy(false);
    }
  }

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setStatus(null);
    try {
      await action();
      onChanged(done);
      onClose();
    } catch (failure) {
      setStatus(describeFailure(failure, 'That did not work. Try again.'));
      setBusy(false);
    }
  }

  const kind = title.type === 'movie' || title.type === 'boxset' ? 'movie' : 'show';
  return (
    <Dialog
      footer={<>
        <Button disabled={busy} onClick={() => void run(() => refreshTitleMetadata(title.id), 'Refreshing details in the background.')}>Refresh</Button>
        <Button disabled={busy} onClick={() => void run(() => unmatchTitle(title.id), 'Match removed. Lumina now uses the file and NFO details only.')}>Unmatch</Button>
        <Button onClick={onClose} variant="quiet">Close</Button>
      </>}
      onClose={onClose}
      open
      size="md"
      title="Fix match"
    >
      <p>Choose the right {kind} for <strong>{title.name}</strong>. Your own edits and NFO details are kept.</p>
      <form className="identify-search" onSubmit={(event) => void search(event)} role="search">
        <Field label="Name">{(ids) => <Input {...fieldProps(ids)} maxLength={200} onChange={(event) => setName(event.target.value)} required value={name} />}</Field>
        <Field label="Year">{(ids) => <Input {...fieldProps(ids)} inputMode="numeric" max={2100} min={1870} onChange={(event) => setYear(event.target.value)} type="number" value={year} />}</Field>
        <Button disabled={busy || !name.trim()} type="submit">Search</Button>
      </form>
      <div aria-busy={busy} aria-live="polite">
        {results === null ? <p role="status">Searching TMDB…</p> : results.length ? (
          <ul className="identify-results">
            {results.map((candidate) => {
              const label = `${candidate.name}${candidate.year ? ` (${candidate.year})` : ''}`;
              return (
                <li key={candidate.tmdb_id}>
                  <Artwork alt="" className="identify-poster" fallback={<span aria-hidden="true"><Film /></span>} src={candidate.poster_url} />
                  <div>
                    <strong>{label}</strong>
                    {candidate.original_name && candidate.original_name !== candidate.name ? <small>{candidate.original_name}</small> : null}
                    {candidate.overview ? <p>{candidate.overview}</p> : null}
                  </div>
                  <Button aria-label={`Choose ${label}`} disabled={busy} onClick={() => void run(() => identifyTitle(title.id, candidate.tmdb_id), `Matched to ${candidate.name}. Details and artwork update in a moment.`)} variant="primary">Choose</Button>
                </li>
              );
            })}
          </ul>
        ) : status ? null : <p>No matches. Try another name or year.</p>}
      </div>
      {status ? <p className="auth-error" role="alert">{status}</p> : null}
    </Dialog>
  );
}
