import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { mockLibraryItems } from './library-mock';
import { mockApi, signIn } from './lumina-mock';

/** The vault's working screens (Collections, Downloads, Deleted) over the mocked API, with synthetic names only. */

const AT = '2026-09-28T10:00:00Z';
const json = (body: unknown, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });

function collectionRow(id: string, name: string, patch: Record<string, unknown> = {}) {
  return { id, owner_user_id: 'member-1', name, description: null, visibility: 'shared', revision: 1, item_count: 0, items: [], entries: [], titles: [], created_at: AT, updated_at: AT, ...patch };
}

/** A stateful /api/collections: list, detail and delete. */
async function mockCollections(page: Page) {
  let rows = [collectionRow('c-1', 'Rainy Sundays'), collectionRow('c-2', 'Short films')];
  const deleted: string[] = [];
  await page.route((url) => url.pathname.startsWith('/api/collections'), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/collections' && request.method() === 'GET') return route.fulfill(json(rows));
    const one = /^\/api\/collections\/([^/]+)$/.exec(path);
    const found = one ? rows.find((row) => row.id === one[1]) : undefined;
    if (one && found && request.method() === 'GET') return route.fulfill(json(found));
    if (one && found && request.method() === 'DELETE') { deleted.push(found.id); rows = rows.filter((row) => row.id !== found.id); return route.fulfill({ status: 204, body: '' }); }
    return route.fulfill(json({ detail: 'mock: unhandled' }, 404));
  });
  return { deleted };
}

async function open(page: Page, path: string, options: { deleted?: number; setup?: () => Promise<void> } = {}) {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, { sidebar_collapsed: true });
  await mockGalleryWall(page);
  await mockLibrarySections(page, options.deleted ? { deleted: options.deleted } : {});
  const library = await mockLibraryItems(page, options);
  const collections = await mockCollections(page);
  await options.setup?.(); // routes registered last win, so a spec's own mocks go after mockApi's catch-all
  await page.goto(path);
  await signIn(page);
  await expect(page.locator('.surface.gallery h1, .gallery h1').first()).toBeVisible();
  return { library, collections };
}
const lensRow = (page: Page) => page.getByRole('navigation', { name: 'Library' });

test('Collections: the list, the lens link, the All chapter, a tile and Back', async ({ page }) => {
  await open(page, '/library/collections');
  await expect(page.getByRole('heading', { level: 1, name: 'Collections' })).toBeVisible();
  await expect(lensRow(page).getByRole('link', { name: 'Collections' })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.g-collection-tile')).toHaveCount(2);

  await lensRow(page).getByRole('link', { name: 'All' }).click();
  const chapter = page.getByRole('heading', { level: 2, name: 'Collections', exact: true });
  await expect(chapter).toBeAttached();
  await chapter.scrollIntoViewIfNeeded(); // the chapter loads lazily, one viewport ahead
  await expect(page.locator('.g-collection-tile')).toHaveCount(2);
  await page.getByRole('link', { name: 'See all', exact: true }).click();
  await expect(page).toHaveURL(/\/library\/collections$/);

  await page.locator('.g-collection-tile').first().click();
  await expect(page).toHaveURL(/\/library\/collections\/c-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Rainy Sundays' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/collections$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Collections' })).toBeVisible();
});

test('Collections: ?new=smart opens the builder and leaves the address clean, with no history entry', async ({ page }) => {
  await page.goto('about:blank');
  await open(page, '/library/collections?new=smart');
  await expect(page.getByRole('dialog', { name: 'New smart collection' })).toBeVisible();
  await expect(page).toHaveURL(/\/library\/collections$/);
  await page.keyboard.press('Escape');
  await page.goBack();
  await expect(page).toHaveURL('about:blank');
});

