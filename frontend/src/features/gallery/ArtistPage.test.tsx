import { createRef } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { albumDetail, albumSummary, albumTrack, artistSummary, titleDetail } from '../../test/galleryFixtures';
import type { TitleDetail } from '../../types';

const api = vi.hoisted(() => ({ getTitle: vi.fn() }));
vi.mock('../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../api')>()), ...api }));
const { ArtistPage } = await import('./ArtistPage');
const { albumQueue, clearAlbumQueue } = await import('./albumQueue');
const { forgetTitles } = await import('./titleCache');
const { resetImageLoader } = await import('./imageLoader');

const albums = [albumSummary('album-2', { name: 'Album Two', year: 2021, child_count: 2 }), albumSummary('album-1', { child_count: 3 })];

function artistPage(detail: TitleDetail | null) {
  const props = { summary: artistSummary(), detail, headingRef: createRef<HTMLHeadingElement>(), backLabel: 'Music', onBack: vi.fn(), onKeyDown: vi.fn(), onOpenTitle: vi.fn(), onPlay: vi.fn() };
  return { ...render(<ArtistPage {...props} />), props };
}

beforeEach(() => { clearAlbumQueue(); resetImageLoader(); });
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); forgetTitles(); });

describe('ArtistPage', () => {
  it('sets the kicker from its albums and lists them as served, captioned by year', async () => {
    const { props } = artistPage(titleDetail(artistSummary(), { children: albums }));
    expect(screen.getByRole('heading', { level: 1, name: 'Artist A' })).toBeTruthy();
    expect(screen.getByText('Artist · 2 albums · 5 tracks')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 2, name: /^Albums/ })).toBeTruthy();
    const cards = screen.getAllByRole('button', { name: / by Artist A, / });
    expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual(['Album Two by Artist A, 2021, 2 tracks', 'Album One by Artist A, 2019, 3 tracks']);
    expect(screen.getByText('2021')).toBeTruthy();
    await userEvent.click(cards[1]);
    expect(props.onOpenTitle).toHaveBeenCalledWith(albums[1]);
  });

  it('plays every album in page order, and Shuffle all shuffles the lot', async () => {
    api.getTitle.mockImplementation(async (id: string) => (id === 'album-2'
      ? albumDetail(albums[0], [albumTrack(1, { item_id: 'b-1' }), albumTrack(2, { item_id: 'b-2' })])
      : albumDetail(albums[1])));
    const { props } = artistPage(titleDetail(artistSummary(), { children: albums }));
    await userEvent.click(screen.getByRole('button', { name: 'Play all' }));
    await waitFor(() => expect(props.onPlay).toHaveBeenCalledWith('b-1'));
    expect(albumQueue()).toMatchObject({ albumId: 'artist-1', order: ['b-1', 'b-2', 'track-1', 'track-2', 'track-3'] });
    vi.spyOn(crypto, 'getRandomValues').mockImplementation((array) => { (array as Uint32Array)[0] = 0; return array; });
    await userEvent.click(screen.getByRole('button', { name: 'Shuffle all' }));
    await waitFor(() => expect(props.onPlay).toHaveBeenLastCalledWith('b-2'));
    expect(albumQueue()?.order).toEqual(['b-2', 'track-1', 'track-2', 'track-3', 'b-1']);
  });

  it('says so when the artist has nothing to show, and offers nothing to play', () => {
    artistPage(titleDetail(artistSummary(), { children: [] }));
    expect(screen.getByText('Nothing by this artist is available right now.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Play all' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
