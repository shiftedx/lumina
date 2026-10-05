import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { episodeSummary, movieSummary, seriesSummary, titleDetail, userData } from '../../test/galleryFixtures';
import type { TitleDetail } from '../../types';

const api = vi.hoisted(() => ({ setTitleWatched: vi.fn(), setFavorite: vi.fn(), addToWatchQueue: vi.fn(), getWatchQueue: vi.fn(), searchTitleMatches: vi.fn(), getTitle: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const warm = vi.hoisted(() => ({ prefetchPlaybackOptions: vi.fn(), speculativeStart: vi.fn(), cancelSpeculativeStart: vi.fn() }));
vi.mock('../../playbackPrefetch', () => warm);
const { SPECULATE_AFTER_MS, TitleActions } = await import('./TitleActions');
const { cachedTitle, forgetTitle, loadTitle } = await import('./titleCache');

beforeAll(() => {
  // jsdom has no modal dialog; model the two methods IdentifyDialog uses.
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute('open'); };
});
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks(); forgetTitle('movie-1'); forgetTitle('series-1'); });

const movie = titleDetail(movieSummary('movie-1', { play_item_id: 'v4k', user_data: userData({ position_seconds: 2530, resume_item_id: 'v4k' }) }), {
  versions: [{ item_id: 'v4k', height: 2160, hdr: true, file_size: 58 * 1024 ** 3 }, { item_id: 'v1080', height: 1080, hdr: false, file_size: 8 * 1024 ** 3 }],
  extras: [{ item_id: 'trailer-1', extra_type: 'trailer', name: 'Trailer', duration_seconds: 120 }],
});
const show = titleDetail(seriesSummary('series-1', { user_data: userData({ last_watched_at: '2026-09-01T12:00:00', unplayed_count: 4 }) }), { play_next: episodeSummary(2, 4, { play_item_id: 'i24' }) });

/** The page keeps the detail; this harness plays that part so patches re-render. */
function Harness({ initial, role = 'viewer', canEdit = false, onEdit, onPlay = () => undefined, onReload = () => undefined }: { initial: TitleDetail; role?: string; canEdit?: boolean; onEdit?: () => void; onPlay?: (itemId: string) => void; onReload?: () => void }) {
  const [detail, setDetail] = useState(initial);
  return <TitleActions detail={detail} onPatch={(update) => setDetail(update)} onPlay={onPlay} onEdit={onEdit} onReload={onReload} user={{ role, can_edit_details: canEdit }} />;
}

describe('TitleActions', () => {
  it('shows Edit details beside Fix match only to people who can edit, and opens the editor', async () => {
    const onEdit = vi.fn();
    const { unmount } = render(<Harness canEdit={false} initial={movie} onEdit={onEdit} />);
    expect(screen.queryByRole('button', { name: 'Edit details' })).toBeNull();
    unmount();
    render(<Harness canEdit initial={movie} onEdit={onEdit} role="viewer" />);
    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Fix match' })).toBeNull(); // Fix match stays admin-only
  });

  it('picks a movie version, resumes by default, plays the chosen file and the trailer, and hides Fix match from members', async () => {
    const onPlay = vi.fn();
    render(<Harness initial={movie} onPlay={onPlay} />);
    expect((screen.getByRole('radio', { name: '4K HDR · 58 GB' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole('button', { name: 'Resume at 42:10' })).toBeTruthy();
    await userEvent.click(screen.getByRole('radio', { name: '1080p · 8.0 GB' }));
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(onPlay).toHaveBeenCalledWith('v1080');
    await userEvent.click(screen.getByRole('button', { name: 'Trailer' }));
    expect(onPlay).toHaveBeenLastCalledWith('trailer-1');
    expect(screen.queryByRole('button', { name: 'Fix match' })).toBeNull();
  });

  it('shows why a movie cannot play when its only file is offline, and warms nothing', () => {
    render(<Harness initial={{ ...movie, user_data: userData(), versions: [{ ...movie.versions[0], media_state: 'offline' }] }} />);
    const play = screen.getByRole('button', { name: 'Play' });
    expect((play as HTMLButtonElement).disabled).toBe(true);
    expect(play.getAttribute('aria-describedby')).toBe('t-play-reason');
    expect(screen.getByText('This file’s drive is offline right now.')).toBeTruthy();
    expect(warm.prefetchPlaybackOptions).not.toHaveBeenCalled();
  });

  it('marks watched, favorites, queues the next episode, lets an admin fix the match, and forgets the cached detail', async () => {
    api.setTitleWatched.mockResolvedValue(userData({ played: true }));
    api.setFavorite.mockResolvedValue(undefined);
    api.addToWatchQueue.mockResolvedValue({ revision: 1, limit: 500, entries: [] });
    api.searchTitleMatches.mockResolvedValue([]);
    api.getTitle.mockResolvedValue(show);
    await loadTitle('series-1');
    const onReload = vi.fn();
    render(<Harness initial={show} onReload={onReload} role="admin" />);
    await userEvent.click(screen.getByRole('button', { name: 'Mark watched' }));
    expect(api.setTitleWatched).toHaveBeenCalledWith('series-1', true);
    expect(await screen.findByRole('button', { name: 'Watched' })).toBeTruthy();
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(cachedTitle('series-1')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Favorite' }));
    expect(api.setFavorite).toHaveBeenCalledWith('series-1', true);
    expect(screen.getByRole('button', { name: 'Favorite' }).getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(screen.getByRole('button', { name: 'Add to watchlist' }));
    expect(api.addToWatchQueue).toHaveBeenCalledWith({ kind: 'library', library_item_id: 'i24' }, 'end');
    await userEvent.click(screen.getByRole('button', { name: 'Fix match' }));
    expect(screen.getByRole('dialog', { name: 'Fix match' })).toBeTruthy();
  });

  it('says so when watched state cannot be saved', async () => {
    api.setTitleWatched.mockRejectedValue(new Error('boom'));
    render(<Harness initial={movie} />);
    await userEvent.click(screen.getByRole('button', { name: 'Mark watched' }));
    expect((await screen.findByRole('status')).textContent).toBe('Lumina could not update watched state. Try again.');
  });

  it('warms the primary action: prefetches its options, starts early after 400 ms of focus, and cancels on blur', () => {
    vi.useFakeTimers();
    render(<Harness initial={movie} />);
    expect(warm.prefetchPlaybackOptions).toHaveBeenCalledWith('v4k');
    const resume = screen.getByRole('button', { name: 'Resume at 42:10' });
    fireEvent.focus(resume);
    act(() => { vi.advanceTimersByTime(SPECULATE_AFTER_MS - 1); });
    expect(warm.speculativeStart).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(1); });
    expect(warm.speculativeStart).toHaveBeenCalledWith('v4k');
    fireEvent.blur(resume);
    expect(warm.cancelSpeculativeStart).toHaveBeenCalledWith('v4k');
  });

  it('cancels a pending early start when the pointer leaves or the page closes', () => {
    vi.useFakeTimers();
    const { unmount } = render(<Harness initial={movie} />);
    const resume = screen.getByRole('button', { name: 'Resume at 42:10' });
    fireEvent.pointerEnter(resume);
    fireEvent.pointerLeave(resume);
    expect(warm.cancelSpeculativeStart).toHaveBeenCalledTimes(1);
    fireEvent.pointerEnter(resume);
    unmount();
    act(() => { vi.advanceTimersByTime(SPECULATE_AFTER_MS); });
    expect(warm.cancelSpeculativeStart).toHaveBeenCalledTimes(2);
    expect(warm.speculativeStart).not.toHaveBeenCalled();
  });
});
