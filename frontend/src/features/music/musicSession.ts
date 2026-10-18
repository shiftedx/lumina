import { useSyncExternalStore } from 'react';

import { ApiRequestError, getMusicQueue, updatePlaybackProgress } from '../../api';
import { shuffled } from '../gallery/albumQueue';
import { forgetMusicCast, restoreSharedMusicCast, stopMusicCast } from './musicCastSession';
import { MAX_AUDIO_BYTES, MAX_DECODED_AUDIO_BYTES, MusicEngine, type MusicEngineState, type MusicRepeatMode, type MusicSource } from './musicEngine';
import { offlineTrack } from './musicOffline';
import { recordMusicListening, setListeningExportOwner } from './musicListeningExport';
import { getMusicSharedSession, putMusicSharedSession, type MusicQueueEntry, type MusicSharedSnapshot } from './musicSharedSession';
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
  shuffle: boolean;
  sharedRevision: number | null;
  sharedConflict: MusicSharedSnapshot | null;
  crossfadeSeconds: number;
  sleepUntil: number | null;
  message: string | null;
  sharedEntries: MusicQueueEntry[];
  sharedEntryIndex: number | null;
  unavailablePolicy: 'skip' | 'wait';
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
  setCrossfade?(seconds: number): void;
  replaceUpcoming(queue: readonly MusicSource[]): void;
  getState(): MusicEngineState;
  dispose(): void;
}

const EMPTY_PLAYBACK: MusicEngineState = { index: -1, position: 0, duration: 0, playing: false, loading: false, error: null };
let state: MusicSession = { owner: null, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', volume: 0.8, normalize: true, repeat: 'off', shuffle: false, sharedRevision: null, sharedConflict: null, crossfadeSeconds: 0, sleepUntil: null, message: null, sharedEntries: [], sharedEntryIndex: null, unavailablePolicy: 'skip' };
let generation = 0;
let engine: MusicEngine | null = null;
let stream: MusicStream | null = null;
let context: AudioContext | null = null;
let checkpointAt = 0;
let accessStopped = false;
let lastCompleted: string | null = null;
let sharedDirty = false;
let sharedWrite: Promise<void> | null = null;
let sharedQueued = false;
let outputTransfer = false;
let adoptingShared = false;
let sleepTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
const listeners = new Set<() => void>();
const emit = (patch: Partial<MusicSession>) => { state = { ...state, ...patch }; listeners.forEach((listener) => listener()); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useMusicSession = (): MusicSession => useSyncExternalStore(subscribe, () => state);
export const musicSession = (): MusicSession => state;
if (typeof window !== 'undefined') window.addEventListener('online', () => { if (state.owner) void restoreMusicSharedSession(state.owner); });

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
  return bytes === null || bytes > MAX_DECODED_AUDIO_BYTES;
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
    localStorage.setItem(`lumina:music:queue:${state.owner}`, JSON.stringify({ queue: state.queue, index: state.playback.index, position: state.playback.position, revision: state.sharedRevision, repeat: state.repeat, shuffle: state.shuffle, dirty: sharedDirty }));
  } catch { /* full storage does not interrupt playback */ }
}

