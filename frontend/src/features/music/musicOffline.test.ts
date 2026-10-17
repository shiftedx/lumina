import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MusicTrack } from './musicTypes';
import {
  MAX_OFFLINE_AUDIO_BYTES, OFFLINE_DATABASE_NAME, OFFLINE_STORE_NAME, clearOfflineMusic, enableOfflineShell,
  lastOfflineOwner, offlineOwners, offlineTrack, offlineTracks, removeOfflineTrack, saveOfflineTrack,
} from './musicOffline';

type Stored = Record<string, unknown> & { key: string; owner: string };

class FakeRequest<T> {
  public result!: T;
  public error: DOMException | null = null;
  public onsuccess: ((event: Event) => void) | null = null;
  public onerror: ((event: Event) => void) | null = null;
  public onupgradeneeded: ((event: Event) => void) | null = null;
  public onblocked: ((event: Event) => void) | null = null;
  public transaction: IDBTransaction | null = null;

  public succeed(result: T): void {
    this.result = result;
    queueMicrotask(() => this.onsuccess?.(new Event('success')));
  }
}

class FakeTransaction {
  public error: DOMException | null = null;
  public oncomplete: (() => void) | null = null;
  public onerror: (() => void) | null = null;
  public onabort: (() => void) | null = null;
  public constructor(private readonly database: FakeDatabase) {}
  public objectStore(name: string): IDBObjectStore { return this.database.store(name) as unknown as IDBObjectStore; }
  public complete(): void { queueMicrotask(() => this.oncomplete?.()); }
}

class FakeIndex {
  public constructor(private readonly values: Map<string, Stored>) {}
  public getAll(owner: string): IDBRequest<Stored[]> {
    const request = new FakeRequest<Stored[]>();
    request.succeed([...this.values.values()].filter((entry) => entry.owner === owner));
    return request as unknown as IDBRequest<Stored[]>;
  }
}

class FakeStore {
  public readonly indexNames = { contains: (name: string) => name === 'owner' } as unknown as DOMStringList;
  public constructor(private readonly values: Map<string, Stored>, private readonly transaction: FakeTransaction) {}
  public createIndex(): IDBIndex { return new FakeIndex(this.values) as unknown as IDBIndex; }
  public index(name: string): IDBIndex {
    if (name !== 'owner') throw new Error(`Unknown index ${name}`);
    return new FakeIndex(this.values) as unknown as IDBIndex;
  }
  public get(key: string): IDBRequest<Stored | undefined> {
    const request = new FakeRequest<Stored | undefined>();
    request.succeed(this.values.get(key));
    return request as unknown as IDBRequest<Stored | undefined>;
  }
  public getAll(): IDBRequest<Stored[]> {
    const request = new FakeRequest<Stored[]>();
    request.succeed([...this.values.values()]);
    return request as unknown as IDBRequest<Stored[]>;
  }
  public put(value: Stored): IDBRequest<unknown> {
    this.values.set(value.key, value);
    const request = new FakeRequest<unknown>();
    request.succeed(value.key);
    this.transaction.complete();
    return request as unknown as IDBRequest<unknown>;
  }
  public delete(key: string): IDBRequest<unknown> {
    this.values.delete(key);
    const request = new FakeRequest<unknown>();
    request.succeed(undefined);
    this.transaction.complete();
    return request as unknown as IDBRequest<unknown>;
  }
}

class FakeDatabase {
  public readonly objectStoreNames = { contains: (name: string) => name === OFFLINE_STORE_NAME } as unknown as DOMStringList;
  private readonly values = new Map<string, Stored>();
  public store(name: string): FakeStore {
    if (name !== OFFLINE_STORE_NAME) throw new Error(`Unknown store ${name}`);
    return new FakeStore(this.values, new FakeTransaction(this));
  }
  public createObjectStore(): FakeStore { return this.store(OFFLINE_STORE_NAME); }
  public transaction(name: string): IDBTransaction {
    if (name !== OFFLINE_STORE_NAME) throw new Error(`Unknown store ${name}`);
    const transaction = new FakeTransaction(this);
    const store = new FakeStore(this.values, transaction);
    (transaction as FakeTransaction & { objectStore: (value: string) => IDBObjectStore }).objectStore = () => store as unknown as IDBObjectStore;
    return transaction as unknown as IDBTransaction;
  }
  public close(): void {}
}

class FakeIndexedDB {
  private readonly databases = new Map<string, FakeDatabase>();
  public open(name: string): IDBOpenDBRequest {
    const request = new FakeRequest<IDBDatabase>();
    queueMicrotask(() => {
      let database = this.databases.get(name);
      const fresh = !database;
      if (!database) {
        database = new FakeDatabase();
        this.databases.set(name, database);
      }
      request.result = database as unknown as IDBDatabase;
      if (fresh) request.onupgradeneeded?.(new Event('upgradeneeded'));
      queueMicrotask(() => request.onsuccess?.(new Event('success')));
    });
    return request as unknown as IDBOpenDBRequest;
  }
  public clear(): void { this.databases.clear(); }
}

