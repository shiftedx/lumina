import { ExternalLink, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { getMusicEditions, identifyMusic } from '../../api';
import type { TitleSummary } from '../../types';
import './trackEditor.css';

type Edition = { id: string; name: string; date: string; country: string | null; edition: string | null; label: string | null };
type EditionPage = { owned: TitleSummary[]; items: Edition[]; next_offset: number | null; refreshing: boolean; error: string | null };
const POLL_LIMIT = 20;
const POLL_DELAY = 3000;
const message = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;

export interface MusicEditionsProps {
  albumId: string;
  identified: boolean;
  canEdit?: boolean;
  onOpenTitle: (title: TitleSummary) => void;
  onRefreshed?: () => void;
}

export function MusicEditions({ albumId, identified, canEdit = false, onOpenTitle, onRefreshed }: MusicEditionsProps) {
  const [owned, setOwned] = useState<TitleSummary[] | null>(null);
  const [editions, setEditions] = useState<Edition[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Edition | null>(null);
  const [choosing, setChoosing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const operation = useRef(0);
  const activeAlbum = useRef(albumId);
  activeAlbum.current = albumId;

  const load = async (offset: number, replace: boolean, refreshDetail = false) => {
    const current = ++operation.current;
    if (replace) { setOwned(null); setEditions([]); setNext(null); }
    else setLoadingMore(true);
    setError(null);
    try {
      let page: EditionPage | null = null;
      for (let poll = 0; poll < POLL_LIMIT; poll++) {
        page = await getMusicEditions(albumId, offset);
        if (current !== operation.current) return;
        if (!page.refreshing) break;
        if (poll === POLL_LIMIT - 1) throw new Error('Edition details are still being prepared. Try again shortly.');
        await new Promise((resolve) => setTimeout(resolve, POLL_DELAY));
        if (current !== operation.current) return;
      }
      if (!page) return;
      if (page.error) throw new Error(page.error);
      setOwned((existing) => replace ? page.owned : [...new Map([...(existing ?? []), ...page.owned].map((item) => [item.id, item])).values()]);
      setEditions((existing) => replace ? page.items : [...new Map([...existing, ...page.items].map((item) => [item.id, item])).values()]);
      setNext(page.next_offset);
      if (refreshDetail) onRefreshed?.();
    } catch (problem) {
      if (current === operation.current) setError(message(problem, 'Editions could not be loaded.'));
    } finally {
      if (current === operation.current) setLoadingMore(false);
    }
  };

  useEffect(() => {
    operation.current += 1; setSelected(null); setNotice(null); setChoosing(false);
    void load(0, true);
    return () => { operation.current += 1; };
  }, [albumId]);

  async function choose() {
    if (!selected || choosing) return;
    const id = selected.id;
    setChoosing(true); setNotice(null); setError(null);
    try {
      await identifyMusic(albumId, id);
      if (activeAlbum.current !== albumId) return;
      setNotice('Metadata update queued. Lumina is checking this edition now.');
      await load(0, true, true);
      setSelected(null);
    } catch (problem) { setError(message(problem, 'This edition could not be applied.')); }
    finally { setChoosing(false); }
  }

  return <section aria-labelledby="album-editions" className="soundstage-album-section music-editions">
    <header className="music-section-head"><h2 id="album-editions">Editions &amp; releases</h2><span>Library &amp; MusicBrainz</span></header>
    {owned === null ? <p aria-live="polite" className="music-note" role="status">Loading available editions…</p> : <>
      {owned.length ? <div className="music-editions-owned"><h3>In your library</h3><ul>{owned.map((album) => <li key={album.id}><button onClick={() => onOpenTitle(album)} type="button"><span><strong>{album.name}</strong><small>{[album.year, album.artist_name].filter(Boolean).join(' · ')}</small></span><span>Open album</span></button></li>)}</ul></div> : null}
      {identified ? editions.length ? <div className="music-editions-public"><h3>Other known releases</h3><ul>{editions.map((edition) => <li className={selected?.id === edition.id ? 'is-selected' : ''} key={edition.id}><div><strong>{edition.name}</strong><span>{[edition.date, edition.country, edition.edition, edition.label].filter(Boolean).join(' · ') || 'Release details unavailable'}</span></div><a aria-label={`Open ${edition.name} release on MusicBrainz`} className="g-icon-button" href={`https://musicbrainz.org/release/${encodeURIComponent(edition.id)}`} rel="noreferrer noopener" target="_blank"><ExternalLink aria-hidden="true" /></a>{canEdit ? <button aria-pressed={selected?.id === edition.id} className="g-text-button" onClick={() => setSelected(edition)} type="button">Select edition</button> : null}</li>)}</ul></div> : !owned.length && !error ? <p className="music-note">No other editions are known for this release yet.</p> : null : !owned.length && !error ? <p className="music-note">This album is not matched to a MusicBrainz release group yet. Library versions can still appear here, while public editions wait for a confident match.</p> : null}
      {selected && canEdit ? <div className="music-editions-confirm" role="group" aria-label="Confirm edition metadata"><p><strong>{selected.name}</strong></p><p>Use this release’s metadata for this album? Your protected manual edits stay protected.</p><button className="g-button g-button-text" disabled={choosing} onClick={() => void choose()} type="button">{choosing ? 'Applying edition…' : 'Use this edition’s metadata'}</button><button className="g-text-button" disabled={choosing} onClick={() => setSelected(null)} type="button">Cancel</button></div> : null}
      {next !== null ? <button className="g-text-button music-editions-more" disabled={loadingMore} onClick={() => void load(next, false)} type="button">{loadingMore ? 'Loading more editions…' : 'Load more editions'}</button> : null}
    </>}
    {notice ? <p className="music-note" role="status">{notice}</p> : null}
    {error ? <div className="music-editions-error" role="alert"><p>{error}</p><button className="g-text-button" disabled={loadingMore || choosing} onClick={() => void load(0, true)} type="button"><RefreshCw aria-hidden="true" /> Try again</button></div> : null}
  </section>;
}

export { POLL_LIMIT };
