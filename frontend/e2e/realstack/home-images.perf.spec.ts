/**
 * Home image feedback loop on the real medium-title server. The retained evidence contains only aggregates,
 * anonymous layout geometry and normalized route classes: artwork capabilities, title IDs and member data never
 * leave the browser process.
 */
import { writeFile } from 'node:fs/promises';
import type { ElementHandle, Request } from '@playwright/test';

import { appMetric, perfTitlesPage, RUNS, scrollToEnd } from './perfProbe';
import { expect, test } from './realstack';

type Phase = 'cold' | 'warm';
type ImageRecord = {
  currentVisible: boolean;
  decodeMs: number;
  fetchPriority: string;
  hero: boolean;
  horizontalOffscreenAtAttach: boolean;
  loadEndToRevealMs: number | null;
  queueWaitMs: number;
  revealedAtMs: number;
  shelf: boolean;
  verticalOffscreenAtAttach: boolean;
};
type Probe = { snapshot: () => { now: number; records: ImageRecord[] } };
type ProbeWindow = Window & { __homeImageProbe?: Probe };
type NetworkPhase = {
  active: number;
  activeUrls: Map<string, number>;
  duplicateConcurrentRequests: number;
  duplicates: number;
  failures: string[];
  issued: number;
  maxActive: number;
  requestAborted: number;
  requestFailed: number;
  routes: Record<string, number>;
  selections: Record<string, number>;
  seen: Set<string>;
};
type PhaseEvidence = {
  appFirstScreenMs: number;
  appHeroMs: number;
  firstScreenDecodedAtMs: number;
  firstViewportReady: { atMs: number; artwork: number; cards: number; decodedImages: number };
  decodedHero: number;
  decodedShelf: number;
  decodeMs: { median: number; max: number };
  duplicateSharpElements: number;
  duplicateConcurrentRequests: number;
  encodedImageBytes: number;
  imageFailures: string[];
  imageBytes: number;
  imageRequests: number;
  duplicateRequests: number;
  duplicateBodyTransfers: number;
  maxImageRequestsInFlight: number;
  maxImageResourceBytes: number;
  networkProtocols: Record<string, number>;
  requestFailures: { aborted: number; other: number };
  preScroll: Array<{
    activeRequests: number;
    atMs: number;
    decodedSharp: number;
    imageRequests: number;
    offscreenDecoded: number;
    sharpElements: number;
    transferBytes: number;
    visibleDecoded: number;
    latestVisibleRevealAtMs: number;
    viewportHeight: number;
    viewportWidth: number;
    windowScrollY: number;
    decodedSlots: Array<{ top: number; bottom: number; left: number; right: number; shelfTop: number | null; shelfBottom: number | null; rowPitch: number | null; shape: string }>;
    shelves: Array<{ ordinal: number; top: number; bottom: number; shape: string; decoded: number }>;
  }>;
  offscreenAtAttach: { horizontal: number; vertical: number };
  queueWaitMs: { median: number; max: number };
  responseToRevealMs: { median: number; max: number };
  resourceDurationMs: { median: number; max: number };
  routes: Record<string, number>;
  scrollAndFocus: { bottomVisibleDecoded: number; focusedCardReadyAtMs: number; sharpElements: number };
  selections: Record<string, number>;
};

const IMAGE_ROUTE = /^\/api\/(?:art\/(?!framing$)|artwork\/|titles\/[^/]+\/images\/|library\/[^/]+\/artwork$)/;
const emptyNetworkPhase = (): NetworkPhase => ({ active: 0, activeUrls: new Map(), duplicateConcurrentRequests: 0, duplicates: 0, failures: [], issued: 0, maxActive: 0, requestAborted: 0, requestFailed: 0, routes: {}, seen: new Set(), selections: {} });
const median = (values: number[]): number => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)] ?? 0;
const summary = (values: number[]): { median: number; max: number } => ({ median: Math.round(median(values)), max: Math.round(Math.max(0, ...values)) });

