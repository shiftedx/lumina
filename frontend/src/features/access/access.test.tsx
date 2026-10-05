import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getMyAccess } from '../../api';
import { Sidebar } from '../../app/Navigation';
import { liveSnapshot } from '../../test/remoteFixtures';
import { SubscriptionControls } from '../../SubscriptionControls';
import type { SourceAutomation } from '../../types';
import { StreamingSurface, type StreamingSurfaceProps } from '../streaming/StreamingSurface';
import { AccessProvider, type AccessValue, allStreamingBlocked, type MyAccess, TimeLeftNotice, useMyAccess, watchGate, WatchStopState } from './access';
import { accessStopCode, reportAccessStop } from './accessEvents';

const open: MyAccess = { sections: null, blocked_streaming: [], followed_only: false, allowed_now: true, until: null, remaining_minutes: null };
const value = (patch: Partial<MyAccess> = {}, stop: AccessValue['stop'] = null): AccessValue => ({ access: { ...open, ...patch }, stop, refresh: vi.fn() });
const respond = (status: number, body: unknown) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe('useMyAccess', () => {
  it('loads at sign-in, refreshes on a timer and on a server access code, and clears a stop once allowed', async () => {
    const fetchMock = respond(200, open);
    const { result } = renderHook(() => useMyAccess('u1'));
    await waitFor(() => expect(result.current.access).toEqual({ ...open, can_download: true }));
    expect(fetchMock.mock.calls[0][0]).toContain('/api/me/access');
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...open, remaining_minutes: 0 }), { status: 200 }));
    act(() => reportAccessStop('screen_time_up'));
    await waitFor(() => expect(result.current.access?.remaining_minutes).toBe(0));
    expect(result.current.stop).toBe('screen_time_up');
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...open, remaining_minutes: 30 }), { status: 200 }));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.stop).toBeNull());
  });

  it('does nothing signed out and fails open when the request fails', async () => {
    const fetchMock = respond(500, {});
    const { result, rerender } = renderHook(({ id }: { id?: string }) => useMyAccess(id), { initialProps: { id: undefined as string | undefined } });
    expect(fetchMock).not.toHaveBeenCalled();
    rerender({ id: 'u1' });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(result.current.access).toBeNull();
    expect(watchGate(result.current)).toBeNull();
  });

  it('fails open on a malformed body: missing arrays are empty, missing booleans permissive', async () => {
    for (const body of [{}, { blocked_streaming: 'youtube', allowed_now: 'no', sections: 'x' }, null, []]) {
      respond(200, body);
      const { result, unmount } = renderHook(() => useMyAccess('u1'));
      await waitFor(() => expect(result.current.access).not.toBeNull());
      expect(result.current.access).toEqual({ ...open, can_download: true });
      expect(watchGate(result.current)).toBeNull();
      expect(allStreamingBlocked(result.current.access)).toBe(false);
      unmount();
      vi.restoreAllMocks();
    }
  });

  it('api.ts reports a 403 access code from any request (start, checkpoint, heartbeat)', async () => {
    const seen: string[] = [];
    const listener = (event: Event) => seen.push((event as CustomEvent<string>).detail);
    window.addEventListener('lumina:access-stop', listener);
    respond(403, { detail: 'outside_hours' });
    await expect(getMyAccess()).rejects.toThrow('outside_hours');
    respond(403, { detail: 'streaming_blocked:youtube' });
    await expect(getMyAccess()).rejects.toThrow();
    respond(403, { detail: 'something else' });
    await expect(getMyAccess()).rejects.toThrow();
    window.removeEventListener('lumina:access-stop', listener);
    expect(seen).toEqual(['outside_hours', 'streaming_blocked:youtube']);
  });

  it('reads access codes from a raw media 403 body, and only a 403', () => {
    expect(accessStopCode(403, '{"detail":"screen_time_up"}')).toBe('screen_time_up');
    expect(accessStopCode(404, 'screen_time_up')).toBeNull();
    expect(accessStopCode(403, '{"code":"stopped_by_admin"}')).toBeNull();
  });
});

describe('watch gate and states', () => {
  it('gates on outside hours, no time left, or a reported stop', () => {
    expect(watchGate(value())).toBeNull();
    expect(watchGate(value({ allowed_now: false }))).toBe('outside_hours');
    expect(watchGate(value({ remaining_minutes: 0 }))).toBe('screen_time_up');
    expect(watchGate(value({}, 'screen_time_up'))).toBe('screen_time_up');
  });

  it('outside hours reads the localized return time; time up says tomorrow; focus lands on the state', () => {
    const until = new Date(2026, 9, 5, 7, 0).toISOString();
    const { rerender } = render(<WatchStopState gate="outside_hours" onHome={vi.fn()} until={until} />);
    const status = screen.getByRole('status');
    expect(status.textContent).toContain('Outside your viewing hours — back at');
    expect(status.textContent).toMatch(/7:00/);
    expect(document.activeElement).toBe(status);
    rerender(<WatchStopState gate="screen_time_up" onHome={vi.fn()} until={null} />);
    expect(screen.getByRole('status').textContent).toContain("That's today's watching time");
  });

  it('a quiet notice only when five minutes or less remain', () => {
    const { rerender } = render(<TimeLeftNotice minutes={5} />);
    expect(screen.getByRole('status').textContent).toBe('5 minutes of watching left today');
    rerender(<TimeLeftNotice minutes={1} />);
    expect(screen.getByRole('status').textContent).toBe('1 minute of watching left today');
    for (const minutes of [6, 0, null]) { rerender(<TimeLeftNotice minutes={minutes} />); expect(screen.queryByRole('status')).toBeNull(); }
  });
});

