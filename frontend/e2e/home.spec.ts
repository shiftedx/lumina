import { expect, test, type Page } from '@playwright/test';

import { albumSummary, episodeSummary, movieSummary } from '../src/test/galleryFixtures';
import { continueEntry, homeMovie, type HomeMock, mockHome } from './home-mock';
import { item, mockApi, signIn } from './lumina-mock';

/** The gallery Home over the mocked API (synthetic media). */

async function openHome(page: Page, options: HomeMock = {}, viewport = { width: 1440, height: 900 }) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  const requests = await mockHome(page, options);
  await page.goto('/');
  await signIn(page);
  return requests;
}
const sections = (page: Page) => page.locator('[data-shelf-section]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-shelf-section')));
const videoTime = (page: Page) => page.locator('video').evaluate((video: HTMLVideoElement) => video.currentTime);

test('default order with the hero from Continue; Resume opens the player at the saved point', async ({ page }) => {
  const lantern = movieSummary('movie-home', { name: 'Northern Lantern', play_item_id: item.id });
  // The synthetic fixture clip is 1.5s: a saved position within that range so the real seek is observable.
  await openHome(page, {
    continueWatching: [continueEntry(lantern, { item_id: item.id, position_seconds: 0.9, duration_seconds: 1.5 })],
    nextUp: [episodeSummary(2, 5)], newest: [homeMovie(1), homeMovie(2)],
  });
  await page.route(`**/api/library/${item.id}/playback`, (route) => route.fulfill({ json: { item_id: item.id, position_seconds: 0.9, duration_seconds: 1.5, completed: false } }));
  await expect(page.locator('.h-hero-kicker')).toHaveText('Continue watching');
  await expect(page.locator('.h-hero-title')).toHaveText('Northern Lantern');
  // The three near shelves are ready; the rest of the catalogue still mounts its placeholder, deferred until scrolled near.
  await expect.poll(() => sections(page)).toEqual(['continue', 'next_up', 'new_in_library', 'because_you_watched', 'recommended', 'collections', 'recently_saved', 'new_anime', 'recent_music']);
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));
  await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(0.8);
});

test('no Continue title: the hero is the newest; Play plays it and Details opens its title page', async ({ page }) => {
  const newest = homeMovie(1, { name: 'Fresh Arrival', play_item_id: item.id });
  await openHome(page, { newest: [newest, homeMovie(2)] });
  await expect(page.locator('.h-hero-kicker')).toHaveText('New in your library');
  await page.getByRole('button', { name: 'Details' }).click();
  await expect(page).toHaveURL(/\/title\/home-movie-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Fresh Arrival' })).toBeVisible();
  await page.goBack();
  await page.getByRole('button', { name: 'Play' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));
});

test('only near shelves are requested until the member scrolls', async ({ page }) => {
  const many = Array.from({ length: 12 }, (_, index) => homeMovie(index));
  const requests = await openHome(page, {
    continueWatching: [continueEntry(episodeSummary(2, 4))], nextUp: [episodeSummary(2, 5), episodeSummary(2, 6)], newest: many,
    rows: [{ id: 'b1', kind: 'because_you_watched', title: 'Because you watched Harbor Lights', items: many }, { id: 'r', kind: 'recommended', title: 'Recommended', items: many }],
    anime: [movieSummary('anime-1', { name: 'Spirited' })], albums: [albumSummary('album-1')],
  });
  await expect(page.getByRole('region', { name: 'New in your library' })).toBeVisible();
  await page.waitForTimeout(500);
  const far = (log: string[]) => log.filter((request) => request.includes('category=anime') || request.includes('type=album'));
  expect(far(requests)).toEqual([]);
  // Scroll in steps so the intersection observer fires for each shelf passed on the way down, not just the final rest position.
  for (let step = 0; step < 10; step += 1) {
    await page.mouse.wheel(0, 900);
    await page.waitForTimeout(100);
  }
  await expect.poll(() => far(requests).length).toBe(2);
  await expect(page.getByRole('region', { name: 'Recently added music' })).toBeVisible();
  // Because you watched and Recommended share one title-rows request per visit.
  expect(requests.filter((request) => request.startsWith('GET /api/home/title-rows'))).toHaveLength(1);
});

test('a return from a title page paints shelves from the session cache before the refetch resolves', async ({ page }) => {
  await openHome(page, { continueWatching: [continueEntry(episodeSummary(2, 4))], nextUp: [episodeSummary(2, 5)], newest: [homeMovie(1)] });
  await expect(page.getByRole('region', { name: 'Next up' })).toBeVisible();
  // E-I5: a tight custom timeout (1 s, against a 3 s delayed refetch) flakes under load for reasons unrelated to the
  // cache. Prove the cache directly instead: the refetch has not resolved by the time the cached content is visible.
  let refetched = false;
  await page.route('**/api/titles/next-up**', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3_000));
    refetched = true;
    await route.fallback();
  });
  await page.getByRole('region', { name: 'New in your library' }).getByRole('button', { name: /^(?!More options)/ }).last().click();
  await expect(page).toHaveURL(/\/title\//);
  await page.goBack();
  await expect(page.getByRole('region', { name: 'Next up' }).getByRole('button', { name: 'Harbor Lights, S2 · E5' })).toBeVisible();
  expect(refetched).toBe(false);
});

test('a fresh member sees the start card and no Edit home', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockHome(page);
  // Registered after mockApi so it wins: an empty library.
  await page.route((url) => url.pathname === '/api/library', (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.goto('/');
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Make Home yours' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit home' })).toHaveCount(0);
  await expect(page.locator('[data-shelf-section]')).toHaveCount(0);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }, { width: 1920, height: 1080 }]) {
    test(`screenshots for the owner, ${scheme} ${viewport.width}: no sideways scroll, no page errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await openHome(page, {
        continueWatching: [continueEntry(episodeSummary(2, 4))], nextUp: [episodeSummary(2, 5)], newest: Array.from({ length: 8 }, (_, index) => homeMovie(index)),
        albums: [albumSummary('album-1')],
      }, viewport);
      await expect(page.locator('.h-hero')).toBeVisible();
      await page.screenshot({ path: `../output/playwright/home/home-${scheme}-${viewport.width}.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect(errors).toEqual([]);
    });
  }
}
