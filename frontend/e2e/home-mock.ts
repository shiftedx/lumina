/**
 * Mocked API for Home: every source Home reads, with fixtures a spec replaces, and the log of Home's own
 * requests. Register after mockApi so these routes win.
 */
import type { Page } from '@playwright/test';

import { movieSummary, seriesSummary, stillItem, titleDetail } from '../src/test/galleryFixtures';
import type { HouseholdCollection, LiveSnapshot, PlaybackProgress, TitleDetail, TitleRow, TitleSummary, WatchQueueEntry, YouTubeSearchResult } from '../src/types';
import { user } from './lumina-mock';

/** A 1×1 lossless WebP: every /api/art and remote artwork request gets it. */
const PIXEL = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64');
const AT = '2026-09-28T20:00:00Z';

export type HomeMock = {
  continueWatching?: PlaybackProgress[];
  nextUp?: TitleSummary[];
  newest?: TitleSummary[];
  anime?: TitleSummary[];
  albums?: TitleSummary[];
  rows?: TitleRow[];
  collections?: HouseholdCollection[];
  live?: Partial<LiveSnapshot>;
  queue?: WatchQueueEntry[];
  /** GET /api/titles/{id}; default titleDetail(summary) for any title listed here, and each episode's series. */
  details?: Record<string, TitleDetail>;
  /** Exact pathnames answered 500. */
  fail?: string[];
};

export const homeMovie = (index: number, patch: Partial<TitleSummary> = {}): TitleSummary =>
  movieSummary(`home-movie-${index}`, { name: `Home Movie ${index}`, year: 2000 + (index % 26), play_item_id: `item-home-movie-${index}`, ...patch });

/** A Continue watching entry for a title (its Library item names the title), 20 of 44 minutes in. */
export function continueEntry(title: TitleSummary, patch: Partial<PlaybackProgress> = {}): PlaybackProgress {
  const item = stillItem(title.play_item_id ?? `item-${title.id}`, { title: title.name, kind: title.type === 'episode' ? 'episode' : 'movie', title_id: title.id, extractor: 'external_library' });
  return { id: `pb-${title.id}`, user_id: user.id, item_id: item.id, position_seconds: 1200, duration_seconds: 2640, completed: false, last_watched_at: AT, created_at: AT, updated_at: AT, item, title, ...patch };
}

/** A live stream as /api/discovery/live lists it: Twitch, 12,400 watching. */
export const liveEntry = (id: string, patch: Partial<YouTubeSearchResult> = {}): YouTubeSearchResult => ({
  id, title: `Live stream ${id}`, uploader: `Channel ${id}`, webpage_url: `https://www.twitch.tv/${id}`, artwork_url: `/api/artwork/remote/${id}`,
  view_count: 12_400, source: 'twitch', source_label: 'Twitch', kind: 'live', ...patch,
});

export const liveSnapshot = (patch: Partial<LiveSnapshot> = {}): LiveSnapshot => ({
  items: [], categories: [], state: 'ready', refreshing: false, stale: false, twitch_available: true, hero: [], ...patch,
});

/** Every path Home itself requests; exported so a spec with its own catch-all route can fall back
 * into mockHome for Home's endpoints instead of duplicating them (register mockHome first). */
export const HOME_PATH = (path: string) => path === '/api/playback/continue' || path === '/api/titles' || path.startsWith('/api/titles/')
  || path === '/api/home/title-rows' || path === '/api/collections' || path.startsWith('/api/collections/') || path === '/api/discovery/live'
  || path === '/api/me/watch-queue' || path.startsWith('/api/art/') || path.startsWith('/api/artwork/remote/') || /^\/api\/library\/[^/]+\/playback-options$/.test(path);

/** Answers every Home source; returns Home's requests as "GET /path?query" (artwork excluded), in order. */
export async function mockHome(page: Page, options: HomeMock = {}): Promise<string[]> {
  const requests: string[] = [];
  const listed = [
    ...(options.continueWatching ?? []).flatMap((entry) => (entry.title ? [entry.title] : [])), ...(options.nextUp ?? []), ...(options.newest ?? []),
    ...(options.anime ?? []), ...(options.albums ?? []), ...(options.rows ?? []).flatMap((row) => row.items),
  ];
  const summaries = new Map<string, TitleSummary>(listed.map((title) => [title.id, title]));
  for (const title of listed) if (title.series_id && !summaries.has(title.series_id)) summaries.set(title.series_id, seriesSummary(title.series_id, { name: title.series_name ?? 'Harbor Lights' }));
  await page.route((url) => HOME_PATH(url.pathname), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.startsWith('/api/art/') || path.startsWith('/api/artwork/remote/')) return route.fulfill({ status: 200, contentType: 'image/webp', body: PIXEL });
    requests.push(`${request.method()} ${path}${url.search}`);
    if (options.fail?.includes(path)) return json({ detail: 'mock failure' }, 500);
    if (path === '/api/playback/continue') return json(options.continueWatching ?? []);
    if (path === '/api/titles') {
      const list = url.searchParams.get('category') === 'anime' ? options.anime : url.searchParams.get('type') === 'album' ? options.albums : options.newest;
      return json({ items: list ?? [], next_cursor: null });
    }
    if (path === '/api/titles/next-up') return json(options.nextUp ?? []);
    if (/^\/api\/titles\/[^/]+\/next-up\/dismiss$/.test(path)) return route.fulfill({ status: 204 });
    const detail = /^\/api\/titles\/([^/]+)$/.exec(path);
    if (detail) {
      const id = decodeURIComponent(detail[1]);
      const summary = summaries.get(id);
      const found = options.details?.[id] ?? (summary ? titleDetail(summary) : null);
      return found ? json(found) : json({ detail: 'Title not found' }, 404);
    }
    if (path === '/api/home/title-rows') return json({ rows: options.rows ?? [] });
    if (path === '/api/collections') return json(options.collections ?? []);
    const collection = /^\/api\/collections\/([^/]+)$/.exec(path);
    if (collection) {
      const found = options.collections?.find((entry) => entry.id === decodeURIComponent(collection[1]));
      return found ? json(found) : json({ detail: 'Not found' }, 404);
    }
    if (path === '/api/discovery/live') return json(liveSnapshot(options.live));
    if (path === '/api/me/watch-queue') return json({ revision: 1, limit: 500, entries: options.queue ?? [] });
    if (path.endsWith('/playback-options')) {
      return json({ mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 160, height: 90, duration: 1.5 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null });
    }
    return route.fallback();
  });
  return requests;
}