describe('hiding rules', () => {
  const nav = (access: AccessValue) => render(<AccessProvider value={access}><Sidebar activeDownloads={0} collapsed={false} mobile={false} onClose={vi.fn()} onNavigate={vi.fn()} open={false} surface="home" /></AccessProvider>);

  it('Streaming leaves the nav only when YouTube, Twitch and Kick are all blocked', () => {
    nav(value({ blocked_streaming: ['youtube', 'twitch'] }));
    expect(screen.getByRole('button', { name: 'Streaming' })).toBeTruthy();
    cleanup();
    expect(allStreamingBlocked(value({ blocked_streaming: ['youtube', 'twitch', 'kick'] }).access)).toBe(true);
    nav(value({ blocked_streaming: ['youtube', 'twitch', 'kick'] }));
    expect(screen.queryByRole('button', { name: 'Streaming' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Library' })).toBeTruthy();
  });

  const surface = (access: AccessValue, patch: Partial<StreamingSurfaceProps> = {}) => {
    const props: StreamingSurfaceProps = {
      view: 'home', provider: 'youtube', providers: ['youtube', 'twitch'], onNavigate: vi.fn(),
      live: { error: null, isQueueing: () => false, library: [], onOpen: vi.fn(), onQueue: vi.fn(), snapshot: liveSnapshot() },
      explore: { error: null, isQueueing: () => false, library: [], loading: false, onOpen: vi.fn(), onQueue: vi.fn(), onSearch: vi.fn(), popularError: null, query: '', results: [] } as unknown as StreamingSurfaceProps['explore'],
      channels: <section aria-label="Channels screen" />,
      ...patch,
    };
    return render(<AccessProvider value={access}><StreamingSurface {...props} /></AccessProvider>);
  };

  it('Live blocked: no Live now tab, and a direct live address is a calm explanation', () => {
    surface(value({ blocked_streaming: ['live'] }));
    expect(screen.queryByRole('radio', { name: 'Live now' })).toBeNull();
    cleanup();
    surface(value({ blocked_streaming: ['live'] }), { view: 'live' });
    expect(screen.getByRole('status').textContent).toBe("Live isn't available right now");
  });

  it('a blocked provider is not offered and the first one left takes over', () => {
    surface(value({ blocked_streaming: ['youtube'] }), { providers: ['twitch'] });
    expect(screen.queryByRole('radio', { name: 'YouTube' })).toBeNull();
    expect(screen.queryByRole('searchbox')).toBeNull();
  });

  it('open search blocked removes the search field; followed-only shows only your channels', () => {
    surface(value({ blocked_streaming: ['open_search'] }));
    expect(screen.queryByRole('searchbox')).toBeNull();
    cleanup();
    surface(value({ followed_only: true }));
    expect(screen.getByLabelText('Channels screen')).toBeTruthy();
    expect(screen.queryByRole('radio', { name: 'Browse' })).toBeNull();
    expect(screen.queryByRole('searchbox')).toBeNull();
  });
});

describe('stays calm', () => {
  beforeEach(() => vi.spyOn(console, 'error').mockImplementation(() => undefined));
  it('never mentions administrators', () => {
    render(<WatchStopState gate="screen_time_up" onHome={vi.fn()} until={null} />);
    expect(document.body.textContent).not.toMatch(/admin|parent|blocked|rating/i);
  });
});

describe('downloads', () => {
  it('can_download false hides the automatic-download option', () => {
    const automation = { id: 'c', user_id: 'u', label: 'Mars', source_url: 'https://www.youtube.com/@mars/videos', source_type: 'channel', cron_expression: '0 * * * *', active: true, auto_download: false, format_selection: { preset: 'best', output_container: 'mp4' }, output_profile: { base_path: null, subdir: '', organize_by: 'uploader' }, rules: {}, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10, last_error: null, last_run_summary: {}, created_at: '', updated_at: '' } as unknown as SourceAutomation;
    const commands = { pause: vi.fn(), resume: vi.fn(), setAutomaticAcquisition: vi.fn(), prepareUnfollow: vi.fn(), confirmUnfollow: vi.fn() } as never;
    const props = { automation, commands, onChange: vi.fn(), onRecover: vi.fn(), onRemoved: vi.fn() };
    const { rerender } = render(<AccessProvider value={value()}><SubscriptionControls {...props} /></AccessProvider>);
    expect(screen.getByText('Download new videos automatically')).toBeTruthy();
    rerender(<AccessProvider value={value({ can_download: false })}><SubscriptionControls {...props} /></AccessProvider>);
    expect(screen.queryByText('Download new videos automatically')).toBeNull();
  });

  it('a 403 downloads_not_allowed is a calm notice: no stop, save actions go away', async () => {
    respond(200, open);
    const notice = vi.fn();
    const { result } = renderHook(() => useMyAccess('u1', notice));
    await waitFor(() => expect(result.current.access).toEqual({ ...open, can_download: true }));
    act(() => reportAccessStop('downloads_not_allowed'));
    expect(notice).toHaveBeenCalledWith("Saving to the vault isn't available on this account");
    expect(result.current.stop).toBeNull();
    expect(result.current.access?.can_download).toBe(false);
    expect(watchGate(result.current)).toBeNull();
  });
});
