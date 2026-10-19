import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';

import { mockApi, signIn } from './lumina-mock';
import { chooseSelect } from './select-helper';

/** Real dash.js, MSE and VP9/Opus prove Auto starts below a slow top rung, then ramps on measured throughput. */

const SOURCE = 'https://fixture.lumina.invalid/watch/dash-auto';
const HIGH_DELAY_MS = 1_600;
const media = new Map<string, Buffer>();
let autoManifest = '';
let fixtureRoot = '';
let lowManifest = '';

const rendition = (id: string, height: number, bitrateKbps: number) => ({
  rendition_id: id, width: height * 16 / 9, height, frame_rate: 24, bitrate_kbps: bitrateKbps,
  video_codec: 'vp9', audio_codec: 'opus', container: 'webm', content_type: 'application/dash+xml',
  display_label: `${height}p`,
});
const renditions = [rendition('r180', 180, 450), rendition('r360', 360, 2_400)];

const playback = (selected: 'auto' | 'r180', generation: number) => ({
  status: 'ready', stream_id: 'dash-e2e', transport: 'dash', media_kind: 'video',
  playback_url: `/api/remote-streams/dash-e2e/dash/${generation}/manifest.mpd`, content_type: 'application/dash+xml',
  has_video: true, has_audio: true, seekable: true, live: false, auto_available: true,
  renditions, selected_rendition_id: selected,
});
const preview = {
  kind: 'video', title: 'Synthetic adaptive DASH fixture', webpage_url: SOURCE, extractor: 'fixture', extractor_key: 'Fixture',
  media_kind: 'video', entries: [], capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: false,
    chat: { live: 'unavailable', replay: 'unavailable' } }, playback: playback('auto', 1),
  raw: { id: 'dash-auto', webpage_url: SOURCE, uploader: 'Lumina fixture', duration: 16, extractor: 'fixture' },
};

