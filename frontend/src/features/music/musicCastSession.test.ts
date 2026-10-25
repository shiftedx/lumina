import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ control: vi.fn(), status: vi.fn(), shared: vi.fn() }));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  controlMusicCast: api.control,
  getMusicCastStatus: api.status,
}));
vi.mock('./musicSharedSession', () => ({ getMusicSharedSession: api.shared }));

const session = (owner = 'member-a') => ({
  owner, ticket: 'ticket_123', name: 'Kitchen', playing: true,
  baseIndex: 2, index: 2, position: 12, duration: 180, volume: .4, queued: 3,
});
const legacy = () => JSON.stringify({ version: 1, expiresAt: Date.now() + 60_000, session: session() });
const key = 'lumina:music:cast:member-a';

beforeEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  vi.clearAllMocks();
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  localStorage.clear();
  sessionStorage.clear();
  api.control.mockResolvedValue({});
  api.shared.mockResolvedValue({ active_output: { kind: 'browser' } });
  api.status.mockResolvedValue({ transport_state: 'PLAYING', volume: 45, track: { track_number: 2, position_seconds: 44, duration_seconds: 200 } });
});

describe('memory-only music cast controller', () => {
  it('keeps tickets out of browser storage and reloads only through a validated server descriptor', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session());
    first.updateMusicCastStatus('ticket_123', { transport_state: 'PLAYING' });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);

    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    await reloaded.restoreMusicCast('member-a');
    expect(reloaded.musicCast()).toBeNull();
    expect(api.status).not.toHaveBeenCalled();

    let resolve: ((value: object) => void) | undefined;
    api.status.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const restoring = reloaded.restoreSharedMusicCast(session());
    expect(reloaded.musicCast()).toBeNull();
    resolve?.({ transport_state: 'PLAYING', volume: 45, track: { track_number: 2, position_seconds: 44, duration_seconds: 200 } });
    await restoring;
    expect(api.status).toHaveBeenCalledWith('ticket_123');
    expect(reloaded.musicCast()).toMatchObject({ owner: 'member-a', playing: true, index: 3, position: 44, duration: 200, volume: .45 });
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it('does not install a deferred shared descriptor after its owner is invalidated', async () => {
    const cast = await import('./musicCastSession');
    let resolve: ((value: object) => void) | undefined;
    api.status.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const restoring = cast.restoreSharedMusicCast(session());
    cast.forgetMusicCast('member-a');
    resolve?.({ transport_state: 'PLAYING' });
    await restoring;
    expect(cast.musicCast()).toBeNull();
  });

  it.each([legacy(), '{malformed', JSON.stringify({ version: 1, expiresAt: 0, session: session() })])('removes legacy state without reading it or touching other member preferences (%s)', async (stored) => {
    localStorage.setItem(key, stored);
    localStorage.setItem('lumina:music:cast:member-b', 'other-member-state');
    localStorage.setItem('lumina:music:queue:member-a', 'queue-preference');
    const get = vi.spyOn(Storage.prototype, 'getItem');
    const cast = await import('./musicCastSession');
    await cast.restoreMusicCast('member-a');
    expect(get).not.toHaveBeenCalled();
    expect(api.status).not.toHaveBeenCalled();
    expect(cast.musicCast()).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
    expect(localStorage.getItem('lumina:music:cast:member-b')).toBe('other-member-state');
    expect(localStorage.getItem('lumina:music:queue:member-a')).toBe('queue-preference');
  });

  it('does not manufacture a controller on a transient restore failure and supports retry', async () => {
    const cast = await import('./musicCastSession');
    api.status.mockRejectedValueOnce(Object.assign(new Error('Gateway'), { status: 502 }));
    await cast.restoreSharedMusicCast(session());
    expect(cast.musicCast()).toBeNull();
    await cast.restoreSharedMusicCast(session());
    expect(cast.musicCast()).toMatchObject({ playing: true, index: 3 });
    api.status.mockRejectedValueOnce(new Error('Offline'));
    await cast.restoreMusicCast('member-a');
    expect(cast.musicCast()).toMatchObject({ playing: true, index: 3 });
    expect(cast.handleMusicCastStatusError('ticket_123', { status: 502 })).toBe(false);
  });

  it.each([401, 403, 404])('clears a rejected in-memory ticket after HTTP %s', async (status) => {
    const cast = await import('./musicCastSession');
    cast.setMusicCast(session());
    api.status.mockRejectedValueOnce(Object.assign(new Error('Rejected'), { status }));
    await cast.restoreMusicCast('member-a');
    expect(cast.musicCast()).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('rejects URL-shaped server tickets without a status request', async () => {
    const cast = await import('./musicCastSession');
    await cast.restoreSharedMusicCast({ ...session(), ticket: 'https://example.com/ticket' });
    expect(cast.musicCast()).toBeNull();
    expect(api.status).not.toHaveBeenCalled();
  });

  it('clears memory after Stop even when the speaker request fails', async () => {
    const cast = await import('./musicCastSession');
    cast.setMusicCast(session());
    api.control.mockRejectedValueOnce(new Error('Speaker unreachable'));
    await expect(cast.stopMusicCast()).rejects.toThrow('Speaker unreachable');
    expect(cast.musicCast()).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('stops a cold-reload controller through authenticated server state without using a legacy bearer', async () => {
    localStorage.setItem(key, legacy());
    api.shared.mockResolvedValueOnce({ active_output: { kind: 'sonos', ticket_id: 'server_ticket' } });
    const cast = await import('./musicCastSession');
    await cast.stopMusicCast('member-a');
    expect(api.shared).toHaveBeenCalledOnce();
    expect(api.control).toHaveBeenCalledWith('server_ticket', 'stop');
    expect(cast.musicCast()).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('stops only the requested owner and forgets an account-switch controller', async () => {
    const cast = await import('./musicCastSession');
    cast.setMusicCast(session('member-b'));
    await cast.stopMusicCast('member-a');
    expect(api.control).not.toHaveBeenCalled();
    expect(api.shared).not.toHaveBeenCalled();
    expect(cast.musicCast()?.owner).toBe('member-b');
    cast.forgetMusicCast('member-b');
    expect(cast.musicCast()).toBeNull();
  });

  it('still stops a cold-reload speaker when shared status restoration runs concurrently', async () => {
    const cast = await import('./musicCastSession');
    let resolveShared: ((value: object) => void) | undefined;
    let resolveStatus: ((value: object) => void) | undefined;
    api.shared.mockImplementationOnce(() => new Promise((done) => { resolveShared = done; }));
    api.status.mockImplementationOnce(() => new Promise((done) => { resolveStatus = done; }));
    const stopping = cast.stopMusicCast('member-a');
    const restoring = cast.restoreSharedMusicCast(session());
    resolveShared?.({ active_output: { kind: 'sonos', ticket_id: 'ticket_123' } });
    await stopping;
    expect(api.control).toHaveBeenCalledWith('ticket_123', 'stop');
    resolveStatus?.({ transport_state: 'PLAYING' });
    await restoring;
    expect(cast.musicCast()).toBeNull();
  });

  it('does not resurrect a cold-reload stop after an owner transition', async () => {
    const cast = await import('./musicCastSession');
    let resolve: ((value: object) => void) | undefined;
    api.shared.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const stopping = cast.stopMusicCast('member-a');
    cast.forgetMusicCast('member-a');
    resolve?.({ active_output: { kind: 'sonos', ticket_id: 'server_ticket' } });
    await stopping;
    expect(api.control).not.toHaveBeenCalled();
  });

  it('does not stop a legacy controller absent from the authenticated server session', async () => {
    localStorage.setItem(key, legacy());
    const cast = await import('./musicCastSession');
    await cast.stopMusicCast('member-a');
    expect(api.control).not.toHaveBeenCalled();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('supports playback, validated restore and stop when browser storage is denied', async () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('Denied'); });
    const writes = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Denied'); });
    const cast = await import('./musicCastSession');
    cast.setMusicCast(session());
    await cast.restoreSharedMusicCast(session());
    expect(cast.musicCast()?.playing).toBe(true);
    await cast.stopMusicCast('member-a');
    expect(api.control).toHaveBeenCalledWith('ticket_123', 'stop');
    expect(cast.musicCast()).toBeNull();
    expect(writes).not.toHaveBeenCalled();
  });
});


it.each(['forget', 'clear', 'invalid restore', 'rejected restore', 'other owner'])('requires authenticated output lookup after %s invalidates known browser output', async (transition) => {
  const cast = await import('./musicCastSession');
  await cast.stopMusicCast('member-a');
  if (transition === 'forget') cast.forgetMusicCast('member-a');
  if (transition === 'clear') cast.setMusicCast(null);
  if (transition === 'invalid restore') await cast.restoreSharedMusicCast({ ...session(), ticket: 'https://example.com/ticket' });
  if (transition === 'rejected restore') {
    api.status.mockRejectedValueOnce({ status: 401 });
    await cast.restoreSharedMusicCast(session());
  }
  if (transition === 'other owner') await cast.restoreMusicCast('member-b');
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
  api.shared.mockRejectedValueOnce(new TypeError('Offline'));
  await expect(cast.stopMusicCast('member-a')).rejects.toThrow('Offline');
  expect(api.control).not.toHaveBeenCalled();
});

it('fails closed on an unknown offline cold reload and observes canonical Sonos transitions while online', async () => {
  const cast = await import('./musicCastSession');
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
  api.shared.mockRejectedValueOnce(new TypeError('Offline'));
  await expect(cast.stopMusicCast('member-a')).rejects.toThrow('Offline');
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: true });
  await cast.stopMusicCast('member-a');
  api.shared.mockResolvedValueOnce({ active_output: { kind: 'sonos', ticket_id: 'canonical_ticket' } });
  await cast.stopMusicCast('member-a');
  expect(api.control).toHaveBeenCalledWith('canonical_ticket', 'stop');
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
  api.shared.mockRejectedValueOnce(new TypeError('Offline'));
  await expect(cast.stopMusicCast('member-a')).rejects.toThrow('Offline');
});

it('does not let an older browser lookup override a concurrent Sonos restore or a newly set cast', async () => {
  const cast = await import('./musicCastSession');
  let resolve: ((value: object) => void) | undefined;
  api.shared.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const stopping = cast.stopMusicCast('member-a');
  api.status.mockRejectedValueOnce({ status: 401 });
  await cast.restoreSharedMusicCast(session());
  resolve?.({ active_output: { kind: 'browser' } });
  await stopping;
  Object.defineProperty(navigator, 'onLine', { configurable: true, value: false });
  api.shared.mockRejectedValueOnce(new TypeError('Offline'));
  await expect(cast.stopMusicCast('member-a')).rejects.toThrow('Offline');
  cast.setMusicCast(session());
  api.control.mockRejectedValueOnce(new Error('Speaker unreachable'));
  await expect(cast.stopMusicCast('member-a')).rejects.toThrow('Speaker unreachable');
});
