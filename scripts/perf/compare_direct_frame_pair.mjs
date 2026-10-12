// Paired direct-play time to first decoded frame.
//
// Trials alternate A/B then B/A. Every trial uses a fresh browser context, so
// browser HTTP/page caches are empty while host and Docker filesystem caches
// remain warm.
//
//   node scripts/perf/compare_direct_frame_pair.mjs TARGET_A TARGET_B OUTPUT [runs]
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const [firstPath, secondPath, outputPath, runsArg = '30'] = process.argv.slice(2);
if (!firstPath || !secondPath || !outputPath) {
  throw new Error('usage: compare_direct_frame_pair.mjs TARGET_A TARGET_B OUTPUT [RUNS]');
}
const runs = Number(runsArg);
if (!Number.isInteger(runs) || runs < 1) throw new Error(`invalid run count: ${runsArg}`);
const AUTH = 'MediaBrowser Client="LuminaCompare", Device="Chrome", DeviceId="compare-direct-chrome", Version="1"';

async function prepare(path) {
  const target = JSON.parse(fs.readFileSync(path, 'utf8'));
  const loginResponse = await fetch(`${target.base_url}/Users/AuthenticateByName`, {
    method: 'POST', headers: { authorization: AUTH, 'content-type': 'application/json' },
    body: JSON.stringify({ Username: target.username, Pw: target.password }),
  });
  if (!loginResponse.ok) throw new Error(`${target.name} login failed: ${loginResponse.status}`);
  const login = await loginResponse.json();
  const authorization = `${AUTH}, Token="${login.AccessToken}"`;
  const itemsResponse = await fetch(
    `${target.base_url}/Users/${login.User.Id}/Items?Recursive=true&IncludeItemTypes=Movie&SearchTerm=Benchmark%20Direct&Fields=MediaSources`,
    { headers: { authorization } },
  );
  if (!itemsResponse.ok) throw new Error(`${target.name} items failed: ${itemsResponse.status}`);
  const item = (await itemsResponse.json()).Items.find((entry) => entry.Name === 'Benchmark Direct');
  if (!item) throw new Error(`${target.name} has no Benchmark Direct fixture`);
  const source = item.MediaSources[0];
  return {
    name: target.name,
    baseUrl: target.base_url.replace(/\/$/, ''),
    mediaUrl: `${target.base_url}/Videos/${item.Id}/stream?Static=true&mediaSourceId=${encodeURIComponent(source.Id)}&api_key=${encodeURIComponent(login.AccessToken)}`,
    fixture: { title: item.Name, container: source.Container, size_bytes: source.Size },
  };
}

function distribution(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const rank = (value) => ordered[Math.max(0, Math.ceil(value * ordered.length) - 1)];
  return {
    count: ordered.length,
    min_ms: ordered[0], p50_ms: rank(0.50), p95_ms: rank(0.95), p99_ms: rank(0.99),
    max_ms: ordered.at(-1), mean_ms: values.reduce((sum, value) => sum + value, 0) / values.length,
  };
}

const prepared = await Promise.all([prepare(firstPath), prepare(secondPath)]);
const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const samples = [];
try {
  for (let pair = 1; pair <= runs; pair += 1) {
    const ordered = pair % 2 === 1 ? prepared : [...prepared].reverse();
    for (let order = 0; order < ordered.length; order += 1) {
      const target = ordered[order];
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.route('**/__compare_direct_frame', (route) => route.fulfill({
        contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><title>direct frame probe</title>',
      }));
      try {
        await page.goto(`${target.baseUrl}/__compare_direct_frame`);
        const result = await page.evaluate(async (url) => {
          const video = Object.assign(document.createElement('video'), {
            muted: true, playsInline: true, preload: 'auto',
          });
          document.body.append(video);
          const started = performance.now();
          let timeout;
          const frame = new Promise((resolve, reject) => {
            timeout = setTimeout(() => reject(new Error('first decoded frame timeout')), 30_000);
            video.requestVideoFrameCallback((_now, metadata) => resolve({
              intent_to_first_decoded_frame_ms: performance.now() - started,
              media_time_seconds: metadata.mediaTime,
              presented_frames: metadata.presentedFrames,
              width: video.videoWidth,
              height: video.videoHeight,
              ready_state: video.readyState,
            }));
          });
          video.src = url;
          await video.play();
          try { return await frame; } finally { clearTimeout(timeout); }
        }, target.mediaUrl);
        samples.push({ pair, order: order + 1, target: target.name, ok: true, ...result });
      } catch (error) {
        samples.push({ pair, order: order + 1, target: target.name, ok: false, error: error instanceof Error ? error.message : String(error) });
      } finally {
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
}

const summary = Object.fromEntries(prepared.map((target) => {
  const rows = samples.filter((sample) => sample.target === target.name && sample.ok);
  return [target.name, {
    attempted: runs,
    succeeded: rows.length,
    failed: runs - rows.length,
    intent_to_first_decoded_frame: distribution(rows.map((row) => row.intent_to_first_decoded_frame_ms)),
    decoded_dimensions: [...new Set(rows.map((row) => `${row.width}x${row.height}`))].sort(),
    fixture: target.fixture,
  }];
}));
const output = {
  schema: 1,
  protocol: 'paired-direct-frame-v1',
  client: 'Google Chrome via Playwright; HTMLVideoElement.requestVideoFrameCallback',
  cache_state: 'fresh browser context per trial; host and Docker filesystem caches warm',
  order: 'odd pairs A then B; even pairs B then A',
  runs_per_target: runs,
  summary,
  samples,
};
fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify({ output: outputPath, summary }));
