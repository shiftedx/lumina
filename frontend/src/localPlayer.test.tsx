import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('hls.js', () => import('./test/fakeHls'));
const api = vi.hoisted(() => ({ getLocalPlaybackOptions: vi.fn(), startLocalPlaybackSession: vi.fn(), stopLocalPlaybackSession: vi.fn() }));
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), ...api }));
const prefetch = vi.hoisted(() => ({ adoptSpeculativeStart: vi.fn(), holdConversion: vi.fn(() => vi.fn()), takePrefetchedOptions: vi.fn() }));
vi.mock('./playbackPrefetch', () => prefetch);

const { LocalLibraryPlayer } = await import('./localPlayer');
const { hlsCalls, resetHlsCalls } = await import('./test/fakeHls');

const direct = { mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 1920, height: 1080, duration: 1320 }, audio_tracks: [], quality_heights: [720, 480], loudness_gain_db: -3.5 };
const tracks = [{ id: 's:0', label: 'English', src: '/api/library/i1/subtitle-tracks/s:0.vtt' }];

afterEach(() => { vi.resetAllMocks(); resetHlsCalls(); });

describe('LocalLibraryPlayer tracks and probe', () => {
  it('reports the probe and keeps subtitle tracks through direct play and a converted session', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    api.startLocalPlaybackSession.mockResolvedValue({ session_id: 's1', mode: 'transcode', playback_url: '/api/playback-sessions/s1/index.m3u8', start: 0 });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    const onOptions = vi.fn();
    const props = { itemId: 'i1', kind: 'video' as const, poster: null, title: 'Film', onOptions, tracks };
    const { rerender } = render(<LocalLibraryPlayer {...props} request={{}} />);
    await waitFor(() => expect(onOptions).toHaveBeenCalledWith(direct));
    expect(screen.getByLabelText('Film video').querySelector('track')?.id).toBe('s:0');
    rerender(<LocalLibraryPlayer {...props} request={{ max_height: 720 }} />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    expect(hlsCalls.attached[0]).toMatchObject({ media: screen.getByLabelText('Film video') });
    expect(screen.getByLabelText('Film video').querySelector('track')?.id).toBe('s:0');
  });

  it('keeps a paused direct stream paused when a quality choice starts HLS', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    api.startLocalPlaybackSession.mockResolvedValue({ session_id: 's1', mode: 'transcode', playback_url: '/api/playback-sessions/s1/index.m3u8', start: 0 });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const props = { itemId: 'i1', kind: 'video' as const, poster: null, title: 'Film' };
    const view = render(<LocalLibraryPlayer {...props} request={{}} />);
    const directMedia = await waitFor(() => {
      const media = screen.getByLabelText('Film video') as HTMLVideoElement;
      expect(media.getAttribute('src')).toBe('/api/library/i1/media');
      return media;
    });
    Object.defineProperty(directMedia, 'currentTime', { configurable: true, value: 0, writable: true });

    view.rerender(<LocalLibraryPlayer {...props} request={{ max_height: 720 }} />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const convertedMedia = screen.getByLabelText('Film video') as HTMLVideoElement;
    expect(convertedMedia.autoplay).toBe(false);
    expect(hlsCalls.manifestParsed).toEqual([]);
    expect(play).not.toHaveBeenCalled();
    play.mockRestore();
  });

  it('resumes a playing direct stream when a quality choice starts HLS', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    api.startLocalPlaybackSession.mockResolvedValue({ session_id: 's1', mode: 'transcode', playback_url: '/api/playback-sessions/s1/index.m3u8', start: 30 });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const props = { itemId: 'i1', kind: 'video' as const, poster: null, title: 'Film' };
    const view = render(<LocalLibraryPlayer {...props} request={{}} />);
    const directMedia = await waitFor(() => {
      const media = screen.getByLabelText('Film video') as HTMLVideoElement;
      expect(media.getAttribute('src')).toBe('/api/library/i1/media');
      return media;
    });
    Object.defineProperties(directMedia, {
      currentTime: { configurable: true, value: 30, writable: true },
      paused: { configurable: true, value: false },
    });

    view.rerender(<LocalLibraryPlayer {...props} request={{ max_height: 720 }} />);
    await waitFor(() => expect(hlsCalls.sources).toHaveLength(1));
    const convertedMedia = screen.getByLabelText('Film video') as HTMLVideoElement;
    expect(convertedMedia.autoplay).toBe(true);
    hlsCalls.manifestParsed[0]();
    expect(play).toHaveBeenCalledTimes(1);
    play.mockRestore();
  });

  it('reports null when the probe is unreachable and still plays the file', async () => {
    api.getLocalPlaybackOptions.mockRejectedValue(new Error('offline'));
    const onOptions = vi.fn();
    render(<LocalLibraryPlayer itemId="i2" kind="video" onOptions={onOptions} poster={null} title="Clip" />);
    await waitFor(() => expect(onOptions).toHaveBeenCalledWith(null));
    expect((screen.getByLabelText('Clip video') as HTMLVideoElement).getAttribute('src')).toBe('/api/library/i2/media');
  });

  it('takes the prefetched decision, adopts the early session, and holds the conversion while it plays', async () => {
    const release = vi.fn();
    prefetch.takePrefetchedOptions.mockReturnValue(Promise.resolve({ ...direct, mode: 'transcode' }));
    prefetch.holdConversion.mockReturnValue(release);
    api.startLocalPlaybackSession.mockResolvedValue({ session_id: 'early', mode: 'transcode', playback_url: '/api/playback-sessions/early/index.m3u8', start: 0 });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    const { unmount } = render(<LocalLibraryPlayer itemId="i3" kind="video" poster={null} title="Film" />);
    await waitFor(() => expect(prefetch.adoptSpeculativeStart).toHaveBeenCalledWith('i3', 'early'));
    expect(prefetch.holdConversion).toHaveBeenCalledTimes(1);
    expect(prefetch.holdConversion.mock.invocationCallOrder[0]).toBeLessThan(api.startLocalPlaybackSession.mock.invocationCallOrder[0]);
    expect(api.getLocalPlaybackOptions).not.toHaveBeenCalled();
    expect(release).not.toHaveBeenCalled();
    unmount();
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('lets the conversion hold go when the start fails', async () => {
    const release = vi.fn();
    prefetch.takePrefetchedOptions.mockReturnValue(Promise.resolve({ ...direct, mode: 'transcode' }));
    prefetch.holdConversion.mockReturnValue(release);
    api.startLocalPlaybackSession.mockRejectedValue(new Error('The server is busy.'));
    render(<LocalLibraryPlayer itemId="i4" kind="video" poster={null} title="Film" />);
    await waitFor(() => expect(release).toHaveBeenCalledTimes(1));
  });
});

describe('LocalLibraryPlayer stopped by the server owner', () => {
  it('says so when the direct file answers 403 stopped_by_admin', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(direct);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'stopped_by_admin', message: 'Stopped' } }), { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<LocalLibraryPlayer itemId="i9" kind="video" poster={null} title="Clip" />);
    const video = await waitFor(() => { const el = screen.getByLabelText('Clip video'); expect(el.getAttribute('src')).toBeTruthy(); return el; });
    fireEvent.error(video);
    expect((await screen.findAllByText('Playback was stopped by the server owner.')).length).toBeGreaterThan(0);
    vi.unstubAllGlobals();
  });
});

