// Browser-side household measurement against a running perf backend (see load.py --ui).
//   node scripts/perf/ui.mjs http://127.0.0.1:8799 LIBRARY_ITEM_ID_WITH_TRANSCRIPT
// Prints cold-load paint timings, per-route client transition time, DOM size and JS heap.
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const base = process.argv[2];
const PASSWORD = 'perf-household-password';
const BUSY = '.surface-loading, .loading-grid, .library-loading, .transcript-state';

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1536, height: 960 } });
const login = await context.request.post(`${base}/api/session/login`, {
  data: { username: 'perfadmin', password: PASSWORD },
  headers: { Origin: base },
});
if (!login.ok()) throw new Error(`login failed: ${login.status()}`);
const watchId = process.argv[3];

const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
await page.addInitScript(() => {
  window.__lcp = 0;
  new PerformanceObserver((list) => { for (const entry of list.getEntries()) window.__lcp = entry.startTime; })
    .observe({ type: 'largest-contentful-paint', buffered: true });
});
const cdp = await context.newCDPSession(page);
await cdp.send('Performance.enable');

async function settled() {
  await page.waitForTimeout(50);
  await page.waitForFunction((selector) => document.querySelector('main h1') && !document.querySelector(selector), BUSY, { timeout: 30_000 });
}

async function heapMb() {
  await cdp.send('HeapProfiler.collectGarbage');
  const { metrics } = await cdp.send('Performance.getMetrics');
  return (metrics.find((metric) => metric.name === 'JSHeapUsedSize').value / 1048576).toFixed(1);
}

const domNodes = () => page.evaluate(() => document.getElementsByTagName('*').length);

const started = Date.now();
await page.goto(`${base}/`);
await settled();
const ready = Date.now() - started;
const paint = await page.evaluate(() => ({
  fcp: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0,
  lcp: window.__lcp,
  dcl: performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd,
  js: performance.getEntriesByType('resource').filter((entry) => entry.name.endsWith('.js')).reduce((sum, entry) => sum + entry.transferSize, 0),
}));
console.log(`cold Home: FCP ${paint.fcp.toFixed(0)}ms, LCP ${paint.lcp.toFixed(0)}ms, DCL ${paint.dcl.toFixed(0)}ms, shelves ready ${ready}ms, JS transferred ${(paint.js / 1024).toFixed(0)}KB`);

await page.waitForTimeout(2000);  // a member reads Home before moving on; idle preloads land here
console.log('route                         ready ms  DOM nodes  heap MB');
const routes = ['/library', '/music', '/subscriptions', '/downloads', '/explore?q=river', '/settings', '/admin',
  '/admin/tasks', '/admin/members', '/admin/diagnostics', `/watch/library/${watchId}`, '/'];
for (const path of routes) {
  const t0 = Date.now();
  // Client-side navigation, as the app's own links do.
  await page.evaluate((target) => { history.pushState({}, '', target); dispatchEvent(new PopStateEvent('popstate')); }, path);
  await settled();
  console.log(`${path.slice(0, 28).padEnd(29)} ${String(Date.now() - t0).padStart(8)}  ${String(await domNodes()).padStart(9)}  ${(await heapMb()).padStart(7)}`);
}

// Transcript panel on a 1,000-cue transcript: pages load as playback needs them.
await page.evaluate((target) => { history.pushState({}, '', target); dispatchEvent(new PopStateEvent('popstate')); }, `/watch/library/${watchId}`);
await settled();
const t2 = Date.now();
await page.getByRole('tab', { name: 'Transcript' }).click();
await page.locator('.transcript-cues li').first().waitFor();
console.log(`transcript tab: first cues ${Date.now() - t2}ms, ${await page.locator('.transcript-cues li').count()} cues rendered, ${await domNodes()} DOM nodes`);

// Long list: scroll the Library grid through 30 pages (infinite scroll, "Load more" when it pauses).
const renderMs = async () => {
  const { metrics } = await cdp.send('Performance.getMetrics');
  const value = (name) => metrics.find((metric) => metric.name === name).value;
  return (value('LayoutDuration') + value('RecalcStyleDuration') + value('ScriptDuration')) * 1000;
};
await page.evaluate(() => { history.pushState({}, '', '/library'); dispatchEvent(new PopStateEvent('popstate')); });
await settled();
const loadMore = page.getByRole('button', { name: 'Load more' });
const cards = () => page.locator('.collection-sentinel').evaluate((node) => node.closest('section, main')?.querySelectorAll('article').length ?? 0);
const before = await renderMs();
const t1 = Date.now();
for (let step = 0; step < 30; step += 1) {
  if (await loadMore.isVisible().catch(() => false)) await loadMore.click();
  await page.locator('.collection-sentinel').scrollIntoViewIfNeeded();
  await settled();
}
const spent = Date.now() - t1;
const scrollStart = Date.now();
await page.evaluate(async () => {  // one smooth pass back to the top, as a member would scroll
  for (let y = document.scrollingElement.scrollTop; y > 0; y -= 600) { window.scrollTo(0, y); await new Promise(requestAnimationFrame); }
});
console.log(`library scrolled 30 pages: ${await cards()} cards, ${await domNodes()} DOM nodes, heap ${await heapMb()}MB, `
  + `${(spent / 30).toFixed(0)}ms per page, main-thread ${(await renderMs() - before).toFixed(0)}ms, scroll-to-top ${Date.now() - scrollStart}ms`);
console.log(errors.length ? `page errors: ${errors.join(' | ')}` : 'no page errors');
await browser.close();
