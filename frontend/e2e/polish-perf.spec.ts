import { existsSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { homeMovie, mockHome } from './home-mock';
import { mockLibraryChannels, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { item, mockAdminFixtures, mockApi, mockTitles, signIn } from './lumina-mock';
import { mockPaletteSearch } from './palette-mock';
import { mockReco } from './reco-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** CLS per route and per overlay, and ⌘K → focused input, on the mocked API at 4× CPU (no mocked perf project exists, so CDP throttling is applied in the test). */
type Shifts = { __cls: number; __src: string[] };
const OBSERVE = () => {
  (window as unknown as Shifts).__cls = 0;
  (window as unknown as Shifts).__src = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as Array<PerformanceEntry & { value: number; hadRecentInput: boolean }>) {
      if (entry.hadRecentInput) continue;
      (window as unknown as Shifts).__cls += entry.value;
      const nodes = ((entry as unknown as { sources?: Array<{ node?: Element; previousRect: DOMRect; currentRect: DOMRect }> }).sources ?? []).map((source) => `${(source.node as Element | undefined)?.closest('[data-shelf-section]')?.getAttribute('data-shelf-section')}/${(source.node as Element | undefined)?.className || source.node?.nodeName} ${Math.round(source.previousRect.y)}->${Math.round(source.currentRect.y)} h${Math.round(source.previousRect.height)}->${Math.round(source.currentRect.height)}`);
      (window as unknown as Shifts).__src.push(`${entry.value.toFixed(3)} ${nodes.join(',')}`);
    }
  }).observe({ type: 'layout-shift' });
};
async function throttle(page: Page) {
  const client = await page.context().newCDPSession(page);
  await client.send('Emulation.setCPUThrottlingRate', { rate: 4 });
}
const shift = (page: Page) => page.evaluate(() => (window as unknown as Shifts).__cls);

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, (route) => route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes 0-${media.length - 1}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media }));
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page); await mockTitles(page); await mockHome(page, { newest: [homeMovie(1), homeMovie(2), homeMovie(3)] }); // the library fixture counts a movie and a show: titles that answer empty would test the race path, not a first render
  await mockRemoteArtwork(page); await mockLive(page); await mockLibraryChannels(page); await mockReco(page);
  await mockPaletteSearch(page, { delayMs: 40 });
  await page.goto('/');
  await signIn(page);
});

const ROUTES = ['/', '/library', '/downloads', '/streaming', '/settings/playback', '/library/collections', '/library/deleted', `/watch/library/${item.id}`, '/streaming/live', '/streaming/channels', '/library/youtube?view=channels'];
async function firstRender(page: Page, path: string) {
  await page.addInitScript(OBSERVE);
  await page.goto(path);
  await expect(page.locator('main h1').first()).toBeVisible();
  await page.waitForTimeout(1000);
  expect(await shift(page), await page.evaluate(() => (window as unknown as Shifts).__src.join(' | '))).toBeLessThanOrEqual(0.02);
}
for (const path of ROUTES) test(`first render CLS ≤ 0.02 on ${path}`, async ({ page }) => firstRender(page, path));

test('first render CLS ≤ 0.02 on /library with only saved videos (no spotlight to show)', async ({ page }) => {
  await mockHome(page); // no titles: the What's new band has nothing to show and must not be reserved
  await page.route('**/api/library/sections', (route) => route.fulfill({ json: { movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 1, recordings: 0, deleted: 0 } }));
  await firstRender(page, '/library');
});

