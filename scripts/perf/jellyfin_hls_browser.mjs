// A Jellyfin app's HLS session in real Chrome with hls.js (what jellyfin-web runs): resume, then seeks, timed against
// a running `playperf_server.py ROOT PORT` that jellyfin_hls_probe.py has already set up (owner, Jellyfin on, media imported).
//   node scripts/perf/jellyfin_hls_browser.mjs http://127.0.0.1:PORT "Perf Gop" copy|encode [resumeSeconds]
// Prints ms from loadSource to the first frame playing, then per seek the ms until the picture advances past the target,
// plus waiting events (stalls) and hls.js errors. Bundled Chromium has no H.264/AAC: needs Google Chrome.
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const [base, title = 'Perf Gop', mode = 'copy', resumeArg = '100'] = process.argv.slice(2);
const HLS = { Type: 'Video', Container: 'mp4', Protocol: 'hls', AudioCodec: 'aac' };
const profiles = {
  copy: { DirectPlayProfiles: [{ Type: 'Video', Container: 'mp4', VideoCodec: 'h264,hevc', AudioCodec: 'aac' }], TranscodingProfiles: [{ ...HLS, VideoCodec: 'h264,hevc' }] },
  encode: {
    DirectPlayProfiles: [{ Type: 'Video', Container: 'mp4', VideoCodec: 'h264', AudioCodec: 'aac' }], TranscodingProfiles: [{ ...HLS, VideoCodec: 'h264' }],
    CodecProfiles: [{ Type: 'Video', Codec: 'h264', Conditions: [{ Condition: 'LessThanEqual', Property: 'Height', Value: '480' }] }],
  },
};

const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const page = await (await browser.newContext()).newPage();
// The app's own pages forbid inline scripts: a bare page on the same origin carries hls.js instead.
await page.route('**/__hls_probe', (route) => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>probe</title>' }));
await page.goto(`${base}/__hls_probe`);
await page.addScriptTag({ path: require.resolve('hls.js/dist/hls.js') });
const result = await page.evaluate(async ({ title: wanted, profile, resume }) => {
  const auth = 'MediaBrowser Client="Swiftfin", Device="chrome", DeviceId="browser-1", Version="1.0"';
  const login = await (await fetch('/Users/AuthenticateByName', { method: 'POST', headers: { 'content-type': 'application/json', Authorization: auth }, body: JSON.stringify({ Username: 'owner', Pw: 'Lantern-harbor-2026' }) })).json();
  const headers = { Authorization: `${auth}, Token="${login.AccessToken}"`, 'content-type': 'application/json' };
  const movies = (await (await fetch('/Items?IncludeItemTypes=Movie&Recursive=true', { headers })).json()).Items;
  const id = movies.find((m) => m.Name === wanted).Id;
  const info = await (await fetch(`/Items/${id}/PlaybackInfo`, { method: 'POST', headers, body: JSON.stringify({ DeviceProfile: profile, StartTimeTicks: resume * 1e7 }) })).json();
  const url = info.MediaSources[0].TranscodingUrl;

  const video = Object.assign(document.createElement('video'), { muted: true, width: 640 });
  document.body.append(video);
  const log = { errors: [], waiting: 0, steps: [] };
  video.addEventListener('waiting', () => { log.waiting += 1; });
  const hls = new window.Hls({ startPosition: resume, maxBufferLength: 30 }); // as jellyfin-web: the app seeks to the resume point itself
  hls.on(window.Hls.Events.ERROR, (_e, data) => log.errors.push(`${data.details}${data.fatal ? ' (fatal)' : ''}`));
  const until = (test, limit = 30000) => new Promise((resolve) => {
    const started = performance.now();
    const tick = () => (test() ? resolve(performance.now() - started) : performance.now() - started > limit ? resolve(-1) : setTimeout(tick, 20));
    tick();
  });
  const started = performance.now();
  hls.loadSource(`${location.origin}${url}`);
  hls.attachMedia(video);
  await video.play().catch(() => undefined);
  log.firstFrame = await until(() => video.currentTime > resume + 0.3 && !video.paused && video.readyState >= 3);
  log.timeline = video.duration;
  log.landedAt = video.currentTime;
  for (const [label, target] of [['seek to 0', 0], ['seek to the last 20 s', Math.floor(video.duration) - 20], ['seek back to resume', resume], ['seek +60 s', resume + 60]]) {
    const before = log.waiting;
    video.currentTime = target;
    const took = await until(() => Math.abs(video.currentTime - target) < 3 && video.currentTime > target + 0.5 && video.readyState >= 3);
    log.steps.push({ label, target, ms: Math.round(took), at: Number(video.currentTime.toFixed(1)), waiting: log.waiting - before });
  }
  log.totalMs = Math.round(performance.now() - started);
  hls.destroy();
  return log;
}, { title, profile: profiles[mode], resume: Number(resumeArg) });
console.log(`${title} (${mode}), resume at ${resumeArg} s: timeline ${result.timeline?.toFixed(1)} s, first frame ${Math.round(result.firstFrame)} ms at ${result.landedAt?.toFixed(1)} s`);
for (const step of result.steps) console.log(`  ${step.label.padEnd(22)} ${String(step.ms).padStart(6)} ms  landed ${step.at} s  waiting events ${step.waiting}`);
console.log(`  waiting events ${result.waiting}, hls.js errors: ${result.errors.join(', ') || 'none'}`);
await browser.close();
