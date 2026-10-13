import { useSyncExternalStore } from 'react';

import { ApiRequestError, getMusicQueue, updatePlaybackProgress } from '../../api';
import { shuffled } from '../gallery/albumQueue';
import { stopMusicCast } from './musicCastSession';
import { MAX_AUDIO_BYTES, MAX_DECODED_AUDIO_BYTES, MusicEngine, type MusicEngineState, type MusicRepeatMode, type MusicSource } from './musicEngine';
import { offlineTrack } from './musicOffline';
import { MusicStream } from './musicStream';
import type { MusicTrack } from './musicTypes';

export interface MusicSession {
  owner: string | null;
  queue: MusicTrack[];
  playback: MusicEngineState;
  mode: 'gapless' | 'streaming';
  volume: number;
  normalize: boolean;
  repeat: MusicRepeatMode;
  message: string | null;
}

interface PlaybackController {
  pause(): void;
  resume(): Promise<void>;
  seek(position: number): Promise<void>;
  next(): Promise<void>;
  previous(): Promise<void>;
  setVolume(volume: number): void;
  setGain(gain: number): void;
  setRepeat(mode: MusicRepeatMode): void;
  replaceUpcoming(queue: readonly MusicSource[]): void;
  getState(): MusicEngineState;
  dispose(): void;
}

