import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, ChevronDown, ChevronUp, ListMusic, Maximize2, Pause, Play, Repeat, SkipBack, SkipForward, Speaker, Volume2, X } from 'lucide-react';

import { castMusic, controlMusicCast, discoverSonos, getMusicCastStatus, getMusicLyrics } from '../../api';
import { Artwork } from '../../Artwork';
import { formatDuration } from '../../utils';
import { useMediaQuery } from '../gallery/WallGrid';
import { MusicVideoRail } from './MusicExtras';
import { handleMusicCastStatusError, restoreMusicCast, setMusicCast, stopMusicCast, updateMusicCastStatus, useMusicCast, type MusicCastSession } from './musicCastSession';
import { enableOfflineShell, offlineTrack, saveOfflineTrack } from './musicOffline';
import { closeMusic, currentMusicSampleRate, normalizeMusic, pauseMusic, playMusicQueue, reorderMusic, repeatMusic, seekMusic, setMusicOwner, setMusicVolume, stepMusic, streamMusicCurrent, toggleMusic, useMusicSession } from './musicSession';
import type { MusicLyrics, MusicTrack, SonosSpeaker } from './musicTypes';
import './player.css';

type RoomTab = 'listening' | 'queue' | 'output';
type OfflineScope = 'track' | 'album' | 'queue';
const MAX_TRANSFER = 100;
const distinct = (tracks: MusicTrack[]) => [...new Map(tracks.map((track) => [track.id, track])).values()];
const problem = (error: unknown, fallback: string) => error instanceof Error && error.message ? error.message : fallback;

export interface MusicPlayerProps { owner: string; hidden?: boolean; onWatchVideo: (url: string, itemId?: string) => void; onOpenAlbum?: (id: string) => void; onOpenPlaylists?: () => void }

