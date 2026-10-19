import { useEffect, useState } from 'react';
import { del, post, put, requestJson } from '../../api';
import { Button, ButtonLink, Field, fieldProps, Input } from '../../ui';
import { refreshListeningExport } from '../music/musicListeningExport';
import { useSettingsHost } from './settingsHost';

type Connection = { provider: 'lastfm' | 'listenbrainz'; enabled: boolean; username: string | null; credentials_set: boolean; authorization_pending: boolean };
type Event = { id: string; started_at: number; deliveries: Record<string, { status: string; error?: string | null }> };
const label = (provider: string) => provider === 'lastfm' ? 'Last.fm' : 'ListenBrainz';

export function ListeningExportRows() {
  const { user } = useSettingsHost();
  return <ListeningExportSettings key={user.id} owner={user.id} />;
}

function ListeningExportSettings({ owner }: { owner: string }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [page, setPage] = useState(1);
  const [token, setToken] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [secret, setSecret] = useState('');
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    setError(null);
    void Promise.all([requestJson<{ items: Connection[] }>('/api/me/listening-export'), requestJson<{ items: Event[] }>(`/api/me/listening-events?page=${page}`)])
      .then(([accounts, history]) => { if (current) { setConnections(accounts.items); setEvents(history.items); } })
      .catch(() => { if (current) setError('Listening history settings could not load.'); });
    return () => { current = false; };
  }, [owner, page]);
  async function act(work: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try {
      await work();
      const result = await requestJson<{ items: Connection[] }>('/api/me/listening-export');
      setConnections(result.items);
      await refreshListeningExport(owner);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The connection could not be updated.'); }
    finally { setBusy(false); }
  }
  return <div className="g-subrows g-listening-export">
    <p className="g-setting-note">Connect an account to export browser music listens. Tracks count after 30 seconds and half the track or four minutes. Seeking and previews do not count.</p>
    {error ? <p className="g-warning" role="alert">{error}</p> : null}
    <fieldset className="g-fieldset"><legend>ListenBrainz</legend>
      <p className="g-setting-note">{connections.find((row) => row.provider === 'listenbrainz')?.enabled ? `Connected as ${connections.find((row) => row.provider === 'listenbrainz')?.username}` : 'Not connected'}</p>
      <form onSubmit={(event) => { event.preventDefault(); void act(async () => { await put('/api/me/listening-export/listenbrainz', { token }); setToken(''); }); }}>
        <Field label="ListenBrainz user token">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={256} onChange={(event) => setToken(event.target.value)} required type="password" value={token} />}</Field>
        <div className="g-actions"><Button disabled={busy || !token.trim()} type="submit">Connect ListenBrainz</Button><ButtonLink href="https://listenbrainz.org/profile/" rel="noopener noreferrer" target="_blank" variant="secondary">Find my token</ButtonLink></div>
      </form>
      {connections.find((row) => row.provider === 'listenbrainz')?.credentials_set ? <Button disabled={busy} onClick={() => { void act(() => del('/api/me/listening-export/listenbrainz')); }} variant="secondary">Disconnect ListenBrainz</Button> : null}
    </fieldset>
    <fieldset className="g-fieldset"><legend>Last.fm</legend>
      <p className="g-setting-note">{connections.find((row) => row.provider === 'lastfm')?.enabled ? `Connected as ${connections.find((row) => row.provider === 'lastfm')?.username}` : 'Not connected'}</p>
      <form onSubmit={(event) => { event.preventDefault(); void act(async () => { const result = await post<{ authorization_url: string }>('/api/me/listening-export/lastfm/authorize', { api_key: apiKey, api_secret: secret }); setAuthUrl(result.authorization_url); setApiKey(''); setSecret(''); }); }}>
        <Field label="Last.fm API key">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={32} minLength={32} onChange={(event) => setApiKey(event.target.value)} pattern="[a-fA-F0-9]{32}" required type="password" value={apiKey} />}</Field>
        <Field label="Last.fm API secret">{(ids) => <Input {...fieldProps(ids)} autoComplete="off" maxLength={32} minLength={32} onChange={(event) => setSecret(event.target.value)} pattern="[a-fA-F0-9]{32}" required type="password" value={secret} />}</Field>
        <div className="g-actions"><Button disabled={busy || apiKey.length !== 32 || secret.length !== 32} type="submit">Start Last.fm authorization</Button><ButtonLink href="https://www.last.fm/api/account/create" rel="noopener noreferrer" target="_blank" variant="secondary">Create an API account</ButtonLink></div>
      </form>
      {authUrl ? <div className="g-actions"><ButtonLink href={authUrl} rel="noopener noreferrer" target="_blank">Approve in Last.fm</ButtonLink></div> : null}
      {connections.find((row) => row.provider === 'lastfm')?.authorization_pending ? <Button disabled={busy} onClick={() => { void act(async () => { await post('/api/me/listening-export/lastfm/finish'); setAuthUrl(null); }); }}>Finish Last.fm connection</Button> : null}
      {connections.find((row) => row.provider === 'lastfm')?.credentials_set ? <Button disabled={busy} onClick={() => { void act(async () => { await del('/api/me/listening-export/lastfm'); setAuthUrl(null); }); }} variant="secondary">Disconnect Last.fm</Button> : null}
    </fieldset>
    <div className="g-subrow"><div className="g-list-row-copy"><strong>Recent export outcomes</strong><p className="g-setting-note">Unknown outcomes are checked against provider history and stay uncertain when no match can be proven. They are never resent automatically.</p></div></div>
    {!events.length ? <p className="g-setting-note">No listening exports yet.</p> : events.map((event) => <div className="g-subrow" key={event.id}><div className="g-list-row-copy"><strong>{new Date(event.started_at * 1000).toLocaleString()}</strong>{Object.entries(event.deliveries).map(([provider, outcome]) => <p className="g-setting-note" key={provider}>{label(provider)}: {outcome.status}{outcome.error ? ` · ${outcome.error}` : ''}</p>)}</div>{Object.values(event.deliveries).some((entry) => entry.status === 'uncertain') ? <Button disabled={busy} onClick={() => { void act(async () => { const updated = await post<Event>(`/api/me/listening-events/${event.id}/reconcile`); setEvents((rows) => rows.map((row) => row.id === updated.id ? updated : row)); }); }} variant="secondary">Check provider history</Button> : null}</div>)}
    {page > 1 || events.length === 50 ? <div className="g-actions"><Button disabled={busy || page === 1} onClick={() => setPage((value) => value - 1)} variant="secondary">Previous</Button><span>Page {page}</span><Button disabled={busy || events.length < 50} onClick={() => setPage((value) => value + 1)} variant="secondary">Next</Button></div> : null}
  </div>;
}
