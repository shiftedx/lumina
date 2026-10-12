/** Mocked API for the gallery title page. Register after mockApi and mockTitles so these routes win. */
import type { Page, Route } from '@playwright/test';

import type { EpisodeSummaries, KeyScenes, RecapResponse, TitleDetail, TitleSummary } from '../src/types';

/** A 1×1 lossless WebP served for every /api/art rendition. */
const WEBP_1X1 = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64');

/** A response body, or an HTTP status to fail with. */
type Reply<T> = T | number;

export type GalleryTitleOptions = {
  detail: TitleDetail;
  /** Other titles the page may load (the source episode of a show's key scenes). */
  others?: TitleDetail[];
  episodes?: Record<number, TitleSummary[]>;
  similar?: TitleSummary[];
  recap?: Reply<RecapResponse>;
  summaries?: Reply<EpisodeSummaries>;
  keyScenes?: Reply<KeyScenes>;
  /** Hold the main title's detail this long (deep-link loading state). */
  detailDelayMs?: number;
};

export async function mockGalleryTitle(page: Page, options: GalleryTitleOptions): Promise<string[]> {
  const calls: string[] = [];
  const titles = new Map([options.detail, ...(options.others ?? [])].map((title) => [title.id, title]));
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const reply = <T>(route: Route, value: Reply<T>) => (typeof value === 'number' ? json(route, { detail: 'Unavailable' }, value) : json(route, value));
  await page.route((url) => url.pathname.startsWith('/api/titles/') || url.pathname.startsWith('/api/art/'), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    calls.push(`${request.method()} ${url.pathname}${url.search}`);
    if (url.pathname.startsWith('/api/art/')) {
      return route.fulfill({ status: 200, contentType: 'image/webp', body: WEBP_1X1, headers: { 'Cache-Control': 'public, max-age=31536000, immutable' } });
    }
    const [, id, part] = /^\/api\/titles\/([^/]+)(?:\/(.+))?$/.exec(url.pathname) ?? [];
    if (!part) {
      const title = titles.get(id);
      if (!title) return route.fallback();
      if (id === options.detail.id && options.detailDelayMs) await new Promise((resolve) => setTimeout(resolve, options.detailDelayMs));
      return json(route, title);
    }
    if (part === 'episodes') return json(route, options.episodes?.[Number(url.searchParams.get('season'))] ?? []);
    if (part === 'similar') return json(route, options.similar ?? []);
    if (part === 'episode-summaries') return reply(route, options.summaries ?? { available: false, items: [] });
    if (part === 'key-scenes') return reply(route, options.keyScenes ?? { available: false, scenes: [] });
    if (part === 'recap') return reply(route, options.recap ?? 404);
    return route.fallback(); // images, watched, favorites, identify: lumina-mock's mockTitles
  });
  return calls;
}
