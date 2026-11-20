import { useCallback, useEffect, useRef, useState } from 'react';

import { Artwork } from '../../Artwork';
import { type MusicHome as MusicHomeData, type MusicHomeLayout, type MusicHomeShelf, getMusicHome, saveMusicHomeLayout } from '../../api';
import type { TitleSummary } from '../../types';
import { formatDuration } from '../../utils';
import { ChevronDown, ChevronUp, Download, ListMusic, Play, RefreshCw } from '../../luminaIcons';
import { Button, TextButton } from '../../ui';
import { musicSession, restoreMusicSharedSession, toggleMusic } from './musicSession';
import { offlineTracks } from './musicOffline';
import type { MusicPlaylist } from './musicTypes';

const SHELVES: ReadonlyArray<{ id: MusicHomeShelf; title: string }> = [
  { id: 'resume', title: 'Resume' },
  { id: 'recent', title: 'Recently played' },
  { id: 'playlists', title: 'Playlists' },
  { id: 'downloads', title: 'Downloads' },
  { id: 'mixes', title: 'Mixes' },
];
const DEFAULT_LAYOUT: MusicHomeLayout = { order: SHELVES.map(({ id }) => id), hidden: [] };

type Props = {
  owner: string;
  onBrowse: () => void;
  onResumeIntent?: () => void;
  onOpenTitle: (title: TitleSummary) => void;
  onOpenPlaylists: () => void;
};

const shelfName = (id: MusicHomeShelf) => SHELVES.find((shelf) => shelf.id === id)?.title ?? id;
const errorMessage = (error: unknown) => error instanceof Error && error.message ? error.message : 'Music Home could not be loaded.';

function canonical(layout: MusicHomeLayout): MusicHomeLayout {
  const known = new Set<MusicHomeShelf>();
  const order = [...layout.order, ...DEFAULT_LAYOUT.order].filter((id): id is MusicHomeShelf => SHELVES.some((shelf) => shelf.id === id) && !known.has(id) && Boolean(known.add(id)));
  return { order, hidden: layout.hidden.filter((id): id is MusicHomeShelf => known.has(id)) };
}

function ResumeShelf({ home, owner, onRefresh, onResumeIntent }: { home: MusicHomeData; owner: string; onRefresh: () => void; onResumeIntent?: () => void }) {
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const resume = home.resume;
  if (!resume) return null;
  if (resume.state === 'waiting') return <section aria-labelledby="music-home-resume" className="music-home-shelf"><header><h2 id="music-home-resume">Resume</h2></header><p className="music-home-empty">Waiting for import{resume.track.title ? `: ${resume.track.title}` : ''}.</p></section>;
  const track = resume.track;
  const label = `Resume ${track.title || 'saved queue'}`;
  const matches = (candidate = musicSession()) => candidate.owner === owner
    && candidate.sharedRevision === resume.revision
    && !candidate.sharedConflict
    && candidate.playback.index === resume.current_index
    && candidate.queue[resume.current_index ?? -1]?.id === track.id;
  const start = async () => {
    setStarting(true); setProblem(null);
    try {
      let current = musicSession();
      if (current.sharedConflict) throw new Error('Resolve the queue conflict in the player before resuming.');
      if (current.playback.playing) throw new Error('Your current music is already playing. Use the player to change queues.');
      if (!matches(current) && current.queue.length) { setNeedsRefresh(true); throw new Error('Your queue changed since Music Home loaded.'); }
      if (!matches(current)) {
        // Boot restoration normally makes this unnecessary. It is only safe from an empty, idle client.
        await restoreMusicSharedSession(owner);
        current = musicSession();
      }
      if (!matches(current) || current.sharedConflict || current.playback.playing) { setNeedsRefresh(true); throw new Error('Your saved queue changed.'); }
      onResumeIntent?.();
      await toggleMusic();
    } catch (error) { setProblem(errorMessage(error)); }
    finally { setStarting(false); }
  };
  return <section aria-labelledby="music-home-resume" className="music-home-shelf music-home-resume"><header><h2 id="music-home-resume">Resume</h2></header><div className="music-home-resume-row"><Artwork alt="" src={track.artwork_url} /><div><strong>{track.title || 'Saved queue'}</strong><span>{[track.artist, track.album].filter(Boolean).join(' · ') || 'Saved music'}</span><small>Queue item {(resume.current_index ?? 0) + 1} · {formatDuration(resume.position_seconds)}{track.duration ? ` / ${formatDuration(track.duration)}` : ''}</small></div><Button busy={starting} icon={<Play aria-hidden="true" />} onClick={() => void start()} variant="primary">{label}</Button></div>{problem ? <p role="alert" className="music-home-problem">{problem}{needsRefresh ? <> <TextButton onClick={onRefresh}>Refresh Music Home</TextButton></> : null}</p> : null}</section>;
}

