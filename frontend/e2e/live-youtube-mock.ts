/**
 * Mocked API answers for the live and YouTube browser specs. Every artwork URL is Lumina's own
 * proxy path: no mock ever puts a provider URL on the page. Tracks may append exports at the end only.
 */
import type { Page, Route } from '@playwright/test';

export const CHANNEL_ID = 'UCabcdefghijklmnopqrstuv';
const art = (id: string) => `/api/artwork/remote/${id}`;
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

export const caps = (provider: string, lifecycle: string, extra: Record<string, unknown> = {}) => ({
  provider, lifecycle, can_play: lifecycle !== 'upcoming', can_acquire: lifecycle === 'vod', chat: { live: 'unavailable', replay: 'unavailable' }, ...extra,
});

/** `n` live YouTube entries, most watched first, alternating Gaming and Music. */
export function liveItems(n: number, patch: Record<string, unknown> = {}, prefix = 'live') {
  return Array.from({ length: n }, (_, index) => {
    const id = `${prefix}-${String(index).padStart(3, '0')}`;
    return {
      id, title: `Live stream ${index + 1}`, uploader: `Creator ${index + 1}`, uploader_id: CHANNEL_ID, source: 'youtube', source_label: 'YouTube', kind: 'live',
      webpage_url: `https://www.youtube.com/watch?v=${id}`, artwork_url: art(id), view_count: 50_000 - index * 400,
      category_keys: [index % 2 ? 'music' : 'gaming'], capabilities: caps('youtube', 'live', { can_record: true }), ...patch,
    };
  });
}

export function liveSnapshotBody(patch: Record<string, unknown> = {}) {
  return {
    items: liveItems(24), hero: [],
    categories: [{ key: 'gaming', label: 'Gaming', state: 'ready', live_count: 1240 }, { key: 'music', label: 'Music', state: 'ready', live_count: 86 }],
    live_total: 12_400,
    state: 'ready', refreshing: false, stale: false, twitch_available: true, followed_unavailable: [],
    refreshed_at: '2026-09-29T20:42:00Z', last_success_at: '2026-09-29T20:42:00Z', ...patch,
  };
}

export function channelPageBody(id = CHANNEL_ID, tab = 'videos', patch: Record<string, unknown> = {}, limit = 60) {
  const entries = Array.from({ length: limit }, (_, index) => {
    const entryId = `${tab}-${String(index).padStart(3, '0')}`;
    return {
      id: entryId, title: `Harbor film ${index + 1}`, uploader: 'Harbor Films', uploader_id: id, source: 'youtube', source_label: 'YouTube',
      kind: tab === 'shorts' ? 'short' : tab === 'playlists' ? 'playlist' : 'video', duration: 600 + index, view_count: 1_000 * (index + 1),
      webpage_url: tab === 'playlists' ? `https://www.youtube.com/playlist?list=PL${entryId}` : `https://www.youtube.com/watch?v=${entryId}`,
      artwork_url: art(entryId), capabilities: caps('youtube', 'vod'),
    };
  });
  return {
    channel: {
      id, name: 'Harbor Films', handle: '@harborfilms', url: `https://www.youtube.com/channel/${id}`, avatar_url: art('avatar'), banner_url: art('banner'),
      follower_count: 1_200_000, video_count: 845, description: 'Films about harbors and the people who keep them.', verified: true,
      tabs: ['videos', 'streams', 'shorts', 'playlists'], follow_id: null, live: null,
    },
    tab, entries, has_more: limit < 120, restricted: false, fetched_at: '2026-09-29T20:42:00Z', stale: false, ...patch,
  };
}

export function libraryChannelsBody(n: number) {
  return Array.from({ length: n }, (_, index) => ({
    key: `ch-${index}`, extractor: 'youtube', name: `Channel ${String.fromCharCode(65 + (index % 26))}${index}`, uploader: `Channel ${String.fromCharCode(65 + (index % 26))}${index}`,
    count: 24 - (index % 20), unwatched_count: index % 4, newest_item_id: 'item-1', newest_at: new Date(Date.UTC(2026, 8, 29 - index)).toISOString(),
    channel_id: index % 3 ? CHANNEL_ID : null, avatar_url: index % 2 ? art(`avatar-${index}`) : null,
  }));
}

export async function mockRemoteArtwork(page: Page) {
  await page.route('**/api/artwork/remote/**', (route) => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#3d4a3f"/></svg>',
  }));
}

/** /api/discovery/live and the recordings list; `set` changes the next answer (refresh tests). */
export async function mockLive(page: Page, snapshot: unknown = liveSnapshotBody()) {
  let next = snapshot;
  const requests: string[] = [];
  await page.route('**/api/discovery/live', (route) => { requests.push(route.request().url()); return json(route, next); });
  await page.route(/\/api\/live-recordings(\?|$)/, (route) => (route.request().method() === 'GET' ? json(route, { items: [], next_cursor: null }) : route.fallback()));
  await mockWalls(page);
  return { set: (body: unknown) => { next = body; }, requests };
}

/** 2.4.0 See all walls: /api/discovery/{live,popular}/{category} in pages of 40 (cursor p2 is the last, 20 more). */
export async function mockWalls(page: Page, requests: string[] = []) {
  await page.route(/\/api\/discovery\/(live|popular)\/[^/?]+/, (route) => {
    const url = new URL(route.request().url());
    const [, , , kind, category] = url.pathname.split('/');
    requests.push(`${kind}/${category}?${url.searchParams.get('cursor') ?? ''}`);
    const second = url.searchParams.get('cursor') === 'p2';
    const items = liveItems(second ? 20 : 40, { category_keys: [category] }, `wall-${category}${second ? '-b' : ''}`);
    return json(route, { items, next_cursor: second ? null : 'p2', live_count: 1240 });
  });
  return requests;
}

export type ChannelMockOptions = { delayMs?: number; status?: number; patch?: Record<string, unknown> };

/** /api/channels/youtube/* by tab and limit, and /api/channels/resolve; returns the request log. */
export async function mockChannelPage(page: Page, options: ChannelMockOptions = {}) {
  const requests: Array<{ id: string; tab: string; limit: string }> = [];
  await page.route(/\/api\/channels\/youtube\/[^/?]+(\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split('/').pop() as string;
    const tab = url.searchParams.get('tab') || 'videos';
    const limit = url.searchParams.get('limit') || '60';
    requests.push({ id, tab, limit });
    if (options.delayMs) await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    if (options.status && options.status !== 200) return json(route, { detail: options.status === 404 ? 'channel_unavailable' : 'YouTube did not respond in time.' }, options.status);
    return json(route, channelPageBody(id, tab, options.patch, Number(limit)));
  });
  await page.route('**/api/channels/resolve', (route) => json(route, { provider: 'youtube', channel_id: CHANNEL_ID }));
  return requests;
}

export async function mockLibraryChannels(page: Page, n = 12) {
  await page.route(/\/api\/library\/channels(\?|$)/, (route) => json(route, libraryChannelsBody(n)));
}