export function setMusicOwner(owner: string | null): void {
  if (state.owner === owner) return;
  const previousOwner = state.owner;
  generation += 1;
  if (previousOwner) forgetMusicCast(previousOwner);
  setListeningExportOwner(owner);
  engine?.dispose();
  stream?.dispose();
  engine = null;
  stream = null;
  context = null;
  accessStopped = false;
  checkpointAt = 0;
  lastCompleted = null;
  sharedDirty = false;
  sharedWrite = null;
  sharedQueued = false;
  if (sleepTimer !== null) { globalThis.clearTimeout(sleepTimer); sleepTimer = null; }
  if (!owner && previousOwner) {
    try { localStorage.removeItem(`lumina:music:sleep:${previousOwner}`); } catch { /* local preference only */ }
  }
  if (owner) {
    try { localStorage.setItem('lumina:music:offline-owner', owner); } catch { /* private browsing may deny storage */ }
  }
  state = { ...state, owner, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', shuffle: false, sharedRevision: null, sharedConflict: null, sleepUntil: null, message: null, sharedEntries: [], sharedEntryIndex: null, unavailablePolicy: 'skip' };
  if (owner) {
    try {
      const stored = JSON.parse(localStorage.getItem(`lumina:music:queue:${owner}`) || 'null') as { queue?: unknown; index?: unknown; position?: unknown; revision?: unknown; repeat?: unknown; shuffle?: unknown; dirty?: unknown } | null;
      if (stored && Array.isArray(stored.queue) && stored.queue.length > 0 && stored.queue.length <= 5000 && stored.queue.every(validStoredTrack)) {
        const index = Number.isInteger(stored.index) ? Number(stored.index) : 0;
        const position = typeof stored.position === 'number' && Number.isFinite(stored.position) && stored.position >= 0 ? stored.position : 0;
        state = { ...state, queue: stored.queue, repeat: stored.repeat === 'all' || stored.repeat === 'one' ? stored.repeat : 'off', shuffle: stored.shuffle === true, sharedRevision: Number.isInteger(stored.revision) ? Number(stored.revision) : null, playback: { ...EMPTY_PLAYBACK, index: Math.max(0, Math.min(index, stored.queue.length - 1)), position } };
        // Cache entries from before shared sessions are a one-time ID-only migration.
        sharedDirty = stored.dirty === true || stored.revision === undefined;
      }
    } catch { /* an old or malformed saved queue starts empty */ }
    try {
      const until = Number(localStorage.getItem(`lumina:music:sleep:${owner}`));
      if (Number.isFinite(until) && until > Date.now()) setMusicSleepTimer((until - Date.now()) / 60_000);
    } catch { /* local timer is optional */ }
  }
  emit({});
  if (owner) void restoreMusicSharedSession(owner);
}

function sharedPayload(base_revision: number) {
  return { base_revision, item_ids: state.queue.map((track) => track.id), current_index: state.queue.length ? Math.max(0, Math.min(state.playback.index, state.queue.length - 1)) : null, position_seconds: Math.max(0, state.playback.position), repeat: state.repeat, shuffle: state.shuffle, active_output: { kind: 'browser' as const },
    entries: state.sharedEntries.length ? state.sharedEntries.map((entry) => entry.kind === 'track' ? { kind: 'track' as const, item_id: entry.item_id } : { kind: 'acquisition' as const, request_id: entry.request_id }) : undefined,
    entry_index: state.sharedEntryIndex, unavailable_policy: state.unavailablePolicy };
}

export function applyMusicSharedSnapshot(snapshot: MusicSharedSnapshot): void {
  // Canonical restore is deliberately paused. Dispose first so a later toggle
  // cannot resume a stale decoded queue after a conflict or an import refresh.
  adoptingShared = true;
  generation += 1;
  try {
    engine?.dispose();
    stream?.dispose();
    engine = null;
    stream = null;
    context = null;
    emit({ queue: snapshot.queue, repeat: snapshot.repeat, shuffle: snapshot.shuffle, sharedRevision: snapshot.revision, sharedConflict: null, sharedEntries: snapshot.entries ?? snapshot.queue.map((track) => ({ kind: 'track' as const, item_id: track.id, state: 'playable' as const, tracks: [track] })), sharedEntryIndex: snapshot.entry_index ?? snapshot.current_index, unavailablePolicy: snapshot.unavailable_policy ?? 'skip', playback: { ...EMPTY_PLAYBACK, index: snapshot.current_index ?? -1, position: snapshot.position_seconds } });
  } finally { adoptingShared = false; }
  if (state.owner && snapshot.active_output.kind === 'sonos') {
    const indices = snapshot.active_output.queue_indices;
    const index = indices[0] ?? snapshot.current_index ?? 0;
    void restoreSharedMusicCast({ owner: state.owner, ticket: snapshot.active_output.ticket_id, name: 'Sonos speaker', playing: false, queueIndices: indices,
      baseIndex: index, index, position: snapshot.position_seconds, duration: snapshot.queue[index]?.duration ?? 0,
      volume: null, queued: Math.max(1, indices.length) });
  }
  sharedDirty = false;
  persist();
}

function conflictSnapshot(error: unknown): MusicSharedSnapshot | null {
  if (!(error instanceof ApiRequestError) || error.status !== 409 || !error.body || typeof error.body !== 'object') return null;
  const detail = (error.body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== 'object' || (detail as { code?: unknown }).code !== 'music_session_conflict') return null;
  const session = (detail as { session?: unknown }).session;
  return session && typeof session === 'object' ? session as MusicSharedSnapshot : null;
}

/** Re-resolve logical acquisition entries without starting browser playback. */
export async function refreshMusicSharedSession(owner: string): Promise<void> {
  if (owner !== state.owner) return;
  if (sharedDirty) { await restoreMusicSharedSession(owner); return; }
  const current = generation;
  try {
    const server = await getMusicSharedSession();
    if (current !== generation || owner !== state.owner) return;
    applyMusicSharedSnapshot(server);
    emit({ message: server.queue.length ? 'Requested albums refreshed. Choose Play when you are ready.' : 'Requested albums are still waiting for import.' });
  } catch (error) {
    if (current === generation && owner === state.owner) emit({ message: 'Requested albums could not refresh. Your saved queue is still available.' });
  }
}

export async function restoreMusicSharedSession(owner: string): Promise<void> {
  const current = generation;
  try {
    const server = await getMusicSharedSession();
    if (current !== generation || owner !== state.owner) return;
    if (sharedDirty && state.sharedRevision === server.revision) { await syncMusicSharedSession(); return; }
    if (sharedDirty && state.sharedRevision === null && server.revision === 0) { state = { ...state, sharedRevision: 0 }; await syncMusicSharedSession(); return; }
    // A pre-shared-session cache has no base revision, so it cannot replace an
    // established server queue. It is only migrated into the empty revision-0 row.
    if (sharedDirty && state.sharedRevision === null) { applyMusicSharedSnapshot(server); return; }
    if (sharedDirty && state.sharedRevision !== server.revision) { emit({ sharedConflict: server, message: 'Your saved queue differs from the server. Choose which queue to keep.' }); return; }
    applyMusicSharedSnapshot(server);
  } catch (error) {
    if (current === generation && owner === state.owner) {
      if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) stopForAccess(new Error('Sign in again to restore your music queue.'));
      else emit({ message: 'Music queue restore is unavailable. Your local queue is still available.' });
    }
  }
}

