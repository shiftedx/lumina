// Browser-side household measurement against a running perf backend (see load.py --ui).
//   node scripts/perf/ui.mjs http://127.0.0.1:8799 LIBRARY_ITEM_ID_WITH_TRANSCRIPT [--out ui.json]
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const argv = process.argv.slice(2);
const base = argv.shift();
const watchId = argv.shift();
let output = null;
while (argv.length) {
  const option = argv.shift();
  if (option === '--out') output = argv.shift() ?? null;
  else throw new Error(`unknown option: ${option}`);
}
if (!base || !watchId) throw new Error('usage: ui.mjs BASE_URL LIBRARY_ITEM_ID_WITH_TRANSCRIPT [--out FILE]');

const PASSWORD = 'perf-household-password';
const BUSY = '.surface-loading, .loading-grid, .library-loading, .transcript-state';
const result = { scope: 'synthetic household browser workflow; no media playback or provider traffic', routes: [], errors: [] };
let phase = 'launch';
let browser;
const writeResult = async () => { if (output) await writeFile(output, `${JSON.stringify(result, null, 2)}\n`); };

try {
  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1536, height: 960 } });
  phase = 'login';
  const login = await context.request.post(`${base}/api/session/login`, { data: { username: 'perfadmin', password: PASSWORD }, headers: { Origin: base } });
  if (!login.ok()) throw new Error(`login failed: ${login.status()}`);

  // Verify the data precondition outside the UI timing. A missing transcript
  // used to become a misleading 30-second tab selector timeout.
  phase = 'transcript-preflight';
  const transcriptResponse = await context.request.get(`${base}/api/library/${encodeURIComponent(watchId)}/transcripts`);
  if (!transcriptResponse.ok()) throw new Error(`transcript fixture unavailable: ${transcriptResponse.status()}`);
  const transcriptRows = await transcriptResponse.json();
  if (!Array.isArray(transcriptRows) || transcriptRows.length === 0) throw new Error('transcript fixture has no visible transcript');
  result.transcript_preflight = { visible_transcripts: transcriptRows.length };

  const page = await context.newPage();
  page.on('pageerror', (error) => result.errors.push(error.message));
  await page.addInitScript(() => {
    window.__lcp = 0;
    new PerformanceObserver((list) => { for (const entry of list.getEntries()) window.__lcp = entry.startTime; })
      .observe({ type: 'largest-contentful-paint', buffered: true });
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send('Performance.enable');
  const settled = async () => {
    await page.waitForTimeout(50);
    await page.waitForFunction((selector) => document.querySelector('main h1') && !document.querySelector(selector), BUSY, { timeout: 30_000 });
  };
  const heapMb = async () => {
    await cdp.send('HeapProfiler.collectGarbage');
    const { metrics } = await cdp.send('Performance.getMetrics');
    return Number((metrics.find((metric) => metric.name === 'JSHeapUsedSize').value / 1048576).toFixed(1));
  };
  const domNodes = () => page.evaluate(() => document.getElementsByTagName('*').length);
  const navigate = (target) => page.evaluate((path) => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }, target);

  phase = 'cold-home';
  const started = Date.now();
  await page.goto(`${base}/`);
  await settled();
  const paint = await page.evaluate(() => ({
    fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0,
    lcp: window.__lcp,
    dcl: performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd,
    js: performance.getEntriesByType('resource').filter((entry) => entry.name.endsWith('.js')).reduce((sum, entry) => sum + entry.transferSize, 0),
  }));
  result.cold_home = { fcp_ms: Math.round(paint.fcp), lcp_ms: Math.round(paint.lcp), dcl_ms: Math.round(paint.dcl), shelves_ready_ms: Date.now() - started, js_transferred_bytes: paint.js };
  console.log(`cold Home: FCP ${paint.fcp.toFixed(0)}ms, LCP ${paint.lcp.toFixed(0)}ms, DCL ${paint.dcl.toFixed(0)}ms, shelves ready ${result.cold_home.shelves_ready_ms}ms, JS transferred ${(paint.js / 1024).toFixed(0)}KB`);

  await page.waitForTimeout(2000);
  console.log('route                         ready ms  DOM nodes  heap MB');
  const routes = ['/library', '/music', '/subscriptions', '/downloads', '/explore?q=river', '/settings', '/admin', '/admin/tasks', '/admin/members', '/admin/diagnostics', `/watch/library/${watchId}`, '/'];
  phase = 'routes';
  for (const path of routes) {
    const t0 = Date.now();
    await navigate(path); await settled();
    const route = { path, ready_ms: Date.now() - t0, dom_nodes: await domNodes(), heap_mb: await heapMb() };
    result.routes.push(route);
    console.log(`${path.slice(0, 28).padEnd(29)} ${String(route.ready_ms).padStart(8)}  ${String(route.dom_nodes).padStart(9)}  ${String(route.heap_mb).padStart(7)}`);
  }

  // The tab only appears after the visible-transcript request resolves; scope
  // it to the Watch tools tablist so unrelated tabs cannot satisfy readiness.
  phase = 'transcript-tab';
  await navigate(`/watch/library/${watchId}`); await settled();
  const transcriptTab = page.getByRole('tablist', { name: 'Video tools' }).getByRole('tab', { name: 'Transcript', exact: true });
  await transcriptTab.waitFor({ state: 'visible', timeout: 30_000 });
  const t2 = Date.now();
  await transcriptTab.click();
  const transcriptCues = page.getByRole('list', { name: 'Transcript' }).getByRole('listitem');
  await transcriptCues.first().waitFor({ state: 'visible', timeout: 30_000 });
  result.transcript = { first_cues_ms: Date.now() - t2, rendered_cues: await transcriptCues.count(), dom_nodes: await domNodes() };
  console.log(`transcript tab: first cues ${result.transcript.first_cues_ms}ms, ${result.transcript.rendered_cues} cues rendered, ${result.transcript.dom_nodes} DOM nodes`);

  const renderMs = async () => {
    const { metrics } = await cdp.send('Performance.getMetrics');
    const value = (name) => metrics.find((metric) => metric.name === name).value;
    return (value('LayoutDuration') + value('RecalcStyleDuration') + value('ScriptDuration')) * 1000;
  };
  phase = 'library-scroll';
  // The All landing is chaptered, not an unbounded collection. Exercise the
  // actual paged still wall instead of its retired collection sentinel.
  await navigate('/library/youtube'); await settled();
  const sentinel = page.locator('.g-sentinel');
  const cards = () => page.locator('.g-still-card').count();
  await page.locator('.g-still-card').first().waitFor({ state: 'visible', timeout: 30_000 });
  const before = await renderMs();
  const t1 = Date.now();
  for (let step = 0; step < 30; step += 1) {
    const priorCards = await cards();
    await sentinel.scrollIntoViewIfNeeded();
    await page.waitForFunction((previous) => document.querySelectorAll('.g-still-card').length > previous, priorCards, { timeout: 30_000 });
  }
  const spent = Date.now() - t1;
  const scrollStart = Date.now();
  await page.evaluate(async () => { for (let y = document.scrollingElement.scrollTop; y > 0; y -= 600) { window.scrollTo(0, y); await new Promise(requestAnimationFrame); } });
  result.library_scroll = { surface: '/library/youtube', cards: await cards(), dom_nodes: await domNodes(), heap_mb: await heapMb(), average_page_ready_ms: Number((spent / 30).toFixed(1)), main_thread_ms: Number((await renderMs() - before).toFixed(1)), scroll_to_top_ms: Date.now() - scrollStart };
  console.log(`library scrolled 30 pages: ${result.library_scroll.cards} cards, ${result.library_scroll.dom_nodes} DOM nodes, heap ${result.library_scroll.heap_mb}MB, ${result.library_scroll.average_page_ready_ms.toFixed(0)}ms per page, main-thread ${result.library_scroll.main_thread_ms.toFixed(0)}ms, scroll-to-top ${result.library_scroll.scroll_to_top_ms}ms`);
  result.ok = result.errors.length === 0;
} catch (error) {
  result.ok = false;
  result.fatal = { phase, error: error instanceof Error ? error.name : typeof error };
  console.error(`ui audit failed at ${phase}: ${result.fatal.error}`);
} finally {
  if (browser) await browser.close();
  await writeResult();
}
console.log(result.errors.length ? `page errors: ${result.errors.join(' | ')}` : 'no page errors');
if (!result.ok) process.exitCode = 1;