function RecentShelf({ titles, onOpen }: { titles: TitleSummary[]; onOpen: (title: TitleSummary) => void }) {
  if (!titles.length) return null;
  return <section aria-labelledby="music-home-recent" className="music-home-shelf"><header><h2 id="music-home-recent">Recently played</h2></header><ul className="music-home-row">{titles.map((title) => <li key={title.id}><button aria-label={`Open ${title.name}`} onClick={() => onOpen(title)} type="button"><Artwork alt="" src={title.poster_url} /><span><strong>{title.name}</strong><small>{title.artist_name || 'Album'}</small></span></button></li>)}</ul></section>;
}

function PlaylistShelf({ playlists, onOpen }: { playlists: MusicPlaylist[]; onOpen: () => void }) {
  if (!playlists.length) return null;
  return <section aria-labelledby="music-home-playlists" className="music-home-shelf"><header><h2 id="music-home-playlists">Playlists</h2><TextButton onClick={onOpen}>See all</TextButton></header><ul className="music-home-list">{playlists.map((playlist) => <li key={playlist.id}><button onClick={onOpen} type="button"><ListMusic aria-hidden="true" /><span><strong>{playlist.name}</strong><small>{playlist.count === null ? 'Playlist' : `${playlist.count} ${playlist.count === 1 ? 'track' : 'tracks'}`}</small></span></button></li>)}</ul></section>;
}

function DownloadsShelf({ owner }: { owner: string }) {
  const [count, setCount] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let current = true;
    setCount(null); setFailed(false);
    void offlineTracks(owner).then((tracks) => { if (current) setCount(tracks.length); }, () => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [owner]);
  const message = failed ? 'Downloads are unavailable in this browser.' : count === null ? 'Checking downloads on this device…' : count ? `${count} ${count === 1 ? 'track is' : 'tracks are'} saved on this device.` : 'Save a track or queue from the player to listen without the server.';
  return <section aria-labelledby="music-home-downloads" className="music-home-shelf"><header><h2 id="music-home-downloads">Downloads</h2></header><div className="music-home-empty"><Download aria-hidden="true" /><p>{message} <a href="/music-offline.html">Open downloads</a></p></div></section>;
}

function MixesShelf({ mixes, onOpen }: { mixes: MusicHomeData['mixes']; onOpen: () => void }) {
  if (!mixes.length) return null;
  return <section aria-labelledby="music-home-mixes" className="music-home-shelf"><header><h2 id="music-home-mixes">Mixes</h2><TextButton onClick={onOpen}>Open mixes</TextButton></header><ul className="music-home-list">{mixes.map((mix) => <li key={mix.kind}><button onClick={onOpen} type="button"><Play aria-hidden="true" /><span><strong>{mix.name}</strong></span></button></li>)}</ul></section>;
}