export async function syncMusicSharedSession(): Promise<void> {
  if (!state.owner || !navigator.onLine || accessStopped || state.sharedRevision === null) return sharedWrite ?? Promise.resolve();
  sharedQueued = true;
  if (sharedWrite) return sharedWrite;
  const run = async (): Promise<void> => {
    while (sharedQueued && state.owner && navigator.onLine && !accessStopped && state.sharedRevision !== null) {
      sharedQueued = false;
      const owner = state.owner, current = generation, payload = sharedPayload(state.sharedRevision);
      try {
        const saved = await putMusicSharedSession(payload);
        if (current !== generation || owner !== state.owner) return;
        const updatedUpcoming = applySharedUpcoming(saved);
        if (!updatedUpcoming && activeController()) {
          applyMusicSharedSnapshot(saved);
        } else {
          emit({ queue: saved.queue, repeat: saved.repeat, shuffle: saved.shuffle, sharedRevision: saved.revision, sharedConflict: null,
            sharedEntries: saved.entries ?? state.sharedEntries, sharedEntryIndex: saved.entry_index ?? state.sharedEntryIndex,
            unavailablePolicy: saved.unavailable_policy ?? state.unavailablePolicy,
            playback: state.playback });
        }
        sharedDirty = false;
        persist();
      } catch (error) {
        if (current !== generation || owner !== state.owner) return;
        const conflict = conflictSnapshot(error);
        if (conflict) emit({ sharedConflict: conflict, message: 'Your saved queue differs from the server. Choose which queue to keep.' });
        else if (error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) stopForAccess(error);
        else emit({ message: 'Music queue sync is unavailable. Changes stay on this device until reconnect.' });
        return;
      }
    }
  };
  sharedWrite = run().finally(() => { sharedWrite = null; });
  return sharedWrite;
}

function markSharedDirty(): void {
  if (adoptingShared) return;
  sharedDirty = true;
  persist();
  if (!outputTransfer) void syncMusicSharedSession();
}

