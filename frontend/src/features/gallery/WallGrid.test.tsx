import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TitleListQuery } from '../../api';
import { movieSummary, titlePage } from '../../test/galleryFixtures';
import { DEFAULT_WALL, type WallQuery } from './galleryModel';
import { resetImageLoader } from './imageLoader';
import { WallGrid, type WallGridHandle } from './WallGrid';
import { WallStore } from './wallPages';

const scrollTo = vi.fn();
const byName: WallQuery = { ...DEFAULT_WALL, sort: 'name' };

function viewport(width: number, height = 800) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}

function fakeList(total: number) {
  return vi.fn(async (query: TitleListQuery) => {
    const start = query.cursor ? Number(query.cursor.slice(1)) : 0;
    const limit = query.limit ?? 60;
    const items = Array.from({ length: Math.max(0, Math.min(limit, total - start)) }, (_, offset) => movieSummary(`movie-${start + offset}`, { name: `Title ${start + offset}` }));
    return titlePage(items, { total: query.cursor ? null : total, start_index: start, next_cursor: start + limit < total ? `c${start + limit}` : null });
  });
}

async function loadedStore(query: WallQuery, total = 1000) {
  const list = fakeList(total);
  const store = new WallStore({ type: 'movie', sort: query.sort }, list);
  store.subscribe(() => undefined);
  await waitFor(() => expect(store.total).toBe(total));
  return { store, list };
}

function grid(store: WallStore, query: WallQuery = byName, extra: Partial<Parameters<typeof WallGrid>[0]> = {}) {
  return render(<WallGrid label="Movies" metricLabel="movies" onExitUp={vi.fn()} onOpen={vi.fn()} query={query} stickyOffset={() => 0} store={store} {...extra} />);
}
const rendered = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>('.g-row')].map((row) => Number(row.dataset.row)).sort((a, b) => a - b);
const focused = () => document.activeElement?.getAttribute('data-index');
const press = (key: string, init: Record<string, unknown> = {}) => act(() => { fireEvent.keyDown(document.activeElement!, { key, ...init }); });

beforeEach(() => {
  resetImageLoader();
  viewport(1200);
  Object.defineProperty(window, 'scrollY', { configurable: true, writable: true, value: 0 });
  window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
  // The grid starts 100 px below the top of the page; everything else measures zero, as jsdom does.
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(this: HTMLElement) {
    const top = this.classList.contains('g-grid') ? 100 - window.scrollY : 0;
    return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  });
});
afterEach(() => { vi.restoreAllMocks(); scrollTo.mockReset(); });