export function MusicPlayer({ owner, hidden = false, onWatchVideo, onOpenAlbum, onOpenPlaylists }: MusicPlayerProps) {
  const session = useMusicSession();
  const cast = useMusicCast();
  const { queue, playback } = session;
  const activeCast = cast?.owner === owner ? cast : null;
  const index = activeCast?.index ?? playback.index;
  const track = queue[index];
  const position = activeCast?.position ?? playback.position;
  const duration = activeCast?.duration || playback.duration;
  const playing = activeCast?.playing ?? playback.playing;
  const [room, setRoom] = useState<RoomTab | null>(null);
  const [lyrics, setLyrics] = useState<MusicLyrics | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [savedIds, setSavedIds] = useState<Set<string>>(new Set());
  const [offlineProgress, setOfflineProgress] = useState<{ scope: OfflineScope; done: number; total: number; preparing: boolean } | null>(null);
  const [speakers, setSpeakers] = useState<SonosSpeaker[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [compatibility, setCompatibility] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const dock = useRef<HTMLElement>(null);
  const download = useRef<AbortController | null>(null);
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const phone = useMediaQuery('(max-width: 599px)');
  const lyricCues = Array.isArray(lyrics?.cues) ? lyrics.cues : [];
  const activeCue = lyricCues.reduce((current, cue, cueIndex) => cue.start <= position ? cueIndex : current, -1);

  useEffect(() => {
    download.current?.abort(); setOfflineProgress(null); setMessage(null); setLyrics(null); setSavedIds(new Set()); setRoom(null);
    let current = true;
    void (async () => {
      if (cast && cast.owner !== owner) await stopMusicCast().catch(() => undefined);
      if (!current) return;
      setMusicOwner(owner);
      await restoreMusicCast(owner);
    })();
    return () => { current = false; download.current?.abort(); };
  }, [owner]);

  useEffect(() => { const element = dialog.current; if (room && element && !element.open) element.showModal(); else if (!room && element?.open) element.close(); }, [room]);

  useEffect(() => {
    const element = dock.current, root = document.documentElement;
    if (!element) { root.style.removeProperty('--music-dock-height'); return; }
    const measure = () => root.style.setProperty('--music-dock-height', `${Math.ceil(window.innerHeight - element.getBoundingClientRect().top)}px`);
    measure(); const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element); window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); root.style.removeProperty('--music-dock-height'); };
  }, [hidden, phone, track?.id]);

  useEffect(() => {
    setMessage(null); setLyrics(null);
    if (!track) return;
    let current = true, timer: ReturnType<typeof setTimeout> | undefined, polls = 0;
    void offlineTrack(owner, track.id).then((cached) => { if (!current) return; if (cached) setSavedIds((ids) => new Set(ids).add(track.id)); if (cached?.lyrics) setLyrics(cached.lyrics); }).catch(() => undefined);
    const load = () => void getMusicLyrics(track.id).then((value) => { if (!current) return; setLyrics(value); if (value.refreshing && polls++ < 12) timer = setTimeout(load, 5000); }, async () => {
      const cached = await offlineTrack(owner, track.id).catch(() => undefined);
      if (current) setLyrics(cached?.lyrics ?? { plain: null, cues: [], source: null, instrumental: false, error: 'Lyrics are unavailable. Try again when the server is reachable.' });
    });
    load(); return () => { current = false; clearTimeout(timer); };
  }, [owner, track?.id]);

  useEffect(() => { if (room === 'listening' && activeCue >= 0) dialog.current?.querySelector(`[data-cue="${activeCue}"]`)?.scrollIntoView?.({ block: 'center', behavior: reducedMotion ? 'instant' : 'smooth' }); }, [activeCue, room, reducedMotion]);

  useEffect(() => {
    if (!activeCast) return;
    let current = true;
    const poll = () => void getMusicCastStatus(activeCast.ticket).then((raw) => {
      if (!current) return;
      updateMusicCastStatus(activeCast.ticket, raw);
    }, (error) => { if (current && !handleMusicCastStatusError(activeCast.ticket, error)) setMessage('Sonos is not responding. Refresh the output or return playback to this browser.'); });
    poll(); const timer = window.setInterval(poll, 5000); return () => { current = false; window.clearInterval(timer); };
  }, [activeCast?.ticket, queue.length]);

  function report(error: unknown) { setMessage(problem(error, 'Playback could not continue.')); }
  async function castControl(action: 'play' | 'pause' | 'next' | 'previous' | 'seek', value?: number) {
    if (activeCast) { await controlMusicCast(activeCast.ticket, action, value); if (action === 'play' || action === 'pause') setMusicCast({ ...activeCast, playing: action === 'play' }); if (action === 'seek' && value !== undefined) setMusicCast({ ...activeCast, position: value }); }
    else if (action === 'play') await toggleMusic(); else if (action === 'pause') pauseMusic(); else if (action === 'next') await stepMusic(1); else if (action === 'previous') await stepMusic(-1); else if (value !== undefined) await seekMusic(value);
  }

  useEffect(() => {
    if (!track || (!playing && !activeCast) || !('mediaSession' in navigator) || typeof MediaMetadata === 'undefined') return;
    const media = navigator.mediaSession, metadata = new MediaMetadata({ title: track.title, artist: track.artist, album: track.album, artwork: track.artwork_url ? [{ src: new URL(track.artwork_url, window.location.origin).href }] : [] });
    media.metadata = metadata; media.playbackState = playing ? 'playing' : 'paused';
    const actions: Array<[MediaSessionAction, MediaSessionActionHandler]> = [['play', () => { void castControl('play').catch(report); }], ['pause', () => { void castControl('pause').catch(report); }], ['nexttrack', () => { void castControl('next').catch(report); }], ['previoustrack', () => { void castControl('previous').catch(report); }], ['seekto', (details) => { if (details.seekTime !== undefined) void castControl('seek', details.seekTime).catch(report); }]];
    for (const [action, handler] of actions) { try { media.setActionHandler(action, handler); } catch { /* unsupported */ } }
    return () => { if (media.metadata !== metadata) return; media.metadata = null; for (const [action] of actions) { try { media.setActionHandler(action, null); } catch { /* unsupported */ } } };
  }, [track?.id, playing, activeCast?.ticket]);
  useEffect(() => { if (playing && duration && 'mediaSession' in navigator) try { navigator.mediaSession.setPositionState({ duration, position: Math.min(duration, position), playbackRate: 1 }); } catch { /* unsupported */ } }, [position, duration, playing]);

  async function discover() { setDiscovering(true); setMessage(null); try { setSpeakers(await discoverSonos()); } catch (error) { report(error); } finally { setDiscovering(false); } }
  async function castTo(speaker: SonosSpeaker) {
    const baseIndex = Math.max(0, playback.index), subset = distinct(queue.slice(baseIndex)).slice(0, MAX_TRANSFER);
    if (!subset.length) { setMessage('There are no tracks available to send to this speaker.'); return; }
    setDiscovering(true); setMessage(null);
    const resumeBrowser = !activeCast && playback.playing;
    try {
      if (activeCast) await stopMusicCast();
      else pauseMusic();
      const result = await castMusic(speaker.id, subset.map((entry) => entry.id), compatibility);
      setMusicCast({ owner, ticket: result.ticket_id, name: speaker.name, playing: true, baseIndex, index: baseIndex, position: playback.position, duration: track?.duration ?? playback.duration, volume: null, queued: subset.length }); setRoom(null);
    } catch (error) {
      if (resumeBrowser) try { await toggleMusic(); } catch { /* the cast failure remains the useful message */ }
      report(error);
    } finally { setDiscovering(false); }
  }
  async function playHere() {
    if (!activeCast) { await toggleMusic(); return; }
    const { index: castIndex, position: castPosition } = activeCast;
    await stopMusicCast();
    await playMusicQueue(queue, castIndex, castPosition);
  }

  const selections = useMemo(() => ({ track: track ? [track] : [], album: track ? queue.filter((entry) => entry.album_id ? entry.album_id === track.album_id : entry.album === track.album && entry.artist === track.artist) : [], queue }), [track?.id, queue]);
  async function saveOffline(scope: OfflineScope) {
    if (offlineProgress) return;
    const candidates = distinct(selections[scope]).slice(0, MAX_TRANSFER); if (!candidates.length) return;
    const controller = new AbortController(); download.current = controller; setOfflineProgress({ scope, done: 0, total: 0, preparing: true }); setMessage(null);
    try {
      await enableOfflineShell(); const missing: MusicTrack[] = [];
      for (const candidate of candidates) { controller.signal.throwIfAborted(); if (!await offlineTrack(owner, candidate.id)) missing.push(candidate); }
      setOfflineProgress({ scope, done: 0, total: missing.length, preparing: false });
      for (let done = 0; done < missing.length; done++) { controller.signal.throwIfAborted(); const candidate = missing[done]; const cachedLyrics = candidate.id === track?.id && lyrics ? lyrics : await getMusicLyrics(candidate.id).catch(() => null); await saveOfflineTrack(owner, candidate, controller.signal, cachedLyrics); setSavedIds((ids) => new Set(ids).add(candidate.id)); setOfflineProgress({ scope, done: done + 1, total: missing.length, preparing: false }); }
      setMessage(missing.length ? `${missing.length} ${missing.length === 1 ? 'track is' : 'tracks are'} ready offline.` : 'Those tracks are already available offline.');
    } catch (error) { if (!controller.signal.aborted) report(error); }
    finally { if (download.current === controller) download.current = null; setOfflineProgress(null); }
  }
  async function watchVideo(url: string, itemId?: string) { if (activeCast) { try { await stopMusicCast(); } catch (error) { report(error); return; } } else pauseMusic(); setRoom(null); onWatchVideo(url, itemId); }

  if (session.owner !== owner || (!track && !playback.loading && !session.message) || (hidden && !playing)) return null;
  const browserQuality = session.mode === 'streaming' ? [track?.codec?.toUpperCase() || 'Source audio', track?.sample_rate ? `${track.sample_rate / 1000} kHz source` : null, 'Browser streaming'] : [track?.codec?.toUpperCase() || 'Source audio', currentMusicSampleRate() ? `${currentMusicSampleRate()! / 1000} kHz browser clock` : null, track?.bit_depth ? `${track.bit_depth}-bit source` : null, playback.error ? 'Buffered playback' : 'Gapless buffer', 'Automatic PCM fallback'];
  const sonosQuality = compatibility ? 'Sonos compatibility · 16-bit lossless' : 'Sonos standard · up to 24-bit / 48 kHz lossless';
  const transferCount = distinct(queue.slice(Math.max(0, playback.index))).length, hasGain = track?.gain_db != null || track?.album_gain_db != null;
  const transport = <div className="music-transport"><button aria-label="Previous track" className="g-icon-button" disabled={!queue.length} onClick={() => void castControl('previous').catch(report)} type="button"><SkipBack aria-hidden="true" /></button><button aria-label={playing ? 'Pause music' : 'Play music'} className="music-play-toggle" disabled={playback.loading || !queue.length} onClick={() => void castControl(playing ? 'pause' : 'play').catch(report)} type="button">{playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}</button><button aria-label="Next track" className="g-icon-button" disabled={index >= queue.length - 1} onClick={() => void castControl('next').catch(report)} type="button"><SkipForward aria-hidden="true" /></button></div>;

  return <><section aria-label="Music player" className="gallery music-dock" id="music-player" ref={dock}><button aria-label={track ? `Open listening room for ${track.title}` : 'Open listening room'} className="music-dock-track" onClick={() => setRoom('listening')} type="button"><Artwork alt="" className="music-dock-art" src={track?.artwork_url} /><span><strong>{track?.title || 'Preparing your music…'}</strong><small>{activeCast ? `Playing on ${activeCast.name}` : track?.artist}</small></span></button>{transport}<label className="music-dock-seek"><span className="sr-only">Track position</span><input disabled={!duration} max={duration || 1} min={0} onChange={(event) => void castControl('seek', Number(event.target.value)).catch(report)} step="0.1" type="range" value={Math.min(position, duration || 0)} /><span>{formatDuration(position)} / {formatDuration(duration)}</span></label><div className="music-dock-tools"><button aria-label="Open queue" className="g-icon-button" onClick={() => setRoom('queue')} type="button"><ListMusic aria-hidden="true" /></button><button aria-label="Choose audio output" className="g-icon-button" onClick={() => { setRoom('output'); if (!speakers) void discover(); }} type="button"><Speaker aria-hidden="true" /></button><button aria-label="Open listening room" className="g-icon-button" onClick={() => setRoom('listening')} type="button"><Maximize2 aria-hidden="true" /></button>{!phone ? <Volume cast={activeCast} onError={report} value={activeCast?.volume ?? session.volume} /> : null}</div>{session.message || playback.error || message ? <p className="music-dock-message" role="status">{message || playback.error || session.message}</p> : null}</section>
    <dialog aria-labelledby="music-room-heading" className="gallery music-room" onCancel={() => setRoom(null)} ref={dialog}><header className="music-room-head"><h2 id="music-room-heading">{room === 'queue' ? 'Your queue' : room === 'output' ? 'Listen on' : 'Listening room'}</h2><button aria-label="Close listening room" className="g-icon-button" onClick={() => setRoom(null)} type="button"><X aria-hidden="true" /></button></header><nav aria-label="Listening room sections" className="music-room-tabs">{(['listening', 'queue', 'output'] as const).map((tab) => <button aria-current={room === tab ? 'page' : undefined} className="g-text-button" key={tab} onClick={() => { setRoom(tab); if (tab === 'output' && !speakers) void discover(); }} type="button">{tab === 'listening' ? 'Now playing' : tab === 'queue' ? 'Queue' : 'Output'}</button>)}</nav>
      {room === 'listening' && track ? <div className="music-room-layout"><div className="music-room-record"><div className="music-room-cover"><Artwork alt={`Cover of ${track.album}`} className="music-room-art" src={track.artwork_url} /></div><h3>{track.title}</h3><p>{track.artist}</p><button className="g-text-button" disabled={!onOpenAlbum || !track.album_id} onClick={() => { if (onOpenAlbum && track.album_id) { setRoom(null); onOpenAlbum(track.album_id); } }} type="button">{track.album}</button><p className="music-quality">{activeCast ? sonosQuality : browserQuality.filter(Boolean).join(' · ')}</p>{transport}<div className="music-room-options"><button aria-label={`Repeat: ${session.repeat}`} aria-pressed={session.repeat !== 'off'} className="g-icon-button" disabled={Boolean(activeCast)} onClick={repeatMusic} type="button"><Repeat aria-hidden="true" />{session.repeat === 'one' ? <span>1</span> : null}</button><label><input checked={session.normalize} disabled={Boolean(activeCast) || !hasGain} onChange={(event) => normalizeMusic(event.target.checked)} type="checkbox" /> Normalize loudness</label>{phone ? <Volume cast={activeCast} onError={report} value={activeCast?.volume ?? session.volume} /> : null}</div><p className="music-normalize-note">{activeCast ? 'Loudness matching stays in this browser.' : hasGain ? 'Uses saved ReplayGain or server analysis.' : 'No loudness data is available for this track.'}</p><OfflineActions albumCount={selections.album.length} currentSaved={savedIds.has(track.id)} onCancel={() => download.current?.abort()} onSave={saveOffline} progress={offlineProgress} queueCount={selections.queue.length} />{session.mode === 'gapless' && playback.error ? <button className="g-text-button" onClick={() => void streamMusicCurrent().catch(report)} type="button">Stream track</button> : null}</div><section aria-label="Lyrics" className="music-lyrics">{lyricCues.length ? <ol>{lyricCues.map((cue, cueIndex) => <li data-cue={cueIndex} key={`${cue.start}:${cueIndex}`}><button aria-current={cueIndex === activeCue ? 'true' : undefined} className={cueIndex === activeCue ? 'is-current' : ''} onClick={() => void castControl('seek', cue.start).catch(report)} type="button">{cue.text}</button></li>)}</ol> : <div><h3>Lyrics</h3><p className="music-plain-lyrics">{lyrics?.plain || (lyrics?.instrumental ? 'Instrumental' : lyrics?.refreshing ? 'Finding lyrics…' : lyrics?.error || 'Lyrics are not available for this recording yet.')}</p></div>}{lyrics?.source ? <p className="music-lyrics-source">{lyrics.source}</p> : null}</section><div className="music-room-videos"><MusicVideoRail id={track.id} kind="track" onWatch={(url, itemId) => void watchVideo(url, itemId)} /></div></div> : null}
      {room === 'queue' ? <div className="music-room-queue"><div className="music-queue-actions">{onOpenPlaylists ? <button className="g-text-button" onClick={() => { setRoom(null); onOpenPlaylists(); }} type="button">Save or manage playlists</button> : null}<button className="g-text-button" disabled={Boolean(offlineProgress)} onClick={() => void saveOffline('queue')} type="button">Save queue offline{selections.queue.length > MAX_TRANSFER ? ` (next ${MAX_TRANSFER})` : ''}</button></div><ol>{queue.map((entry, queueIndex) => <li aria-current={queueIndex === index ? 'true' : undefined} key={`${entry.id}:${queueIndex}`}><button className="music-queue-track" onClick={() => void playMusicQueue(queue, queueIndex).catch(report)} type="button"><Artwork alt="" src={entry.artwork_url} /><span><strong>{entry.title}</strong><small>{entry.artist} · {entry.album}</small></span><span>{entry.duration ? formatDuration(entry.duration) : ''}</span></button>{!activeCast && queueIndex > playback.index ? <span><button aria-label={`Move ${entry.title} earlier`} className="g-icon-button" disabled={queueIndex <= playback.index + 1} onClick={() => reorderMusic(queueIndex, queueIndex - 1)} type="button"><ChevronUp aria-hidden="true" /></button><button aria-label={`Move ${entry.title} later`} className="g-icon-button" disabled={queueIndex === queue.length - 1} onClick={() => reorderMusic(queueIndex, queueIndex + 1)} type="button"><ChevronDown aria-hidden="true" /></button></span> : null}</li>)}</ol><button className="g-text-button" onClick={() => void stopMusicCast().then(() => { closeMusic(); setRoom(null); }).catch(report)} type="button">Clear queue</button></div> : null}
      {room === 'output' ? <div className="music-output"><button aria-pressed={!activeCast} className="music-output-device" onClick={() => void playHere().catch(report)} type="button"><Speaker aria-hidden="true" /><span>This browser<small>{activeCast ? 'Return playback here' : session.mode === 'streaming' ? 'Streaming source audio' : `${currentMusicSampleRate() ? `${currentMusicSampleRate()! / 1000} kHz` : 'Browser'} clock · ${playback.error ? 'buffered playback' : 'gapless buffer'}`}</small></span></button><label className="music-compatibility"><input checked={compatibility} onChange={(event) => setCompatibility(event.target.checked)} type="checkbox" /> Compatibility mode <small>16-bit lossless for older Sonos S1 systems</small></label><header className="music-section-head"><h3>Sonos</h3><button className="g-text-button" disabled={discovering} onClick={() => void discover()} type="button">{discovering ? 'Looking for speakers…' : 'Refresh'}</button></header>{transferCount > MAX_TRANSFER ? <p className="music-note">Sonos will receive the next {MAX_TRANSFER} of {transferCount} distinct queued tracks. Return here later to send the following set.</p> : <p className="music-note">Sonos will receive {transferCount} {transferCount === 1 ? 'track' : 'tracks'}.</p>}{speakers?.map((speaker) => <button className="music-output-device" disabled={discovering} key={speaker.id} onClick={() => void castTo(speaker)} type="button"><Speaker aria-hidden="true" /><span>{speaker.name}<small>{speaker.model} · {compatibility ? '16-bit compatibility' : 'up to 24-bit / 48 kHz'}</small></span></button>)}{speakers && !speakers.length ? <p className="music-note">No Sonos speakers found. Check that the server and speakers share a network, then refresh.</p> : null}</div> : null}{message || session.message || playback.error ? <p className="music-note" role="status">{message || playback.error || session.message}</p> : null}</dialog></>;
}

