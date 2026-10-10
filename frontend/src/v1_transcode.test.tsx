import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('hls.js', () => import('./test/fakeHls'));

import { LocalLibraryPlayer, loudnessGain, lowerQuality, needsSession, needsRestart } from './localPlayer';
import { hlsCalls, resetHlsCalls } from './test/fakeHls';
import type { LocalPlaybackOptions } from './types';

const transcode = { mode: 'transcode', reason: 'VideoCodecNotSupported,AudioCodecNotSupported', facts: { container: 'matroska,webm', video_codec: 'hevc', audio_codec: 'ac3', width: 1920, height: 1080, duration: 60 } };

function routeFetch(session: { status: number; body: unknown }, options: unknown = transcode) {
  const calls: Array<{ url: string; method: string; body?: string }> = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || 'GET';
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined });
    const restart = /\/playback-sessions\?start=(\d+)$/.exec(url);
    const [status, body] = url.includes('/playback-options') ? [200, options]
      : url.endsWith('/playback-sessions') ? [session.status, session.body]
        : restart ? [201, { session_id: 'sess-2', mode: 'transcode', playback_url: '/api/playback-sessions/sess-2/index.m3u8', start: Number(restart[1]) }]
          : [204, null];
    return new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }));
  return calls;
}

describe('converted local playback', () => {
  afterEach(() => { vi.unstubAllGlobals(); resetHlsCalls(); });

  it('plays the authorized HLS derivative and cancels the session when the item closes', async () => {
    const calls = routeFetch({ status: 201, body: { session_id: 'sess-1', mode: 'transcode', playback_url: '/api/playback-sessions/sess-1/index.m3u8' } });
    const view = render(<LocalLibraryPlayer itemId="item-1" kind="video" poster={null} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toEqual([expect.stringMatching(/\/api\/playback-sessions\/sess-1\/index\.m3u8$/)]));
    expect(screen.getByText('1080p · HEVC / AC3 · Transcoded')).toBeTruthy();
    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/api/library/item-1/playback-sessions'))).toBe(true);
    view.unmount();
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/api/playback-sessions/sess-1'))).toBe(true));
  });

  it('shows a clear failure instead of an endless spinner when conversion is refused', async () => {
    routeFetch({ status: 429, body: { detail: 'Every playback conversion slot is busy. Try again shortly.' } });
    render(<LocalLibraryPlayer itemId="item-2" kind="video" poster={null} title="Clip" />);
    expect(await screen.findAllByText('Every playback conversion slot is busy. Try again shortly.')).not.toHaveLength(0);
    expect(hlsCalls.sources).toEqual([]);
  });

  it('restarts the conversion at a seek beyond the converted part, on an absolute timeline (#137)', async () => {
    const calls = routeFetch({ status: 201, body: { session_id: 'sess-1', mode: 'transcode', playback_url: '/api/playback-sessions/sess-1/index.m3u8', start: 0 } });
    const onLoadedMetadata = vi.fn();
    const { container } = render(<LocalLibraryPlayer itemId="item-3" kind="video" onLoadedMetadata={onLoadedMetadata} poster={null} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = container.querySelector('video') as HTMLVideoElement;
    // The whole file is seekable from the first load: the duration override spans it.
    expect(hlsCalls.attached[0]).toEqual({ media, overrides: { duration: 60 } });

    let position = 8; // inside the converted part: plain seek, no restart
    Object.defineProperty(media, 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });
    Object.defineProperty(media, 'paused', { configurable: true, value: false });
    fireEvent(media, new Event('seeking'));
    fireEvent.loadedMetadata(media);
    expect(onLoadedMetadata).toHaveBeenCalledTimes(1);
    position = 45.6; // past the converted edge (12 s), e.g. a saved resume position
    fireEvent(media, new Event('seeking'));

    await waitFor(() => expect(hlsCalls.sources[1]).toMatch(/\/api\/playback-sessions\/sess-2\/index\.m3u8$/));
    expect(calls.filter((call) => call.method === 'POST').map((call) => call.url.split('/api')[1])).toEqual(['/library/item-3/playback-sessions', '/library/item-3/playback-sessions?start=45']);
    expect(hlsCalls.stopped).toBeGreaterThan(0);
    expect(hlsCalls.configs[1]).toMatchObject({ timelineOffset: 45 });

    // An event playlist isn't marked VOD until ffmpeg finishes, so hls.js ignores
    // timelineOffset for the initial position on its own; the restarted generation must
    // be pinned to its start explicitly. The first (start=0) load needs no such pin.
    expect(hlsCalls.manifestParsed).toHaveLength(1);
    media.play = vi.fn().mockResolvedValue(undefined);
    hlsCalls.manifestParsed[0]();
    expect(position).toBe(45);
    expect(media.play).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/api/playback-sessions/sess-1'))).toBe(true));
    fireEvent.loadedMetadata(media); // the restarted load must not re-apply the caller's resume
    expect(onLoadedMetadata).toHaveBeenCalledTimes(1);
  });

  it('a copied restart starts its timeline at the earlier keyframe the server reports, then pins the asked position', async () => {
    routeFetch({ status: 201, body: { session_id: 'sess-1', mode: 'transcode', playback_url: '/api/playback-sessions/sess-1/index.m3u8', start: 0 } });
    const { container } = render(<LocalLibraryPlayer itemId="item-9" kind="video" poster={null} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = container.querySelector('video') as HTMLVideoElement;
    let position = 0;
    Object.defineProperty(media, 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });
    Object.defineProperty(media, 'paused', { configurable: true, value: false });
    // The restart at 45 s copies video from the keyframe at 40.4 s.
    vi.mocked(fetch).mockImplementation(async (url) => new Response(JSON.stringify(String(url).includes('?start=')
      ? { session_id: 'sess-2', mode: 'transcode', playback_url: '/api/playback-sessions/sess-2/index.m3u8', start: 40.4 } : null), { status: String(url).includes('?start=') ? 201 : 204 }));
    position = 45.6;
    fireEvent(media, new Event('seeking'));
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(2));
    expect(hlsCalls.configs[1]).toMatchObject({ timelineOffset: 40.4 });
    media.play = vi.fn().mockResolvedValue(undefined);
    hlsCalls.manifestParsed[0]();
    expect(position).toBe(45);    // hls.js would aim a growing playlist's first fragment at its live edge plus timelineOffset (twice the asked
    // position); loading starts only once the playlist is in, at the asked position relative to the session start.
    expect(hlsCalls.configs[1]).toMatchObject({ autoStartLoad: false });
    expect(hlsCalls.startLoads).toEqual([]);
    hlsCalls.levelLoaded[1]();
    expect(hlsCalls.startLoads[0]).toBeCloseTo(4.6);
  });

  it('only restarts outside [start, edge]', () => {
    expect(needsRestart(30, 0, 12)).toBe(true);
    expect(needsRestart(10, 0, 12)).toBe(false);
    expect(needsRestart(44.5, 45, 60)).toBe(false);
    expect(needsRestart(10, 45, 60)).toBe(true);
    expect(needsRestart(30, 0, undefined)).toBe(false);
  });

  const sess1 = { status: 201, body: { session_id: 'sess-1', mode: 'transcode', playback_url: '/api/playback-sessions/sess-1/index.m3u8', start: 0 } };
  const direct: LocalPlaybackOptions = {
    mode: 'direct', reason: null, facts: { container: 'mov,mp4', video_codec: 'h264', audio_codec: 'aac', width: 1920, height: 1080, duration: 60 },
    audio_tracks: [{ index: 1, label: 'English', default: true }, { index: 2, label: 'Commentary', default: false }], quality_heights: [720, 480], loudness_gain_db: -6,
  };

  it('restarts at the current position when the member picks another quality', async () => {
    const calls = routeFetch(sess1);
    const view = render(<LocalLibraryPlayer itemId="item-5" kind="video" poster={null} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = view.container.querySelector('video') as HTMLVideoElement;
    Object.defineProperty(media, 'currentTime', { configurable: true, get: () => 30.4, set: () => undefined });
    view.rerender(<LocalLibraryPlayer itemId="item-5" kind="video" poster={null} request={{ max_height: 720 }} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources[1]).toMatch(/sess-2\/index\.m3u8$/));
    const restart = calls.filter((call) => call.method === 'POST')[1];
    expect(restart.url).toMatch(/\/library\/item-5\/playback-sessions\?start=30$/);
    expect(JSON.parse(String(restart.body))).toEqual({ max_height: 720 });
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/api/playback-sessions/sess-1'))).toBe(true));
  });

  it('switches version where the member is, even though the video element is replaced', async () => {
    const calls = routeFetch(sess1);
    const view = render(<LocalLibraryPlayer itemId="item-8" kind="video" poster={null} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    Object.defineProperty(view.container.querySelector('video') as HTMLVideoElement, 'currentTime', { configurable: true, value: 312.4, writable: true });
    view.rerender(<LocalLibraryPlayer itemId="item-8" kind="video" poster={null} request={{ version_id: 'item-8-sd', max_height: null }} title="Clip" />);
    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2));
    const restart = calls.filter((call) => call.method === 'POST')[1];
    expect(restart.url).toMatch(/\/library\/item-8\/playback-sessions\?start=312$/);
    expect(JSON.parse(String(restart.body))).toEqual({ version_id: 'item-8-sd' });
  });

  it('starts a session to burn in an image subtitle even for a direct-play file', async () => {
    const calls = routeFetch(sess1, direct);
    render(<LocalLibraryPlayer itemId="item-6" kind="video" poster={null} request={{ subtitle: 'i:3' }} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toEqual([expect.stringMatching(/sess-1\/index\.m3u8$/)]));
    const post = calls.find((call) => call.method === 'POST');
    expect(JSON.parse(String(post?.body))).toEqual({ subtitle: 'i:3' });
  });

  it('returns from a burn-in session to direct play at the same position', async () => {
    const calls = routeFetch(sess1, direct);
    const onLoadedMetadata = vi.fn();
    const view = render(<LocalLibraryPlayer itemId="item-10" kind="video" onLoadedMetadata={onLoadedMetadata} poster={null} request={{ subtitle: 'i:3' }} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const destroyed = hlsCalls.destroyed;
    Object.defineProperty(view.container.querySelector('video') as HTMLVideoElement, 'currentTime', { configurable: true, value: 300, writable: true });
    view.rerender(<LocalLibraryPlayer itemId="item-10" kind="video" onLoadedMetadata={onLoadedMetadata} poster={null} request={{}} title="Clip" />);
    await waitFor(() => expect(view.container.querySelector('video')?.getAttribute('src')).toMatch(/\/api\/library\/item-10\/media$/));
    expect(hlsCalls.destroyed).toBe(destroyed + 1);
    const media = view.container.querySelector('video') as HTMLVideoElement;
    fireEvent.loadedMetadata(media);
    expect(media.currentTime).toBe(300);
    expect(onLoadedMetadata).not.toHaveBeenCalled();
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE' && call.url.endsWith('/api/playback-sessions/sess-1'))).toBe(true));
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1);
  });

  it('offers one lower-quality restart when a transcode keeps buffering', async () => {
    routeFetch(sess1, { ...transcode, quality_heights: [720, 480] });
    const onRequestChange = vi.fn();
    const { container } = render(<LocalLibraryPlayer itemId="item-7" kind="video" onRequestChange={onRequestChange} poster={null} request={{}} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = container.querySelector('video') as HTMLVideoElement;
    for (let stall = 0; stall < 3; stall += 1) fireEvent(media, new Event('waiting'));
    fireEvent.click(await screen.findByRole('button', { name: 'Switch to 720p' }));
    expect(onRequestChange).toHaveBeenCalledWith({ max_height: 720 });
    expect(screen.queryByRole('button', { name: 'Switch to 720p' })).toBeNull();
  });

  it('keeps quality for the rest of the session once the member declines the offer', async () => {
    routeFetch(sess1, { ...transcode, quality_heights: [720, 480] });
    const { container } = render(<LocalLibraryPlayer itemId="item-9" kind="video" onRequestChange={vi.fn()} poster={null} request={{}} title="Clip" />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = container.querySelector('video') as HTMLVideoElement;
    for (let stall = 0; stall < 3; stall += 1) fireEvent(media, new Event('waiting'));
    fireEvent.click(await screen.findByRole('button', { name: 'Keep quality' }));
    for (let stall = 0; stall < 3; stall += 1) fireEvent(media, new Event('waiting'));
    expect(screen.queryByRole('button', { name: 'Switch to 720p' })).toBeNull();
  });

  it('counts stalls and offers once per session even when the parent re-renders with new callbacks', async () => {
    routeFetch(sess1, { ...transcode, quality_heights: [720, 480] });
    const player = () => <LocalLibraryPlayer itemId="item-11" kind="video" onRequestChange={vi.fn()} poster={null} request={{}} title="Clip" />;
    const view = render(player());
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const media = view.container.querySelector('video') as HTMLVideoElement;
    // WatchSurface re-renders on every timeupdate with a fresh onRequestChange.
    for (let stall = 0; stall < 3; stall += 1) { fireEvent(media, new Event('waiting')); view.rerender(player()); }
    fireEvent.click(await screen.findByRole('button', { name: 'Keep quality' }));
    for (let stall = 0; stall < 3; stall += 1) { view.rerender(player()); fireEvent(media, new Event('waiting')); }
    expect(screen.queryByRole('button', { name: 'Switch to 720p' })).toBeNull();
  });

  it('derives session need, loudness gain and the next lower rung', () => {
    expect(needsSession(direct, undefined)).toBe(false);
    expect(needsSession(direct, { max_height: 720 })).toBe(true);
    expect(needsSession(direct, { max_height: 1080 })).toBe(false);
    expect(needsSession(direct, { audio_index: 1 })).toBe(false);
    expect(needsSession(direct, { audio_index: 2 })).toBe(true);
    expect(needsSession(direct, { subtitle: 's:0' })).toBe(false); // text tracks are sidecars
    expect(loudnessGain(direct, true)).toBeCloseTo(0.501, 3);
    expect(loudnessGain(direct, false)).toBe(1);
    expect(loudnessGain({ ...direct, loudness_gain_db: null }, true)).toBe(1);
    expect(lowerQuality(direct, 1080)).toBe(720);
    expect(lowerQuality(direct, 720)).toBe(480);
    expect(lowerQuality(direct, 480)).toBeNull();
  });
});
