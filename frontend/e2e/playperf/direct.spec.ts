/**
 * `make playperf`: click -> first frame of DIRECT play and the start path every mode shares, each from a cold browser
 * cache (a new context per run), with the request waterfall printed (PLAYPERF-WF lines, ms after the click).
 * Budgets (LAN, p50 of RUNS): start < 1000 ms, resume < 1500 ms; DIRECT_BUDGET / RESUME_BUDGET override them.
 */
import { type ChildProcess, spawn } from 'node:child_process';
import { firstFrameProbe, NETWORK as PROFILES, openWatch, RUNS } from '../realstack/perfProbe';
import { expect, libraryItemId, OWNER_STATE, test } from '../realstack/realstack';
import type { Page } from '@playwright/test';

const DIRECT_BUDGET = Number(process.env.DIRECT_BUDGET ?? 1000);
const RESUME_BUDGET = Number(process.env.RESUME_BUDGET ?? 1500);
// `rtt`: 40 ms of latency and all the bandwidth, which counts round trips; the shared `slow` profile's 20 Mbit cap makes the first
// frame wait on the first megabyte instead, whatever the app does.
const NETWORK = { lan: PROFILES.lan, rtt: { ...PROFILES.slow, downloadThroughput: -1, uploadThroughput: -1 } };
type Profile = keyof typeof NETWORK;
const SETTLE = Number(process.env.PLAYPERF_SETTLE ?? 2500); // the app preloads the Watch surface 1.5 s after sign-in; a person rarely clicks sooner
type W = Window & { __perfFirstFrame?: number };

type Hit = { ttfb?: number; info?: string; at: number; method: string; status?: number; path: string; range?: string; end?: number; type: string };

