/**
 * Media loading budgets on the real stack, run by `make e2e-realstack` (so by `make check`). Chromium at
 * 4x CPU through CDP, LAN and slow networks, each measure 3 times in a new context (cold), median asserted, all
 * three samples printed on failure. Release A gates time to first frame; Release B adds the image budgets.
 */
import type { Page } from '@playwright/test';

import { channelPageBody, liveItems, liveSnapshotBody } from '../live-youtube-mock';
import { appMetric, appSamples, closeQuietly, firstFrameAt, medianOf, openWatch, perfTitlesPage, resetAppSamples, RUNS, scrollToEnd, stopSession, throttledPage } from './perfProbe';
import { expect, libraryItemId, test } from './realstack';

const TTFF_BUDGET_MS = { direct: { lan: 1500, slow: 3000 }, transcode: { lan: 6000 } } as const;
const SESSION_START = /\/api\/library\/[^/]+\/playback-sessions$/;

for (const profile of ['lan', 'slow'] as const) {
  test(`time to first frame, direct play, ${profile}, cold`, async ({ browser, baseURL, page: owner }) => {
    const id = await libraryItemId(owner, 'Realstack Direct');
    const { median, samples } = await medianOf(`TTFF direct ${profile} (ms)`, async () => {
      const page = await throttledPage(browser, baseURL!, profile);
      try {
        const started = await openWatch(page, id);
        const frame = await firstFrameAt(page);
        await page.goto('about:blank'); // pagehide: the app beacons its own ttff_ms sample
        return frame - started;
      } finally {
        await closeQuietly(page);
      }
    });
    expect(median, samples).toBeLessThanOrEqual(TTFF_BUDGET_MS.direct[profile]);
  });
}

// A pipeline smoke check, not the spec 9.6 budget: it proves the conversion path starts and delivers a first
// segment within 6 s on the 160x90 fixture. The 1080p transcode budget is measured on the reference host by
// controller step C2. Bundled Chromium cannot decode the H.264 a conversion produces, so this times
// Play to the first media segment fetched by the page: the server start, the first segment and the network.
test('conversion pipeline smoke: first media segment within 6 s, 160x90 fixture, lan, cold', async ({ browser, baseURL, page: owner }) => {
  const id = await libraryItemId(owner, 'Realstack Transcode');
  const { median, samples } = await medianOf('first segment transcode lan (ms)', async () => {
    const page = await throttledPage(browser, baseURL!, 'lan');
    let sessionId: string | null = null;
    try {
      const created = page.waitForResponse((response) => response.request().method() === 'POST' && SESSION_START.test(new URL(response.url()).pathname));
      const started = await openWatch(page, id);
      const session = (await (await created).json()) as { session_id: string; playback_url: string };
      sessionId = session.session_id;
      const segmentAt = await page.evaluate(async (manifest) => {
        const text = await (await fetch(manifest, { credentials: 'include' })).text();
        const segment = /^seg\d+\.m4s$/m.exec(text)?.[0];
        if (!segment) throw new Error('the manifest lists no segment');
        await (await fetch(new URL(segment, new URL(manifest, window.location.href)).href, { credentials: 'include' })).arrayBuffer();
        return performance.now();
      }, session.playback_url);
      await stopSession(page, session.session_id); // asserted only here; the finally below never masks a failure
      sessionId = null;
      return segmentAt - started;
    } finally {
      if (sessionId) await stopSession(page, sessionId).catch((error: unknown) => console.log(`could not stop session ${sessionId}: ${String(error)}`));
      await closeQuietly(page);
    }
  });
  expect(median, samples).toBeLessThanOrEqual(TTFF_BUDGET_MS.transcode.lan);
});