test.beforeAll(() => {
  const work = fixtureRoot = mkdtempSync(join(tmpdir(), 'lumina-dash-startup-'));
  const encodeVideo = (name: string, size: string, bitrate: string) => execFileSync('ffmpeg', [
    '-v', 'error', '-y', '-f', 'lavfi', '-i', `testsrc2=size=${size}:rate=24:duration=16`,
    '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', bitrate,
    '-g', '48', '-keyint_min', '48', '-an', '-dash', '1', join(work, name),
  ]);
  encodeVideo('low.webm', '320x180', '450k');
  encodeVideo('high.webm', '640x360', '2400k');
  execFileSync('ffmpeg', [
    '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=16',
    '-c:a', 'libopus', '-b:a', '64k', '-dash', '1', join(work, 'audio.webm'),
  ]);
  const manifest = (name: string, inputs: string[], sets: string) => {
    // ffmpeg's manifest reader reports a harmless Opus packet diagnostic for its own `-dash 1` output.
    // Quiet only this metadata pass; a non-zero exit still fails before the browser starts.
    const args = ['-v', 'quiet', '-y'];
    for (const input of inputs) args.push('-f', 'webm_dash_manifest', '-i', join(work, input));
    inputs.forEach((_input, index) => args.push('-map', String(index)));
    args.push('-c', 'copy', '-f', 'webm_dash_manifest', '-adaptation_sets', sets, join(work, name));
    execFileSync('ffmpeg', args);
    return readFileSync(join(work, name), 'utf8');
  };
  autoManifest = manifest('auto.mpd', ['low.webm', 'high.webm', 'audio.webm'], 'id=0,streams=0,1 id=1,streams=2')
    .replace(/(Representation id="0" bandwidth=")\d+/, '$1450000')
    .replace(/(Representation id="1" bandwidth=")\d+/, '$12400000');
  lowManifest = manifest('low.mpd', ['low.webm', 'audio.webm'], 'id=0,streams=0 id=1,streams=1')
    .replace(/(Representation id="0" bandwidth=")\d+/, '$1450000');
  for (const name of ['low.webm', 'high.webm', 'audio.webm']) media.set(name, readFileSync(join(work, name)));
});

test.afterAll(() => {
  if (fixtureRoot) rmSync(fixtureRoot, { force: true, recursive: true });
});

type MediaRequest = { generation: number; name: string; kind: 'init' | 'index' | 'media'; startedAt: number; completedAt: number };

function rangeKind(header: string, size: number): MediaRequest['kind'] {
  const match = /bytes=(\d+)-(\d*)/.exec(header);
  if (!match || Number(match[1]) === 0) return 'init';
  const end = match[2] ? Number(match[2]) : size - 1;
  return end >= size - 500 ? 'index' : 'media';
}

async function serveRange(route: Route, body: Buffer, delayMs: number) {
  const header = route.request().headers().range || '';
  const match = /bytes=(\d+)-(\d*)/.exec(header);
  if (delayMs) await new Promise((resolve) => setTimeout(resolve, delayMs));
  if (!match) return route.fulfill({ status: 200, contentType: 'video/webm', body, headers: { 'Accept-Ranges': 'bytes' } });
  const start = Number(match[1]);
  const end = match[2] ? Math.min(Number(match[2]), body.length - 1) : body.length - 1;
  return route.fulfill({ status: 206, contentType: 'video/webm', body: body.subarray(start, end + 1),
    headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${body.length}` } });
}

async function prepare(page: Page, requests: MediaRequest[]) {
  await page.addInitScript(() => {
    const evidence = { firstFrame: null as null | { at: number; epochMs: number; height: number; mediaTime: number } };
    Object.defineProperty(window, '__dashFrameEvidence', { configurable: true, value: evidence });
    const attach = (video: HTMLVideoElement) => {
      if (video.dataset.dashFrameProbe) return;
      video.dataset.dashFrameProbe = 'true';
      video.requestVideoFrameCallback((_now, metadata) => {
        evidence.firstFrame ||= {
          at: performance.now(), epochMs: Date.now(), height: video.videoHeight, mediaTime: metadata.mediaTime,
        };
      });
    };
    const observe = () => {
      document.querySelectorAll('video').forEach((element) => attach(element));
      new MutationObserver(() => document.querySelectorAll('video').forEach((element) => attach(element)))
        .observe(document.documentElement, { childList: true, subtree: true });
    };
    if (document.documentElement) observe(); else document.addEventListener('DOMContentLoaded', observe, { once: true });
  });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(preview) }));
  await page.route('**/api/playback/remote/**', (route) => route.fulfill({ contentType: 'application/json', body: 'null' }));
  await page.route('**/api/remote-streams/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    if (path.endsWith('/renditions/r180/select')) {
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(playback('r180', 2)) });
    }
    const manifest = /\/dash\/(\d+)\/manifest\.mpd$/.exec(path);
    if (manifest) return route.fulfill({ contentType: 'application/dash+xml', body: manifest[1] === '1' ? autoManifest : lowManifest });
    const resource = /\/dash\/(\d+)\/(low|high|audio)\.webm$/.exec(path);
    if (!resource) return route.fulfill({ status: 404, body: '' });
    const generation = Number(resource[1]);
    const name = `${resource[2]}.webm`;
    const body = media.get(name) as Buffer;
    const kind = rangeKind(route.request().headers().range || '', body.length);
    const record: MediaRequest = { generation, name, kind, startedAt: Date.now(), completedAt: 0 };
    requests.push(record);
    const delay = generation === 1 && name === 'high.webm' && kind === 'media'
      && !requests.some((request) => request !== record && request.generation === 1 && request.name === name && request.kind === kind)
      ? HIGH_DELAY_MS : 0;
    await serveRange(route, body, delay);
    record.completedAt = Date.now();
  });
}

test('DASH Auto renders the small rung before a slow top rung, then ramps and honors a pin', async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  const requests: MediaRequest[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await prepare(page, requests);
  const startedAt = Date.now();
  await page.goto(`/watch?url=${encodeURIComponent(SOURCE)}`);
  await signIn(page);

  const video = page.locator('video');
  await expect(video).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => (window as unknown as {
    __dashFrameEvidence: { firstFrame: { at: number; epochMs: number; height: number; mediaTime: number } | null };
  }).__dashFrameEvidence.firstFrame), { timeout: 20_000 }).not.toBeNull();
  const firstFrame = await page.evaluate(() => (window as unknown as {
    __dashFrameEvidence: { firstFrame: { at: number; epochMs: number; height: number; mediaTime: number } };
  }).__dashFrameEvidence.firstFrame);
  const firstFrameAt = firstFrame.epochMs;
  expect(firstFrame.height).toBe(180);
  const firstVideoMedia = requests.find((request) => request.generation === 1 && request.kind === 'media'
    && request.name !== 'audio.webm');
  expect(firstVideoMedia?.name).toBe('low.webm');

  await expect.poll(() => requests.find((request) => request.generation === 1 && request.name === 'high.webm'
    && request.kind === 'media')?.completedAt || 0, { timeout: 20_000 }).toBeGreaterThan(0);
  const firstHigh = requests.find((request) => request.generation === 1 && request.name === 'high.webm'
    && request.kind === 'media') as MediaRequest;
  expect(firstFrameAt).toBeLessThan(firstHigh.completedAt);
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.videoHeight), { timeout: 20_000 }).toBe(360);

  const quality = page.getByLabel('Remote playback quality');
  await expect(quality).toHaveText(/^Auto/);
  await video.evaluate((element: HTMLVideoElement) => element.pause());
  await chooseSelect(page, quality, '180p');
  await expect(quality).toHaveText('180p');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.videoHeight), { timeout: 20_000 }).toBe(180);
  await expect.poll(() => requests.some((request) => request.generation === 2 && request.name === 'low.webm'
    && request.kind === 'media')).toBe(true);
  expect(requests.some((request) => request.generation === 2 && request.name === 'high.webm')).toBe(false);
  expect(errors).toEqual([]);

  const evidence = {
    navigationToFirstFrameMs: firstFrameAt - startedAt,
    firstVideoRequestToFrameMs: firstFrameAt - (firstVideoMedia?.startedAt || firstFrameAt),
    firstFrame,
    firstVideoMedia: firstVideoMedia?.name,
    highMediaDelayMs: firstHigh.completedAt - firstHigh.startedAt,
    videoMediaOrder: requests.filter((request) => request.kind === 'media' && request.name !== 'audio.webm')
      .map(({ generation, name }) => ({ generation, name })),
  };
  console.log(`DASH-STARTUP ${JSON.stringify(evidence)}`);
  await testInfo.attach('dash-startup-evidence.json', {
    contentType: 'application/json', body: Buffer.from(JSON.stringify(evidence, null, 2)),
  });
});