function routeClass(path: string): string | null {
  const parts = path.split('/');
  if (parts[1] !== 'api') return null;
  if (parts[2] === 'art') return `art/${parts[5] ?? 'unknown'}`;
  if (parts[2] === 'artwork') return `artwork/${parts[3] ?? 'unknown'}`;
  if (parts[2] === 'titles' && parts[4] === 'images') return 'titles/images';
  if (parts[2] === 'library' && parts[4] === 'artwork') return 'library/artwork';
  return null;
}

/** No capability or title identity: just the rendition width/kind or the legacy original route kind. */
function selectionClass(path: string): string | null {
  const parts = path.split('/');
  if (parts[2] === 'art') {
    const width = /-(\d+)\.[^.]+$/.exec(parts[6] ?? '')?.[1] ?? 'unknown';
    return `rendition/${parts[5] ?? 'unknown'}/${width}`;
  }
  if (parts[2] === 'titles' && parts[4] === 'images') return `original/${parts[5] ?? 'unknown'}`;
  if (parts[2] === 'artwork') return `remote/${parts[3] ?? 'unknown'}`;
  if (parts[2] === 'library' && parts[4] === 'artwork') return 'library/original';
  return null;
}

/** Installed before Home: observes slot birth, sharp-image admission, and decoded reveal on the page clock. */
function homeImageProbe(): void {
  type InternalRecord = Omit<ImageRecord, 'currentVisible' | 'decodeMs' | 'loadEndToRevealMs' | 'queueWaitMs' | 'revealedAtMs'> & {
    attachedAt: number;
    bornAt: number;
    image: HTMLImageElement;
    shownAt: number | null;
  };
  const artBorn = new WeakMap<Element, number>();
  const images = new WeakSet<HTMLImageElement>();
  const records: InternalRecord[] = [];
  const inViewport = (rect: DOMRect) => ({
    horizontal: rect.right > 0 && rect.left < innerWidth,
    vertical: rect.bottom > 0 && rect.top < innerHeight,
  });
  const registerArt = (art: Element) => {
    if (!artBorn.has(art)) artBorn.set(art, performance.now());
  };
  const registerImage = (image: HTMLImageElement) => {
    if (!image.classList.contains('g-art-image') || images.has(image)) return;
    images.add(image);
    const art = image.closest('.g-art');
    if (!art) return;
    registerArt(art);
    const attachedAt = performance.now();
    const visible = inViewport(art.getBoundingClientRect());
    records.push({
      attachedAt,
      bornAt: artBorn.get(art)!,
      image,
      shownAt: image.classList.contains('is-shown') ? attachedAt : null,
      fetchPriority: image.fetchPriority,
      hero: Boolean(image.closest('.h-hero')),
      horizontalOffscreenAtAttach: !visible.horizontal,
      shelf: Boolean(image.closest('.h-shelf')),
      verticalOffscreenAtAttach: !visible.vertical,
    });
  };
  const walk = (node: Node) => {
    if (!(node instanceof Element)) return;
    if (node.matches('.g-art')) registerArt(node);
    node.querySelectorAll('.g-art').forEach(registerArt);
    if (node instanceof HTMLImageElement) registerImage(node);
    node.querySelectorAll('img.g-art-image').forEach((image) => registerImage(image as HTMLImageElement));
  };
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') mutation.addedNodes.forEach(walk);
      else if (mutation.target instanceof HTMLImageElement) {
        registerImage(mutation.target);
        if (mutation.target.classList.contains('is-shown')) {
          for (let index = records.length - 1; index >= 0; index -= 1) {
            const record = records[index];
            if (record.image !== mutation.target) continue;
            if (record.shownAt === null) record.shownAt = performance.now();
            break;
          }
        }
      }
    }
  });
  observer.observe(document, { attributes: true, attributeFilter: ['class'], childList: true, subtree: true });
  (window as ProbeWindow).__homeImageProbe = {
    snapshot: () => ({
      now: performance.now(),
      records: records.flatMap((record) => {
        if (record.shownAt === null) return [];
        const timing = performance.getEntriesByName(record.image.currentSrc).at(-1) as PerformanceResourceTiming | undefined;
        const { attachedAt, bornAt, image: _image, shownAt, ...kept } = record;
        const visible = inViewport(record.image.getBoundingClientRect());
        return [{
          ...kept,
          currentVisible: visible.horizontal && visible.vertical,
          decodeMs: shownAt - attachedAt,
          loadEndToRevealMs: timing ? Math.max(0, shownAt - timing.responseEnd) : null,
          queueWaitMs: attachedAt - bornAt,
          revealedAtMs: shownAt,
        }];
      }),
    }),
  };
}

