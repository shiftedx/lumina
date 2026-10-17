import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Download, ListMusic, Pause, Play, SkipBack, SkipForward, Trash2, Volume2, WifiOff } from 'lucide-react';

import '../../styles/app.css';
import { MAX_DECODED_AUDIO_BYTES, MusicEngine, type MusicEngineState } from './musicEngine';
import {
  clearOfflineMusic, enableOfflineShell, lastOfflineOwner, offlineTrack, offlineTracks, removeOfflineTrack,
  type OfflineTrack,
} from './musicOffline';
import './offline.css';

const EMPTY_PLAYBACK: MusicEngineState = { index: -1, position: 0, duration: 0, playing: false, loading: false, error: null };
type NativeOfflinePlayback = { audio: HTMLAudioElement; url: string; index: number };

function formatTime(value: number | null | undefined): string {
  const seconds = Number.isFinite(value) && value !== undefined && value !== null ? Math.max(0, Math.floor(value)) : 0;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function messageFor(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function predictedDecodedBytes(track: OfflineTrack['track']): number | null {
  if (![track.duration, track.sample_rate, track.channels].every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null;
  return track.duration! * track.sample_rate! * track.channels! * Float32Array.BYTES_PER_ELEMENT;
}

function offlineMime(track: OfflineTrack['track']): string {
  const codec = (track.codec || '').toLowerCase();
  if (codec.includes('flac')) return 'audio/flac';
  if (codec.includes('opus') || codec.includes('vorbis') || codec === 'ogg') return 'audio/ogg';
  if (codec.includes('mp3') || codec.includes('mpeg')) return 'audio/mpeg';
  if (codec.includes('aac') || codec.includes('m4a')) return 'audio/mp4';
  if (codec.includes('pcm') || codec.includes('wav')) return 'audio/wav';
  return 'application/octet-stream';
}

function Artwork({ entry, size = 'small' }: { entry: OfflineTrack | null; size?: 'small' | 'large' }) {
  if (entry?.artwork) return <img alt="" className={`offline-art offline-art-${size}`} src={entry.artwork} />;
  return <span aria-hidden="true" className={`offline-art offline-art-${size} offline-art-placeholder`}><span /></span>;
}

export function OfflineListening() {
  const [owner, setOwner] = useState<string | null>(() => lastOfflineOwner());
  const ownerRef = useRef(owner);
  const [tracks, setTracks] = useState<OfflineTrack[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [playback, setPlayback] = useState<MusicEngineState>(EMPTY_PLAYBACK);
  const [volume, setVolume] = useState(.8);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [shellError, setShellError] = useState<string | null>(null);
  const [nativeStreaming, setNativeStreaming] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const engineRef = useRef<MusicEngine | null>(null);
  const nativeRef = useRef<NativeOfflinePlayback | null>(null);

  useEffect(() => { ownerRef.current = owner; }, [owner]);

  useEffect(() => {
    let current = true;
    const currentOwner = lastOfflineOwner();
    setOwner(currentOwner);
    setLoading(true);
    setError(null);
    if (!currentOwner) {
      setTracks([]);
      setSelectedId(null);
      setLoading(false);
      return () => { current = false; };
    }
    void offlineTracks(currentOwner).then((entries) => {
      if (!current) return;
      setTracks(entries);
      setSelectedId((selected) => entries.some((entry) => entry.track.id === selected) ? selected : entries[0]?.track.id ?? null);
      setLoading(false);
    }, (reason) => {
      if (!current) return;
      setError(messageFor(reason, 'Saved music could not be read from this device.'));
      setLoading(false);
    });
    return () => { current = false; };
  }, [refreshKey]);

  useEffect(() => {
    let current = true;
    void enableOfflineShell().catch((reason) => {
      if (current) setShellError(messageFor(reason, 'The offline page could not finish preparing.'));
    });
    return () => { current = false; };
  }, []);

  useEffect(() => () => {
    engineRef.current?.dispose();
    const native = nativeRef.current;
    if (native) { native.audio.pause(); URL.revokeObjectURL(native.url); }
  }, []);

  const selected = useMemo(
    () => tracks.find((entry) => entry.track.id === selectedId) || tracks[playback.index] || tracks[0] || null,
    [playback.index, selectedId, tracks],
  );
  const active = playback.index >= 0 ? tracks[playback.index] || null : null;
  const selectedIndex = selected ? tracks.findIndex((entry) => entry.track.id === selected.track.id) : -1;
  const activeCue = selected?.lyrics?.cues.reduce((current, cue, index) => cue.start <= playback.position && active?.track.id === selected.track.id ? index : current, -1) ?? -1;
  const duration = playback.duration || active?.track.duration || 0;

  function disposePlayer() {
    engineRef.current?.dispose();
    engineRef.current = null;
    const native = nativeRef.current;
    nativeRef.current = null;
    if (native) {
      native.audio.onloadedmetadata = null;
      native.audio.ontimeupdate = null;
      native.audio.onplaying = null;
      native.audio.onpause = null;
      native.audio.onended = null;
      native.audio.onerror = null;
      native.audio.pause();
      native.audio.removeAttribute('src');
      native.audio.load();
      URL.revokeObjectURL(native.url);
    }
    setNativeStreaming(false);
    setPlayback(EMPTY_PLAYBACK);
  }

  function engine() {
    if (engineRef.current) return engineRef.current;
    const created = new MusicEngine({
      onChange: setPlayback,
      getAudio: async (source, signal) => {
        const currentOwner = ownerRef.current;
        if (!currentOwner) throw new Error('No offline account is selected.');
        const saved = await offlineTrack(currentOwner, source.id);
        if (!saved) throw new Error('This track is no longer saved on this device.');
        if (signal.aborted) throw new DOMException('Playback cancelled.', 'AbortError');
        return saved.audio;
      },
    });
    created.setVolume(volume);
    engineRef.current = created;
    return created;
  }

  async function playNative(index: number, position: number) {
    const entry = tracks[index];
    if (!entry) return;
    disposePlayer();
    const url = URL.createObjectURL(new Blob([entry.audio], { type: offlineMime(entry.track) }));
    const audio = new Audio(url);
    const native = { audio, url, index };
    nativeRef.current = native;
    audio.preload = 'auto';
    audio.volume = volume;
    audio.onloadedmetadata = () => {
      if (nativeRef.current !== native) return;
      try { audio.currentTime = Math.min(Math.max(0, position), Number.isFinite(audio.duration) ? audio.duration : position); } catch { /* seekability follows */ }
      setPlayback((value) => ({ ...value, duration: Number.isFinite(audio.duration) ? audio.duration : entry.track.duration ?? 0, position: audio.currentTime || position }));
    };
    audio.ontimeupdate = () => { if (nativeRef.current === native) setPlayback((value) => ({ ...value, position: audio.currentTime })); };
    audio.onplaying = () => { if (nativeRef.current === native) setPlayback((value) => ({ ...value, playing: true, loading: false, error: null })); };
    audio.onpause = () => { if (nativeRef.current === native && !audio.ended) setPlayback((value) => ({ ...value, playing: false, loading: false })); };
    audio.onended = () => {
      if (nativeRef.current !== native) return;
      setPlayback((value) => ({ ...value, position: value.duration, playing: false, loading: index + 1 < tracks.length }));
      if (index + 1 < tracks.length) void playAt(index + 1);
    };
    audio.onerror = () => {
      if (nativeRef.current === native) setPlayback((value) => ({ ...value, playing: false, loading: false, error: 'This saved track is not supported by the browser.' }));
    };
    setNativeStreaming(true);
    setPlayback({ ...EMPTY_PLAYBACK, index, position, duration: entry.track.duration ?? 0, loading: true });
    try { audio.currentTime = Math.max(0, position); } catch { /* metadata will apply the position */ }
    await audio.play();
    if (nativeRef.current === native) setPlayback({ index, position: audio.currentTime || position, duration: Number.isFinite(audio.duration) ? audio.duration : entry.track.duration ?? 0, playing: true, loading: false, error: null, playbackMode: 'native' });
  }

  async function playAt(index: number, position = 0) {
    const entry = tracks[index];
    if (!entry) return;
    setSelectedId(entry.track.id);
    setNotice(null);
    setError(null);
    try {
      const predicted = predictedDecodedBytes(entry.track);
      if (predicted !== null && predicted > MAX_DECODED_AUDIO_BYTES) {
        await playNative(index, position);
        return;
      }
      // A prior oversized track may still be owned by the native media element.
      // Release it before Web Audio starts so the offline page has one output.
      if (nativeRef.current) disposePlayer();
      await engine().play(tracks.map((saved) => ({
        id: saved.track.id,
        url: saved.track.audio_url,
        duration: saved.track.duration ?? undefined,
        sampleRate: saved.track.sample_rate ?? undefined,
        channels: saved.track.channels ?? undefined,
      })), index, position);
    } catch (reason) {
      if (reason instanceof Error && reason.message.includes('decoded-memory budget')) {
        try { await playNative(index, position); return; } catch (nativeReason) { reason = nativeReason; }
      }
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(messageFor(reason, 'This saved track could not be played.'));
    }
  }

  async function togglePlayback() {
    setNotice(null);
    setError(null);
    try {
      if (playback.playing) {
        if (nativeRef.current) nativeRef.current.audio.pause(); else engineRef.current?.pause();
      } else if (nativeRef.current && playback.index >= 0) {
        await nativeRef.current.audio.play();
      } else if (engineRef.current && playback.index >= 0) {
        await engineRef.current.resume();
      } else if (selectedIndex >= 0) {
        await playAt(selectedIndex);
      }
    } catch (reason) {
      setError(messageFor(reason, 'Playback could not continue.'));
    }
  }

  async function step(direction: 1 | -1) {
    try {
      if (nativeRef.current && playback.index >= 0) {
        await playAt(Math.max(0, Math.min(tracks.length - 1, playback.index + direction)));
      } else if (!engineRef.current || playback.index < 0) {
        await playAt(Math.max(0, selectedIndex + (direction === 1 ? 1 : 0)));
      } else if (direction === 1) {
        await engineRef.current.next();
      } else {
        await engineRef.current.previous();
      }
    } catch (reason) {
      setError(messageFor(reason, 'Playback could not continue.'));
    }
  }

  async function seek(position: number) {
    try {
      if (nativeRef.current) nativeRef.current.audio.currentTime = Math.max(0, Math.min(position, playback.duration || position));
      else await engineRef.current?.seek(position);
    }
    catch (reason) { setError(messageFor(reason, 'The track could not seek.')); }
  }

  function changeVolume(next: number) {
    const value = Math.max(0, Math.min(1, next));
    setVolume(value);
    engineRef.current?.setVolume(value);
    if (nativeRef.current) nativeRef.current.audio.volume = value;
  }

  async function remove(entry: OfflineTrack) {
    if (!owner) return;
    disposePlayer();
    setError(null);
    try {
      await removeOfflineTrack(owner, entry.track.id);
      setNotice(`Removed ${entry.track.title} from this device.`);
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(messageFor(reason, 'The saved track could not be removed.'));
    }
  }

  async function clearAll() {
    if (!owner) return;
    disposePlayer();
    setError(null);
    try {
      await clearOfflineMusic(owner);
      setNotice('Saved music cleared from this device.');
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      setError(messageFor(reason, 'Saved music could not be cleared.'));
    }
  }

  const playerTitle = active?.track.title || selected?.track.title || 'Choose a saved track';
  const playLabel = playback.playing ? 'Pause music' : 'Play music';

  return <div className="offline-listening">
    <a className="offline-skip" href="#offline-main">Skip to saved music</a>
    <header className="offline-header">
      <a className="offline-wordmark" href="/audio">Lumina <span>/ Offline</span></a>
      <div className="offline-header-actions"><span className="offline-connection"><WifiOff aria-hidden="true" /> No connection required</span><a className="offline-backlink" href="/audio"><ArrowLeft aria-hidden="true" /> Back to Music</a></div>
    </header>

    <main className="offline-main" id="offline-main" tabIndex={-1}>
      <section className="offline-intro" aria-labelledby="offline-heading">
        <div><p className="offline-eyebrow"><Download aria-hidden="true" /> Saved on this device</p><h1 id="offline-heading">Offline listening</h1><p className="offline-lede">Your saved music stays available when the server is out of reach.</p></div>
        <div className="offline-intro-mark" aria-hidden="true"><ListMusic /></div>
      </section>

      {shellError ? <p className="offline-message" role="status">{shellError} Saved tracks can still be played while this page remains open.</p> : null}
      {error ? <p className="offline-message is-error" role="alert">{error}</p> : null}
      {notice ? <p className="offline-message" role="status">{notice}</p> : null}

      {loading ? <div className="offline-loading" role="status"><span className="offline-loading-bar" /><span className="offline-loading-bar is-short" /><span className="offline-loading-bar" /></div> : tracks.length ? <div className="offline-layout">
        <section aria-labelledby="offline-player-heading" className="offline-player">
          <div className="offline-player-art"><Artwork entry={active || selected} size="large" /></div>
          <div className="offline-player-copy"><p className="offline-eyebrow">{playback.playing ? 'Now playing' : 'Ready when you are'}</p><h2 id="offline-player-heading">{playerTitle}</h2><p>{(active || selected)?.track.artist || 'Saved music'}</p><small>{(active || selected)?.track.album || 'Offline queue'}</small></div>
          <div className="offline-transport" aria-label="Offline playback controls">
            <button aria-label="Previous track" className="offline-icon-button" disabled={tracks.length < 1} onClick={() => void step(-1)} type="button"><SkipBack aria-hidden="true" /></button>
            <button aria-label={playLabel} className="offline-play-button" disabled={!selected} onClick={() => void togglePlayback()} type="button">{playback.playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}</button>
            <button aria-label="Next track" className="offline-icon-button" disabled={tracks.length < 1 || playback.index >= tracks.length - 1} onClick={() => void step(1)} type="button"><SkipForward aria-hidden="true" /></button>
          </div>
          <label className="offline-seek"><span className="sr-only">Track position</span><input aria-label="Track position" disabled={!playback.duration} max={duration || 1} min="0" onChange={(event) => void seek(Number(event.target.value))} step="0.1" type="range" value={Math.min(playback.position, duration || 0)} /><span><time>{formatTime(playback.position)}</time><time>{formatTime(duration)}</time></span></label>
          <label className="offline-volume"><Volume2 aria-hidden="true" /><span className="sr-only">Volume</span><input aria-label="Volume" max="1" min="0" onChange={(event) => changeVolume(Number(event.target.value))} step="0.01" type="range" value={volume} /></label>
          {playback.loading ? <p className="offline-player-status" role="status">Loading saved audio…</p> : playback.error ? <p className="offline-player-status is-error" role="alert">{playback.error}</p> : <p className="offline-player-status" role="status">{nativeStreaming ? playback.playing ? `Streaming saved source · ${playerTitle}` : 'Streaming playback is paused.' : playback.playing ? `Playing ${playerTitle}` : 'Playback is paused.'}</p>}
        </section>

        <section aria-labelledby="offline-library-heading" className="offline-library">
          <header className="offline-section-head"><div><p className="offline-eyebrow">{tracks.length} {tracks.length === 1 ? 'track' : 'tracks'}</p><h2 id="offline-library-heading">Saved queue</h2></div><div className="offline-library-actions"><button className="offline-button is-primary" onClick={() => void playAt(0)} type="button"><Play aria-hidden="true" /> Play all</button><button className="offline-button" onClick={() => void clearAll()} type="button"><Trash2 aria-hidden="true" /> Clear all</button></div></header>
          <ol className="offline-track-list">{tracks.map((entry, index) => <li aria-current={entry.track.id === active?.track.id ? 'true' : undefined} className={entry.track.id === active?.track.id ? 'is-active' : ''} key={entry.key}>
            <button aria-label={`Play ${entry.track.title}`} className="offline-track-main" onClick={() => void playAt(index)} type="button"><Artwork entry={entry} /><span className="offline-track-copy"><strong>{entry.track.title}</strong><span>{entry.track.artist} · {entry.track.album}</span></span><time>{formatTime(entry.track.duration)}</time></button>
            <button aria-label={`Remove ${entry.track.title}`} className="offline-icon-button offline-remove-button" onClick={() => void remove(entry)} type="button"><Trash2 aria-hidden="true" /></button>
          </li>)}</ol>
        </section>

        <section aria-labelledby="offline-lyrics-heading" className="offline-lyrics">
          <header className="offline-section-head"><div><p className="offline-eyebrow">{selected?.track.title || 'Select a track'}</p><h2 id="offline-lyrics-heading">Cached lyrics</h2></div></header>
          {selected?.lyrics?.cues.length ? <ol>{selected.lyrics.cues.map((cue, index) => <li key={`${cue.start}:${index}`}><button aria-current={selected.track.id === active?.track.id && index === activeCue ? 'true' : undefined} className={selected.track.id === active?.track.id && index === activeCue ? 'is-current' : ''} onClick={() => void playAt(selectedIndex, cue.start)} type="button">{cue.text}</button></li>)}</ol> : <p className="offline-plain-lyrics">{selected?.lyrics?.instrumental ? 'Instrumental' : selected?.lyrics?.plain || 'Lyrics were not saved with this track.'}</p>}
          {selected?.lyrics?.source ? <p className="offline-lyrics-source">{selected.lyrics.source}</p> : null}
        </section>
      </div> : <section className="offline-empty" aria-labelledby="offline-empty-heading"><div className="offline-empty-icon" aria-hidden="true"><Download /></div><h2 id="offline-empty-heading">Nothing saved yet</h2><p>Save tracks from Music while you are online, then they will appear here for listening without the server.</p><a className="offline-button is-primary" href="/audio"><ArrowLeft aria-hidden="true" /> Go to Music</a></section>}
    </main>
  </div>;
}

const root = document.getElementById('root');
if (root) {
  import('react-dom/client').then(({ createRoot }) => createRoot(root).render(<OfflineListening />));
}
