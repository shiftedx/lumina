import type { MusicLyrics, MusicTrack } from './musicTypes';

export interface OfflineTrack {
  key: string;
  owner: string;
  track: MusicTrack;
  audio: ArrayBuffer;
  artwork: string | null;
  lyrics?: MusicLyrics | null;
  savedAt: number;
}

export const OFFLINE_DATABASE_NAME = 'lumina-music-offline';
export const OFFLINE_STORE_NAME = 'tracks';
export const MAX_OFFLINE_AUDIO_BYTES = 128 * 1024 * 1024;
export const MAX_OFFLINE_ARTWORK_BYTES = 2 * 1024 * 1024;
export const OFFLINE_SHELL_MESSAGE = 'CACHE_OFFLINE_SHELL';
export const OFFLINE_SHELL_READY = 'OFFLINE_SHELL_READY';
export const OFFLINE_SHELL_ERROR = 'OFFLINE_SHELL_ERROR';

const LAST_OWNER_KEY = 'lumina:music:offline-owner';
const SHELL_ACK_TIMEOUT_MS = 20_000;

function abortError(): Error {
  if (typeof DOMException !== 'undefined') return new DOMException('Offline download cancelled.', 'AbortError');
  const error = new Error('Offline download cancelled.');
  error.name = 'AbortError';
  return error;
}

function assertNotAborted(signal: AbortSignal): void {
  if (signal.aborted) throw abortError();
}

function isAbortError(error: unknown): boolean {
  return (typeof DOMException !== 'undefined' && error instanceof DOMException && error.name === 'AbortError')
    || (error instanceof Error && error.name === 'AbortError');
}

function storageError(error: unknown, fallback: string): Error {
  const name = typeof DOMException !== 'undefined' && error instanceof DOMException ? error.name : error instanceof Error ? error.name : '';
  if (name === 'QuotaExceededError') return new Error('Offline storage quota exceeded. Free browser storage and try again.');
  if (error instanceof Error && error.message) return new Error(error.message);
  return new Error(fallback);
}

async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(OFFLINE_DATABASE_NAME, 1);
    } catch (error) {
      reject(storageError(error, 'Offline storage is unavailable.'));
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      const transaction = request.transaction;
      const store = db.objectStoreNames.contains(OFFLINE_STORE_NAME)
        ? transaction?.objectStore(OFFLINE_STORE_NAME)
        : db.createObjectStore(OFFLINE_STORE_NAME, { keyPath: 'key' });
      if (store && !store.indexNames.contains('owner')) store.createIndex('owner', 'owner');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(storageError(request.error, 'Offline storage is unavailable.'));
    request.onblocked = () => reject(new Error('Offline storage is busy. Close another Lumina tab and try again.'));
  });
}

async function read<T>(run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await database();
  try {
    return await new Promise<T>((resolve, reject) => {
      let transaction: IDBTransaction;
      try {
        transaction = db.transaction(OFFLINE_STORE_NAME, 'readonly');
        const request = run(transaction.objectStore(OFFLINE_STORE_NAME));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(storageError(request.error, 'Offline music could not be read.'));
        transaction.onerror = () => reject(storageError(transaction.error, 'Offline music could not be read.'));
        transaction.onabort = () => reject(storageError(transaction.error, 'Offline music could not be read.'));
      } catch (error) {
        reject(storageError(error, 'Offline music could not be read.'));
      }
    });
  } finally {
    db.close();
  }
}

async function write(run: (store: IDBObjectStore) => void): Promise<void> {
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      let transaction: IDBTransaction;
      try {
        transaction = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
        run(transaction.objectStore(OFFLINE_STORE_NAME));
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(storageError(transaction.error, 'Offline music could not be saved.'));
        transaction.onabort = () => reject(storageError(transaction.error, 'Offline music could not be saved.'));
      } catch (error) {
        reject(storageError(error, 'Offline music could not be saved.'));
      }
    });
  } finally {
    db.close();
  }
}