test('Collections: deleting asks first, Esc returns focus to Delete, confirming removes it', async ({ page }) => {
  const { collections } = await open(page, '/library/collections/c-1');
  const remove = page.getByRole('button', { name: 'Delete', exact: true });
  await remove.click();
  const dialog = page.getByRole('dialog', { name: 'Delete “Rainy Sundays”?' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(remove).toBeFocused();
  await remove.click();
  await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect.poll(() => collections.deleted).toEqual(['c-1']);
  await expect(page).toHaveURL(/\/library\/collections$/);
  await expect(page.getByText('Rainy Sundays', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Short films/ })).toBeVisible();
});

async function mockDownloads(page: Page, jobs: unknown[], second: unknown[] = []) {
  const cancelled: string[] = [];
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill(json(route.request().url().includes('cursor=') ? { items: second, next_cursor: null } : { items: jobs, next_cursor: second.length ? 'page-2' : null })));
  await page.route(/\/api\/jobs\/[^/]+\/cancel$/, (route) => { cancelled.push(route.request().url().split('/').at(-2) ?? ''); return route.fulfill(json({ ...(jobs[0] as object), status: 'cancelled' })); });
  await page.route(/\/api\/live-recordings(\?|$)/, (route) => route.fulfill(json({ items: [], next_cursor: null })));
  await page.route(/\/api\/acquisition-batches(\?|$)/, (route) => route.fulfill(json([])));
  return cancelled;
}
const job = (id: string, status: string, title: string) => ({ id, source_url: `https://example.test/${id}`, status, title, progress: status === 'running' ? 30 : undefined, created_at: AT });

test('Downloads: an empty page offers "Add a link", which asks for the palette in link mode', async ({ page }) => {
  await mockDownloads(page, []);
  await page.addInitScript(() => { (window as unknown as { paletteRequests: unknown[] }).paletteRequests = []; window.addEventListener('lumina:palette', (event) => (window as unknown as { paletteRequests: unknown[] }).paletteRequests.push((event as CustomEvent).detail)); });
  await open(page, '/downloads');
  await expect(page.getByText('Nothing is downloading.')).toBeVisible();
  await page.getByLabel('Main content').getByRole('button', { name: 'Add a link' }).click();
  expect(await page.evaluate(() => (window as unknown as { paletteRequests: unknown[] }).paletteRequests)).toEqual([{ mode: 'link' }]);
});

test('Downloads: keyboard only, Cancel on a running row and Show more', async ({ page }) => {
  let cancelled: string[] = [];
  await open(page, '/downloads', { setup: async () => { cancelled = await mockDownloads(page, [job('run-1', 'running', 'Synthetic running download')], [job('done-2', 'completed', 'Synthetic finished download')]); } });
  const cancel = page.getByRole('button', { name: 'Cancel Synthetic running download' });
  await cancel.focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => cancelled).toEqual(['run-1']);
  const more = page.getByRole('button', { name: 'Show more' });
  await more.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Synthetic finished download')).toBeVisible();
});

test('Deleted: Restore is busy then the row leaves; a failing restore shows its reason', async ({ page }) => {
  const { library } = await open(page, '/library/deleted', { deleted: 2 });
  await expect(page.getByRole('heading', { level: 1, name: 'Deleted' })).toBeVisible();
  const rows = page.locator('.g-deleted-rows li');
  await expect(rows).toHaveCount(2);
  await rows.first().getByRole('button', { name: 'Restore' }).click();
  await expect.poll(() => library.calls.filter((call) => call.startsWith('restore-file:'))).toHaveLength(1);
  await expect(page.getByText(/^Restored “/)).toBeVisible();

  await page.route(/\/api\/library\/[^/]+\/restore-file$/, (route) => route.fulfill(json({ detail: 'The file is gone from disk.' }, 409)));
  await rows.last().getByRole('button', { name: 'Restore' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Restore failed' })).toBeVisible();
  await expect(rows.last()).toBeVisible();
});

test('YouTube coexistence: the YouTube channels view keeps its lens, and Collections then Back returns to it', async ({ page }) => {
  await open(page, '/library/youtube?view=channels');
  await expect(lensRow(page).getByRole('link', { name: 'YouTube' })).toHaveAttribute('aria-current', 'page');
  await expect(lensRow(page).getByRole('link', { name: 'Collections' })).not.toHaveAttribute('aria-current', 'page');
  await lensRow(page).getByRole('link', { name: 'Collections' }).click();
  await expect(page).toHaveURL(/\/library\/collections$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/youtube\?view=channels/);
  await expect(lensRow(page).getByRole('link', { name: 'YouTube' })).toHaveAttribute('aria-current', 'page');
});
