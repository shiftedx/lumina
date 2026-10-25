import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Play, RefreshCw } from '../../luminaIcons';

import { getMusicDiscography, getMusicEnrichment, getMusicHighlights, getMusicVideos, identifyMusic } from '../../api';
import { Artwork } from '../../Artwork';
import { formatDuration } from '../../utils';
import type { MusicTitleInfo, MusicTrack, MusicVideos } from './musicTypes';
import { setAlbumQueue } from '../gallery/albumQueue';
import { MusicReleaseRequest } from '../integrations/MusicRequestControls';
import './music.css';

const PERFORMANCE_LABEL = { official: 'Official upload', live: 'Live performance', cover: 'Cover', remix: 'Remix', performance: 'Performance' };

export function MusicVideoRail({ id, kind = 'title', onWatch }: { id: string; kind?: 'track' | 'title'; onWatch?: (url: string, itemId?: string) => void }) {
  const [answer, setAnswer] = useState<MusicVideos | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true, timer: ReturnType<typeof setTimeout> | undefined, polls = 0;
    setAnswer(null);
    const load = () => {
      void getMusicVideos(id, kind).then((value) => {
        if (!current) return;
        setAnswer(value);
        if (value.refreshing && polls++ < 18) timer = setTimeout(load, 5000);
      }, () => { if (current) setAnswer({ items: [], refreshing: false, error: 'Performances could not load.' }); });
    };
    load();
    return () => { current = false; clearTimeout(timer); };
  }, [id, kind, attempt]);
  return <section aria-labelledby={`music-film-${id}`} className="music-films">
    <header className="music-section-head"><h2 id={`music-film-${id}`}>On film</h2>{answer?.error ? <button className="g-text-button" onClick={() => setAttempt((n) => n + 1)} type="button">Try again</button> : null}</header>
    {!answer ? <div aria-label="Loading performances" className="music-film-skeleton" role="status" /> : answer.items.length ? <ul className="music-film-row" data-focus-row>
      {answer.items.map((video) => <li key={video.id}><a className="music-film" data-focus-item href={video.item_id ? `/watch/library/${encodeURIComponent(video.item_id)}` : video.url} onClick={onWatch ? (event) => { if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) { event.preventDefault(); onWatch(video.url, video.item_id ?? undefined); } } : undefined} rel="noreferrer noopener" target="_blank">
        <Artwork alt="" className="music-film-art" src={video.thumbnail_url} />
        <span className="music-film-type"><Play aria-hidden="true" />{PERFORMANCE_LABEL[video.performance]}</span>
        <span className="music-film-name">{video.title}</span><small>{video.uploader}{video.duration ? ` · ${formatDuration(video.duration)}` : ''}</small>
      </a></li>)}
    </ul> : <p className="music-note" role="status">{answer.error || (answer.refreshing ? 'Finding performances connected to your music…' : 'No confident matches yet. Your music stays ready to play.')}</p>}
  </section>;
}

export function MusicHighlights({ id, onPlay }: { id: string; onPlay: (id: string) => void }) {
  const [tracks, setTracks] = useState<MusicTrack[] | null>(null);
  useEffect(() => { let current = true; void getMusicHighlights(id).then((value) => { if (current) setTracks(value); }, () => { if (current) setTracks([]); }); return () => { current = false; }; }, [id]);
  if (!tracks?.length) return null;
  return <section aria-labelledby="music-owned-tracks" className="music-highlights"><header className="music-section-head"><h2 id="music-owned-tracks">In your rotation</h2><span>From your library</span></header><ol>
    {tracks.map((track, index) => <li key={track.id}><button className="music-highlight" data-focus-item onClick={() => { setAlbumQueue(id, tracks.map((entry) => ({ item_id: entry.id, name: entry.title, number: entry.number }))); onPlay(track.id); }} type="button"><span className="music-highlight-number">{index + 1}</span><span><strong>{track.title}</strong><small>{track.album}</small></span><span>{track.duration ? formatDuration(track.duration) : ''}</span><Play aria-hidden="true" /></button></li>)}
  </ol></section>;
}