function requireOwner(owner: string): void {
  if (!owner.trim()) throw new Error('An account is required to use offline music.');
}

function setLastOfflineOwner(owner: string): void {
  try { localStorage.setItem(LAST_OWNER_KEY, owner); } catch { /* a blocked localStorage does not make saved audio unusable */ }
}

function clearLastOfflineOwner(owner: string): void {
  try {
    if (localStorage.getItem(LAST_OWNER_KEY) === owner) localStorage.removeItem(LAST_OWNER_KEY);
  } catch { /* a blocked localStorage does not make saved audio unusable */ }
}

export function lastOfflineOwner(): string | null {
  try { return localStorage.getItem(LAST_OWNER_KEY); } catch { return null; }
}

export async function offlineOwners(): Promise<string[]> {
  const entries = await read<OfflineTrack[]>((store) => store.getAll());
  return [...new Set(entries.map((entry) => entry.owner).filter((owner) => typeof owner === 'string' && owner.length > 0))].sort();
}

export async function offlineTracks(owner: string): Promise<OfflineTrack[]> {
  requireOwner(owner);
  return (await read<OfflineTrack[]>((store) => store.index('owner').getAll(owner))).sort((a, b) => b.savedAt - a.savedAt);
}

export const offlineTrack = (owner: string, id: string): Promise<OfflineTrack | undefined> => {
  requireOwner(owner);
  return read((store) => store.get(`${owner}:${id}`));
};

export async function removeOfflineTrack(owner: string, id: string): Promise<void> {
  requireOwner(owner);
  await write((store) => { store.delete(`${owner}:${id}`); });
  if (!(await offlineTracks(owner)).length) clearLastOfflineOwner(owner);
}

export async function clearOfflineMusic(owner: string): Promise<void> {
  requireOwner(owner);
  const entries = await offlineTracks(owner);
  await write((store) => entries.forEach((entry) => store.delete(entry.key)));
  clearLastOfflineOwner(owner);
}

type DownloadedBody = { bytes: ArrayBuffer; contentType: string };
type OfflineAudioDelivery = DownloadedBody & { codec: string | null; sampleRate: number | null; bitDepth: number | null; channels: number | null };

const PRIVATE_QUERY_KEY = /^(?:token|auth|credential|cookie|session|ticket|sig|signature|secret|password|api[_-]?key|jwt)$/i;
const SERVER_FALLBACK_CODECS = new Set(['aiff', 'aif', 'aifc', 'alac', 'dsd', 'dsf', 'dff']);

function tooLarge(label: string, maxBytes: number): Error {
  const maxMiB = Math.round(maxBytes / 1024 / 1024);
  return new Error(`${label} is larger than the ${maxMiB} MiB offline limit.`);
}

async function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
  try { await reader.cancel(); } catch { /* the fetch abort or stream close is already the useful outcome */ }
}

