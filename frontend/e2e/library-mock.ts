/**
 * Mocked Library pages for the library journeys: /api/library by kind, sort, source and cursor with
 * progress, delete and restore, artwork pixels; category walls answered by lumina-mock's type lists; and title, album
 * and artist details. Register after mockApi (and after mockTitles) so these win.
 */
import type { Page } from '@playwright/test';

import { albumDetail, albumSummary, artistSummary, movieSummary, seriesSummary, stillItem, titleDetail } from '../src/test/galleryFixtures';
import type { LibraryItem } from '../src/types';

/** A 1×1 lossless WebP: what every artwork request gets. */
const PIXEL = Buffer.from('UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==', 'base64');
type Kind = 'video' | 'recording' | 'audio';
export type LibraryItemsMock = { videos?: number; recordings?: number; audio?: number; deleted?: number; failKinds?: Kind[] };

/** `total` items of one kind, newest first: every fourth started, every seventh (from the fourth) watched, every fifth from Twitch. */
export function libraryItems(kind: Kind, total: number): LibraryItem[] {
  const word = kind === 'video' ? 'Video' : kind === 'recording' ? 'Recording' : 'Track';
  return Array.from({ length: total }, (_, index) => stillItem(`${kind}-${String(index).padStart(4, '0')}`, {
    kind, title: `${word} ${index}`, extractor: index % 5 === 4 ? 'twitch' : 'youtube', user_id: 'member-1',
    downloaded_at: new Date(Date.UTC(2026, 8, 28) - index * 3_600_000).toISOString(),
    progress: index % 7 === 3 ? { position_seconds: 761, duration_seconds: 761, completed: true } : index % 4 === 1 ? { position_seconds: 300, duration_seconds: 761, completed: false } : null,
  }));
}

export async function mockLibraryItems(page: Page, options: LibraryItemsMock = {}): Promise<{ requests: URL[]; calls: string[] }> {
  const lists: Record<Kind, LibraryItem[]> = { video: libraryItems('video', options.videos ?? 318), recording: libraryItems('recording', options.recordings ?? 12), audio: libraryItems('audio', options.audio ?? 6) };
  const deleted = libraryItems('video', options.deleted ?? 0).map((entry) => ({ ...entry, id: `deleted-${entry.id}`, status: 'missing' as const, media_state: 'quarantined' as const }));
  const everything = () => [...lists.video, ...lists.recording, ...lists.audio, ...deleted];
  const requests: URL[] = [];
  const calls: string[] = [];
  await page.route((url) => url.pathname === '/api/library' || /^\/api\/library\/[^/]+\/(delete-file|restore-file|artwork)$/.test(url.pathname), async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname.endsWith('/artwork')) return route.fulfill({ status: 200, contentType: 'image/webp', headers: { 'Cache-Control': 'no-store' }, body: PIXEL });
    const action = /^\/api\/library\/([^/]+)\/(delete-file|restore-file)$/.exec(url.pathname);
    if (action) {
      const entry = everything().find((candidate) => candidate.id === action[1]);
      if (!entry) return json({ detail: 'Library item not found' }, 404);
      Object.assign(entry, action[2] === 'delete-file' ? { status: 'missing', media_state: 'quarantined' } : { status: 'available', media_state: 'available' });
      calls.push(`${action[2]}:${entry.id}`);
      return json(entry);
    }
    requests.push(url);
    const query = url.searchParams;
    const kind = query.get('kind') as Kind | null;
    if (kind && options.failKinds?.includes(kind)) return json({ detail: 'boom' }, 500);
    let items = query.get('status') === 'missing' ? everything().filter((entry) => entry.status === 'missing') : kind ? lists[kind] ?? [] : lists.video;
    if (query.get('source')) items = items.filter((entry) => entry.extractor === query.get('source'));
    if (query.get('sort') === 'title') items = [...items].sort((a, b) => a.title.localeCompare(b.title));
    const start = Number(query.get('cursor')?.slice(1) ?? 0);
    const limit = Number(query.get('limit') ?? 60);
    return json({ items: items.slice(start, start + limit), next_cursor: start + limit < items.length ? `c${start + limit}` : null });
  });
  return { requests, calls };
}

/** lumina-mock's mockTitles answers walls by type; a category wall gets the same list (register after mockTitles). */
export async function answerCategoriesByType(page: Page): Promise<void> {
  await page.route((url) => url.pathname === '/api/titles' && url.searchParams.has('category'), (route) => {
    const url = new URL(route.request().url());
    const category = url.searchParams.get('category');
    url.searchParams.delete('category');
    url.searchParams.set('type', category === 'movies' ? 'movie' : 'series');
    return route.fallback({ url: url.href });
  });
}

/** GET /api/titles/{id}: an artist with two albums, an album with three tracks, else a movie or series page. */
export async function mockTitleDetails(page: Page): Promise<void> {
  await page.route((url) => /^\/api\/titles\/[^/]+$/.test(url.pathname) && !['/api/titles/facets', '/api/titles/next-up'].includes(url.pathname), (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()!);
    const detail = id.startsWith('artist-')
      ? titleDetail(artistSummary(id), { children: [albumSummary(`album-of-${id}`), albumSummary(`album-of-${id}-2`, { name: 'Album Two', year: 2021 })] })
      : id.startsWith('album-') ? albumDetail(albumSummary(id))
        : titleDetail(id.includes('series') ? seriesSummary(id) : movieSummary(id));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail) });
  });
}
