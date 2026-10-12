import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import * as perfMetrics from '../../perfMetrics';
import { albumSummary, artistSummary, facets, movieSummary, seriesSummary, titlePage } from '../../test/galleryFixtures';
import type { LibraryItem, LocalSearchResponse, TitlePage } from '../../types';
import { GalleryWall } from './GalleryWall';
import { resetImageLoader } from './imageLoader';
import { ToastProvider } from '../../ui';
import { forgetWallStores } from './wallPages';

const scrollTo = vi.fn();
// A page that says there are more titles than it holds carries a cursor: the store ends the wall where the chain ends.
const page = (count: number, patch: Partial<TitlePage> = {}) => titlePage(Array.from({ length: count }, (_, index) => movieSummary(`movie-${index}`, { name: `Title ${index}` })), { next_cursor: (patch.total ?? count) > count ? 'more' : null, ...patch });

function Harness({ initial = '', changes = [], onPlayAt = vi.fn() }: { initial?: string; changes?: string[]; onPlayAt?: (itemId: string, startSeconds: number) => void }) {
  const [wall, setWall] = useState(initial);
  return <GalleryWall onOpen={vi.fn()} onPlayAt={onPlayAt} onWallChange={(next) => { changes.push(next); setWall(next); }} state={wall} wall="movies" />;
}

const searchResponse = (matches: LocalSearchResponse['matches']): LocalSearchResponse => ({ query: 'lighthouse', mode: 'lexical', matches, items: [], index_generation: 1 });
const northern = movieSummary('movie-7');
const titleHit = { kind: 'title' as const, id: 'movie-7', title: 'Northern Lantern', subtitle: '2019', score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical' as const, title_id: 'movie-7', media_title: northern };
const momentHit = { ...titleHit, kind: 'moment' as const, id: 'item-9@761000', subtitle: 'At 12:41', item: { id: 'item-9' } as LibraryItem, start_ms: 761_000 };

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) { this.removeAttribute('open'); this.dispatchEvent(new Event('close')); };
});
beforeEach(() => {
  forgetWallStores();
  resetImageLoader();
  window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
});
afterEach(() => { vi.restoreAllMocks(); scrollTo.mockReset(); });

