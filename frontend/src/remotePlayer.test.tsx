import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToStaticMarkup } from 'react-dom/server';
import { StrictMode, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const dashCalls = vi.hoisted(() => ({
  credentials: [] as Array<{ requestType: string; value: boolean }>,
  handlers: [] as Array<(event: unknown) => void>,
  initialized: [] as Array<{ autoPlay: boolean; startTime: number | string; url: string }>,
  resets: 0,
  settings: [] as unknown[],
  streamInitialized: [] as Array<() => void>,
  videoTracks: [{}] as unknown[],
}));

// These tests own the player and fetch sequence; the queue rail (and its GET) is covered in v1_watch_queue.test.tsx.
vi.mock('./features/watch/WatchQueue', async (importOriginal) => ({ ...(await importOriginal<object>()), WatchQueuePanel: () => null }));

vi.mock('hls.js', () => import('./test/fakeHls'));
const climbs = vi.hoisted(() => ({ started: 0, stopped: 0 }));
vi.mock('./dashClimb', () => ({ startDashClimb: () => { climbs.started += 1; return () => { climbs.stopped += 1; }; } }));

vi.mock('dashjs', () => {
  const MediaPlayer = Object.assign(() => ({
    create: () => ({
      initialize: (_video: HTMLVideoElement, url: string, autoPlay: boolean, startTime: number | string) => {
        dashCalls.initialized.push({ autoPlay, startTime, url });
      },
      on: (event: string, handler: (event: unknown) => void) => {
        if (event === 'error') dashCalls.handlers.push(handler);
        else dashCalls.streamInitialized.push(handler as () => void);
      },
      getTracksFor: () => dashCalls.videoTracks,
      reset: () => { dashCalls.resets += 1; },
      setXHRWithCredentialsForType: (requestType: string, value: boolean) => { dashCalls.credentials.push({ requestType, value }); },
      updateSettings: (value: unknown) => { dashCalls.settings.push(value); },
    }),
  }), { events: { ERROR: 'error', STREAM_INITIALIZED: 'streamInitialized' } });
  return { MediaPlayer };
});

import { getSession } from './api';
import { hlsCalls, resetHlsCalls } from './test/fakeHls';
import { DASH_TOP_RUNG_KBPS, RemotePlayer } from './remotePlayer';
import { watchSurface } from './test/watchSurface';
import type { LibraryItem, RemotePlayback, YouTubeSearchResult } from './types';

afterEach(async () => {
  // Let unmount-scheduled stream releases fire now, not inside the next test's fetch mock.
  await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
});

describe('RemotePlayer', () => {
  beforeEach(() => {
    climbs.started = 0;
    climbs.stopped = 0;
    resetHlsCalls();
    dashCalls.handlers = [];
    dashCalls.initialized = [];
    dashCalls.resets = 0;
    dashCalls.credentials = [];
    dashCalls.settings = [];
    dashCalls.streamInitialized = [];
    dashCalls.videoTracks = [{}];
    vi.restoreAllMocks();
  });

  const ladder = [
    { rendition_id: 'r720', width: 1280, height: 720, video_codec: 'av1', audio_codec: 'aac', container: 'mp4', content_type: 'application/dash+xml', display_label: '720p' },
    { rendition_id: 'r2160', width: 3840, height: 2160, video_codec: 'av1', audio_codec: 'aac', container: 'mp4', content_type: 'application/dash+xml', display_label: '4K' },
  ];
  const adaptive = (streamId: string, selected: string, generation = 1): RemotePlayback => ({
    status: 'ready', stream_id: streamId, transport: 'dash', media_kind: 'video',
    playback_url: `/api/remote-streams/${streamId}/dash/${generation}/manifest.mpd`, content_type: 'application/dash+xml',
    has_video: true, has_audio: true, seekable: true, selected_rendition_id: selected, auto_available: true, renditions: ladder,
  });

  it('plays Auto through dash.js ABR from the top rung, stepping down only on measured trouble, and reports the active resolution', async () => {
    const view = render(<RemotePlayer playback={adaptive('auto-abr', 'auto')} poster={null} title="Auto" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));
    expect(climbs.started).toBe(1); // and climbs back once the trouble passes
    expect(dashCalls.settings).toEqual([{ streaming: { abr: { initialBitrate: { video: DASH_TOP_RUNG_KBPS }, rules: { throughputRule: { active: false }, bolaRule: { active: false }, droppedFramesRule: { active: true } } } } }]);
    const select = screen.getByLabelText('Remote playback quality') as HTMLSelectElement;
    expect(select.value).toBe('auto');
    expect([...select.options].map((option) => option.text)).toEqual(['Auto', '720p', '4K']);

    const video = screen.getByTitle('DASH remote stream') as HTMLVideoElement;
    Object.defineProperty(video, 'videoHeight', { configurable: true, value: 720 });
    fireEvent(video, new Event('resize'));
    expect(select.options[0].text).toBe('Auto (720p)');
    view.unmount();
    expect(climbs.stopped).toBe(1);
  });

  it('switches from a pinned quality back to Auto without losing time or play state', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(adaptive('auto-return', 'auto', 3)), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    render(<RemotePlayer playback={adaptive('auto-return', 'r2160', 2)} poster={null} title="Auto return" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));
    expect(dashCalls.settings).toEqual([]);
    expect(climbs.started).toBe(0); // a pinned quality never changes by itself
    const video = screen.getByTitle('DASH remote stream') as HTMLVideoElement;
    video.currentTime = 42;
    Object.defineProperty(video, 'paused', { configurable: true, value: false });

    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'auto' } });

    await waitFor(() => expect(dashCalls.initialized).toHaveLength(2));
    expect(fetchMock).toHaveBeenCalledWith('/api/remote-streams/auto-return/renditions/auto/select', expect.objectContaining({ method: 'POST' }));
    expect(dashCalls.initialized[1]).toEqual({ autoPlay: true, startTime: 42, url: '/api/remote-streams/auto-return/dash/3/manifest.mpd' });
  });

  it('offers Switch to Auto once after sustained buffering and never changes a pinned quality by itself', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    render(<RemotePlayer playback={adaptive('pinned-stalls', 'r2160')} poster={null} title="Pinned" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));
    const video = screen.getByTitle('DASH remote stream') as HTMLVideoElement;

    fireEvent.waiting(video); // startup buffering is expected
    fireEvent.playing(video);
    fireEvent.waiting(video);
    fireEvent.playing(video);
    fireEvent.waiting(video);
    expect(screen.queryByText('Switch to Auto')).toBeNull();
    fireEvent.playing(video);
    fireEvent.waiting(video);

    expect(await screen.findByText('4K keeps pausing to buffer.')).toBeTruthy();
    screen.getByRole('button', { name: 'Keep 4K' }).focus();
    await user.keyboard('{Enter}');
    expect(screen.queryByText('Switch to Auto')).toBeNull();
    for (let stall = 0; stall < 4; stall += 1) {
      fireEvent.playing(video);
      fireEvent.waiting(video);
    }
    expect(screen.queryByText('Switch to Auto')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect((screen.getByLabelText('Remote playback quality') as HTMLSelectElement).value).toBe('r2160');
  });

  it('fails honestly instead of playing sound only when the browser cannot decode the video', async () => {
    render(<RemotePlayer playback={adaptive('undecodable', 'r2160')} poster={null} title="Undecodable" />);
    await waitFor(() => expect(dashCalls.streamInitialized).toHaveLength(1));
    dashCalls.videoTracks = [];
    act(() => dashCalls.streamInitialized[0]());
    expect(await screen.findByText('This browser cannot decode the selected quality. Choose another quality to continue.')).toBeTruthy();
    expect(dashCalls.resets).toBe(1);
    expect(screen.getByLabelText('Remote playback quality')).toBeTruthy();
  });

  it('switches to Auto from the buffering suggestion', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(adaptive('pinned-accept', 'auto', 2)), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    render(<RemotePlayer playback={adaptive('pinned-accept', 'r2160')} poster={null} title="Pinned" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));
    const video = screen.getByTitle('DASH remote stream') as HTMLVideoElement;
    for (let stall = 0; stall < 3; stall += 1) {
      fireEvent.playing(video);
      fireEvent.waiting(video);
    }

    fireEvent.click(await screen.findByRole('button', { name: 'Switch to Auto' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/remote-streams/pinned-accept/renditions/auto/select', expect.objectContaining({ method: 'POST' })));
    await waitFor(() => expect((screen.getByLabelText('Remote playback quality') as HTMLSelectElement).value).toBe('auto'));
    expect(screen.queryByText('Switch to Auto')).toBeNull();
  });

  it('uses only the stable backend stream URL for progressive playback', () => {
    const { container } = render(
      <RemotePlayer
        playback={{
          status: 'ready',
          stream_id: 'opaque-stream',
          transport: 'progressive',
          media_kind: 'video',
          playback_url: '/api/remote-streams/opaque-stream/content',
          content_type: 'video/mp4',
          has_video: true,
          has_audio: true,
          seekable: true,
        }}
        poster="https://images.example.test/poster.jpg"
        title="A video"
      />,
    );

    expect(container.querySelector('video')?.getAttribute('src')).toBe('/api/remote-streams/opaque-stream/content');
    expect(container.innerHTML).not.toContain('googlevideo');
  });

  it('applies a resume checkpoint that arrives after media metadata without rewinding newer playback', async () => {
    const playback: RemotePlayback = {
      status: 'ready', stream_id: 'resume-stream', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/resume-stream/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    const { rerender } = render(<RemotePlayer playback={playback} poster={null} resumePosition={null} title="Resume" />);
    const media = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 4, writable: true },
      duration: { configurable: true, value: 300 },
      readyState: { configurable: true, value: HTMLMediaElement.HAVE_METADATA },
    });

    rerender(<RemotePlayer playback={playback} poster={null} resumePosition={90} title="Resume" />);
    await waitFor(() => expect(media.currentTime).toBe(90));

    media.currentTime = 140;
    rerender(<RemotePlayer playback={playback} poster={null} resumePosition={120} title="Resume" />);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    expect(media.currentTime).toBe(140);
  });

  it('starts DASH acquisition from the saved checkpoint', async () => {
    render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'resume-dash', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/resume-dash/manifest.mpd', content_type: 'application/dash+xml', has_video: true, has_audio: true, seekable: true,
    }} poster={null} resumePosition={88} title="Resume DASH" />);

    await waitFor(() => expect(dashCalls.initialized).toContainEqual({
      autoPlay: true,
      startTime: 88,
      url: '/api/remote-streams/resume-dash/manifest.mpd',
    }));
  });

  it('shows the selected quality even when the provider only exposes one rendition', () => {
    render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'single-quality', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/single-quality/content', content_type: 'video/mp4',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'r360',
      renditions: [
        { rendition_id: 'r360', width: 640, height: 360, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '360p' },
      ],
    }} poster={null} title="Single quality" />);

    const quality = screen.getByLabelText('Remote playback quality') as HTMLSelectElement;
    expect(quality.value).toBe('r360');
    expect(quality.disabled).toBe(true);
    expect(screen.getByRole('option', { name: '360p' })).toBeTruthy();
    expect(screen.getByText('Only 360p is available from this source.')).toBeTruthy();
  });

  it('offers recovery choices when a previously selected rendition is unavailable', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'ready', stream_id: 'recover-quality', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/recover-quality/renditions/r720/content', content_type: 'video/mp4',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'r720',
      renditions: [
        { rendition_id: 'r720', width: 1280, height: 720, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '720p' },
      ],
    }), { status: 200 }));
    render(<RemotePlayer playback={{
      status: 'unsupported', stream_id: 'recover-quality', transport: null, media_kind: null,
      playback_url: null, content_type: null, has_video: false, has_audio: false, seekable: false,
      fallback_code: 'selected_rendition_unavailable', fallback_message: 'Choose another quality to continue.',
      selected_rendition_id: null,
      renditions: [
        { rendition_id: 'r720', width: 1280, height: 720, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '720p' },
      ],
    }} poster={null} title="Recover quality" />);

    const selector = screen.getByLabelText('Remote playback quality') as HTMLSelectElement;
    expect(selector.value).toBe('');
    expect(selector.disabled).toBe(false);
    expect(screen.getByRole('option', { name: 'Choose quality' })).toBeTruthy();
    expect(screen.getByText('720p is available. Choose it to resume.')).toBeTruthy();
    await user.selectOptions(selector, 'r720');

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/remote-streams/recover-quality/renditions/r720/select',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('renders an audio-only stream as audio instead of a silent video', () => {
    const { container } = render(
      <RemotePlayer
        playback={{
          status: 'ready',
          stream_id: 'opaque-audio',
          transport: 'progressive',
          media_kind: 'audio',
          playback_url: '/api/remote-streams/opaque-audio/content',
          content_type: 'audio/webm',
          has_video: false,
          has_audio: true,
          seekable: true,
        }}
        poster={null}
        title="An episode"
      />,
    );

    expect(container.querySelector('audio')?.getAttribute('src')).toBe('/api/remote-streams/opaque-audio/content');
    expect(container.querySelector('video')).toBeNull();
  });

  it('shows the server-provided unsupported fallback instead of a player', () => {
    const html = renderToStaticMarkup(
      <RemotePlayer
        playback={{
          status: 'unsupported',
          stream_id: 'opaque-unsupported',
          transport: null,
          media_kind: null,
          playback_url: null,
          content_type: null,
          has_video: false,
          has_audio: false,
          seekable: false,
          fallback_code: 'no_audio',
          fallback_message: 'This source has no compatible stream with audio.',
        }}
        poster={null}
        title="Unsupported video"
      />,
    );

    expect(html).toContain('This source has no compatible stream with audio.');
    expect(html).not.toContain('<video');
    expect(html).not.toContain('<audio');
  });

  it('loads split audio and video through hls.js using the stable manifest URL', async () => {
    const canPlayType = vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
    const { unmount } = render(
      <RemotePlayer
        playback={{
          status: 'ready',
          stream_id: 'opaque-hls',
          transport: 'hls',
          media_kind: 'video',
          playback_url: '/api/remote-streams/opaque-hls/hls/1/manifest.m3u8',
          content_type: 'application/vnd.apple.mpegurl',
          has_video: true,
          has_audio: true,
          seekable: true,
        }}
        poster={null}
        title="Split video"
      />,
    );

    await waitFor(() => expect(hlsCalls.sources).toEqual(['/api/remote-streams/opaque-hls/hls/1/manifest.m3u8']));
    expect(hlsCalls.attached).toHaveLength(1);
    expect(screen.getByText('Preparing the remote stream')).toBeTruthy();
    unmount();
    expect(hlsCalls.destroyed).toBe(1);
    canPlayType.mockRestore();
  });

  it('keeps hls.js when the browser also claims native HLS but has MediaSource (Chrome)', async () => {
    // Chrome's native HLS plays YouTube's demuxed live audio whole segments off its video.
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('maybe');
    vi.stubGlobal('MediaSource', class {});
    const { container, unmount } = render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'opaque-live', transport: 'hls', media_kind: 'video',
      playback_url: '/api/remote-streams/opaque-live/relay/1/master.m3u8', content_type: 'application/vnd.apple.mpegurl',
      has_video: true, has_audio: true, seekable: false, live: true,
    }} poster={null} title="Live" />);

    await waitFor(() => expect(hlsCalls.sources).toEqual(['/api/remote-streams/opaque-live/relay/1/master.m3u8']));
    expect(container.querySelector('video')?.getAttribute('src')).toBeNull();
    unmount();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('starts a multi-rendition stream at its highest rendition (owner: highest fidelity first, step down only on trouble)', async () => {
    const { unmount } = render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'opaque-live', transport: 'hls', media_kind: 'video',
      playback_url: '/api/remote-streams/opaque-live/relay/1/master.m3u8', content_type: 'application/vnd.apple.mpegurl',
      has_video: true, has_audio: true, seekable: false, live: true,
    }} poster={null} title="Live" />);
    await waitFor(() => expect(hlsCalls.onManifestParsed).toHaveLength(1));
    hlsCalls.onManifestParsed[0]('hlsManifestParsed', { levels: [{}, {}, {}, {}] });
    expect(hlsCalls.instances.at(-1)?.startLevel).toBe(3);
    // With a healthy buffer, ABR judges a level by what its segments really weigh, not its declared peak: YouTube live
    // declares 1080p at 5.4 Mb/s for ~0.8 Mb/s of segments, and dropped to 720p/480p with 15 s buffered and no stall.
    expect(hlsCalls.configs.at(-1)).toMatchObject({ abrEwmaDefaultEstimate: 20_000_000, abrMaxWithRealBitrate: true });
    unmount();
  });

  it('offers the relayed ladder as a quality menu: a pinned height switches at once, Auto hands back to ABR', async () => {
    const { unmount } = render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'opaque-live', transport: 'hls', media_kind: 'video',
      playback_url: '/api/remote-streams/opaque-live/relay/1/master.m3u8', content_type: 'application/vnd.apple.mpegurl',
      has_video: true, has_audio: true, seekable: false, live: true,
    }} poster={null} title="Live" />);
    await waitFor(() => expect(hlsCalls.onManifestParsed).toHaveLength(1));
    // hls.js orders levels by bitrate; a height may appear twice (YouTube's 240p over two audio groups).
    act(() => hlsCalls.onManifestParsed[0]('hlsManifestParsed', { levels: [{ height: 240 }, { height: 240 }, { height: 720 }, { height: 1080 }] }));
    const quality = screen.getByLabelText('Remote playback quality') as HTMLSelectElement;
    expect([...quality.options].map((option) => option.textContent)).toEqual(['Auto', '1080p', '720p', '240p']);
    const player = hlsCalls.instances.at(-1) as unknown as { currentLevel?: number; nextLevel?: number };
    fireEvent.change(quality, { target: { value: '2' } });
    expect(player.currentLevel).toBe(2);
    // Chrome keeps showing the old height until the next keyframe (a whole 5 s YouTube segment) when the pick lands at
    // a segment start; once the picked level's segment under the playhead is buffered, a same-time seek shows it now.
    const video = document.querySelector('video') as HTMLVideoElement;
    let seekedTo: number | null = null;
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => 3610.01, set: (value: number) => { seekedTo = value; } });
    const buffered = (level: number, start: number) => hlsCalls.listeners.hlsFragBuffered.forEach((listener) => listener('hlsFragBuffered', { frag: { type: 'main', level, start, end: start + 5 } } as never));
    buffered(3, 3610); // another level's segment: not the pick
    expect(seekedTo).toBeNull();
    buffered(2, 3610);
    expect(seekedTo).toBe(3610.01);
    seekedTo = null;
    buffered(2, 3610); // once per pick
    expect(seekedTo).toBeNull();
    fireEvent.change(quality, { target: { value: '3' } });
    buffered(3, 3615); // the pick's first segment is ahead of the playhead: it shows by itself at 3615, no seek ever
    buffered(3, 3610);
    expect(seekedTo).toBeNull();
    fireEvent.change(quality, { target: { value: 'auto' } });
    expect(player.nextLevel).toBe(-1);
    unmount();
  });

  it('loads split MP4 audio and video through dash.js without assigning the MPD to video.src', async () => {
    const { unmount } = render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'opaque-dash', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/opaque-dash/dash/1/manifest.mpd', content_type: 'application/dash+xml',
      has_video: true, has_audio: true, seekable: true,
    }} poster={null} title="DASH video" />);

    await waitFor(() => expect(dashCalls.initialized).toEqual([{
      autoPlay: true,
      startTime: 0,
      url: '/api/remote-streams/opaque-dash/dash/1/manifest.mpd',
    }]));
    expect(screen.getByTitle('DASH remote stream').getAttribute('src')).toBeNull();
    expect(dashCalls.credentials).toEqual([
      { requestType: 'MPD', value: true },
      { requestType: 'InitializationSegment', value: true },
      { requestType: 'IndexSegment', value: true },
      { requestType: 'MediaSegment', value: true },
    ]);
    expect(screen.getByText('Preparing the remote stream')).toBeTruthy();

    unmount();
    expect(dashCalls.resets).toBe(1);
  });

  it('does not restart DASH playback when a parent render replaces the reacquire callback', async () => {
    const playback: RemotePlayback = {
      status: 'ready', stream_id: 'stable-dash', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/stable-dash/dash/1/manifest.mpd', content_type: 'application/dash+xml',
      has_video: true, has_audio: true, seekable: true,
    };
    const { rerender } = render(<RemotePlayer onReacquire={vi.fn()} playback={playback} poster={null} title="Stable DASH" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));

    rerender(<RemotePlayer onReacquire={vi.fn()} playback={playback} poster={null} title="Stable DASH" />);

    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    expect(dashCalls.initialized).toHaveLength(1);
    expect(dashCalls.resets).toBe(0);
  });

  it('recovers from a native DASH media error without waiting for a dash.js error event', async () => {
    render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'native-dash-error', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/native-dash-error/dash/1/manifest.mpd', content_type: 'application/dash+xml',
      has_video: true, has_audio: true, seekable: true,
    }} poster={null} title="Native DASH error" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));

    fireEvent.error(screen.getByTitle('DASH remote stream'));

    await waitFor(() => expect(dashCalls.initialized).toHaveLength(2));
    expect(screen.queryByText('Stream unavailable')).toBeNull();
    expect(screen.getByTitle('DASH remote stream')).toBeTruthy();
  });

  it('lets dash.js own DASH recovery so one failure cannot consume both retries', async () => {
    const refreshed: RemotePlayback = {
      status: 'ready', stream_id: 'dash-recovery', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/dash-recovery/dash/2/manifest.mpd', content_type: 'application/dash+xml',
      has_video: true, has_audio: true, seekable: true,
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(refreshed), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    render(<RemotePlayer playback={{ ...refreshed, playback_url: '/api/remote-streams/dash-recovery/dash/1/manifest.mpd' }} poster={null} title="DASH recovery" />);
    await waitFor(() => expect(dashCalls.handlers).toHaveLength(1));
    const firstMedia = screen.getByTitle('DASH remote stream');

    act(() => dashCalls.handlers[0]({ error: 'network' }));
    fireEvent.error(firstMedia);
    expect(fetchMock).not.toHaveBeenCalled();
    await waitFor(() => expect(dashCalls.handlers).toHaveLength(2));

    act(() => dashCalls.handlers[1]({ error: 'network-again' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/dash-recovery/refresh',
      expect.objectContaining({ method: 'POST' }),
    ));
  });

  it('resumes the same DASH position after an exact manual quality change', async () => {
    const renditions = [
      { rendition_id: 'r720', width: 1280, height: 720, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'application/dash+xml', display_label: '720p' },
      { rendition_id: 'r1080', width: 1920, height: 1080, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'application/dash+xml', display_label: '1080p' },
    ];
    const selected: RemotePlayback = {
      status: 'ready', stream_id: 'dash-quality', transport: 'dash', media_kind: 'video',
      playback_url: '/api/remote-streams/dash-quality/dash/2/manifest.mpd', content_type: 'application/dash+xml',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'r720', renditions,
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(selected), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    render(<RemotePlayer playback={{ ...selected, playback_url: '/api/remote-streams/dash-quality/dash/1/manifest.mpd', selected_rendition_id: 'r1080' }} poster={null} title="DASH quality" />);
    await waitFor(() => expect(dashCalls.initialized).toHaveLength(1));
    const video = screen.getByTitle('DASH remote stream') as HTMLVideoElement;
    video.currentTime = 42;
    Object.defineProperty(video, 'paused', { configurable: true, value: false });

    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'r720' } });

    await waitFor(() => expect(dashCalls.initialized).toHaveLength(2));
    expect(dashCalls.initialized[1]).toEqual({
      autoPlay: true,
      startTime: 42,
      url: '/api/remote-streams/dash-quality/dash/2/manifest.mpd',
    });
  });

  it('destroys fatal HLS instances, retries locally once, then refreshes the backend once', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const refreshed: RemotePlayback = {
      status: 'ready', stream_id: 'opaque-hls', transport: 'hls', media_kind: 'video',
      playback_url: '/api/remote-streams/opaque-hls/hls/2/manifest.m3u8', content_type: 'application/vnd.apple.mpegurl',
      has_video: true, has_audio: true, seekable: true,
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(refreshed), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    render(<RemotePlayer playback={{ ...refreshed, playback_url: '/api/remote-streams/opaque-hls/hls/1/manifest.m3u8' }} poster={null} title="Split" />);
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(1));
    const firstMedia = screen.getByTitle('HLS remote stream') as HTMLVideoElement;
    Object.defineProperties(firstMedia, {
      currentTime: { configurable: true, value: 73, writable: true },
      paused: { configurable: true, value: false },
    });

    hlsCalls.handlers[0]('error', { fatal: true, type: 'networkError' });
    expect(hlsCalls.networkRecoveries).toBe(1);
    expect(hlsCalls.destroyed).toBe(1);
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(2));
    const retryMedia = screen.getByTitle('HLS remote stream') as HTMLVideoElement;
    expect(retryMedia).not.toBe(firstMedia);
    Object.defineProperty(retryMedia, 'duration', { configurable: true, value: 300 });
    fireEvent.canPlay(retryMedia);
    expect(retryMedia.currentTime).toBe(73);
    expect(play).toHaveBeenCalled();
    hlsCalls.handlers[1]('error', { fatal: true, type: 'networkError' });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/opaque-hls/refresh',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    ));
    expect(hlsCalls.destroyed).toBeGreaterThanOrEqual(2);
    await waitFor(() => expect(hlsCalls.sources).toContain('/api/remote-streams/opaque-hls/hls/2/manifest.m3u8'));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('recovers a guarded HLS relay stream by refreshing to the next generation master', async () => {
    // A relay session serves transport: "hls" with a /relay/{gen}/master.m3u8 URL.
    // When an expired upstream segment returns 409, hls.js raises a fatal network
    // error; the player must refresh once and swap to the re-resolved generation.
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
    const refreshed: RemotePlayback = {
      status: 'ready', stream_id: 'relay-stream', transport: 'hls', media_kind: 'video',
      playback_url: '/api/remote-streams/relay-stream/relay/2/master.m3u8', content_type: 'application/vnd.apple.mpegurl',
      has_video: true, has_audio: true, seekable: true,
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(refreshed), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    render(<RemotePlayer playback={{ ...refreshed, playback_url: '/api/remote-streams/relay-stream/relay/1/master.m3u8' }} poster={null} title="Twitch VOD" />);
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(1));

    hlsCalls.handlers[0]('error', { fatal: true, type: 'networkError' });
    await waitFor(() => expect(hlsCalls.handlers).toHaveLength(2));
    hlsCalls.handlers[1]('error', { fatal: true, type: 'networkError' });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/relay-stream/refresh',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    ));
    await waitFor(() => expect(hlsCalls.sources).toContain('/api/remote-streams/relay-stream/relay/2/master.m3u8'));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('refreshes progressive playback once even when the stable URL is unchanged', async () => {
    const playback = {
      status: 'ready' as const, stream_id: 'opaque-progressive', transport: 'progressive' as const, media_kind: 'video' as const,
      playback_url: '/api/remote-streams/opaque-progressive/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(new Response(JSON.stringify(playback), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(<RemotePlayer playback={playback} poster={null} title="Progressive" />);
    const original = screen.getByTitle('Progressive remote stream');
    Object.defineProperties(original, {
      currentTime: { configurable: true, value: 44, writable: true },
      duration: { configurable: true, value: 300 },
      paused: { configurable: true, value: true },
    });

    fireEvent.error(original);
    await waitFor(() => expect(screen.getByTitle('Progressive remote stream')).not.toBe(original));
    const localRetry = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperty(localRetry, 'duration', { configurable: true, value: 300 });
    fireEvent.canPlay(localRetry);
    expect(localRetry.currentTime).toBe(44);
    fireEvent.error(localRetry);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    await waitFor(() => expect(screen.getByTitle('Progressive remote stream')).not.toBe(localRetry));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    await waitFor(() => expect(screen.getByText('The stream could not be played. You can still download it to the vault.')).toBeTruthy());
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);

    // Automatic recovery is spent, but an explicit Refresh never dead-ends (#139).
    fireEvent.click(screen.getAllByRole('button', { name: 'Refresh stream' })[0]);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2));
    await waitFor(() => expect(screen.queryByText('The stream could not be played. You can still download it to the vault.')).toBeNull());
  });

  it('reacquires playback when a backend restart invalidates the stream session', async () => {
    const playback = {
      status: 'ready' as const, stream_id: 'expired-after-restart', transport: 'progressive' as const, media_kind: 'video' as const,
      playback_url: '/api/remote-streams/expired-after-restart/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => Promise.resolve(
      new Response(null, { status: init?.method === 'POST' ? 404 : 204 }),
    ));
    const reacquire = vi.fn().mockResolvedValue(true);
    render(<RemotePlayer onReacquire={reacquire} playback={playback} poster={null} title="Restart recovery" />);

    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));

    await waitFor(() => expect(reacquire).toHaveBeenCalledTimes(1));
  });

  it('installs a replacement descriptor after reacquiring a restarted backend session', async () => {
    const expired: RemotePlayback = {
      status: 'ready', stream_id: 'expired-session', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/expired-session/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    const replacement: RemotePlayback = {
      ...expired,
      stream_id: 'replacement-session',
      playback_url: '/api/remote-streams/replacement-session/content',
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => Promise.resolve(
      new Response(null, { status: init?.method === 'POST' ? 404 : 204 }),
    ));

    function Harness() {
      const [playback, setPlayback] = useState(expired);
      return <RemotePlayer onReacquire={async () => { setPlayback(replacement); return true; }} playback={playback} poster={null} title="Restart replacement" />;
    }

    render(<Harness />);
    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));

    await waitFor(() => expect(screen.getByTitle('Progressive remote stream').getAttribute('src')).toBe('/api/remote-streams/replacement-session/content'));
    expect(screen.queryByText('The stream could not be played. You can still download it to the vault.')).toBeNull();
  });

  it('reacquires a restarted backend session from manual quality selection', async () => {
    const playback: RemotePlayback = {
      status: 'ready', stream_id: 'expired-quality', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/expired-quality/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
      selected_rendition_id: 'r360',
      renditions: [
        { rendition_id: 'r360', width: 640, height: 360, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '360p' },
        { rendition_id: 'r1080', width: 1920, height: 1080, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '1080p' },
      ],
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 404 }));
    const reacquire = vi.fn().mockResolvedValue(true);
    render(<RemotePlayer onReacquire={reacquire} playback={playback} poster={null} title="Restart quality" />);

    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'r1080' } });

    await waitFor(() => expect(reacquire).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('That quality could not be selected. The previous stream is still available.')).toBeNull();
  });

  it('keeps recovery pending when another media error arrives during re-acquisition', async () => {
    const playback: RemotePlayback = {
      status: 'ready', stream_id: 'pending-reacquire', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/pending-reacquire/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => Promise.resolve(
      new Response(null, { status: init?.method === 'POST' ? 404 : 204 }),
    ));
    let finishReacquire: ((value: boolean) => void) | undefined;
    const reacquire = vi.fn(() => new Promise<boolean>((resolve) => { finishReacquire = resolve; }));
    render(<RemotePlayer onReacquire={reacquire} playback={playback} poster={null} title="Pending recovery" />);

    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    await waitFor(() => expect(reacquire).toHaveBeenCalledTimes(1));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));

    expect(screen.queryByText('The stream could not be played. You can still download it to the vault.')).toBeNull();
    await act(async () => finishReacquire?.(true));
  });

  it('aborts and ignores a deferred refresh when playback changes from A to B', async () => {
    let resolveRefresh: ((response: Response) => void) | undefined;
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => {
      if (init?.method === 'POST') return new Promise<Response>((resolve) => { resolveRefresh = resolve; });
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const playbackA: RemotePlayback = {
      status: 'ready', stream_id: 'stream-a', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/stream-a/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    const playbackB: RemotePlayback = {
      status: 'ready', stream_id: 'stream-b', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/stream-b/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    };
    const { rerender } = render(<RemotePlayer playback={playbackA} poster={null} title="A" />);
    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    fireEvent.error(screen.getByTitle('Progressive remote stream'));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1));
    const refreshSignal = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')?.[1]?.signal;

    rerender(<RemotePlayer playback={playbackB} poster={null} title="B" />);
    await waitFor(() => expect(screen.getByTitle('Progressive remote stream').getAttribute('src')).toBe('/api/remote-streams/stream-b/content'));
    expect(refreshSignal?.aborted).toBe(true);
    resolveRefresh?.(new Response(JSON.stringify(playbackA), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));

    expect(screen.getByTitle('Progressive remote stream').getAttribute('src')).toBe('/api/remote-streams/stream-b/content');
  });

  it('pins a manual quality and restores playback time and playing state on the replacement source', async () => {
    const selected: RemotePlayback = {
      status: 'ready', stream_id: 'quality-stream', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/quality-stream/renditions/r720/content', content_type: 'video/mp4',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'r720',
      renditions: [
        { rendition_id: 'r720', width: 1280, height: 720, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '720p' },
        { rendition_id: 'r1080', width: 1920, height: 1080, video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '1080p' },
      ],
    };
    const initial = { ...selected, playback_url: '/api/remote-streams/quality-stream/renditions/r1080/content', selected_rendition_id: 'r1080' };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(selected), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    render(<RemotePlayer playback={initial} poster={null} title="Quality" />);
    const original = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(original, {
      currentTime: { configurable: true, value: 42, writable: true },
      paused: { configurable: true, value: false },
    });

    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'r720' } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/quality-stream/renditions/r720/select',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    ));
    const replacement = await screen.findByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperty(replacement, 'duration', { configurable: true, value: 300 });
    fireEvent.canPlay(replacement);
    expect(replacement.currentTime).toBe(42);
    expect(play).toHaveBeenCalled();
  });

  it('keeps a paused stream paused at the same time after a quality change', async () => {
    const selected: RemotePlayback = {
      status: 'ready', stream_id: 'paused-quality', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/paused-quality/renditions/low/content', content_type: 'video/mp4',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'low',
      renditions: [
        { rendition_id: 'low', video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '720p' },
        { rendition_id: 'high', video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '1080p' },
      ],
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(selected), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
    render(<RemotePlayer playback={{ ...selected, playback_url: '/api/remote-streams/paused-quality/renditions/high/content', selected_rendition_id: 'high' }} poster={null} title="Paused quality" />);
    const original = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(original, {
      currentTime: { configurable: true, value: 27, writable: true },
      paused: { configurable: true, value: true },
    });

    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'low' } });
    await waitFor(() => expect(screen.getByTitle('Progressive remote stream').getAttribute('src')).toBe('/api/remote-streams/paused-quality/renditions/low/content'));
    const replacement = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperty(replacement, 'duration', { configurable: true, value: 200 });
    fireEvent.canPlay(replacement);
    expect(replacement.currentTime).toBe(27);
    expect(pause).toHaveBeenCalled();
    expect(play).not.toHaveBeenCalled();
  });

  it('keeps the current source usable when quality selection fails', async () => {
    const playback: RemotePlayback = {
      status: 'ready', stream_id: 'quality-failure', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/quality-failure/renditions/high/content', content_type: 'video/mp4',
      has_video: true, has_audio: true, seekable: true, selected_rendition_id: 'high',
      renditions: [
        { rendition_id: 'low', video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '720p' },
        { rendition_id: 'high', video_codec: 'avc', audio_codec: 'aac', container: 'mp4', content_type: 'video/mp4', display_label: '1080p' },
      ],
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 503 }));
    render(<RemotePlayer playback={playback} poster={null} title="Quality failure" />);
    fireEvent.change(screen.getByLabelText('Remote playback quality'), { target: { value: 'low' } });
    expect(await screen.findByText('That quality could not be selected. The previous stream is still available.')).toBeTruthy();
    expect(screen.getByTitle('Progressive remote stream').getAttribute('src')).toBe('/api/remote-streams/quality-failure/renditions/high/content');
  });

  it('releases the owned backend stream after the player truly unmounts, with the session CSRF token', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => String(url).endsWith('/api/session/me')
      ? new Response(JSON.stringify({ user: null, csrf_token: 'csrf-release' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(null, { status: 204 }));
    await getSession();
    const { unmount } = render(<StrictMode><RemotePlayer playback={{
      status: 'ready', stream_id: 'release-me', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/release-me/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    }} poster={null} title="Release" /></StrictMode>);

    await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0);

    unmount();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/release-me',
      expect.objectContaining({ method: 'DELETE', credentials: 'include', keepalive: true, headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-release' }) }),
    ));
  });

  it('releases its stream when the page goes away without unmounting (a hard navigation or a closed tab)', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => String(url).endsWith('/api/session/me')
      ? new Response(JSON.stringify({ user: null, csrf_token: 'csrf-pagehide' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(null, { status: 204 }));
    await getSession();
    render(<RemotePlayer playback={{
      status: 'ready', stream_id: 'leave-me', transport: 'progressive', media_kind: 'video',
      playback_url: '/api/remote-streams/leave-me/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true,
    }} poster={null} title="Leave" />);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0);

    window.dispatchEvent(new Event('pagehide'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote-streams/leave-me',
      expect.objectContaining({ method: 'DELETE', credentials: 'include', keepalive: true, headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-pagehide' }) }),
    ));
  });
});