async function decodedFirstScreen(page: import('@playwright/test').Page): Promise<number> {
  await expect.poll(() => page.evaluate(() => {
    const hero = document.querySelector('.h-hero img.g-art-image.is-shown') as HTMLImageElement | null;
    return Boolean(hero?.complete && hero.naturalWidth > 0);
  }), { timeout: 30_000 }).toBe(true);
  await page.locator('.h-shelf').first().scrollIntoViewIfNeeded();
  await expect.poll(() => page.evaluate(() => [...document.querySelectorAll('.h-shelf img.g-art-image.is-shown')]
    .filter((candidate) => {
      const image = candidate as HTMLImageElement;
      const rect = image.getBoundingClientRect();
      return image.complete && image.naturalWidth > 0 && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    }).length), { timeout: 30_000 }).toBeGreaterThanOrEqual(4);
  return page.evaluate(() => performance.now());
}

/** The time at which every currently visible artwork slot has either decoded sharp pixels or shown its real fallback card. */
async function visibleViewportReady(page: import('@playwright/test').Page) {
  const snapshot = () => page.evaluate(() => {
    const visible = [...document.querySelectorAll('.g-art:not(.g-art-logo)')].filter((candidate) => {
      const rect = candidate.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    });
    const decodedImages = visible.filter((art) => {
      const image = art.querySelector('img.g-art-image.is-shown') as HTMLImageElement | null;
      return Boolean(image?.complete && image.naturalWidth > 0);
    }).length;
    const cards = visible.filter((art) => art.querySelector('.g-card')).length;
    return { atMs: Math.round(performance.now()), artwork: visible.length, cards, decodedImages };
  });
  await expect.poll(async () => {
    const state = await snapshot();
    return state.artwork > 0 && state.decodedImages + state.cards === state.artwork;
  }, { timeout: 30_000 }).toBe(true);
  return snapshot();
}

