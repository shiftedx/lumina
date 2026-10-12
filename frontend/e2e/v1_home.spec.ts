import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn, user } from './lumina-mock';

/** Home as a calm personal landing page (mocked API, synthetic media). */

const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });

async function seedContent(page: Page) {
  const progress = { id: 'pb-1', user_id: user.id, item_id: item.id, position_seconds: 40, duration_seconds: 90, completed: false, last_watched_at: '2026-01-02T00:00:00Z', created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z', item };
  await page.route((url) => url.pathname === '/api/playback/continue', (route) => route.fulfill(json([progress])));
  await page.route((url) => url.pathname === '/api/me/watch-queue', (route) => route.fulfill(json({ revision: 1, limit: 500, entries: [
    { id: 'q1', position: 0, availability: 'available', ref: { kind: 'remote', library_item_id: null, provider: 'youtube', remote_id: 'r1', url: 'https://example.test/queued/1' }, title: 'Evening walk through the old harbour', uploader: 'Queue fixture channel', artwork_url: null, duration: 245 },
  ] })));
  await page.route((url) => url.pathname === '/api/automations', (route) => route.fulfill(json([{
    id: 'follow-1', user_id: user.id, label: 'Fixture channel', source_url: 'https://www.youtube.com/@fixture', source_type: 'channel', artwork_url: null, active: true, auto_download: false,
    last_checked_at: '2026-01-01T12:00:00', next_check_at: null, last_error: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    feed_entries: [{ id: 'f1', title: 'The only new upload this week, with a title long enough to wrap onto a second line', uploader: 'Fixture channel', duration: 610, webpage_url: 'https://www.youtube.com/watch?v=f1', capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } } }],
  }])));
}

async function openHome(page: Page, fresh: boolean, viewport = { width: 1536, height: 960 }) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  if (fresh) await page.route((url) => url.pathname === '/api/library', (route) => route.fulfill(json({ items: [], next_cursor: null })));
  else await seedContent(page);
  await page.goto('/');
  await signIn(page);
}

test('test_home_priority_and_empty: seeded shelves in order; a lone card keeps card width', async ({ page }) => {
  await openHome(page, false);
  await expect(page.getByRole('region', { name: 'New from your follows' })).toBeVisible();
  await expect.poll(() => page.locator('[data-shelf-section]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-shelf-section'))))
    // Picked for you shows on any signal since 1.9.0, not only after interests are chosen.
    .toEqual(['continue', 'watchlist', 'picked_for_you', 'from_follows', 'recently_saved']);
  const follows = page.getByRole('region', { name: 'New from your follows' });
  const [card, shelf] = await Promise.all([follows.locator('.h-card').first().boundingBox(), follows.boundingBox()]);
  expect(card!.width).toBeLessThan(shelf!.width / 3);
});

test('a fresh member sees one start card and no empty shelves', async ({ page }) => {
  await openHome(page, true);
  await expect(page.getByRole('heading', { name: 'Make Home yours' })).toBeVisible();
  await expect(page.locator('[data-shelf-section]')).toHaveCount(0);
});

// Heading-below-topbar is a layout check that genuinely differs between desktop and phone.
for (const viewport of [{ width: 1536, height: 960 }, { width: 390, height: 844 }]) {
  for (const fresh of [false, true]) {
    test(`test_home_two_themes_mobile ${fresh ? 'empty' : 'content'} ${viewport.width}`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await openHome(page, fresh, viewport);
      await expect(page.getByRole(fresh ? 'heading' : 'region', { name: fresh ? 'Make Home yours' : 'Recently saved' })).toBeVisible();
      const heading = await page.locator('.surface h1').boundingBox();
      const topbar = await page.locator('.g-topbar').boundingBox();
      expect(heading!.y).toBeGreaterThanOrEqual(topbar!.y + topbar!.height);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(errors).toEqual([]);
    });
  }
}
