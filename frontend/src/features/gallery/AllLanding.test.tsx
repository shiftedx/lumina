import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from 'vitest';

import * as api from '../../api';
import type { TitleListQuery } from '../../api';
import { albumSummary, librarySections, movieSummary, seriesSummary, stillItem, titlePage } from '../../test/galleryFixtures';
import type { TitlePage } from '../../types';
import { forgetAllStore, saveAllStore } from './allStore';
import { AllLanding, type AllLandingProps } from './AllLanding';
import { resetImageLoader } from './imageLoader';

type Observed = { callback: IntersectionObserverCallback; elements: Element[] };
let observers: Observed[] = [];
class FakeObserver {
  callback: IntersectionObserverCallback;
  elements: Element[] = [];
  constructor(callback: IntersectionObserverCallback) { this.callback = callback; observers.push(this); }
  observe(element: Element) { this.elements.push(element); }
  unobserve() {}
  disconnect() {}
}
let resizers: ResizeObserverCallback[] = [];
class FakeResizer {
  constructor(callback: ResizeObserverCallback) { resizers.push(callback); }
  observe() {}
  unobserve() {}
  disconnect() {}
}
/** The chapter's section comes within a viewport. */
function reveal(id: string) {
  for (const observer of observers) {
    const target = observer.elements.find((element) => element.id === `g-chapter-${id}`);
    if (target) act(() => observer.callback([{ isIntersecting: true, target } as unknown as IntersectionObserverEntry], observer as unknown as IntersectionObserver));
  }
}
/** The band and the chapters arrive together, once the spotlight has answered. */
const settled = () => waitFor(() => expect(document.querySelector('.g-spotlight')).not.toBeNull());
const chapter = (id: string) => document.getElementById(`g-chapter-${id}`)!;
const kind = (query: TitleListQuery) => query.category ?? query.type ?? 'spotlight';
const lists = async (query: TitleListQuery): Promise<TitlePage> => titlePage(
  kind(query) === 'album' ? [albumSummary()] : kind(query) === 'shows' ? [seriesSummary()] : [movieSummary()],
);
let titles: MockInstance<typeof api.listTitles>;
let items: MockInstance<typeof api.listLibrary>;

function landing(overrides: Partial<AllLandingProps> = {}) {
  const props: AllLandingProps = {
    lenses: <nav aria-label="Library" className="g-tabs" data-focus-row><a aria-current="page" href="/library">All</a><a href="/library/movies">Movies</a></nav>,
    onOpenTitle: vi.fn(), onPlay: vi.fn(), onRetrySections: vi.fn(), onSeeAll: vi.fn(), sections: librarySections(), sectionsFailed: false, ...overrides,
  };
  return { props, ...render(<AllLanding {...props} />) };
}

beforeEach(() => {
  resetImageLoader();
  forgetAllStore();
  observers = [];
  resizers = [];
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  vi.stubGlobal('ResizeObserver', FakeResizer);
  titles = vi.spyOn(api, 'listTitles').mockImplementation(lists);
  items = vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [stillItem()], next_cursor: null });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); forgetAllStore(); });

