/**
 * Gallery pure helpers, and stubs marked "contract stub", which are replaced test-first without changing their signatures.
 */
import { Disc3, Film, type LucideIcon, Mic2, Sparkles, Tv } from 'lucide-react';

import type { TitleFacetsQuery, TitleListQuery } from '../../api';
import type { SearchScope, TitleArt, TitleResolution, TitleSort, TitleSummary } from '../../types';
import { progressPercent } from '../titles/titleModel';

export type ArtKind = 'poster' | 'backdrop' | 'still' | 'logo' | 'square';
export type GalleryTheme = 'light' | 'dark';

/** The Library walls GalleryWall draws. */
export type WallKind = 'movies' | 'shows' | 'anime' | 'albums' | 'artists';
/** One wall's fixed behaviour. `sorts[0]` is the wall's default sort. */
export type WallDef = {
  heading: string;
  /** Singular and plural of the kicker's count noun: 'title'/'titles', 'album'/'albums', 'artist'/'artists'. */
  noun: [string, string];
  /** What the wall lists: movies → { category: 'movies' }, albums → { type: 'album' }. */
  query: Pick<TitleListQuery, 'category' | 'type'>;
  shape: 'poster' | 'square';
  sorts: TitleSort[];
  chips: boolean;
  search: SearchScope | null;
  facets: TitleFacetsQuery | null;
  featured: boolean;
  empty: { title: string; body: string; icon: LucideIcon };
};

const POSTER_SORTS: TitleSort[] = ['created', 'name', 'year', 'rating'];
const MUSIC_EMPTY = 'Add a music folder (Artist/Album/01 Track.flac) as an external root in Settings › Library & storage, or save a video as audio.';

/**
 * Every wall GalleryWall draws. Movies, Shows and Anime list their category, never a
 * type; music walls list their title type.
 */
export const WALLS: Record<WallKind, WallDef> = {
  movies: {
    heading: 'Movies', noun: ['title', 'titles'], query: { category: 'movies' }, shape: 'poster', sorts: POSTER_SORTS, chips: true, search: 'movies',
    facets: { category: 'movies' }, featured: true,
    empty: { title: 'No movies yet', body: 'Import a folder of movies (Movie (2019)/Movie (2019).mkv) in Settings › Library & storage.', icon: Film },
  },
  shows: {
    heading: 'Shows', noun: ['title', 'titles'], query: { category: 'shows' }, shape: 'poster', sorts: POSTER_SORTS, chips: true, search: 'shows',
    facets: { category: 'shows' }, featured: true,
    empty: { title: 'No shows yet', body: 'Import a folder of shows (Show/Season 1/Show S01E01.mkv) in Settings › Library & storage.', icon: Tv },
  },
  anime: {
    heading: 'Anime', noun: ['title', 'titles'], query: { category: 'anime' }, shape: 'poster', sorts: POSTER_SORTS, chips: true, search: 'anime',
    facets: { category: 'anime' }, featured: true,
    empty: { title: 'No anime yet', body: 'Keep anime in a folder named Anime (for example TV/Anime/Show/Season 1/…), or choose your anime folders in Settings › Library & storage.', icon: Sparkles },
  },
  albums: {
    heading: 'Albums', noun: ['album', 'albums'], query: { type: 'album' }, shape: 'square', sorts: ['created', 'name', 'year'], chips: false, search: null,
    facets: { type: 'album' }, featured: false, empty: { title: 'No music yet', body: MUSIC_EMPTY, icon: Disc3 },
  },
  artists: {
    heading: 'Artists', noun: ['artist', 'artists'], query: { type: 'artist' }, shape: 'square', sorts: ['name', 'created'], chips: false, search: null,
    facets: null, featured: false, empty: { title: 'No artists yet', body: MUSIC_EMPTY, icon: Mic2 },
  },
};

/** "1,735 titles", "1 album": a count with its singular or plural noun. */
export const countNoun = (count: number, [one, many]: readonly [string, string]): string => `${count.toLocaleString()} ${count === 1 ? one : many}`;