describe('WatchSurface remote playback contract', () => {
  it('presents live capabilities without creating VOD controls or enabled acquisition', () => {
    const html = renderToStaticMarkup(
      watchSurface({
        selection: { kind: 'remote', item: { id: 'live-1', title: 'Live now', webpage_url: 'https://www.youtube.com/watch?v=live-1' }, preview: {
          kind: 'video', entries: [], playback: null,
          capabilities: { provider: 'youtube', lifecycle: 'live', can_play: false, play_reason: 'live_playback_not_supported', can_acquire: false, acquire_reason: 'live_acquisition_not_supported', chat: { live: 'unavailable', replay: 'unavailable' } },
          raw: { extractor_key: 'Youtube', is_live: true },
        } },
      }),
    );

    expect(html).toMatch(/class="g-label g-watch-kicker">.*?class="g-live is-live[^"]*"[^>]*>.*?LIVE/);
    expect(html).toContain('Live playback is not available yet.');
    expect(html).toContain('Live acquisition is not available yet.');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*Save to library/);
    expect(html).not.toContain('/api/playback/remote/');
  });

  it('enables acquisition for the Twitch VOD tracer and implies no replay chat', () => {
    // Issue #95: a completed Twitch VOD reports can_acquire true and
    // chat.replay unavailable. The Download-to-vault affordance is enabled and
    // no replay-chat rail is offered — a kept Twitch VOD never implies historical
    // Twitch chat.
    const html = renderToStaticMarkup(
      watchSurface({
        selection: { kind: 'remote', item: { id: '2170248437', title: 'Twitch VOD', webpage_url: 'https://www.twitch.tv/videos/2170248437' }, preview: {
          kind: 'video', entries: [], playback: null,
          capabilities: { provider: 'twitch', lifecycle: 'completed_live', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
          raw: { extractor_key: 'Twitch', id: '2170248437', was_live: true },
        } },
      }),
    );

    expect(html).toMatch(/class="g-label g-watch-kicker">.*?class="g-live is-ended[^"]*"[^>]*>.*?ENDED/);
    // Acquisition is enabled: the split download-options control renders and the
    // acquire-unavailable note is absent (it only renders when acquire is gated).
    expect(html).toContain('Save to library');
    expect(html).toContain('aria-label="Download options"');
    expect(html).not.toContain('id="watch-acquisition-unavailable"');
    expect(html).not.toContain('Acquisition is not available');
    // No replay-chat surface accompanies the kept Twitch VOD.
    expect(html).not.toContain('Replay chat');
  });

  it('restores and checkpoints remote playback in fifteen-second intervals', async () => {
    const saved = {
      id: 'progress-1', user_id: 'user-1', source_identity: 'youtube:video-1', source_url: 'https://www.youtube.com/watch?v=video-1',
      title: 'Remote video', uploader: 'Channel', artwork_url: null, position_seconds: 90, duration_seconds: 300, completed: false,
      extractor: 'youtube', remote_id: 'video-1', selected_rendition_id: null, checkpoint_client_id: 'server-player', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false,
      last_watched_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => {
      if (init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        return Promise.resolve(new Response(JSON.stringify({ ...saved, position_seconds: 105, checkpoint_client_id: payload.checkpoint_client_id, checkpoint_sequence: payload.checkpoint_sequence, checkpoint_revision: 2 }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    render(watchSurface({
      selection: {
        kind: 'remote',
        item: { id: 'video-1', source: 'youtube', title: 'Remote video', uploader: 'Channel', webpage_url: 'https://www.youtube.com/watch?v=video-1' },
        preview: { kind: 'video', entries: [], playback: { status: 'ready', stream_id: 'remote-progress', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/remote-progress/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true }, raw: { extractor: 'youtube', id: 'video-1', duration: 300 } },
      },
    }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/playback/remote/youtube%3Avideo-1', expect.objectContaining({ credentials: 'include' })));
    const media = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, { currentTime: { configurable: true, value: 0, writable: true }, duration: { configurable: true, value: 300 }, readyState: { configurable: true, value: HTMLMediaElement.HAVE_METADATA } });
    fireEvent.loadedMetadata(media);
    await waitFor(() => expect(media.currentTime).toBe(90));
    media.currentTime = 105;
    fireEvent.timeUpdate(media);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/playback/remote/youtube%3Avideo-1' && init?.method === 'PUT' && String(init.body).includes('"position_seconds":105'))).toBe(true));
  });

  it('sends the channel with each remote checkpoint so watch depth can key to it', async () => {
    const channel = 'UCabcdefghijklmnopqrstuv';
    const saved = {
      id: 'progress-1', user_id: 'user-1', source_identity: 'youtube:video-1', source_url: 'https://www.youtube.com/watch?v=video-1',
      title: 'Remote video', uploader: 'Channel', artwork_url: null, position_seconds: 90, duration_seconds: 300, completed: false,
      extractor: 'youtube', remote_id: 'video-1', selected_rendition_id: null, checkpoint_client_id: 'server-player', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false,
      last_watched_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((_url, init) => {
      if (init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        return Promise.resolve(new Response(JSON.stringify({ ...saved, position_seconds: 105, checkpoint_client_id: payload.checkpoint_client_id, checkpoint_sequence: payload.checkpoint_sequence, checkpoint_revision: 2 }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    });
    render(watchSurface({
      selection: {
        kind: 'remote',
        item: { id: 'video-1', source: 'youtube', title: 'Remote video', uploader: 'Channel', webpage_url: 'https://www.youtube.com/watch?v=video-1' },
        preview: { kind: 'video', entries: [], playback: { status: 'ready', stream_id: 'remote-progress', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/remote-progress/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true }, raw: { extractor: 'youtube', id: 'video-1', duration: 300, channel_id: channel, channel_url: `https://www.youtube.com/channel/${channel}` } },
      },
    }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/playback/remote/youtube%3Avideo-1', expect.objectContaining({ credentials: 'include' })));
    const media = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, { currentTime: { configurable: true, value: 0, writable: true }, duration: { configurable: true, value: 300 }, readyState: { configurable: true, value: HTMLMediaElement.HAVE_METADATA } });
    fireEvent.loadedMetadata(media);
    await waitFor(() => expect(media.currentTime).toBe(90));
    media.currentTime = 105;
    fireEvent.timeUpdate(media);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/playback/remote/youtube%3Avideo-1' && init?.method === 'PUT'
      && String(init.body).includes(`"channel_id":"${channel}"`) && String(init.body).includes(`"channel_url":"https://www.youtube.com/channel/${channel}"`))).toBe(true));
  });

  it('does not recreate cleared progress from the stale playback sample during cleanup', async () => {
    const browser = userEvent.setup();
    const saved = {
      id: 'progress-clear', user_id: 'user-1', source_identity: 'youtube:video-clear', source_url: 'https://www.youtube.com/watch?v=video-clear',
      extractor: 'youtube', remote_id: 'video-clear', title: 'Clear me', uploader: 'Channel', artwork_url: null,
      position_seconds: 90, duration_seconds: 300, completed: false, selected_rendition_id: null,
      checkpoint_client_id: 'server-player', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false,
      last_watched_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    };
    const progressUrl = '/api/playback/remote/youtube%3Avideo-clear';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((url, init) => {
      if (String(url).startsWith(progressUrl) && init?.method === 'DELETE') {
        const query = new URL(String(url), 'http://lumina.test').searchParams;
        return Promise.resolve(new Response(JSON.stringify({ ...saved, position_seconds: 0, checkpoint_client_id: query.get('checkpoint_client_id'), checkpoint_sequence: Number(query.get('checkpoint_sequence')), checkpoint_revision: 2, cleared: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url === progressUrl && init?.method === 'PUT') return Promise.resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      if (url === progressUrl) return Promise.resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    const view = render(watchSurface({
      selection: {
        kind: 'remote',
        item: { id: 'video-clear', source: 'youtube', title: 'Clear me', uploader: 'Channel', webpage_url: 'https://www.youtube.com/watch?v=video-clear' },
        preview: { kind: 'video', entries: [], playback: { status: 'ready', stream_id: 'remote-clear', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/remote-clear/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true }, raw: { extractor: 'youtube', id: 'video-clear', duration: 300 } },
      },
    }));

    await screen.findByRole('button', { name: 'Clear progress' });
    const media = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 90, writable: true },
      duration: { configurable: true, value: 300 },
      readyState: { configurable: true, value: HTMLMediaElement.HAVE_METADATA },
    });
    fireEvent.timeUpdate(media);
    await browser.click(screen.getByRole('button', { name: 'Clear progress' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).startsWith(progressUrl) && init?.method === 'DELETE')).toBe(true));

    media.currentTime = 91;
    fireEvent.timeUpdate(media);
    fireEvent.pause(media);
    view.unmount();
    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    expect(fetchMock.mock.calls.filter(([url, init]) => url === progressUrl && init?.method === 'PUT')).toEqual([]);
  });

  it('does not let a completed clear request hide a newer checkpoint', async () => {
    const browser = userEvent.setup();
    let resolveClear: ((response: Response) => void) | undefined;
    const clearResponse = new Promise<Response>((resolve) => { resolveClear = resolve; });
    const saved = {
      id: 'progress-race', user_id: 'user-1', source_identity: 'youtube:video-race', source_url: 'https://www.youtube.com/watch?v=video-race',
      extractor: 'youtube', remote_id: 'video-race', title: 'Race', uploader: 'Channel', artwork_url: null,
      position_seconds: 90, duration_seconds: 300, completed: false, selected_rendition_id: null,
      checkpoint_client_id: 'server-player', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false,
      last_watched_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    };
    const completed = { ...saved, position_seconds: 300, completed: true, checkpoint_revision: 3 };
    let currentCompleted = completed;
    const progressUrl = '/api/playback/remote/youtube%3Avideo-race';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((url, init) => {
      if (String(url).startsWith(progressUrl) && init?.method === 'DELETE') return clearResponse;
      if (url === progressUrl && init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body));
        currentCompleted = { ...completed, checkpoint_client_id: payload.checkpoint_client_id, checkpoint_sequence: payload.checkpoint_sequence };
        return Promise.resolve(new Response(JSON.stringify(currentCompleted), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      if (url === progressUrl) return Promise.resolve(new Response(JSON.stringify(saved), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    render(watchSurface({
      selection: {
        kind: 'remote',
        item: { id: 'video-race', source: 'youtube', title: 'Race', uploader: 'Channel', webpage_url: 'https://www.youtube.com/watch?v=video-race' },
        preview: { kind: 'video', entries: [], playback: { status: 'ready', stream_id: 'remote-race', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/remote-race/content', content_type: 'video/mp4', has_video: true, has_audio: true, seekable: true }, raw: { extractor: 'youtube', id: 'video-race', duration: 300 } },
      },
    }));

    await screen.findByRole('button', { name: 'Clear progress' });
    const media = screen.getByTitle('Progressive remote stream') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 90, writable: true },
      duration: { configurable: true, value: 300 },
      ended: { configurable: true, value: false, writable: true },
    });
    fireEvent.timeUpdate(media);
    await browser.click(screen.getByRole('button', { name: 'Clear progress' }));
    media.currentTime = 300;
    Object.defineProperty(media, 'ended', { configurable: true, value: true });
    fireEvent.ended(media);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === progressUrl && init?.method === 'PUT')).toBe(true));

    await act(async () => { resolveClear?.(new Response(JSON.stringify(currentCompleted), { status: 200, headers: { 'Content-Type': 'application/json' } })); });
    await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
    expect(screen.getByRole('button', { name: 'Clear progress' })).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).startsWith(progressUrl) && init?.method === 'DELETE')).toHaveLength(1);
  });

  it('does not expose a raw signed media URL and keeps download available for unsupported playback', () => {
    const html = renderToStaticMarkup(
      watchSurface({
        selection: {
          kind: 'remote',
          item: { id: 'video-1', title: 'Protected video', webpage_url: 'https://source.example/watch/1' },
          preview: {
            kind: 'video',
            title: 'Protected video',
            entries: [],
            playback: {
              status: 'unsupported',
              stream_id: 'opaque-unsupported',
              transport: null,
              media_kind: null,
              playback_url: null,
              content_type: null,
              has_video: false,
              has_audio: false,
              seekable: false,
              fallback_code: 'no_audio',
              fallback_message: 'This protected source cannot be streamed here.',
            },
            raw: { url: 'https://signed.example/media?token=DO-NOT-EXPOSE' },
          },
        },
      }),
    );

    expect(html).toContain('This protected source cannot be streamed here.');
    expect(html).toContain('Save to library');
    expect(html).not.toContain('signed.example');
    expect(html).not.toContain('DO-NOT-EXPOSE');
  });
});

describe('WatchSurface autoplay queue', () => {
  const current = {
    id: 'library-current',
    title: 'Current video',
    uploader: 'Channel',
    webpage_url: 'https://youtu.be/abc123?feature=shared',
    metadata_json: { extractor: 'youtube', id: 'abc123', duration: 120 },
  } as LibraryItem;
  const currentDuplicate = {
    id: 'abc123', source: 'youtube', title: 'Current duplicate',
    webpage_url: 'https://www.youtube.com/watch?v=abc123&si=duplicate',
  } as YouTubeSearchResult;
  const next = {
    id: 'def456', source: 'youtube', title: 'Actual next video', uploader: 'Next channel',
    webpage_url: 'https://www.youtube.com/watch?v=def456',
  } as YouTubeSearchResult;
  const liveNext = {
    id: 'live789', source: 'youtube', title: 'Live stream', uploader: 'Live channel',
    webpage_url: 'https://www.youtube.com/watch?v=live789',
    capabilities: { provider: 'youtube', lifecycle: 'live', can_play: false, play_reason: 'live_playback_not_supported', can_acquire: false, acquire_reason: 'live_acquisition_not_supported', chat: { live: 'unavailable', replay: 'unavailable' } },
  } as YouTubeSearchResult;
  const playableNext = {
    id: 'ghi012', source: 'youtube', title: 'Playable next', uploader: 'Vod channel',
    webpage_url: 'https://www.youtube.com/watch?v=ghi012',
    capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
  } as YouTubeSearchResult;

  function renderAutoplay({
    autoplayUpNext = true,
    related = [currentDuplicate, next],
    onOpenRelated = vi.fn(),
    onPlaybackCheckpoint = vi.fn(),
    onAutoplayUpNextChange = vi.fn(),
  }: {
    autoplayUpNext?: boolean;
    related?: YouTubeSearchResult[];
    onOpenRelated?: (item: YouTubeSearchResult) => void;
    onPlaybackCheckpoint?: (item: LibraryItem, position: number, duration: number | null, completed: boolean) => void;
    onAutoplayUpNextChange?: (autoplayUpNext: boolean) => void;
  } = {}) {
    render(watchSurface({
      autoplayUpNext,
      library: [current],
      onAutoplayUpNextChange,
      onOpenRelated,
      onPlaybackCheckpoint,
      related,
      selection: { kind: 'library', item: current },
    }));
    return { onAutoplayUpNextChange, onOpenRelated, onPlaybackCheckpoint };
  }

  function finishCurrentVideo() {
    const media = screen.getByLabelText('Current video video') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 120, writable: true },
      duration: { configurable: true, value: 120 },
      ended: { configurable: true, value: true, writable: true },
    });
    fireEvent.ended(media);
    return media;
  }

  it('checkpoints completion and advances exactly once to the visibly first distinct video', () => {
    const { onOpenRelated, onPlaybackCheckpoint } = renderAutoplay();

    expect(screen.queryByText('Current duplicate')).toBeNull();
    expect(screen.getByText('Plays next')).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Actual next video/ }).getAttribute('aria-describedby')?.split(' ')).toContain(screen.getByText('Plays next').id);

    const media = finishCurrentVideo();
    fireEvent.ended(media);

    expect(onPlaybackCheckpoint).toHaveBeenCalledWith(current, 120, 120, true);
    expect(onPlaybackCheckpoint).toHaveBeenCalledTimes(1);
    expect(onOpenRelated).toHaveBeenCalledTimes(1);
    expect(onOpenRelated).toHaveBeenCalledWith(next);
  });

  it('re-arms autoplay only after the same video genuinely resumes', () => {
    const { onOpenRelated, onPlaybackCheckpoint } = renderAutoplay();
    const media = finishCurrentVideo();

    Object.defineProperty(media, 'ended', { configurable: true, value: false });
    media.currentTime = 1;
    fireEvent.timeUpdate(media);
    media.currentTime = 120;
    Object.defineProperty(media, 'ended', { configurable: true, value: true });
    fireEvent.ended(media);

    expect(onOpenRelated).toHaveBeenCalledTimes(2);
    expect(vi.mocked(onPlaybackCheckpoint).mock.calls.filter((call) => call[3] === true)).toHaveLength(2);
  });

  it('exposes a controlled autoplay toggle and leaves playback ended when disabled', () => {
    const { onAutoplayUpNextChange, onOpenRelated } = renderAutoplay({ autoplayUpNext: false });
    const toggle = screen.getByRole('checkbox', { name: 'Autoplay next video' });

    expect((toggle as HTMLInputElement).checked).toBe(false);
    fireEvent.click(toggle);
    expect(onAutoplayUpNextChange).toHaveBeenCalledWith(true);
    finishCurrentVideo();

    expect(onOpenRelated).not.toHaveBeenCalled();
    expect(screen.queryByText('Plays next')).toBeNull();
  });

  it('ends normally when the distinct queue is empty', () => {
    const { onOpenRelated } = renderAutoplay({ related: [currentDuplicate] });
    finishCurrentVideo();
    expect(onOpenRelated).not.toHaveBeenCalled();
    expect(screen.queryByRole('checkbox', { name: 'Autoplay next video' })).toBeNull();
  });

  it('advances to the first playable recommendation, skipping a known not-playable one', () => {
    const { onOpenRelated } = renderAutoplay({ related: [liveNext, playableNext] });

    // The "Plays next" badge and autoplay target is the playable candidate, not
    // the higher-listed but not-playable live source.
    expect(screen.getByRole('button', { name: /^Playable next/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Live stream/ })).toBeTruthy();

    finishCurrentVideo();

    expect(onOpenRelated).toHaveBeenCalledTimes(1);
    expect(onOpenRelated).toHaveBeenCalledWith(playableNext);
  });

  it('does not auto-advance when every recommendation is not playable', () => {
    const { onOpenRelated } = renderAutoplay({ related: [liveNext] });

    expect(screen.queryByText('Plays next')).toBeNull();
    finishCurrentVideo();

    expect(onOpenRelated).not.toHaveBeenCalled();
  });
});
