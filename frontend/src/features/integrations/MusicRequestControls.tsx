import { Check, Music2, RefreshCw } from '../../luminaIcons';
import { useEffect, useState } from 'react';

import { errorMessage, formatDateTime } from '../../utils';
import { Button, EmptyState, ErrorState, Select, StatusText } from '../../ui';
import { enqueueMusicRequest, musicSession } from '../music/musicSession';
import {
  type MusicRequest, type MusicRequestStatus, type MusicRequestTarget, createMusicRequest, approveMusicRequest, getAdminMusicRequests, getMusicRequestTargets, reconcileMusicRequest,
} from './integrationsApi';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const id = () => crypto.randomUUID();
const statusTone = (status: MusicRequestStatus) => status === 'failed' ? 'danger' : status === 'uncertain' ? 'attention' : status === 'awaiting_import' || status === 'succeeded' ? 'ok' : 'muted';
const statusCopy: Record<MusicRequestStatus, string> = {
  pending_approval: 'Waiting for approval', dispatching: 'Sending to Lidarr', awaiting_import: 'Waiting for import', succeeded: 'Complete', failed: 'Failed', uncertain: 'Needs reconciliation',
};

/** A release group is requestable only with the artist's verified MusicBrainz UUID. */
export function MusicReleaseRequest({ artistId, artistName, release }: { artistId: string | null | undefined; artistName: string; release: { id: string; name: string } }) {
  const [targets, setTargets] = useState<MusicRequestTarget[] | null>(null);
  const [target, setTarget] = useState('');
  const [request, setRequest] = useState<MusicRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);
  const valid = Boolean(artistId && UUID.test(artistId) && UUID.test(release.id));
  useEffect(() => {
    if (!valid) { setTargets([]); return; }
    let active = true;
    getMusicRequestTargets().then((answer) => { if (active) { const items = Array.isArray(answer.items) ? answer.items : []; setTargets(items); setTarget((current) => current && items.some((item) => item.id === current) ? current : items[0]?.id ?? ''); } }, () => { if (active) setTargets([]); });
    return () => { active = false; };
  }, [artistId, release.id, valid]);
  async function requestAlbum() {
    if (!artistId || !target || busy) return;
    setBusy(true); setError(null);
    try { setRequest(await createMusicRequest({ id: id(), connector_id: target, foreign_artist_id: artistId, foreign_album_id: release.id, artist_name: artistName, album_name: release.name })); }
    catch (cause) { setError(errorMessage(cause, 'Lumina could not submit this album request.')); }
    finally { setBusy(false); }
  }
  async function addToQueue() {
    const owner = musicSession().owner;
    if (!request || !owner || !UUID.test(request.id) || busy) return;
    setBusy(true); setError(null);
    try { await enqueueMusicRequest(owner, request.id); setQueued(true); }
    catch (cause) { setError(errorMessage(cause, 'Lumina could not add this request to your queue.')); }
    finally { setBusy(false); }
  }
  if (!valid || targets === null || !targets.length) return null;
  if (request) {
    const canQueue = Boolean(musicSession().owner && UUID.test(request.id));
    return <span className="music-release-request"><StatusText tone={statusTone(request.status)}>{statusCopy[request.status]}</StatusText>{canQueue && !queued ? <Button busy={busy} onClick={() => { void addToQueue(); }} variant="quiet">Add request to queue</Button> : null}{queued ? <small>Added to your queue.</small> : null}{request.error ? <small>{request.error}</small> : null}{error ? <small role="alert">{error}</small> : null}</span>;
  }
  return <span className="music-release-request"><Select aria-label={`Request destination for ${release.name}`} onChange={(event) => setTarget(event.currentTarget.value)} value={target}>{targets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</Select><Button busy={busy} icon={<Music2 />} onClick={() => { void requestAlbum(); }} variant="quiet">Request album</Button>{error ? <small role="alert">{error}</small> : null}</span>;
}

export function MusicRequestOperations() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Awaited<ReturnType<typeof getAdminMusicRequests>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = async () => {
    setError(null);
    try {
      const answer = await getAdminMusicRequests({ page });
      setData(Array.isArray(answer.items) ? answer : { items: [], page: 1, page_size: 25, total: 0, total_pages: 1 });
    } catch (cause) { setError(errorMessage(cause, 'Unable to load music requests.')); }
  };
  useEffect(() => { void load(); }, [page]);
  async function update(request: MusicRequest, action: 'approve' | 'reconcile') {
    setBusy(request.id); setError(null);
    try { const next = action === 'approve' ? await approveMusicRequest(request.id) : await reconcileMusicRequest(request.id); setData((current) => current ? { ...current, items: current.items.map((item) => item.id === next.id ? next : item) } : current); }
    catch (cause) { setError(errorMessage(cause, `Unable to ${action} this music request.`)); }
    finally { setBusy(null); }
  }
  return <section aria-labelledby="music-request-operations" className="music-request-operations"><header><div><h3 id="music-request-operations">Music requests</h3><p>Members choose an album; approval is the only action that can send it to Lidarr.</p></div><Button icon={<RefreshCw />} onClick={() => { void load(); }} variant="quiet">Refresh requests</Button></header>{error ? <ErrorState body={error} onRetry={() => { void load(); }} title="Lumina could not load music requests" /> : !data ? <p className="admin-note" role="status">Loading music requests…</p> : data.items.length ? <><ul className="music-request-list">{data.items.map((request) => <li key={request.id}><div><strong>{request.album_name}</strong><small>{request.artist_name} · {formatDateTime(request.created_at)}</small></div><div><StatusText tone={statusTone(request.status)}>{statusCopy[request.status]}</StatusText>{request.error ? <small>{request.error}</small> : null}{request.status === 'pending_approval' ? <Button busy={busy === request.id} icon={<Check />} onClick={() => { void update(request, 'approve'); }} variant="quiet">Approve request</Button> : null}{request.status === 'dispatching' || request.status === 'uncertain' ? <Button busy={busy === request.id} onClick={() => { void update(request, 'reconcile'); }} variant="quiet">Reconcile request</Button> : null}</div></li>)}</ul><MusicPager page={data.page} pages={data.total_pages} onPage={setPage} /></> : <EmptyState body="Member album requests will appear here for deliberate approval." title="No music requests" />}</section>;
}

function MusicPager({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  if (pages <= 1) return null;
  return <nav aria-label="Music request page" className="arr-pager"><span>Page {page} of {pages}</span><Button disabled={page <= 1} onClick={() => onPage(page - 1)} variant="quiet">Previous</Button><Button disabled={page >= pages} onClick={() => onPage(page + 1)} variant="quiet">Next</Button></nav>;
}