export const HEX_COLOUR = /^#[0-9a-f]{6}$/;
export const PREVIEW_PREFIX = /^data:image\/(?:webp|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/;
export const FALLBACK_PALETTE = ['#3d4a3f', '#4a3d45', '#3f4659', '#5a4a36', '#3a5357', '#563c3c', '#45453a', '#3c3f56'] as const;
export const GALLERY_RETRY_DELAYS_MS = [250, 750, 2_000, 5_000, 15_000, 45_000] as const;
export const prefersReducedMotion = (): boolean => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
export const CARD_TEXT_LIGHT = '#f4f1ea';
export const CARD_TEXT_DARK = '#15130f';

/** A server colour, only when it is exactly lowercase #rrggbb. */
export const safeColour = (value: string | null | undefined): string | null => (value && HEX_COLOUR.test(value) ? value : null);

/** A server preview, only as a webp/jpeg base64 data URI. */
export const safePreview = (value: string | null | undefined): string | null => (value && PREVIEW_PREFIX.test(value) ? value : null);

/** 32-bit FNV-1a over UTF-16 code units. */
function fnv1a(text: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

export const fallbackColour = (titleId: string): string => FALLBACK_PALETTE[fnv1a(titleId) % FALLBACK_PALETTE.length];

/** The rendition URL at `width`, or null when the image has no rendition of that width. */
export function renditionUrl(art: TitleArt | null | undefined, width: number): string | null {
  if (!art?.rendition || !art.widths.includes(width)) return null;
  return art.rendition.replace('{w}', String(width));
}

/** `srcset` for an image: every rendition width; stills add the original at its source width. */
export function artSrcSet(art: TitleArt | null | undefined, kind: ArtKind): string | null {
  if (!art?.rendition || !art.widths.length) return null;
  const entries = art.widths.map((width) => `${renditionUrl(art, width)} ${width}w`);
  const largest = Math.max(...art.widths);
  if (kind === 'still' && art.width && art.width > largest) entries.push(`${art.url} ${art.width}w`);
  return entries.join(', ');
}

/** Backdrop rendition for a hero preload or intent prefetch: 1920w on viewports >= 1200 px, else 960w. */
export const backdropWidthFor = (viewportWidth: number): 960 | 1920 => (viewportWidth >= 1200 ? 1920 : 960);

/**
 * Background for an image slot and its typographic card: the image's own dominant colour, then the other
 * art's (a poster falls back to the backdrop, anything else to the poster), then the palette.
 */
export function cardColour(title: Pick<TitleSummary, 'id' | 'poster' | 'backdrop'>, kind: ArtKind): { colour: string; fromPalette: boolean } {
  const own = kind === 'backdrop' ? title.backdrop : kind === 'logo' ? null : title.poster;
  const other = kind === 'poster' || kind === 'still' ? title.backdrop : title.poster;
  const colour = safeColour(own?.dominant) ?? safeColour(other?.dominant);
  return colour ? { colour, fromPalette: false } : { colour: fallbackColour(title.id), fromPalette: true };
}

const PAPER: Record<GalleryTheme, string> = { light: '#f4f1ea', dark: '#0f0e0c' };

function channels(hex: string): [number, number, number] {
  return [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two #rrggbb colours (1 to 21). */
export function contrastRatio(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

function toHsl(hex: string): [number, number, number] {
  const [r, g, b] = channels(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  const delta = max - min;
  if (delta === 0) return [0, 0, lightness];
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  const hue = max === r ? (g - b) / delta + (g < b ? 6 : 0) : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  return [hue * 60, saturation, lightness];
}

function fromHsl(hue: number, saturation: number, lightness: number): string {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const x = chroma * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = lightness - chroma / 2;
  const [r, g, b] = hue < 60 ? [chroma, x, 0] : hue < 120 ? [x, chroma, 0] : hue < 180 ? [0, chroma, x] : hue < 240 ? [0, x, chroma] : hue < 300 ? [x, 0, chroma] : [chroma, 0, x];
  return `#${[r, g, b].map((value) => Math.round(Math.min(1, Math.max(0, value + m)) * 255).toString(16).padStart(2, '0')).join('')}`;
}

/** Typographic card text: light on the palette; on an extracted colour, whichever of the two reads better. */
export function cardTextColour(background: string, fromPalette: boolean): string {
  const colour = safeColour(background);
  if (fromPalette || !colour) return CARD_TEXT_LIGHT;
  return contrastRatio(colour, CARD_TEXT_LIGHT) >= contrastRatio(colour, CARD_TEXT_DARK) ? CARD_TEXT_LIGHT : CARD_TEXT_DARK;
}

/** The title's accent, darkened on light paper or lightened on dark paper in 2 % steps until it reaches 3:1. */
export function readableAccent(hex: string | null | undefined, theme: GalleryTheme): string {
  const colour = safeColour(hex);
  if (!colour) return 'var(--g-ink)';
  const [hue, saturation, lightness] = toHsl(colour);
  const direction = theme === 'light' ? -1 : 1;
  for (let step = 0; step <= 50; step += 1) {
    const candidate = step === 0 ? colour : fromHsl(hue, saturation, Math.min(1, Math.max(0, lightness + direction * step * 0.02)));
    if (contrastRatio(candidate, PAPER[theme]) >= 3) return candidate;
  }
  return 'var(--g-ink)';
}

export type PosterMarker = { kind: 'unwatched' } | { kind: 'progress'; percent: number } | { kind: 'count'; count: number } | { kind: 'none' };

/** Movies and episodes are watched one by one; series, seasons and boxsets count unwatched episodes. */
const LEAF_TYPES = new Set<TitleSummary['type']>(['movie', 'episode']);

/** Plex-style state: gold corner when unwatched, progress bar when started, episode count on shows. */
export function posterMarker(title: TitleSummary): PosterMarker {
  if (LEAF_TYPES.has(title.type)) {
    if (title.user_data.played) return { kind: 'none' };
    return title.user_data.position_seconds > 0 ? { kind: 'progress', percent: Math.max(4, progressPercent(title)) } : { kind: 'unwatched' };
  }
  const count = title.user_data.unplayed_count ?? 0;
  return count > 0 ? { kind: 'count', count } : { kind: 'none' };
}

function minutesLeft(title: TitleSummary): number | null {
  const duration = title.user_data.duration_seconds || title.runtime_seconds || 0;
  const left = duration - title.user_data.position_seconds;
  return left > 0 ? Math.ceil(left / 60) : null;
}

/** The poster button's accessible name: the marker in words, because the marker itself is aria-hidden. */
export function posterLabel(title: TitleSummary): string {
  const named = title.year ? `${title.name}, ${title.year}` : title.name;
  const marker = posterMarker(title);
  if (marker.kind === 'unwatched') return `${named}, unwatched`;
  if (marker.kind === 'progress') {
    const left = minutesLeft(title);
    return left === null ? `${named}, in progress` : `${named}, in progress, ${left} minute${left === 1 ? '' : 's'} left`;
  }
  if (marker.kind === 'count') return `${title.name}, ${marker.count} unwatched episode${marker.count === 1 ? '' : 's'}`;
  return LEAF_TYPES.has(title.type) || title.user_data.unplayed_count === 0 ? `${named}, watched` : named;
}

/** A wall's state as it lives in the address. `q` is the "Find the one where…" query. */
export type WallQuery = { sort: TitleSort; unwatched: boolean; progress: boolean; fav: boolean; genre: string[]; from: number | null; to: number | null; res: TitleResolution[]; q: string };

export const DEFAULT_WALL: WallQuery = { sort: 'created', unwatched: false, progress: false, fav: false, genre: [], from: null, to: null, res: [], q: '' };
const WALL_SORTS: readonly TitleSort[] = ['created', 'name', 'year', 'rating'];
export const RESOLUTIONS: readonly TitleResolution[] = ['4k', '1080p', '720p', 'sd'];
const MAX_GENRES = 10;
const MAX_QUERY = 200;

export function wallYear(value: string | null | undefined): number | null {
  if (!value || !/^\d{4}$/.test(value)) return null;
  const year = Number(value);
  return year >= 1870 && year <= 2100 ? year : null;
}

/** The wall query from the address; invalid values are dropped silently. */
export function parseWallQuery(wall: string | undefined, sorts: readonly TitleSort[] = WALL_SORTS): WallQuery {
  const params = new URLSearchParams(wall ?? '');
  const sort = params.get('sort') as TitleSort | null;
  const q = (params.get('q') ?? '').trim();
  return {
    sort: sort && sorts.includes(sort) ? sort : sorts[0],
    unwatched: params.get('unwatched') === '1',
    progress: params.get('progress') === '1',
    fav: params.get('fav') === '1',
    genre: [...new Set(params.getAll('genre').filter((genre) => genre.length >= 1 && genre.length <= 64))].slice(0, MAX_GENRES),
    from: wallYear(params.get('from')),
    to: wallYear(params.get('to')),
    res: RESOLUTIONS.filter((value) => params.getAll('res').includes(value)),
    q: q.length <= MAX_QUERY ? q : '',
  };
}

/** Canonical address form: fixed key order, genres sorted, resolutions in 4K → SD order; '' for the wall's default. */
export function serializeWallQuery(query: WallQuery, defaultSort: TitleSort = 'created'): string {
  const params = new URLSearchParams();
  if (query.sort !== defaultSort) params.set('sort', query.sort);
  if (query.unwatched) params.set('unwatched', '1');
  if (query.progress) params.set('progress', '1');
  if (query.fav) params.set('fav', '1');
  for (const genre of [...query.genre].sort()) params.append('genre', genre);
  if (query.from !== null) params.set('from', String(query.from));
  if (query.to !== null) params.set('to', String(query.to));
  for (const value of RESOLUTIONS) if (query.res.includes(value)) params.append('res', value);
  if (query.q) params.set('q', query.q);
  return params.toString();
}

/** Facets set in the drawer: genre, year and resolution each count once ("Filters (n)"). */
export const activeFacetCount = (query: WallQuery): number =>
  Number(query.genre.length > 0) + Number(query.from !== null || query.to !== null) + Number(query.res.length > 0);

/** Any chip or facet is on (the masthead says "Filtered"); sort and search do not count. */
export const wallFiltered = (query: WallQuery): boolean => query.unwatched || query.progress || query.fav || activeFacetCount(query) > 0;

/** A wall's query from its address state, with whatever that wall does not offer dropped. */
export function wallQueryFor(wall: WallKind, state: string | undefined): WallQuery {
  const def = WALLS[wall];
  const query = parseWallQuery(state, def.sorts);
  return {
    ...query,
    ...(def.chips ? {} : { unwatched: false, progress: false, fav: false }),
    ...(def.facets ? {} : { genre: [], from: null, to: null }),
    res: def.facets && def.facets.type !== 'album' ? query.res : [],
    q: def.search ? query.q : '',
  };
}

/** The listing request of a wall: its own category or type, then the query. */
export function wallListQuery(wall: WallKind, query: WallQuery): TitleListQuery {
  return {
    ...WALLS[wall].query, sort: query.sort, unwatched: query.unwatched, in_progress: query.progress, favorites: query.fav,
    genre: query.genre, year_from: query.from, year_to: query.to, resolution: query.res,
  };
}

/** Session-cache key of a wall's pages: the search query does not change the grid behind it. */
export const wallKey = (wall: WallKind, query: WallQuery): string => `${wall}?${serializeWallQuery({ ...query, q: '' }, WALLS[wall].sorts[0])}`;

export type WallLayout = { columns: number; featured: boolean };
export type WallCell = { index: number; column: number; span: 1 | 3 };
const POSTER_MIN = 168;
const FEATURE_EVERY = 6;

export const wallColumns = (width: number, gap: number, phone: boolean): number => (phone ? 3 : Math.max(3, Math.floor((width + gap) / (POSTER_MIN + gap))));

export const wallFeatured = (query: WallQuery, columns: number, phone: boolean): boolean =>
  !phone && columns >= 6 && query.sort === 'created' && !query.q && !wallFiltered(query);

const featureRow = (row: number, layout: WallLayout): boolean => layout.featured && row % FEATURE_EVERY === 0;
const rowCapacity = (row: number, layout: WallLayout): number => (featureRow(row, layout) ? layout.columns - 2 : layout.columns);

/** Index of the first title in `row`; closed form, so it never depends on loaded data. */
export function rowStart(row: number, layout: WallLayout): number {
  if (!layout.featured) return row * layout.columns;
  const block = FEATURE_EVERY * layout.columns - 2;
  const within = row % FEATURE_EVERY;
  return Math.floor(row / FEATURE_EVERY) * block + (within === 0 ? 0 : layout.columns - 2 + (within - 1) * layout.columns);
}

export function rowOf(index: number, layout: WallLayout): number {
  if (!layout.featured) return Math.floor(index / layout.columns);
  const block = FEATURE_EVERY * layout.columns - 2;
  const offset = index % block;
  const base = Math.floor(index / block) * FEATURE_EVERY;
  return offset < layout.columns - 2 ? base : base + 1 + Math.floor((offset - (layout.columns - 2)) / layout.columns);
}

export const rowCount = (total: number, layout: WallLayout): number => (total > 0 ? rowOf(total - 1, layout) + 1 : 0);

/** One row's cells: 0-based grid column and span. A feature tile spans 3, first on even feature rows, last on odd ones. */
export function rowCells(row: number, layout: WallLayout, total: number): WallCell[] {
  const start = rowStart(row, layout);
  const capacity = rowCapacity(row, layout);
  const count = Math.max(0, Math.min(capacity, total - start));
  if (!featureRow(row, layout)) return Array.from({ length: count }, (_, offset) => ({ index: start + offset, column: offset, span: 1 }));
  const tileFirst = (row / FEATURE_EVERY) % 2 === 0;
  return Array.from({ length: count }, (_, offset): WallCell => {
    if (tileFirst) return offset === 0 ? { index: start, column: 0, span: 3 } : { index: start + offset, column: offset + 2, span: 1 };
    return offset === capacity - 1 ? { index: start + offset, column: layout.columns - 3, span: 3 } : { index: start + offset, column: offset, span: 1 };
  });
}

/** Up/Down: the cell in the next row whose centre is closest to this cell's centre; null past either end. */
export function verticalNeighbour(index: number, direction: 1 | -1, layout: WallLayout, total: number): number | null {
  const row = rowOf(index, layout);
  const target = row + direction;
  if (target < 0 || target >= rowCount(total, layout)) return null;
  const from = rowCells(row, layout, total).find((cell) => cell.index === index);
  if (!from) return null;
  const centre = (cell: WallCell) => cell.column + cell.span / 2;
  return rowCells(target, layout, total).reduce((best, cell) => (Math.abs(centre(cell) - centre(from)) < Math.abs(centre(best) - centre(from)) ? cell : best)).index;
}

/** Rows intersecting the viewport (`first`–`last`) and the rendered window with `overscan` either side (`from`–`to`). */
export function visibleRows(scrollTop: number, viewport: number, pitch: number, rows: number, overscan = 3): { first: number; last: number; from: number; to: number } {
  if (rows <= 0) return { first: 0, last: -1, from: 0, to: -1 };
  const first = Math.min(rows - 1, Math.max(0, Math.floor(scrollTop / pitch)));
  const last = Math.min(rows - 1, Math.max(first, Math.floor((scrollTop + viewport) / pitch)));
  return { first, last, from: Math.max(0, first - overscan), to: Math.min(rows - 1, last + overscan) };
}
