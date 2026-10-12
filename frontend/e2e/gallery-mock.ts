/**
 * Mocked API for the gallery walls: paged titles by start index (Movies, Shows, Anime, albums, artists), facets, scoped
 * search, /api/art pixels, and Library sections.
 */
import type { Page } from '@playwright/test';

import { albumSummary, artistSummary, facets as defaultFacets, librarySections, movieSummary, seriesSummary, userData } from '../src/test/galleryFixtures';
import type { LibrarySections, LocalSearchResponse, TitleFacets, TitleLetter, TitleSummary } from '../src/types';

/** A 1×1 lossless WebP: what every /api/art request gets. */
const PIXEL = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64');
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

export type GalleryWallMock = {
  total?: number; anime?: number; albums?: number; artists?: number; facets?: TitleFacets; search?: LocalSearchResponse | 'fail';
  artDelayMs?: number; failTitles?: boolean; without?: Array<'backdrop' | 'poster'>;
};
export type WallTitleType = 'movie' | 'series' | 'album' | 'artist';

/** `total` titles named "A Title 0" … "Z Title n", in name order; every third movie watched, every fifth started. `prefix` keeps two walls' ids apart. */
export function wallTitles(type: WallTitleType, total: number, patch: Partial<TitleSummary> = {}, prefix: string = type): TitleSummary[] {
  return Array.from({ length: total }, (_, index) => {
    const id = `${prefix}-${String(index).padStart(4, '0')}`;
    const own = { name: `${LETTERS[Math.floor((index * LETTERS.length) / total)]} Title ${index}`, year: 1960 + (index % 60), ...patch };
    if (type === 'album') return albumSummary(id, { artist_name: `Artist ${index % 40}`, child_count: 8 + (index % 9), ...own });
    if (type === 'artist') return artistSummary(id, { child_count: 1 + (index % 5), ...own, year: null });
    if (type === 'series') return seriesSummary(id, { ...own, user_data: userData({ unplayed_count: index % 7 }) });
    const watched = index % 3 === 0 ? userData({ played: true }) : index % 5 === 0 ? userData({ position_seconds: 1200, duration_seconds: 6000 }) : userData();
    return movieSummary(id, { ...own, user_data: watched });
  });
}

/** The Anime wall: movies and series together (category anime), in name order. */
function animeTitles(total: number): TitleSummary[] {
  const movies = wallTitles('movie', Math.ceil(total / 2), { category: 'anime' }, 'anime-movie');
  const series = wallTitles('series', Math.floor(total / 2), { category: 'anime' }, 'anime-series');
  return [...movies, ...series].sort((a, b) => a.name.localeCompare(b.name));
}

function letterIndex(items: TitleSummary[]): TitleLetter[] {
  const letters: TitleLetter[] = [];
  items.forEach((item, index) => { if (letters.at(-1)?.letter !== item.name[0]) letters.push({ letter: item.name[0], index }); });
  return letters;
}

export async function mockGalleryWall(page: Page, options: GalleryWallMock = {}): Promise<URL[]> {
  const total = options.total ?? 300;
  // `without` strips art from every title, as a library with unfetched artwork has.
  const strip = (items: TitleSummary[]) => items.map((item) => ({ ...item, ...Object.fromEntries((options.without ?? []).map((kind) => [kind, null])) }));
  const lists = {
    movie: strip(wallTitles('movie', total, { category: 'movies' })), series: strip(wallTitles('series', total, { category: 'shows' })),
    anime: strip(animeTitles(options.anime ?? 24)), album: strip(wallTitles('album', options.albums ?? 120)), artist: strip(wallTitles('artist', options.artists ?? 108)),
  };
  const requests: URL[] = [];
  await page.route((url) => url.pathname === '/api/titles' || url.pathname === '/api/titles/facets' || url.pathname === '/api/search' || url.pathname.startsWith('/api/art/'), async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname.startsWith('/api/art/')) {
      if (options.artDelayMs) await new Promise((resolve) => setTimeout(resolve, options.artDelayMs));
      return route.fulfill({ status: 200, contentType: 'image/webp', headers: { 'Cache-Control': 'no-store' }, body: PIXEL });
    }
    if (url.pathname === '/api/titles/facets') return json(options.facets ?? defaultFacets);
    if (url.pathname === '/api/search') {
      if (options.search === 'fail') return json({ detail: 'Search is down' }, 503);
      return json(options.search ?? { query: url.searchParams.get('q'), mode: 'lexical', matches: [], items: [], index_generation: 1 });
    }
    if (options.failTitles) return json({ detail: 'boom' }, 500);
    // Category first, then type; no parameter is the Movies list, as before.
    const category = url.searchParams.get('category');
    const type = url.searchParams.get('type');
    const wall = category === 'anime' ? 'anime' : category === 'shows' || type === 'series' ? 'series' : type === 'album' || type === 'artist' ? type : 'movie';
    const filtered = ['unwatched', 'in_progress', 'favorites', 'genre', 'year_from', 'year_to', 'resolution'].some((key) => url.searchParams.has(key));
    const items = lists[wall].filter((_, index) => !filtered || index % 2 === 0);
    const letters = letterIndex(items);
    const cursor = url.searchParams.get('cursor');
    const letter = url.searchParams.get('letter');
    const start = cursor ? Number(cursor.slice(1)) : letter ? letters.find((entry) => entry.letter === letter)?.index ?? 0 : 0;
    const end = Math.min(items.length, start + Number(url.searchParams.get('limit') ?? 60));
    const first = !cursor && !letter;
    return json({
      items: items.slice(start, end), next_cursor: end < items.length ? `c${end}` : null, start_index: start,
      total: first ? items.length : null, letters: first && url.searchParams.get('sort') === 'name' ? letters : null,
    });
  });
  return requests;
}

/** GET /api/library/sections. Register after mockApi so it wins. */
export async function mockLibrarySections(page: Page, patch: Partial<LibrarySections> = {}): Promise<URL[]> {
  const requests: URL[] = [];
  await page.route((url) => url.pathname === '/api/library/sections', async (route) => {
    requests.push(new URL(route.request().url()));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(librarySections(patch)) });
  });
  return requests;
}