describe('LocalLibraryPlayer HEVC fallback', () => {
  const hevc = { mode: 'remux', reason: null, facts: { container: 'matroska', video_codec: 'hevc', audio_codec: 'dts', width: 1920, height: 1080, duration: 7000 }, audio_tracks: [], quality_heights: [720] };
  const copy = { session_id: 'copy', mode: 'remux', kind: 'audio', playback_url: '/api/playback-sessions/copy/index.m3u8', start: 600 };
  const encode = { session_id: 'enc', mode: 'transcode', kind: 'video_hw', playback_url: '/api/playback-sessions/enc/index.m3u8', start: 600 };
  const props = { itemId: 'borat', kind: 'video' as const, poster: null, title: 'Borat', startAt: 600 };
  afterEach(() => { localStorage.clear(); vi.useRealTimers(); });

  async function playCopiedHevc() {
    api.getLocalPlaybackOptions.mockResolvedValueOnce(hevc).mockResolvedValueOnce({ ...hevc, mode: 'transcode' });
    api.startLocalPlaybackSession.mockResolvedValueOnce(copy).mockResolvedValueOnce(encode);
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    render(<LocalLibraryPlayer {...props} />);
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(1));
  }

  it('re-asks once without HEVC at the same position when hls.js cannot decode it', async () => {
    await playCopiedHevc();
    hlsCalls.handlers[0]('error', { fatal: true, type: 'mediaError' });
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2));
    expect(api.startLocalPlaybackSession).toHaveBeenLastCalledWith('borat', 600, undefined);
    expect(api.getLocalPlaybackOptions).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem('lumina.hevcFailed')).toBe('1');
    expect(api.stopLocalPlaybackSession).toHaveBeenCalledWith('copy');
    await waitFor(() => expect(hlsCalls.sources.at(-1)).toContain('/enc/'));
    expect(screen.queryByText('Playback of the converted file failed.')).toBeNull();
  });

  it('falls back when data arrives but no frame shows within the timeout', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await playCopiedHevc();
    hlsCalls.listeners.hlsFragLoaded[0]('hlsFragLoaded', undefined as never);
    vi.advanceTimersByTime(8000);
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2));
    expect(api.startLocalPlaybackSession).toHaveBeenLastCalledWith('borat', 600, undefined);
  });

  it('falls back only once: a second failure shows the failure state', async () => {
    api.getLocalPlaybackOptions.mockResolvedValue(hevc);
    api.startLocalPlaybackSession.mockResolvedValueOnce(copy).mockResolvedValueOnce({ ...copy, session_id: 'copy2' });
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    render(<LocalLibraryPlayer {...props} />);
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(1));
    hlsCalls.handlers[0]('error', { fatal: true, type: 'mediaError' });
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(2));
    hlsCalls.handlers[1]('error', { fatal: true, type: 'mediaError' });
    expect((await screen.findAllByText('Playback of the converted file failed.')).length).toBeGreaterThan(0);
    expect(api.startLocalPlaybackSession).toHaveBeenCalledTimes(2);
  });

  it('a direct HEVC file the element cannot decode is re-asked as a conversion', async () => {
    api.getLocalPlaybackOptions.mockResolvedValueOnce({ ...hevc, mode: 'direct', facts: { ...hevc.facts, container: 'mp4', audio_codec: 'aac' } }).mockResolvedValueOnce({ ...hevc, mode: 'transcode' });
    api.startLocalPlaybackSession.mockResolvedValue(encode);
    api.stopLocalPlaybackSession.mockResolvedValue(undefined);
    render(<LocalLibraryPlayer {...props} />);
    const video = await waitFor(() => { const el = screen.getByLabelText('Borat video') as HTMLVideoElement; expect(el.getAttribute('src')).toBeTruthy(); return el; });
    Object.defineProperty(video, 'error', { configurable: true, value: { code: 4 } });
    fireEvent.error(video);
    await waitFor(() => expect(api.startLocalPlaybackSession).toHaveBeenCalledWith('borat', 600, undefined));
    expect(screen.queryByText(/failed|cannot/i)).toBeNull();
  });
});