describe('AllLanding', () => {
  it('names the Library and its totals under the tab row', async () => {
    landing();
    await settled();
    expect(screen.getByRole('navigation', { name: 'Library' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Library' })).toBeTruthy();
    expect(screen.getByText('1,735 movies · 812 shows · 29 anime · 110 albums · 318 videos · 12 recordings')).toBeTruthy();
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(['New in your library', 'Movies', 'Shows', 'Anime', 'Music', 'YouTube', 'Recordings']);
  });

  it('spotlights the three newest with backdrops, each named by category, year and genre', async () => {
    titles.mockImplementation(async (query) => (kind(query) === 'spotlight' ? titlePage([
      movieSummary('a', { name: 'Alpha', backdrop: null }), movieSummary('b', { name: 'Bravo' }),
      movieSummary('c', { name: 'Charlie', type: 'series', category: 'shows', year: 2021, genres: ['Drama'] }), movieSummary('d', { name: 'Delta' }), movieSummary('e', { name: 'Echo' }),
    ]) : lists(query)));
    const { container } = landing();
    await waitFor(() => expect([...container.querySelectorAll('.g-spotlight .g-feature-name')].map((name) => name.textContent)).toEqual(['Bravo', 'Charlie', 'Delta']));
    expect([...container.querySelectorAll('.g-spotlight .g-feature .g-label')].map((label) => label.textContent)).toEqual(['Movie · 2019 · Adventure', 'Show · 2021 · Drama', 'Movie · 2019 · Adventure']);
    expect(container.querySelector('.g-spotlight .is-lead')?.getAttribute('aria-label')).toBe('Bravo, 2019, unwatched');
  });

  it('shapes the band for one or two titles and leaves it out with none', async () => {
    titles.mockImplementation(async (query) => (kind(query) === 'spotlight' ? titlePage([movieSummary('a', { name: 'Alpha', backdrop: null }), movieSummary('b', { name: 'Bravo' })]) : lists(query)));
    const two = landing();
    await waitFor(() => expect(two.container.querySelector('.g-spotlight.is-count-2')).not.toBeNull());
    expect([...two.container.querySelectorAll('.g-spotlight .g-feature-name')].map((name) => name.textContent)).toEqual(['Bravo', 'Alpha']);
    two.unmount();
    forgetAllStore();
    titles.mockImplementation(async (query) => (kind(query) === 'spotlight' ? titlePage([]) : lists(query)));
    landing();
    await waitFor(() => expect(screen.queryByText('New in your library')).toBeNull());
  });

  it('loads the first two chapters with the page and the rest as they come within a viewport', async () => {
    landing();
    await settled();
    await within(chapter('shows')).findByRole('button', { name: /^Harbor Lights, / });
    expect(titles.mock.calls.map(([query]) => kind(query))).toEqual(['spotlight', 'movies', 'shows']);
    expect(items).not.toHaveBeenCalled();
    expect(within(chapter('anime')).queryByRole('button', { name: /^Northern Lantern/ })).toBeNull();
    reveal('anime');
    expect(await within(chapter('anime')).findByRole('button', { name: /^Northern Lantern, / })).toBeTruthy();
    reveal('music');
    expect(await within(chapter('music')).findByRole('button', { name: /Album One/ })).toBeTruthy();
    reveal('youtube');
    expect(await within(chapter('youtube')).findByRole('button', { name: /^Harbor walk at dawn, / })).toBeTruthy();
    expect(items.mock.calls[0][0]).toEqual({ kind: 'video', status: 'available', sort: 'recent', limit: 6 });
  });

  it('cancels the chapter requests still out when the landing is left', async () => {
    titles.mockImplementation(() => new Promise(() => undefined));
    const { unmount } = landing();
    await waitFor(() => expect(titles.mock.calls.some(([query]) => kind(query) === 'movies')).toBe(true));
    const signals = titles.mock.calls.filter(([query]) => kind(query) !== 'spotlight').map(([, options]) => options?.signal);
    expect(signals.length).toBeGreaterThan(0);
    expect(signals.every((signal) => signal && !signal.aborted)).toBe(true);
    unmount();
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
  });

  it('asks for two rows of its columns and more when the columns grow', async () => {
    const width = vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(600);
    landing();
    await waitFor(() => expect(titles).toHaveBeenCalledWith(expect.objectContaining({ category: 'movies', limit: 6 }), expect.anything()));
    width.mockReturnValue(1400);
    act(() => resizers.forEach((notify) => notify([], {} as ResizeObserver)));
    await waitFor(() => expect(titles).toHaveBeenCalledWith(expect.objectContaining({ category: 'movies', limit: 14 }), expect.anything()));
  });

  it('keeps a failed chapter to itself', async () => {
    titles.mockImplementation(async (query) => { if (kind(query) === 'movies') throw new Error('boom'); return lists(query); });
    landing();
    await settled();
    expect(await within(chapter('movies')).findByText('Lumina could not load your movies.')).toBeTruthy();
    expect(await within(chapter('shows')).findByRole('button', { name: /^Harbor Lights, / })).toBeTruthy();
    titles.mockImplementation(lists);
    await userEvent.click(within(chapter('movies')).getByRole('button', { name: 'Try again' }));
    expect(await within(chapter('movies')).findByRole('button', { name: /^Northern Lantern, / })).toBeTruthy();
  });

  it('drops a chapter whose slice comes back empty and asks for sections once', async () => {
    titles.mockImplementation(async (query) => (kind(query) === 'shows' ? titlePage([]) : lists(query)));
    const { props, rerender } = landing();
    await waitFor(() => expect(screen.queryByRole('heading', { level: 2, name: 'Shows' })).toBeNull());
    expect(props.onRetrySections).toHaveBeenCalledTimes(1);
    rerender(<AllLanding {...props} sections={librarySections()} />);
    await within(chapter('movies')).findByRole('button', { name: /^Northern Lantern, / });
    expect(props.onRetrySections).toHaveBeenCalledTimes(1);
  });

  it('shows loading frames, a sections error and an empty library', async () => {
    const loading = landing({ sections: null });
    expect(document.querySelector('.g-kicker')?.textContent?.trim()).toBe('');
    expect(document.querySelectorAll('.g-chapter.is-skeleton')).toHaveLength(0); // nothing is painted to shift while the counts are unknown (CLS)
    loading.unmount();
    forgetAllStore();
    const onRetrySections = vi.fn();
    const failing = landing({ sections: null, sectionsFailed: true, onRetrySections });
    const error = screen.getByText('Lumina could not load your library.').closest('.g-inline-error') as HTMLElement;
    await userEvent.click(within(error).getByRole('button', { name: 'Try again' }));
    expect(onRetrySections).toHaveBeenCalledTimes(1);
    failing.unmount();
    forgetAllStore();
    titles.mockResolvedValue(titlePage([]));
    landing({ sections: librarySections({ movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 0, recordings: 0 }) });
    expect(await screen.findByText('Your library is empty')).toBeTruthy();
    expect(screen.queryByText('New in your library')).toBeNull();
  });

  it('comes back from the store at once, refreshes what is in view, and refocuses the poster that opened', async () => {
    const first = landing();
    await settled();
    const poster = await within(chapter('movies')).findByRole('button', { name: /^Northern Lantern, / });
    await userEvent.click(poster);
    expect(first.props.onOpenTitle).toHaveBeenCalledWith(expect.objectContaining({ id: 'movie-1' }));
    first.unmount();
    titles.mockClear();
    titles.mockImplementation(() => new Promise(() => undefined));
    landing();
    const again = within(chapter('movies')).getByRole('button', { name: /^Northern Lantern, / }); // synchronously, from the store
    await waitFor(() => expect(document.activeElement).toBe(again));
    await waitFor(() => expect(titles.mock.calls.map(([query]) => kind(query))).toEqual(['spotlight', 'movies', 'shows']));
  });

  it('refocuses a poster past the first measure’s columns on Back, without waiting a frame', () => {
    const movies = Array.from({ length: 10 }, (_, index) => movieSummary(`movie-${index}`, { name: `Movie ${index}` }));
    saveAllStore({ spotlight: [], slices: { movies: { kind: 'titles', items: movies, limit: 10, fresh: true } }, scrollY: 0, focus: { key: 'movie-8', chapter: 'movies' } });
    vi.stubGlobal('requestAnimationFrame', () => 0);
    titles.mockImplementation(() => new Promise(() => undefined));
    landing();
    expect(document.activeElement).toBe(within(chapter('movies')).getByRole('button', { name: /^Movie 8, / }));
  });

  it('keeps what opened a title even when the next landing mounts before this one unmounts (a quick Back)', async () => {
    const first = landing();
    await settled();
    const poster = await within(chapter('movies')).findByRole('button', { name: /^Northern Lantern, / });
    await userEvent.click(poster);
    window.dispatchEvent(new Event('scroll')); // the title page scrolls the window while this landing is still mounted
    titles.mockImplementation(() => new Promise(() => undefined));
    const second = render(<AllLanding {...first.props} />);
    first.unmount();
    const again = within(second.container.querySelector<HTMLElement>('#g-chapter-movies')!).getByRole('button', { name: /^Northern Lantern, / });
    await waitFor(() => expect(document.activeElement).toBe(again));
  });

  it('moves along a row with Left and Right, to its ends with Home and End, and to the tabs with Ctrl+Home', async () => {
    titles.mockImplementation(async (query) => (kind(query) === 'spotlight' ? titlePage(['First', 'Second', 'Third'].map((name, index) => movieSummary(`s${index}`, { name }))) : lists(query)));
    landing();
    const lead = await screen.findByRole('button', { name: /^First, / });
    lead.focus();
    fireEvent.keyDown(lead, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(lead);
    fireEvent.keyDown(lead, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Second, / }));
    fireEvent.keyDown(document.activeElement!, { key: 'End' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Third, / }));
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Third, / }));
    fireEvent.keyDown(document.activeElement!, { key: 'Home' });
    expect(document.activeElement).toBe(lead);
    fireEvent.keyDown(lead, { key: 'Home', ctrlKey: true });
    expect(document.activeElement).toBe(screen.getByRole('link', { name: 'All' }));
  });

  it('mounts Collections only when it comes within a viewport', async () => {
    landing({ collections: <p>Household collections</p> });
    await settled();
    expect(screen.getByRole('heading', { level: 2, name: 'Collections' })).toBeTruthy();
    expect(screen.queryByText('Household collections')).toBeNull();
    reveal('collections');
    expect(screen.getByText('Household collections')).toBeTruthy();
  });

  it('opens a tab from See all on a plain click', async () => {
    const { props } = landing();
    await settled();
    const seeAll = screen.getByRole('link', { name: 'See all movies' });
    expect(seeAll.getAttribute('href')).toBe('/library/movies');
    fireEvent.click(seeAll);
    expect(props.onSeeAll).toHaveBeenCalledWith('movies');
    expect(screen.getByRole('link', { name: 'See all videos' }).getAttribute('href')).toBe('/library/youtube');
  });
});
