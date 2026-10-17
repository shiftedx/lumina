import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api';
import type { TitlePage, TitleSummary } from '../../types';
import type { MusicTrack } from './musicTypes';

const api = vi.hoisted(() => ({
  getMusicQueue: vi.fn(), getMusicTrackMetadata: vi.fn(), listTitles: vi.fn(), editMusicTracks: vi.fn(), undoMusicEdit: vi.fn(),
  createMusicRelease: vi.fn(),
  previewMusicWriteback: vi.fn(), applyMusicWriteback: vi.fn(), listMusicFileBackups: vi.fn(), restoreMusicFile: vi.fn(),
}));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { MusicTrackEditor } = await import('./MusicTrackEditor');

const owner = { id: 'owner', role: 'admin', can_edit_details: true };

function track(id: string, title: string, patch: Partial<MusicTrack> = {}): MusicTrack {
  return { id, title, artist: 'Artist A', album_artist: 'Artist A', album: 'Album One', album_id: 'album-1', artist_id: 'artist-1', number: 1, disc: 1, duration: 180, artwork_url: null, audio_url: `/api/music/tracks/${id}/audio`, codec: 'flac', sample_rate: 48000, bit_depth: 24, channels: 2, gain_db: null, genres: ['Folk'], edited: false, ...patch };
}

