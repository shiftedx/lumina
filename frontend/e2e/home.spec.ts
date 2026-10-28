import { readFileSync } from 'node:fs';

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
const ARTWORK_PNG = readFileSync(new URL('./fixtures/books-page.png', import.meta.url));

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
  // Empty optional rows disappear as they approach the viewport; the populated shelves retain their default order.
  const populated = ['continue', 'next_up', 'new_in_library', 'recently_saved', 'new_anime', 'recent_music'];
  await expect.poll(async () => (await sections(page)).filter((id) => id !== null && populated.includes(id))).toEqual(populated);
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));
  await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(0.8);
});

test('no Continue title: the hero is the newest; Play plays it and Details opens its title page', async ({ page }) => {
  const newest = homeMovie(1, { name: 'Fresh Arrival', play_item_id: item.id });
  await openHome(page, { newest: [newest, homeMovie(2)] });
  await expect(page.locator('.h-hero-kicker')).toHaveText('New in your library');
  await page.getByRole('button', { name: 'Details' }).click();
  await expect(page).toHaveURL(/\/video\/title\/home-movie-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Fresh Arrival' })).toBeVisible();
  await page.goBack();
  await page.getByRole('button', { name: 'Play' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${item.id}$`));
});

test('an artless Home hero keeps Resume in view without reserving artwork space', async ({ page }) => {
  const artful = homeMovie(1, { play_item_id: item.id });
  await openHome(page, { continueWatching: [continueEntry(artful)] });
  const artworkHeight = (await page.locator('.h-slide.is-backdrop').boundingBox())!.height;

  const artless = homeMovie(2, { backdrop: null, poster: null, play_item_id: item.id });
  await openHome(page, { continueWatching: [continueEntry(artless)] });
  await expect(page.locator('.h-slide.is-field')).toBeVisible();
  expect((await page.locator('.h-slide.is-field').boundingBox())!.height).toBeLessThan(artworkHeight);
  const resume = page.getByRole('button', { name: 'Resume', exact: true });
  await expect(resume).toBeInViewport({ ratio: 1 });
  await resume.click();
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

test('rapid horizontal scrolling keeps delayed artwork bounded and fills the focused window', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockHome(page, { newest: Array.from({ length: 18 }, (_, index) => homeMovie(index)) });

  let active = 0;
  let maxActive = 0;
  let cancelled = 0;
  let completed = 0;
  let issued = 0;
  const requested = new Set<string>();
  const artwork = (url: string) => {
    const path = new URL(url).pathname;
    return path.startsWith('/api/art/') && path !== '/api/art/framing';
  };
  page.on('request', (request) => {
    if (!artwork(request.url())) return;
    requested.add(new URL(request.url()).pathname);
    issued += 1;
    active += 1;
    maxActive = Math.max(maxActive, active);
  });
  const finished = (request: import('@playwright/test').Request) => { if (artwork(request.url())) { active -= 1; completed += 1; } };
  page.on('requestfinished', finished);
  page.on('requestfailed', (request) => { if (artwork(request.url())) { active -= 1; cancelled += 1; } });
  await page.route('**/api/art/**', async (route) => {
    if (new URL(route.request().url()).pathname === '/api/art/framing') return route.fallback();
    if (!route.request().url().includes('/fixture/')) await new Promise((resolve) => setTimeout(resolve, 2_500));
    await route.fulfill({ status: 200, contentType: 'image/png', body: ARTWORK_PNG }).catch(() => {});
  });
  await page.setContent('<img id="fixture-route" alt="" sizes="168px" src="http://127.0.0.1:4187/api/art/fixture/Primary/pixel-240.webp" srcset="http://127.0.0.1:4187/api/art/fixture/Primary/pixel-240.webp 240w, http://127.0.0.1:4187/api/art/fixture/Primary/pixel-480.webp 480w">');
  expect(await page.locator('#fixture-route').evaluate(async (element: HTMLImageElement) => {
    await element.decode();
    return element.naturalWidth;
  })).toBeGreaterThan(0);
  active = 0;
  maxActive = 0;
  cancelled = 0;
  completed = 0;
  issued = 0;
  requested.clear();
  await page.addInitScript(() => {
    const events = { errors: 0, loads: 0 };
    (window as Window & { __homeImageEvents?: typeof events }).__homeImageEvents = events;
    document.addEventListener('error', (event) => { if ((event.target as Element | null)?.classList?.contains('g-art-image')) events.errors += 1; }, true);
    document.addEventListener('load', (event) => { if ((event.target as Element | null)?.classList?.contains('g-art-image')) events.loads += 1; }, true);
  });

  const shelf = page.getByRole('region', { name: 'New in your library' });
  const row = shelf.locator('.h-row');
  const last = shelf.getByRole('button', { name: /Home Movie 17/ }).first();
  await page.goto('/');
  await signIn(page);
  await expect(row).toBeVisible();
  await expect.poll(() => active).toBeGreaterThan(0);

  await row.evaluate(async (element) => {
    for (const fraction of [0.35, 0.7, 1]) {
      element.scrollLeft = (element.scrollWidth - element.clientWidth) * fraction;
      element.dispatchEvent(new Event('scroll'));
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    }
  });
  await last.focus();
  await expect(last).toBeFocused();
  expect(active).toBeGreaterThan(0);
  expect(completed).toBe(0); // focus moved while every admitted image response was still delayed

  const pipelineState = async () => ({ active, cancelled, completed, issued, maxActive, browser: await row.evaluate((element) => {
    const cards = [...element.querySelectorAll(':scope > .h-card')];
    const first = cards[0] as HTMLElement | undefined;
    return {
      cardsWithSharp: cards.flatMap((card, index) => card.querySelector('img.g-art-image') ? [index] : []),
      sharp: cards.flatMap((card, index) => {
        const image = card.querySelector('img.g-art-image') as HTMLImageElement | null;
        return image ? [{ index, complete: image.complete, currentSrc: image.currentSrc, naturalWidth: image.naturalWidth, shown: image.classList.contains('is-shown'), src: image.getAttribute('src') }] : [];
      }),
      clientWidth: element.clientWidth,
      events: (window as Window & { __homeImageEvents?: { errors: number; loads: number } }).__homeImageEvents,
      lastHasSharp: Boolean(cards.at(-1)?.querySelector('img.g-art-image')),
      loader: (window as Window & { __luminaImageLoader?: { stats: () => unknown } }).__luminaImageLoader?.stats(),
      pitch: first ? first.offsetWidth + (parseFloat(getComputedStyle(element).columnGap) || 0) : 0,
      scrollLeft: element.scrollLeft,
      scrollWidth: element.scrollWidth,
      shown: element.querySelectorAll('img.g-art-image.is-shown').length,
    };
  }) });
  try {
    await expect.poll(() => [...requested].some((path) => path.includes('/home-movie-17/Primary/')), { timeout: 7_500 }).toBe(true);
  } catch (error) {
    throw new Error(`focused artwork was never admitted: ${JSON.stringify(await pipelineState())}`, { cause: error });
  }

  const currentWindowDecoded = () => row.evaluate((element) => {
    const cards = [...element.querySelectorAll(':scope > .h-card')];
    const first = cards[0] as HTMLElement | undefined;
    const pitch = first ? first.offsetWidth + (parseFloat(getComputedStyle(element).columnGap) || 0) : 0;
    if (!pitch) return false;
    const start = Math.floor(element.scrollLeft / pitch);
    const visible = Math.ceil(element.clientWidth / pitch);
    const window = cards.slice(start, start + visible);
    return window.length > 0 && window.every((card) => {
      const image = card.querySelector('img.g-art-image.is-shown') as HTMLImageElement | null;
      return image?.complete && image.naturalWidth > 0;
    });
  });
  try {
    await expect.poll(currentWindowDecoded, { timeout: 15_000 }).toBe(true);
  } catch (error) {
    throw new Error(`horizontal image pipeline stalled: ${JSON.stringify(await pipelineState())}`, { cause: error });
  }
  await expect(last.locator('img.g-art-image.is-shown')).toHaveCount(1);
  await expect.poll(() => active).toBe(0);
  const cap = await page.evaluate(() => /^h[23]/.test((performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined)?.nextHopProtocol ?? '') ? 12 : 4);
  expect(maxActive).toBeLessThanOrEqual(cap);
  expect(cancelled).toBeGreaterThan(0); // leaving the initial window releases its outstanding browser work
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
  await expect(page).toHaveURL(/\/video\/title\//);
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
