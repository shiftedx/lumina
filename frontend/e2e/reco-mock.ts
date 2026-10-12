/**
 * Mocked recommendation API: Picked for you, Up Next, More like this, the event and
 * history endpoints, and suppressions as a stateful list. Register after mockApi, mockHome and the mocks so these
 * routes win.
 */
import type { Page } from '@playwright/test';

import { pickedSnapshot, recoEntry, suppressionList } from '../src/test/recoFixtures';
import { remoteEntry } from '../src/test/remoteFixtures';
import type { MemberRecommendationSnapshot, PopularSnapshot, PreviewResponse, RecoEventBatch, RecoEventIn, SuppressRecommendationInput, Suppression, SuppressionList, TitleSummary, UpNextRequest } from '../src/types';

export type RecoMock = {
  home?: MemberRecommendationSnapshot;
  upNext?: MemberRecommendationSnapshot;
  similar?: TitleSummary[];
  suppressions?: Partial<SuppressionList>;
  /** Exact pathnames answered 500. */
  fail?: string[];
};

/** What the page sent, in order. `beacons` counts batches that carried the body token (sendBeacon on pagehide). */
export type RecoLog = {
  events: RecoEventIn[];
  beacons: number;
  suppressed: SuppressRecommendationInput[];
  restored: string[];
  cleared: number;
  upNext: UpNextRequest[];
};

const SCOPE_LIST: Record<Suppression['scope'], 'items' | 'channels' | 'fewer' | 'titles'> = { item: 'items', channel: 'channels', fewer: 'fewer', title: 'titles' };

export const RECO_PATH = (path: string) => path === '/api/discovery/home' || path === '/api/discovery/up-next' || path === '/api/reco/events'
  || path === '/api/reco/history' || path === '/api/discovery/suppressions' || path.startsWith('/api/discovery/suppressions/')
  || /^\/api\/titles\/[^/]+\/similar$/.test(path);

export async function mockReco(page: Page, options: RecoMock = {}): Promise<RecoLog> {
  const log: RecoLog = { events: [], beacons: 0, suppressed: [], restored: [], cleared: 0, upNext: [] };
  const lists: SuppressionList = suppressionList(options.suppressions);
  let next = 1;
  await page.route((url) => RECO_PATH(url.pathname), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (options.fail?.includes(path)) return json({ detail: 'mock failure' }, 500);
    if (path === '/api/discovery/home') return json(options.home ?? pickedSnapshot());
    if (path === '/api/discovery/up-next') {
      log.upNext.push(request.postDataJSON() as UpNextRequest);
      return json(options.upNext ?? pickedSnapshot(12));
    }
    if (path.endsWith('/similar')) return json(options.similar ?? []);
    if (path === '/api/reco/events') {
      const batch = JSON.parse(request.postData() ?? '{"events":[]}') as RecoEventBatch;
      if ('csrf' in batch) log.beacons += 1;
      log.events.push(...batch.events);
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/reco/history' && method === 'DELETE') {
      log.cleared += 1;
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/discovery/suppressions' && method === 'GET') return json(lists);
    if (path === '/api/discovery/suppressions' && method === 'POST') {
      const input = request.postDataJSON() as SuppressRecommendationInput;
      log.suppressed.push(input);
      const created: Suppression = {
        id: `mock-suppression-${next++}`, scope: input.scope, target_key: input.title_id ?? input.source_id ?? input.uploader ?? 'mock',
        title: input.title ?? null, channel_name: input.uploader ?? null, source: input.source ?? null,
        created_at: '2026-09-30T10:00:00Z', recovers_at: input.scope === 'fewer' ? '2027-02-17T10:00:00Z' : null,
      };
      lists[SCOPE_LIST[input.scope]] = [...(lists[SCOPE_LIST[input.scope]] ?? []), created];
      return json(created);
    }
    const restore = /^\/api\/discovery\/suppressions\/([^/]+)$/.exec(path);
    if (restore && method === 'DELETE') {
      const id = decodeURIComponent(restore[1]);
      log.restored.push(id);
      for (const key of Object.values(SCOPE_LIST)) lists[key] = (lists[key] ?? []).filter((entry) => entry.id !== id);
      return route.fulfill({ status: 204 });
    }
    return route.fallback();
  });
  return log;
}

// ---- Appended by R5 ----------------------------------------------------------------

/** Explore's snapshot with a For you rail and a category order. Category items carry no `reco`. */
export function popularWithForYou(patch: Partial<PopularSnapshot> = {}): PopularSnapshot {
  return {
    items: [
      remoteEntry('g1', { title: 'Gaming video one', category_keys: ['gaming'] }),
      remoteEntry('m1', { title: 'Music video one', category_keys: ['music'] }),
    ],
    categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }, { key: 'music', label: 'Music', state: 'ready' }],
    state: 'ready', refreshing: false, stale: false,
    for_you: Array.from({ length: 3 }, (_, index) => recoEntry(`f${index + 1}`, index, { title: `Picked for you ${index + 1}`, uploader: `Channel ${index + 1}` })),
    category_order: ['music', 'gaming'],
    ...patch,
  };
}

/** GET /api/discovery/popular. Register after `mockApi`, whose empty answer it replaces. */
export async function mockPopular(page: Page, snapshot: PopularSnapshot = popularWithForYou()): Promise<void> {
  await page.route((url) => url.pathname === '/api/discovery/popular', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) }));
}

/** POST /api/preview for a YouTube watch address that cannot play, so the watch page shows Up next without a stream. */
export async function mockPreview(page: Page, patch: Partial<PreviewResponse> = {}): Promise<void> {
  const channel = 'UCabcdefghijklmnopqrstuv';
  const preview: PreviewResponse = {
    kind: 'video', title: 'Harbor walk at dawn', webpage_url: 'https://www.youtube.com/watch?v=lumina00001', extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video', entries: [],
    capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: false, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
    raw: { id: 'lumina00001', uploader: 'Harbor Films', channel_id: channel, channel_url: `https://www.youtube.com/channel/${channel}`, extractor: 'youtube', duration: 600 },
    ...patch,
  } as PreviewResponse;
  await page.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(preview) }));
}