export function MusicEnrichment({ id, canEdit = false, onRefreshed }: { id: string; canEdit?: boolean; onRefreshed?: () => void }) {
  const [answer, setAnswer] = useState<Awaited<ReturnType<typeof getMusicEnrichment>> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const refreshed = useRef(onRefreshed); refreshed.current = onRefreshed;
  useEffect(() => {
    let current = true, timer: ReturnType<typeof setTimeout> | undefined, polls = 0, wasRefreshing = false;
    const load = () => {
      void getMusicEnrichment(id).then((value) => {
        if (!current) return;
        setAnswer(value);
        if (wasRefreshing && !value.refreshing) refreshed.current?.();
        wasRefreshing = value.refreshing;
        if (value.refreshing && polls++ < 24) timer = setTimeout(load, 5000);
      }, () => undefined);
    };
    load();
    return () => { current = false; clearTimeout(timer); };
  }, [id, attempt]);
  async function choose(providerId?: string) {
    setBusy(true);
    try { await identifyMusic(id, providerId); setAttempt((n) => n + 1); }
    catch { setAnswer((current) => current ? { ...current, error: 'Identification could not start. Try again.' } : null); }
    finally { setBusy(false); }
  }
  if (!canEdit) return null;
  return <div className="music-enrichment">
    <button className="g-text-button" disabled={busy || answer?.refreshing} onClick={() => void choose()} type="button"><RefreshCw aria-hidden="true" />{answer?.refreshing ? 'Enriching details…' : answer?.identified ? 'Refresh music details' : 'Identify music'}</button>
    {answer?.error ? <p className="music-note" role="status">{answer.error}</p> : null}
    {answer?.candidates.length ? <details className="music-matches"><summary>Review {answer.candidates.length} possible matches</summary><p>Choose the correct release or artist. Your saved edits stay protected.</p><ul>{answer.candidates.map((candidate) => <li key={candidate.id}><span><strong>{candidate.name}</strong><small>{[candidate.artist, candidate.date, candidate.disambiguation].filter(Boolean).join(' · ')}</small></span><button className="g-text-button" disabled={busy} onClick={() => void choose(candidate.id)} type="button">Use this match</button></li>)}</ul></details> : null}
  </div>;
}

export function MusicDiscography({ id, artistId, artistName, music }: { id?: string; artistId?: string | null; artistName: string; music: MusicTitleInfo | null | undefined }) {
  const [extra, setExtra] = useState<MusicTitleInfo['discography']>([]);
  const [next, setNext] = useState<number | null>(music?.discography?.length ? 100 : 0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef(0);
  useEffect(() => { operation.current += 1; setExtra([]); setNext(music?.discography?.length ? 100 : 0); setLoading(false); setError(null); }, [id]);
  useEffect(() => () => { operation.current += 1; }, []);
  async function more() {
    if (!id || loading || next === null) return;
    const generation = ++operation.current, offset = next;
    setLoading(true); setError(null);
    try {
      let answer = await getMusicDiscography(id, offset);
      for (let poll = 0; answer.refreshing && poll < 20; poll++) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        if (generation !== operation.current) return;
        answer = await getMusicDiscography(id, offset);
      }
      if (generation !== operation.current) return;
      if (answer.error || answer.refreshing) throw new Error(answer.error || 'Discography is still loading. Try again shortly.');
      setExtra((current) => [...new Map([...current, ...answer.items].map((release) => [release.id, release])).values()]);
      setNext(answer.next_offset);
    } catch (problem) { if (generation === operation.current) setError(problem instanceof Error ? problem.message : 'Discography could not load.'); }
    finally { if (generation === operation.current) setLoading(false); }
  }
  const releases = [...new Map([...(music?.discography ?? []), ...extra].map((release) => [release.id, release])).values()].sort((a, b) => String(a.year).localeCompare(String(b.year)) || a.name.localeCompare(b.name));
  if (!releases.length && !music?.identified) return null;
  return <section aria-labelledby="music-discography" className="music-discography"><header className="music-section-head"><h2 id="music-discography">The complete picture</h2><span>Discography · MusicBrainz</span></header><ul>{releases.map((release) => <li key={release.id}><span className="music-discography-year">{release.year || '—'}</span><span>{release.name}<small>{release.type}</small></span><span className="music-discography-actions"><MusicReleaseRequest artistId={artistId} artistName={artistName} release={release} /><a aria-label={`Explore ${release.name} on MusicBrainz`} className="g-icon-button" href={`https://musicbrainz.org/release-group/${encodeURIComponent(release.id)}`} rel="noreferrer noopener" target="_blank"><ExternalLink aria-hidden="true" /></a></span></li>)}</ul>{id && next !== null ? <button className="g-text-button" disabled={loading} onClick={() => void more()} type="button">{loading ? 'Loading releases…' : releases.length ? 'Explore more releases' : 'Explore discography'}</button> : null}{error ? <p className="music-note" role="status">{error}</p> : null}</section>;
}
