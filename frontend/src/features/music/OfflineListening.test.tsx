// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';

import type { MusicEngineState } from './musicEngine';
import type { OfflineTrack } from './musicOffline';
import { OfflineListening } from './OfflineListening';

const mocks = vi.hoisted(() => ({
  enginePlay: vi.fn(),
  nativePlay: vi.fn(),
  nativePause: vi.fn(),
  nativeLoad: vi.fn(),
  tracks: [] as OfflineTrack[],
}));

vi.mock('./musicOffline', () => ({
  clearOfflineMusic: vi.fn(),
  enableOfflineShell: vi.fn().mockResolvedValue(undefined),
  lastOfflineOwner: () => 'member',
  offlineTrack: vi.fn(),
  offlineTracks: () => Promise.resolve(mocks.tracks),
  removeOfflineTrack: vi.fn(),
}));

vi.mock('./musicEngine', () => ({
  MAX_DECODED_AUDIO_BYTES: 128 * 1024 * 1024,
  MusicEngine: class {
    public constructor(private readonly options: { onChange: (state: MusicEngineState) => void }) {}
    public play = mocks.enginePlay.mockImplementation(async (_queue: unknown, index: number) => {
      this.options.onChange({ index, position: 0, duration: 180, playing: true, loading: false, error: null });
    });
    public pause = vi.fn();
    public resume = vi.fn();
    public next = vi.fn();
    public previous = vi.fn();
    public seek = vi.fn();
    public setVolume = vi.fn();
    public dispose = vi.fn();
  },
}));

const saved = (id: string, duration: number): OfflineTrack => ({
  key: `member:${id}`,
  owner: 'member',
  track: {
    id, title: id, artist: 'Artist', album_artist: 'Artist', album: 'Album', album_id: 'album', artist_id: 'artist',
    number: 1, disc: 1, duration, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`, codec: 'flac',
    sample_rate: 48_000, bit_depth: 24, channels: 2, gain_db: null, genres: [], edited: false,
  },
  audio: new ArrayBuffer(8), artwork: null, savedAt: 1,
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.tracks = [saved('Long track', 60 * 60), saved('Short track', 180)];
  vi.stubGlobal('Audio', class {
    public currentTime = 0;
    public duration = 60 * 60;
    public ended = false;
    public volume = 1;
    public preload = '';
    public src = '';
    public onloadedmetadata: (() => void) | null = null;
    public ontimeupdate: (() => void) | null = null;
    public onplaying: (() => void) | null = null;
    public onpause: (() => void) | null = null;
    public onended: (() => void) | null = null;
    public onerror: (() => void) | null = null;
    public play = mocks.nativePlay.mockResolvedValue(undefined);
    public pause = mocks.nativePause;
    public load = mocks.nativeLoad;
    public removeAttribute = vi.fn();
  });
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:saved'), revokeObjectURL: vi.fn() });
});

it.each([false, true])('stops and releases native offline playback before starting Web Audio (unprobed: %s)', async (unprobed) => {
  if (unprobed) Object.assign(mocks.tracks[0].track, { duration: null, sample_rate: null, channels: null });
  render(<OfflineListening />);
  await userEvent.click(await screen.findByRole('button', { name: 'Play Long track' }));
  expect(mocks.nativePlay).toHaveBeenCalledOnce();

  await userEvent.click(screen.getByRole('button', { name: 'Play Short track' }));
  await waitFor(() => expect(mocks.enginePlay).toHaveBeenCalledOnce());
  expect(mocks.nativePause).toHaveBeenCalled();
  expect(mocks.nativeLoad).toHaveBeenCalled();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:saved');
  expect(mocks.nativePause.mock.invocationCallOrder[0]).toBeLessThan(mocks.enginePlay.mock.invocationCallOrder[0]);
});
