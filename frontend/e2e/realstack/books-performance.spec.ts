/** Real archives and decoded pages; raw observations, not host-dependent timing gates. */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { Page, TestInfo } from '@playwright/test';

import { OWNER_STATE, expect, test } from './realstack';

type Format = 'epub' | 'pdf' | 'cbz' | 'cbr';
const formats: Format[] = ['epub', 'pdf', 'cbz', 'cbr'];
const pages = 120;
const slow = process.env.BOOKS_PERF_SLOW === '1';

async function mutationHeaders(page: Page, origin: string) {
  const session = await page.request.get('/api/session/me');
  expect(session.ok()).toBe(true);
  const state = await session.json() as { csrf_token: string };
  return { Origin: origin, 'X-CSRF-Token': state.csrf_token };
}

async function writeEvidence(info: TestInfo, name: string, value: object) {
  const target = info.outputPath(name);
  writeFileSync(target, JSON.stringify(value, null, 2) + '\n');
  await info.attach(name, { path: target, contentType: 'application/json' });
}

async function contentReady(page: Page, format: Format, index: number) {
  await expect(page.getByLabel('Reading position')).toHaveValue(String(index));
  if (format === 'epub') {
    await expect(page.locator('.books-prose')).toContainText(`Chapter ${index + 1}`);
  } else {
    const image = page.getByAltText(`Page ${index + 1} of ${pages}`);
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
    // The entire archive must never become a wall of decoded image elements.
    expect(await page.locator('.books-reader-page img').count()).toBeLessThanOrEqual(2);
  }
}

