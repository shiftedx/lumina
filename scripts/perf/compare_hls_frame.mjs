// Paired common-client HLS time to first decoded frame.
//
// Each trial starts with PlaybackInfo, loads the negotiated HLS URL through the
// same local hls.js build in a fresh Chrome context, and ends on
// requestVideoFrameCallback. Credentials and access tokens never enter output.
//
//   node scripts/perf/compare_hls_frame.mjs TARGET_A TARGET_B OUTPUT [runs]
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const hlsPath = require.resolve('hls.js/dist/hls.js');
const [firstPath, secondPath, outputPath, runsArg = '5'] = process.argv.slice(2);
if (!firstPath || !secondPath || !outputPath) {
  throw new Error('usage: compare_hls_frame.mjs TARGET_A TARGET_B OUTPUT [RUNS]');
}
const runs = Number(runsArg);
if (!Number.isInteger(runs) || runs < 1) throw new Error(`invalid run count: ${runsArg}`);

const AUTH = 'MediaBrowser Client="LuminaCompare", Device="Chrome", DeviceId="compare-hls-chrome", Version="1"';
const PROFILE = {
  MaxStreamingBitrate: 2_000_000,
  DirectPlayProfiles: [],
  TranscodingProfiles: [{
    Type: 'Video', Container: 'mp4', Protocol: 'hls', VideoCodec: 'h264', AudioCodec: 'aac',
    Context: 'Streaming', MaxAudioChannels: '2', MinSegments: 1, SegmentLength: 1,
  }],
  CodecProfiles: [{
    Type: 'Video', Codec: 'h264', Conditions: [
      { Condition: 'LessThanEqual', Property: 'Width', Value: '854' },
      { Condition: 'LessThanEqual', Property: 'Height', Value: '480' },
    ],
  }],
};

const targets = [firstPath, secondPath].map((path) => JSON.parse(fs.readFileSync(path, 'utf8')));

async function prepare(target) {
  const loginResponse = await fetch(`${target.base_url}/Users/AuthenticateByName`, {
    method: 'POST',
    headers: { authorization: AUTH, 'content-type': 'application/json' },
    body: JSON.stringify({ Username: target.username, Pw: target.password }),
  });
  if (!loginResponse.ok) throw new Error(`${target.name} login failed: ${loginResponse.status}`);
  const login = await loginResponse.json();
  const authorization = `${AUTH}, Token="${login.AccessToken}"`;
  const itemsResponse = await fetch(
    `${target.base_url}/Users/${login.User.Id}/Items?Recursive=true&IncludeItemTypes=Movie&SearchTerm=Benchmark%20Transcode&Fields=MediaSources,MediaStreams`,
    { headers: { authorization } },
  );
  if (!itemsResponse.ok) throw new Error(`${target.name} items failed: ${itemsResponse.status}`);
  const item = (await itemsResponse.json()).Items.find((entry) => entry.Name === 'Benchmark Transcode');
  if (!item) throw new Error(`${target.name} has no Benchmark Transcode fixture`);
  return {
    name: target.name,
    baseUrl: target.base_url.replace(/\/$/, ''),
    userId: login.User.Id,
    itemId: item.Id,
    authorization,
  };
}

function distribution(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const rank = (value) => ordered[Math.max(0, Math.ceil(value * ordered.length) - 1)];
  return {
    count: ordered.length,
    min_ms: ordered[0],
    p50_ms: rank(0.50),
    p95_ms: rank(0.95),
    p99_ms: rank(0.99),
    max_ms: ordered.at(-1),
    mean_ms: values.reduce((sum, value) => sum + value, 0) / values.length,
  };
}

const browser = await chromium.launch({ channel: 'chrome', args: ['--autoplay-policy=no-user-gesture-required'] });
const prepared = await Promise.all(targets.map(prepare));
const samples = [];

