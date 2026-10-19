import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MusicSession } from './musicSession';
import type { MusicTrack } from './musicTypes';
import { setMusicCast } from './musicCastSession';
import { MusicPlayer } from './MusicPlayer';

const mocks = vi.hoisted(() => ({
  cast: vi.fn(), control: vi.fn(), speakers: vi.fn(), status: vi.fn(), lyrics: vi.fn(),
  shell: vi.fn(), cached: vi.fn(), save: vi.fn(), owner: vi.fn(), close: vi.fn(), pause: vi.fn(), playQueue: vi.fn(),
  reorder: vi.fn(), repeat: vi.fn(), seek: vi.fn(), volume: vi.fn(), step: vi.fn(), stream: vi.fn(), toggle: vi.fn(), crossfade: vi.fn(), sleep: vi.fn(), unavailablePolicy: vi.fn(), refreshShared: vi.fn(), useServer: vi.fn(), keepDevice: vi.fn(), acknowledgeBrowser: vi.fn(), resumeBrowser: vi.fn(), applyShared: vi.fn(),
}));

const track = (id: string, extra: Partial<MusicTrack> = {}): MusicTrack => ({ id, title: `Song ${id}`, artist: 'Mara Vale', album_artist: 'Mara Vale', album: 'Amber Rooms', album_id: 'album-1', artist_id: 'artist-1', number: 1, disc: 1, duration: 180, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`, codec: 'flac', sample_rate: 48000, bit_depth: 24, channels: 2, gain_db: -4, genres: ['Soul'], edited: false, ...extra });
const queue = [track('one'), track('two', { title: 'Second Song', number: 2 }), track('three', { number: 3 })];
let music: MusicSession;

vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), castMusic: mocks.cast, controlMusicCast: mocks.control, discoverSonos: mocks.speakers, getMusicCastStatus: mocks.status, getMusicLyrics: mocks.lyrics }));
vi.mock('./musicOffline', () => ({ enableOfflineShell: mocks.shell, offlineTrack: mocks.cached, saveOfflineTrack: mocks.save }));
vi.mock('./musicSession', () => ({
  useMusicSession: () => music, currentMusicSampleRate: () => 48000, setMusicOwner: mocks.owner, closeMusic: mocks.close,
  normalizeMusic: vi.fn(), pauseMusic: mocks.pause, playMusicQueue: mocks.playQueue, reorderMusic: mocks.reorder, repeatMusic: mocks.repeat,
  seekMusic: mocks.seek, setMusicVolume: mocks.volume, setMusicCrossfade: mocks.crossfade, setMusicSleepTimer: mocks.sleep, setMusicUnavailablePolicy: mocks.unavailablePolicy, refreshMusicSharedSession: mocks.refreshShared, stepMusic: mocks.step, streamMusicCurrent: mocks.stream, toggleMusic: mocks.toggle, useServerMusicSession: mocks.useServer, keepDeviceMusicSession: mocks.keepDevice, acknowledgeMusicBrowserOutput: mocks.acknowledgeBrowser, resumeMusicFromCast: mocks.resumeBrowser, applyMusicSharedSnapshot: mocks.applyShared,
}));
vi.mock('../gallery/WallGrid', () => ({ useMediaQuery: () => false }));
vi.mock('./MusicExtras', () => ({ MusicVideoRail: () => <div>Performances</div> }));
vi.mock('../../Artwork', () => ({ Artwork: ({ alt }: { alt: string }) => <span aria-label={alt || undefined} /> }));

const props = { owner: 'member-a', onWatchVideo: vi.fn(), onOpenAlbum: vi.fn(), onOpenPlaylists: vi.fn() };

beforeEach(() => {
  vi.clearAllMocks(); setMusicCast(null); localStorage.clear();
  music = { owner: 'member-a', queue, playback: { index: 0, position: 12, duration: 180, playing: true, loading: false, error: null }, mode: 'gapless', volume: .7, normalize: true, repeat: 'off', shuffle: false, sharedRevision: 1, sharedConflict: null, crossfadeSeconds: 0, sleepUntil: null, message: null, sharedEntries: [], sharedEntryIndex: 0, unavailablePolicy: 'skip' };
  mocks.control.mockResolvedValue({}); mocks.speakers.mockResolvedValue([{ id: 'speaker-1', name: 'Kitchen', model: 'One', coordinator_id: 'speaker-1' }]);
  mocks.status.mockResolvedValue({ transport_state: 'PLAYING', volume: 35, track: { track_number: 1, position_seconds: 12, duration_seconds: 180 } });
  mocks.lyrics.mockResolvedValue({ plain: 'Server lyric', cues: [], source: 'library', instrumental: false });
  mocks.cached.mockResolvedValue(undefined); mocks.shell.mockResolvedValue(undefined); mocks.save.mockResolvedValue(undefined); mocks.cast.mockResolvedValue({ ticket_id: 'ticket-1', state: {}, requested_count: 3, queued_count: 3, excluded_count: 0, queue_indices: [0, 1, 2], current_index: 0, position_seconds: 12, session: {} }); mocks.resumeBrowser.mockResolvedValue(undefined); mocks.acknowledgeBrowser.mockResolvedValue(undefined);
});

afterEach(() => { vi.useRealTimers(); setMusicCast(null); delete (navigator as { mediaSession?: unknown }).mediaSession; vi.unstubAllGlobals(); });

describe('MusicPlayer', () => {
  it('keeps the player usable when lyrics return only a provider error', async () => {
    mocks.lyrics.mockResolvedValue({ error: 'Music provider is temporarily unavailable', refreshing: false });

    const view = render(<MusicPlayer {...props} />);
    await waitFor(() => expect(mocks.lyrics).toHaveBeenCalledWith('one'));
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));

    expect(await screen.findByText('Music provider is temporarily unavailable')).toBeTruthy();
    expect(screen.getByRole('dialog', { name: 'Listening room' }).classList.contains('has-lyrics')).toBe(false);
    mocks.lyrics.mockResolvedValueOnce({ plain: 'Recovered words', cues: [], source: 'LRCLIB', instrumental: false });
    await userEvent.click(screen.getByRole('button', { name: 'Retry lyrics' }));
    expect(await screen.findByText('Recovered words')).toBeTruthy();
    expect(mocks.lyrics).toHaveBeenLastCalledWith('one', true);
    expect(screen.getByRole('dialog', { name: 'Listening room' }).classList.contains('has-lyrics')).toBe(true);
    expect(screen.getByRole('dialog', { name: 'Listening room' })).toBeTruthy();
    await userEvent.click(screen.getAllByRole('button', { name: 'Pause music' })[0]);
    expect(mocks.pause).toHaveBeenCalledOnce();
    music = { ...music, playback: { ...music.playback, playing: false } };
    view.rerender(<MusicPlayer {...props} />);
    expect(screen.queryByRole('region', { name: 'Music player' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Listening room' })).toBeTruthy();
    expect(within(screen.getByRole('dialog', { name: 'Listening room' })).getByRole('button', { name: 'Play music' })).toBeTruthy();
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

  it('collapses paused music, expands without playing, and can be closed without losing the queue', async () => {
    const view = render(<MusicPlayer {...props} />);
    expect(screen.getByRole('region', { name: 'Music player' })).toBeTruthy();
    expect(document.documentElement.style.getPropertyValue('--music-dock-height')).not.toBe('');

    music = { ...music, playback: { ...music.playback, playing: false } };
    view.rerender(<MusicPlayer {...props} />);

    expect(screen.queryByRole('region', { name: 'Music player' })).toBeNull();
    const mini = screen.getByRole('region', { name: 'Mini music player' });
    await waitFor(() => expect(document.documentElement.style.getPropertyValue('--music-dock-height')).toBe(''));
    await userEvent.click(within(mini).getByRole('button', { name: /Expand music player/ }));
    expect(screen.getByRole('region', { name: 'Music player' })).toBeTruthy();
    expect(mocks.toggle).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Collapse music player' }));
    expect(screen.getByRole('region', { name: 'Mini music player' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss music player' }));
    expect(screen.queryByRole('region', { name: 'Mini music player' })).toBeNull();
    expect(mocks.close).not.toHaveBeenCalled();
  });

  it('keeps dismissal across remounts until playback starts again without clearing the queue', async () => {
    const view = render(<><main id="main-content" tabIndex={-1} /><MusicPlayer {...props} /></>);

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss music player' }));

    expect(mocks.pause).toHaveBeenCalledOnce();
    expect(screen.queryByRole('region', { name: 'Music player' })).toBeNull();
    expect(mocks.close).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(document.getElementById('main-content'));
    view.unmount();

    music = { ...music, playback: { ...music.playback, playing: false } };
    const restored = render(<MusicPlayer {...props} />);
    expect(screen.queryByRole('region', { name: /^(Mini music player|Music player)$/ })).toBeNull();
    expect(music.queue).toEqual(queue);

    music = { ...music, playback: { ...music.playback, playing: true } };
    restored.rerender(<MusicPlayer {...props} />);
    expect(await screen.findByRole('region', { name: 'Music player' })).toBeTruthy();
    restored.unmount();

    music = { ...music, playback: { ...music.playback, playing: false } };
    render(<MusicPlayer {...props} />);
    expect(screen.getByRole('region', { name: 'Mini music player' })).toBeTruthy();
  });

  it('reopens if music starts before the player mounts again', async () => {
    music = { ...music, playback: { ...music.playback, playing: false } };
    const view = render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss music player' }));
    view.unmount();

    music = { ...music, playback: { ...music.playback, playing: true } };
    render(<MusicPlayer {...props} />);
    expect(screen.getByRole('region', { name: 'Music player' })).toBeTruthy();
  });

  it('keeps dismissal private to the member who closed the player', async () => {
    music = { ...music, playback: { ...music.playback, playing: false } };
    const view = render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss music player' }));

    music = { ...music, owner: 'member-b' };
    view.rerender(<MusicPlayer {...props} owner="member-b" />);
    expect(screen.getByRole('region', { name: 'Mini music player' })).toBeTruthy();

    music = { ...music, owner: 'member-a' };
    view.rerender(<MusicPlayer {...props} />);
    expect(screen.queryByRole('region', { name: /^(Mini music player|Music player)$/ })).toBeNull();
  });

  it('keeps Sonos playback visible when dismiss is not acknowledged and hides it after retry', async () => {
    setMusicCast({ owner: 'member-a', ticket: 'ticket-1', name: 'Kitchen', playing: true, baseIndex: 0, index: 0, position: 12, duration: 180, volume: .4, queued: 3 });
    mocks.control.mockRejectedValueOnce(new Error('Speaker unavailable'));
    render(<MusicPlayer {...props} />);

    const player = await screen.findByRole('region', { name: 'Music player' });
    await userEvent.click(within(player).getByRole('button', { name: 'Dismiss music player' }));

    expect(await screen.findByRole('region', { name: 'Music player' })).toBeTruthy();
    expect(await screen.findAllByText('Speaker unavailable')).not.toHaveLength(0);
    expect(mocks.pause).not.toHaveBeenCalled();
    expect(mocks.close).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss music player' }));

    await waitFor(() => expect(screen.queryByRole('region', { name: 'Music player' })).toBeNull());
    expect(mocks.control).toHaveBeenCalledTimes(2);
    expect(mocks.control).toHaveBeenNthCalledWith(1, 'ticket-1', 'pause', undefined);
    expect(mocks.control).toHaveBeenNthCalledWith(2, 'ticket-1', 'pause', undefined);
    expect(mocks.pause).not.toHaveBeenCalled();
    expect(mocks.close).not.toHaveBeenCalled();
  });

  it('offers explicit shared-session conflict choices', async () => {
    music = { ...music, sharedConflict: { revision: 2, queue, current_index: 0, position_seconds: 12, repeat: 'off', shuffle: false, active_output: { kind: 'browser' }, updated_at: null, removed_count: 0 } };
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Use server' }));
    await userEvent.click(screen.getByRole('button', { name: 'Keep this device' }));
    expect(mocks.useServer).toHaveBeenCalledOnce();
    expect(mocks.keepDevice).toHaveBeenCalledOnce();
  });

  it('offers browser-only crossfade and sleep controls', async () => {
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open listening room for Song one' }));
    await userEvent.click(screen.getByRole('button', { name: 'Crossfade' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: '3 seconds' }));
    await userEvent.click(screen.getByRole('button', { name: 'Sleep timer' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: '30 minutes' }));
    expect(mocks.crossfade).toHaveBeenCalledWith(3);
    expect(mocks.sleep).toHaveBeenCalledWith(30);
  });

  it('shows pending acquisitions and lets the member choose the wait boundary', async () => {
    music = { ...music, sharedEntries: [{ kind: 'track', item_id: 'one', state: 'playable', tracks: [queue[0]] }, { kind: 'acquisition', request_id: '11111111-1111-4111-8111-111111111111', state: 'awaiting_import', name: 'Requested album', message: 'Waiting for library import' }], unavailablePolicy: 'wait' };
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open queue' }));
    expect(screen.getByText(/Requested album/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Unavailable queue policy' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: 'Skip it and continue' }));
    expect(mocks.unavailablePolicy).toHaveBeenCalledWith('skip');
    await userEvent.click(screen.getByRole('button', { name: 'Refresh requested albums' }));
    expect(mocks.refreshShared).toHaveBeenCalledWith('member-a');
  });

  it('keeps a pending-only shared queue reachable without inventing playback', async () => {
    music = { ...music, queue: [], playback: { ...music.playback, index: -1, playing: false }, sharedEntries: [{ kind: 'acquisition', request_id: '11111111-1111-4111-8111-111111111111', state: 'awaiting_import', name: 'Requested album', message: 'Waiting for library import' }] };
    render(<MusicPlayer {...props} />);
    expect(screen.getByText('Waiting for requested album')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Play music' }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Open queue' }));
    expect(screen.getByRole('button', { name: 'Refresh requested albums' })).toBeTruthy();
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
    expect(await screen.findByText(/next 100 of 105 queued tracks/)).toBeTruthy();
    await userEvent.click(screen.getByRole('checkbox', { name: /Compatibility mode/ }));
    await userEvent.click(screen.getByRole('button', { name: /Kitchen/ }));
    expect(mocks.cast).toHaveBeenCalledWith('speaker-1', many.slice(0, 100).map((entry) => entry.id), true, expect.objectContaining({ base_revision: 1, current_index: 0, position_seconds: 12 }));
    expect(mocks.cast.mock.invocationCallOrder[0]).toBeLessThan(mocks.pause.mock.invocationCallOrder[0]);
  });

  it('keeps browser playback recoverable when speaker acknowledgement fails', async () => {
    mocks.cast.mockRejectedValueOnce(new Error('Speaker unavailable'));
    render(<MusicPlayer {...props} />);
    await userEvent.click(screen.getByRole('button', { name: 'Choose audio output' }));
    await userEvent.click(await screen.findByRole('button', { name: /Kitchen/ }));

    expect(mocks.pause).not.toHaveBeenCalled();
    expect(mocks.toggle).not.toHaveBeenCalled();
    expect(await screen.findAllByText('Speaker unavailable')).not.toHaveLength(0);
  });

  it('hands playback back at the speaker current track and position', async () => {
    setMusicCast({ owner: 'member-a', ticket: 'ticket-1', name: 'Kitchen', playing: true, baseIndex: 0, index: 1, position: 40, duration: 180, volume: .4, queued: 3 });
    mocks.status.mockResolvedValue({ transport_state: 'PLAYING', track: { track_number: 2, position_seconds: 40, duration_seconds: 180 } });
    render(<MusicPlayer {...props} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Choose audio output' }));
    await userEvent.click(await screen.findByRole('button', { name: /This browser/ }));

    expect(mocks.resumeBrowser).toHaveBeenCalledWith(queue, 1, 40);
    expect(mocks.acknowledgeBrowser).toHaveBeenCalledWith('ticket-1');
    expect(mocks.control).toHaveBeenCalledWith('ticket-1', 'stop');
    expect(mocks.toggle).not.toHaveBeenCalled();
  });
});