function HomeLayoutEditor({ layout, onChange, onClose }: { layout: MusicHomeLayout; onChange: (next: MusicHomeLayout) => Promise<void>; onClose: () => void }) {
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const focusShelf = useRef<MusicHomeShelf | null>(null);
  const update = async (next: MusicHomeLayout, focus?: MusicHomeShelf) => {
    setPending(true);
    try { await onChange(next); focusShelf.current = focus ?? null; }
    catch (error) { setProblem(errorMessage(error)); }
    finally { setPending(false); }
  };
  useEffect(() => {
    const id = focusShelf.current;
    if (!id) return;
    focusShelf.current = null;
    requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-music-home-shelf="${id}"] button:not(:disabled)`)?.focus());
  }, [layout]);
  const move = (from: number, direction: -1 | 1) => {
    const to = from + direction;
    if (to < 0 || to >= layout.order.length) return;
    const order = [...layout.order]; [order[from], order[to]] = [order[to], order[from]];
    void update({ ...layout, order }, layout.order[from]);
  };
  const toggle = (id: MusicHomeShelf) => {
    const hidden = layout.hidden.includes(id) ? layout.hidden.filter((value) => value !== id) : [...layout.hidden, id];
    void update({ ...layout, hidden }, id);
  };
  return <section aria-label="Music Home layout" className="music-home-editor"><header><div><h2>Arrange Music Home</h2><p>Choose what appears and in what order.</p></div><TextButton onClick={onClose}>Done</TextButton></header>{problem ? <p className="music-home-problem" role="alert">{problem}</p> : null}<ol>{layout.order.map((id, index) => <li data-music-home-shelf={id} key={id}><span><strong>{shelfName(id)}</strong>{layout.hidden.includes(id) ? <small>Hidden</small> : null}</span><span className="music-home-editor-actions"><button aria-label={`Move ${shelfName(id)} earlier`} disabled={pending || index === 0} onClick={() => move(index, -1)} type="button"><ChevronUp aria-hidden="true" /></button><button aria-label={`Move ${shelfName(id)} later`} disabled={pending || index === layout.order.length - 1} onClick={() => move(index, 1)} type="button"><ChevronDown aria-hidden="true" /></button><TextButton disabled={pending} onClick={() => toggle(id)}>{layout.hidden.includes(id) ? `Show ${shelfName(id)}` : `Hide ${shelfName(id)}`}</TextButton></span></li>)}</ol><TextButton disabled={pending} icon={<RefreshCw aria-hidden="true" />} onClick={() => void update(DEFAULT_LAYOUT, 'resume')}>Reset Music Home</TextButton></section>;
}

export function MusicHome({ owner, onBrowse, onResumeIntent, onOpenTitle, onOpenPlaylists }: Props) {
  const [home, setHome] = useState<MusicHomeData | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const editTrigger = useRef<HTMLButtonElement>(null);
  const request = useRef(0);
  const load = useCallback(async () => {
    const current = ++request.current;
    setProblem(null); setHome(null);
    try {
      const next = await getMusicHome();
      if (request.current === current) setHome({ ...next, layout: canonical(next.layout) });
    } catch (error) { if (request.current === current) setProblem(errorMessage(error)); }
  }, []);
  useEffect(() => { void load(); return () => { request.current += 1; }; }, [load, owner]);
  const saveLayout = async (layout: MusicHomeLayout) => {
    const saved = await saveMusicHomeLayout(canonical(layout));
    setHome((current) => current ? { ...current, layout: canonical(saved) } : current);
  };
  if (!home && !problem) return <section aria-busy="true" aria-label="Music Home" className="music-home"><header className="music-home-head"><div><h1>Music Home</h1><p>Loading your music shelves…</p></div></header><div className="music-home-skeleton" role="status">Loading Music Home…</div></section>;
  if (!home) return <section aria-label="Music Home" className="music-home"><header className="music-home-head"><div><h1>Music Home</h1><p>Your music shelves are unavailable right now.</p></div></header><div className="music-home-error" role="alert"><p>{problem}</p><Button onClick={() => void load()}>Try again</Button></div></section>;
  const visible = home.layout.order.filter((id) => !home.layout.hidden.includes(id));
  return <section aria-label="Music Home" className="music-home"><header className="music-home-head"><div><h1>Music Home</h1></div><span><TextButton onClick={onBrowse}>Browse music</TextButton><Button onClick={() => setEditing((value) => !value)} pressed={editing} ref={editTrigger}>Edit Music Home</Button></span></header>{editing ? <HomeLayoutEditor layout={home.layout} onChange={saveLayout} onClose={() => { setEditing(false); requestAnimationFrame(() => editTrigger.current?.focus()); }} /> : null}<div className="music-home-shelves">{visible.map((id) => id === 'resume' ? <ResumeShelf home={home} key={id} onRefresh={() => void load()} onResumeIntent={onResumeIntent} owner={owner} /> : id === 'recent' ? <RecentShelf key={id} onOpen={onOpenTitle} titles={home.recent} /> : id === 'playlists' ? <PlaylistShelf key={id} onOpen={onOpenPlaylists} playlists={home.playlists} /> : id === 'downloads' ? <DownloadsShelf key={id} owner={owner} /> : <MixesShelf key={id} mixes={home.mixes} onOpen={onOpenPlaylists} />)}</div></section>;
}
