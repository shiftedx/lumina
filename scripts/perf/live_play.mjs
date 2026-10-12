// Live playback in real Google Chrome against a running, NETWORKED backend.
//   node scripts/perf/live_play.mjs http://127.0.0.1:6150 USER PASSWORD [--watch=S] [--runs=N] [--prefetch=MS] [--assert] URL ...
// Per run it prints click -> playing (ms), the first and top heights, latency at 30 s (player behind the playlist edge,
// plus the upstream edge's own age from EXT-X-PROGRAM-DATE-TIME), stalls over --watch seconds (default 120), quality
// switches top -> 720p -> Auto through the player's quality menu (ms to the new height, and the latency jump), and the
// relay's playlist/segment time to first byte. --prefetch=MS posts the card-intent prefetch MS before the click (a hover). --assert exits 1 when a median misses: first frame 3 s at the top
// height, no stall, switches 1.5 s.
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const [base, username, password, ...rest] = process.argv.slice(2);
const option = (name, fallback) => Number(rest.find((arg) => arg.startsWith(`--${name}=`))?.split('=')[1] || fallback);
const watchSeconds = option('watch', 120);
const runs = option('runs', 3);
const prefetchMs = option('prefetch', 0);
let profiles = null; // what the app sends with /api/preview, sent again with the prefetch
const assertTargets = rest.includes('--assert');
const sources = rest.filter((arg) => !arg.startsWith('--'));
const median = (values) => {
  const sorted = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  return sorted.length ? sorted[Math.floor((sorted.length - 1) / 2)] : null;
};

/** Seconds the newest listed segment's end lags the wall clock (the provider's own edge delay), or null. */
function edgeAge(playlist, receivedAt) {
  let date = null;
  let end = null;
  for (const line of playlist.split('\n')) {
    if (line.startsWith('#EXT-X-PROGRAM-DATE-TIME:')) date = Date.parse(line.slice(25).trim());
    else if (line.startsWith('#EXTINF:') && date !== null) {
      const length = Number.parseFloat(line.slice(8)) * 1000;
      end = date + length;
      date = end;
    }
  }
  return end === null ? null : (receivedAt - end) / 1000;
}