test('the app reports its own time to first frame to Diagnostics', async ({ browser, baseURL, page: owner }) => {
  const id = await libraryItemId(owner, 'Realstack Direct');
  const directSamples = async () => {
    const report = (await (await owner.request.get('/api/admin/diagnostics')).json()) as { media_loading?: { metrics: { metric: string; label: string; today_count: number }[] } };
    return report.media_loading?.metrics.filter((summary) => summary.metric === 'ttff_ms' && summary.label.startsWith('direct')).reduce((sum, summary) => sum + summary.today_count, 0) ?? 0;
  };
  // The journeys before this one spend the owner's client-metrics quota (12 a minute, sliding), and a 429 drops the
  // beacon this test waits for. A probe can't tell how many slots are left, so wait out the whole window.
  // a fixed 61 s; a per-test user (empty bucket) if the gate's wall time matters.
  test.setTimeout(170_000);
  await owner.waitForTimeout(61_000);
  const before = await directSamples();
  const page = await throttledPage(browser, baseURL!, 'lan', { cpu: 1 });
  const statuses: number[] = [];
  page.on('response', (response) => { if (new URL(response.url()).pathname === '/api/metrics/client') statuses.push(response.status()); });
  await openWatch(page, id);
  await firstFrameAt(page);
  await page.goto('about:blank');
  await page.context().close();
  try {
    await expect.poll(directSamples, { timeout: 15_000 }).toBeGreaterThan(before);
  } catch (error) {
    expect(statuses, 'client-metrics beacon was rate limited (429): the owner quota was spent by earlier journeys').not.toContain(429);
    throw error;
  }
});

// ---- Release B: gallery image budgets on the medium seed ----

const CASES = [
  { profile: 'lan', cache: 'cold' }, { profile: 'lan', cache: 'warm' }, { profile: 'slow', cache: 'cold' },
] as const;
const IMAGE_BUDGET_MS = {
  wallFirstScreen: { 'lan cold': 1500, 'lan warm': 800, 'slow cold': 3000 },
  wallSharp: { 'lan cold': 2500, 'lan warm': 1000, 'slow cold': 6000 },
  hero: { 'lan cold': 1500, 'lan warm': 600, 'slow cold': 3500 },
} as const;
const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
/** The reference host (PERF_STRICT=1) also gates what only it measures faithfully, and runs the longer measures. */
const STRICT = process.env.PERF_STRICT === '1';

/**
 * Every first-screen poster shows a preview, its image, its card or its colour, and none is a broken image.
 * Colour alone is valid: a poster whose preview did not fit the size limit waits for a load slot on its colour.
 */
async function expectPreviewsPainted(page: Page) {
  const missing = await page.evaluate(() => [...document.querySelectorAll('.g-poster')].filter((poster) => {
    const box = poster.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) return false;
    const broken = [...poster.querySelectorAll('img')].some((image) => image.complete && image.naturalWidth === 0);
    const art = poster.querySelector('.g-art');
    const coloured = !!art && getComputedStyle(art).backgroundColor !== 'rgba(0, 0, 0, 0)';
    return broken || !(poster.querySelector('.g-art-preview, .g-art-image, .g-card') || coloured);
  }).map((poster) => `${poster.getAttribute('aria-label')}: ${poster.querySelector('.g-art')?.outerHTML.slice(0, 200)}`));
  expect(missing, 'first-screen posters with no preview, image, card or colour, or a broken image').toEqual([]);
}

/** Every poster in view shows its image or its card: none is left on a placeholder (criterion 15). */
async function expectSharpInView(page: Page) {
  const placeholders = await page.evaluate(() => [...document.querySelectorAll('.g-poster')].filter((poster) => {
    const box = poster.getBoundingClientRect();
    return box.bottom > 0 && box.top < window.innerHeight && !poster.querySelector('.g-art-image.is-shown, .g-card');
  }).length);
  expect(placeholders, 'posters in view still on a placeholder after the scroll settled').toBe(0);
}