async function exerciseScrollAndFocus(page: import('@playwright/test').Page) {
  const card = page.locator('.h-card').last();
  const main = card.locator('[data-focus-item]').first();
  await expect(main).toBeAttached();
  const mainHandle = await main.elementHandle();
  expect(mainHandle, 'far card main control exists').not.toBeNull();
  await mainHandle!.focus();
  await expect.poll(() => mainHandle!.evaluate((element) => element.isConnected && document.activeElement === element)).toBe(true);
  await expect.poll(() => page.evaluate(() => {
    const card = document.activeElement?.closest('.h-card');
    const image = card?.querySelector('img.g-art-image.is-shown') as HTMLImageElement | null;
    return Boolean(image?.complete && image.naturalWidth > 0);
  }), { timeout: 30_000 }).toBe(true);
  const secondaryValue = await mainHandle!.evaluateHandle((element) => element.closest('.h-card')?.querySelector('.h-remove') ?? null);
  const secondary = secondaryValue.asElement() as ElementHandle<HTMLElement> | null;
  if (secondary) {
    await secondary.focus();
    await expect.poll(() => secondary.evaluate((element) => ({
      connected: element.isConnected,
      focused: document.activeElement === element,
      visibility: getComputedStyle(element).visibility,
    }))).toEqual({ connected: true, focused: true, visibility: 'visible' });
    expect(await mainHandle!.evaluate((element) => Boolean(element.closest('.h-card')?.querySelector('img.g-art-image.is-shown'))), 'secondary focus retained decoded card art').toBe(true);
  }
  const focusedCardReadyAtMs = await page.evaluate(() => performance.now());
  await page.evaluate(() => window.scrollTo(0, 0));
  await scrollToEnd(page);
  const visibleDecoded = () => page.evaluate(() => [...document.querySelectorAll('.h-shelf img.g-art-image.is-shown')]
    .filter((candidate) => {
      const image = candidate as HTMLImageElement;
      const rect = image.getBoundingClientRect();
      return image.complete && image.naturalWidth > 0 && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    }).length);
  await expect.poll(visibleDecoded, { timeout: 30_000 }).toBeGreaterThan(0);
  const bottomVisibleDecoded = await visibleDecoded();
  const sharpElements = await page.locator('img.g-art-image').count();
  // The next warm phase measures the initial viewport, not the bottom reached by this exercise.
  await page.evaluate(async () => {
    (document.activeElement as HTMLElement | null)?.blur();
    document.querySelectorAll<HTMLElement>('.h-row').forEach((row) => { row.scrollLeft = 0; });
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await expect.poll(() => page.evaluate(() => ({
    focusedCard: Boolean(document.activeElement?.closest('.h-card')),
    horizontal: Math.max(0, ...[...document.querySelectorAll<HTMLElement>('.h-row')].map((row) => Math.abs(row.scrollLeft))),
    vertical: window.scrollY,
  })), { message: 'initial viewport restored after scroll/focus coverage' }).toEqual({ focusedCard: false, horizontal: 0, vertical: 0 });
  return { bottomVisibleDecoded, focusedCardReadyAtMs: Math.round(focusedCardReadyAtMs), sharpElements };
}

async function preScrollPoint(page: import('@playwright/test').Page, phase: NetworkPhase) {
  const point = await page.evaluate(() => {
    const sharp = [...document.querySelectorAll('img.g-art-image')] as HTMLImageElement[];
    const decoded = sharp.filter((image) => image.classList.contains('is-shown') && image.complete && image.naturalWidth > 0);
    const visible = decoded.filter((image) => {
      const rect = image.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
    });
    const resources = (performance.getEntriesByType('resource') as PerformanceResourceTiming[]).filter((entry) => {
      const path = new URL(entry.name).pathname;
      return /^\/api\/(?:art\/(?!framing$)|artwork\/|titles\/[^/]+\/images\/|library\/[^/]+\/artwork$)/.test(path);
    });
    return {
      atMs: Math.round(performance.now()),
      decodedSharp: decoded.length,
      offscreenDecoded: decoded.length - visible.length,
      sharpElements: sharp.length,
      transferBytes: resources.reduce((total, entry) => total + entry.transferSize, 0),
      visibleDecoded: visible.length,
      viewportHeight: innerHeight,
      viewportWidth: innerWidth,
      windowScrollY: scrollY,
      decodedSlots: decoded.map((image) => {
        const rect = image.getBoundingClientRect();
        const shelf = image.closest('.h-shelf');
        const shelfRect = shelf?.getBoundingClientRect();
        const card = image.closest<HTMLElement>('.h-card');
        const row = card?.closest<HTMLElement>('.h-row');
        const gap = row ? parseFloat(getComputedStyle(row).columnGap) || 0 : 0;
        return {
          top: Math.round(rect.top), bottom: Math.round(rect.bottom),
          left: Math.round(rect.left), right: Math.round(rect.right),
          shelfTop: shelfRect ? Math.round(shelfRect.top) : null,
          shelfBottom: shelfRect ? Math.round(shelfRect.bottom) : null,
          rowPitch: card ? Math.round(card.getBoundingClientRect().width + gap) : null,
          shape: shelf?.classList.contains('is-poster') ? 'poster' : shelf?.classList.contains('is-square') ? 'square' : shelf ? 'still' : 'hero',
        };
      }),
      shelves: [...document.querySelectorAll<HTMLElement>('.h-shelf')].map((shelf, ordinal) => {
        const rect = shelf.getBoundingClientRect();
        return {
          ordinal,
          top: Math.round(rect.top),
          bottom: Math.round(rect.bottom),
          shape: shelf.classList.contains('is-poster') ? 'poster' : shelf.classList.contains('is-square') ? 'square' : 'still',
          decoded: shelf.querySelectorAll('img.g-art-image.is-shown').length,
        };
      }),
    };
  });
  const probe = await page.evaluate(() => (window as ProbeWindow).__homeImageProbe!.snapshot());
  const latestVisibleRevealAtMs = Math.max(0, ...probe.records.filter((record) => record.currentVisible).map((record) => record.revealedAtMs));
  return { ...point, activeRequests: phase.active, imageRequests: phase.issued, latestVisibleRevealAtMs: Math.round(latestVisibleRevealAtMs) };
}

test('Home visible artwork decodes within a bounded real-image pipeline, cold and warm', async ({ browser }, testInfo) => {
  test.setTimeout(240_000);
  const evidence: Record<Phase, PhaseEvidence[]> = { cold: [], warm: [] };

  for (let run = 0; run < RUNS; run += 1) {
    const page = await perfTitlesPage(browser, 'lan');
    // Cold and warm measurements share the same first-screen origin, independent of browser reload restoration.
    await page.context().addInitScript(() => { history.scrollRestoration = 'manual'; });
    await page.context().addInitScript(homeImageProbe);
    let current: Phase = 'cold';
    const network: Record<Phase, NetworkPhase> = { cold: emptyNetworkPhase(), warm: emptyNetworkPhase() };
    const requestPhase = new WeakMap<Request, Phase>();
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      const kind = routeClass(path);
      if (!kind || !IMAGE_ROUTE.test(path)) return;
      const phase = network[current];
      requestPhase.set(request, current);
      phase.issued += 1;
      phase.active += 1;
      const activeForUrl = phase.activeUrls.get(request.url()) ?? 0;
      if (activeForUrl > 0) phase.duplicateConcurrentRequests += 1;
      phase.activeUrls.set(request.url(), activeForUrl + 1);
      phase.maxActive = Math.max(phase.maxActive, phase.active);
      phase.routes[kind] = (phase.routes[kind] ?? 0) + 1;
      const selection = selectionClass(path);
      if (selection) phase.selections[selection] = (phase.selections[selection] ?? 0) + 1;
      if (phase.seen.has(request.url())) phase.duplicates += 1;
      phase.seen.add(request.url());
    });
    page.on('response', (response) => {
      const phaseName = requestPhase.get(response.request());
      if (!phaseName) return;
      const phase = network[phaseName];
      if (response.status() >= 400) phase.failures.push(`${response.status()} ${routeClass(new URL(response.url()).pathname)}`);
    });
    const finish = (request: Request) => {
      const phaseName = requestPhase.get(request);
      if (phaseName) {
        const phase = network[phaseName];
        phase.active -= 1;
        const remaining = (phase.activeUrls.get(request.url()) ?? 1) - 1;
        if (remaining) phase.activeUrls.set(request.url(), remaining);
        else phase.activeUrls.delete(request.url());
      }
    };
    page.on('requestfinished', finish);
    page.on('requestfailed', (request) => {
      const phaseName = requestPhase.get(request);
      if (phaseName) {
        const error = request.failure()?.errorText ?? '';
        if (/abort|cancel/i.test(error)) network[phaseName].requestAborted += 1;
        else network[phaseName].requestFailed += 1;
      }
      finish(request);
    });

    try {
      for (const phaseName of ['cold', 'warm'] as const) {
        current = phaseName;
        if (phaseName === 'cold') await page.goto('/');
        else await page.reload();
        const appFirstScreenMs = await appMetric(page, 'home_first_screen_ms', 'home');
        const preScroll = [await preScrollPoint(page, network[phaseName])];
        const firstViewportReady = await visibleViewportReady(page);
        for (const atMs of [1_000, 3_000, 8_000]) {
          const remaining = await page.evaluate((target) => Math.max(0, target - performance.now()), atMs);
          if (remaining) await page.waitForTimeout(remaining);
          preScroll.push(await preScrollPoint(page, network[phaseName]));
        }
        const appHeroMs = await appMetric(page, 'home_hero_ms', 'home');
        const decodedAtMs = await decodedFirstScreen(page);
        const scrollAndFocus = await exerciseScrollAndFocus(page);
        await expect.poll(() => network[phaseName].active, { timeout: 30_000 }).toBe(0);
        const snapshot = await page.evaluate(() => (window as ProbeWindow).__homeImageProbe!.snapshot());
        const resources = await page.evaluate(() => {
          const entries = (performance.getEntriesByType('resource') as PerformanceResourceTiming[]).filter((entry) => {
            const path = new URL(entry.name).pathname;
            return /^\/api\/(?:art\/(?!framing$)|artwork\/|titles\/[^/]+\/images\/|library\/[^/]+\/artwork$)/.test(path);
          });
          return {
            durations: entries.map((entry) => entry.duration),
            duplicateBodyTransfers: [...entries.reduce<Map<string, number>>((counts, entry) => {
              const status = (entry as PerformanceResourceTiming & { responseStatus?: number }).responseStatus;
              if (entry.transferSize > 0 && entry.encodedBodySize > 0 && status === 200) counts.set(entry.name, (counts.get(entry.name) ?? 0) + 1);
              return counts;
            }, new Map()).values()].reduce((total, count) => total + Math.max(0, count - 1), 0),
            encodedBytes: entries.reduce((total, entry) => total + entry.encodedBodySize, 0),
            maxEncodedBytes: Math.max(0, ...entries.map((entry) => entry.encodedBodySize)),
            protocols: entries.reduce<Record<string, number>>((counts, entry) => {
              const protocol = entry.nextHopProtocol || 'unknown';
              counts[protocol] = (counts[protocol] ?? 0) + 1;
              return counts;
            }, {}),
            transferBytes: entries.reduce((total, entry) => total + entry.transferSize, 0),
          };
        });
        const shown = snapshot.records;
        const hero = shown.filter((record) => record.hero);
        const shelf = shown.filter((record) => record.shelf);
        const waits = shown.map((record) => record.queueWaitMs);
        const decode = shown.map((record) => record.decodeMs);
        const responseToReveal = shown.flatMap((record) => record.loadEndToRevealMs === null ? [] : [record.loadEndToRevealMs]);
        const sourceCounts = await page.locator('img.g-art-image.is-shown').evaluateAll((images) => {
          const counts = new Map<string, number>();
          for (const image of images) {
            const src = (image as HTMLImageElement).currentSrc;
            if (src) counts.set(src, (counts.get(src) ?? 0) + 1);
          }
          return [...counts.values()];
        });
        const measured = network[phaseName];
        evidence[phaseName].push({
          appFirstScreenMs: Math.round(appFirstScreenMs),
          appHeroMs: Math.round(appHeroMs),
          firstScreenDecodedAtMs: Math.round(decodedAtMs),
          firstViewportReady,
          decodedHero: hero.length,
          decodedShelf: shelf.length,
          decodeMs: summary(decode),
          duplicateSharpElements: sourceCounts.reduce((total, count) => total + Math.max(0, count - 1), 0),
          duplicateConcurrentRequests: measured.duplicateConcurrentRequests,
          encodedImageBytes: resources.encodedBytes,
          imageFailures: measured.failures,
          imageBytes: resources.transferBytes,
          imageRequests: measured.issued,
          duplicateRequests: measured.duplicates,
          duplicateBodyTransfers: resources.duplicateBodyTransfers,
          maxImageRequestsInFlight: measured.maxActive,
          maxImageResourceBytes: resources.maxEncodedBytes,
          networkProtocols: resources.protocols,
          requestFailures: { aborted: measured.requestAborted, other: measured.requestFailed },
          preScroll,
          offscreenAtAttach: {
            horizontal: shown.filter((record) => record.horizontalOffscreenAtAttach).length,
            vertical: shown.filter((record) => record.verticalOffscreenAtAttach).length,
          },
          queueWaitMs: summary(waits),
          responseToRevealMs: summary(responseToReveal),
          resourceDurationMs: summary(resources.durations),
          routes: measured.routes,
          scrollAndFocus,
          selections: measured.selections,
        });
      }
    } finally {
      await page.context().close();
    }
  }

  const target = testInfo.outputPath('home-image-evidence.json');
  await writeFile(target, `${JSON.stringify({ runs: RUNS, profile: 'lan-4x-cpu', evidence }, null, 2)}\n`);
  await testInfo.attach('home-image-evidence', { path: target, contentType: 'application/json' });
  console.log(`Home images cold viewport ready ${evidence.cold.map((run) => run.firstViewportReady.atMs).join(', ')} ms; warm ${evidence.warm.map((run) => run.firstViewportReady.atMs).join(', ')} ms`);
  const phases = [...evidence.cold, ...evidence.warm];
  expect(phases.flatMap((phase) => phase.imageFailures), 'real image responses >= 400').toEqual([]);
  for (const phase of phases) {
    const imageCap = Object.keys(phase.networkProtocols).some((protocol) => /^h[23]$/.test(protocol)) ? 12 : 4;
    expect(phase.maxImageRequestsInFlight, `actual image requests concurrently in flight over ${JSON.stringify(phase.networkProtocols)}`).toBeLessThanOrEqual(imageCap);
  }
  expect(Math.max(...phases.map((phase) => phase.maxImageResourceBytes)), 'one decoded image exceeded the 4 MiB rendition limit').toBeLessThanOrEqual(4 * 1024 * 1024);
  expect(phases.flatMap((phase) => phase.duplicateConcurrentRequests), 'same image URL requested concurrently during one Home phase').toEqual(phases.map(() => 0));
  expect(phases.flatMap((phase) => phase.duplicateBodyTransfers), 'same image body transferred twice during one Home phase').toEqual(phases.map(() => 0));
  expect(Math.min(...phases.map((phase) => phase.decodedHero)), 'decoded hero artwork').toBeGreaterThan(0);
  expect(Math.min(...phases.map((phase) => phase.decodedShelf)), 'decoded shelf artwork').toBeGreaterThanOrEqual(4);
  for (const phase of phases) {
    const atThreeSeconds = phase.preScroll[2];
    const outsideAdmissionBand = atThreeSeconds.decodedSlots.filter((slot) => {
      if (slot.shape === 'hero') {
        return slot.bottom < -1 || slot.top >= atThreeSeconds.viewportHeight + 1
          || slot.right < -1 || slot.left >= atThreeSeconds.viewportWidth + 1;
      }
      if (slot.shelfTop === null || slot.shelfBottom === null || slot.rowPitch === null) return true;
      return slot.shelfBottom < -1 || slot.shelfTop >= atThreeSeconds.viewportHeight * 2 + 1
        || slot.right < -2 * slot.rowPitch - 1
        || slot.left >= atThreeSeconds.viewportWidth + 2 * slot.rowPitch + 1;
    });
    expect(
      outsideAdmissionBand,
      `pre-scroll decoded images should stay within the viewport plus one vertical viewport and two horizontal cards: ${JSON.stringify(atThreeSeconds)}`,
    ).toEqual([]);
  }
});