async function boundedResponse(url: string, signal: AbortSignal, maxBytes: number, label: string): Promise<DownloadedBody> {
  assertNotAborted(signal);
  const response = await fetch(url, { credentials: 'same-origin', signal });
  assertNotAborted(signal);
  if (!response.ok) {
    try { await response.body?.cancel(); } catch { /* the HTTP status is the useful error */ }
    throw new Error(`${label} could not be downloaded (HTTP ${response.status}).`);
  }

  const contentLength = Number(response.headers.get('content-length'));
  if (Number.isFinite(contentLength) && contentLength > maxBytes) {
    try { await response.body?.cancel(); } catch { /* the size error is the useful outcome */ }
    throw tooLarge(label, maxBytes);
  }

  if (!response.body) {
    const bytes = await response.arrayBuffer();
    assertNotAborted(signal);
    if (bytes.byteLength > maxBytes) throw tooLarge(label, maxBytes);
    return { bytes, contentType: response.headers.get('content-type') || '' };
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let finished = false;
  const cancelOnAbort = () => { void cancelReader(reader); };
  signal.addEventListener('abort', cancelOnAbort, { once: true });
  try {
    while (true) {
      assertNotAborted(signal);
      const result = await reader.read();
      if (result.done) {
        finished = true;
        break;
      }
      if (!result.value) continue;
      total += result.value.byteLength;
      if (total > maxBytes) {
        await cancelReader(reader);
        finished = true;
        throw tooLarge(label, maxBytes);
      }
      chunks.push(result.value.slice());
    }
  } catch (error) {
    if (signal.aborted && !isAbortError(error)) throw abortError();
    throw error;
  } finally {
    signal.removeEventListener('abort', cancelOnAbort);
    if (!finished) await cancelReader(reader);
    try { reader.releaseLock(); } catch { /* already released by the stream */ }
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { bytes: bytes.buffer, contentType: response.headers.get('content-type') || '' };
}

async function artworkData(url: string, signal: AbortSignal): Promise<string | null> {
  try {
    const result = await boundedResponse(url, signal, MAX_OFFLINE_ARTWORK_BYTES, 'Artwork');
    if (!result.contentType.toLowerCase().split(';', 1)[0].startsWith('image/')) return null;
    const blob = new Blob([result.bytes], { type: result.contentType });
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error('Artwork could not be saved.'));
      reader.readAsDataURL(blob);
    });
  } catch (error) {
    if (signal.aborted || isAbortError(error)) throw abortError();
    return null; // Artwork is optional; an unavailable cover must not lose the audio.
  }
}