for (const { profile, cache } of CASES) {
  const name = `${profile} ${cache}` as keyof typeof IMAGE_BUDGET_MS.hero; // one of the three CASES

  test(`movies wall first screen and sharp posters, ${name}`, async ({ browser }) => {
    const first: number[] = [];
    const sharp: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      await page.goto('/library/movies');
      if (cache === 'warm') {
        await appMetric(page, 'wall_sharp_ms', 'movies');
        await page.reload();
      }
      first.push(await appMetric(page, 'wall_first_screen_ms', 'movies'));
      await expectPreviewsPainted(page); // at first screen, before the sharp posters could replace the previews
      sharp.push(await appMetric(page, 'wall_sharp_ms', 'movies'));
      await page.context().close();
    }
    console.log(`wall ${name}: first screen ${first.join(', ')} ms; sharp ${sharp.join(', ')} ms`);
    expect(median(first), `first screen ${first.join(', ')}`).toBeLessThanOrEqual(IMAGE_BUDGET_MS.wallFirstScreen[name]);
    expect(median(sharp), `sharp ${sharp.join(', ')}`).toBeLessThanOrEqual(IMAGE_BUDGET_MS.wallSharp[name]);
  });

  test(`title page hero from a wall click, ${name}`, async ({ browser }) => {
    const hero: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      const firstPage = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/titles');
      await page.goto('/library/movies');
      // A title with no backdrop settles its hero at once (the colour field), so time one whose summary names a backdrop.
      const { items } = (await (await firstPage).json()) as { items: { name: string; backdrop?: unknown }[] };
      // By its name (the first item may be the feature tile, outside the grid), so a name the page repeats is skipped.
      const pick = items.find((item) => item.backdrop && items.filter((other) => other.name === item.name).length === 1);
      expect(pick, 'a first-page movie with a backdrop and a unique name').toBeDefined();
      const escaped = pick!.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const poster = page.getByRole('button', { name: new RegExp(`^${escaped}, `) }).first();
      await expect(poster).toBeVisible();
      await appMetric(page, 'wall_first_screen_ms', 'movies');
      if (cache === 'warm') {
        await poster.click();
        await appMetric(page, 'detail_hero_ms', 'click');
        await resetAppSamples(page); // an in-app Back: the first visit's wall sample must not satisfy the wait below
        await page.goBack();
        await expect(poster).toBeVisible(); // a wall restored from its store records no new first screen
      }
      await resetAppSamples(page); // the warm visit's hero sample must not be read as this one
      await poster.click();
      hero.push(await appMetric(page, 'detail_hero_ms', 'click'));
      // The sample timed the backdrop itself, not a card or a failed image.
      await expect.poll(() => page.locator('.t-hero-art img.is-shown').evaluate((image: HTMLImageElement) => image.naturalWidth), { timeout: 2_000 }).toBeGreaterThan(0);
      await page.context().close();
    }
    console.log(`hero ${name}: ${hero.join(', ')} ms`);
    expect(median(hero), `hero ${hero.join(', ')}`).toBeLessThanOrEqual(IMAGE_BUDGET_MS.hero[name]);
  });

  // Zero is asserted on the run itself (stricter than a median), so this measure runs once per profile. Only lan cold
  // runs in make check; warm and slow cold take minutes each and run on the reference host.
  test(`no image fails across a full scroll of both walls, ${name}`, async ({ browser }) => {
    test.skip(!STRICT && name !== 'lan cold', 'reference host only (PERF_STRICT=1)');
    test.setTimeout(300_000);
    const page = await perfTitlesPage(browser, profile);
    const failures: string[] = [];
    page.on('response', (response) => {
      const route = new URL(response.url()).pathname;
      if ((route.startsWith('/api/art/') || /^\/api\/titles\/[^/]+\/images\//.test(route)) && response.status() >= 400) failures.push(`${response.status()} ${route.split('/').slice(0, 3).join('/')}`);
    });
    for (const lens of ['movies', 'shows']) {
      await page.goto(`/library/${lens}`);
      if (cache === 'warm') { await scrollToEnd(page); await expectSharpInView(page); await page.reload(); }
      await scrollToEnd(page);
      await expectSharpInView(page);
    }
    const failed = (await appSamples(page)).filter((sample) => sample.metric === 'image_failed');
    expect(failures, 'image responses >= 400').toEqual([]);
    expect(failed, 'image_failed samples').toEqual([]);
    await page.context().close();
  });
}

for (const cpu of [4, 1] as const) {
  test(`scripted fling on the movies wall at ${cpu}x CPU`, async ({ browser }) => {
    const longTasks: number[] = [];
    const fps: number[] = [];
    const scrolled: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, 'lan', { cpu });
      await page.goto('/library/movies');
      await appMetric(page, 'wall_sharp_ms', 'movies');
      await page.evaluate(() => {
        const fling = { longTasks: 0, frames: [] as number[] };
        (window as Window & { __perfFling?: typeof fling }).__perfFling = fling;
        new PerformanceObserver((list) => { fling.longTasks += list.getEntries().length; }).observe({ type: 'longtask' });
        const tick = (now: number) => { fling.frames.push(now); if (fling.frames.length < 1000) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      });
      const height = page.viewportSize()!.height;
      const startY = await page.evaluate(() => window.scrollY);
      await page.mouse.move(640, 400);
      for (let step = 0; step < 80; step += 1) { // 20 viewport heights in 4 s
        await page.mouse.wheel(0, (20 * height) / 80);
        await page.waitForTimeout(50);
      }
      const flung = (await page.evaluate(() => window.scrollY)) - startY;
      scrolled.push(Math.round(flung / height));
      expect(flung, 'the fling scrolled the wall at least 10 of its 20 viewport heights').toBeGreaterThanOrEqual(10 * height);
      const result = await page.evaluate(() => {
        const fling = (window as Window & { __perfFling?: { longTasks: number; frames: number[] } }).__perfFling!;
        const gaps = fling.frames.slice(1).map((time, index) => time - fling.frames[index]).sort((a, b) => a - b);
        return { longTasks: fling.longTasks, fps: 1000 / gaps[Math.floor(gaps.length / 2)] };
      });
      longTasks.push(result.longTasks);
      fps.push(Math.round(result.fps));
      await page.context().close();
    }
    console.log(`fling ${cpu}x: long tasks ${longTasks.join(', ')}; fps ${fps.join(', ')}; scrolled ${scrolled.join(', ')} viewports`);
    if (cpu === 4) expect(median(longTasks), `long tasks ${longTasks.join(', ')}`).toBeLessThanOrEqual(2);
    // Unthrottled timing follows this host's load, so it is a budget only on the reference host (PERF_STRICT=1); elsewhere it is only logged above.
    else if (STRICT) {
      expect(median(longTasks), `long tasks ${longTasks.join(', ')}`).toBe(0);
      expect(median(fps), `fps ${fps.join(', ')}`).toBeGreaterThanOrEqual(55);
    }
  });
}

