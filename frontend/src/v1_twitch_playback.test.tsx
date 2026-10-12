import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('hls.js', () => import('./test/fakeHls'));

import { RemotePlayer } from './remotePlayer';
import { hlsCalls as hls, resetHlsCalls } from './test/fakeHls';
import type { RemotePlayback } from './types';

/** Twitch live edge/end and finite VOD resume through the guarded relay descriptor. */

afterEach(() => { resetHlsCalls(); vi.restoreAllMocks(); });

const live: RemotePlayback = {
  status: 'ready', stream_id: 'twitch-live', transport: 'hls', media_kind: 'video',
  playback_url: '/api/remote-streams/twitch-live/relay/1/master.m3u8', content_type: 'application/vnd.apple.mpegurl',
  has_video: true, has_audio: true, seekable: false, live: true,
};

describe('Twitch playback lifecycle', () => {
  it('test_twitch_live_edge_fixture: Live state, no seek or duration, then one refresh reports the end', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
    const ended: RemotePlayback = {
      status: 'unsupported', stream_id: 'twitch-live', transport: null, media_kind: null, playback_url: null, content_type: null,
      has_video: false, has_audio: false, seekable: false, live: true, fallback_code: 'live_stream_ended', fallback_message: 'This live stream has ended.',
    };
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(ended), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const onLiveEnded = vi.fn();
    render(<RemotePlayer onLiveEnded={onLiveEnded} playback={live} poster={null} title="Twitch live" />);
    expect(screen.getByRole('status', { name: 'Watching live' })).toBeTruthy();
    expect(screen.queryByLabelText('Seek')).toBeNull();

    await waitFor(() => expect(hls.handlers).toHaveLength(1));
    hls.handlers[0]('error', { fatal: true, type: 'networkError' });
    await waitFor(() => expect(hls.handlers).toHaveLength(2));
    hls.handlers[1]('error', { fatal: true, type: 'networkError' });

    await waitFor(() => expect(screen.getByText('This live stream has ended.')).toBeTruthy());
    // No retry storm: exactly one refresh, and the ended state attaches no new player.
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(hls.handlers).toHaveLength(2);
    expect(onLiveEnded).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Refresh stream' })).toBeNull();
  });

  it('test_twitch_vod_seek_fixture: a finite relay VOD is seekable and resumes at the checkpoint', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'canPlayType').mockReturnValue('');
    const vod: RemotePlayback = { ...live, stream_id: 'twitch-vod', playback_url: '/api/remote-streams/twitch-vod/relay/1/master.m3u8', seekable: true, live: false };
    render(<RemotePlayer playback={vod} poster={null} resumePosition={125} title="Twitch VOD" />);
    await waitFor(() => expect(hls.sources).toEqual(['/api/remote-streams/twitch-vod/relay/1/master.m3u8']));
    expect(screen.queryByRole('status', { name: 'Watching live' })).toBeNull();
    const media = screen.getByTitle('HLS remote stream') as HTMLVideoElement;
    Object.defineProperties(media, {
      currentTime: { configurable: true, value: 0, writable: true },
      duration: { configurable: true, value: 3600 },
    });
    fireEvent.canPlay(media);
    expect(media.currentTime).toBe(125);
  });
});
