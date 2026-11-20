import { useSyncExternalStore } from 'react';

import { ApiRequestError, getContextualMusicMix, getMusicQueue, updatePlaybackProgress } from '../../api';
import { shuffled } from '../gallery/albumQueue';
import { forgetMusicCast, musicCast, restoreSharedMusicCast, stopMusicCast } from './musicCastSession';
import { MAX_AUDIO_BYTES, MAX_DECODED_AUDIO_BYTES, MusicEngine, type MusicEngineState, type MusicRepeatMode, type MusicSource } from './musicEngine';
import { offlineTrack } from './musicOffline';
import { readMusicCheckpoint, storeMusicCheckpoint } from './musicCheckpoint';
import { recordMusicListening, setListeningExportOwner } from './musicListeningExport';
import { getMusicSharedSession, putMusicSharedSession, type MusicQueueEntry, type MusicSharedSnapshot } from './musicSharedSession';
import { MusicStream } from './musicStream';
import type { MusicTrack } from './musicTypes';
import type { MusicMixPreview } from '../../api';

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
  continuation: boolean;
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
let state: MusicSession = { owner: null, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', volume: 0.8, normalize: true, repeat: 'off', shuffle: false, sharedRevision: null, sharedConflict: null, crossfadeSeconds: 0, sleepUntil: null, message: null, sharedEntries: [], sharedEntryIndex: null, unavailablePolicy: 'skip', continuation: false };
let generation = 0;
let continuationIntent = 0;
let continuationArmed = false;
let continuationRequest: AbortController | null = null;
let recentRecommendations: string[] = [];
try { state.continuation = localStorage.getItem('lumina:music:continuation') === 'true'; } catch { /* device preference */ }
export function cancelMusicContinuation(): void {
  continuationIntent += 1;
  continuationArmed = false;
  continuationRequest?.abort();
  continuationRequest = null;
}
export function setMusicContinuation(enabled: boolean): void {
  if (!enabled) cancelMusicContinuation();
  else if (state.playback.playing && !musicCast()) continuationArmed = true;
  emit({ continuation: enabled });
  try { localStorage.setItem('lumina:music:continuation', String(enabled)); } catch { /* device preference */ }
}

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

function persist(localOutput = !musicCast() && !outputTransfer): void {
  if (!state.owner) return;
  storeMusicCheckpoint(state.owner, {
    queue: state.queue, index: state.playback.index, position: state.playback.position,
    revision: state.sharedRevision, repeat: state.repeat, shuffle: state.shuffle, dirty: sharedDirty,
    entries: state.sharedEntries, entryIndex: state.sharedEntryIndex, unavailablePolicy: state.unavailablePolicy,
    localOutput,
  });
}

export function setMusicOwner(owner: string | null): void {
  if (state.owner === owner) return;
  cancelMusicContinuation();
  recentRecommendations = [];
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
  state = { ...state, owner, queue: [], playback: EMPTY_PLAYBACK, mode: 'gapless', shuffle: false, sharedRevision: null, sharedConflict: null, sleepUntil: null, message: null, sharedEntries: [], sharedEntryIndex: null, unavailablePolicy: 'skip', continuation: false };
  if (owner) {
    const stored = readMusicCheckpoint(owner);
    if (stored) {
      state = {
        ...state, queue: stored.queue, repeat: stored.repeat === 'all' || stored.repeat === 'one' ? stored.repeat : 'off',
        shuffle: stored.shuffle === true, sharedRevision: Number.isInteger(stored.revision) ? Number(stored.revision) : null,
        sharedEntries: stored.entries ?? [], sharedEntryIndex: stored.entryIndex ?? null,
        unavailablePolicy: stored.unavailablePolicy === 'wait' ? 'wait' : 'skip',
        playback: { ...EMPTY_PLAYBACK, index: stored.index, position: stored.position },
      };
      // Cache entries from before shared sessions are a one-time ID-only migration.
      sharedDirty = stored.dirty === true || stored.revision === undefined;
    }
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
    entries: state.sharedEntries.length ? state.sharedEntries.map((entry) => entry.kind === 'track'
      ? { kind: 'track' as const, item_id: entry.item_id, ...(entry.provenance && { provenance: entry.provenance }) }
      : { kind: 'acquisition' as const, request_id: entry.request_id }) : undefined,
    entry_index: state.sharedEntryIndex, unavailable_policy: state.unavailablePolicy };
}

export function applyMusicSharedSnapshot(snapshot: MusicSharedSnapshot): void {
  // Canonical restore is deliberately paused. Dispose first so a later toggle
  // cannot resume a stale decoded queue after a conflict or an import refresh.
  cancelMusicContinuation();
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
  persist(snapshot.active_output.kind === 'browser');
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
  void reportProgress(track.id, { ...playback, playing: false }, complete);
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
  if (Date.now() - checkpointAt > 10_000 || previous.playing !== playback.playing || previous.index !== playback.index) void checkpointMusic();
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
    onNaturalEnd: continueAfterNaturalEnd,
  });
  engine.setVolume(state.volume);
  engine.setRepeat(state.repeat);
  engine.setCrossfade(state.crossfadeSeconds);
  return engine;
}