// ---- Library gallery: the All landing and the albums wall on the medium seed ----

const LIBRARY_BUDGET_MS = {
  allFirstScreen: { 'lan cold': 1500, 'lan warm': 800, 'slow cold': 3000 },
  twoChapters: { 'lan cold': 2000, 'lan warm': 1000, 'slow cold': 4000 },
  albumsFirstScreen: { 'lan cold': 1500, 'lan warm': 800, 'slow cold': 3000 },
} as const;
/** What the All landing itself asks for: sections, the spotlight and chapter slices. */
const landingRequest = (url: URL) => url.pathname === '/api/library/sections' || url.pathname === '/api/titles' || (url.pathname === '/api/library' && url.searchParams.has('kind'));

/** Page-clock time at which the first two chapters' first rows each show a preview, image, card or colour. */
async function twoChaptersPaintedAt(page: Page): Promise<number> {
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('.g-all .g-chapter:not(.is-skeleton)')].slice(0, 2).map((chapter) => chapter.querySelector('.g-chapter-row'));
    if (rows.length < 2 || rows.some((row) => !row?.querySelector('.g-art'))) return false;
    return rows.every((row) => [...row!.querySelectorAll('.g-art')].every((art) => art.querySelector('.g-art-preview, .g-art-image, .g-card') || getComputedStyle(art).backgroundColor !== 'rgba(0, 0, 0, 0)'));
  }, undefined, { timeout: 30_000, polling: 'raf' });
  return page.evaluate(() => performance.now());
}

