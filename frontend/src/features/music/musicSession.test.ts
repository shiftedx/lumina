// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MusicEngineState, MusicSource } from './musicEngine';
import type { MusicTrack } from './musicTypes';

const mocks = vi.hoisted(() => ({
  forgetCast: vi.fn(),
  queues: [] as MusicTrack[][],
  getQueue: vi.fn(),
  progress: vi.fn().mockResolvedValue(undefined),
  stopCast: vi.fn().mockResolvedValue(undefined),
  engines: [] as FakeController[],
  streams: [] as FakeController[],
  getShared: vi.fn(),
  putShared: vi.fn(),
}));

class FakeApiRequestError extends Error {
  public constructor(public status: number, message: string) { super(message); }
}

class FakeController {
  public state: MusicEngineState = { index: -1, position: 0, duration: 0, playing: false, loading: false, error: null };
  public queue: MusicSource[] = [];
  public readonly play = vi.fn(async (queue: MusicSource[], index = 0, position = 0) => {
    this.queue = queue;
    this.emit({ index, position, duration: 100, playing: true, loading: false, error: null, playbackMode: 'native' });
  });
  public readonly pause = vi.fn();
  public readonly resume = vi.fn(async () => { this.emit({ playing: true }); });
  public readonly seek = vi.fn(async (position: number) => { this.emit({ position }); });
  public readonly next = vi.fn(async () => undefined);
  public readonly previous = vi.fn(async () => undefined);
  public readonly setVolume = vi.fn();
  public readonly setGain = vi.fn();
  public readonly setRepeat = vi.fn();
  public readonly setCrossfade = vi.fn();
  public readonly replaceUpcoming = vi.fn((queue: MusicSource[]) => { this.queue = queue; });
  public readonly dispose = vi.fn();

  public constructor(public readonly options: { onChange: (state: MusicEngineState) => void; getAudio?: (source: MusicSource, signal: AbortSignal) => Promise<ArrayBuffer> }) {}
  public getState() { return { ...this.state }; }
  public emit(patch: Partial<MusicEngineState>) { this.state = { ...this.state, ...patch }; this.options.onChange({ ...this.state }); }
}

class FakeEngine extends FakeController {
  public constructor(options: ConstructorParameters<typeof FakeController>[0]) { super(options); mocks.engines.push(this); }
}

class FakeStream extends FakeController {
  public constructor(options: ConstructorParameters<typeof FakeController>[0]) { super(options); mocks.streams.push(this); }
}

class FakeAudioContext {
  public sampleRate = 96_000;
  public state = 'running';
  public resume = vi.fn().mockResolvedValue(undefined);
}

vi.mock('../../api', () => ({
  ApiRequestError: FakeApiRequestError,
  getMusicQueue: mocks.getQueue,
  updatePlaybackProgress: mocks.progress,
}));
vi.mock('./musicOffline', () => ({ offlineTrack: vi.fn().mockResolvedValue(undefined) }));
vi.mock('./musicCastSession', () => ({ stopMusicCast: mocks.stopCast, forgetMusicCast: mocks.forgetCast, restoreSharedMusicCast: vi.fn() }));
vi.mock('./musicSharedSession', () => ({ getMusicSharedSession: mocks.getShared, putMusicSharedSession: mocks.putShared }));
vi.mock('./musicEngine', () => ({ MusicEngine: FakeEngine, MAX_AUDIO_BYTES: 8, MAX_DECODED_AUDIO_BYTES: 128 * 1024 * 1024 }));
vi.mock('./musicStream', () => ({ MusicStream: FakeStream }));

const track = (id: string, fields: Partial<MusicTrack> = {}): MusicTrack => ({
  id,
  title: id,
  artist: 'Artist',
  album_artist: 'Artist',
  album: 'Album',
  album_id: 'album-1',
  artist_id: 'artist-1',
  number: 1,
  disc: 1,
  duration: 100,
  artwork_url: null,
  audio_url: `/api/music/tracks/${id}/audio`,
  codec: 'flac',
  sample_rate: 96_000,
  bit_depth: 24,
  channels: 2,
  gain_db: null,
  genres: [],
  edited: false,
  ...fields,
});