describe('GalleryWall', () => {
  it('sets the masthead with the count and sort, and asks for the first 60 recently added', async () => {
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 1735 }));
    render(<Harness />);
    expect(screen.getByRole('heading', { level: 1, name: 'Movies' })).toBeTruthy();
    expect(await screen.findByText('1,735 titles · Sorted by recently added')).toBeTruthy();
    expect(list).toHaveBeenCalledWith(
      { category: 'movies', sort: 'created', unwatched: false, in_progress: false, favorites: false, genre: [], year_from: null, year_to: null, resolution: [], cursor: null, letter: null, limit: 60 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('keeps focus on a chip it toggles, writes the address, and announces the new count', async () => {
    vi.spyOn(api, 'listTitles').mockImplementation(async (query) => page(3, { total: query.unwatched ? 12 : 300 }));
    const changes: string[] = [];
    render(<Harness changes={changes} />);
    await screen.findByText('300 titles · Sorted by recently added');
    const chip = screen.getByRole('button', { name: 'Unwatched' });
    await userEvent.click(chip);
    expect(changes).toEqual(['unwatched=1']);
    expect(chip.getAttribute('aria-pressed')).toBe('true');
    expect(document.activeElement).toBe(chip);
    expect(await screen.findByText('12 titles · Filtered · Sorted by recently added')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe('12 titles');
    expect(scrollTo).toHaveBeenCalledWith({ top: 0 });
  });

  it('counts active facets on the Filters button and returns focus to it when the drawer closes', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    vi.spyOn(api, 'getTitleFacets').mockResolvedValue(facets);
    const changes: string[] = [];
    render(<Harness changes={changes} initial="genre=Drama&from=1990" />);
    const filters = screen.getByRole('button', { name: 'Filters (2)' });
    await userEvent.click(filters);
    await userEvent.click(await screen.findByRole('checkbox', { name: '4K 8' }));
    expect(api.getTitleFacets).toHaveBeenCalledWith({ category: 'movies' }, expect.anything());
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(changes.at(-1)).toBe('genre=Drama&from=1990&res=4k');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Filters (3)' }));
  });

  it('shows the A–Z rail only for Title A–Z, with empty letters disabled, and jumps to a letter', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(60, { total: 900, letters: [{ letter: 'A', index: 0 }, { letter: 'M', index: 40 }] }));
    const view = render(<Harness />);
    await screen.findByText('900 titles · Sorted by recently added');
    expect(screen.queryByRole('navigation', { name: 'Jump to letter' })).toBeNull();
    view.unmount();
    render(<Harness initial="sort=name" />);
    const rail = await screen.findByRole('navigation', { name: 'Jump to letter' });
    expect(rail.querySelectorAll('button')).toHaveLength(27);
    expect(screen.getByRole('button', { name: 'B' }).getAttribute('aria-disabled')).toBe('true');
    expect(screen.getByRole('button', { name: 'B' }).tabIndex).toBe(-1);
    expect(screen.getByRole('button', { name: 'M' }).tabIndex).toBe(0);
    await userEvent.click(screen.getByRole('button', { name: 'M' }));
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }));
    await waitFor(() => expect(document.activeElement?.getAttribute('data-index')).toBe('40'));
  });

  it('searches only on Enter, shows title and moment hits, plays a moment at its time, and clears', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    const search = vi.spyOn(api, 'searchLibrary').mockResolvedValue(searchResponse([titleHit, momentHit]));
    const changes: string[] = [];
    const onPlayAt = vi.fn();
    render(<Harness changes={changes} onPlayAt={onPlayAt} />);
    const field = screen.getByRole('searchbox', { name: 'Find the one where…' });
    await userEvent.type(field, 'lighthouse');
    expect(search).not.toHaveBeenCalled();
    await userEvent.type(field, '{Enter}');
    expect(changes.at(-1)).toBe('q=lighthouse');
    expect(await screen.findByRole('heading', { level: 2, name: 'Results for “lighthouse”' })).toBeTruthy();
    expect(search).toHaveBeenCalledWith('lighthouse', 30, 'movies', { signal: expect.any(AbortSignal) });
    expect(screen.getByRole('button', { name: 'Northern Lantern, 2019, unwatched' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Play Northern Lantern from 12:41' }));
    expect(onPlayAt).toHaveBeenCalledWith('item-9', 761);
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(changes.at(-1)).toBe('');
    expect(await screen.findByRole('group', { name: 'Movies' })).toBeTruthy();
  });

  it('says Searching… until results arrive, and cancels a search a newer one replaced', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    const signals: AbortSignal[] = [];
    vi.spyOn(api, 'searchLibrary').mockImplementation((_q, _limit, _scope, options) => { signals.push(options?.signal as AbortSignal); return new Promise(() => undefined); });
    render(<Harness initial="q=first" />);
    expect(await screen.findByText('Searching…')).toBeTruthy();
    const field = screen.getByRole('searchbox', { name: 'Find the one where…' });
    await userEvent.clear(field);
    await userEvent.type(field, 'second{Enter}');
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it('says when search is unavailable or finds nothing, and Escape clears the query', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    vi.spyOn(api, 'searchLibrary').mockRejectedValueOnce(new Error('down')).mockResolvedValue(searchResponse([]));
    const changes: string[] = [];
    render(<Harness changes={changes} initial="q=nothing" />);
    expect(await screen.findByText('Search is unavailable right now.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Nothing in your movies matches “nothing”.')).toBeTruthy();
    await userEvent.type(screen.getByRole('searchbox', { name: 'Find the one where…' }), '{Escape}');
    expect(changes.at(-1)).toBe('');
  });

  it('tells an empty library from an empty filter, and clears the filter', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(0, { total: 0 }));
    const view = render(<Harness />);
    expect(await screen.findByText('No movies yet')).toBeTruthy();
    view.unmount();
    const changes: string[] = [];
    render(<Harness changes={changes} initial="sort=name&fav=1" />);
    expect(await screen.findByText('Nothing matches these filters.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(changes.at(-1)).toBe('sort=name');
  });

  it('keeps loaded posters when a page fails and retries it', async () => {
    vi.spyOn(api, 'listTitles').mockRejectedValueOnce(new Error('boom')).mockResolvedValue(page(3, { total: 3 }));
    render(<Harness />);
    expect(await screen.findByText('Lumina could not load these titles.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('button', { name: /^Title 0,/ })).toBeTruthy();
  });

  it('lets ArrowDown from the masthead reach the grid, and Up from the first row reach Filters, never Sort', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(12, { total: 12 }));
    render(<Harness initial="sort=name" />);
    const poster = await screen.findByRole('button', { name: /^Title 0,/ });
    const heading = screen.getByRole('heading', { level: 1 });
    heading.tabIndex = -1;
    heading.focus();
    fireEvent.keyDown(heading, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(poster);
    fireEvent.keyDown(poster, { key: 'ArrowUp' });
    const filters = screen.getByRole('button', { name: 'Filters' });
    expect(document.activeElement).toBe(filters);
    fireEvent.keyDown(filters, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(poster);
  });

  it('moves Left/Right across the toolbar on a TV remote, out of the Sort select too', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(12, { total: 12 }));
    render(<Harness />);
    await screen.findByRole('button', { name: /^Title 0,/ });
    const sort = screen.getByRole('combobox', { name: 'Sort' });
    const unwatched = screen.getByRole('button', { name: 'Unwatched' });
    sort.focus();
    expect(fireEvent.keyDown(sort, { key: 'ArrowRight' })).toBe(false);
    expect(document.activeElement).toBe(unwatched);
    fireEvent.keyDown(unwatched, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(sort);
    expect(fireEvent.keyDown(sort, { key: 'ArrowLeft' })).toBe(false); // the toolbar's start: the sort never changes
    expect(document.activeElement).toBe(sort);
    const filters = screen.getByRole('button', { name: 'Filters' });
    filters.focus();
    fireEvent.keyDown(filters, { key: 'ArrowLeft' });
    const find = screen.getByRole('searchbox', { name: 'Find the one where…' }) as HTMLInputElement;
    expect(document.activeElement).toBe(find);
  });

  it('never traps a TV remote in the Find field: the caret moves inside, arrows leave at its ends, Down reaches the grid', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(12, { total: 12 }));
    render(<Harness />);
    const poster = await screen.findByRole('button', { name: /^Title 0,/ });
    const find = screen.getByRole('searchbox', { name: 'Find the one where…' }) as HTMLInputElement;
    await userEvent.type(find, 'ab');
    find.setSelectionRange(1, 1);
    expect(fireEvent.keyDown(find, { key: 'ArrowLeft' })).toBe(true); // native caret movement
    expect(document.activeElement).toBe(find);
    find.setSelectionRange(0, 0);
    fireEvent.keyDown(find, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Favorites' }));
    find.focus();
    find.setSelectionRange(2, 2);
    fireEvent.keyDown(find, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Filters' }));
    find.focus();
    fireEvent.keyDown(find, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(poster);
  });

  it('applies and returns focus to Filters when the drawer closes another way (Escape)', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    vi.spyOn(api, 'getTitleFacets').mockResolvedValue(facets);
    const changes: string[] = [];
    render(<Harness changes={changes} />);
    await userEvent.click(screen.getByRole('button', { name: 'Filters' }));
    await userEvent.click(await screen.findByRole('checkbox', { name: 'Drama 30' }));
    (screen.getByRole('dialog') as HTMLDialogElement).close(); // what the browser does on Escape
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(changes.at(-1)).toBe('genre=Drama');
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Filters (1)' }));
  });

  it('round-trips the drawer choice through the address: a reload and Back restore it', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    vi.spyOn(api, 'getTitleFacets').mockResolvedValue(facets);
    const changes: string[] = [];
    const first = render(<Harness changes={changes} />);
    await userEvent.click(screen.getByRole('button', { name: 'Filters' }));
    await userEvent.click(await screen.findByRole('checkbox', { name: '4K 8' }));
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(changes.at(-1)).toBe('res=4k');
    first.unmount();
    // Reload: the wall is built again from the address alone.
    const props = { onOpen: vi.fn(), onPlayAt: vi.fn(), onWallChange: vi.fn(), wall: 'movies' as const };
    const reloaded = render(<GalleryWall {...props} state={changes.at(-1)} />);
    await userEvent.click(screen.getByRole('button', { name: 'Filters (1)' }));
    expect((await screen.findByRole('checkbox', { name: '4K 8' }) as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: /^Show/ }));
    expect(props.onWallChange).not.toHaveBeenCalled();
    // Back: the address returns to the unfiltered wall.
    reloaded.rerender(<GalleryWall {...props} state="" />);
    await userEvent.click(screen.getByRole('button', { name: 'Filters' }));
    expect((await screen.findByRole('checkbox', { name: '4K 8' }) as HTMLInputElement).checked).toBe(false);
  });

  it('puts focus back in the search field after Clear, and shows a title found twice once', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    vi.spyOn(api, 'searchLibrary').mockResolvedValue(searchResponse([titleHit, { ...titleHit, id: 'movie-7#2' }]));
    render(<Harness initial="q=lighthouse" />);
    expect(await screen.findAllByRole('button', { name: 'Northern Lantern, 2019, unwatched' })).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Find the one where…' }));
  });

  it('on a phone, Close search returns focus to the Search button', async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: (query: string) => ({ matches: query === '(max-width: 599px)', media: query, addEventListener() {}, removeEventListener() {} }),
    });
    try {
      vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
      render(<Harness />);
      await userEvent.click(screen.getByRole('button', { name: 'Search' }));
      expect(document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Find the one where…' }));
      await userEvent.click(screen.getByRole('button', { name: 'Close search' }));
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Search' }));
    } finally {
      delete (window as { matchMedia?: unknown }).matchMedia;
    }
  });

  it('keeps focus in the search field after Clear, even when a title was opened from the wall before', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(page(12, { total: 12 }));
    vi.spyOn(api, 'searchLibrary').mockResolvedValue(searchResponse([titleHit]));
    const first = render(<Harness initial="sort=name" />);
    await userEvent.click(await screen.findByRole('button', { name: /^Title 1,/ })); // opens a title: the wall remembers poster 1
    first.unmount();
    render(<Harness initial="sort=name" />); // Back to the wall
    await waitFor(() => expect(document.activeElement?.getAttribute('data-index')).toBe('1'));
    const field = screen.getByRole('searchbox', { name: 'Find the one where…' });
    await userEvent.type(field, 'lighthouse{Enter}');
    await userEvent.click(await screen.findByRole('button', { name: 'Clear' }));
    await screen.findByRole('button', { name: /^Title 1,/ });
    await new Promise((resolve) => requestAnimationFrame(resolve));
    expect(document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Find the one where…' }));
  });

  it('lists the wall its wall prop names (library gallery contract)', async () => {
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(page(3, { total: 3 }));
    render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall="shows" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Shows' })).toBeTruthy();
    expect(await screen.findByText('3 titles · Sorted by recently added')).toBeTruthy();
    expect(list.mock.calls[0][0]).toMatchObject({ category: 'shows', sort: 'created' });
  });

  it('draws the Anime wall from its category, mixing movies and series with their own markers', async () => {
    const mixed = [movieSummary('anime-movie', { name: 'Film', category: 'anime' }), seriesSummary('anime-series', { name: 'Show A', category: 'anime' })];
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage(mixed, { total: 2 }));
    const metric = vi.spyOn(perfMetrics, 'recordMetric');
    const { container } = render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall="anime" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Anime' })).toBeTruthy();
    expect(await screen.findByText('2 titles · Sorted by recently added')).toBeTruthy();
    expect(list.mock.calls[0][0]).toMatchObject({ category: 'anime', sort: 'created' });
    expect(list.mock.calls[0][0]).not.toHaveProperty('type');
    expect(screen.getByRole('button', { name: 'Film, 2019, unwatched' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Show A, 4 unwatched episodes' })).toBeTruthy();
    expect(container.querySelector('.g-marker-triangle')).not.toBeNull();
    expect(container.querySelector('.g-marker-count')).not.toBeNull();
    expect(screen.getByRole('group', { name: 'Quick filters' })).toBeTruthy();
    await waitFor(() => expect(metric).toHaveBeenCalledWith('wall_first_screen_ms', 'anime', expect.any(Number)));
  });

  it('searches the Anime wall in scope anime, keeps its movies and series, and has its own empty copy', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 0 }));
    const show = seriesSummary('anime-series', { name: 'Show A', category: 'anime' });
    const search = vi.spyOn(api, 'searchLibrary').mockResolvedValue(searchResponse([{ ...titleHit, id: 'anime-series', title_id: 'anime-series', media_title: show }, titleHit]));
    const first = render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} state="q=lantern" wall="anime" />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Results for “lantern”' })).toBeTruthy();
    expect(search).toHaveBeenCalledWith('lantern', 30, 'anime', expect.anything());
    expect(await screen.findByRole('button', { name: 'Show A, 4 unwatched episodes' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Northern Lantern/ })).toBeTruthy();
    first.unmount();
    render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall="anime" />);
    expect(await screen.findByText('No anime yet')).toBeTruthy();
    expect(screen.getByText('Keep anime in a folder named Anime (for example TV/Anime/Show/Season 1/…), or choose your anime folders in Settings › Library & storage.')).toBeTruthy();
  });

  it('draws the Albums wall as square covers with no chips or search, three sorts, and genre and year filters only', async () => {
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary('album-1'), albumSummary('album-2', { name: 'Album Two' })], { total: 2 }));
    const facetsSpy = vi.spyOn(api, 'getTitleFacets').mockResolvedValue({ ...facets, resolutions: [] });
    const metric = vi.spyOn(perfMetrics, 'recordMetric');
    const { container } = render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall="albums" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Albums' })).toBeTruthy();
    expect(await screen.findByText('2 albums · Sorted by recently added')).toBeTruthy();
    expect(list.mock.calls[0][0]).toMatchObject({ type: 'album', sort: 'created' });
    expect(container.querySelectorAll('.g-album')).toHaveLength(2);
    expect(container.querySelector('.g-poster-frame')).toBeNull();
    expect(screen.queryByRole('group', { name: 'Quick filters' })).toBeNull();
    expect(screen.queryByRole('search')).toBeNull();
    expect([...screen.getByRole('combobox', { name: 'Sort' }).querySelectorAll('option')].map((option) => option.textContent)).toEqual(['Recently added', 'Title A–Z', 'Year']);
    await userEvent.click(screen.getByRole('button', { name: 'Filters' }));
    expect(facetsSpy).toHaveBeenCalledWith({ type: 'album' }, expect.anything());
    expect(screen.queryByRole('group', { name: 'Resolution' })).toBeNull();
    await waitFor(() => expect(metric).toHaveBeenCalledWith('wall_first_screen_ms', 'albums', expect.any(Number)));
  });

  it('sorts the Artists wall by name by default, offers Recently added, has no Filters, and Up from the first card reaches Sort', async () => {
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([artistSummary()], { total: 1 }));
    const changes: string[] = [];
    render(<GalleryWall onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={(next) => changes.push(next)} wall="artists" />);
    expect(await screen.findByText('1 artist · Sorted by title')).toBeTruthy();
    expect(list.mock.calls[0][0]).toMatchObject({ type: 'artist', sort: 'name' });
    expect(screen.queryByRole('button', { name: /^Filters/ })).toBeNull();
    const sort = screen.getByRole('combobox', { name: 'Sort' });
    expect([...sort.querySelectorAll('option')].map((option) => option.textContent)).toEqual(['Title A–Z', 'Recently added']);
    const card = await screen.findByRole('button', { name: 'Artist A, 4 albums' });
    card.focus();
    fireEvent.keyDown(card, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(sort);
    await userEvent.selectOptions(sort, 'created');
    expect(changes).toEqual(['sort=created']);
  });

  it('lets the Music tab replace the masthead, handing it the words after "Sorted by"', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary()], { total: 1 }));
    render(<GalleryWall header={(sortedBy) => <header><h1>Music</h1><p>{`Sorted by ${sortedBy}`}</p></header>} onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} state="sort=year" wall="albums" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Music' })).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 1, name: 'Albums' })).toBeNull();
    expect(screen.getByText('Sorted by year')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Album One by Artist A, 2019, 12 tracks' })).toBeTruthy();
  });

  describe('select mode and bulk edit', () => {
    const editable = (wall: 'movies' | 'albums' = 'movies', canEdit = true) => render(<ToastProvider><GalleryWall canEdit={canEdit} onOpen={vi.fn()} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall={wall} /></ToastProvider>);
    const posters = () => screen.findAllByRole('button', { name: /^Title \d/ });

    it('offers Select only to editors, and not on the music walls', async () => {
      vi.spyOn(api, 'listTitles').mockResolvedValue(page(3));
      const denied = editable('movies', false);
      await posters();
      expect(screen.queryByRole('button', { name: 'Select' })).toBeNull();
      denied.unmount();
      vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary()], { total: 1 }));
      editable('albums');
      await screen.findByRole('heading', { level: 1, name: 'Albums' });
      expect(screen.queryByRole('button', { name: 'Select' })).toBeNull();
    });

    it('toggles picks without opening, selects a range with shift, and Done returns focus to Select', async () => {
      vi.spyOn(api, 'listTitles').mockResolvedValue(page(4));
      const onOpen = vi.fn();
      render(<ToastProvider><GalleryWall canEdit onOpen={onOpen} onPlayAt={vi.fn()} onWallChange={vi.fn()} wall="movies" /></ToastProvider>);
      await posters();
      const select = screen.getByRole('button', { name: 'Select' });
      await userEvent.click(select);
      expect(select.getAttribute('aria-pressed')).toBe('true');
      const cards = await posters();
      expect(cards[0].getAttribute('aria-pressed')).toBe('false');
      await userEvent.click(cards[0]);
      expect(screen.getByText('1 selected')).toBeTruthy();
      fireEvent.click(cards[2], { shiftKey: true });
      expect(screen.getByText('3 selected')).toBeTruthy();
      expect(onOpen).not.toHaveBeenCalled();
      expect(screen.getByRole('button', { name: 'Edit details…' })).toBeTruthy();
      await userEvent.click(screen.getByRole('button', { name: 'Done' }));
      expect(screen.queryByRole('region', { name: 'Selection' })).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Select' }));
    });

    it('applies a bulk edit, toasts with Undo and undoes the batch once', async () => {
      vi.spyOn(api, 'listTitles').mockResolvedValue(page(3));
      const bulk = vi.spyOn(api, 'bulkEditMetadata').mockResolvedValue({ batch_id: 'batch-1', applied: 2, skipped: [] });
      const undo = vi.spyOn(api, 'undoMetadataBatch').mockResolvedValue({ batch_id: 'batch-1', restored: 2, skipped: [] });
      vi.spyOn(api, 'getMetadataVocabulary').mockResolvedValue([]);
      editable();
      await posters();
      await userEvent.click(screen.getByRole('button', { name: 'Select' }));
      const cards = await posters();
      await userEvent.click(cards[0]);
      await userEvent.click(cards[1]);
      await userEvent.click(screen.getByRole('button', { name: 'Edit details…' }));
      await userEvent.type(await screen.findByRole('combobox', { name: 'Add genres' }), 'Noir{Enter}');
      await userEvent.click(screen.getByRole('button', { name: 'Apply to 2 titles' }));
      expect(bulk).toHaveBeenCalledWith({ title_ids: ['movie-0', 'movie-1'], ops: [{ op: 'add', field: 'genres', values: ['Noir'] }] });
      expect(await screen.findByText('Updated 2 titles.')).toBeTruthy();
      expect(screen.queryByRole('region', { name: 'Selection' })).toBeNull();
      await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
      expect(undo).toHaveBeenCalledTimes(1);
      expect(undo).toHaveBeenCalledWith('batch-1');
      expect(await screen.findByText('Undone.')).toBeTruthy();
    });
  });
});
