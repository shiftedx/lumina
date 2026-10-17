import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MusicTrack } from './musicTypes';

const api = vi.hoisted(() => ({ previewMusicWriteback: vi.fn(), applyMusicWriteback: vi.fn(), listMusicFileBackups: vi.fn(), restoreMusicFile: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { MusicWriteback } = await import('./MusicWriteback');

const track: MusicTrack = { id: 'track-1', title: 'First light', artist: 'Artist A', album_artist: 'Artist A', album: 'Album One', album_id: 'album-1', artist_id: 'artist-1', number: 1, disc: 1, duration: 180, artwork_url: null, audio_url: '/audio', codec: 'flac', sample_rate: 48000, bit_depth: 24, channels: 2, gain_db: -3, genres: ['Folk'], edited: true };

afterEach(() => vi.resetAllMocks());

describe('MusicWriteback', () => {
  it('does no file work before an explicit preview and Apply, then exposes the dated journal for restore', async () => {
    const onRefreshed = vi.fn();
    api.previewMusicWriteback.mockResolvedValue({ plan_id: 'p'.repeat(64), backup_bytes: 4096, include_artwork: true, entries: [{ item_id: 'track-1', title: 'First light', format: 'flac', supported: true, fields: { title: 'First light', genres: ['Folk'] } }] });
    api.applyMusicWriteback.mockResolvedValue({ applied: [{ id: 'journal-1', item_id: 'track-1', title: 'First light', format: 'flac' }], skipped: [] });
    api.listMusicFileBackups.mockResolvedValue([{ id: 'journal-1', item_id: 'track-1', created_at: '2026-10-12T12:00:00Z', restored: false }]);
    api.restoreMusicFile.mockResolvedValue({ restored: true });
    render(<MusicWriteback isAdmin onRefreshed={onRefreshed} tracks={[track]} />);

    await userEvent.click(screen.getByRole('checkbox', { name: 'Include current album artwork' }));
    expect(api.previewMusicWriteback).not.toHaveBeenCalled();
    expect(api.applyMusicWriteback).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Preview file writeback' }));
    expect(api.previewMusicWriteback).toHaveBeenCalledWith(['track-1'], true);
    expect(screen.getByText(/Backup space needed: 4\.0 KB/)).toBeTruthy();
    expect(api.applyMusicWriteback).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Apply to audio files' }));
    expect(api.applyMusicWriteback).toHaveBeenCalledWith('p'.repeat(64));
    expect(await screen.findAllByText('journal-1')).toHaveLength(2);
    expect(screen.getByText(/Oct 12, 2026/)).toBeTruthy();

    api.listMusicFileBackups.mockResolvedValue([{ id: 'journal-1', item_id: 'track-1', created_at: '2026-10-12T12:00:00Z', restored: true }]);
    await userEvent.click(screen.getByRole('button', { name: 'Restore file' }));
    expect(api.restoreMusicFile).toHaveBeenCalledWith('journal-1');
    expect(await screen.findByRole('button', { name: 'Restored' })).toBeTruthy();
    expect(onRefreshed).toHaveBeenCalledTimes(2);
  });

  it('is absent for non-admins', () => {
    const { container } = render(<MusicWriteback isAdmin={false} tracks={[track]} />);
    expect(container.childElementCount).toBe(0);
  });
});
