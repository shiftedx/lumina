import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError, type TitleListQuery } from '../../api';
import { movieSummary, titlePage } from '../../test/galleryFixtures';
import type { TitleLetter } from '../../types';
import { forgetWallStores, NEXT_PAGE, WallStore, wallStore } from './wallPages';

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function fakeList(total: number, letters: TitleLetter[] | null = null) {
  return vi.fn(async (query: TitleListQuery, _options?: { signal?: AbortSignal }) => {
    const letterStart = query.letter ? letters?.find((entry) => entry.letter === query.letter)?.index ?? 0 : null;
    const start = query.cursor ? Number(query.cursor.slice(1)) : letterStart ?? 0;
    const limit = query.limit ?? 60;
    const items = Array.from({ length: Math.max(0, Math.min(limit, total - start)) }, (_, offset) => movieSummary(`movie-${start + offset}`));
    const first = !query.cursor && !query.letter;
    return titlePage(items, { total: first ? total : null, letters: first ? letters : null, start_index: start, next_cursor: start + limit < total ? `c${start + limit}` : null });
  });
}
/** A keyset-cursor server over a list that can change: c60 means "after movie-59", wherever that now is. */
function keysetList(ids: () => string[]) {
  return vi.fn(async (query: TitleListQuery, _options?: { signal?: AbortSignal }) => {
    const all = ids();
    const start = query.cursor ? all.indexOf(`movie-${Number(query.cursor.slice(1)) - 1}`) + 1 : 0;
    const items = all.slice(start, start + (query.limit ?? 60)).map((id) => movieSummary(id));
    const end = start + items.length;
    return titlePage(items, { total: query.cursor ? null : all.length, start_index: start, next_cursor: end < all.length ? `c${Number(all[end - 1].slice(6)) + 1}` : null });
  });
}
const cursors = (list: ReturnType<typeof fakeList>) => list.mock.calls.map(([query]) => query.cursor ?? null);

beforeEach(() => forgetWallStores());