test('opening the palette, a dialog, a menu, a toast, the reco menu and the mini player shifts nothing', async ({ page }) => {
  // [name, where to stand, what to open]: navigation resets the observer, so each overlay is measured after it has arrived.
  const opens: Array<[string, string, () => Promise<void>]> = [
    ['palette', '/settings/members', async () => { await page.keyboard.press('ControlOrMeta+k'); await expect(page.locator('dialog.g-palette').getByRole('combobox')).toBeFocused(); await page.keyboard.press('Escape'); }],
    ['menu', '/settings/members', async () => { await page.getByRole('button', { name: /, account menu$/ }).click(); await page.keyboard.press('Escape'); }],
    ['dialog', '/settings/members', async () => { await page.getByRole('button', { name: 'Invite someone' }).click(); await page.keyboard.press('Escape'); }],
    ['toast', '/settings/playback', async () => { await page.getByRole('switch').first().click(); await page.waitForTimeout(300); }],
    ['reco menu', '/', async () => { await page.locator('[data-shelf-section="picked_for_you"]').getByRole('button', { name: /^More options for / }).first().click(); await page.keyboard.press('Escape'); }],
    ['mini player', `/watch/library/${item.id}`, async () => { await page.getByRole('button', { name: 'Home', exact: true }).and(page.locator('#primary-navigation button')).click(); await page.waitForTimeout(300); }],
  ];
  for (const [name, where, open] of opens) {
    await page.goto(where);
    await expect(page.locator('main h1').first()).toBeVisible();
    await page.waitForTimeout(800);
    // Home's first-screen shelves settle on their own (an empty one collapses, which first render CLS covers); measure
    // the overlay once none is still loading. Below-the-fold shelves load only when scrolled near, so they are left out.
    await expect.poll(() => page.evaluate(() => [...document.querySelectorAll('[data-shelf-section][aria-busy="true"]')].filter((node) => node.getBoundingClientRect().top < innerHeight).length)).toBe(0);
    await page.evaluate(OBSERVE);
    await open();
    expect(await shift(page), name).toBeLessThanOrEqual(0.01);
  }
});

test('⌘K focuses the palette input within 150 ms cold and 50 ms warm (4× CPU)', async ({ page }) => {
  // The mocked project serves the Vite dev server (unminified React: ~3x slower than what ships), so the timing is
  // taken on the production bundle in dist/ (built by `make check` before the mocked suite), served from the same origin.
  const dist = resolve('dist');
  expect(existsSync(join(dist, 'index.html')), 'run `npm run build` first: the palette timing needs dist/').toBe(true);
  await page.route(/^https?:\/\/[^/]+\/(?!api\/)/, async (route) => {
    const { pathname } = new URL(route.request().url());
    const file = join(dist, pathname);
    const found = pathname !== '/' && file.startsWith(dist) && existsSync(file) && statSync(file).isFile();
    await route.fulfill({ path: found ? file : join(dist, 'index.html') });
  });
  await throttle(page);
  // Time from the keydown reaching the window to the palette input holding focus (a rAF poll in the page, so the CDP round trip is not measured).
  const time = async () => {
    await page.evaluate(() => {
      (window as unknown as { __open: Promise<number> }).__open = new Promise<number>((resolve) => {
        window.addEventListener('keydown', () => {
          const start = performance.now();
          const check = () => { if (document.activeElement?.getAttribute('role') === 'combobox' && document.activeElement.closest('dialog.g-palette')) resolve(performance.now() - start); else requestAnimationFrame(check); };
          check();
        }, { once: true, capture: true });
      });
    });
    await page.keyboard.press('ControlOrMeta+k');
    return page.evaluate(() => (window as unknown as { __open: Promise<number> }).__open);
  };
  // Cold is one-shot per page load, so a scheduler hiccup on a shared host is retried on a fresh load: the budget must be met by at least one of three.
  const colds: number[] = [];
  for (let attempt = 0; attempt < 3 && !(colds.at(-1)! <= 150); attempt++) {
    await page.goto('/');
    await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
    await page.waitForTimeout(1500); // the idle prefetch has fired: the state a member reaches a second after signing in
    colds.push(await time());
  }
  const cold = Math.min(...colds);
  const warmMedian = async () => {
    const runs: number[] = [];
    for (let run = 0; run < 5; run++) { // median of five: scheduler hiccups on a shared host are not the palette's cost
      await page.keyboard.press('Escape');
      await page.waitForTimeout(200);
      runs.push(await time());
    }
    return runs.sort((a, b) => a - b)[2];
  };
  let warm = await warmMedian();
  for (let retry = 0; retry < 2 && warm > 50; retry++) warm = Math.min(warm, await warmMedian()); // a whole noisy window is retried; a real regression is slow in every window
  expect(cold, 'cold').toBeLessThanOrEqual(150);
  expect(warm, 'warm').toBeLessThanOrEqual(50);
});
