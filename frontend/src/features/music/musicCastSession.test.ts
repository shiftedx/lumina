import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ control: vi.fn(), status: vi.fn() }));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  controlMusicCast: api.control,
  getMusicCastStatus: api.status,
}));

const session = (owner = 'member-a') => ({
  owner, ticket: 'ticket_123', name: 'Kitchen', playing: true,
  baseIndex: 2, index: 2, position: 12, duration: 180, volume: .4, queued: 3,
});

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  localStorage.clear();
  api.control.mockResolvedValue({});
  api.status.mockResolvedValue({ transport_state: 'PLAYING', volume: 45, track: { track_number: 2, position_seconds: 44, duration_seconds: 200 } });
});

describe('persisted music cast controller', () => {
  it('restores only its owner after validating status and advances to the speaker track', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session());
    expect(localStorage.getItem('lumina:music:cast:member-a')).toContain('ticket_123');
    expect(localStorage.getItem('lumina:music:cast:member-a')).not.toContain('/api/');

    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    await reloaded.restoreMusicCast('member-a');

    expect(api.status).toHaveBeenCalledWith('ticket_123');
    expect(reloaded.musicCast()).toMatchObject({ owner: 'member-a', playing: true, index: 3, position: 44, duration: 200, volume: .45 });
  });

  it('keeps persisted controllers isolated by owner', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session('member-a'));

    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    await reloaded.restoreMusicCast('member-b');

    expect(reloaded.musicCast()).toBeNull();
    expect(api.status).not.toHaveBeenCalled();
    expect(localStorage.getItem('lumina:music:cast:member-a')).not.toBeNull();
  });

  it('retains a paused controller across a transient gateway failure and validates it on retry', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session());
    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    api.status.mockRejectedValueOnce(Object.assign(new Error('Gateway'), { status: 502 }));

    await reloaded.restoreMusicCast('member-a');
    expect(reloaded.musicCast()).toMatchObject({ ticket: 'ticket_123', playing: false });
    expect(localStorage.getItem('lumina:music:cast:member-a')).not.toBeNull();

    await reloaded.restoreMusicCast('member-a');
    expect(reloaded.musicCast()).toMatchObject({ playing: true, index: 3 });
  });

  it.each([401, 403, 404])('clears a rejected persisted ticket after HTTP %s', async (status) => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session());
    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    api.status.mockRejectedValueOnce(Object.assign(new Error('Rejected'), { status }));

    await reloaded.restoreMusicCast('member-a');

    expect(reloaded.musicCast()).toBeNull();
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
  });

  it('rejects malformed or URL-shaped stored tickets without a status request', async () => {
    localStorage.setItem('lumina:music:cast:member-a', JSON.stringify({
      version: 1, expiresAt: Date.now() + 60_000, session: { ...session(), ticket: 'https://secret.example/ticket' },
    }));
    const cast = await import('./musicCastSession');

    await cast.restoreMusicCast('member-a');

    expect(cast.musicCast()).toBeNull();
    expect(api.status).not.toHaveBeenCalled();
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
  });

  it('clears memory and storage after Stop even when the speaker request fails', async () => {
    const cast = await import('./musicCastSession');
    cast.setMusicCast(session());
    api.control.mockRejectedValueOnce(new Error('Speaker unreachable'));

    await expect(cast.stopMusicCast()).rejects.toThrow('Speaker unreachable');

    expect(cast.musicCast()).toBeNull();
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
  });

  it('stops an owner persisted controller after a cold reload before the player restores it', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session('member-a'));
    vi.resetModules();
    const reloaded = await import('./musicCastSession');
    expect(reloaded.musicCast()).toBeNull();

    await reloaded.stopMusicCast('member-a');

    expect(api.control).toHaveBeenCalledWith('ticket_123', 'stop');
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
  });

  it('stops only the requested owner and preserves another owner persisted controller', async () => {
    const first = await import('./musicCastSession');
    first.setMusicCast(session('member-a'));
    first.setMusicCast({ ...session('member-b'), ticket: 'ticket_b' });
    vi.resetModules();
    const reloaded = await import('./musicCastSession');

    await reloaded.stopMusicCast('member-a');

    expect(api.control).toHaveBeenCalledWith('ticket_123', 'stop');
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
    expect(localStorage.getItem('lumina:music:cast:member-b')).not.toBeNull();
  });

  it('does not stop an invalid or expired persisted controller', async () => {
    localStorage.setItem('lumina:music:cast:member-a', JSON.stringify({
      version: 1, expiresAt: Date.now() - 1, session: session('member-a'),
    }));
    const cast = await import('./musicCastSession');

    await cast.stopMusicCast('member-a');

    expect(api.control).not.toHaveBeenCalled();
    expect(localStorage.getItem('lumina:music:cast:member-a')).toBeNull();
  });
});