describe('wall pages', () => {
  it('loads 60 first, then 120 at a time, one request at a time, forward from the last contiguous page', async () => {
    const list = fakeList(1000);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    expect(list.mock.calls[0][0]).toMatchObject({ cursor: null, letter: null, limit: 60 });
    expect(store.total).toBe(1000);
    store.ensure(300, 320);
    expect(list).toHaveBeenCalledTimes(2);
    await settle();
    expect(cursors(list)).toEqual([null, 'c60', 'c180', 'c300']);
    expect(list.mock.calls.slice(1).every(([query]) => query.limit === NEXT_PAGE)).toBe(true);
    expect(store.itemAt(320)?.id).toBe('movie-320');
    expect(store.itemAt(1000)).toBeUndefined();
  });

  it('seeks a name-sorted wall from the nearest letter anchor, then follows its cursor', async () => {
    const letters = [{ letter: '#', index: 0 }, { letter: 'A', index: 10 }, { letter: 'M', index: 850 }];
    const list = fakeList(1000, letters);
    const store = new WallStore({ type: 'movie', sort: 'name' }, list);
    store.subscribe(() => undefined);
    await settle();
    expect(store.letters).toEqual(letters);
    store.ensure(990, 999);
    await settle();
    expect(list.mock.calls.slice(1).map(([query]) => [query.letter ?? null, query.cursor ?? null])).toEqual([['M', null], [null, 'c970']]);
    expect(store.itemAt(999)?.id).toBe('movie-999');
  });

  it('keeps loaded posters on a failed page, asks nothing more, and retries the same request', async () => {
    const list = fakeList(500);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    list.mockRejectedValueOnce(new ApiRequestError('boom', 500));
    store.ensure(100, 110);
    await settle();
    expect(store.failed).toBe(true);
    expect(store.itemAt(5)).toBeDefined();
    store.ensure(100, 111);
    await settle();
    expect(list).toHaveBeenCalledTimes(2);
    store.retry();
    await settle();
    expect(cursors(list)).toEqual([null, 'c60', 'c60']);
    expect(store.failed).toBe(false);
    expect(store.itemAt(110)).toBeDefined();
  });

  it('reloads from the first page when the server rejects a stale cursor', async () => {
    const list = fakeList(500);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    list.mockRejectedValueOnce(new ApiRequestError('Invalid cursor', 400));
    store.ensure(100, 110);
    await settle();
    expect(store.failed).toBe(false);
    expect(cursors(list)).toEqual([null, 'c60', null, 'c60']);
    expect(store.itemAt(110)).toBeDefined();
  });

  it('treats a page without total as the whole list', async () => {
    const list = vi.fn(async () => titlePage([movieSummary('movie-0'), movieSummary('movie-1')], { total: null, start_index: 0 }));
    const store = new WallStore({ type: 'movie' }, list);
    store.subscribe(() => undefined);
    await settle();
    expect(store.total).toBe(2);
    expect(store.itemAt(1)?.id).toBe('movie-1');
  });

  it('aborts and stops asking when nobody is watching', async () => {
    const list = vi.fn((_query: TitleListQuery, options?: { signal?: AbortSignal }) => new Promise<never>((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Request cancelled.', 'AbortError')));
    }));
    const store = new WallStore({ type: 'movie' }, list);
    const stop = store.subscribe(() => undefined);
    stop();
    await settle();
    expect(list.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(store.failed).toBe(false);
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('revalidates only the pages on screen, replacing them in place', async () => {
    const list = fakeList(500);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 110);
    await settle();
    list.mockClear();
    store.revalidate(70, 90);
    store.revalidate(70, 90);
    await settle();
    expect(cursors(list).filter((cursor) => cursor === 'c60')).toHaveLength(1);
    expect(store.itemAt(90)?.id).toBe('movie-90');
  });

  it('shows Try again when the server keeps rejecting cursors it issued', async () => {
    const list = fakeList(500);
    const pages = list.getMockImplementation()!;
    list.mockImplementation(async (query, options) => {
      if (query.cursor) throw new ApiRequestError('Invalid cursor', 400);
      return pages(query, options);
    });
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 110);
    await settle();
    expect(cursors(list)).toEqual([null, 'c60', null, 'c60']);
    expect(store.failed).toBe(true);
  });

  it('ends the wall where the cursor chain ends when the first page overstated the total', async () => {
    const list = fakeList(160);
    list.mockResolvedValueOnce(titlePage(Array.from({ length: 60 }, (_, index) => movieSummary(`movie-${index}`)), { total: 500, next_cursor: 'c60' }));
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 499);
    await settle();
    expect(store.total).toBe(160);
    expect(store.itemAt(159)?.id).toBe('movie-159');
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('shrinks the wall when revalidation finds a title gone', async () => {
    let size = 500;
    const list = vi.fn((query: TitleListQuery, options?: { signal?: AbortSignal }) => fakeList(size)(query, options));
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 178);
    await settle();
    size = 179;
    store.revalidate(100, 178);
    await settle();
    expect(store.total).toBe(179);
    expect(store.itemAt(178)?.id).toBe('movie-178');
    expect(store.itemAt(179)).toBeUndefined();
  });

  it('drops cached pages a refreshed page overlaps, so no poster repeats', async () => {
    let ids = Array.from({ length: 500 }, (_, index) => `movie-${index}`);
    const list = keysetList(() => ids);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 110);
    await settle();
    ids = ids.filter((id) => id !== 'movie-10');
    store.revalidate(70, 90);
    await settle();
    const shown = Array.from({ length: 179 }, (_, index) => store.itemAt(index)?.id);
    expect(new Set(shown).size).toBe(179);
    expect(shown[59]).toBe('movie-60');
    expect(store.itemAt(179)).toBeUndefined();
  });

  it('refetches a page that was in flight when the list changed, so its stale copy repeats no poster', async () => {
    let ids = Array.from({ length: 500 }, (_, index) => `movie-${index}`);
    const list = keysetList(() => ids);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    store.ensure(100, 110);
    ids = ids.filter((id) => id !== 'movie-10');
    store.revalidate(100, 110);
    await settle();
    const shown = Array.from({ length: 179 }, (_, index) => store.itemAt(index)?.id);
    expect(new Set(shown).size).toBe(179);
    expect(shown[59]).toBe('movie-60');
  });

  it('recovers a failed wall on revalidation', async () => {
    const list = fakeList(500);
    const store = new WallStore({ type: 'movie', sort: 'created' }, list);
    store.subscribe(() => undefined);
    await settle();
    list.mockRejectedValueOnce(new ApiRequestError('boom', 500));
    store.ensure(100, 110);
    await settle();
    expect(store.failed).toBe(true);
    store.revalidate(100, 110);
    await settle();
    expect(store.failed).toBe(false);
    expect(store.itemAt(110)?.id).toBe('movie-110');
  });

  it('keeps the five most recent wall queries for the session, one per wall kind', () => {
    const a = wallStore('movie?', { type: 'movie' });
    const b = wallStore('movie?sort=name', { type: 'movie', sort: 'name' });
    wallStore('movie?fav=1', { type: 'movie', favorites: true });
    wallStore('series?', { type: 'series' });
    wallStore('album?', { type: 'album' });
    expect(wallStore('movie?', { type: 'movie' })).toBe(a);
    wallStore('series?sort=name', { type: 'series', sort: 'name' });
    expect(wallStore('movie?', { type: 'movie' })).toBe(a);
    expect(wallStore('movie?sort=name', { type: 'movie', sort: 'name' })).not.toBe(b);
  });
});