describe('WallGrid', () => {
  it('renders only the rows around the viewport and follows the scroll, keeping the focused row (criterion 8)', async () => {
    const { store, list } = await loadedStore(byName);
    const { container } = grid(store);
    await screen.findByRole('button', { name: /^Title 0,/ });
    // 1200 px wide: 6 columns of 180 px, rows 314 px high plus a 24 px gap.
    expect(rendered(container)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(container.querySelectorAll('.g-row[data-row="0"] .g-poster')).toHaveLength(6);
    act(() => { window.scrollY = 338 * 20; window.dispatchEvent(new Event('scroll')); });
    expect(rendered(container)).toEqual([0, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]);
    await waitFor(() => expect(container.querySelector('.g-row[data-row="25"] .g-poster')).not.toBeNull());
    expect(list.mock.calls.map(([query]) => query.cursor ?? null)).toEqual([null, 'c60']);
  });

  it('shows hairline frames, never grey boxes, until a page arrives', async () => {
    const list = vi.fn(() => new Promise<never>(() => undefined));
    const store = new WallStore({ type: 'movie' }, list);
    const { container } = grid(store);
    expect(container.querySelector('.g-grid')?.getAttribute('aria-busy')).toBe('true');
    expect(container.querySelectorAll('.g-slot')).toHaveLength(18);
    expect(container.querySelector('.g-poster')).toBeNull();
  });

  it('shows exactly three rows of frames while the default wall loads, feature row included', () => {
    viewport(1400);
    const store = new WallStore({ type: 'movie' }, vi.fn(() => new Promise<never>(() => undefined)));
    const { container } = grid(store, DEFAULT_WALL);
    expect(rendered(container)).toEqual([0, 1, 2]);
    expect(container.querySelectorAll('.g-slot')).toHaveLength(5 + 7 + 7);
  });

  it('keeps focus on the same title, in view, when the rows re-flow on resize', async () => {
    const { store } = await loadedStore(byName);
    const handle: { current: WallGridHandle | null } = { current: null };
    grid(store, byName, { handle });
    await screen.findByRole('button', { name: /^Title 0,/ });
    act(() => handle.current?.jumpTo(40));
    await waitFor(() => expect(focused()).toBe('40'));
    scrollTo.mockReset();
    act(() => { viewport(1000); window.dispatchEvent(new Event('resize')); });
    // 5 columns now: index 40 moves from row 6 to row 8, below the fold.
    await waitFor(() => expect(focused()).toBe('40'));
    expect(document.activeElement?.closest('.g-row')?.getAttribute('data-row')).toBe('8');
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'auto' }));
  });

  it('moves by cell, row, row ends and the whole wall, and hands Up from the first row to the toolbar', async () => {
    const { store } = await loadedStore(byName);
    const onExitUp = vi.fn();
    grid(store, byName, { onExitUp });
    const first = await screen.findByRole('button', { name: /^Title 0,/ });
    expect(first.tabIndex).toBe(0);
    expect(screen.getByRole('button', { name: /^Title 1,/ }).tabIndex).toBe(-1);
    first.focus();
    press('ArrowRight');
    await waitFor(() => expect(focused()).toBe('1'));
    press('ArrowDown');
    await waitFor(() => expect(focused()).toBe('7'));
    press('End');
    await waitFor(() => expect(focused()).toBe('11'));
    press('Home', { ctrlKey: true });
    await waitFor(() => expect(focused()).toBe('0'));
    press('ArrowUp');
    expect(onExitUp).toHaveBeenCalledTimes(1);
  });

  it('jumps as the rail does when a letter is typed under name sort', async () => {
    const { store } = await loadedStore(byName);
    const onLetter = vi.fn();
    grid(store, byName, { onLetter });
    (await screen.findByRole('button', { name: /^Title 0,/ })).focus();
    press('m');
    expect(onLetter).toHaveBeenCalledWith('M');
  });

  it('jumps to an index, scrolling it under the toolbar and focusing its poster', async () => {
    const { store } = await loadedStore(byName);
    const handle: { current: WallGridHandle | null } = { current: null };
    grid(store, byName, { handle, stickyOffset: () => 140 });
    await screen.findByRole('button', { name: /^Title 0,/ });
    act(() => handle.current?.jumpTo(40));
    expect(scrollTo).toHaveBeenCalledWith({ top: 100 + 6 * 338 - 140, behavior: 'smooth' });
    await waitFor(() => expect(focused()).toBe('40'));
  });

  it('places a three-column feature tile first on the first row of the default wall', async () => {
    viewport(1400);
    const { store } = await loadedStore(DEFAULT_WALL);
    const { container } = grid(store, DEFAULT_WALL);
    await screen.findAllByRole('button');
    const first = container.querySelectorAll<HTMLElement>('.g-row[data-row="0"] > *');
    expect(first).toHaveLength(5);
    expect(first[0].classList.contains('g-feature')).toBe(true);
    expect(first[0].style.gridColumn).toBe('1 / span 3');
    expect(container.querySelectorAll('.g-row[data-row="1"] > *')).toHaveLength(7);
  });

  it('fills a feature tile with no usable backdrop from the poster, and names the title only in its caption', async () => {
    viewport(1400);
    const first = movieSummary('movie-0', { name: 'Resident Evil', year: 2026 });
    const cases = [
      { backdrop: first.backdrop && { ...first.backdrop, rendition: null, url: '' }, poster: first.poster }, // a backdrop that cannot load
      { backdrop: null, poster: first.poster },
      { backdrop: null, poster: null },
    ];
    for (const patch of cases) {
      // Alone on the wall: jsdom never loads the other cells' images, which would hold every load slot.
      const list = vi.fn(async () => titlePage([{ ...first, ...patch }], { total: 1 }));
      const store = new WallStore({ type: 'movie', sort: 'created' }, list);
      store.subscribe(() => undefined);
      await waitFor(() => expect(store.total).toBe(1));
      const view = grid(store, DEFAULT_WALL);
      const tile = await waitFor(() => view.container.querySelector<HTMLElement>('.g-feature') ?? Promise.reject(new Error('no tile')));
      // The caption, on its scrim, is the only place the name appears; the art is never a typographic card.
      expect(tile.querySelector('.g-card')).toBeNull();
      expect(tile.textContent?.split('Resident Evil')).toHaveLength(2);
      expect(tile.querySelector('.g-feature-copy .g-label')?.textContent).toMatch(/^2026 · /);
      if (patch.poster) await waitFor(() => expect(tile.querySelector<HTMLImageElement>('.g-art-poster img.g-art-image')?.getAttribute('src')).toContain('/Primary/'));
      else expect(tile.querySelector('img')).toBeNull();
      view.unmount();
      resetImageLoader();
    }
  });

  it('remembers where the wall was when a title opens', async () => {
    const { store } = await loadedStore(byName);
    const onOpen = vi.fn();
    grid(store, byName, { onOpen });
    window.scrollY = 1234;
    act(() => { screen.getAllByRole('button', { name: /^Title 3,/ })[0].click(); });
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'movie-3' }));
    expect([store.frozen, store.scrollY, store.focusIndex]).toEqual([true, 1234, 3]);
  });

  it('comes back to the same offset and poster, then refreshes the pages on screen', async () => {
    const { store, list } = await loadedStore(byName);
    store.freeze(3000, 40);
    grid(store);
    expect(scrollTo).toHaveBeenCalledWith(0, 3000);
    await waitFor(() => expect(focused()).toBe('40'));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(list.mock.calls[1][0].cursor ?? null).toBeNull();
    expect(store.frozen).toBe(false);
  });
  it('lays a square wall out in 1:1 rows of album cards and never features a tile', async () => {
    const { store } = await loadedStore(DEFAULT_WALL);
    const { container } = grid(store, DEFAULT_WALL, { label: 'Albums', metricLabel: 'albums', shape: 'square' });
    await screen.findByRole('button', { name: /^Title 0\b/ });
    // 1200 px wide: 6 columns of 180 px; a square row is 180 + 44 caption = 224 px high, plus a 24 px gap.
    const row = container.querySelector<HTMLElement>('.g-row[data-row="1"]');
    expect(row?.style.height).toBe('224px');
    expect(row?.style.top).toBe('248px');
    expect(container.querySelectorAll('.g-row[data-row="0"] .g-album')).toHaveLength(6);
    expect(container.querySelector('.g-feature')).toBeNull();
    expect(container.querySelector('.g-poster-frame')).toBeNull();
    expect(container.querySelector('.g-row[data-row="0"] .g-art-square')).not.toBeNull();
  });

  it('in select mode a click picks the title instead of opening it', async () => {
    const { store } = await loadedStore(byName);
    const pick = vi.fn();
    const onOpen = vi.fn();
    grid(store, byName, { onOpen, select: { on: true, selected: new Set(['movie-0']), pick } });
    const first = await screen.findByRole('button', { name: /^Title 0,/ });
    expect(first.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(await screen.findByRole('button', { name: /^Title 1,/ }), { shiftKey: true });
    expect(pick).toHaveBeenCalledWith(expect.objectContaining({ id: 'movie-1' }), 1, true);
    expect(onOpen).not.toHaveBeenCalled();
  });
});