function normalizedCodec(codec: string | null | undefined): string {
  return (codec || '').trim().toLowerCase().replace(/^audio\//, '');
}

function playableMime(track: MusicTrack, contentType: string): string {
  const declared = contentType.toLowerCase().split(';', 1)[0].trim();
  if (declared.startsWith('audio/') && declared !== 'audio/octet-stream') return declared;
  const codec = normalizedCodec(track.codec);
  if (codec.includes('flac')) return 'audio/flac';
  if (codec.includes('opus') || codec.includes('vorbis') || codec === 'ogg') return 'audio/ogg';
  if (codec.includes('mp3') || codec.includes('mpeg')) return 'audio/mpeg';
  if (codec.includes('aac') || codec.includes('m4a')) return 'audio/mp4';
  if (codec.includes('pcm') || codec.includes('wav')) return 'audio/wav';
  return declared;
}

function recognizableAudio(bytes: ArrayBuffer): boolean {
  const data = new Uint8Array(bytes, 0, Math.min(bytes.byteLength, 16));
  const ascii = (start: number, length: number) => String.fromCharCode(...data.slice(start, start + length));
  if (data.length >= 12 && ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WAVE') return true;
  if (data.length >= 4 && ['fLaC', 'OggS'].includes(ascii(0, 4))) return true;
  if (data.length >= 4 && data[0] === 0x1a && data[1] === 0x45 && data[2] === 0xdf && data[3] === 0xa3) return true;
  if (data.length >= 12 && ascii(4, 4) === 'ftyp') return true;
  if (data.length >= 3 && ascii(0, 3) === 'ID3') return true;
  return data.length >= 2 && data[0] === 0xff && (data[1] & 0xe0) === 0xe0;
}

function assertBrowserPlayable(track: MusicTrack, downloaded: DownloadedBody): void {
  if (!recognizableAudio(downloaded.bytes)) throw new Error('Downloaded audio is malformed or uses an unrecognized container.');
  const mime = playableMime(track, downloaded.contentType);
  const audio = document.createElement('audio');
  if (!mime || audio.canPlayType(mime) === '') throw new Error(`This browser cannot play the saved ${track.codec || 'audio'} source.`);
}

function parseWavDelivery(downloaded: DownloadedBody): OfflineAudioDelivery {
  const view = new DataView(downloaded.bytes);
  const data = new Uint8Array(downloaded.bytes);
  const ascii = (start: number, length: number) => String.fromCharCode(...data.slice(start, start + length));
  if (data.byteLength < 44 || ascii(0, 4) !== 'RIFF' || ascii(8, 4) !== 'WAVE') {
    throw new Error('The compatible WAV download is malformed.');
  }
  let offset = 12;
  while (offset + 8 <= view.byteLength) {
    const chunk = ascii(offset, 4);
    const size = view.getUint32(offset + 4, true);
    if (chunk === 'fmt ' && size >= 16 && offset + 8 + size <= view.byteLength) {
      const payload = offset + 8;
      const formatTag = view.getUint16(payload, true);
      const channels = view.getUint16(offset + 10, true);
      const sampleRate = view.getUint32(offset + 12, true);
      const bitDepth = view.getUint16(offset + 22, true);
      if (!channels || !sampleRate || !bitDepth) break;
      let pcmFormat = formatTag;
      if (formatTag === 0xfffe) {
        if (size < 40 || view.getUint16(payload + 16, true) < 22) break;
        pcmFormat = view.getUint16(payload + 24, true);
      }
      const integerPcm = pcmFormat === 1 && [16, 24, 32].includes(bitDepth);
      const floatPcm = pcmFormat === 3 && bitDepth === 32;
      if (!integerPcm && !floatPcm) break;
      const codec = floatPcm ? 'pcm_f32le' : `pcm_s${bitDepth}le`;
      return { ...downloaded, codec, sampleRate, bitDepth, channels };
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error('The compatible WAV download has no valid PCM format metadata.');
}

function audioDownloadUrl(track: MusicTrack, fallback: boolean): string {
  const url = new URL(track.audio_url, window.location.origin);
  if (url.origin !== window.location.origin || url.username || url.password || url.hash
    || !/^\/api\/music\/tracks\/[^/]+\/audio$/.test(url.pathname)
    || [...url.searchParams.keys()].some((key) => PRIVATE_QUERY_KEY.test(key))) {
    throw new Error('Offline audio must use a private, same-origin music URL without credentials.');
  }
  const safe = new URL(url.pathname, window.location.origin);
  if (fallback) safe.searchParams.set('format', 'wav');
  safe.searchParams.set('download', 'true');
  return safe.pathname + safe.search;
}

async function offlineAudio(track: MusicTrack, signal: AbortSignal): Promise<OfflineAudioDelivery> {
  const sourceCodec = normalizedCodec(track.codec);
  let nativeFailure: unknown;
  if (!SERVER_FALLBACK_CODECS.has(sourceCodec)) {
    const native = await boundedResponse(audioDownloadUrl(track, false), signal, MAX_OFFLINE_AUDIO_BYTES, 'This track');
    try {
      assertBrowserPlayable(track, native);
      return { ...native, codec: track.codec, sampleRate: track.sample_rate, bitDepth: track.bit_depth, channels: track.channels };
    } catch (error) {
      if (signal.aborted || isAbortError(error)) throw abortError();
      nativeFailure = error;
      // The current browser rejected this source. Save it only if the server supplies
      // a bounded PCM WAV replacement that passes the same local validation.
    }
  }
  try {
    const fallback = await boundedResponse(audioDownloadUrl(track, true), signal, MAX_OFFLINE_AUDIO_BYTES, 'Compatible audio');
    const delivery = parseWavDelivery(fallback);
    assertBrowserPlayable({ ...track, codec: delivery.codec }, fallback);
    return delivery;
  } catch (error) {
    if (signal.aborted || isAbortError(error)) throw abortError();
    if (nativeFailure) throw new Error(`${nativeFailure instanceof Error ? nativeFailure.message : 'The source audio is unsupported'} Compatible fallback failed: ${error instanceof Error ? error.message : 'unknown audio error'}`);
    throw error;
  }
}

function offlineManifest(track: MusicTrack, delivery: OfflineAudioDelivery): MusicTrack {
  // The saved bytes are the source of truth. Keep the IDB manifest useful for the local session while
  // discarding any query string or remote URL that could carry a credential, ticket, or signed-art URL.
  return {
    ...track,
    audio_url: `/api/music/tracks/${encodeURIComponent(track.id)}/audio`,
    artwork_url: null,
    codec: delivery.codec,
    sample_rate: delivery.sampleRate,
    bit_depth: delivery.bitDepth,
    channels: delivery.channels,
  };
}

export async function saveOfflineTrack(owner: string, track: MusicTrack, signal: AbortSignal, lyrics?: MusicLyrics | null): Promise<void> {
  requireOwner(owner);
  assertNotAborted(signal);
  const delivery = await offlineAudio(track, signal);
  const audio = delivery.bytes;
  let artwork: string | null = null;
  if (track.artwork_url) artwork = await artworkData(track.artwork_url, signal);
  assertNotAborted(signal);

  try {
    const estimate = await navigator.storage?.estimate?.();
    const artworkBytes = artwork ? Math.ceil(artwork.length * 0.75) : 0;
    const required = audio.byteLength + artworkBytes;
    if (estimate?.quota !== undefined && Number.isFinite(estimate.quota) && estimate.quota - (estimate.usage ?? 0) < required) {
      throw new Error('There is not enough offline storage for this track.');
    }
  } catch (error) {
    if (error instanceof Error && error.message === 'There is not enough offline storage for this track.') throw error;
    // StorageManager.estimate is advisory and may be unavailable; IndexedDB remains the final quota check.
  }

  assertNotAborted(signal);
  const savedTrack = offlineManifest(track, delivery);
  await write((store) => store.put({
    key: `${owner}:${track.id}`,
    owner,
    track: savedTrack,
    audio,
    artwork,
    lyrics: lyrics ?? null,
    savedAt: Date.now(),
  } satisfies OfflineTrack));
  if (signal.aborted) {
    await write((store) => store.delete(`${owner}:${track.id}`));
    throw abortError();
  }
  setLastOfflineOwner(owner);
}

function shellMessageError(data: unknown): Error {
  if (data && typeof data === 'object' && 'message' in data && typeof data.message === 'string' && data.message) return new Error(data.message);
  return new Error('The offline listening page could not be prepared.');
}

async function awaitShellAck(worker: ServiceWorker): Promise<void> {
  const serviceWorkers = navigator.serviceWorker;
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let channel: MessageChannel | null = null;
    let finish: (error?: Error) => void;
    const timeout = window.setTimeout(() => finish(new Error('The offline listening page did not become ready in time.')), SHELL_ACK_TIMEOUT_MS);
    const handle = (data: unknown) => {
      if (!data || typeof data !== 'object' || !('type' in data)) return;
      if (data.type === OFFLINE_SHELL_READY) finish();
      else if (data.type === OFFLINE_SHELL_ERROR) finish(shellMessageError(data));
    };
    const onMessage = (event: MessageEvent) => handle(event.data);
    finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      serviceWorkers.removeEventListener('message', onMessage);
      channel?.port1.close();
      if (error) reject(error); else resolve();
    };
    serviceWorkers.addEventListener('message', onMessage);
    channel = typeof MessageChannel === 'undefined' ? null : new MessageChannel();
    if (channel) channel.port1.onmessage = (event) => handle(event.data);
    try {
      if (channel) worker.postMessage({ type: OFFLINE_SHELL_MESSAGE }, [channel.port2]);
      else worker.postMessage({ type: OFFLINE_SHELL_MESSAGE });
    } catch (error) {
      finish(storageError(error, 'The offline listening page could not be prepared.'));
    }
  });
}

export async function enableOfflineShell(): Promise<void> {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) throw new Error('Offline listening needs HTTPS or localhost in this browser.');
  const registration = await navigator.serviceWorker.register('/music-sw.js', { scope: '/' });
  if (typeof registration.update === 'function') await registration.update().catch(() => undefined);
  const ready = await navigator.serviceWorker.ready;
  const worker = ready.active ?? registration.active ?? navigator.serviceWorker.controller;
  if (!worker) throw new Error('The offline listening service is not active yet. Reload this page and try again.');
  await awaitShellAck(worker);
}