for (const { profile, cache } of CASES) {
  const name = `${profile} ${cache}` as keyof typeof LIBRARY_BUDGET_MS.allFirstScreen;

  test(`All landing first screen, first two chapters and requests before a scroll, ${name}`, async ({ browser }) => {
    const first: number[] = [];
    const chapters: number[] = [];
    const requests: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      if (cache === 'warm') {
        await page.goto('/library');
        await appMetric(page, 'wall_first_screen_ms', 'all');
        await twoChaptersPaintedAt(page);
      }
      const asked: string[] = [];
      page.on('request', (request) => { const url = new URL(request.url()); if (landingRequest(url)) asked.push(`${url.pathname}${url.search}`); });
      if (cache === 'warm') await page.reload(); else await page.goto('/library');
      first.push(await appMetric(page, 'wall_first_screen_ms', 'all'));
      chapters.push(await twoChaptersPaintedAt(page));
      await page.waitForTimeout(1_000); // anything else the landing would ask for before a scroll
      requests.push(asked.length);
      await page.context().close();
    }
    console.log(`All ${name}: first screen ${first.join(', ')} ms; two chapters ${chapters.map(Math.round).join(', ')} ms; requests ${requests.join(', ')}`);
    expect(median(first), `first screen ${first.join(', ')}`).toBeLessThanOrEqual(LIBRARY_BUDGET_MS.allFirstScreen[name]);
    expect(median(chapters), `two chapters ${chapters.join(', ')}`).toBeLessThanOrEqual(LIBRARY_BUDGET_MS.twoChapters[name]);
    expect(Math.max(...requests), `requests ${requests.join(', ')}`).toBeLessThanOrEqual(4);
  });

  // Only lan cold runs in make check; the other two take minutes each and run on the reference host.
  test(`long tasks while scrolling the All landing top to bottom, ${name}`, async ({ browser }) => {
    test.skip(!STRICT && name !== 'lan cold', 'reference host only (PERF_STRICT=1)');
    test.setTimeout(300_000);
    const counts: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      await page.goto('/library');
      if (cache === 'warm') { await appMetric(page, 'wall_first_screen_ms', 'all'); await page.reload(); }
      await appMetric(page, 'wall_first_screen_ms', 'all');
      await page.evaluate(() => {
        const w = window as Window & { __perfLong?: number };
        w.__perfLong = 0;
        new PerformanceObserver((list) => { w.__perfLong! += list.getEntries().filter((entry) => entry.duration > 50).length; }).observe({ type: 'longtask' });
      });
      await scrollToEnd(page); // every chapter and Collections load on the way down
      counts.push(await page.evaluate(() => (window as Window & { __perfLong?: number }).__perfLong ?? 0));
      await page.context().close();
    }
    console.log(`All scroll ${name}: long tasks ${counts.join(', ')}`);
    expect(median(counts), `long tasks ${counts.join(', ')}`).toBeLessThanOrEqual(2);
  });

  test(`albums wall first screen, ${name}`, async ({ browser }) => {
    const first: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      await page.goto('/library/music');
      if (cache === 'warm') {
        await appMetric(page, 'wall_first_screen_ms', 'albums');
        await page.reload();
      }
      first.push(await appMetric(page, 'wall_first_screen_ms', 'albums'));
      await page.context().close();
    }
    console.log(`albums ${name}: first screen ${first.join(', ')} ms`);
    expect(median(first), `first screen ${first.join(', ')}`).toBeLessThanOrEqual(LIBRARY_BUDGET_MS.albumsFirstScreen[name]);
  });
}

// ---- Home budgets on the medium seed ----
const HOME_BUDGET_MS = {
  firstScreen: { 'lan cold': 1500, 'lan warm': 800, 'slow cold': 3000 },
  hero: { 'lan cold': 2000, 'slow cold': 4000 },
} as const;
/** Requests Home itself starts: titles, next up, a title's detail, live, the watch queue, title rows, collections, playback options. */
const HOME_REQUEST = /^\/api\/(titles(\/next-up|\/[^/]+)?|discovery\/live|me\/watch-queue|home\/title-rows|collections(\/[^/]+)?|library\/[^/]+\/playback-options)$/;