function track(id: string): MusicTrack {
  return {
    id, title: `Track ${id}`, artist: 'Offline Artist', album_artist: 'Offline Artist', album: 'Offline Album', album_id: null,
    artist_id: null, number: 1, disc: 1, duration: 90, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`,
    codec: 'opus', sample_rate: 48_000, bit_depth: null, channels: 2, gain_db: null, genres: [], edited: false,
  };
}

function webmBytes(): Uint8Array {
  return new Uint8Array([0x1a, 0x45, 0xdf, 0xa3, 0x01, 0x02]);
}

function wavBytes(sampleRate = 96_000, channels = 2, bitDepth = 24, formatTag = 1, subtype?: number): Uint8Array {
  const fmtSize = formatTag === 0xfffe ? 40 : 16;
  const dataOffset = 20 + fmtSize;
  const bytes = new Uint8Array(dataOffset + 8);
  const view = new DataView(bytes.buffer);
  bytes.set(new TextEncoder().encode('RIFF'), 0);
  view.setUint32(4, bytes.byteLength - 8, true);
  bytes.set(new TextEncoder().encode('WAVEfmt '), 8);
  view.setUint32(16, fmtSize, true);
  view.setUint16(20, formatTag, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * bitDepth / 8, true);
  view.setUint16(32, channels * bitDepth / 8, true);
  view.setUint16(34, bitDepth, true);
  if (formatTag === 0xfffe) {
    view.setUint16(36, 22, true);
    view.setUint16(38, bitDepth, true);
    view.setUint16(44, subtype ?? 1, true);
  }
  bytes.set(new TextEncoder().encode('data'), dataOffset);
  return bytes;
}

function audioResponse(bytes = webmBytes(), contentType = 'audio/webm'): Response {
  return new Response(Uint8Array.from(bytes).buffer, { status: 200, headers: { 'Content-Type': contentType } });
}

const indexed = new FakeIndexedDB();

beforeEach(() => {
  indexed.clear();
  Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: indexed });
  vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => audioResponse()));
  Object.defineProperty(navigator, 'storage', {
    configurable: true,
    value: { estimate: vi.fn(async () => ({ quota: 1024 * 1024 * 1024, usage: 0 })) },
  });
  vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('probably');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('music offline storage', () => {
  it('keeps tracks and cached lyrics isolated by owner and exposes owner listing', async () => {
    const lyrics = { plain: 'One two three', cues: [{ start: 0, text: 'One two three' }], source: 'local', instrumental: false };
    await saveOfflineTrack('member-a', track('a'), new AbortController().signal, lyrics);
    await saveOfflineTrack('member-b', track('b'), new AbortController().signal);

    expect((await offlineTracks('member-a')).map((entry) => entry.track.id)).toEqual(['a']);
    expect((await offlineTracks('member-b')).map((entry) => entry.track.id)).toEqual(['b']);
    expect((await offlineTrack('member-a', 'b'))).toBeUndefined();
    expect(await offlineOwners()).toEqual(['member-a', 'member-b']);
    expect((await offlineTrack('member-a', 'a'))?.lyrics).toEqual(lyrics);
    expect(lastOfflineOwner()).toBe('member-b');
  });

  it('removes only the requested owner and clears the last-owner marker when their library is empty', async () => {
    await saveOfflineTrack('member-a', track('a'), new AbortController().signal);
    await saveOfflineTrack('member-b', track('b'), new AbortController().signal);
    await removeOfflineTrack('member-a', 'a');

    expect(await offlineTracks('member-a')).toEqual([]);
    expect((await offlineTracks('member-b'))[0].track.id).toBe('b');
    expect(lastOfflineOwner()).toBe('member-b');

    await clearOfflineMusic('member-b');
    expect(await offlineOwners()).toEqual([]);
    expect(lastOfflineOwner()).toBeNull();
  });

  it('rejects bad audio responses clearly and keeps the existing saver signature compatible', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('', { status: 404 }));
    await expect(saveOfflineTrack('member-a', track('missing'), new AbortController().signal)).rejects.toThrow('HTTP 404');
  });

  it('keeps a browser-playable source and adds only the explicit download permission query', async () => {
    await saveOfflineTrack('member-a', track('native'), new AbortController().signal);

    expect(fetch).toHaveBeenCalledWith('/api/music/tracks/native/audio?download=true', expect.objectContaining({ credentials: 'same-origin' }));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect((await offlineTrack('member-a', 'native'))?.track).toMatchObject({ codec: 'opus', sample_rate: 48_000, channels: 2 });
  });

  it('saves a verified PCM WAV for an unsupported source and records delivered format metadata', async () => {
    const unsupported = { ...track('alac'), codec: 'alac', sample_rate: 44_100, bit_depth: 16 };
    vi.mocked(fetch).mockResolvedValueOnce(audioResponse(wavBytes(), 'audio/wav'));

    await saveOfflineTrack('member-a', unsupported, new AbortController().signal);

    expect(fetch).toHaveBeenCalledWith('/api/music/tracks/alac/audio?format=wav&download=true', expect.objectContaining({ credentials: 'same-origin' }));
    expect(fetch).toHaveBeenCalledTimes(1);
    const saved = await offlineTrack('member-a', 'alac');
    expect(saved?.track).toMatchObject({ codec: 'pcm_s24le', sample_rate: 96_000, bit_depth: 24, channels: 2 });
    expect([...new Uint8Array(saved!.audio).slice(0, 4)]).toEqual([...new TextEncoder().encode('RIFF')]);
  });

  it.each([
    ['ordinary', wavBytes(48_000, 2, 32, 3)],
    ['extensible', wavBytes(48_000, 2, 32, 0xfffe, 3)],
  ])('records %s IEEE float WAV deliveries truthfully', async (id, bytes) => {
    vi.mocked(fetch).mockResolvedValueOnce(audioResponse(bytes, 'audio/wav'));

    await saveOfflineTrack('member-a', { ...track(id), codec: 'alac' }, new AbortController().signal);

    expect((await offlineTrack('member-a', id))?.track).toMatchObject({
      codec: 'pcm_f32le', sample_rate: 48_000, bit_depth: 32, channels: 2,
    });
  });

  it('rejects a WAV format it cannot label as playable PCM', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(audioResponse(wavBytes(48_000, 2, 16, 6), 'audio/wav'));

    await expect(saveOfflineTrack(
      'member-a', { ...track('unsupported-wav'), codec: 'alac' }, new AbortController().signal,
    )).rejects.toThrow(/no valid PCM format metadata/i);
    expect(await offlineOwners()).toEqual([]);
  });

  it.each([401, 403])('does not request a compatibility copy after an HTTP %s denial', async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response('', { status }));

    await expect(saveOfflineTrack('member-a', track('denied'), new AbortController().signal)).rejects.toThrow(`HTTP ${status}`);

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(await offlineOwners()).toEqual([]);
  });

  it('rejects malformed native and compatibility data instead of labeling it playable', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(audioResponse(new Uint8Array([1, 2, 3])))
      .mockResolvedValueOnce(audioResponse(new Uint8Array([4, 5, 6]), 'audio/wav'));

    await expect(saveOfflineTrack('member-a', track('broken'), new AbortController().signal)).rejects.toThrow(/compatible WAV.*malformed/i);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(await offlineOwners()).toEqual([]);
  });

  it('rejects credential-bearing audio URLs before making a request', async () => {
    const secret = { ...track('secret'), audio_url: '/api/music/tracks/secret/audio?sig=private' };
    await expect(saveOfflineTrack('member-a', secret, new AbortController().signal)).rejects.toThrow(/without credentials/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('aborts a stale owner download before it can write', async () => {
    let resolve: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; }));
    const controller = new AbortController();
    const saving = saveOfflineTrack('old-owner', track('slow'), controller.signal);
    controller.abort();
    resolve?.(audioResponse());

    await expect(saving).rejects.toMatchObject({ name: 'AbortError' });
    expect(await offlineOwners()).toEqual([]);
  });

  it('rejects a response above the 128 MiB limit before storing anything', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(new Uint8Array([1]), {
      status: 200,
      headers: { 'Content-Length': String(MAX_OFFLINE_AUDIO_BYTES + 1) },
    }));
    await expect(saveOfflineTrack('member-a', track('large'), new AbortController().signal)).rejects.toThrow('128 MiB');
    expect(await offlineOwners()).toEqual([]);
  });

  it('honors cancellation before a body is read', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(saveOfflineTrack('member-a', track('cancelled'), controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(await offlineOwners()).toEqual([]);
  });

  it('waits for the service worker readiness acknowledgement', async () => {
    const worker = {
      postMessage: vi.fn((_message: unknown, ports?: MessagePort[]) => {
        setTimeout(() => ports?.[0]?.postMessage({ type: 'OFFLINE_SHELL_READY' }), 0);
      }),
    } as unknown as ServiceWorker;
    const serviceWorkers = new EventTarget() as ServiceWorkerContainer;
    const registration = { active: worker, update: vi.fn(async () => undefined) } as unknown as ServiceWorkerRegistration;
    Object.defineProperty(serviceWorkers, 'register', { value: vi.fn(async () => registration) });
    Object.defineProperty(serviceWorkers, 'ready', { value: Promise.resolve(registration) });
    Object.defineProperty(serviceWorkers, 'controller', { value: worker });
    Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: serviceWorkers });
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });

    await enableOfflineShell();
    expect(vi.mocked(serviceWorkers.register)).toHaveBeenCalledWith('/music-sw.js', { scope: '/' });
    expect(worker.postMessage).toHaveBeenCalled();
  });
});