async function loadSession() {
  return import('./musicSession');
}

beforeEach(() => {
  vi.resetModules();
  mocks.getQueue.mockReset();
  mocks.progress.mockReset().mockResolvedValue(undefined);
  mocks.stopCast.mockReset().mockResolvedValue(undefined);
  mocks.forgetCast.mockReset();
  mocks.engines.length = 0;
  mocks.streams.length = 0;
  mocks.getShared.mockResolvedValue({ revision: 0, queue: [], current_index: null, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
  mocks.putShared.mockResolvedValue({ revision: 1, queue: [], current_index: null, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
  vi.stubGlobal('AudioContext', FakeAudioContext);
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
});

describe('musicSession', () => {
  it('restores only a bounded, same-origin queue with finite state', async () => {
    const good = track('safe-id');
    localStorage.setItem('lumina:music:queue:member', JSON.stringify({ queue: [good], index: 9, position: 42 }));
    const session = await loadSession();
    session.setMusicOwner('member');
    expect(session.musicSession()).toMatchObject({ queue: [good], playback: { index: 0, position: 42 }, mode: 'gapless' });

    session.setMusicOwner(null);
    localStorage.setItem('lumina:music:queue:member', JSON.stringify({ queue: [{ ...good, audio_url: 'https://example.test/private.flac' }], index: 0, position: Infinity }));
    session.setMusicOwner('member');
    expect(session.musicSession().queue).toEqual([]);
  });

  it('keeps an untouched cached queue provisional when the server has a canonical session', async () => {
    const local = track('local');
    const server = track('server');
    localStorage.setItem('lumina:music:queue:member', JSON.stringify({ queue: [local], index: 0, position: 25, revision: 2, repeat: 'off', shuffle: false, dirty: false }));
    mocks.getShared.mockResolvedValue({ revision: 3, queue: [server], current_index: 0, position_seconds: 4, repeat: 'all', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    const session = await loadSession();
    session.setMusicOwner('member');
    await vi.waitFor(() => expect(session.musicSession().queue).toEqual([server]));
    expect(session.musicSession().playback.playing).toBe(false);
    expect(mocks.putShared).not.toHaveBeenCalled();
  });

  it('preserves a local rewind when its session revision still matches', async () => {
    const local = track('local');
    localStorage.setItem('lumina:music:queue:member', JSON.stringify({ queue: [local], index: 0, position: 12, revision: 3, repeat: 'off', shuffle: false, dirty: true }));
    mocks.getShared.mockResolvedValue({ revision: 3, queue: [local], current_index: 0, position_seconds: 40, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    mocks.putShared.mockResolvedValue({ revision: 4, queue: [local], current_index: 0, position_seconds: 12, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    const session = await loadSession();
    session.setMusicOwner('member');
    await vi.waitFor(() => expect(mocks.putShared).toHaveBeenCalledWith(expect.objectContaining({ base_revision: 3, position_seconds: 12, item_ids: ['local'] })));
    expect(session.musicSession().sharedRevision).toBe(4);
  });

  it('never autoplays an async queue that returns after the member changes', async () => {
    let resolve: ((queue: MusicTrack[]) => void) | undefined;
    mocks.getQueue.mockImplementation(() => new Promise<MusicTrack[]>((done) => { resolve = done; }));
    const session = await loadSession();
    const pending = session.playMusic('old-member', { item_ids: ['a'] });
    await vi.waitFor(() => expect(mocks.getQueue).toHaveBeenCalled());
    session.setMusicOwner('new-member');
    resolve?.([track('a')]);
    await pending;

    expect(mocks.engines[0].dispose).toHaveBeenCalled();
    expect(mocks.engines[0].play).not.toHaveBeenCalled();
    expect(session.musicSession().owner).toBe('new-member');
  });

  it.each([
    ['logout', null],
    ['member switch', 'new-member'],
  ])('does not fetch or start after a deferred speaker stop is invalidated by %s', async (_label, nextOwner) => {
    let finishStop: (() => void) | undefined;
    mocks.stopCast.mockImplementationOnce(() => new Promise<void>((resolve) => { finishStop = resolve; }));
    const session = await loadSession();

    const pending = session.playMusic('old-member', { item_ids: ['a'] });
    await vi.waitFor(() => expect(mocks.stopCast).toHaveBeenCalledWith('old-member'));
    session.setMusicOwner(nextOwner);
    finishStop?.();
    await pending;

    expect(mocks.getQueue).not.toHaveBeenCalled();
    expect(mocks.engines).toHaveLength(0);
    expect(session.musicSession().owner).toBe(nextOwner);
  });

  it('uses the cold owner when stopping speaker output before browser play and resume', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');

    await session.playMusicQueue([track('a')]);
    expect(mocks.stopCast).toHaveBeenLastCalledWith('member');

    session.pauseMusic();
    mocks.engines[0].emit({ playing: false });
    mocks.stopCast.mockClear();
    await session.toggleMusic();

    expect(mocks.stopCast).toHaveBeenCalledWith('member');
    expect(mocks.engines[0].resume).toHaveBeenCalledOnce();
  });

  it('routes repeat and future reordering without restarting the current controller', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    const queue = [track('a'), track('b'), track('c')];
    await session.playMusicQueue(queue);
    const controller = mocks.engines[0];
    const plays = controller.play.mock.calls.length;

    session.repeatMusic();
    session.repeatMusic();
    session.repeatMusic();
    expect(controller.setRepeat.mock.calls.slice(-3).map(([mode]) => mode)).toEqual(['all', 'one', 'off']);
    session.reorderMusic(1, 2);
    expect(controller.replaceUpcoming).toHaveBeenCalledWith([
      expect.objectContaining({ id: 'a' }), expect.objectContaining({ id: 'c' }), expect.objectContaining({ id: 'b' }),
    ]);
    expect(controller.play).toHaveBeenCalledTimes(plays);
    expect(session.musicSession().queue.map(({ id }) => id)).toEqual(['a', 'c', 'b']);
  });

  it('throttles queue persistence instead of writing on 4 Hz position updates', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000);
    const session = await loadSession();
    session.setMusicOwner('member');
    const stored = vi.spyOn(localStorage, 'setItem');
    await session.playMusicQueue([track('a')]);
    stored.mockClear();
    const controller = mocks.engines[0];
    for (let position = 1; position <= 8; position += 1) {
      now.mockReturnValue(1_000 + position * 250);
      controller.emit({ position, playing: true });
    }
    expect(stored).not.toHaveBeenCalled();
    now.mockReturnValue(12_000);
    controller.emit({ position: 9 });
    expect(stored).toHaveBeenCalledTimes(1);
  });

  it('records completion against the old track at an automatic transition', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('a'), track('b')]);
    mocks.progress.mockClear();
    const controller = mocks.engines[0];
    controller.emit({ index: 0, position: 100, duration: 100, playing: true });
    controller.emit({ index: 1, position: 0, duration: 100, playing: true });
    await Promise.resolve();

    expect(mocks.progress).toHaveBeenCalledWith('a', expect.objectContaining({ position_seconds: 100, completed: true }), { keepalive: true });
  });

  it('pauses immediately when a music response denies access', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 403 })));
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('a')]);
    const controller = mocks.engines[0];
    const request = controller.options.getAudio?.(controller.queue[0], new AbortController().signal);
    await expect(request).rejects.toThrow('Listening access');

    expect(controller.pause).toHaveBeenCalled();
    expect(session.musicSession().message).toBe('Listening access is currently unavailable.');
  });

  it('streams a queue with a predicted track above the per-track decoded limit before creating an engine', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('long-opus', { duration: 60 * 60, sample_rate: 48_000, channels: 2 })]);

    expect(mocks.engines).toHaveLength(0);
    expect(mocks.streams).toHaveLength(1);
    expect(mocks.streams[0].play).toHaveBeenCalledWith([expect.objectContaining({ id: 'long-opus' })], 0, 0);
    expect(session.musicSession().mode).toBe('streaming');
  });

  it.each(['duration', 'sample_rate', 'channels'] as const)('streams unprobed audio with missing %s instead of decoding it', async (field) => {
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('unprobed', { [field]: null })], 0, 60);

    expect(mocks.engines).toHaveLength(0);
    expect(mocks.streams[0].play).toHaveBeenCalledWith([expect.objectContaining({ id: 'unprobed' })], 0, 60);
    expect(session.musicSession().mode).toBe('streaming');
  });

  it('cancels oversized and aborted gapless response streams', async () => {
    const session = await loadSession();
    const oversizedCancel = vi.fn();
    const oversized = new Response(new ReadableStream({
      start(controller) { controller.enqueue(new Uint8Array(9)); },
      cancel: oversizedCancel,
    }));
    await expect(session.readMusicAudio(oversized, new AbortController().signal)).rejects.toThrow(/too large/);
    expect(oversizedCancel).toHaveBeenCalledOnce();

    const abortedCancel = vi.fn();
    const pending = new ReadableStream<Uint8Array>({ pull: () => new Promise(() => undefined), cancel: abortedCancel });
    const controller = new AbortController();
    const reading = session.readMusicAudio(new Response(pending), controller.signal);
    controller.abort();
    await expect(reading).rejects.toMatchObject({ name: 'AbortError' });
    expect(abortedCancel).toHaveBeenCalledOnce();
  });

  it('selects album, manual, and mixed-queue gain before switching controllers cleanly', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    const queue = [track('a', { duration: 30, sample_rate: 192_000, gain_db: -2, album_gain_db: 6, album_peak: 2 }), track('b', { gain_db: -3, album_gain_db: 6, album_peak: 2 })];
    await session.playMusicQueue(queue);
    const engine = mocks.engines[0];
    expect(engine.setGain).toHaveBeenLastCalledWith(0.5);
    expect(session.currentMusicSampleRate()).toBe(96_000);

    await session.playMusicQueue([
      track('a', { gain_db: -6, gain_is_manual: true, album_gain_db: 6, album_peak: 2 }),
      track('b', { gain_db: -3, album_gain_db: 6, album_peak: 2 }),
    ]);
    expect(engine.setGain).toHaveBeenLastCalledWith(10 ** (-6 / 20));

    await session.playMusicQueue([
      track('a', { gain_db: -2, album_gain_db: 6, album_peak: 2 }),
      track('b', { album_id: 'album-2', gain_db: -3, album_gain_db: 4, album_peak: 2 }),
    ]);
    expect(engine.setGain).toHaveBeenLastCalledWith(10 ** (-2 / 20));

    await session.streamMusicCurrent();
    expect(engine.dispose).toHaveBeenCalled();
    expect(mocks.streams[0].play).toHaveBeenCalledWith(expect.any(Array), 0, 0);
    expect(session.musicSession().mode).toBe('streaming');
    expect(session.currentMusicSampleRate()).toBeNull();
  });

  it('does not start browser audio when stopping an active speaker fails', async () => {
    mocks.stopCast.mockRejectedValueOnce(new Error('Speaker stop failed'));
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('a')]);
    expect(mocks.engines).toHaveLength(0);
    expect(session.musicSession().message).toBe('Speaker stop failed');
  });
});

  it('adopts a refreshed canonical queue paused and never resumes the old controller', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    await session.playMusicQueue([track('old')]);
    const old = mocks.engines[0];
    mocks.getShared.mockResolvedValue({ revision: 4, queue: [track('new')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    await session.refreshMusicSharedSession('member');

    expect(old.dispose).toHaveBeenCalled();
    expect(session.musicSession()).toMatchObject({ queue: [track('new')], playback: { playing: false } });
    await session.toggleMusic();
    expect(old.resume).not.toHaveBeenCalled();
    expect(mocks.engines.at(-1)?.play).toHaveBeenCalledWith([expect.objectContaining({ id: 'new' })], 0, 0);
  });

  it('replaces a preloaded successor when canonical wait resolution truncates the queue', async () => {
    const session = await loadSession();
    mocks.putShared.mockResolvedValue({ revision: 1, queue: [track('a'), track('b')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    session.setMusicOwner('member');
    await vi.waitFor(() => expect(session.musicSession().sharedRevision).toBe(0));
    await session.playMusicQueue([track('a'), track('b')]);
    const controller = mocks.engines[0];
    await vi.waitFor(() => expect(mocks.putShared).toHaveBeenCalled());
    controller.replaceUpcoming.mockClear();
    mocks.putShared.mockResolvedValue({ revision: 2, queue: [track('a')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0,
      entries: [{ kind: 'track', item_id: 'a', state: 'playable', tracks: [track('a')] }, { kind: 'acquisition', request_id: '11111111-1111-4111-8111-111111111111', state: 'awaiting_import', name: 'Album', message: 'Waiting for library import' }], entry_index: 0, unavailable_policy: 'wait' });
    session.setMusicUnavailablePolicy('wait');
    await session.syncMusicSharedSession();
    expect(session.musicSession().queue.map((entry) => entry.id)).toEqual(['a']);
    await vi.waitFor(() => expect(controller.replaceUpcoming).toHaveBeenCalledWith([expect.objectContaining({ id: 'a' })]));
    expect(session.musicSession().queue.map((entry) => entry.id)).toEqual(['a']);
  });


  it('serializes a durable clear behind an in-flight checkpoint', async () => {
    const session = await loadSession();
    session.setMusicOwner("member");
    await vi.waitFor(() => expect(session.musicSession().sharedRevision).toBe(0));
    mocks.putShared.mockClear();
    let finish: ((value: object) => void) | undefined;
    mocks.putShared.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await session.playMusicQueue([track('a')]);
    await vi.waitFor(() => expect(mocks.putShared).toHaveBeenCalledTimes(1));
    const closing = session.closeMusic();
    finish?.({ revision: 1, queue: [track('a')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    await closing;
    await vi.waitFor(() => expect(mocks.putShared.mock.calls.some(([body]) => Array.isArray(body.item_ids) && body.item_ids.length === 0 && body.current_index === null)).toBe(true));
    expect(session.musicSession().queue).toEqual([]);
  });


  it('stops the old controller when a sync removes its current track', async () => {
    const session = await loadSession();
    mocks.putShared.mockResolvedValue({ revision: 1, queue: [track('a')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 });
    session.setMusicOwner('member');
    await vi.waitFor(() => expect(session.musicSession().sharedRevision).toBe(0));
    await session.playMusicQueue([track('a')]);
    const old = mocks.engines[0];
    await vi.waitFor(() => expect(mocks.putShared).toHaveBeenCalled());
    mocks.putShared.mockResolvedValue({ revision: 2, queue: [track('b')], current_index: 0, position_seconds: 0, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 1 });
    session.setMusicUnavailablePolicy('wait');
    await session.syncMusicSharedSession();
    expect(old.dispose).toHaveBeenCalled();
    expect(session.musicSession()).toMatchObject({ queue: [track('b')], playback: { playing: false } });
  });


  it('maps an engine advance from a direct track into the resolved acquisition entry', async () => {
    const session = await loadSession();
    session.setMusicOwner('member');
    const snapshot = { revision: 3, queue: [track('direct'), track('album-one'), track('album-two')], current_index: 0, position_seconds: 0,
      repeat: 'off' as const, shuffle: false, active_output: { kind: 'browser' as const }, updated_at: null, removed_count: 0,
      entries: [
        { kind: 'track' as const, item_id: 'direct', state: 'playable' as const, tracks: [track('direct')] },
        { kind: 'acquisition' as const, request_id: '11111111-1111-4111-8111-111111111111', state: 'playable' as const, name: 'Album', tracks: [track('album-one'), track('album-two')] },
      ], entry_index: 0, unavailable_policy: 'skip' as const };
    session.applyMusicSharedSnapshot(snapshot);
    mocks.putShared.mockResolvedValue({ ...snapshot, revision: 4 });
    await session.toggleMusic();
    mocks.engines.at(-1)?.emit({ index: 1, position: 0, playing: true });
    expect(session.musicSession().sharedEntryIndex).toBe(1);
  });