function primeStream(): MusicStream {
  if (engine) { engine.dispose(); engine = null; context = null; }
  if (!stream) stream = new MusicStream({ onChange: playbackChanged, onNaturalEnd: continueAfterNaturalEnd, onAccessDenied: stopForAccess });
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
    await updatePlaybackProgress(id, { position_seconds: Math.floor(playback.position), duration_seconds: Math.round(playback.duration), completed: complete, playing: playback.playing }, { keepalive: true });
  } catch (error) {
    if (owner === state.owner && error instanceof ApiRequestError && (error.status === 401 || error.status === 403)) stopForAccess(error);
  }
}

function stopForAccess(error: Error): void {
  cancelMusicContinuation();
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
  cancelMusicContinuation();
  continuationArmed = true;
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
  cancelMusicContinuation(); continuationArmed = true;
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  await startMusicQueue(queue, index, position);
}

export async function resumeMusicFromCast(queue: MusicTrack[], index: number, position: number): Promise<void> {
  cancelMusicContinuation();
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
  cancelMusicContinuation(); continuationArmed = true;
  const owner = state.owner;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  await startStreamQueue(queue, index, position);
}

export async function streamMusicCurrent(): Promise<void> {
  await streamMusicQueue(state.queue, Math.max(0, state.playback.index), state.playback.position);
}

/** A callback from the playback engine, never from pause, manual Next or restore. */
function continueAfterNaturalEnd(): void {
  const owner = state.owner, intent = continuationIntent, current = generation;
  const valid = () => Boolean(owner && owner === state.owner && intent === continuationIntent && current === generation
    && continuationArmed && state.continuation && navigator.onLine && !accessStopped && !state.sharedConflict
    && !musicCast() && state.repeat === 'off' && !state.playback.playing && !state.playback.loading
    && !(state.unavailablePolicy === 'wait' && state.sharedEntries.some((entry) => entry.kind === 'acquisition' && entry.state !== 'playable')));
  const atEnd = () => valid() && state.playback.index === state.queue.length - 1;
  if (!atEnd()) return;
  const anchor = state.queue[state.playback.index];
  if (!anchor) return;
  const controller = new AbortController();
  continuationRequest?.abort(); continuationRequest = controller;
  void (async () => {
    try {
      if (sharedWrite) await sharedWrite;
      if (!atEnd() || state.sharedRevision === null || sharedDirty) return;
      const revision = state.sharedRevision;
      const excluded = [...new Set([...state.queue.map((track) => track.id), ...recentRecommendations])].slice(-100);
      const preview = await getContextualMusicMix('track', anchor.id, excluded, controller.signal);
      if (!atEnd() || state.sharedRevision !== revision || controller.signal.aborted) return;
      const items = preview.items.slice(0, 12);
      if (!items.length) return;
      await appendMusicMix(owner!, items, { revision, valid: atEnd, signal: controller.signal });
      if (!valid() || controller.signal.aborted || state.sharedConflict || state.sharedRevision === revision) return;
      recentRecommendations = [...new Set([...recentRecommendations, ...items.map((item) => item.id)])].slice(-100);
      await activeController()?.next();
    } catch { /* timeout, conflict and offline end the queue normally */ }
    finally { if (continuationRequest === controller) continuationRequest = null; }
  })();
}

/** Remove only generated entries after the selected logical occurrence. */
export async function removeUpcomingMusicRecommendations(): Promise<void> {
  const owner = state.owner;
  if (!owner || musicCast()?.owner === owner) throw new Error('Return playback to this browser before changing the queue.');
  if (sharedWrite) await sharedWrite;
  if (owner !== state.owner || state.sharedRevision === null || state.sharedConflict || !navigator.onLine) throw new Error('Restore your shared queue before removing recommendations.');
  cancelMusicContinuation();
  const current = generation, revision = state.sharedRevision, payload = sharedPayload(revision);
  const entries = payload.entries ?? [];
  const selected = payload.entry_index ?? -1;
  const retained = entries.filter((entry, index) => index <= selected || entry.kind !== 'track' || !entry.provenance);
  if (retained.length === entries.length) return;
  const run = async () => {
    try {
      const saved = await putMusicSharedSession({ ...payload, entries: retained });
      if (current !== generation || owner !== state.owner || revision !== state.sharedRevision) return;
      if (activeController() && !applySharedUpcoming(saved)) throw new Error('The queue changed while removing tracks. Refresh your queue.');
      emit({ queue: saved.queue, sharedRevision: saved.revision, sharedEntries: saved.entries ?? [], sharedEntryIndex: saved.entry_index ?? null });
      persist();
    } catch (error) {
      const conflict = conflictSnapshot(error);
      if (conflict && current === generation && owner === state.owner) emit({ sharedConflict: conflict, message: 'Your saved queue differs from the server. Choose which queue to keep.' });
      throw error;
    }
  };
  sharedWrite = run().finally(() => { sharedWrite = null; if (sharedQueued && !state.sharedConflict) void syncMusicSharedSession(); });
  await sharedWrite;
}

