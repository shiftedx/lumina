// Time to the right picture against a running, NETWORKED backend and real providers, in real
// Google Chrome (bundled Chromium has no H.264/AAC). The realstack gate is offline, so this runs by hand:
//   node scripts/perf/time_to_play.mjs http://127.0.0.1:4931 USER PASSWORD [--prefetch=MS] [--assert] [URL ...]
// Per source it prints click -> preview -> manifest -> first media -> playing (ms) and the height that played first.
// --prefetch=MS posts the card-intent prefetch MS before the click (a hover); --assert exits 1 when a prefetched click
// misses the targets with 50% headroom (VOD 3 s, live 4.5 s) or a DASH ladder does not start at its top rung.
// A seekable VOD then also times a seek far ahead (seek), a quality pin one rung down and back to Auto (switchDown,
// switchUp: change -> playing at the new height, never jumping back), and a reload that resumes at the saved
// position (resume); --assert holds them to 3 s, 1.5 s, 1.5 s and 3 s, and a
// switch to at most 1 s of frozen picture (switchFrozen).
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const [base, username, password, ...rest] = process.argv.slice(2);
const prefetchMs = Number(rest.find((arg) => arg.startsWith('--prefetch='))?.split('=')[1] || 0);
const assertTargets = rest.includes('--assert');
const sources = rest.filter((arg) => !arg.startsWith('--'));
if (!sources.length) sources.push('https://www.youtube.com/watch?v=aqz-KE-bpKQ', 'https://www.twitch.tv/xqc');

const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const page = await (await browser.newContext()).newPage();
// Clicks start from Settings, not Home: Home's discovery and live shelves run provider searches on every load, which
// at measurement volume earned 403s from YouTube.
const start = new URL('/settings/account', base).href;
await page.goto(start);
const { csrf_token: csrf } = await page.evaluate(async ([user, pass]) => (await fetch('/api/session/login', {
  method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: user, password: pass }),
})).json(), [username, password]);
let profiles = null; // what the app sends with /api/preview, sent again with the prefetch
page.on('request', (request) => {
  if (!profiles && new URL(request.url()).pathname === '/api/preview') profiles = JSON.parse(request.postData()).supported_profiles;
});

