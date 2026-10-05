import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('hls.js', () => import('./test/fakeHls'));
const api = vi.hoisted(() => ({ getLocalPlaybackOptions: vi.fn(), getPlaybackProgress: vi.fn(), startLocalPlaybackSession: vi.fn(), stopLocalPlaybackSession: vi.fn(), sendClientMetrics: vi.fn(), beaconClientMetrics: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const metrics = vi.hoisted(() => ({ recordMetric: vi.fn() }));
vi.mock('./perfMetrics', async (importOriginal) => ({ ...(await importOriginal<typeof import('./perfMetrics')>()), ...metrics }));

const { LocalLibraryPlayer, ttffLabel } = await import('./localPlayer');
const prefetch = await import('./playbackPrefetch');
const perf = await import('./perfMetrics');
const { hlsCalls, resetHlsCalls } = await import('./test/fakeHls');

const direct = { mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 1920, height: 1080, duration: 1320 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 2 };
const transcode = { ...direct, mode: 'transcode', facts: { ...direct.facts, video_codec: 'hevc' } };
const session = (id: string, start = 0, kind = 'video_sw') => ({ session_id: id, mode: 'transcode', playback_url: `/api/playback-sessions/${id}/index.m3u8`, start, kind });
const video = () => screen.getByLabelText('Film video') as HTMLVideoElement;

afterEach(() => { vi.resetAllMocks(); resetHlsCalls(); });

describe('playback start', () => {
  it('uses the decision the title page prefetched instead of asking again', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    prefetch.prefetchPlaybackOptions('pre-1');
    render(<LocalLibraryPlayer itemId="pre-1" kind="video" poster={null} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/pre-1/media'));
    expect(api.getLocalPlaybackOptions).toHaveBeenCalledTimes(1);
  });

  it('asks for the start point in the first range request of a direct file', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    render(<LocalLibraryPlayer itemId="d-1" kind="video" poster={null} startAt={754} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-1/media#t=754'));
  });

  it('plays a start point at or past the end from the beginning', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    render(<LocalLibraryPlayer itemId="d-2" kind="video" poster={null} startAt={1319.5} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-2/media'));
  });

  it('starts one conversion at the start point with a short first buffer', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(transcode);
    api.startLocalPlaybackSession.mockResolvedValue(session('s-1', 754));
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    render(<LocalLibraryPlayer itemId="t-1" kind="video" poster={null} startAt={754} title="Film" />);
    await waitFor(() => expect(hlsCalls.configs).toHaveLength(1));
    expect(api.startLocalPlaybackSession.mock.calls).toEqual([['t-1', 754, undefined]]);
    expect(hlsCalls.configs[0]).toMatchObject({ startFragPrefetch: true, maxBufferLength: 12, timelineOffset: 754 });
  });

  it('seeks when a new start time arrives for the open item', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    const view = render(<LocalLibraryPlayer itemId="d-3" kind="video" poster={null} startAt={0} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-3/media'));
    let position = 0;
    Object.defineProperty(video(), 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });
    view.rerender(<LocalLibraryPlayer itemId="d-3" kind="video" poster={null} startAt={42} title="Film" />);
    expect(position).toBe(42);
  });

  it('records time to first frame once, from the noted Play, labelled by how it plays', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    perf.notePlayIntent({}, performance.now() - 800);
    render(<LocalLibraryPlayer itemId="d-4" kind="video" poster={null} startAt={30} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-4/media#t=30'));
    fireEvent.playing(video());
    fireEvent.playing(video());
    expect(metrics.recordMetric).toHaveBeenCalledTimes(1);
    const [metric, label, value] = metrics.recordMetric.mock.calls[0];
    expect([metric, label]).toEqual(['ttff_ms', 'direct_resume']);
    expect(value).toBeGreaterThanOrEqual(800);
  });

  it('labels a start clamped to the beginning as a direct Play, not a resume', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    perf.notePlayIntent();
    render(<LocalLibraryPlayer itemId="d-6" kind="video" poster={null} startAt={1319.5} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-6/media'));
    fireEvent.playing(video());
    expect(metrics.recordMetric.mock.calls[0]?.slice(0, 2)).toEqual(['ttff_ms', 'direct']);
  });

  it('records nothing without a noted Play', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    render(<LocalLibraryPlayer itemId="d-5" kind="video" poster={null} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-5/media'));
    fireEvent.playing(video());
    expect(metrics.recordMetric).not.toHaveBeenCalled();
  });

  it('labels conversions by the encoder the server used', () => {
    expect([ttffLabel(null, 0), ttffLabel(null, 5)]).toEqual(['direct', 'direct_resume']);
    expect(ttffLabel(session('a', 0, 'video_hw') as never, 0)).toBe('transcode_hw');
    expect(ttffLabel(session('a', 0, 'video_sw') as never, 0)).toBe('transcode_sw');
    expect(ttffLabel(session('a', 0, 'audio') as never, 0)).toBe('remux');
  });

  it('adopts the early session the title page started', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(transcode);
    api.getPlaybackProgress.mockResolvedValue(null);
    api.startLocalPlaybackSession.mockResolvedValue(session('early'));
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    prefetch.prefetchPlaybackOptions('t-2');
    await api.getLocalPlaybackOptions.mock.results[0].value;
    prefetch.speculativeStart('t-2');
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1));
    prefetch.claimSpeculativeStart('t-2');
    prefetch.cancelSpeculativeStart('t-2');
    render(<LocalLibraryPlayer itemId="t-2" kind="video" poster={null} title="Film" />);
    await waitFor(() => expect(hlsCalls.sources).toEqual([expect.stringMatching(/\/early\/index\.m3u8$/)]));
    expect(api.stopLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('resumes a conversion at exactly the position the early start used, so the server hands back that session', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(transcode);
    api.getPlaybackProgress.mockResolvedValue({ item_id: 't-3', position_seconds: 754.25, duration_seconds: 1320, completed: false });
    api.startLocalPlaybackSession.mockResolvedValue(session('early', 754.25));
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    prefetch.prefetchPlaybackOptions('t-3');
    await api.getLocalPlaybackOptions.mock.results[0].value;
    prefetch.speculativeStart('t-3');
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1));
    prefetch.claimSpeculativeStart('t-3');
    // WatchSurface's resume point for the same progress row.
    render(<LocalLibraryPlayer itemId="t-3" kind="video" poster={null} startAt={754.25} title="Film" />);
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2));
    const [early, player] = api.startLocalPlaybackSession.mock.calls;
    expect(player.slice(0, 2)).toEqual(early.slice(0, 2));
    expect(player[2]).toBeUndefined();
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    expect(api.stopLocalPlaybackSession).not.toHaveBeenCalled();
  });

  it('converts a start point at or past the end from the beginning, and the early start does the same', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(transcode);
    api.getPlaybackProgress.mockResolvedValue({ item_id: 't-4', position_seconds: 1319.5, duration_seconds: 1320, completed: false });
    api.startLocalPlaybackSession.mockResolvedValue(session('early'));
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    prefetch.prefetchPlaybackOptions('t-4');
    await api.getLocalPlaybackOptions.mock.results[0].value;
    prefetch.speculativeStart('t-4');
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(1));
    prefetch.claimSpeculativeStart('t-4');
    render(<LocalLibraryPlayer itemId="t-4" kind="video" poster={null} startAt={1319.5} title="Film" />);
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2));
    expect(api.startLocalPlaybackSession.mock.calls).toEqual([['t-4', 0], ['t-4', 0, undefined]]);
  });

  it('seeks again when the same start time is asked for again', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    const view = render(<LocalLibraryPlayer itemId="d-6" kind="video" poster={null} startAt={42} startKey={1} title="Film" />);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-6/media#t=42'));
    let position = 0;
    Object.defineProperty(video(), 'currentTime', { configurable: true, get: () => position, set: (value: number) => { position = value; } });
    position = 300;
    view.rerender(<LocalLibraryPlayer itemId="d-6" kind="video" poster={null} startAt={42} startKey={1} title="Film" />);
    expect(position).toBe(300);
    view.rerender(<LocalLibraryPlayer itemId="d-6" kind="video" poster={null} startAt={42} startKey={2} title="Film" />);
    expect(position).toBe(42);
  });

  it('records time to first frame under StrictMode double effects', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    perf.notePlayIntent({}, performance.now() - 500);
    render(<StrictMode><LocalLibraryPlayer itemId="d-7" kind="video" poster={null} title="Film" /></StrictMode>);
    await waitFor(() => expect(video().getAttribute('src')).toBe('/api/library/d-7/media'));
    fireEvent.playing(video());
    expect(metrics.recordMetric).toHaveBeenCalledTimes(1);
    expect(metrics.recordMetric.mock.calls[0][1]).toBe('direct');
  });
});
