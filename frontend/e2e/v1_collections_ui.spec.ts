import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Mixed-source household collections: explicit Watch vs Save to vault, reorder, Play all, tombstoned refs. */

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
  const jobs: unknown[] = [];
  const queued: unknown[] = [];
  const detail = () => ({ ...collection, item_count: collection.entries.filter((entry) => entry.ref.kind === 'library' && entry.availability === 'available').length, items: [], entries: collection.entries });
  const summary = () => ({ ...detail(), items: [], entries: [] });

  await page.route((url) => url.pathname === '/api/jobs' && url.search === '', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON() as { source_url: string };
    jobs.push(body);
    return route.fulfill({
      status: 201, contentType: 'application/json',
      body: JSON.stringify({ id: `job-${jobs.length}`, source_url: body.source_url, status: 'queued', queue_position: 1, format_selection: {}, output_profile: {}, attempts: [], created_at: new Date().toISOString() }),
    });
  });
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
  await page.route((url) => url.pathname.startsWith('/api/me/watch-queue'), async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    queued.push(route.request().postDataJSON());
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ revision: queued.length, limit: 500, entries: [] }) });
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
  return { collection, jobs, queued };
}

async function openLibrary(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark' = 'dark') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const server = await mockCollections(page);
  await page.goto('/library/collections/c1');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();
  return server;
}

test('test_collection_mixed_watch_vs_save', async ({ page }) => {
  await openLibrary(page, { width: 1536, height: 960 });

  // The saved library item watches locally (no preview/job request); tombstoned
  // refs are unwatchable and offer no Save action.
  await page.getByRole('button', { name: `Watch ${item.title}` }).click();
  await expect(page.locator('video').first()).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();

  const goneRow = page.getByRole('button', { name: /no longer available/ });
  await expect(goneRow).toBeDisabled();
  await expect(page.getByRole('button', { name: /Save .* to vault/ })).toHaveCount(1);

  // Watch on the remote entry opens the remote stream (a preview request), never a download.
  await page.getByRole('button', { name: /^Watch A public link/ }).click();
  await expect(page).toHaveURL(new RegExp(`watch\\?url=${encodeURIComponent(REMOTE_URL)}`));
});

test('test_collection_save_to_vault_is_explicit_and_separate_from_watch', async ({ page }) => {
  const server = await openLibrary(page, { width: 1536, height: 960 });
  await page.getByRole('button', { name: /Save A public link.* to vault/ }).click();
  await expect.poll(() => server.jobs).toHaveLength(1);
  expect((server.jobs[0] as { source_url: string }).source_url).toBe(REMOTE_URL);
  // Saving never navigated away from the collection.
  await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible();
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

test('test_collection_play_all_pushes_to_watch_queue', async ({ page }) => {
  const server = await openLibrary(page, { width: 1536, height: 960 });
  await page.getByRole('button', { name: 'Play all' }).click();
  await expect.poll(() => server.queued).toHaveLength(2);
  const refs = server.queued as Array<{ ref: { kind: string; url?: string; library_item_id?: string } }>;
  expect(refs[0].ref).toMatchObject({ kind: 'library', library_item_id: item.id });
  expect(refs[1].ref).toMatchObject({ kind: 'remote', url: REMOTE_URL });
});

test('test_collection_visual_mixed_source', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openLibrary(page, { width: 1536, height: 960 });
  const lastEntry = page.locator('.collection-entry-item').last();
  await expect(lastEntry).toBeVisible();
  await page.locator('.g-collections-page').scrollIntoViewIfNeeded();
  await lastEntry.scrollIntoViewIfNeeded();
  expect(errors).toEqual([]);
});
