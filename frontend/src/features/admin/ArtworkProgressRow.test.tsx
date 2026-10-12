import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getArtworkProgress } from '../../api';
import type { ArtworkProgress } from '../../types';
import { ArtworkProgressRow } from './ArtworkProgressRow';

vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), getArtworkProgress: vi.fn() }));

const serving = { hits_memory: 0, hits_disk: 0, misses: 0, generated: 0, fallback_original: 0, fallback_unavailable: 0, failures: 0 };
const progress = (patch: Partial<ArtworkProgress> = {}): ArtworkProgress => ({
  total: 41_200, prepared: 38_112, failed: 3, unsupported: 1, cache_bytes: 1.2e9, running: true, paused_for_playback: false,
  failure_reasons: { timeout: 3 }, serving, updated_at: null, ...patch,
});

describe('ArtworkProgressRow', () => {
  beforeEach(() => vi.mocked(getArtworkProgress).mockReset());

  it('reads "Artwork prepared n / m" with grouped numbers', async () => {
    vi.mocked(getArtworkProgress).mockResolvedValue(progress());
    render(<ArtworkProgressRow />);
    expect((await screen.findByRole('status')).textContent).toContain('Artwork prepared 38,112 / 41,200');
    expect(screen.queryByText('Paused while a video is being converted')).toBeNull();
  });

  it('has a progress bar named Artwork prepared with the rounded percentage', async () => {
    vi.mocked(getArtworkProgress).mockResolvedValue(progress());
    const { unmount } = render(<ArtworkProgressRow />);
    const bar = await screen.findByRole('progressbar', { name: 'Artwork prepared' });
    expect(bar.getAttribute('aria-valuenow')).toBe('92');
    unmount();
    vi.mocked(getArtworkProgress).mockResolvedValue(progress({ total: 0, prepared: 0 }));
    render(<ArtworkProgressRow />);
    expect((await screen.findByRole('progressbar', { name: 'Artwork prepared' })).hasAttribute('aria-valuenow')).toBe(false);
  });

  it('says when preparation is paused for playback', async () => {
    vi.mocked(getArtworkProgress).mockResolvedValue(progress({ paused_for_playback: true }));
    render(<ArtworkProgressRow />);
    expect(await screen.findByText('Paused while a video is being converted')).toBeTruthy();
  });

  it('shows a plain error with Try again that reloads', async () => {
    vi.mocked(getArtworkProgress).mockRejectedValueOnce({}).mockResolvedValue(progress());  // not an Error: the fixed copy shows
    render(<ArtworkProgressRow />);
    expect((await screen.findByRole('alert')).textContent).toContain('Lumina could not load artwork progress.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect((await screen.findByRole('status')).textContent).toContain('Artwork prepared 38,112 / 41,200');
  });
});