export function useServerMusicSession(): void { if (state.sharedConflict) applyMusicSharedSnapshot(state.sharedConflict); }
export async function keepDeviceMusicSession(): Promise<void> {
  if (!state.sharedConflict) return;
  state = { ...state, sharedRevision: state.sharedConflict.revision, sharedConflict: null };
  sharedDirty = true;
  await syncMusicSharedSession();
}

/** The browser is already playing when this is called; only then may a remote ticket be revoked. */
export async function acknowledgeMusicBrowserOutput(ticket: string): Promise<void> {
  if (!state.owner || state.sharedRevision === null) return;
  const saved = await putMusicSharedSession({ ...sharedPayload(state.sharedRevision), acknowledged_output_ticket: ticket });
  emit({ sharedRevision: saved.revision, sharedConflict: null });
  sharedDirty = false;
  persist();
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

function entryForFlatIndex(entries: readonly MusicQueueEntry[], index: number): number | null {
  let offset = 0;
  for (let entryIndex = 0; entryIndex < entries.length; entryIndex++) {
    const count = entries[entryIndex].state === 'playable' ? entries[entryIndex].tracks?.length ?? (entries[entryIndex].kind === 'track' ? 1 : 0) : 0;
    if (index >= offset && index < offset + count) return entryIndex;
    offset += count;
  }
  return null;
}

function trackRefs(queue: readonly MusicTrack[]): MusicQueueEntry[] {
  return queue.map((track) => ({ kind: 'track' as const, item_id: track.id, state: 'playable' as const, tracks: [track] }));
}

function applySharedUpcoming(snapshot: MusicSharedSnapshot): boolean {
  const controller = activeController();
  const index = state.playback.index;
  if (!controller || index < 0 || snapshot.current_index === null || snapshot.queue[index]?.id !== state.queue[index]?.id) return false;
  try { controller.replaceUpcoming(snapshot.queue.map(sourceFor)); return true; }
  catch { return false; }
}

function playbackChanged(playback: MusicEngineState): void {
  const previous = state.playback;
  if (previous.index !== playback.index && previous.index >= 0) {
    const track = state.queue[previous.index];
    if (track) reportTrack(track, previous, completed(previous));
  }
  const current = state.queue[playback.index];
  if (state.owner && current) recordMusicListening(state.owner, current, playback);
  if (current && !completed(playback) && lastCompleted === completionKey(current)) lastCompleted = null;
  const entryIndex = entryForFlatIndex(state.sharedEntries, playback.index);
  emit({ playback, sharedEntryIndex: entryIndex ?? state.sharedEntryIndex });
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
  engine.setCrossfade(state.crossfadeSeconds);
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
  markSharedDirty();
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

async function startMusicQueue(queue: MusicTrack[], index: number, position: number, preserveLogical = false): Promise<void> {
  if (!queue.length) { emit({ message: 'No tracks are available in this queue.' }); return; }
  if (queueNeedsStreaming(queue)) {
    await startStreamQueue(queue, index, position, preserveLogical);
  } else {
    const player = primeEngine();
    emit({ queue, message: null, mode: 'gapless', sharedEntries: preserveLogical ? state.sharedEntries : trackRefs(queue), sharedEntryIndex: preserveLogical ? entryForFlatIndex(state.sharedEntries, index) : index });
    accessStopped = false;
    lastCompleted = null;
    await player.play(queue.map(sourceFor), index, position);
    applyGain();
    markSharedDirty();
  }
}

export async function playMusicQueue(queue: MusicTrack[], index = 0, position = 0): Promise<void> {
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  await startMusicQueue(queue, index, position);
}

export async function resumeMusicFromCast(queue: MusicTrack[], index: number, position: number): Promise<void> {
  const current = ++generation;
  outputTransfer = true;
  try {
    await startMusicQueue(queue, index, position, true);
    if (current !== generation) throw new Error('Browser playback was superseded before transfer completed.');
  } finally {
    outputTransfer = false;
  }
}

async function startStreamQueue(queue: MusicTrack[], index: number, position: number, preserveLogical = false): Promise<void> {
  if (!queue.length) { emit({ message: 'No tracks are available in this queue.' }); return; }
  const player = primeStream();
  emit({ queue, message: null, mode: 'streaming', sharedEntries: preserveLogical ? state.sharedEntries : trackRefs(queue), sharedEntryIndex: preserveLogical ? entryForFlatIndex(state.sharedEntries, index) : index });
  accessStopped = false;
  lastCompleted = null;
  await player.play(queue.map(sourceFor), index, position);
  applyGain();
  markSharedDirty();
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

export async function enqueueMusicRequest(owner: string, requestId: string): Promise<void> {
  if (owner !== state.owner || !/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(requestId)) return;
  state = { ...state, sharedEntries: [...state.sharedEntries, { kind: 'acquisition', request_id: requestId }], sharedEntryIndex: state.sharedEntryIndex ?? 0 };
  markSharedDirty();
}

export function setMusicUnavailablePolicy(policy: 'skip' | 'wait'): void {
  if (policy !== 'skip' && policy !== 'wait') return;
  emit({ unavailablePolicy: policy });
  markSharedDirty();
}

export const pauseMusic = (): void => { activeController()?.pause(); void checkpointMusic(); };
export async function toggleMusic(): Promise<void> {
  if (state.playback.playing) { pauseMusic(); return; }
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  const controller = activeController();
  if (controller && controller.getState().index >= 0) await controller.resume();
  else if (state.mode === 'streaming') await startStreamQueue(state.queue, Math.max(0, state.playback.index), state.playback.position, true);
  else await startMusicQueue(state.queue, Math.max(0, state.playback.index), state.playback.position, true);
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
export const setMusicCrossfade = (seconds: number): void => {
  const crossfadeSeconds = Number.isFinite(seconds) ? Math.max(0, Math.min(12, seconds)) : 0;
  emit({ crossfadeSeconds });
  engine?.setCrossfade(crossfadeSeconds);
};
export function setMusicSleepTimer(minutes: number | null): void {
  if (sleepTimer !== null) { globalThis.clearTimeout(sleepTimer); sleepTimer = null; }
  if (!state.owner || minutes === null || !Number.isFinite(minutes) || minutes <= 0) {
    emit({ sleepUntil: null });
    return;
  }
  const owner = state.owner;
  const sleepUntil = Date.now() + Math.min(minutes, 24 * 60) * 60_000;
  try { localStorage.setItem(`lumina:music:sleep:${owner}`, String(sleepUntil)); } catch { /* local preference only */ }
  sleepTimer = globalThis.setTimeout(() => {
    if (state.owner !== owner) return;
    pauseMusic();
    try { localStorage.removeItem(`lumina:music:sleep:${owner}`); } catch { /* local preference only */ }
    emit({ sleepUntil: null, message: 'Sleep timer paused browser playback.' });
  }, Math.max(0, sleepUntil - Date.now()));
  emit({ sleepUntil });
}
export const repeatMusic = (): void => {
  emit({ repeat: state.repeat === 'off' ? 'all' : state.repeat === 'all' ? 'one' : 'off' });
  activeController()?.setRepeat(state.repeat);
  markSharedDirty();
};
export async function closeMusic(): Promise<void> {
  const owner = state.owner;
  // Do not use pauseMusic here: its checkpoint can race the durable clear.
  activeController()?.pause();
  await sharedWrite?.catch(() => undefined);
  if (owner !== state.owner) return;
  generation += 1;
  engine?.dispose();
  stream?.dispose();
  engine = null;
  stream = null;
  context = null;
  emit({ queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', message: null, sharedEntries: [], sharedEntryIndex: null });
  markSharedDirty();
  await syncMusicSharedSession();
}
export function reorderMusic(from: number, to: number): void {
  if (from <= state.playback.index || to <= state.playback.index || to < 0 || to >= state.queue.length) return;
  const queue = [...state.queue];
  const [track] = queue.splice(from, 1);
  queue.splice(to, 0, track);
  if (state.sharedEntries.some((entry) => entry.kind !== 'track')) {
    emit({ message: 'Reorder tracks after requested albums finish importing or choose Skip.' });
    return;
  }
  const entries = state.sharedEntries.length ? [...state.sharedEntries] : trackRefs(state.queue);
  const [entry] = entries.splice(from, 1);
  entries.splice(to, 0, entry);
  activeController()?.replaceUpcoming(queue.map(sourceFor));
  emit({ queue, sharedEntries: entries, sharedEntryIndex: entryForFlatIndex(entries, state.playback.index) });
  markSharedDirty();
}
