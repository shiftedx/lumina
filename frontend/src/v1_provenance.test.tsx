import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ getProvenance: vi.fn() }));
vi.mock('./api', () => api);

import { ProvenancePanel } from './features/watch/ProvenancePanel';
import type { Provenance } from './api';

afterEach(() => { vi.clearAllMocks(); });

const saved: Provenance = {
  origin: 'saved', provider: 'Youtube', original_url: 'https://www.youtube.com/watch?v=abc', channel: 'Alpine Films', channel_url: 'https://www.youtube.com/@alpine',
  uploaded_on: '2025-01-02', saved_at: '2026-01-01T10:00:00Z', storage_label: 'Library', storage_mode: 'managed', media_state: 'available', file_size: 1_500_000,
  format: { container: 'mov,mp4', video_codec: 'h264', audio_codec: 'aac', width: 1920, height: 1080 }, notes_count: 2,
  related: [{ reason: 'same_channel', name: 'Alpine Films', count: 3, items: [{ id: 'sib', title: 'Sibling film' }] }],
};

describe('ProvenancePanel', () => {
  it('test_provenance_one_video_one_origin: one origin, canonical links, related opens in-app', async () => {
    const onOpenItem = vi.fn();
    api.getProvenance.mockResolvedValue(saved);
    render(<ProvenancePanel itemId="film" onOpenItem={onOpenItem} />);
    expect(await screen.findByText('Saved from Youtube')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'www.youtube.com' }).getAttribute('href')).toBe('https://www.youtube.com/watch?v=abc');
    expect(screen.getByText('1920×1080 · h264 / aac · mov · 1.4 MB')).toBeTruthy();
    expect(screen.getByText('2 notes you can read')).toBeTruthy();
    expect(screen.queryByText(/sources/i)).toBeNull(); // no invented multi-source blend
    await userEvent.setup().click(screen.getByRole('link', { name: 'Sibling film' }));
    expect(onOpenItem).toHaveBeenCalledWith('sib');
  });

  it('test_import_origin_truthful: an external library item claims no online provider', async () => {
    api.getProvenance.mockResolvedValue({ ...saved, origin: 'imported', provider: null, original_url: null, channel_url: null, storage_label: 'Family NAS', storage_mode: 'external', format: null, notes_count: 0, related: [] });
    render(<ProvenancePanel itemId="ep" />);
    expect(await screen.findByText('Imported from Family NAS')).toBeTruthy();
    expect(screen.getByText('Family NAS · external, read-only')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByText(/Youtube/)).toBeNull();
    expect(screen.queryByText('Notes')).toBeNull();
  });
});