const EMPTY_PLAYBACK: MusicEngineState = { index: -1, position: 0, duration: 0, playing: false, loading: false, error: null };
let state: MusicSession = { owner: null, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', volume: 0.8, normalize: true, repeat: 'off', message: null };
let generation = 0;
let engine: MusicEngine | null = null;
let stream: MusicStream | null = null;
let context: AudioContext | null = null;
let checkpointAt = 0;
let accessStopped = false;
let lastCompleted: string | null = null;
const listeners = new Set<() => void>();
const emit = (patch: Partial<MusicSession>) => { state = { ...state, ...patch }; listeners.forEach((listener) => listener()); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useMusicSession = (): MusicSession => useSyncExternalStore(subscribe, () => state);
export const musicSession = (): MusicSession => state;

const sourceFor = (track: MusicTrack): MusicSource => ({
  id: track.id,
  url: track.audio_url,
  fallbackUrl: `${track.audio_url}?format=wav`,
  duration: track.duration ?? undefined,
  sampleRate: track.sample_rate ?? undefined,
  channels: track.channels ?? undefined,
});
const predictedDecodedBytes = (track: MusicTrack): number | null => {
  const facts = [track.duration, track.sample_rate, track.channels];
  if (!facts.every((value) => typeof value === 'number' && Number.isFinite(value) && value > 0)) return null;
  const bytes = track.duration! * track.sample_rate! * track.channels! * Float32Array.BYTES_PER_ELEMENT;
  return Number.isFinite(bytes) && bytes > 0 ? bytes : null;
};
const queueNeedsStreaming = (queue: readonly MusicTrack[]): boolean => queue.some((track) => {
  const bytes = predictedDecodedBytes(track);
  return bytes !== null && bytes > MAX_DECODED_AUDIO_BYTES;
});
const activeController = (): PlaybackController | null => state.mode === 'streaming' ? stream : engine;

function validStoredTrack(value: unknown): value is MusicTrack {
  if (!value || typeof value !== 'object') return false;
  const track = value as Partial<MusicTrack>;
  return typeof track.id === 'string'
    && /^[a-zA-Z0-9_-]{1,64}$/.test(track.id)
    && track.audio_url === `/api/music/tracks/${track.id}/audio`
    && typeof track.title === 'string'
    && typeof track.artist === 'string'
    && typeof track.album_artist === 'string'
    && typeof track.album === 'string'
    && Array.isArray(track.genres);
}

function persist(): void {
  if (!state.owner) return;
  try {
    localStorage.setItem(`lumina:music:queue:${state.owner}`, JSON.stringify({ queue: state.queue, index: state.playback.index, position: state.playback.position }));
  } catch { /* full storage does not interrupt playback */ }
}

export function setMusicOwner(owner: string | null): void {
  if (state.owner === owner) return;
  generation += 1;
  engine?.dispose();
  stream?.dispose();
  engine = null;
  stream = null;
  context = null;
  accessStopped = false;
  checkpointAt = 0;
  lastCompleted = null;
  if (owner) {
    try { localStorage.setItem('lumina:music:offline-owner', owner); } catch { /* private browsing may deny storage */ }
  }
  state = { ...state, owner, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', message: null };
  if (owner) {
    try {
      const stored = JSON.parse(localStorage.getItem(`lumina:music:queue:${owner}`) || 'null') as { queue?: unknown; index?: unknown; position?: unknown } | null;
      if (stored && Array.isArray(stored.queue) && stored.queue.length > 0 && stored.queue.length <= 5000 && stored.queue.every(validStoredTrack)) {
        const index = Number.isInteger(stored.index) ? Number(stored.index) : 0;
        const position = typeof stored.position === 'number' && Number.isFinite(stored.position) && stored.position >= 0 ? stored.position : 0;
        state = { ...state, queue: stored.queue, playback: { ...EMPTY_PLAYBACK, index: Math.max(0, Math.min(index, stored.queue.length - 1)), position } };
      }
    } catch { /* an old or malformed saved queue starts empty */ }
  }
  emit({});
}

const completed = (playback: MusicEngineState): boolean => playback.duration > 0 && playback.position >= playback.duration - 0.5;
const completionKey = (track: MusicTrack): string => `${state.owner ?? ''}:${track.id}`;

function reportTrack(track: MusicTrack, playback: MusicEngineState, complete: boolean): void {
  if (!state.owner || !navigator.onLine || accessStopped) return;
  const key = completionKey(track);
  if (complete && lastCompleted === key) return;
  if (complete) lastCompleted = key;
  void reportProgress(track.id, playback, complete);
}

function playbackChanged(playback: MusicEngineState): void {
  const previous = state.playback;
  if (previous.index !== playback.index && previous.index >= 0) {
    const track = state.queue[previous.index];
    if (track) reportTrack(track, previous, completed(previous));
  }
  const current = state.queue[playback.index];
  if (current && !completed(playback) && lastCompleted === completionKey(current)) lastCompleted = null;
  emit({ playback });
  if (playback.index !== previous.index) applyGain();
  if (Date.now() - checkpointAt > 10_000 || (previous.playing && !playback.playing)) void checkpointMusic();
}

function primeEngine(): MusicEngine {
  if (stream) { stream.dispose(); stream = null; }
  if (!context) {
    const Context = globalThis.AudioContext ?? (globalThis as typeof globalThis & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Context) throw new Error('This browser cannot start the music player.');
    context = new Context();
  }
  void context.resume().catch(() => undefined);
  if (!engine) engine = new MusicEngine({
    createContext: () => context!,
    getAudio: async (source: MusicSource, signal: AbortSignal) => {
      const owner = state.owner;
      const local = owner && !source.url.includes('?format=') ? await offlineTrack(owner, source.id).catch(() => undefined) : undefined;
      signal.throwIfAborted();
      if (owner !== state.owner) throw new DOMException('Member changed', 'AbortError');
      if (local) return local.audio;
      const response = await fetch(source.url, { credentials: 'same-origin', signal });
      if (!response.ok) {
        const error = Object.assign(new Error(response.status === 401 ? 'Sign in again to continue listening.' : response.status === 403 ? 'Listening access is currently unavailable.' : 'This track is unavailable.'), { status: response.status });
        if (owner === state.owner && (response.status === 401 || response.status === 403)) stopForAccess(error);
        throw error;
      }
      return readMusicAudio(response, signal);
    },
    onChange: playbackChanged,
  });
  engine.setVolume(state.volume);
  engine.setRepeat(state.repeat);
  return engine;
}

function primeStream(): MusicStream {
  if (engine) { engine.dispose(); engine = null; context = null; }
  if (!stream) stream = new MusicStream({ onChange: playbackChanged, onAccessDenied: stopForAccess });
  stream.setVolume(state.volume);
  stream.setRepeat(state.repeat);
  return stream;
}

export function checkpointMusic(): Promise<void> {
  checkpointAt = Date.now();
  const track = state.queue[state.playback.index];
  persist();
  if (track && state.owner && navigator.onLine && !accessStopped) {
    const complete = completed(state.playback);
    const key = completionKey(track);
    if (complete && lastCompleted === key) return Promise.resolve();
    if (complete) lastCompleted = key;
    return reportProgress(track.id, state.playback, complete);
  }
  return Promise.resolve();
}

async function reportProgress(id: string, playback: MusicEngineState, complete: boolean): Promise<void> {
  const owner = state.owner;
  try {
    await updatePlaybackProgress(id, { position_seconds: Math.floor(playback.position), duration_seconds: Math.round(playback.duration), completed: complete }, { keepalive: true });
  } catch (error) {
    if (owner === state.owner && error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) stopForAccess(error);
  }
}

function stopForAccess(error: Error): void {
  accessStopped = true;
  activeController()?.pause();
  emit({ message: error.message });
}

export async function readMusicAudio(response: Response, signal: AbortSignal): Promise<ArrayBuffer> {
  const tooLarge = () => new Error('This track is too large for the gapless buffer. Choose streaming playback.');
  if (Number(response.headers.get('content-length') || 0) > MAX_AUDIO_BYTES) {
    await response.body?.cancel().catch(() => undefined);
    throw tooLarge();
  }
  if (!response.body) {
    const bytes = await response.arrayBuffer();
    signal.throwIfAborted();
    if (bytes.byteLength > MAX_AUDIO_BYTES) throw tooLarge();
    return bytes;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let completedRead = false;
  let cancellation: Promise<void> | null = null;
  const cancel = (): Promise<void> => cancellation ??= reader.cancel().catch(() => undefined);
  const abort = () => { void cancel(); };
  signal.addEventListener('abort', abort, { once: true });
  try {
    while (true) {
      signal.throwIfAborted();
      const part = await reader.read();
      signal.throwIfAborted();
      if (part.done) break;
      total += part.value.byteLength;
      if (total > MAX_AUDIO_BYTES) throw tooLarge();
      chunks.push(part.value.slice());
    }
    completedRead = true;
  } finally {
    signal.removeEventListener('abort', abort);
    if (!completedRead) await cancel();
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes.buffer as ArrayBuffer;
}

function applyGain(): void {
  const track = state.queue[state.playback.index];
  const albumId = track?.album_id;
  const oneAlbum = Boolean(albumId && state.queue.every((entry) => entry.album_id === albumId));
  const sequential = state.queue.every((entry, index, queue) => {
    if (index === 0) return true;
    const previous = queue[index - 1];
    return (previous.disc ?? 1) < (entry.disc ?? 1)
      || (previous.disc ?? 1) === (entry.disc ?? 1) && (previous.number ?? Number.MAX_SAFE_INTEGER) <= (entry.number ?? Number.MAX_SAFE_INTEGER);
  });
  const useManual = track?.gain_is_manual && track.gain_db != null;
  const useAlbum = !useManual && oneAlbum && sequential && track?.album_gain_db != null;
  const gainDb = useAlbum ? track?.album_gain_db : track?.gain_db;
  const peak = useAlbum ? track?.album_peak : track?.peak;
  let gain = state.normalize && typeof gainDb === 'number' && Number.isFinite(gainDb) ? 10 ** (gainDb / 20) : 1;
  if (state.normalize && typeof peak === 'number' && Number.isFinite(peak) && peak > 0) gain = Math.min(gain, 1 / peak);
  activeController()?.setGain(gain);
}

export function currentMusicSampleRate(): number | null {
  return state.mode === 'gapless' && context && Number.isFinite(context.sampleRate) ? context.sampleRate : null;
}

async function stopCastForBrowserPlayback(owner: string | null, isCurrent: () => boolean): Promise<boolean> {
  try { await stopMusicCast(owner ?? undefined); }
  catch (error) {
    if (isCurrent()) emit({ message: error instanceof Error ? error.message : 'The speaker session could not be stopped.' });
    return false;
  }
  return isCurrent();
}

export async function playMusic(owner: string, request: { item_ids?: string[]; title_id?: string }, startId?: string, position = 0, shuffle = false): Promise<void> {
  setMusicOwner(owner);
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  const player = primeEngine();
  accessStopped = false;
  player.pause();
  emit({ message: null, mode: 'gapless', playback: { ...state.playback, loading: true } });
  try {
    const loaded = await getMusicQueue(request);
    const queue = shuffle ? shuffled(loaded) : loaded;
    if (current !== generation || owner !== state.owner) return;
    await startMusicQueue(queue, Math.max(0, queue.findIndex((track) => track.id === startId)), position);
  } catch (error) {
    if (current === generation) emit({ message: error instanceof Error ? error.message : 'Music could not load.', playback: { ...state.playback, loading: false } });
  }
}

async function startMusicQueue(queue: MusicTrack[], index: number, position: number): Promise<void> {
  if (!queue.length) { emit({ message: 'No tracks are available in this queue.' }); return; }
  if (queueNeedsStreaming(queue)) {
    await startStreamQueue(queue, index, position);
  } else {
    const player = primeEngine();
    emit({ queue, message: null, mode: 'gapless' });
    accessStopped = false;
    lastCompleted = null;
    await player.play(queue.map(sourceFor), index, position);
    applyGain();
    persist();
  }
}

export async function playMusicQueue(queue: MusicTrack[], index = 0, position = 0): Promise<void> {
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  await startMusicQueue(queue, index, position);
}

async function startStreamQueue(queue: MusicTrack[], index: number, position: number): Promise<void> {
  if (!queue.length) { emit({ message: 'No tracks are available in this queue.' }); return; }
  const player = primeStream();
  emit({ queue, message: null, mode: 'streaming' });
  accessStopped = false;
  lastCompleted = null;
  await player.play(queue.map(sourceFor), index, position);
  applyGain();
  persist();
}

export async function streamMusicQueue(queue: MusicTrack[], index = 0, position = 0): Promise<void> {
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  await startStreamQueue(queue, index, position);
}

export async function streamMusicCurrent(): Promise<void> {
  await streamMusicQueue(state.queue, Math.max(0, state.playback.index), state.playback.position);
}

export const pauseMusic = (): void => { activeController()?.pause(); void checkpointMusic(); };
export async function toggleMusic(): Promise<void> {
  if (state.playback.playing) { pauseMusic(); return; }
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  const controller = activeController();
  if (controller && controller.getState().index >= 0) await controller.resume();
  else if (state.mode === 'streaming') await startStreamQueue(state.queue, Math.max(0, state.playback.index), state.playback.position);
  else await startMusicQueue(state.queue, Math.max(0, state.playback.index), state.playback.position);
}
export const seekMusic = async (position: number): Promise<void> => { await (state.mode === 'streaming' ? primeStream() : primeEngine()).seek(position); };
export const stepMusic = async (direction: 1 | -1): Promise<void> => {
  const controller = activeController();
  if (!controller) {
    const index = Math.max(0, state.playback.index + direction);
    if (state.mode === 'streaming') await streamMusicQueue(state.queue, index);
    else await playMusicQueue(state.queue, index);
    return;
  }
  if (direction === 1) await controller.next(); else await controller.previous();
};
export const setMusicVolume = (volume: number): void => { emit({ volume: Math.max(0, Math.min(1, volume)) }); activeController()?.setVolume(state.volume); };
export const normalizeMusic = (normalize: boolean): void => { emit({ normalize }); applyGain(); };
export const repeatMusic = (): void => {
  emit({ repeat: state.repeat === 'off' ? 'all' : state.repeat === 'all' ? 'one' : 'off' });
  activeController()?.setRepeat(state.repeat);
};
export function closeMusic(): void {
  pauseMusic();
  generation += 1;
  engine?.dispose();
  stream?.dispose();
  engine = null;
  stream = null;
  context = null;
  emit({ queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', message: null });
  persist();
}
export function reorderMusic(from: number, to: number): void {
  if (from <= state.playback.index || to <= state.playback.index || to < 0 || to >= state.queue.length) return;
  const queue = [...state.queue];
  const [track] = queue.splice(from, 1);
  queue.splice(to, 0, track);
  activeController()?.replaceUpcoming(queue.map(sourceFor));
  emit({ queue });
  persist();
}
