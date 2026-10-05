import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { albumDetail, albumSummary, albumTrack, userData } from '../../test/galleryFixtures';
import type { TitleDetail } from '../../types';

const api = vi.hoisted(() => ({ setFavorite: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { AlbumPage } = await import('./AlbumPage');
const { albumQueue, clearAlbumQueue } = await import('./albumQueue');
const { resetImageLoader } = await import('./imageLoader');

function albumPage(detail: TitleDetail | null, overrides: Record<string, unknown> = {}) {
  const props = {
    summary: albumSummary(), detail, headingRef: createRef<HTMLHeadingElement>(), backLabel: 'Music', onBack: vi.fn(), onKeyDown: vi.fn(),
    onOpenTitle: vi.fn(), onPlay: vi.fn(), onPatch: vi.fn(), ...overrides,
  };
  return { ...render(<AlbumPage {...props} />), props };
}

beforeEach(() => { clearAlbumQueue(); resetImageLoader(); });
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  delete (window as { matchMedia?: unknown }).matchMedia;
});

describe('AlbumPage', () => {
  it('sets the kicker, the artist link and the tracklist in order, grouped by disc', async () => {
    const tracks = [
      albumTrack(1, { duration_seconds: 1500 }), albumTrack(2, { duration_seconds: 1380 }),
      albumTrack(1, { item_id: 'track-d2', disc: 2, name: 'Closing', duration_seconds: 1440 }),
    ];
    const { container, props } = albumPage(albumDetail(albumSummary('album-1', { genres: ['Folk', 'Pop', 'Jazz', 'Rock'] }), tracks));
    expect(screen.getByText('Album · 2019 · 3 tracks · 1 h 12 min')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Album One' })).toBeTruthy();
    expect(screen.getByText('Folk · Pop · Jazz')).toBeTruthy();
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(['Disc 1', 'Disc 2']);
    expect(screen.getAllByRole('button', { name: /^Play \d/ }).map((row) => row.getAttribute('aria-label'))).toEqual(['Play 1. Song 1, 25:00', 'Play 2. Song 2, 23:00', 'Play 1. Closing, 24:00']);
    expect(container.querySelector('.g-album-cover .g-art-square')).not.toBeNull();
    await userEvent.click(screen.getByRole('link', { name: 'Artist A' }));
    expect(props.onOpenTitle).toHaveBeenCalledWith(expect.objectContaining({ id: 'artist-1', type: 'artist', name: 'Artist A' }));
  });

  it('marks played and in-progress tracks in words and offers to resume the latest one', () => {
    const tracks = [
      albumTrack(1, { user_data: userData({ played: true }) }),
      albumTrack(2, { duration_seconds: 900, user_data: userData({ position_seconds: 180, last_watched_at: '2026-09-01T10:00:00' }) }),
      albumTrack(3, { duration_seconds: 200, user_data: userData({ position_seconds: 170, last_watched_at: '2026-09-02T10:00:00' }) }),
      albumTrack(4, { number: null, name: 'Hidden', duration_seconds: 60 }),
    ];
    const { container } = albumPage(albumDetail(albumSummary(), tracks));
    expect(screen.getByRole('button', { name: 'Play 1. Song 1, 3:01, played' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play 2. Song 2, 15:00, 12 minutes left' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play 3. Song 3, 3:20, less than a minute left' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play Hidden, 1:00' })).toBeTruthy();
    expect(screen.getByText('12 min left')).toBeTruthy();
    expect(screen.getByText('<1 min left')).toBeTruthy();
    expect(screen.getByText('–')).toBeTruthy();
    expect(container.querySelectorAll('.g-track-progress')).toHaveLength(2);
    expect(container.querySelectorAll('.g-track-duration svg')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Resume · 3. Song 3' })).toBeTruthy();
  });

  it('plays a row with the album order queued, and Shuffle stores a shuffled order and plays its first', async () => {
    const onPlay = vi.fn();
    albumPage(albumDetail(), { onPlay });
    await userEvent.click(screen.getByRole('button', { name: /^Play 2\. Song 2/ }));
    expect(onPlay).toHaveBeenLastCalledWith('track-2');
    expect(albumQueue()).toMatchObject({ albumId: 'album-1', order: ['track-1', 'track-2', 'track-3'] });
    vi.spyOn(crypto, 'getRandomValues').mockImplementation((array) => { (array as Uint32Array)[0] = 0; return array; });
    await userEvent.click(screen.getByRole('button', { name: 'Shuffle' }));
    expect(albumQueue()?.order).toEqual(['track-2', 'track-3', 'track-1']);
    expect(onPlay).toHaveBeenLastCalledWith('track-2');
    await userEvent.click(screen.getByRole('button', { name: 'Play' }));
    expect(onPlay).toHaveBeenLastCalledWith('track-1');
    expect(albumQueue()?.order).toEqual(['track-1', 'track-2', 'track-3']);
  });

  it('links no artist for Various Artists, and shows each differing track artist', () => {
    albumPage(albumDetail(albumSummary('album-1', { artist_name: 'Various Artists' }), [albumTrack(1, { artist: 'Singer B' })]));
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText('by Various Artists')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Play 1. Song 1, by Singer B, 3:01' })).toBeTruthy();
    expect(screen.getByText('Singer B')).toBeTruthy();
  });

  it('says when every track is missing and disables Play and Shuffle', () => {
    albumPage(albumDetail(albumSummary(), []));
    expect(screen.getByText('These tracks are not available right now.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Play' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Shuffle' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('paints the summary before the detail, then toggles Favorite through the page’s patch', async () => {
    const first = albumPage(null);
    expect(screen.getByRole('heading', { level: 1, name: 'Album One' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Play \d/ })).toBeNull();
    expect((screen.getByRole('button', { name: 'Favorite' }) as HTMLButtonElement).disabled).toBe(true);
    first.unmount();
    api.setFavorite.mockResolvedValue(undefined);
    const { props } = albumPage(albumDetail());
    const favorite = screen.getByRole('button', { name: 'Favorite' });
    expect(favorite.getAttribute('aria-pressed')).toBe('false');
    await userEvent.click(favorite);
    expect(api.setFavorite).toHaveBeenCalledWith('album-1', true);
    const update = props.onPatch.mock.calls[0][0] as (current: TitleDetail) => TitleDetail;
    expect(update(albumDetail()).user_data.is_favorite).toBe(true);
  });

  it('lays out for a phone: one column, icon Shuffle and Favorite with their names', () => {
    window.matchMedia = vi.fn((query: string) => ({ matches: query === '(max-width: 599px)', addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
    const { container } = albumPage(albumDetail());
    expect(container.querySelector('.g-album-page.is-phone')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Shuffle' }).classList.contains('g-icon-button')).toBe(true);
    expect(screen.getByRole('button', { name: 'Favorite' }).classList.contains('g-icon-button')).toBe(true);
  });

  it('moves between rows with Up and Down, and Left reaches Play', () => {
    albumPage(albumDetail());
    const rows = screen.getAllByRole('button', { name: /^Play \d/ });
    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(rows[1]);
    fireEvent.keyDown(rows[1], { key: 'ArrowUp' });
    expect(document.activeElement).toBe(rows[0]);
    fireEvent.keyDown(rows[0], { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Play' }));
  });
});