function Volume({ cast, value, onError }: { cast: MusicCastSession | null; value: number; onError: (error: unknown) => void }) { return <label className="music-volume"><Volume2 aria-hidden="true" /><span className="sr-only">Music volume</span><input max="1" min="0" onChange={(event) => { const next = Number(event.target.value); if (cast) void controlMusicCast(cast.ticket, 'volume', Math.round(next * 100)).then(() => setMusicCast({ ...cast, volume: next })).catch(onError); else setMusicVolume(next); }} step="0.01" type="range" value={value} /></label>; }

function OfflineActions({ currentSaved, progress, queueCount, albumCount, onCancel, onSave }: { currentSaved: boolean; progress: { scope: OfflineScope; done: number; total: number; preparing: boolean } | null; queueCount: number; albumCount: number; onCancel: () => void; onSave: (scope: OfflineScope) => void }) {
  const status = progress ? progress.preparing ? 'Checking saved tracks…' : `Saved ${progress.done} of ${progress.total}` : null;
  return <div className="music-offline-actions"><button className="g-text-button" disabled={Boolean(progress) || currentSaved} onClick={() => onSave('track')} type="button"><ArrowDownToLine aria-hidden="true" />{currentSaved ? 'Track available offline' : 'Save track'}</button><button className="g-text-button" disabled={Boolean(progress) || albumCount < 1} onClick={() => onSave('album')} type="button">Save album{albumCount > MAX_TRANSFER ? ` (first ${MAX_TRANSFER})` : ''}</button><button className="g-text-button" disabled={Boolean(progress) || queueCount < 1} onClick={() => onSave('queue')} type="button">Save queue{queueCount > MAX_TRANSFER ? ` (next ${MAX_TRANSFER})` : ''}</button>{progress ? <><span aria-live="polite" role="status">{status}</span><button className="g-text-button" onClick={onCancel} type="button">Cancel</button></> : null}</div>;
}