test('120-unit EPUB, PDF, CBZ and CBR startup, navigation and resume resource observations', async ({ page, browser, baseURL }, info) => {
  test.setTimeout(slow ? 360_000 : 180_000);
  const headers = await mutationHeaders(page, baseURL!);
  const fixtureRoot = mkdtempSync(path.join(tmpdir(), 'lumina-books-perf-'));
  const observations: object[] = [];
  try {
    execFileSync('../backend/.venv/bin/python', ['../scripts/perf/audit_books.py', '--fixtures-only', fixtureRoot, '--pages', String(pages)], { timeout: 60_000 });
    for (const format of formats) {
      const imported = await page.request.put(`/api/books/import?filename=audit.${format}`, {
        data: readFileSync(path.join(fixtureRoot, `audit.${format}`)),
        headers: { 'Content-Type': 'application/octet-stream', ...headers },
      });
      expect(imported.status(), await imported.text()).toBe(201);
      const book = await imported.json() as { id: string };
      for (let trial = 1; trial <= 3; trial++) {
        const reset = await page.request.patch(`/api/books/${book.id}/state`, { data: { unit_index: 0, offset: 0, completed: false }, headers });
        expect(reset.ok()).toBe(true);
        const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1440, height: 900 } });
        try {
          const reader = await context.newPage();
          // Record the first real content independently of goto(load), font loading and assertion polling.
          await reader.addInitScript(() => {
            const complete = () => {
              performance.mark('books-first-content');
              observer.disconnect();
              document.removeEventListener('load', imageLoaded, true);
            };
            const imageLoaded = (event: Event) => {
              const image = event.target;
              if (image instanceof HTMLImageElement && image.closest('.books-manga-pages') && image.complete && image.naturalWidth > 0) complete();
            };
            const observer = new MutationObserver(() => {
              if (document.querySelector('.books-prose')) complete();
            });
            observer.observe(document, { childList: true, subtree: true });
            document.addEventListener('load', imageLoaded, true);
          });
          const cdp = await context.newCDPSession(reader);
          await cdp.send('Performance.enable');
          if (slow) {
            await cdp.send('Network.enable');
            await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 40, downloadThroughput: 2_500_000 / 8, uploadThroughput: 1_000_000 / 8 });
            await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
          }
          const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.filter((metric) => ['JSHeapUsedSize', 'Nodes', 'TaskDuration', 'ScriptDuration', 'LayoutCount'].includes(metric.name)).map((metric) => [metric.name, metric.value]));
          const initialMetrics = await metrics();
          const errors: string[] = [];
          const responses: { resource: string; status: number; bytes: number }[] = [];
          let saves = 0;
          const pageRequests: Record<string, number> = {};
          reader.on('pageerror', (error) => errors.push(error.message));
          reader.on('request', (request) => {
            if (request.method() === 'PATCH' && request.url().includes('/state')) saves++;
            const match = new URL(request.url()).pathname.match(/\/pages\/(\d+)$/);
            if (match) pageRequests[match[1]] = (pageRequests[match[1]] ?? 0) + 1;
          });
          reader.on('response', (response) => {
            const resource = new URL(response.url()).pathname.replace(book.id, 'fixture');
            if (resource.startsWith('/api/books/')) responses.push({ resource, status: response.status(), bytes: Number(response.headers()['content-length'] || 0) });
          });
          const started = performance.now();
          await reader.goto(`/books/read/${book.id}`);
          await contentReady(reader, format, 0);
          const startup = performance.now() - started;
          const firstContent = await reader.evaluate(() => performance.getEntriesByName('books-first-content')[0]?.startTime ?? null);
          const startupResources = await reader.evaluate(() => performance.getEntriesByType('resource').map((entry) => {
            const resource = entry as PerformanceResourceTiming;
            return { path: new URL(resource.name).pathname, initiator: resource.initiatorType, start_ms: resource.startTime, duration_ms: resource.duration, transfer_bytes: resource.transferSize, encoded_bytes: resource.encodedBodySize };
          }));
          const turnMs: number[] = [];
          for (let index = 1; index <= 4; index++) {
            const at = performance.now();
            await reader.getByRole('button', { name: 'Next', exact: true }).click();
            await contentReady(reader, format, index);
            turnMs.push(performance.now() - at);
          }
          // A reader normally spends time on each page; measure a settled turn separately
          // from the deliberately rapid sequence above, so lookahead cannot hide stalls.
          await reader.waitForTimeout(1_000);
          const settledAt = performance.now();
          await reader.getByRole('button', { name: 'Next', exact: true }).click();
          await contentReady(reader, format, 5);
          const settledTurn = performance.now() - settledAt;
          const navigation = reader.getByRole('button', { name: format === 'epub' ? 'Chapters' : 'Pages', exact: true });
          if (await navigation.getAttribute('aria-expanded') === 'false') await navigation.click();
          const lateAt = performance.now();
          if (format === 'epub') await reader.getByLabel('Table of contents').locator('ol button').nth(110).click();
          else {
            await reader.getByLabel('Go to page', { exact: true }).fill('111');
            await reader.getByRole('button', { name: 'Go to page', exact: true }).click();
          }
          await contentReady(reader, format, 110);
          const latePage = performance.now() - lateAt;
          await expect.poll(async () => ((await (await reader.request.get(`/api/books/${book.id}/reader`)).json()) as { book: { progress: { unit_index: number } } }).book.progress.unit_index).toBe(110);
          const resumeAt = performance.now();
          await reader.reload();
          const resumeReload = performance.now() - resumeAt;
          await contentReady(reader, format, 110);
          const resumed = performance.now() - resumeAt;
          const resumeContent = await reader.evaluate(() => performance.getEntriesByName('books-first-content')[0]?.startTime ?? null);
          const heap = await reader.evaluate(() => (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? null);
          expect(errors).toEqual([]);
          expect(responses.filter((response) => response.status >= 500)).toEqual([]);
          const resources = await reader.evaluate(() => performance.getEntriesByType('resource').map((entry) => {
            const resource = entry as PerformanceResourceTiming;
            return { path: new URL(resource.name).pathname, initiator: resource.initiatorType, start_ms: resource.startTime, duration_ms: resource.duration, transfer_bytes: resource.transferSize, encoded_bytes: resource.encodedBodySize, decoded_bytes: resource.decodedBodySize };
          }));
          // Reload has a fresh reader window; ordinary promotion of a prefetched page must not refetch it.
          for (const [index, requests] of Object.entries(pageRequests)) expect(requests).toBeLessThanOrEqual(Number(index) >= 110 ? 2 : 1);
          observations.push({ format, trial, units: pages, first_content_navigation_ms: firstContent, resume_content_navigation_ms: resumeContent, resume_reload_ms: resumeReload, resume_ready_ms: resumed, startup_resources: startupResources, page_requests: pageRequests, settled_turn_ms: settledTurn, resources, startup_ms: startup, turns_ms: turnMs, late_page_ms: latePage, resume_ms: resumed, state_writes: saves, responses, cdp_initial: initialMetrics, cdp_final: await metrics(), js_heap_bytes_supplement_only: heap });
        } finally { await context.close(); }
      }
    }
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
    await writeEvidence(info, 'books-performance.json', { browser: browser.version(), network: slow ? '2.5 Mbps down / 1 Mbps up / 40 ms latency, 4x CPU' : 'unshaped loopback', cache: 'fresh browser contexts; server cache warm after first access; OS cache uncontrolled', observations });
  }
});