/** One cold start: returns click -> first frame ms and the waterfall. */
async function coldStart(browser: import('@playwright/test').Browser, baseURL: string, profile: Profile, title: string, query = '', opts: { episode?: boolean; deep?: boolean; settle?: number } = {}) {
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE });
  await context.addInitScript(firstFrameProbe);
  await context.addInitScript(() => { // music has no frame: the first sound is the audio element's first `playing`
    document.addEventListener('playing', (event) => { if (event.target instanceof HTMLAudioElement) (window as W).__perfFirstFrame ??= performance.now(); }, true);
  });
  if (process.env.PLAYPERF_LOAF) await context.addInitScript(() => { // long animation frames with their scripts: where the main thread went
    const frames: unknown[] = [];
    (window as unknown as { __loaf: unknown[] }).__loaf = frames;
    new PerformanceObserver((list) => frames.push(...list.getEntries().map((entry) => entry.toJSON()))).observe({ type: 'long-animation-frame', buffered: true });
    const marks: string[] = [];
    (window as unknown as { __marks: string[] }).__marks = marks;
    const desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src')!;
    Object.defineProperty(HTMLMediaElement.prototype, 'src', { ...desc, set(value: string) { marks.push(`${Math.round(performance.now())} video.src=${value.slice(-30)}`); desc.set!.call(this, value); } });
    const append = Node.prototype.appendChild;
    Node.prototype.appendChild = function appendChild<T extends Node>(this: Node, node: T): T { if (node instanceof HTMLVideoElement) marks.push(`${Math.round(performance.now())} video appended`); return append.call(this, node) as T; };
    for (const type of ['loadstart', 'loadedmetadata', 'loadeddata', 'canplay', 'playing']) document.addEventListener(type, (event) => { if (event.target instanceof HTMLVideoElement) marks.push(`${Math.round(performance.now())} ${type}`); }, true);
    new MutationObserver((records) => { for (const record of records) if (record.target instanceof HTMLVideoElement && record.attributeName === 'src') marks.push(`${Math.round(performance.now())} src attr`); }).observe(document, { subtree: true, attributes: true, attributeFilter: ['src'] });
    let last = performance.now();
    const tick = () => { const now = performance.now(); if (now - last > 40) marks.push(`${Math.round(last)} FRAME GAP ${Math.round(now - last)} ms`); last = now; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, ...NETWORK[profile] });
  const dbg: string[] = [];
  if (process.env.PLAYPERF_LOAF) {
    const types = new Map<string, string>();
    cdp.on('Network.requestWillBeSent', (e) => { if (e.type === 'Media') { types.set(e.requestId, e.request.headers.Range ?? ''); dbg.push(`${Date.now() % 100000} media request ${e.request.headers.Range}`); } });
    cdp.on('Network.responseReceived', (e) => { if (types.has(e.requestId)) dbg.push(`${Date.now() % 100000} media response ${e.response.status} protocol=${e.response.protocol} conn=${e.response.connectionId} reused=${e.response.connectionReused}`); });
    cdp.on('Network.dataReceived', (e) => { if (types.has(e.requestId)) dbg.push(`${Date.now() % 100000} media data ${e.dataLength}`); });
  }
  try {
    const id = await resolveId(page, title, opts.episode || opts.deep);
    const hits: Hit[] = [];
    page.on('request', (request) => {
      if (!request.url().startsWith(baseURL)) return;
      hits.push({ at: Date.now(), method: request.method(), path: new URL(request.url()).pathname.replace(/\/[0-9a-f-]{20,}/g, '/:id'), range: request.headers().range, type: request.resourceType() });
    });
    page.on('requestfinished', (request) => {
      const hit = hits.find((entry) => entry.end === undefined && entry.path === new URL(request.url()).pathname.replace(/\/[0-9a-f-]{20,}/g, '/:id') && entry.range === request.headers().range);
      if (hit) { hit.end = Date.now(); const t = request.timing(); hit.ttfb = Math.round(t.responseStart - t.requestStart); }
    });
    page.on('response', async (response) => {
      if (response.request().resourceType() !== 'media') return;
      const h = response.headers();
      const hit = [...hits].reverse().find((entry) => entry.type === 'media' && entry.range === response.request().headers().range);
      const t = response.request().timing();
      if (hit) hit.info = `queued=${Math.round(t.requestStart)}ms ttfb=${Math.round(t.responseStart - t.requestStart)}ms ${response.status()} ${h['content-range'] ?? ''} len=${h['content-length']} cc=${h['cache-control'] ?? '-'} etag=${h.etag ? 'y' : 'n'} type=${h['content-type']}`;
    });
    let playAt = 0; // the click, on the page clock
    if (opts.deep) await page.goto(`/watch/library/${id}${query}`); // a link, a reload or a bookmark: everything cold, nothing preloaded
    else playAt = await openWatch(page, `${id}${query}`, opts.settle ?? SETTLE);
    const epoch = Math.round(await page.evaluate(() => performance.timeOrigin)) + playAt; // the click, on the Date.now() clock
    await expect.poll(() => page.evaluate(() => (window as W).__perfFirstFrame ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);
    const ttff = Math.round((await page.evaluate(() => (window as W).__perfFirstFrame!)) - playAt);
    for (const hit of hits.filter((entry) => entry.at >= epoch)) console.log(`PLAYPERF-WF ${profile}${opts.deep ? ' deep' : ''}${opts.settle === 0 ? ' early' : ''} ${title}${query} +${String(Math.round(hit.at - epoch)).padStart(5)} ${hit.end === undefined ? '  ?  ' : `..${String(Math.round(hit.end - epoch)).padStart(5)}`} ${hit.method} ${hit.path} ${hit.range ?? ''} [${hit.type}]${hit.ttfb === undefined ? '' : ` ttfb=${hit.ttfb}`}${hit.info ? ` ${hit.info}` : ''}`);
    if (process.env.PLAYPERF_LOAF) {
      const frames = (await page.evaluate(() => (window as unknown as { __loaf: { startTime: number; duration: number; blockingDuration: number; scripts: { sourceURL: string; sourceFunctionName: string; invoker: string; duration: number }[] }[] }).__loaf));
      for (const frame of frames.filter((entry) => entry.startTime >= playAt - 5)) console.log(`PLAYPERF-LOAF +${Math.round(frame.startTime - playAt)} ${Math.round(frame.duration)} ms: ${frame.scripts.map((script) => `${script.invoker} ${script.sourceURL.split('/').pop()}:${script.sourceFunctionName} ${Math.round(script.duration)}`).join(' | ')}`);
    }
    if (process.env.PLAYPERF_LOAF) console.log(`PLAYPERF-MARKS click@${Math.round(playAt)}: ${(await page.evaluate(() => (window as unknown as { __marks: string[] }).__marks)).join(' ; ')}`);
    if (dbg.length) console.log(`PLAYPERF-CDP now=${Date.now() % 100000}: ${dbg.slice(0, 14).join(' ; ')}`);
    return ttff;
  } finally { await context.close(); }
}

async function resolveId(page: Page, title: string, episode?: boolean): Promise<string> {
  if (!episode) { await page.goto('/library'); return libraryItemId(page, title); }
  const listing = await page.request.get('/api/library?limit=100');
  const items = (await listing.json()).items as { id: string; title: string; media_type?: string; series_title?: string }[];
  const found = items.find((item) => item.title === title || item.title.includes(title));
  expect(found, `episode ${title}`).toBeTruthy();
  return found!.id;
}

const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

const SCENARIOS: { name: string; title: string; query?: string; budget: number; episode?: boolean; deep?: boolean; settle?: number }[] = [
  { name: 'direct start', title: 'Perf Direct', budget: DIRECT_BUDGET },
  { name: 'direct start, moov at the end', title: 'Perf Tail', budget: DIRECT_BUDGET },
  { name: 'direct resume t=600', title: 'Perf Long', query: '?t=600', budget: RESUME_BUDGET },
  { name: 'early click (before idle preloads)', title: 'Perf Direct', budget: DIRECT_BUDGET, settle: 0 },
  { name: 'deep link (cold load)', title: 'Perf Direct', budget: DIRECT_BUDGET * 2, deep: true },
  { name: 'music track', title: 'Perf Track', budget: DIRECT_BUDGET, episode: true },
  { name: 'vault YouTube download (VP9/Opus WebM)', title: 'Perf Webm', budget: DIRECT_BUDGET },
  { name: 'episode', title: 'Perf Show S01E02', budget: DIRECT_BUDGET, episode: true },
];

let warmed = false; // the first video a Chrome process decodes pays for its decoder start-up (~0.5 s), which no app change touches
for (const profile of ['lan', 'rtt'] as Profile[]) for (const scenario of SCENARIOS) {
  test(`${scenario.name} (${profile}): cold-cache click -> first frame`, async ({ browser, baseURL }) => {
    if (!warmed) { warmed = true; await coldStart(browser, baseURL!, 'lan', 'Perf Direct'); }
    const samples: number[] = [];
    for (let run = 0; run < RUNS; run += 1) samples.push(await coldStart(browser, baseURL!, profile, scenario.title, scenario.query, scenario));
    const budget = profile === 'lan' ? scenario.budget : scenario.budget * 2;
    console.log(`PLAYPERF ${scenario.name} (${profile}) ttff ${samples.join(', ')} median ${median(samples)} (budget ${budget})`);
    expect(median(samples), `${scenario.name} (${profile}) p50 click -> first frame ms`).toBeLessThan(budget);
  });
}

// The watch page's side requests answered 2 s late (a loaded NAS): the video's own request must not queue behind them.
// RED before backgroundGate.ts: the first frame came after two waves of them, ~4.1 s. Needs the proxy's port (E2E_PORT + 1).
test('direct start with the info column answering in 2 s: the video does not wait for it', async ({ browser, baseURL }) => {
  const port = Number(process.env.E2E_PORT) + 1;
  const proxy: ChildProcess = spawn('../backend/.venv/bin/python', ['../scripts/perf/slow_proxy.py', String(port), String(process.env.E2E_PORT), '2'], { stdio: 'inherit' });
  try {
    if (!warmed) { warmed = true; await coldStart(browser, `http://127.0.0.1:${process.env.E2E_PORT}`, 'lan', 'Perf Direct'); }
    await expect.poll(() => fetch(`http://127.0.0.1:${port}/api/health`).then((r) => r.ok, () => false)).toBe(true);
    const samples: number[] = [];
    for (let run = 0; run < RUNS; run += 1) samples.push(await coldStart(browser, `http://127.0.0.1:${port}`, 'lan', 'Perf Direct'));
    console.log(`PLAYPERF direct start, slow info endpoints (lan) ttff ${samples.join(', ')} median ${median(samples)} (budget ${DIRECT_BUDGET})`);
    expect(median(samples), 'p50 click -> first frame ms with a 2 s info column').toBeLessThan(DIRECT_BUDGET);
  } finally { proxy.kill(); void baseURL; }
});