const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const results = [];
async function signedInPage() {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  await page.goto(base);
  await page.evaluate(async ([user, pass]) => fetch('/api/session/login', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: user, password: pass }),
  }), [username, password]);
  await page.goto(base);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  return { context, page };
}
const navigate = (page, path) => page.evaluate((target) => {
  window.history.pushState(null, '', target);
  window.dispatchEvent(new PopStateEvent('popstate'));
}, path);
if (prefetchMs) {
  // The prefetch must carry the profiles the click's preview sends (they key the server's cache): learn them once.
  const { context, page } = await signedInPage();
  const preview = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/preview');
  await navigate(page, `/watch?url=${encodeURIComponent(sources[0])}`);
  profiles = JSON.parse((await preview).postData()).supported_profiles;
  await page.waitForTimeout(3000);
  await navigate(page, '/');
  await page.waitForTimeout(1000);
  await context.close();
}
for (const url of sources) {
  for (let run = 0; run < runs; run += 1) {
    const { context, page } = await signedInPage();
    if (prefetchMs && profiles) {
      await page.evaluate(async ([source, supported]) => {
        const { csrf_token: csrf } = await (await fetch('/api/session/me')).json();
        await fetch('/api/remote/prefetch', {
          method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
          body: JSON.stringify({ source_url: source, supported_profiles: supported }),
        });
      }, [url, profiles]);
      await page.waitForTimeout(prefetchMs);
    }
    let top = null;
    let age = null;
    const ttfb = { playlist: [], segment: [] };
    const marks = {}; // ms after the click each step first answered: preview, master, playlist, segment
    let clickedAt = 0;
    page.on('response', async (response) => {
      const path = new URL(response.url()).pathname;
      if (path === '/api/preview') marks.preview ??= Date.now() - clickedAt;
      if (!path.includes('/relay/')) return;
      marks[path.endsWith('master.m3u8') ? 'master' : (response.headers()['content-type'] || '').includes('mpegurl') ? 'playlist' : 'segment'] ??= Date.now() - clickedAt;
      const timing = response.request().timing();
      const kind = (response.headers()['content-type'] || '').includes('mpegurl') ? 'playlist' : 'segment';
      if (timing.responseStart > 0) ttfb[kind].push(timing.responseStart - timing.requestStart);
      if (kind !== 'playlist') return;
      const text = await response.text().catch(() => '');
      if (text.includes('#EXT-X-STREAM-INF')) top = Math.max(...[...text.matchAll(/RESOLUTION=\d+x(\d+)/g)].map((match) => Number(match[1])), 0) || null;
      else age = edgeAge(text, Date.now()) ?? age;
    });
    clickedAt = Date.now();
    await navigate(page, `/watch?url=${encodeURIComponent(url)}`);
    const firstHeight = await page.waitForFunction(() => {
      const video = document.querySelector('video');
      return video && !video.paused && video.currentTime > 0 && video.videoHeight ? video.videoHeight : null;
    }, null, { timeout: 30_000, polling: 50 }).then((handle) => handle.jsonValue()).catch(() => null);
    const row = { url, run, playing: firstHeight ? Date.now() - clickedAt : null, firstHeight, top, marks };
    if (!firstHeight) {
      if (process.env.LIVE_SHOT) await page.screenshot({ path: process.env.LIVE_SHOT });
      row.problem = await page.locator('.player-state, [role="alert"], .toast').first().textContent({ timeout: 1000 }).catch(() => null);
      console.log(JSON.stringify(row));
      results.push(row);
      await context.close();
      continue;
    }
    // Stalls: every 'waiting' after the first frame that is not a seek, until playback resumes.
    await page.evaluate(() => {
      const video = document.querySelector('video');
      // A wait within 5 s of a quality pick is that switch's own flush (switchStalls), not a network stall.
      const probe = { stalls: 0, stalledMs: 0, switchStalls: 0, switchAt: -Infinity, since: null, heights: [video.videoHeight], started: performance.now() };
      window.__liveProbe = probe;
      video.addEventListener('resize', () => probe.heights.push(`${video.videoHeight}@${Math.round((performance.now() - probe.started) / 1000)}s`));
      video.addEventListener('waiting', () => {
        if (video.seeking || probe.since !== null) return;
        if (performance.now() - probe.switchAt < 5000) { probe.switchStalls += 1; return; }
        probe.stalls += 1;
        probe.since = performance.now();
      });
      video.addEventListener('playing', () => { if (probe.since !== null) { probe.stalledMs += performance.now() - probe.since; probe.since = null; } });
    });
    const behind = () => page.evaluate(() => {
      const video = document.querySelector('video');
      const end = video.seekable.length ? video.seekable.end(video.seekable.length - 1) : NaN;
      return { behind: end - video.currentTime, height: video.videoHeight, time: video.currentTime };
    });
    await page.waitForTimeout(30_000);
    const at30 = await behind();
    row.behindEdge30 = Number(at30.behind.toFixed(1));
    row.edgeAge = age === null ? null : Number(age.toFixed(1));
    row.latency30 = age === null ? null : Number((at30.behind + age).toFixed(1));
    row.height30 = at30.height;
    // Quality switches through the player's own menu, when it offers one: top, 720p, then Auto.
    const quality = page.getByLabel(/playback quality/i);
    if (await quality.count()) {
      row.switches = [];
      const options = await quality.locator('option').evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, label: node.textContent })));
      const heightOf = (option) => Number.parseInt(option.label, 10);
      const plan = [options.find((option) => heightOf(option) > 0), options.find((option) => heightOf(option) === 720), options.find((option) => /^Auto/.test(option.label))].filter(Boolean);
      for (const { value, label } of plan) {
        const before = await behind();
        const want = heightOf({ label }) || null;
        const started = Date.now();
        await page.evaluate(() => { window.__liveProbe.switchAt = performance.now(); });
        await quality.selectOption(value);
        const ok = await page.waitForFunction(([height, from]) => {
          const video = document.querySelector('video');
          return video && !video.paused && video.readyState >= 3 && (height === null || video.videoHeight === height) && Math.abs(video.currentTime - from) < 120;
        }, [want, before.time], { timeout: 15_000, polling: 50 }).then(() => true).catch(() => false);
        const after = await behind();
        // jump: change in distance behind the playlist edge (a newly published segment moves the edge too);
        // skip: how far the position moved beyond the wall-clock time that passed (0 = no position jump).
        const skip = after.time - before.time - (Date.now() - started) / 1000;
        row.switches.push({ label, ms: ok ? Date.now() - started : null, jump: Number((after.behind - before.behind).toFixed(1)), skip: Number(skip.toFixed(1)) });
        await page.waitForTimeout(5000);
      }
    }
    const elapsed = 30 + (row.switches?.length || 0) * 5;
    if (watchSeconds > elapsed) await page.waitForTimeout((watchSeconds - elapsed) * 1000);
    Object.assign(row, await page.evaluate(() => {
      const { stalls, stalledMs, since, heights, switchStalls } = window.__liveProbe;
      const frames = document.querySelector('video').getVideoPlaybackQuality();
      return { stalls, stalledMs: Math.round(stalledMs + (since === null ? 0 : performance.now() - since)), dropped: `${frames.droppedVideoFrames}/${frames.totalVideoFrames}`, heights: heights.join(' '), switchStalls };
    }));
    row.playlistTtfb = Math.round(median(ttfb.playlist));
    row.segmentTtfb = Math.round(median(ttfb.segment));
    console.log(JSON.stringify(row));
    results.push(row);
    // Leave through the app so the player unmounts and releases its relay session (the per-member cap is small).
    await navigate(page, '/');
    await page.waitForTimeout(1000);
    await context.close();
  }
}
await browser.close();

