import { afterEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { albumSummary, librarySections, movieSummary, stillItem, titlePage } from '../../test/galleryFixtures';
import { chapterColumns, chapterLimit, fetchChapter, forgetAllStore, gridGap, restoreAllStore, saveAllStore, sectionsKicker, spotlightKicker, spotlightPicks, visibleChapters } from './allStore';

afterEach(() => { vi.restoreAllMocks(); forgetAllStore(); });

describe('All landing model', () => {
  it('counts the non-empty sections in tab order, with singular nouns and anime invariant', () => {
    expect(sectionsKicker(librarySections())).toBe('1,735 movies · 812 shows · 29 anime · 110 albums · 318 videos · 12 recordings');
    expect(sectionsKicker(librarySections({ movies: 1, shows: 0, anime: 1, albums: 0, youtube: 0, recordings: 1 }))).toBe('1 movie · 1 anime · 1 recording');
    expect(visibleChapters(librarySections({ shows: 0, recordings: 0 })).map((chapter) => chapter.id)).toEqual(['movies', 'anime', 'music', 'youtube']);
  });

  it('spotlights three newest with backdrops in arrival order, filling from the newest without', () => {
    const titles = ['a', 'b', 'c', 'd', 'e'].map((id) => movieSummary(id, { name: id }));
    const bare = (id: string) => movieSummary(id, { name: id, backdrop: null });
    expect(spotlightPicks(titles).map((title) => title.id)).toEqual(['a', 'b', 'c']);
    expect(spotlightPicks([bare('a'), titles[1], bare('c'), titles[3]]).map((title) => title.id)).toEqual(['b', 'd', 'a']);
    expect(spotlightPicks([bare('a'), titles[1]]).map((title) => title.id)).toEqual(['b', 'a']);
    expect(spotlightPicks([bare('a')]).map((title) => title.id)).toEqual(['a']);
    expect(spotlightPicks([])).toEqual([]);
  });

  it('names a spotlight tile by its category noun, year and first genre', () => {
    expect(spotlightKicker(movieSummary())).toBe('Movie · 2019 · Adventure');
    expect(spotlightKicker(movieSummary('s', { type: 'series', category: 'shows', year: 2019, genres: ['Drama'] }))).toBe('Show · 2019 · Drama');
    expect(spotlightKicker(movieSummary('x', { type: 'series', category: 'anime', year: 2023, genres: [] }))).toBe('Anime · 2023');
    expect(spotlightKicker(movieSummary('y', { category: 'anime' }))).toBe('Anime · 2019 · Adventure');
  });

  it('lays chapters out by the wall formulas and asks for two rows, at most 40', () => {
    expect(gridGap(1440)).toBe(24);
    expect(gridGap(800)).toBe(16);
    expect(gridGap(390)).toBe(8);
    expect(chapterColumns('poster', 1344, 24, false, false)).toBe(7);
    expect(chapterColumns('square', 400, 16, false, false)).toBe(3);
    expect(chapterColumns('still', 1344, 24, false, false)).toBe(4);
    expect(chapterColumns('still', 300, 16, false, false)).toBe(2);
    expect(chapterColumns('poster', 1776, 24, false, true)).toBe(8);
    expect(chapterColumns('still', 1776, 24, false, true)).toBe(5);
    expect(chapterColumns('poster', 358, 8, true, false)).toBe(3);
    expect(chapterColumns('still', 358, 8, true, false)).toBe(2);
    expect(chapterLimit(7)).toBe(14);
    expect(chapterLimit(25)).toBe(40);
  });

  it('asks each chapter for its own list, newest first', async () => {
    const titles = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary()]));
    const items = vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [stillItem('v1')], next_cursor: 'c2' });
    expect(await fetchChapter('anime', 14)).toMatchObject({ kind: 'titles', limit: 14, fresh: true });
    await fetchChapter('music', 10);
    expect(titles.mock.calls.map(([query]) => query)).toEqual([{ category: 'anime', sort: 'created', limit: 14 }, { type: 'album', sort: 'created', limit: 10 }]);
    const videos = await fetchChapter('youtube', 8);
    await fetchChapter('recordings', 8);
    expect(items.mock.calls.map(([query]) => query)).toEqual([{ kind: 'video', status: 'available', sort: 'recent', limit: 8 }, { kind: 'recording', status: 'available', sort: 'recent', limit: 8 }]);
    expect(videos.items.map((item) => item.id)).toEqual(['v1']); // the server leaves deleted files out before the limit, so a full row stays full
  });

  it('passes the abort signal to every chapter request, still chapters too', async () => {
    const titles = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([movieSummary()]));
    const items = vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
    const { signal } = new AbortController();
    await fetchChapter('movies', 8, signal);
    await fetchChapter('youtube', 8, signal);
    expect(titles.mock.calls[0][1]).toEqual({ signal });
    expect(items.mock.calls[0][1]).toEqual({ signal });
  });

  it('keeps one landing for Back, marks its slices stale on the way back, and forgets it', () => {
    expect(restoreAllStore()).toBeNull();
    saveAllStore({ spotlight: [movieSummary()], slices: { movies: { kind: 'titles', items: [movieSummary()], limit: 14, fresh: true } }, scrollY: 900, focus: { key: 'movie-1', chapter: 'movies' } });
    expect(restoreAllStore()).toMatchObject({ scrollY: 900, focus: { key: 'movie-1', chapter: 'movies' }, slices: { movies: { limit: 14, fresh: false } } });
    forgetAllStore();
    expect(restoreAllStore()).toBeNull();
  });
});
