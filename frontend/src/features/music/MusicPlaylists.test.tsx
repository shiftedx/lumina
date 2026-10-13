import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import type { MusicPlaylist, MusicTrack } from './musicTypes';
import { MusicPlaylists } from './MusicPlaylists';

const mocks = vi.hoisted(() => ({
  list: vi.fn(), save: vi.fn(), remove: vi.fn(), tracks: vi.fn(), mix: vi.fn(),
  setOwner: vi.fn(), play: vi.fn(),
  session: { owner: 'u1', queue: [] as MusicTrack[], playback: { index: -1, position: 0, duration: 0, playing: false, loading: false, error: null }, volume: .8, normalize: true, repeat: 'off' as const, message: null },
}));

vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  listMusicPlaylists: mocks.list,
  saveMusicPlaylist: mocks.save,
  deleteMusicPlaylist: mocks.remove,
  getMusicPlaylistTracks: mocks.tracks,
  getMusicMix: mocks.mix,
}));
vi.mock('./musicSession', () => ({ musicSession: () => mocks.session, useMusicSession: () => mocks.session, setMusicOwner: mocks.setOwner, playMusicQueue: mocks.play }));

const track = (id: string, title = id): MusicTrack => ({
  id, title, artist: 'The Artist', album_artist: 'The Artist', album: 'Record', album_id: 'album-1', artist_id: 'artist-1',
  number: 1, disc: 1, duration: 180, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`, codec: 'flac', sample_rate: 48_000,
  bit_depth: 24, channels: 2, gain_db: null, genres: ['Jazz'], edited: false,
});
const playlist = (over: Partial<MusicPlaylist> = {}): MusicPlaylist => ({ id: 'p1', name: 'Morning', shared: false, item_ids: ['a', 'b'], count: 2, can_edit: true, revision: 4, ...over });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.session.owner = 'u1'; mocks.session.queue = []; mocks.session.playback.index = -1;
  mocks.list.mockResolvedValue([]); mocks.tracks.mockResolvedValue([]); mocks.mix.mockResolvedValue([]); mocks.play.mockResolvedValue(undefined);
});

describe('MusicPlaylists', () => {
  it('creates a playlist from the live queue and plays a real mix through the owned music session', async () => {
    const queue = [track('a', 'Blue Hour'), track('b', 'Golden Hour')];
    mocks.session.queue = queue; mocks.session.playback.index = 0;
    mocks.save.mockResolvedValue(playlist({ name: 'Sunday', item_ids: ['a', 'b'] }));
    mocks.list.mockResolvedValueOnce([]).mockResolvedValueOnce([playlist({ name: 'Sunday' })]);
    mocks.tracks.mockResolvedValue(queue);
    mocks.mix.mockResolvedValue([track('mix-1', 'Deep Cut')]);
    render(<MusicPlaylists owner="u1" />);
    await screen.findByText(/No playlists yet/);

    await userEvent.type(screen.getByLabelText('Name'), 'Sunday');
    await userEvent.click(screen.getByRole('button', { name: 'Create playlist' }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({ name: 'Sunday', shared: false, item_ids: ['a', 'b'] }));
    expect(await screen.findByRole('heading', { name: 'Sunday' })).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: 'Play In rotation' }));
    await waitFor(() => expect(mocks.mix).toHaveBeenCalledWith('rotation', undefined));
    expect(mocks.setOwner).toHaveBeenCalledWith('u1');
    expect(mocks.play).toHaveBeenCalledWith([expect.objectContaining({ id: 'mix-1' })]);
    expect(mocks.setOwner.mock.invocationCallOrder.at(-1)).toBeLessThan(mocks.play.mock.invocationCallOrder.at(-1)!);
  });

  it('reorders and removes tracks, then saves the exact optimistic revision', async () => {
    mocks.list.mockResolvedValue([playlist()]);
    mocks.tracks.mockResolvedValue([track('a', 'First'), track('b', 'Second'), track('c', 'Third')]);
    mocks.save.mockResolvedValue(playlist({ name: 'Renamed', shared: true, item_ids: ['b', 'a'], count: 2, revision: 5 }));
    render(<MusicPlaylists owner="u1" />);
    await userEvent.click(await screen.findByRole('button', { name: /Morning/ }));
    await screen.findByText('First');
    await userEvent.click(screen.getByRole('button', { name: 'Move Second up' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove Third' }));
    const editor = screen.getByRole('heading', { name: 'Morning' }).closest('section') as HTMLElement;
    await userEvent.clear(within(editor).getByLabelText('Name'));
    await userEvent.type(within(editor).getByLabelText('Name'), 'Renamed');
    await userEvent.click(within(editor).getByLabelText('Share with the household'));
    await userEvent.click(within(editor).getByRole('button', { name: 'Save changes' }));
    expect(mocks.save).toHaveBeenCalledWith({ name: 'Renamed', shared: true, item_ids: ['b', 'a'], rules: undefined, revision: 4 }, 'p1');
    expect(await screen.findByText('Playlist saved.')).toBeTruthy();
  });

  it.each([
    [403, 'This shared playlist is read-only. Only its owner can change it.'],
    [409, 'This playlist changed on another screen. Reopen it before saving your changes.'],
  ])('explains a %s save failure without replacing the editor', async (status, expected) => {
    mocks.list.mockResolvedValue([playlist()]); mocks.tracks.mockResolvedValue([track('a')]);
    mocks.save.mockRejectedValue(new api.ApiRequestError('server detail', status));
    render(<MusicPlaylists owner="u1" />);
    await userEvent.click(await screen.findByRole('button', { name: /Morning/ }));
    await screen.findByText('a');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByText(expected)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Morning' })).toBeTruthy();
  });

  it('reopens the latest revision and tracks after an optimistic conflict', async () => {
    const first = playlist();
    const latest = playlist({ name: 'Changed elsewhere', revision: 5, item_ids: ['c'], count: 1 });
    mocks.list.mockResolvedValueOnce([first]).mockResolvedValueOnce([latest]);
    mocks.tracks.mockResolvedValueOnce([track('a', 'Old order')]).mockResolvedValueOnce([track('c', 'Latest order')]);
    mocks.save.mockRejectedValueOnce(new api.ApiRequestError('conflict', 409)).mockResolvedValueOnce({ ...latest, revision: 6 });
    render(<MusicPlaylists owner="u1" />);
    await userEvent.click(await screen.findByRole('button', { name: /Morning/ }));
    await screen.findByText('Old order');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText(/changed on another screen/);
    await userEvent.click(screen.getByRole('button', { name: 'Refresh playlists' }));
    expect(await screen.findByRole('heading', { name: 'Changed elsewhere' })).toBeTruthy();
    expect(await screen.findByText('Latest order')).toBeTruthy();
    expect(screen.queryByText('Old order')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(mocks.save).toHaveBeenLastCalledWith({ name: 'Changed elsewhere', shared: false, item_ids: ['c'], rules: undefined, revision: 5 }, 'p1');
  });

  it('makes another member’s shared playlist read-only but playable', async () => {
    mocks.list.mockResolvedValue([playlist({ id: 'shared', name: 'House favorites', can_edit: false, shared: true })]);
    mocks.tracks.mockResolvedValue([track('a', 'Shared song')]);
    render(<MusicPlaylists owner="u1" />);
    await userEvent.click(await screen.findByRole('button', { name: /House favorites/ }));
    expect(await screen.findByText(/only its owner can change it/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete playlist' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Play all' }));
    expect(mocks.setOwner).toHaveBeenCalledWith('u1');
    expect(mocks.play).toHaveBeenCalledWith([expect.objectContaining({ id: 'a' })]);
  });

  it('ignores a stale owner response and gives loading, failure, and empty-track states', async () => {
    let finishOld!: (value: MusicPlaylist[]) => void;
    const old = new Promise<MusicPlaylist[]>((resolve) => { finishOld = resolve; });
    mocks.list.mockReturnValueOnce(old).mockResolvedValueOnce([playlist({ id: 'new', name: 'New member list' })]);
    mocks.tracks.mockRejectedValueOnce(new Error('The playlist tracks are unavailable.'));
    const view = render(<MusicPlaylists owner="old" />);
    expect(screen.getByText('Loading playlists…')).toBeTruthy();
    view.rerender(<MusicPlaylists owner="new" />);
    expect(await screen.findByText('New member list')).toBeTruthy();
    finishOld([playlist({ id: 'old', name: 'Old member list' })]);
    await Promise.resolve();
    expect(screen.queryByText('Old member list')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /New member list/ }));
    expect(await screen.findByText('The playlist tracks are unavailable.')).toBeTruthy();
    expect(screen.getByText('No tracks are in this playlist.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play all' }).hasAttribute('disabled')).toBe(true);
  });

  it('builds smart rules without asking the member for an artist id', async () => {
    mocks.session.queue = [track('seed', 'Now playing')]; mocks.session.playback.index = 0;
    mocks.save.mockResolvedValue(playlist({ id: 'smart', name: 'Soul years', rules: { genres: ['Soul'], artist_id: 'artist-1', year_from: 1970, year_to: 1979, favorites: true, unplayed: false }, item_ids: [], count: null }));
    mocks.list.mockResolvedValueOnce([]).mockResolvedValueOnce([]);
    render(<MusicPlaylists owner="u1" />);
    await screen.findByText(/No playlists yet/);
    await userEvent.type(screen.getByLabelText('Name'), 'Soul years');
    await userEvent.click(screen.getByLabelText('Smart'));
    await userEvent.type(screen.getByLabelText(/Genres/), 'Soul');
    fireEvent.change(screen.getByLabelText('From year'), { target: { value: '1970' } });
    fireEvent.change(screen.getByLabelText('Through year'), { target: { value: '1979' } });
    await userEvent.click(screen.getByLabelText('Favorites only'));
    await userEvent.click(screen.getByLabelText('Only The Artist'));
    expect(screen.queryByLabelText(/artist id/i)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Create playlist' }));
    expect(mocks.save).toHaveBeenCalledWith({ name: 'Soul years', shared: false, item_ids: [], rules: { genres: ['Soul'], artist_id: 'artist-1', year_from: 1970, year_to: 1979, favorites: true, unplayed: false } });
  });
});
