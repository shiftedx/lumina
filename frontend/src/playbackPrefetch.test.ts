import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getLocalPlaybackOptions: vi.fn(), getPlaybackProgress: vi.fn(), startLocalPlaybackSession: vi.fn(), stopLocalPlaybackSession: vi.fn() }));
vi.mock('./api', () => api);

const options = (patch: Record<string, unknown> = {}) => ({ mode: 'transcode', reason: null, facts: null, free_video_slots: 1, ...patch });
const early = { session_id: 'early', mode: 'transcode', playback_url: '/api/playback-sessions/early/index.m3u8', start: 754, kind: 'video_sw' };

async function fresh() {
  vi.resetModules();
  return import('./playbackPrefetch');
}
const settle = () => vi.advanceTimersByTimeAsync(0);

async function ready(patch: Record<string, unknown> = {}) {
  const prefetch = await fresh();
  api.getLocalPlaybackOptions.mockResolvedValue(options(patch));
  prefetch.prefetchPlaybackOptions('i1');
  await settle();
  return prefetch;
}

beforeEach(() => {
  vi.useFakeTimers();
  api.getPlaybackProgress.mockResolvedValue({ position_seconds: 754, completed: false });
  api.startLocalPlaybackSession.mockResolvedValue(early);
  api.stopLocalPlaybackSession.mockResolvedValue(undefined);
});
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, 'connection');
  Reflect.deleteProperty(navigator, 'locks');
});

describe('playback options prefetch', () => {
  it('fetches once per 60 s, hands the result to the player once, and forgets a failure', async () => {
    const prefetch = await fresh();
    api.getLocalPlaybackOptions.mockResolvedValue(options());
    prefetch.prefetchPlaybackOptions('i1');
    prefetch.prefetchPlaybackOptions('i1');
    expect(api.getLocalPlaybackOptions).toHaveBeenCalledTimes(1);
    await expect(prefetch.takePrefetchedOptions('i1')).resolves.toMatchObject({ mode: 'transcode' });
    expect(prefetch.takePrefetchedOptions('i1')).toBeNull();
    prefetch.prefetchPlaybackOptions('i2');
    vi.advanceTimersByTime(prefetch.OPTIONS_TTL_MS);
    expect(prefetch.takePrefetchedOptions('i2')).toBeNull();
    api.getLocalPlaybackOptions.mockRejectedValueOnce(new Error('offline'));
    prefetch.prefetchPlaybackOptions('i3');
    await settle();
    prefetch.prefetchPlaybackOptions('i3');
    expect(api.getLocalPlaybackOptions).toHaveBeenCalledTimes(4);
  });
});