let failed = false;
for (const url of sources) {
  const rows = results.filter((row) => row.url === url);
  const summary = {
    url,
    playing: median(rows.map((row) => row.playing)),
    atTop: rows.filter((row) => row.firstHeight && row.firstHeight === row.top).length + '/' + rows.length,
    latency30: median(rows.map((row) => row.latency30)),
    behindEdge30: median(rows.map((row) => row.behindEdge30)),
    stalls: median(rows.map((row) => row.stalls)),
    stalledMs: median(rows.map((row) => row.stalledMs)),
    // The step down to 720p is the real switch (the top pin is usually what already plays; Auto keeps the picture).
    switch720Ms: median(rows.flatMap((row) => (row.switches || []).filter((step) => step.label.startsWith('720')).map((step) => step.ms ?? Infinity))),
    switchStalls: median(rows.map((row) => row.switchStalls)),
    playlistTtfb: median(rows.map((row) => row.playlistTtfb)),
    segmentTtfb: median(rows.map((row) => row.segmentTtfb)),
  };
  console.log('MEDIAN', JSON.stringify(summary));
  if (!assertTargets) continue;
  const misses = [];
  if (summary.playing === null || summary.playing > 3000) misses.push(`first frame ${summary.playing} ms > 3000`);
  if (rows.some((row) => row.firstHeight !== row.top)) misses.push('did not start at the top height');
  if (summary.stalls) misses.push(`${summary.stalls} stalls`);
  if (summary.switch720Ms !== null && summary.switch720Ms > 1500) misses.push(`switch to 720p ${summary.switch720Ms} ms > 1500`);
  if (misses.length) { failed = true; console.error(`MISS ${url}: ${misses.join('; ')}`); }
}
process.exit(failed ? 1 : 0);
