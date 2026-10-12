// Common-client direct-play time to first decoded frame for a compare_stack target.
// A new browser context per trial gives an empty browser HTTP/page cache; host and
// Docker filesystem caches remain warm after setup.
//
//   node scripts/perf/compare_first_frame.mjs target.json output.json [runs]
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const [targetPath, outputPath, runsArg = '5'] = process.argv.slice(2);
if (!targetPath || !outputPath) throw new Error('usage: compare_first_frame.mjs TARGET_JSON OUTPUT_JSON [RUNS]');
const target = JSON.parse(fs.readFileSync(targetPath, 'utf8'));
const runs = Number(runsArg);
const auth = 'MediaBrowser Client="LuminaCompare", Device="Chrome", DeviceId="compare-chrome", Version="1"';

const loginResponse = await fetch(`${target.base_url}/Users/AuthenticateByName`, {
  method: 'POST', headers: { authorization: auth, 'content-type': 'application/json' },
  body: JSON.stringify({ Username: target.username, Pw: target.password }),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status} ${await loginResponse.text()}`);
const login = await loginResponse.json();
const token = login.AccessToken;
const userId = login.User.Id;
const itemsResponse = await fetch(`${target.base_url}/Users/${userId}/Items?Recursive=true&IncludeItemTypes=Movie&SearchTerm=Benchmark%20Direct&Fields=MediaSources`, {
  headers: { authorization: `${auth}, Token="${token}"` },
});
if (!itemsResponse.ok) throw new Error(`items failed: ${itemsResponse.status}`);
const item = (await itemsResponse.json()).Items.find((entry) => entry.Name === 'Benchmark Direct');
if (!item) throw new Error('Benchmark Direct fixture not found');
const source = item.MediaSources[0];
const mediaUrl = `${target.base_url}/Videos/${item.Id}/stream?Static=true&mediaSourceId=${encodeURIComponent(source.Id)}&api_key=${encodeURIComponent(token)}`;

const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const samples = [];
for (let trial = 0; trial < runs; trial += 1) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/__compare_frame', (route) => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>frame probe</title>' }));
  await page.goto(`${target.base_url}/__compare_frame`);
  const sample = await page.evaluate(async (url) => {
    const video = Object.assign(document.createElement('video'), { muted: true, playsInline: true, preload: 'auto' });
    document.body.append(video);
    const started = performance.now();
    const firstFrame = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('first decoded frame timeout')), 30000);
      video.requestVideoFrameCallback((_now, metadata) => {
        clearTimeout(timeout);
        resolve({
          milliseconds: performance.now() - started,
          mediaTime: metadata.mediaTime,
          width: video.videoWidth,
          height: video.videoHeight,
          readyState: video.readyState,
        });
      });
    });
    video.src = url;
    await video.play();
    return firstFrame;
  }, mediaUrl);
  samples.push({ trial: trial + 1, ...sample });
  await context.close();
}
await browser.close();
const ordered = samples.map((sample) => sample.milliseconds).sort((a, b) => a - b);
const nearestRank = (p) => ordered[Math.max(0, Math.ceil(p * ordered.length) - 1)];
const output = {
  schema: 1,
  target: { name: target.name, base_url: target.base_url },
  fixture: { title: item.Name, item_id: item.Id, source_id: source.Id, container: source.Container },
  cache_state: 'fresh browser context per trial; host and Docker filesystem caches warm',
  client: 'Google Chrome via Playwright; HTMLVideoElement.requestVideoFrameCallback',
  runs,
  p50_ms: nearestRank(0.5), p95_ms: nearestRank(0.95), p99_ms: nearestRank(0.99),
  percentile_note: runs < 20 ? 'At this sample size p95/p99 are the maximum; inspect samples.' : undefined,
  samples,
};
fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify({ target: target.name, p50_ms: output.p50_ms, samples: samples.map((sample) => sample.milliseconds) }));
