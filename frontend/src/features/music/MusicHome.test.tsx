import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MusicHome as MusicHomeData } from '../../api';
import { MusicHome } from './MusicHome';

const mocks = vi.hoisted(() => ({ home: vi.fn(), save: vi.fn(), restore: vi.fn(), toggle: vi.fn(), offline: vi.fn(), session: {
  owner: 'member', queue: [{ id: 'track-1' }, { id: 'track-2' }], playback: { index: 1, playing: false }, sharedRevision: 3, sharedConflict: null,
} }));
vi.mock('../../api', async (original) => ({ ...(await original<typeof import('../../api')>()), getMusicHome: mocks.home, saveMusicHomeLayout: mocks.save }));
vi.mock('./musicSession', () => ({ restoreMusicSharedSession: mocks.restore, toggleMusic: mocks.toggle, musicSession: () => mocks.session, useMusicSession: () => mocks.session }));
vi.mock('./musicOffline', () => ({ offlineTracks: mocks.offline }));

const home = (): MusicHomeData => ({
  layout: { order: ['resume', 'recent', 'playlists', 'downloads', 'mixes'], hidden: [] },
  resume: { state: 'playable', revision: 3, entry_index: 1, current_index: 1, position_seconds: 23, track: { id: 'track-2', title: 'Second light', artist: 'Mara Vale', album: 'Amber Rooms', album_id: 'album-1', artwork_url: null, duration: 180 } },
  recent: [{ id: 'album-1', type: 'album', name: 'Amber Rooms', genres: [], added_at: '', user_data: { played: false, is_favorite: false, position_seconds: 0 }, artist_name: 'Mara Vale' }],
  playlists: [{ id: 'playlist-1', name: 'Late night', shared: false, item_ids: [], count: 4, can_edit: true, revision: 1 }],
  mixes: [{ kind: 'rotation', name: 'Rotation' }],
});

describe('MusicHome', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.home.mockResolvedValue(home());
    mocks.save.mockImplementation(async (layout) => layout);
    mocks.offline.mockResolvedValue([]);
  });

  it('shows all five shelves and resumes the matching paused session explicitly', async () => {
    render(<MusicHome onBrowse={vi.fn()} onOpenPlaylists={vi.fn()} onOpenTitle={vi.fn()} owner="member" />);
    await screen.findByRole('heading', { name: 'Music Home' });
    expect(screen.getByRole('region', { name: 'Resume' }).textContent).toContain('23');
    expect(screen.getByRole('region', { name: 'Recently played' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Playlists' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Downloads' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Mixes' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Resume Second light' }));
    expect(mocks.restore).not.toHaveBeenCalled();
    expect(mocks.toggle).toHaveBeenCalledOnce();
  });

  it('saves keyboard-accessible shelf ordering and visibility, then restores focus on exit', async () => {
    render(<MusicHome onBrowse={vi.fn()} onOpenPlaylists={vi.fn()} onOpenTitle={vi.fn()} owner="member" />);
    await screen.findByRole('heading', { name: 'Music Home' });
    const edit = screen.getByRole('button', { name: 'Edit Music Home' });
    await userEvent.click(edit);
    const editor = screen.getByRole('region', { name: 'Music Home layout' });
    await userEvent.click(within(editor).getByRole('button', { name: 'Move Resume later' }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({ order: ['recent', 'resume', 'playlists', 'downloads', 'mixes'], hidden: [] }));
    await userEvent.click(within(editor).getByRole('button', { name: 'Hide Resume' }));
    await waitFor(() => expect(mocks.save).toHaveBeenLastCalledWith({ order: ['recent', 'resume', 'playlists', 'downloads', 'mixes'], hidden: ['resume'] }));
    expect(screen.queryByRole('region', { name: 'Resume' })).toBeNull();
    await userEvent.click(within(editor).getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(document.activeElement).toBe(edit));
  });

  it('keeps Downloads as a useful empty shelf while omitting empty enabled shelves', async () => {
    mocks.home.mockResolvedValue({ ...home(), resume: null, recent: [], playlists: [], mixes: [] });
    render(<MusicHome onBrowse={vi.fn()} onOpenPlaylists={vi.fn()} onOpenTitle={vi.fn()} owner="member" />);
    await screen.findByRole('heading', { name: 'Music Home' });
    expect(screen.queryByRole('region', { name: 'Resume' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Recently played' })).toBeNull();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Downloads' }).textContent).toContain('Save a track or queue'));
  });
});