async function runTrial(target, pair, order) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.route('**/__compare_hls_frame', (route) => route.fulfill({
    contentType: 'text/html',
    body: '<!doctype html><meta charset="utf-8"><title>HLS frame probe</title>',
  }));
  await page.goto(`${target.baseUrl}/__compare_hls_frame`);
  await page.addScriptTag({ path: hlsPath });
  try {
    return await page.evaluate(async ({ target: current, profile }) => {
      const marks = {};
      const errors = [];
      const started = performance.now();
      const playbackResponse = await fetch(`/Items/${current.itemId}/PlaybackInfo?UserId=${encodeURIComponent(current.userId)}`, {
        method: 'POST',
        headers: { authorization: current.authorization, 'content-type': 'application/json' },
        body: JSON.stringify({
          UserId: current.userId,
          DeviceProfile: profile,
          StartTimeTicks: 0,
          EnableDirectPlay: false,
          EnableDirectStream: false,
          EnableTranscoding: true,
        }),
      });
      if (!playbackResponse.ok) throw new Error(`PlaybackInfo ${playbackResponse.status}`);
      const playback = await playbackResponse.json();
      marks.playback_info_ms = performance.now() - started;
      const source = playback.MediaSources?.[0];
      if (!source?.TranscodingUrl) throw new Error('PlaybackInfo has no TranscodingUrl');
      if (!window.Hls?.isSupported()) throw new Error('hls.js MediaSource support unavailable');

      const video = Object.assign(document.createElement('video'), { muted: true, playsInline: true, preload: 'auto' });
      document.body.append(video);
      const hls = new window.Hls({
        autoStartLoad: true,
        startPosition: 0,
        xhrSetup(xhr) { xhr.setRequestHeader('Authorization', current.authorization); },
      });
      const relative = () => performance.now() - started;
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => { marks.manifest_parsed_ms ??= relative(); });
      hls.on(window.Hls.Events.FRAG_LOADING, (_event, data) => {
        if (data.frag?.type === 'main') marks.first_fragment_loading_ms ??= relative();
      });
      hls.on(window.Hls.Events.FRAG_BUFFERED, (_event, data) => {
        if (data.frag?.type === 'main') marks.first_fragment_buffered_ms ??= relative();
      });
      hls.on(window.Hls.Events.ERROR, (_event, data) => {
        errors.push({ type: data.type, details: data.details, fatal: data.fatal });
      });

      let timeout;
      const frame = new Promise((resolve, reject) => {
        timeout = setTimeout(() => reject(new Error('first decoded frame timeout')), 30_000);
        video.requestVideoFrameCallback((_now, metadata) => resolve({
          intent_to_first_decoded_frame_ms: relative(),
          media_time_seconds: metadata.mediaTime,
          presented_frames: metadata.presentedFrames,
          width: video.videoWidth,
          height: video.videoHeight,
          ready_state: video.readyState,
        }));
      });
      hls.on(window.Hls.Events.MEDIA_ATTACHED, () => {
        marks.hls_attach_to_load_ms = relative();
        hls.loadSource(new URL(source.TranscodingUrl, location.origin).href);
        video.play().catch((error) => errors.push({ type: 'play', details: String(error), fatal: false }));
      });
      hls.attachMedia(video);
      try {
        const decoded = await frame;
        return {
          ...decoded,
          ...marks,
          hls_load_to_first_decoded_frame_ms: decoded.intent_to_first_decoded_frame_ms - marks.hls_attach_to_load_ms,
          play_session_present: Boolean(playback.PlaySessionId),
          play_session_id: playback.PlaySessionId,
          errors,
        };
      } finally {
        clearTimeout(timeout);
        hls.destroy();
      }
    }, { target, profile: PROFILE });
  } finally {
    await context.close();
  }
}

try {
  for (let pair = 1; pair <= runs; pair += 1) {
    const ordered = pair % 2 === 1 ? prepared : [...prepared].reverse();
    for (let order = 0; order < ordered.length; order += 1) {
      const target = ordered[order];
      const base = { pair, order: order + 1, target: target.name };
      try {
        const result = await runTrial(target, pair, order + 1);
        const session = result.play_session_id;
        delete result.play_session_id;
        samples.push({ ...base, ok: true, ...result });
        if (session) {
          await fetch(`${target.baseUrl}/Videos/ActiveEncodings?PlaySessionId=${encodeURIComponent(session)}`, {
            method: 'DELETE', headers: { authorization: target.authorization },
          }).catch(() => undefined);
        }
      } catch (error) {
        samples.push({ ...base, ok: false, error: error instanceof Error ? error.message : String(error) });
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
    playback_info: distribution(rows.map((row) => row.playback_info_ms)),
    hls_load_to_first_decoded_frame: distribution(rows.map((row) => row.hls_load_to_first_decoded_frame_ms)),
    decoded_dimensions: [...new Set(rows.map((row) => `${row.width}x${row.height}`))].sort(),
    hls_errors: rows.flatMap((row) => row.errors),
  }];
}));

const output = {
  schema: 1,
  protocol: 'paired-hls-frame-v1',
  client: `Google Chrome via Playwright; hls.js ${require('hls.js/package.json').version}; requestVideoFrameCallback`,
  cache_state: 'fresh browser context per trial; host and Docker filesystem caches warm',
  order: 'odd pairs A then B; even pairs B then A',
  fixture: 'Benchmark Transcode (30 seconds, 1280x720 HEVC Main 10/E-AC-3)',
  requested_profile: PROFILE,
  runs_per_target: runs,
  summary,
  samples,
};
fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`);
console.log(JSON.stringify({ output: outputPath, summary }));