test('Home high-density stills select a bounded rendition instead of an original', async ({ browser }, testInfo) => {
  const page = await perfTitlesPage(browser, 'lan', { deviceScaleFactor: 2 });
  const selections: Record<string, number> = {};
  const failures: string[] = [];
  page.on('request', (request) => {
    const selection = selectionClass(new URL(request.url()).pathname);
    if (selection) selections[selection] = (selections[selection] ?? 0) + 1;
  });
  page.on('response', (response) => {
    const path = new URL(response.url()).pathname;
    if (IMAGE_ROUTE.test(path) && response.status() >= 400) failures.push(`${response.status()} ${routeClass(path)}`);
  });
  try {
    await page.goto('/');
    await appMetric(page, 'home_first_screen_ms', 'home');
    await decodedFirstScreen(page);
    const target = testInfo.outputPath('home-image-dpr2-evidence.json');
    await writeFile(target, `${JSON.stringify({ deviceScaleFactor: 2, failures, selections }, null, 2)}\n`);
    await testInfo.attach('home-image-dpr2-evidence', { path: target, contentType: 'application/json' });
    expect(failures, 'real image responses >= 400').toEqual([]);
    expect(selections['original/Primary'] ?? 0, `DPR2 selected original Primary artwork: ${JSON.stringify(selections)}`).toBe(0);
    expect((selections['rendition/Primary/800'] ?? 0) + (selections['rendition/Primary/1200'] ?? 0), `DPR2 selected no high-density Primary rendition: ${JSON.stringify(selections)}`).toBeGreaterThan(0);
  } finally {
    await page.context().close();
  }
});
