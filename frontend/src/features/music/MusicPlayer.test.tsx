import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MusicSession } from './musicSession';
import type { MusicTrack } from './musicTypes';
import { setMusicCast } from './musicCastSession';
import { MusicPlayer } from './MusicPlayer';

const mocks = vi.hoisted(() => ({
  cast: vi.fn(), control: vi.fn(), speakers: vi.fn(), status: vi.fn(), lyrics: vi.fn(),
  shell: vi.fn(), cached: vi.fn(), save: vi.fn(), owner: vi.fn(), close: vi.fn(), pause: vi.fn(), playQueue: vi.fn(),
  reorder: vi.fn(), repeat: vi.fn(), seek: vi.fn(), volume: vi.fn(), step: vi.fn(), stream: vi.fn(), toggle: vi.fn(),
}));

const track = (id: string, extra: Partial<MusicTrack> = {}): MusicTrack => ({ id, title: `Song ${id}`, artist: 'Mara Vale', album_artist: 'Mara Vale', album: 'Amber Rooms', album_id: 'album-1', artist_id: 'artist-1', number: 1, disc: 1, duration: 180, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`, codec: 'flac', sample_rate: 48000, bit_depth: 24, channels: 2, gain_db: -4, genres: ['Soul'], edited: false, ...extra });
const queue = [track('one'), track('two', { title: 'Second Song', number: 2 }), track('three', { number: 3 })];
let music: MusicSession;

vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), castMusic: mocks.cast, controlMusicCast: mocks.control, discoverSonos: mocks.speakers, getMusicCastStatus: mocks.status, getMusicLyrics: mocks.lyrics }));
vi.mock('./musicOffline', () => ({ enableOfflineShell: mocks.shell, offlineTrack: mocks.cached, saveOfflineTrack: mocks.save }));
vi.mock('./musicSession', () => ({
  useMusicSession: () => music, currentMusicSampleRate: () => 48000, setMusicOwner: mocks.owner, closeMusic: mocks.close,
  normalizeMusic: vi.fn(), pauseMusic: mocks.pause, playMusicQueue: mocks.playQueue, reorderMusic: mocks.reorder, repeatMusic: mocks.repeat,
  seekMusic: mocks.seek, setMusicVolume: mocks.volume, stepMusic: mocks.step, streamMusicCurrent: mocks.stream, toggleMusic: mocks.toggle,
}));
vi.mock('../gallery/WallGrid', () => ({ useMediaQuery: () => false }));
vi.mock('./MusicExtras', () => ({ MusicVideoRail: () => <div>Performances</div> }));
vi.mock('../../Artwork', () => ({ Artwork: ({ alt }: { alt: string }) => <span aria-label={alt || undefined} /> }));

const props = { owner: 'member-a', onWatchVideo: vi.fn(), onOpenAlbum: vi.fn(), onOpenPlaylists: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks(); setMusicCast(null); localStorage.clear();
  music = { owner: 'member-a', queue, playback: { index: 0, position: 12, duration: 180, playing: true, loading: false, error: null }, mode: 'gapless', volume: .7, normalize: true, repeat: 'off', message: null };
  mocks.control.mockResolvedValue({}); mocks.speakers.mockResolvedValue([{ id: 'speaker-1', name: 'Kitchen', model: 'One', coordinator_id: 'speaker-1' }]);
  mocks.status.mockResolvedValue({ transport_state: 'PLAYING', volume: 35, track: { track_number: 1, position_seconds: 12, duration_seconds: 180 } });
  mocks.lyrics.mockResolvedValue({ plain: 'Server lyric', cues: [], source: 'library', instrumental: false });
  mocks.cached.mockResolvedValue(undefined); mocks.shell.mockResolvedValue(undefined); mocks.save.mockResolvedValue(undefined); mocks.cast.mockResolvedValue({ ticket_id: 'ticket-1', state: {} });
});

afterEach(() => { vi.useRealTimers(); setMusicCast(null); delete (navigator as { mediaSession?: unknown }).mediaSession; vi.unstubAllGlobals(); });

describe('MusicPlayer', () => {
  it('keeps the player usable when lyrics return only a provider error', async () => {
    mocks.lyrics.mockResolvedValue({ error: 'Music provider is temporarily unavailable', refreshing: false });

    render(<MusicPlayer {...props} />);
    await waitFor(() => expect(mocks.lyrics).toHaveBeenCalledWith('one'));
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));

    expect(await screen.findByText('Music provider is temporarily unavailable')).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Listening room' })).toBeTruthy();
    await userEvent.click(screen.getAllByRole('button', { name: 'Pause music' })[0]);
    expect(mocks.pause).toHaveBeenCalledOnce();
  });

  it('replaces a partial pending lyrics reply with the provider error', async () => {
    vi.useFakeTimers();
    mocks.lyrics
      .mockResolvedValueOnce({ refreshing: true })
      .mockResolvedValueOnce({ error: 'Music provider is temporarily unavailable', refreshing: false });

    render(<MusicPlayer {...props} />);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    fireEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));
    expect(screen.getByText('Finding lyrics…')).toBeTruthy();

    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByText('Music provider is temporarily unavailable')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Pause music' })).not.toHaveLength(0);
  });

  it('uses cached lyrics when the server is unavailable and keeps the dialog close accessible', async () => {
    mocks.cached.mockResolvedValue({ lyrics: { plain: 'Saved lyric', cues: [], source: 'offline', instrumental: false } });
    mocks.lyrics.mockRejectedValue(new Error('offline'));
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));
    expect(await screen.findByText('Saved lyric')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Close listening room' }));
    expect((screen.getByRole('dialog', { hidden: true }) as HTMLDialogElement).open).toBe(false);
  });

  it('aborts a sequential offline save when the member changes', async () => {
    let signal: AbortSignal | undefined;
    mocks.save.mockImplementation((_owner: string, _track: MusicTrack, next: AbortSignal) => { signal = next; return new Promise(() => undefined); });
    const view = render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save queue' }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(1));
    music = { ...music, owner: 'member-b' };
    view.rerender(<MusicPlayer {...props} owner="member-b" />);
    expect(signal?.aborted).toBe(true);
  });

  it('reports completed offline tracks and lets the listener cancel the remaining sequence', async () => {
    let secondSignal: AbortSignal | undefined;
    mocks.save.mockResolvedValueOnce(undefined).mockImplementation((_owner: string, _track: MusicTrack, signal: AbortSignal) => { secondSignal = signal; return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), { once: true })); });
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save queue' }));
    expect(await screen.findByText('Saved 1 of 3')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(secondSignal?.aborted).toBe(true);
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull());
  });

  it('tracks the real Sonos item and routes seek plus Media Session controls to its ticket', async () => {
    const handlers = new Map<string, MediaSessionActionHandler | null>();
    Object.defineProperty(navigator, 'mediaSession', { configurable: true, value: { metadata: null, playbackState: 'none', setActionHandler: vi.fn((action, handler) => handlers.set(action, handler)), setPositionState: vi.fn() } });
    vi.stubGlobal('MediaMetadata', class { constructor(init: object) { Object.assign(this, init); } });
    setMusicCast({ owner: 'member-a', ticket: 'ticket-1', name: 'Kitchen', playing: true, baseIndex: 0, index: 0, position: 12, duration: 180, volume: .4, queued: 3 });
    mocks.status.mockResolvedValue({ transport_state: 'PLAYING', volume: 42, track: { track_number: 2, position_seconds: 40, duration_seconds: 200 } });
    render(<MusicPlayer {...props} />);
    expect(await screen.findByText('Second Song')).toBeTruthy();
    fireEvent.change(screen.getByRole('slider', { name: /^Track position/ }), { target: { value: '55' } });
    await waitFor(() => expect(mocks.control).toHaveBeenCalledWith('ticket-1', 'seek', 55));
    handlers.get('nexttrack')?.({ action: 'nexttrack' } as MediaSessionActionDetails);
    await waitFor(() => expect(mocks.control).toHaveBeenCalledWith('ticket-1', 'next', undefined));
  });

  it('hides a paused dock for video without clearing its queue', () => {
    music = { ...music, playback: { ...music.playback, playing: false } };
    render(<MusicPlayer {...props} hidden />);
    expect(screen.queryByRole('region', { name: 'Music player' })).toBeNull();
    expect(mocks.close).not.toHaveBeenCalled();
  });

  it('opens playlist management from the queue and explains bounded Sonos transfers', async () => {
    const many = Array.from({ length: 105 }, (_, index) => track(`t-${index}`));
    music = { ...music, queue: many };
    const open = vi.fn();
    render(<MusicPlayer {...props} onOpenPlaylists={open} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open queue' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save or manage playlists' }));
    expect(open).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole('button', { name: 'Choose audio output' }));
    expect(await screen.findByText(/next 100 of 105 distinct queued tracks/)).toBeTruthy();
    await userEvent.click(screen.getByRole('checkbox', { name: /Compatibility mode/ }));
    await userEvent.click(screen.getByRole('button', { name: /Kitchen/ }));
    expect(mocks.cast).toHaveBeenCalledWith('speaker-1', many.slice(0, 100).map((entry) => entry.id), true);
    expect(mocks.pause.mock.invocationCallOrder[0]).toBeLessThan(mocks.cast.mock.invocationCallOrder[0]);
  });

  it('pauses before starting a speaker and resumes the browser queue when casting fails', async () => {
    mocks.cast.mockRejectedValueOnce(new Error('Speaker unavailable'));
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Choose audio output' }));
    await userEvent.click(await screen.findByRole('button', { name: /Kitchen/ }));

    expect(mocks.pause).toHaveBeenCalledOnce();
    expect(mocks.pause.mock.invocationCallOrder[0]).toBeLessThan(mocks.cast.mock.invocationCallOrder[0]);
    expect(mocks.toggle).toHaveBeenCalledOnce();
    expect(await screen.findAllByText('Speaker unavailable')).not.toHaveLength(0);
  });

  it('hands playback back at the speaker current track and position', async () => {
    setMusicCast({ owner: 'member-a', ticket: 'ticket-1', name: 'Kitchen', playing: true, baseIndex: 0, index: 1, position: 40, duration: 180, volume: .4, queued: 3 });
    mocks.status.mockResolvedValue({ transport_state: 'PLAYING', track: { track_number: 2, position_seconds: 40, duration_seconds: 180 } });
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Choose audio output' }));
    await userEvent.click(await screen.findByRole('button', { name: /This browser/ }));

    expect(mocks.control).toHaveBeenCalledWith('ticket-1', 'stop');
    expect(mocks.playQueue).toHaveBeenCalledWith(queue, 1, 40);
    expect(mocks.toggle).not.toHaveBeenCalled();
  });
});