for (const { profile, cache } of CASES) {
  const name = `${profile} ${cache}` as keyof typeof HOME_BUDGET_MS.firstScreen;

  test(`home first screen and hero, ${name}`, async ({ browser }) => {
    const first: number[] = [];
    const hero: number[] = [];
    for (let run = 0; run < RUNS; run += 1) {
      const page = await perfTitlesPage(browser, profile);
      await page.goto('/');
      if (cache === 'warm') {
        await appMetric(page, 'home_hero_ms', 'home');
        await page.reload(); // the init script starts a fresh sample list on every load
      }
      first.push(await appMetric(page, 'home_first_screen_ms', 'home'));
      // The seed gives the perf member unfinished progress, so the hero is a Continue title.
      await expect(page.locator('.h-hero-kicker')).toHaveText(/continue watching/i);
      hero.push(await appMetric(page, 'home_hero_ms', 'home'));
      await page.context().close();
    }
    console.log(`home ${name}: first screen ${first.join(', ')} ms; hero ${hero.join(', ')} ms`);
    expect(median(first), `first screen ${first.join(', ')}`).toBeLessThanOrEqual(HOME_BUDGET_MS.firstScreen[name]);
    if (name !== 'lan warm') expect(median(hero), `hero ${hero.join(', ')}`).toBeLessThanOrEqual(HOME_BUDGET_MS.hero[name]);
  });
}

test('home makes at most 5 of its own requests before its first screen, lan cold', async ({ browser }) => {
  const counts: number[] = [];
  const seen: string[][] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan');
    // Counted when each request is issued, not from resource timing: that only lists a request once it has finished, so a request still in flight at the first screen was undercounted.
    const issued: { at: number; path: string }[] = [];
    page.on('request', (request) => { const path = new URL(request.url()).pathname; if (HOME_REQUEST.test(path)) issued.push({ at: Date.now(), path }); });
    await page.goto('/');
    const at = await appMetric(page, 'home_first_screen_ms', 'home');
    const origin = await page.evaluate(() => performance.timeOrigin);
    const early = issued.filter((request) => request.at <= origin + at).map((request) => request.path);
    counts.push(early.length);
    seen.push(early);
    await page.context().close();
  }
  expect(median(counts), JSON.stringify(seen)).toBeLessThanOrEqual(5);
});

test('home: no image fails and at most 2 long tasks across a scripted scroll to the bottom, lan cold', async ({ browser }) => {
  test.setTimeout(240_000);
  const longTasks: number[] = [];
  const failures: string[] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan');
    page.on('response', (response) => {
      const route = new URL(response.url()).pathname;
      const image = route.startsWith('/api/art/') || route.startsWith('/api/artwork/') || /^\/api\/titles\/[^/]+\/images\//.test(route) || /^\/api\/library\/[^/]+\/artwork$/.test(route);
      if (image && response.status() >= 400) failures.push(`${response.status()} ${route.split('/').slice(0, 3).join('/')}`);
    });
    await page.goto('/');
    await appMetric(page, 'home_first_screen_ms', 'home');
    await page.evaluate(() => {
      const w = window as Window & { __homeLongTasks?: number };
      w.__homeLongTasks = 0;
      new PerformanceObserver((list) => { w.__homeLongTasks! += list.getEntries().length; }).observe({ type: 'longtask' });
    });
    await scrollToEnd(page);
    longTasks.push(await page.evaluate(() => (window as Window & { __homeLongTasks?: number }).__homeLongTasks ?? 0));
    expect((await appSamples(page)).filter((sample) => sample.metric === 'image_failed'), 'image_failed samples').toEqual([]);
    await page.context().close();
  }
  console.log(`home scroll: long tasks ${longTasks.join(', ')}`);
  expect(failures, 'image responses >= 400').toEqual([]);
  expect(median(longTasks), `long tasks ${longTasks.join(', ')}`).toBeLessThanOrEqual(2);
});

