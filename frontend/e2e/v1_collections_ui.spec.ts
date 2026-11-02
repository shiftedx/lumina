import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTitles, signIn } from './lumina-mock';
import { movieSummary, titleDetail } from '../src/test/galleryFixtures';
import { mockGalleryTitle } from './gallery-title-mock';

/** Mixed-source household collections: Watch routing and reorder persistence. */

type Entry = { id: string; position: number; availability: 'available' | 'unavailable'; ref: Record<string, string | null>; title: string | null; uploader: string | null; artwork_url: string | null; duration: number | null };

const REMOTE_URL = 'https://example.test/watch/99';

function libraryEntry(id: string, libraryItemId: string, title: string, position: number): Entry {
  return { id, position, availability: 'available', ref: { kind: 'library', library_item_id: libraryItemId, provider: null, remote_id: null, url: null }, title, uploader: null, artwork_url: null, duration: item.duration };
}
function remoteEntry(id: string, position: number): Entry {
  return { id, position, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: 'yt-99', url: REMOTE_URL }, title: 'A public link kept for later, never downloaded until asked', uploader: 'External channel', artwork_url: null, duration: 240 };
}
function tombstone(id: string, position: number): Entry {
  return { id, position, availability: 'unavailable', ref: { kind: 'library', library_item_id: null, provider: null, remote_id: null, url: null }, title: null, uploader: null, artwork_url: null, duration: null };
}

/** A tiny stateful stand-in for /api/collections that survives reorders within one test. */
async function mockCollections(page: Page) {
  const collection = {
    id: 'c1', owner_user_id: 'member-1', name: 'Weekend watchlist', description: null, visibility: 'private', revision: 1,
    entries: [libraryEntry('e-lib', item.id, item.title, 0), remoteEntry('e-remote', 1), tombstone('e-gone', 2)],
  };
  const detail = () => ({ ...collection, item_count: collection.entries.filter((entry) => entry.ref.kind === 'library' && entry.availability === 'available').length, items: [], entries: collection.entries });
  const summary = () => ({ ...detail(), items: [], entries: [] });
  await page.route('**/api/preview', async (route) => {
    const body = route.request().postDataJSON() as { source_url: string };
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        kind: 'video', title: 'A public link kept for later, never downloaded until asked', webpage_url: body.source_url, uploader: 'External channel',
        availability: 'public', capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
        artwork_url: null, chapters: [], description_timestamps: [], entries: [], raw: { id: 'yt-99', title: 'A public link kept for later, never downloaded until asked', uploader: 'External channel', duration: 240 },
      }),
    });
  });
  await page.route((url) => url.pathname.startsWith('/api/collections'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/collections' && request.method() === 'GET') return json([summary()]);
    if (path === `/api/collections/${collection.id}` && request.method() === 'GET') return json(detail());
    const move = /^\/api\/collections\/([^/]+)\/entries\/([^/]+)$/.exec(path);
    if (move && request.method() === 'PATCH') {
      const { position, expected_revision } = request.postDataJSON() as { position: number; expected_revision: number };
      if (expected_revision !== collection.revision) return json({ detail: 'conflict' }, 409);
      const entry = collection.entries.find((candidate) => candidate.id === move[2]) as Entry;
      collection.entries = collection.entries.filter((candidate) => candidate !== entry);
      collection.entries.splice(position, 0, entry);
      collection.entries.forEach((candidate, index) => { candidate.position = index; });
      collection.revision += 1;
      return json(detail());
    }
    if (move && request.method() === 'DELETE') {
      collection.entries = collection.entries.filter((candidate) => candidate.id !== move[2]);
      collection.revision += 1;
      return json(detail());
    }
    return json({ detail: `mock: unhandled ${request.method()} ${path}` }, 404);
  });
}

async function openLibrary(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark' = 'dark') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockCollections(page);
  await page.goto('/library/collections/c1');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();
}