const go = (url) => page.evaluate((target) => {
  window.history.pushState(null, '', `/watch?url=${encodeURIComponent(target)}`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}, url);
let failed = false;
// The height playing once the media element advances past `from` (seconds) and, when given, at `height`; 0 for audio.
const playingAt = (from, height = 0, timeout = 30_000) => page.waitForFunction(([after, wanted]) => {
  const media = document.querySelector('video, audio');
  if (!media || media.paused || media.seeking || media.currentTime <= after) return null;
  const shown = media.videoHeight || 0;
  return media.tagName === 'AUDIO' || (shown && (!wanted || shown === wanted)) ? { height: shown } : null;
}, [from, height], { timeout, polling: 50 }).then((handle) => handle.jsonValue()).then((value) => value.height).catch(() => null);
// The longest the picture stood still while meant to play, from now on (both switches): a switch must never freeze.
// A rebuilt player is a new element, paused until it plays, so this follows whichever <video> is on the page and counts
// pauses too (the viewer never pauses during the switches).
const watchFrozen = () => page.evaluate(() => {
  const state = window.__frozen = { longest: 0, since: performance.now(), at: document.querySelector('video')?.currentTime };
  const tick = () => {
    const at = document.querySelector('video')?.currentTime;
    if (at !== state.at) { state.at = at; state.since = performance.now(); }
    else state.longest = Math.max(state.longest, Math.round(performance.now() - state.since));
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
const now = () => page.evaluate(() => document.querySelector('video, audio').currentTime);
const timed = async (step) => { const started = Date.now(); return await step() === null ? null : Date.now() - started; };

// Seek far ahead, pin one rung below the top and go back to Auto, then reload and resume where it was left.
async function afterStart(url, playback, top) {
  const out = {};
  await page.waitForTimeout(3000); // a little steady playback first, as a viewer would
  const duration = await page.evaluate(() => document.querySelector('video, audio').duration);
  if (!(duration > 90)) return out; // too short to seek, switch and resume before it ends
  const target = Math.floor(Math.min(duration * 0.6, duration - 20));
  if (target > 30) {
    out.seek = await timed(async () => { await page.evaluate((to) => { document.querySelector('video, audio').currentTime = to; }, target); return playingAt(target + 0.1); });
    await page.waitForTimeout(2000);
  }
  const showing = await page.evaluate(() => document.querySelector('video, audio').videoHeight || 0);
  const down = (playback?.renditions || []).filter((rendition) => rendition.height && rendition.height < showing).sort((a, b) => b.height - a.height)[0];
  if (down && playback.auto_available) {
    await watchFrozen();
    let before = await now();
    out.switchDown = await timed(async () => { await page.selectOption('select[aria-label="Remote playback quality"]', down.rendition_id); return playingAt(before, down.height); });
    let after = await now();
    if (after < before - 1) out.jumpedBack = Math.round(before - after);
    await page.waitForTimeout(2000);
    before = await now();
    out.switchUp = await timed(async () => { await page.selectOption('select[aria-label="Remote playback quality"]', 'auto'); return playingAt(before, top); });
    after = await now();
    if (after < before - 1) out.jumpedBack = Math.round(before - after);
    await page.waitForTimeout(1000);
    out.switchFrozen = await page.evaluate(() => window.__frozen.longest);
  }
  // Pausing checkpoints the position; the next visit resumes there.
  out.savedAt = Math.round(await page.evaluate(() => { const media = document.querySelector('video, audio'); media.pause(); return media.currentTime; }));
  await page.waitForTimeout(1500);
  await page.goto(start);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  out.resume = await timed(async () => { await go(url); return playingAt(out.savedAt - 5); });
  out.resumedAt = out.resume === null ? null : Math.round(await now());
  return out;
}
for (const url of sources) {
  await page.goto(start);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  if (prefetchMs) {
    if (!profiles) { await go('https://www.youtube.com/watch?v=jNQXAC9IVRw'); await page.waitForTimeout(3000); await page.goto(start); }
    await page.evaluate(([source, token, supported]) => fetch('/api/remote/prefetch', {
      method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': token },
      body: JSON.stringify({ source_url: source, supported_profiles: supported }),
    }), [url, csrf, profiles]);
    await page.waitForTimeout(prefetchMs);
  }
  const marks = {};
  let clickedAt = 0;
  const at = () => Date.now() - clickedAt;
  let preview = null;
  const onResponse = (response) => {
    const path = new URL(response.url()).pathname;
    if (path === '/api/preview') { marks.preview ??= at(); response.json().then((body) => { preview = body; }).catch(() => undefined); }
    else if (/\.(m3u8|mpd)$/.test(path)) marks.manifest ??= at();
    else if (path.startsWith('/api/remote-streams/')) marks.firstMedia ??= at();
  };
  page.on('response', onResponse);
  clickedAt = Date.now();
  await go(url);
  // Audio-only sources (SoundCloud) play in an <audio> element with no height: report 0.
  const first = await playingAt(0);
  marks.playing = first === null ? null : at();
  page.off('response', onResponse);
  const playback = preview?.playback;
  const top = Math.max(0, ...(playback?.renditions || []).map((rendition) => rendition.height || 0)) || null;
  const live = playback?.live === true;
  const vod = first !== null && !live && playback?.seekable !== false;
  const later = vod ? await afterStart(url, playback, top) : {};
  console.log(JSON.stringify({ url, transport: playback?.transport, live, top, firstHeight: first, ...marks, ...later }));
  if (assertTargets && prefetchMs) {
    const budget = live ? 4500 : 3000;
    if (marks.playing === null || marks.playing > budget) { failed = true; console.error(`over budget: ${url} ${marks.playing} > ${budget} ms`); }
    if (playback?.transport === 'dash' && top && first !== top) { failed = true; console.error(`not the top rung: ${url} ${first}p of ${top}p`); }
    for (const [step, limit] of Object.entries({ seek: 3000, switchDown: 1500, switchUp: 1500, resume: 3000 })) {
      if (step in later && (later[step] === null || later[step] > limit)) { failed = true; console.error(`${step} over budget: ${url} ${later[step]} > ${limit} ms`); }
    }
    if (later.switchFrozen > 1000) { failed = true; console.error(`a switch froze the picture ${later.switchFrozen} ms: ${url}`); }
    if (later.jumpedBack) { failed = true; console.error(`a switch jumped back: ${url} ${later.jumpedBack} s`); }
    if (later.resumedAt !== undefined && Math.abs(later.resumedAt - later.savedAt) > 5) { failed = true; console.error(`resume landed at ${later.resumedAt} s, saved ${later.savedAt} s: ${url}`); }
  }
}
await browser.close();
process.exit(failed ? 1 : 0);