// Entering from a large series can compete with its still-loading CBR covers.
// Unlike goto(waitUntil=load), this deliberately does not wait for those covers.
test('opening a comic while a series cover grid is loading', async ({ page, browser, baseURL }, info) => {
  test.setTimeout(slow ? 360_000 : 180_000);
  const headers = await mutationHeaders(page, baseURL!);
  const fixtureRoot = mkdtempSync(path.join(tmpdir(), 'lumina-books-grid-perf-'));
  try {
    execFileSync('../backend/.venv/bin/python', ['../scripts/perf/audit_books.py', '--fixtures-only', fixtureRoot, '--pages', '28'], { timeout: 60_000 });
    const body = readFileSync(path.join(fixtureRoot, 'audit.cbr'));
    let seriesId = '';
    for (let volume = 1; volume <= 20; volume++) {
      const imported = await page.request.put(`/api/books/import?filename=grid-volume-${volume}.cbr`, { data: body, headers: { 'Content-Type': 'application/octet-stream', ...headers } });
      expect(imported.status(), await imported.text()).toBe(201);
      const book = await imported.json() as { id: string };
      const edited = await page.request.patch(`/api/books/${book.id}`, { data: { title: `Grid volume ${volume}`, series_name: 'Synthetic cover grid', author: 'Grid benchmark', volume_number: volume }, headers });
      expect(edited.ok(), await edited.text()).toBe(true);
      seriesId = ((await edited.json()) as { series_id: string }).series_id;
    }
    const samples: object[] = [];
    for (let trial = 1; trial <= 3; trial++) {
      const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1440, height: 900 } });
      try {
        const reader = await context.newPage();
        const pending = new Set<import('@playwright/test').Request>();
        let coversStarted = 0;
        reader.on('request', (request) => { if (new URL(request.url()).pathname.endsWith('/cover')) { pending.add(request); coversStarted++; } });
        reader.on('requestfinished', (request) => pending.delete(request));
        reader.on('requestfailed', (request) => pending.delete(request));
        await reader.goto(`/books/series/${seriesId}`, { waitUntil: 'domcontentloaded' });
        const read = reader.getByRole('button', { name: /^(Read|Continue) volume 1$/i });
        await expect(read).toBeVisible();
        const pendingAtClick = pending.size;
        await read.evaluate((button) => {
          const state = window as typeof window & { __booksPerfPhases?: { fontAtArm: FontFaceSetLoadStatus; fontAtClick?: FontFaceSetLoadStatus } };
          performance.clearMarks('books-perf-arm');
          performance.clearMarks('books-perf-actual-click');
          performance.clearMarks('books-perf-page-image-load');
          state.__booksPerfPhases = { fontAtArm: document.fonts.status };
          performance.mark('books-perf-arm');
          button.addEventListener('click', () => {
            state.__booksPerfPhases!.fontAtClick = document.fonts.status;
            performance.mark('books-perf-actual-click');
          }, { capture: true, once: true });
          const loaded = (event: Event) => {
            const image = event.target;
            if (!(image instanceof HTMLImageElement) || image.alt !== 'Page 1 of 28' || !image.complete || image.naturalWidth <= 0) return;
            performance.mark('books-perf-page-image-load');
            document.removeEventListener('load', loaded, true);
          };
          document.addEventListener('load', loaded, true);
        });
        const started = performance.now();
        await read.click();
        const image = reader.getByAltText('Page 1 of 28');
        await expect(image).toBeVisible();
        await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
        const phases = await reader.evaluate(() => {
          const state = window as typeof window & { __booksPerfPhases?: { fontAtArm: FontFaceSetLoadStatus; fontAtClick?: FontFaceSetLoadStatus } };
          const mark = (name: string) => performance.getEntriesByName(name, 'mark').at(-1)?.startTime;
          const arm = mark('books-perf-arm');
          const click = mark('books-perf-actual-click');
          const loaded = mark('books-perf-page-image-load');
          if (arm === undefined || click === undefined || loaded === undefined) throw new Error('Comic opening phase mark was not captured');
          const resources = performance.getEntriesByType('resource').flatMap((entry) => {
            const resource = entry as PerformanceResourceTiming;
            const pathname = new URL(resource.name).pathname;
            const kind = pathname.endsWith('/reader') ? 'reader' : pathname.endsWith('/pages/0') ? 'page_0' : null;
            if (kind === null || !pathname.startsWith('/api/books/')) return [];
            return [{
              resource: `/api/books/fixture/${kind === 'reader' ? 'reader' : 'pages/0'}`,
              start_offset_from_actual_click_ms: resource.startTime - click,
              response_end_offset_from_actual_click_ms: resource.responseEnd - click,
              duration_ms: resource.duration,
            }];
          });
          return {
            actual_click_to_image_load_ms: loaded - click,
            arm_to_actual_click_ms: click - arm,
            image_load_to_observed_ms: performance.now() - loaded,
            font_status_at_arm: state.__booksPerfPhases?.fontAtArm ?? null,
            font_status_at_actual_click: state.__booksPerfPhases?.fontAtClick ?? null,
            reader_page_request_timings: resources,
          };
        });
        samples.push({ trial, volumes: 20, pages_per_volume: 28, cbr_bytes_per_volume: body.length, pending_cover_requests_at_click: pendingAtClick, covers_started: coversStarted, click_to_decoded_page_ms: performance.now() - started, ...phases });
      } finally { await context.close(); }
    }
    // Exercise the queued rendition/retry path through the real UI, including initially off-screen cards.
    const covers = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1440, height: 900 } });
    const coverDimensions: { width: number; height: number }[] = [];
    try {
      const gallery = await covers.newPage();
      await gallery.goto(`/books/series/${seriesId}`, { waitUntil: 'domcontentloaded' });
      const cards = gallery.locator('.books-volume .books-cover-button');
      await expect(cards).toHaveCount(20);
      for (let index = 0; index < 20; index++) {
        const card = cards.nth(index);
        await card.scrollIntoViewIfNeeded();
        await expect.poll(() => card.locator('img').evaluateAll((images) => images.some((node) => {
          const image = node as HTMLImageElement;
          return image.complete && image.naturalWidth > 0 && Math.max(image.naturalWidth, image.naturalHeight) <= 600;
        })), { timeout: 45_000 }).toBe(true);
        coverDimensions.push(await card.locator('img').evaluate((node: HTMLImageElement) => ({ width: node.naturalWidth, height: node.naturalHeight })));
      }
    } finally { await covers.close(); }
    await writeEvidence(info, 'books-cover-contention.json', { browser: browser.version(), network: 'unshaped loopback', server_cache: 'warm after first access; no restart between contexts', samples, cover_dimensions: coverDimensions });
  } finally { rmSync(fixtureRoot, { recursive: true, force: true }); }
});