describe('speculative start', () => {
  it('starts early at the resume point only when a conversion is needed and a slot is free', async () => {
    for (const patch of [{ mode: 'direct' }, { free_video_slots: 0 }]) {
      (await ready(patch)).speculativeStart('i1');
      await settle();
    }
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
    (await ready({ mode: 'remux', free_video_slots: 0 })).speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).toHaveBeenLastCalledWith('i1', 754);
    (await ready()).speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2);
  });

  it('never starts early on a phone or with reduced data', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    (await ready()).speculativeStart('i1');
    vi.unstubAllGlobals();
    Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    (await ready()).speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('never starts early while this tab plays a conversion (mini player), since the server keeps one per device', async () => {
    const prefetch = await ready();
    const release = prefetch.holdConversion();
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
    release();
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1);
  });

  it('never starts early while another tab plays a conversion, and a held conversion holds the cross-tab lock', async () => {
    const held: { name: string }[] = [];
    const request = vi.fn((name: string, _opts: unknown, work: () => Promise<void>) => {
      const lock = { name };
      held.push(lock);
      return work().then(() => { held.splice(held.indexOf(lock), 1); });
    });
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request, query: async () => ({ held: [...held], pending: [] }) } });
    const prefetch = await ready();
    held.push({ name: prefetch.CONVERSION_LOCK }); // another tab's player
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
    held.length = 0;
    const release = prefetch.holdConversion();
    expect(request).toHaveBeenCalledWith(prefetch.CONVERSION_LOCK, { mode: 'shared' }, expect.any(Function));
    expect(held).toHaveLength(1);
    release();
    await settle();
    expect(held).toHaveLength(0);
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1);
  });

  it('without Web Locks (plain http), another tab\'s fresh localStorage hold blocks an early start and a stale one does not', async () => {
    const prefetch = await ready();
    localStorage.setItem(`${prefetch.HOLD_KEY}other-tab`, String(Date.now()));
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
    localStorage.setItem(`${prefetch.HOLD_KEY}other-tab`, String(Date.now() - prefetch.HOLD_FRESH_MS - 1));
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(`${prefetch.HOLD_KEY}other-tab`)).toBeNull(); // the stale hold is swept
  });

  it('sends no early start once the player holds its own conversion (Play on the same item)', async () => {
    const prefetch = await ready();
    prefetch.speculativeStart('i1');
    prefetch.claimSpeculativeStart('i1');
    const release = prefetch.holdConversion();
    await settle();
    release();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('a page restored from the back-forward cache while it holds a conversion resumes its heartbeat', async () => {
    const prefetch = await ready();
    const release = prefetch.holdConversion();
    const holds = () => Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index) ?? '').filter((key) => key.startsWith(prefetch.HOLD_KEY));
    window.dispatchEvent(new Event('pagehide'));
    expect(holds()).toHaveLength(0);
    window.dispatchEvent(Object.assign(new Event('pageshow'), { persisted: true }));
    expect(holds()).toHaveLength(1);
    const first = Number(localStorage.getItem(holds()[0]));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(Number(localStorage.getItem(holds()[0]))).toBeGreaterThan(first);
    release();
  });

  it('a held conversion keeps a heartbeat in localStorage for other tabs and removes it on release', async () => {
    const prefetch = await ready();
    const release = prefetch.holdConversion();
    const holds = () => Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index) ?? '').filter((key) => key.startsWith(prefetch.HOLD_KEY));
    expect(holds()).toHaveLength(1);
    const first = Number(localStorage.getItem(holds()[0]));
    await vi.advanceTimersByTimeAsync(prefetch.HOLD_FRESH_MS);
    expect(Number(localStorage.getItem(holds()[0]))).toBeGreaterThan(first);
    release();
    expect(holds()).toHaveLength(0);
  });

  it('never starts early when neither Web Locks nor localStorage can see other tabs', async () => {
    const prefetch = await ready();
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new Error('denied'); } });
    prefetch.speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('never starts early on a touch device', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('pointer: coarse') }));
    (await ready()).speculativeStart('i1');
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('sends no early start once it was cancelled, adopted or replaced before the request went out', async () => {
    let prefetch = await ready();
    prefetch.speculativeStart('i1');
    prefetch.cancelSpeculativeStart('i1');
    await settle();
    prefetch = await ready();
    prefetch.speculativeStart('i1');
    prefetch.claimSpeculativeStart('i1');
    prefetch.adoptSpeculativeStart('i1', 'players');
    await settle();
    prefetch = await ready();
    prefetch.speculativeStart('i1');
    prefetch.claimSpeculativeStart('i2'); // a trailer's Play
    await settle();
    expect(api.startLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('stops the early session when the page closes without Play, or 30 s after start', async () => {
    let prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.cancelSpeculativeStart('i1');
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
    prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    await vi.advanceTimersByTimeAsync(prefetch.SPECULATIVE_TTL_MS);
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledTimes(2);
  });

  it('Play keeps the early session for the player, which adopts it or lets it go', async () => {
    const prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.claimSpeculativeStart('i1');
    prefetch.cancelSpeculativeStart('i1'); // the title page closes after Play
    prefetch.adoptSpeculativeStart('i1', 'early');
    prefetch.cancelSpeculativeStart('i1'); // a late unmount after adoption
    await vi.advanceTimersByTimeAsync(prefetch.SPECULATIVE_TTL_MS);
    expect(api.stopLocalPlaybackSession).not.toHaveBeenCalled();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.claimSpeculativeStart('i1');
    prefetch.adoptSpeculativeStart('i1', 'another');
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
  });

  it('keeps at most one early session: starting another stops the first', async () => {
    const prefetch = await ready();
    prefetch.prefetchPlaybackOptions('i2');
    await settle();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.speculativeStart('i2');
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
  });
  it('Play with a time (openRoute) stops any unclaimed early session, whichever item it is for', async () => {
    const prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.cancelSpeculativeStart();
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
  });

  it('a member change stops the early session, even a claimed one, and forgets every decision', async () => {
    const prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    prefetch.claimSpeculativeStart('i1');
    prefetch.forgetPlaybackWarmup();
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
    expect(prefetch.takePrefetchedOptions('i1')).toBeNull();
    prefetch.adoptSpeculativeStart('i1', 'early'); // the next member's player finds nothing to adopt
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledTimes(1);
  });
});

describe('sign-out', () => {
  it('forgetPlaybackWarmup settles once the early session\'s stop is sent, and never waits past FORGET_WAIT_MS', async () => {
    let prefetch = await ready();
    prefetch.speculativeStart('i1');
    await settle();
    let done = false;
    void prefetch.forgetPlaybackWarmup().then(() => { done = true; });
    await settle();
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('early');
    expect(done).toBe(true);
    prefetch = await ready();
    api.startLocalPlaybackSession.mockReturnValue(new Promise(() => undefined)); // the start never answers
    prefetch.speculativeStart('i1');
    await settle();
    done = false;
    void prefetch.forgetPlaybackWarmup().then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(prefetch.FORGET_WAIT_MS - 1);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toBe(true);
  });
});

describe('decision cache bounds', () => {
  it('drops expired decisions on insert and keeps at most MAX_DECISIONS', async () => {
    const prefetch = await fresh();
    api.getLocalPlaybackOptions.mockResolvedValue(options());
    const start = Date.now();
    prefetch.prefetchPlaybackOptions('old');
    vi.advanceTimersByTime(prefetch.OPTIONS_TTL_MS);
    prefetch.prefetchPlaybackOptions('new');
    vi.setSystemTime(start);
    expect(prefetch.takePrefetchedOptions('old')).toBeNull(); // swept, not merely stale
    for (let index = 0; index < prefetch.MAX_DECISIONS; index += 1) prefetch.prefetchPlaybackOptions(`i${index}`);
    expect(prefetch.takePrefetchedOptions('new')).toBeNull();
    expect(prefetch.takePrefetchedOptions('i0')).not.toBeNull();
  });
});