function album(id: string, name: string): TitleSummary {
  return { id, type: 'album', name, year: 2024, artist_name: 'Artist A', genres: [], added_at: '2026-01-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 } };
}

function page(items: TitleSummary[], next_cursor: string | null = null): TitlePage {
  return { items, next_cursor, total: items.length, start_index: 0 };
}

function renderEditor(tracks = [track('track-1', 'First light')]) {
  api.getMusicQueue.mockResolvedValue(tracks);
  api.listTitles.mockResolvedValue(page([album('album-1', 'Album One')]));
  api.getMusicTrackMetadata.mockResolvedValue({ fields: { title: 'Protected title', artist: 'Artist A', album_artist: 'Artist A', album_id: 'album-1', track_number: 1, disc_number: 1, genres: ['Folk'], lyrics: 'Old words', gain_db: null }, overridden: ['title'] });
  render(<MusicTrackEditor albumId="album-1" albumName="Album One" onClose={vi.fn()} open user={owner} />);
}

afterEach(() => vi.resetAllMocks());

describe('MusicTrackEditor', () => {
  it('requires preview, sends original optimistic bases, and can undo the resulting batch', async () => {
    const current = track('track-1', 'First light', { edited: true });
    renderEditor([current]);
    await screen.findByRole('checkbox', { name: 'First light' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'First light' }));
    await screen.findByText(/Protected manual value/);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Title action' }), 'set');
    const title = screen.getByRole('textbox', { name: 'New title' });
    expect((title as HTMLInputElement).value).toBe('Protected title');
    await userEvent.clear(title);
    await userEvent.type(title, 'New title');
    expect(api.editMusicTracks).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    expect(screen.getByText('Title: New title')).toBeTruthy();
    expect(api.editMusicTracks).not.toHaveBeenCalled();

    api.editMusicTracks.mockResolvedValue({ batch_id: 'batch-1', applied: 1, tracks: [{ ...current, title: 'New title', edited: true }] });
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    expect(api.editMusicTracks).toHaveBeenCalledWith({ item_ids: ['track-1'], changes: { title: 'New title' }, base: { 'track-1': { title: 'Protected title' } } });
    expect(await screen.findByText('Updated 1 track.')).toBeTruthy();
    api.undoMusicEdit.mockResolvedValue({ applied: 1, skipped: [] });
    api.getMusicQueue.mockResolvedValue([current]);
    await userEvent.click(screen.getByRole('button', { name: 'Undo track edit' }));
    expect(api.undoMusicEdit).toHaveBeenCalledWith('batch-1');
    expect(await screen.findByText(/Undo restored 1/)).toBeTruthy();
  });

  it('loads paged album choices and regroups multiple tracks only after review', async () => {
    const rows = [track('track-1', 'First light'), track('track-2', 'Second light', { number: 2 })];
    api.getMusicQueue.mockResolvedValue(rows);
    api.getMusicTrackMetadata.mockResolvedValue({ fields: {}, overridden: [] });
    api.listTitles.mockImplementation(({ cursor }: { cursor?: string | null }) => Promise.resolve(cursor === 'more' ? page([album('album-2', 'Album Two')]) : page([album('album-1', 'Album One')], 'more')));
    render(<MusicTrackEditor albumId="album-1" albumName="Album One" onClose={vi.fn()} open user={owner} />);
    await screen.findByRole('checkbox', { name: 'First light' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'First light' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Second light' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Album action' }), 'set');
    await userEvent.click(screen.getByRole('button', { name: 'Load more albums' }));
    expect(api.listTitles).toHaveBeenLastCalledWith({ type: 'album', sort: 'name', cursor: 'more', limit: 60 });
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Album' }), 'album-2');
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    expect(screen.getByText('Album: Album Two')).toBeTruthy();
    expect(api.editMusicTracks).not.toHaveBeenCalled();
  });

  it('reports changed-since conflicts as skips and never retries the mutation', async () => {
    renderEditor();
    await screen.findByRole('checkbox', { name: 'First light' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'First light' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Genres action' }), 'set');
    const genres = screen.getByRole('textbox', { name: 'New genres' });
    await userEvent.clear(genres);
    await userEvent.type(genres, 'Jazz');
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    api.editMusicTracks.mockRejectedValue(new ApiRequestError('Tracks changed while you were editing', 409, null, null, { detail: { message: 'Tracks changed while you were editing', conflicts: [{ item_id: 'track-1' }] } }));
    await userEvent.click(screen.getByRole('button', { name: 'Apply changes' }));
    expect(await screen.findByText(/changed after this editor loaded and were skipped/)).toBeTruthy();
    expect(api.editMusicTracks).toHaveBeenCalledOnce();
    await waitFor(() => expect(api.getMusicQueue).toHaveBeenCalledTimes(2));
  });

  it('previews and atomically creates a distinct edition with the selected tracks’ existing artist', async () => {
    const rows = [track('track-1', 'First light'), track('track-2', 'Second light', { number: 2 })];
    renderEditor(rows);
    await screen.findByRole('checkbox', { name: 'First light' });
    await userEvent.click(screen.getByRole('checkbox', { name: 'First light' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Second light' }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Album action' }), 'set');
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Regroup tracks into' }), 'create');
    await userEvent.type(screen.getByRole('textbox', { name: 'Release name' }), 'Album One Live');
    await userEvent.type(screen.getByRole('spinbutton', { name: 'Release year' }), '2025');
    await userEvent.type(screen.getByRole('textbox', { name: 'Edition' }), 'Tour edition');
    expect(screen.getByText(/Artist:/).textContent).toContain('Artist A');
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    expect(screen.getByText('Release: Album One Live')).toBeTruthy();
    expect(api.createMusicRelease).not.toHaveBeenCalled();
    api.createMusicRelease.mockResolvedValue({ batch_id: 'release-batch', applied: 2, album_id: 'new-album', tracks: rows.map((entry) => ({ ...entry, album_id: 'new-album', album: 'Album One Live' })) });
    await userEvent.click(screen.getByRole('button', { name: 'Create release' }));
    expect(api.createMusicRelease).toHaveBeenCalledWith({
      item_ids: ['track-1', 'track-2'], name: 'Album One Live', artist_id: 'artist-1', year: 2025,
      edition: 'Tour edition', base: { 'track-1': { album_id: 'album-1' }, 'track-2': { album_id: 'album-1' } },
    });
    expect(await screen.findByText('Updated 2 tracks.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo track edit' })).toBeTruthy();
  });
});