// ---- The fixture server has no provider, so /api/discovery/live and the channel
// route are served from fixtures over the real app, artwork proxy 404s included (plans 00 D13). Budgets: 1.5x LAN, 4x CPU.
const LIVE_BUDGET_MS = { liveFirstScreen: 1500, channelFirstScreen: 1500 } as const;
const PART_C_CATEGORIES = ['gaming', 'music', 'news', 'sports', 'creative', 'other'];
const liveFixture = liveSnapshotBody({
  items: PART_C_CATEGORIES.flatMap((key) => liveItems(16, { category_keys: [key] }, key)),
  categories: PART_C_CATEGORIES.map((key) => ({ key, label: key, state: 'ready' })),
});

async function serveLive(page: Page) {
  await page.route('**/api/discovery/live', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(liveFixture) }));
}

/** ms from navigation start until the masthead, the hero's colour field and the first rail's cards are painted. */
async function liveFirstScreen(page: Page): Promise<number> {
  await page.waitForFunction(() => {
    const hero = document.querySelector('.g-live-hero .g-art') as HTMLElement | null;
    return Boolean(document.querySelector('.g-streaming h1') && hero && getComputedStyle(hero).backgroundColor !== 'rgba(0, 0, 0, 0)' && document.querySelector('section.g-rail .g-remote-card'));
  });
  return page.evaluate(() => performance.now());
}

test('/live first screen with colour fields painted, lan cold', async ({ browser }) => {
  const samples: number[] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan');
    await serveLive(page);
    await page.goto('/streaming/live');
    samples.push(await liveFirstScreen(page));
    await page.context().close();
  }
  console.log(`/live first screen ${samples.join(', ')} ms`);
  expect(median(samples), `first screen ${samples.join(', ')}`).toBeLessThanOrEqual(LIVE_BUDGET_MS.liveFirstScreen);
});

test('scripted fling across the /live rails at 4x CPU', async ({ browser }) => {
  const longTasks: number[] = [];
  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan', { cpu: 4 });
    await serveLive(page);
    await page.goto('/streaming/live');
    await liveFirstScreen(page);
    await page.evaluate(() => {
      const fling = { longTasks: 0 };
      (window as Window & { __liveFling?: typeof fling }).__liveFling = fling;
      new PerformanceObserver((list) => { fling.longTasks += list.getEntries().length; }).observe({ type: 'longtask' });
    });
    const height = page.viewportSize()!.height;
    await page.mouse.move(640, 400);
    for (let step = 0; step < 40; step += 1) {
      await page.mouse.wheel(0, height / 4);
      await page.waitForTimeout(50);
    }
    const rail = page.locator('.g-rail-scroller').first();
    await rail.hover();
    for (let step = 0; step < 20; step += 1) {
      await page.mouse.wheel(320, 0);
      await page.waitForTimeout(40);
    }
    longTasks.push(await page.evaluate(() => (window as Window & { __liveFling?: { longTasks: number } }).__liveFling!.longTasks));
    await page.context().close();
  }
  console.log(`/live fling long tasks ${longTasks.join(', ')}`);
  expect(median(longTasks), `long tasks ${longTasks.join(', ')}`).toBeLessThanOrEqual(2);
});

test('channel page first screen on a cache hit, lan', async ({ browser }) => {
  const samples: number[] = [];
  const id = 'UCabcdefghijklmnopqrstuv';
  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan');
    await page.route(/\/api\/channels\/youtube\//, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(channelPageBody(id)) }));
    await page.goto(`/channel/youtube/${id}`);
    await page.waitForFunction(() => document.querySelectorAll('.g-channel-wall .g-remote-card').length >= 4 && Boolean(document.querySelector('.g-channel-header h1')));
    samples.push(await page.evaluate(() => performance.now()));
    await page.context().close();
  }
  console.log(`channel page first screen ${samples.join(', ')} ms`);
  expect(median(samples), `first screen ${samples.join(', ')}`).toBeLessThanOrEqual(LIVE_BUDGET_MS.channelFirstScreen);
});