/** Append a generated batch against the current revision without disturbing the active occurrence. */
export async function appendMusicMix(owner: string, items: MusicMixPreview['items'], guard?: { revision: number; valid: () => boolean; signal: AbortSignal }): Promise<void> {
  if (!items.length) return;
  if (owner !== state.owner || musicCast()?.owner === owner) throw new Error('Return playback to this browser before changing the queue.');
  if (sharedWrite) await sharedWrite;
  if (guard && (!guard.valid() || guard.revision !== state.sharedRevision)) return;
  if (owner !== state.owner || state.sharedRevision === null || state.sharedConflict || !navigator.onLine) throw new Error('Restore your shared queue before adding tracks.');
  const current = generation, revision = state.sharedRevision;
  const payload = sharedPayload(revision);
  const entries = payload.entries ?? state.queue.map((track) => ({ kind: 'track' as const, item_id: track.id }));
  const existing = new Set(entries.filter((entry) => entry.kind === 'track').map((entry) => entry.item_id));
  const additions = items.filter((item) => !existing.has(item.id)).map((item) => ({ kind: 'track' as const, item_id: item.id, provenance: { source: 'contextual_mix' as const, reason: item.reason.code } }));
  if (!additions.length) return;
  const run = async () => {
    try {
      const write = { ...payload, entries: [...entries, ...additions], entry_index: payload.entry_index ?? payload.current_index };
      const saved = guard ? await putMusicSharedSession(write, guard.signal) : await putMusicSharedSession(write);
      if (current !== generation || owner !== state.owner || revision !== state.sharedRevision || (guard && !guard.valid())) return;
      if (activeController() && !applySharedUpcoming(saved)) throw new Error('The queue changed while adding tracks. Refresh your queue.');
      emit({ queue: saved.queue, sharedRevision: saved.revision, sharedEntries: saved.entries ?? [], sharedEntryIndex: saved.entry_index ?? null, unavailablePolicy: saved.unavailable_policy ?? state.unavailablePolicy });
      if (!sharedQueued) sharedDirty = false;
      persist();
    } catch (error) {
      if (current !== generation || owner !== state.owner) return;
      const conflict = conflictSnapshot(error);
      if (conflict) emit({ sharedConflict: conflict, message: 'Your saved queue differs from the server. Choose which queue to keep.' });
      throw error;
    }
  };
  const operation = run();
  sharedWrite = operation.finally(() => { sharedWrite = null; if (sharedQueued && !state.sharedConflict) void syncMusicSharedSession(); });
  await sharedWrite;
}

/** Start exactly the previewed order, retaining the generated reason on each entry. */
export async function playMusicMix(owner: string, items: MusicMixPreview['items']): Promise<void> {
  if (!items.length) return;
  if (owner !== state.owner || musicCast()?.owner === owner) throw new Error('Return playback to this browser before changing the queue.');
  if (sharedWrite) await sharedWrite;
  if (owner !== state.owner || state.sharedRevision === null || state.sharedConflict) throw new Error('Restore your shared queue before playing a mix.');
  cancelMusicContinuation(); continuationArmed = true;
  const current = ++generation;
  const refs: MusicQueueEntry[] = items.map((item) => ({ kind: 'track', item_id: item.id, state: 'playable', tracks: [item], provenance: { source: 'contextual_mix', reason: item.reason.code } }));
  if (current !== generation || owner !== state.owner) return;
  emit({ sharedEntries: refs, sharedEntryIndex: 0 });
  await startMusicQueue(items, 0, 0, true);
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

export const pauseMusic = (): void => { cancelMusicContinuation(); activeController()?.pause(); void checkpointMusic(); };
export async function toggleMusic(): Promise<void> {
  if (state.playback.playing) { pauseMusic(); return; }
  const owner = state.owner;
  cancelMusicContinuation(); continuationArmed = true;
  const current = ++generation;
  if (!await stopCastForBrowserPlayback(owner, () => current === generation && state.owner === owner)) return;
  const controller = activeController();
  if (controller && controller.getState().index >= 0) await controller.resume();
  else if (state.mode === 'streaming') await startStreamQueue(state.queue, Math.max(0, state.playback.index), state.playback.position, true);
  else await startMusicQueue(state.queue, Math.max(0, state.playback.index), state.playback.position, true);
}
export const seekMusic = async (position: number): Promise<void> => { const playing = state.playback.playing; cancelMusicContinuation(); continuationArmed = playing; await (state.mode === 'streaming' ? primeStream() : primeEngine()).seek(position); };
export const stepMusic = async (direction: 1 | -1): Promise<void> => {
  cancelMusicContinuation(); continuationArmed = true;
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
  const playing = state.playback.playing;
  cancelMusicContinuation(); continuationArmed = playing;
  emit({ repeat: state.repeat === 'off' ? 'all' : state.repeat === 'all' ? 'one' : 'off' });
  activeController()?.setRepeat(state.repeat);
  markSharedDirty();
};
export async function closeMusic(): Promise<void> {
  cancelMusicContinuation();
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