test('test_collection_watch_routing', async ({ page }) => {
  await openLibrary(page, { width: 1536, height: 960 });

  // A saved library item opens its local player; a remote entry opens the
  // remote stream route with its source URL.
  await page.getByRole('button', { name: `Watch ${item.title}` }).click();
  await expect(page.locator('video').first()).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();
  await page.getByRole('button', { name: /^Watch A public link/ }).click();
  await expect(page).toHaveURL(new RegExp(`watch\\?url=${encodeURIComponent(REMOTE_URL)}`));
});

test('test_collection_reorder_persists_across_reload', async ({ page }) => {
  await openLibrary(page, { width: 1536, height: 960 });
  const titles = () => page.locator('.collection-entry-item strong').allTextContents();
  await expect.poll(titles).toEqual([item.title, 'A public link kept for later, never downloaded until asked', 'No longer available']);

  await page.getByRole('button', { name: `Move ${item.title} down` }).click();
  await expect.poll(titles).toEqual(['A public link kept for later, never downloaded until asked', item.title, 'No longer available']);

  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();
  await expect.poll(titles).toEqual(['A public link kept for later, never downloaded until asked', item.title, 'No longer available']);
});

for (const width of [1440, 390]) {
  test(`franchises coexist with manual and smart collections and open their movies at ${width}px`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: width === 390 ? 'light' : 'dark' });
    await mockApi(page, {});
    await mockTitles(page);
    await mockCollections(page);
    const movie = movieSummary();
    const franchise = movieSummary('franchise-1', { type: 'boxset', name: 'Lantern Collection', play_item_id: null, child_count: 1 });
    const household = (id: string, name: string, smart = false) => ({
      id, name, owner_user_id: 'member-1', visibility: 'private', revision: 1, item_count: 1,
      items: [], entries: [], titles: [movie],
      rules: smart ? { type: 'movie', match: 'all', conditions: [{ field: 'year', op: 'gte', value: 2000 }], limit: 100 } : null,
    });
    await page.route((url) => url.pathname === '/api/collections', (route) => route.fulfill({ json: [
      household('c1', 'Weekend watchlist'), household('smart-1', 'Recent adventures', true),
    ] }));
    await page.route((url) => url.pathname === '/api/titles' && url.searchParams.get('type') === 'boxset', (route) => route.fulfill({
      json: { items: [franchise], next_cursor: null, total: 1, start_index: 0, letters: [] },
    }));
    await mockGalleryTitle(page, { detail: titleDetail(franchise, { children: [movie] }), others: [titleDetail(movie)] });
    await page.goto('/library/collections');
    await signIn(page);
    await expect(page.getByRole('button', { name: /^Lantern Collection/ })).toBeVisible();
    for (const name of ['Weekend watchlist', 'Recent adventures']) {
      await expect(page.getByRole('link', { name: new RegExp(name) })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'New collection', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `../output/playwright/collections/combined-${width}.png`, fullPage: true });
    await page.getByRole('button', { name: /^Lantern Collection/ }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Lantern Collection' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'In this collection' })).toBeVisible();
    await page.getByRole('button', { name: /^Northern Lantern/ }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
    // The production symptom: franchise groups exist, but no household collections do.
    await page.route((url) => url.pathname === '/api/collections', (route) => route.fulfill({ json: [] }));
    await page.goto('/library/collections');
    await expect(page.getByRole('button', { name: /^Lantern Collection/ })).toBeVisible();
    await expect(page.getByText('No collections yet.', { exact: true })).toHaveCount(0);
    await page.screenshot({ path: `../output/playwright/collections/franchises-only-${width}.png`, fullPage: true });
    await page.route((url) => url.pathname === '/api/collections', (route) => route.fulfill({ status: 503, json: { detail: 'Unavailable' } }));
    await page.goto('/library/collections');
    await expect(page.getByText('Lumina could not load your collections.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Lantern Collection/ })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
