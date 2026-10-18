/**
 * Opt-in raw evidence collection for the local synthetic playback matrix.
 *
 * Run only in a quiet measurement window:
 *   LUMINA_RUN_VIDEO_AUDIT=1 mise exec -- scripts/with-lock.sh /tmp/lumina-perf.lock 1800 \
 *     backend/.venv/bin/python scripts/perf/audit_run.py --name video-local --require-quiet \
 *     --out output/performance-audit/video/resources.json -- \
 *     sh -c 'cd frontend && npx playwright test -c playwright.audit-video.config.ts'
 *
 * Three starts establish a startup smoke comparison only.  They are recorded
 * as raw samples and are never labelled p95/p99.  audit_run.py supplies the
 * process-tree RSS/CPU observations; browser heap omits native decoder/GPU and
 * audio allocations, so both values are intentionally reported separately.
 */
import { execFileSync } from 'node:child_process';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { Browser, Page, Route, TestInfo } from '@playwright/test';

import { openWatch, stopSession } from '../realstack/perfProbe';
import { expect, libraryItemId, OWNER_STATE, test } from '../realstack/realstack';
import { mockApi, signIn } from '../lumina-mock';

const ENABLED = process.env.LUMINA_RUN_VIDEO_AUDIT === '1';
const SEEK_DIAGNOSTIC = process.env.AUDIT_VIDEO_SEEK_DIAGNOSTIC === '1';
const RUNS = 3;
const SOURCE_OBSERVATION_MS = 10_000;
const OBSERVATION_MS = 20_000;
const OUTPUT = process.env.AUDIT_VIDEO_OUTPUT ?? path.resolve('..', 'output', 'performance-audit', 'video');

type Fixture = { name: string; title: string; expected_mode: 'direct' | 'remux' | 'transcode' };
type BrowserMemory = { used_js_heap_bytes: number | null; total_js_heap_bytes: number | null; cdp_js_heap_bytes: number | null };
type Probe = {
  first_frame: { at_ms: number; media_time: number; width: number; height: number } | null;
  last_frame: { at_ms: number; media_time: number; width: number; height: number } | null;
  events: Array<{ type: string; at_ms: number }>;
};
type VideoQuality = { dropped_frames: number | null; total_frames: number | null; corrupted_frames: number | null };
type QualityDelta = VideoQuality & { counter_reset: boolean; before: VideoQuality; after: VideoQuality };
type PlaybackOptions = {
  mode?: unknown; reason?: unknown; quality_heights?: unknown; audio_tracks?: unknown;
  facts?: { container?: unknown; video_codec?: unknown; audio_codec?: unknown; width?: unknown; height?: unknown; duration?: unknown } | null;
};
type Result = {
  schema: 1;
  fixture: Fixture;
  trial: number;
  browser_cache: 'cold-context';
  playback_options: { mode: string | null; reason: string | null; facts: { container: string | null; video_codec: string | null; audio_codec: string | null; width: number | null; height: number | null; duration_seconds: number | null }; quality_heights: number[]; audio_track_count: number };
  server_cache: 'warm-or-unknown';
  startup: { click_to_first_decoded_frame_ms: number; mode: string | null; decoded_size: { width: number; height: number } };
  resume: { target_seconds: number; elapsed_to_decoded_frame_ms: number | null; landed_seconds: number | null };
  quality_switch: { requested: '480p'; elapsed_to_decoded_frame_ms: number | null; available: boolean; session_acknowledged: boolean; initially_selected: boolean; session_mode: string | null; delivered_decoded_height: number | null };
  source_quality_observation: { phase: 'before_480p_selection'; duration_ms: number; clock_advance_seconds: number; stalls: { count: number; total_duration_ms: number }; quality: QualityDelta; decoded_size: { width: number; height: number } };
  observation: { phase: 'after_480p_selection'; duration_ms: number; clock_advance_seconds: number; stalls: { count: number; total_duration_ms: number }; quality: QualityDelta };
  browser: { heap: BrowserMemory; resource_bytes: number; decoded_size: { width: number; height: number } };
  unsupported: string[];
  outcomes: { resume: 'passed' | 'failed'; quality: 'passed' | 'failed' | 'unsupported' };
};

const FIXTURES: readonly Fixture[] = [
  { name: 'direct-h264-aac', title: 'Perf Direct', expected_mode: 'direct' },
  { name: 'remux-h264-aac-mkv', title: 'Perf Remux', expected_mode: 'remux' },
  { name: 'transcode-mpeg4', title: 'Perf Encode', expected_mode: 'transcode' },
  { name: 'hevc10-eac3', title: 'Perf Hevc', expected_mode: 'transcode' },
  { name: 'long-gop-h264-ac3', title: 'Perf Gop', expected_mode: 'remux' },
  { name: 'vp9-opus-webm', title: 'Perf Webm', expected_mode: 'direct' },
  { name: 'long-h264-resume', title: 'Perf Long', expected_mode: 'direct' },
  { name: 'tail-moov-h264', title: 'Perf Tail', expected_mode: 'direct' },
  { name: 'tracks-h264-two-audio-text-subtitle', title: 'Perf Tracks', expected_mode: 'remux' },
];

test.skip(!ENABLED, 'Set LUMINA_RUN_VIDEO_AUDIT=1 in an isolated, locked measurement window.');

type AuditWindow = Window & { __videoAudit?: Probe };

function installProbe(): void {
  const probe: Probe = { first_frame: null, last_frame: null, events: [] };
  Object.defineProperty(window, '__videoAudit', { configurable: true, value: probe });
  for (const type of ['waiting', 'stalled', 'playing', 'seeking', 'seeked']) {
    document.addEventListener(type, (event) => {
      if (event.target instanceof HTMLVideoElement) probe.events.push({ type, at_ms: performance.now() });
    }, true);
  }
  document.addEventListener('loadeddata', (event) => {
    const video = event.target;
    if (!(video instanceof HTMLVideoElement)) return;
    const next = () => video.requestVideoFrameCallback((at, metadata) => {
      const frame = { at_ms: at, media_time: metadata.mediaTime, width: video.videoWidth, height: video.videoHeight };
      probe.first_frame ??= frame;
      probe.last_frame = { at_ms: at, media_time: metadata.mediaTime, width: video.videoWidth, height: video.videoHeight };
      next();
    });
    next();
  }, true);
}

function stalls(events: Probe['events'], start: number, end: number): { count: number; total_duration_ms: number } {
  let began: number | null = null;
  let count = 0;
  let total = 0;
  for (const event of events) {
    if (event.at_ms < start || event.at_ms > end) continue;
    if ((event.type === 'waiting' || event.type === 'stalled') && began === null) began = event.at_ms;
    if ((event.type === 'playing' || event.type === 'seeked') && began !== null) {
      count += 1;
      total += Math.max(0, event.at_ms - began);
      began = null;
    }
  }
  if (began !== null) { count += 1; total += Math.max(0, end - began); }
  return { count, total_duration_ms: Math.round(total) };
}

async function waitFrameAfter(page: Page, minimumAt: number, timeout = 30_000, minimumMediaTime: number | null = null, maximumHeight: number | null = null, expectedMediaTime: number | null = null, seekToleranceSeconds = 2): Promise<Probe['last_frame']> {
  try {
    await expect.poll(() => page.evaluate(({ at, minimumMediaTime: minimum, maximumHeight: maximum, expectedMediaTime: expected, seekToleranceSeconds: tolerance }) => {
      const frame = (window as AuditWindow).__videoAudit?.last_frame;
      const video = document.querySelector('video') as HTMLVideoElement | null;
      const exactSeek = expected === null || Boolean(video && !video.seeking && Math.abs(video.currentTime - expected) <= tolerance && Math.abs((frame?.media_time ?? Infinity) - expected) <= tolerance);
      return frame && frame.at_ms >= at && (minimum === null || frame.media_time >= minimum) && (maximum === null || frame.height <= maximum) && exactSeek ? frame : null;
    }, { at: minimumAt, minimumMediaTime, maximumHeight, expectedMediaTime, seekToleranceSeconds }), { timeout }).not.toBeNull();
    return page.evaluate(() => (window as AuditWindow).__videoAudit?.last_frame ?? null);
  } catch {
    return null;
  }
}

async function browserMemory(page: Page): Promise<BrowserMemory> {
  const cdp = await page.context().newCDPSession(page);
  const cdpMetrics = await cdp.send('Performance.getMetrics').catch(() => ({ metrics: [] as Array<{ name: string; value: number }> }));
  const cdpHeap = cdpMetrics.metrics.find((metric) => metric.name === 'JSHeapUsedSize')?.value ?? null;
  const heap = await page.evaluate(() => {
    const memory = (performance as Performance & { memory?: { usedJSHeapSize: number; totalJSHeapSize: number } }).memory;
    return memory ? { used: memory.usedJSHeapSize, total: memory.totalJSHeapSize } : null;
  });
  return { used_js_heap_bytes: heap?.used ?? null, total_js_heap_bytes: heap?.total ?? null, cdp_js_heap_bytes: cdpHeap };
}

async function videoState(page: Page): Promise<{ probe: Probe; time: number; duration: number; quality: VideoQuality; resource_bytes: number; width: number; height: number }> {
  return page.evaluate(() => {
    const video = document.querySelector('video') as HTMLVideoElement | null;
    if (!video) throw new Error('video element missing');
    const quality = video.getVideoPlaybackQuality?.();
    const resources = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
    return {
      probe: (window as AuditWindow).__videoAudit ?? { first_frame: null, last_frame: null, events: [] },
      time: video.currentTime,
      duration: video.duration,
      quality: quality ? { dropped_frames: quality.droppedVideoFrames, total_frames: quality.totalVideoFrames, corrupted_frames: quality.corruptedVideoFrames } : { dropped_frames: null, total_frames: null, corrupted_frames: null },
      resource_bytes: resources.reduce((sum, entry) => sum + (Number.isFinite(entry.transferSize) ? entry.transferSize : 0), 0),
      width: video.videoWidth,
      height: video.videoHeight,
    };
  });
}

function qualityDelta(start: VideoQuality, end: VideoQuality): QualityDelta {
  const counters = ['dropped_frames', 'total_frames', 'corrupted_frames'] as const;
  const counterReset = counters.some((counter) => start[counter] !== null && end[counter] !== null && end[counter]! < start[counter]!);
  const delta = (before: number | null, after: number | null) => before === null || after === null || counterReset ? null : after - before;
  // A source/session switch may reset browser counters. Keep both absolutes and
  // mark the phase unknown instead of falsely reporting zero dropped frames.
  return { dropped_frames: delta(start.dropped_frames, end.dropped_frames), total_frames: delta(start.total_frames, end.total_frames), corrupted_frames: delta(start.corrupted_frames, end.corrupted_frames), counter_reset: counterReset, before: start, after: end };
}

async function select480(page: Page): Promise<{ available: boolean; elapsed: number | null; acknowledged: boolean; initiallySelected: boolean; sessionMode: string | null; deliveredHeight: number | null; sessionId: string | null }> {
  const video = page.locator('video');
  await video.hover();
  await page.getByRole('button', { name: 'Playback settings' }).click();
  const option = page.getByRole('radio', { name: '480p', exact: true });
  if (await option.count() === 0) {
    await page.keyboard.press('Escape');
    return { available: false, elapsed: null, acknowledged: false, initiallySelected: false, sessionMode: null, deliveredHeight: null, sessionId: null };
  }
  const initiallySelected = await option.isChecked();
  if (initiallySelected) {
    await page.keyboard.press('Escape');
    return { available: true, elapsed: null, acknowledged: false, initiallySelected: true, sessionMode: null, deliveredHeight: await video.evaluate((element: HTMLVideoElement) => element.videoHeight), sessionId: null };
  }
  const successfulSession = page.waitForResponse((response) => response.request().method() === 'POST'
    && /\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(response.url()).pathname)
    && response.ok(), { timeout: 5_000 }).catch(() => null);
  const switchedAt = await page.evaluate(() => performance.now());
  await option.dispatchEvent('click');
  await page.keyboard.press('Escape');
  const response = await successfulSession;
  if (!response) return { available: true, elapsed: null, acknowledged: false, initiallySelected: false, sessionMode: null, deliveredHeight: null, sessionId: null };
  const body = await response.json().catch(() => null) as { mode?: unknown; session_id?: unknown } | null;
  const sessionMode = typeof body?.mode === 'string' ? body.mode : null;
  const returnedSessionId = typeof body?.session_id === 'string' ? body.session_id : null;
  // A response alone is not a quality-switch measurement: require the new
  // rendition to emit a subsequent decoded frame at the requested-or-lower size.
  const frame = await waitFrameAfter(page, switchedAt, 30_000, null, 480);
  return { available: true, elapsed: frame ? Math.round(frame.at_ms - switchedAt) : null, acknowledged: true, initiallySelected: false, sessionMode, deliveredHeight: frame?.height ?? null, sessionId: returnedSessionId };
}
async function resetLocalTrialState(page: Page, baseURL: string, itemId: string): Promise<void> {
  const session = await page.request.get('/api/session/me');
  expect(session.ok(), await session.text()).toBe(true);
  const sessionBody = await session.json() as { csrf_token?: unknown };
  expect(typeof sessionBody.csrf_token, 'session CSRF token').toBe('string');
  const headers = { Origin: baseURL, 'X-CSRF-Token': sessionBody.csrf_token as string };
  const cleared = await page.request.delete(`/api/library/${itemId}/playback`, { headers });
  expect(cleared.status(), 'clear residual playback position before each cold context').toBe(204);
  const saved = await page.request.put('/api/settings/me', { headers, data: { ui_prefs: { playback_max_height: null } } });
  expect(saved.ok(), await saved.text()).toBe(true);
  const preferences = await saved.json() as { ui_prefs?: { playback_max_height?: unknown } };
  expect(preferences.ui_prefs?.playback_max_height, 'start each trial at Best available').toBeNull();
}

async function sessionId(response: import('@playwright/test').Response): Promise<string | null> {
  if (!response.ok()) return null;
  const body = await response.json().catch(() => null) as { session_id?: unknown } | null;
  return typeof body?.session_id === 'string' ? body.session_id : null;
}

async function stopOwnedSessions(page: Page, sessions: ReadonlySet<string>): Promise<void> {
  for (const id of sessions) {
    await stopSession(page, id).catch((error: unknown) => console.log(`could not stop local audit session ${id}: ${String(error)}`));
  }
}

async function measure(browser: Browser, baseURL: string, fixture: Fixture, trial: number): Promise<Result> {
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript(installProbe);
  const page = await context.newPage();
  const ownedSessions = new Set<string>();
  const unsupported = [
    ...(fixture.title === 'Perf Tracks' ? [] : ['alternate_audio: fixture contains one audio stream', 'subtitles: fixture contains no embedded or sidecar subtitle track']),
    'native_decoder_gpu_memory: unavailable to JS heap and process RSS sampling',
  ];
  const captureOwnedSession = (response: import('@playwright/test').Response) => {
    if (response.request().method() !== 'POST' || !/\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(response.url()).pathname)) return;
    void sessionId(response).then((id) => { if (id) ownedSessions.add(id); });
  };
  page.on('response', captureOwnedSession);
  try {
    await page.goto('/library');
    const id = await libraryItemId(page, fixture.title);
    await resetLocalTrialState(page, baseURL, id);
    const optionsResponse = page.waitForResponse((response) => response.request().method() === 'GET'
      && new URL(response.url()).pathname === `/api/library/${id}/playback-options`, { timeout: 5_000 }).catch(() => null);
    const startedSession = page.waitForResponse((response) => response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/library/${id}/playback-sessions`, { timeout: 5_000 }).catch(() => null);
    const clickAt = await openWatch(page, id);
    const initialSessionId = await startedSession.then((response) => response ? sessionId(response) : null);
    if (initialSessionId) ownedSessions.add(initialSessionId);
    const options = await optionsResponse.then((response) => response ? response.json() as Promise<PlaybackOptions> : {} as PlaybackOptions);
    await expect.poll(() => page.evaluate(() => (window as AuditWindow).__videoAudit?.first_frame ?? null), { timeout: 30_000 }).not.toBeNull();
    const first = await page.evaluate(() => (window as AuditWindow).__videoAudit!.first_frame!);
    const startupMode = await page.locator('[data-playback-mode]').getAttribute('data-playback-mode');
    const beforeSeek = await videoState(page);
    // Long-GOP resume needs a material offset; leave a five-second decode tail.
    const target = Math.max(0, Math.min(300, Math.floor(beforeSeek.duration / 3), Math.max(0, beforeSeek.duration - 5)));
    const seekAt = await page.evaluate((to) => {
      const video = document.querySelector('video') as HTMLVideoElement;
      video.currentTime = to;
      return performance.now();
    }, target);
    const resumed = await waitFrameAfter(page, seekAt, 30_000, null, null, target);
    // Keep source-codec playback separate from the later forced-480 H.264
    // compatibility observation: HEVC/VP9/WebM can otherwise be represented
    // only by startup and seek timings.
    const sourceObservationStart = await page.evaluate(() => performance.now());
    const sourceStart = await videoState(page);
    await page.waitForTimeout(SOURCE_OBSERVATION_MS);
    const sourceObservationEnd = await page.evaluate(() => performance.now());
    const sourceEnd = await videoState(page);
    const quality = await select480(page);
    if (quality.sessionId) ownedSessions.add(quality.sessionId);
    const observationStart = await page.evaluate(() => performance.now());
    const start = await videoState(page);
    await page.waitForTimeout(OBSERVATION_MS);
    const observationEnd = await page.evaluate(() => performance.now());
    const end = await videoState(page);
    const heap = await browserMemory(page);
    return {
      schema: 1,
      fixture,
      trial,
      browser_cache: 'cold-context',
      playback_options: {
        mode: typeof options.mode === 'string' ? options.mode : null,
        reason: typeof options.reason === 'string' ? options.reason : null,
        facts: {
          container: typeof options.facts?.container === 'string' ? options.facts.container : null,
          video_codec: typeof options.facts?.video_codec === 'string' ? options.facts.video_codec : null,
          audio_codec: typeof options.facts?.audio_codec === 'string' ? options.facts.audio_codec : null,
          width: typeof options.facts?.width === 'number' ? options.facts.width : null,
          height: typeof options.facts?.height === 'number' ? options.facts.height : null,
          duration_seconds: typeof options.facts?.duration === 'number' ? options.facts.duration : null,
        },
        quality_heights: Array.isArray(options.quality_heights) ? options.quality_heights.filter((height): height is number => typeof height === 'number') : [],
        audio_track_count: Array.isArray(options.audio_tracks) ? options.audio_tracks.length : 0,
      },
      server_cache: 'warm-or-unknown',
      startup: { click_to_first_decoded_frame_ms: Math.round(first.at_ms - clickAt), mode: startupMode, decoded_size: { width: first.width, height: first.height } },
      resume: { target_seconds: target, elapsed_to_decoded_frame_ms: resumed ? Math.round(resumed.at_ms - seekAt) : null, landed_seconds: resumed?.media_time ?? null },
      quality_switch: { requested: '480p', elapsed_to_decoded_frame_ms: quality.elapsed, available: quality.available, session_acknowledged: quality.acknowledged, initially_selected: quality.initiallySelected, session_mode: quality.sessionMode, delivered_decoded_height: quality.deliveredHeight },
      source_quality_observation: { phase: 'before_480p_selection', duration_ms: Math.round(sourceObservationEnd - sourceObservationStart), clock_advance_seconds: Number((sourceEnd.time - sourceStart.time).toFixed(3)), stalls: stalls(sourceEnd.probe.events, sourceObservationStart, sourceObservationEnd), quality: qualityDelta(sourceStart.quality, sourceEnd.quality), decoded_size: { width: sourceEnd.width, height: sourceEnd.height } },
      observation: { phase: 'after_480p_selection', duration_ms: Math.round(observationEnd - observationStart), clock_advance_seconds: Number((end.time - start.time).toFixed(3)), stalls: stalls(end.probe.events, observationStart, observationEnd), quality: qualityDelta(start.quality, end.quality) },
      browser: { heap, resource_bytes: end.resource_bytes, decoded_size: { width: end.width, height: end.height } },
      unsupported,
      outcomes: {
        resume: resumed ? 'passed' : 'failed',
        quality: !quality.available ? 'unsupported' : quality.acknowledged && quality.elapsed !== null && quality.deliveredHeight !== null ? 'passed' : 'failed',
      },
    };
  } finally {
    page.off('response', captureOwnedSession);
    await stopOwnedSessions(page, ownedSessions);
    await context.close();
  }
}

async function record(testInfo: TestInfo, name: string, body: unknown): Promise<void> {
  const json = JSON.stringify(body, null, 2) + '\n';
  await mkdir(OUTPUT, { recursive: true });
  await writeFile(path.join(OUTPUT, `${name}.json`), json);
  await testInfo.attach(`${name}.json`, { body: Buffer.from(json), contentType: 'application/json' });
}

// This is the same real dash.js/MSE shape as v1_remote_dash_startup.spec.ts,
// kept here so this audit can write its raw samples in one place.  The upstream
// is fulfilled in the page, therefore it qualifies frontend adaptation only;
// it does not measure the backend DASH relay.
const DASH_SOURCE = 'https://fixture.lumina.invalid/watch/audit-dash';
let dashRoot = '';
let hlsRoot = '';
let hlsSegments: string[] = [];
let dashAuto = '';
let dashLow = '';
const dashMedia = new Map<string, Buffer>();
const dashRenditions = [
  { rendition_id: 'r180', width: 320, height: 180, frame_rate: 24, bitrate_kbps: 450, video_codec: 'vp9', audio_codec: 'opus', container: 'webm', content_type: 'application/dash+xml', display_label: '180p' },
  { rendition_id: 'r360', width: 640, height: 360, frame_rate: 24, bitrate_kbps: 2400, video_codec: 'vp9', audio_codec: 'opus', container: 'webm', content_type: 'application/dash+xml', display_label: '360p' },
];
const dashPlayback = (selected: 'auto' | 'r180', generation: number) => ({ status: 'ready', stream_id: 'audit-dash', transport: 'dash', media_kind: 'video', playback_url: `/api/remote-streams/audit-dash/dash/${generation}/manifest.mpd`, content_type: 'application/dash+xml', has_video: true, has_audio: true, seekable: true, live: false, auto_available: true, renditions: dashRenditions, selected_rendition_id: selected });
const dashPreview = { kind: 'video', title: 'Audit DASH fixture', webpage_url: DASH_SOURCE, extractor: 'fixture', extractor_key: 'Fixture', media_kind: 'video', entries: [], capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' } }, playback: dashPlayback('auto', 1), raw: { id: 'audit-dash', webpage_url: DASH_SOURCE, uploader: 'Lumina fixture', duration: 16, extractor: 'fixture' } };

test.beforeAll(async () => {
  dashRoot = await mkdtemp(path.join(tmpdir(), 'lumina-audit-dash-'));
  const encode = (name: string, size: string, bitrate: string) => execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `testsrc2=size=${size}:rate=24:duration=16`, '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', bitrate, '-g', '48', '-an', '-dash', '1', path.join(dashRoot, name)]);
  encode('low.webm', '320x180', '450k');
  encode('high.webm', '640x360', '2400k');
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=16', '-c:a', 'libopus', '-b:a', '64k', '-dash', '1', path.join(dashRoot, 'audio.webm')]);
  const manifest = async (name: string, inputs: string[], sets: string) => {
    const args = ['-v', 'quiet', '-y'];
    for (const input of inputs) args.push('-f', 'webm_dash_manifest', '-i', path.join(dashRoot, input));
    inputs.forEach((_input, index) => args.push('-map', String(index)));
    args.push('-c', 'copy', '-f', 'webm_dash_manifest', '-adaptation_sets', sets, path.join(dashRoot, name));
    execFileSync('ffmpeg', args);
    return readFile(path.join(dashRoot, name), 'utf8');
  };
  dashAuto = await manifest('auto.mpd', ['low.webm', 'high.webm', 'audio.webm'], 'id=0,streams=0,1 id=1,streams=2');
  dashLow = await manifest('low.mpd', ['low.webm', 'audio.webm'], 'id=0,streams=0 id=1,streams=1');
  for (const name of ['low.webm', 'high.webm', 'audio.webm']) dashMedia.set(name, await readFile(path.join(dashRoot, name)));

  // Matches the fMP4 HLS fixture shape used by v1_twitch_playback.spec.ts.
  // It remains local to this audit: requests are intercepted in the browser and
  // do not exercise a resolver, outbound network, or the backend HLS relay.
  hlsRoot = await mkdtemp(path.join(tmpdir(), 'lumina-audit-hls-'));
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=40', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=40',
    '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '300k', '-g', '48', '-c:a', 'libopus', '-shortest', '-f', 'hls',
    '-hls_segment_type', 'fmp4', '-hls_time', '2', '-hls_playlist_type', 'vod', '-hls_fmp4_init_filename', 'init.mp4',
    '-hls_segment_filename', path.join(hlsRoot, 'seg%d.m4s'), path.join(hlsRoot, 'index.m3u8')]);
  hlsSegments = (await readdir(hlsRoot)).filter((name) => /^seg\d+\.m4s$/.test(name)).sort((left, right) => Number(/\d+/.exec(left)![0]) - Number(/\d+/.exec(right)![0]));
  if (hlsSegments.length < 4) throw new Error('HLS audit fixture needs at least four media segments');
});

test.afterAll(async () => {
  await Promise.all([dashRoot && rm(dashRoot, { force: true, recursive: true }), hlsRoot && rm(hlsRoot, { force: true, recursive: true })]);
});

async function dashRoute(route: Route): Promise<void> {
  const pathname = new URL(route.request().url()).pathname;
  if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
  if (pathname.endsWith('/renditions/r180/select')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(dashPlayback('r180', 2)) });
  const manifest = /\/dash\/(\d+)\/manifest\.mpd$/.exec(pathname);
  if (manifest) return route.fulfill({ contentType: 'application/dash+xml', body: manifest[1] === '1' ? dashAuto : dashLow });
  const resource = /\/dash\/\d+\/(low|high|audio)\.webm$/.exec(pathname);
  if (!resource) return route.fulfill({ status: 404, body: '' });
  const body = dashMedia.get(`${resource[1]}.webm`)!;
  const range = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range ?? '');
  if (!range) return route.fulfill({ status: 200, contentType: 'video/webm', body, headers: { 'Accept-Ranges': 'bytes' } });
  const start = Number(range[1]);
  const end = range[2] ? Math.min(Number(range[2]), body.length - 1) : body.length - 1;
  return route.fulfill({ status: 206, contentType: 'video/webm', body: body.subarray(start, end + 1), headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${body.length}` } });
}

async function openMockedWatch(page: Page, source: string): Promise<number> {
  await page.goto('/');
  await signIn(page);
  const started = await page.evaluate(() => performance.now());
  await page.evaluate((target) => {
    history.pushState({}, '', target);
    dispatchEvent(new PopStateEvent('popstate'));
  }, `/watch?url=${encodeURIComponent(source)}`);
  return started;
}

test('remote DASH frontend-only synthetic upstream: three cold contexts', async ({ browser, baseURL }, testInfo) => {
  const runs: Array<Record<string, unknown>> = [];
  for (let trial = 1; trial <= RUNS; trial += 1) {
    const context = await browser.newContext({ baseURL });
    await context.addInitScript(installProbe);
    const page = await context.newPage();
    try {
      await mockApi(page, { sidebar_collapsed: false });
      await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(dashPreview) }));
      await page.route('**/api/playback/remote/**', (route) => route.fulfill({ contentType: 'application/json', body: 'null' }));
      await page.route('**/api/remote-streams/**', dashRoute);
      const started = await openMockedWatch(page, DASH_SOURCE);
      await expect.poll(() => page.evaluate(() => (window as AuditWindow).__videoAudit?.first_frame ?? null), { timeout: 30_000 }).not.toBeNull();
      const first = await page.evaluate(() => (window as AuditWindow).__videoAudit!.first_frame!);
      const video = page.locator('video');
      await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.videoHeight), { timeout: 30_000 }).toBe(360);
      const switchAt = await page.evaluate(() => performance.now());
      await page.getByLabel('Remote playback quality').selectOption('r180');
      await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.videoHeight), { timeout: 30_000 }).toBe(180);
      const switched = await waitFrameAfter(page, switchAt);
      const state = await videoState(page);
      runs.push({ trial, upstream: 'frontend-route synthetic DASH; backend relay excluded', click_to_first_decoded_frame_ms: Math.round(first.at_ms - started), first_height: first.height, quality_pin_to_decoded_frame_ms: switched ? Math.round(switched.at_ms - switchAt) : null, resource_bytes: state.resource_bytes, browser_heap: await browserMemory(page), dropped_frames: state.quality, stalls_before_pin: stalls(state.probe.events, started, switchAt) });
      await record(testInfo, 'audit-video-remote-dash', { schema: 1, runs, complete: runs.length === RUNS });
    } finally { await context.close(); }
  }
  await record(testInfo, 'audit-video-remote-dash', { schema: 1, runs, statistics: 'Raw samples only; N=3 is not p95/p99.', qualification: 'frontend-only synthetic upstream. Backend RemoteStreamingService/relay transport remains a separate measurement.' });
});


const HLS_VOD_SOURCE = 'https://fixture.lumina.invalid/watch/audit-hls-vod';
const HLS_LIVE_SOURCE = 'https://fixture.lumina.invalid/watch/audit-hls-live';
type HlsState = { playlist_reads: number };

function hlsPlayback(streamId: string, live: boolean) {
  return { status: 'ready', stream_id: streamId, transport: 'hls', media_kind: 'video', playback_url: `/api/remote-streams/${streamId}/relay/1/master.m3u8`, content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true, seekable: !live, live, renditions: [] };
}

function hlsPreview(source: string, streamId: string, live: boolean) {
  return {
    kind: 'video', title: live ? 'Audit rolling HLS live fixture' : 'Audit HLS VOD fixture', webpage_url: source, extractor: 'fixture', extractor_key: live ? 'TwitchStream' : 'TwitchVod', media_kind: 'video', entries: [],
    capabilities: { provider: 'twitch', lifecycle: live ? 'live' : 'completed_live', can_play: true, can_acquire: !live, acquire_reason: live ? 'live_acquisition_not_supported' : null, can_record: live, from_start_available: false, can_schedule: false, chat: { live: 'unavailable', replay: 'unavailable' } },
    playback: hlsPlayback(streamId, live), raw: { id: streamId, webpage_url: source, uploader: 'Lumina fixture', extractor: 'fixture', ...(live ? { is_live: true } : { was_live: true, duration: 40 }) },
  };
}

function rollingPlaylist(streamId: string, state: HlsState): string {
  // The three-segment window advances on each client reload. It intentionally
  // has no ENDLIST: this exercises hls.js current-edge behavior, not DVR.
  const width = 3;
  const first = Math.min(state.playlist_reads++, hlsSegments.length - width);
  const base = `/api/remote-streams/${streamId}/relay/1/r`;
  return ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-TARGETDURATION:2', `#EXT-X-MEDIA-SEQUENCE:${first}`, '#EXT-X-INDEPENDENT-SEGMENTS', `#EXT-X-MAP:URI="${base}/init.mp4"`, ...hlsSegments.slice(first, first + width).flatMap((name) => ['#EXTINF:2.000,', `${base}/${name}`])].join('\n') + '\n';
}

async function hlsRoute(route: Route, streamId: string, live: boolean, state: HlsState): Promise<void> {
  const pathname = new URL(route.request().url()).pathname;
  if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
  if (pathname.endsWith('/master.m3u8')) {
    return route.fulfill({ contentType: 'application/vnd.apple.mpegurl', body: `#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360,CODECS="vp09.00.21.08,opus"\n/api/remote-streams/${streamId}/relay/1/r/media\n` });
  }
  const resource = pathname.split('/r/')[1];
  if (resource === 'media') {
    const body = live
      ? rollingPlaylist(streamId, state)
      : (await readFile(path.join(hlsRoot, 'index.m3u8'), 'utf8')).replace(/init\.mp4|seg\d+\.m4s/g, (name) => `/api/remote-streams/${streamId}/relay/1/r/${name}`);
    return route.fulfill({ contentType: 'application/vnd.apple.mpegurl', body });
  }
  if (resource !== 'init.mp4' && !hlsSegments.includes(resource)) return route.fulfill({ status: 404, body: '' });
  return route.fulfill({ contentType: resource === 'init.mp4' ? 'video/mp4' : 'video/iso.segment', body: await readFile(path.join(hlsRoot, resource)) });
}

async function installHlsRoutes(page: Page, source: string, streamId: string, live: boolean, state: HlsState): Promise<void> {
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(hlsPreview(source, streamId, live)) }));
  await page.route('**/api/playback/remote/**', (route) => route.request().method() === 'GET'
    ? route.fulfill({ contentType: 'application/json', body: JSON.stringify({ position_seconds: 0, duration_seconds: live ? null : 40, completed: false, checkpoint_client_id: 'audit', checkpoint_sequence: 0, checkpoint_revision: 0, cleared: false }) })
    : route.fulfill({ contentType: 'application/json', body: route.request().postData() ?? '{}' }));
  await page.route(`**/api/remote-streams/${streamId}/**`, (route) => hlsRoute(route, streamId, live, state));
}

function requireBaseURL(baseURL: string | undefined): string {
  if (!baseURL) throw new Error('audit video requires Playwright baseURL');
  return baseURL;
}

async function measureHls(browser: Browser, baseURL: string, source: string, streamId: string, live: boolean, trial: number): Promise<Record<string, unknown>> {
  const context = await browser.newContext({ baseURL, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript(installProbe);
  const page = await context.newPage();
  const hlsState: HlsState = { playlist_reads: 0 };
  try {
    await installHlsRoutes(page, source, streamId, live, hlsState);
    const started = await openMockedWatch(page, source);
    await expect.poll(() => page.evaluate(() => (window as AuditWindow).__videoAudit?.first_frame ?? null), { timeout: 30_000 }).not.toBeNull();
    const first = await page.evaluate(() => (window as AuditWindow).__videoAudit!.first_frame!);
    const video = page.locator('video');
    let seek: Record<string, unknown> | null = null;
    if (!live) {
      const seekAt = await page.evaluate(() => {
        const element = document.querySelector('video') as HTMLVideoElement;
        element.currentTime = 12;
        return performance.now();
      });
      const frame = await waitFrameAfter(page, seekAt, 30_000, null, null, 12);
      seek = { target_seconds: 12, elapsed_to_decoded_frame_ms: frame ? Math.round(frame.at_ms - seekAt) : null, landed_seconds: frame?.media_time ?? null };
    }
    const observationStart = await page.evaluate(() => performance.now());
    const start = await videoState(page);
    await page.waitForTimeout(OBSERVATION_MS);
    const observationEnd = await page.evaluate(() => performance.now());
    const end = await videoState(page);
    await video.hover();
    return {
      trial, upstream: 'frontend-route synthetic HLS; backend resolver/relay and external network excluded', live, browser_cache: 'cold-context', server_cache: 'warm-or-unknown',
      click_to_first_decoded_frame_ms: Math.round(first.at_ms - started), decoded_size: { width: first.width, height: first.height },
      seek, observation: { duration_ms: Math.round(observationEnd - observationStart), clock_advance_seconds: Number((end.time - start.time).toFixed(3)), stalls: stalls(end.probe.events, observationStart, observationEnd), dropped_frames: qualityDelta(start.quality, end.quality) },
      browser_heap: await browserMemory(page), resource_bytes: end.resource_bytes,
      rolling_playlist_reads: live ? hlsState.playlist_reads : null,
      qualification: live ? 'Current-edge rolling playlist only. No DVR archive or seek was supplied or measured.' : 'VOD seek uses the browser player against a static fixture playlist.',
    };
  } finally {
    await context.close();
  }
}

test('remote HLS VOD frontend-only synthetic upstream: three cold contexts', async ({ browser, baseURL }, testInfo) => {
  const runs: Array<Record<string, unknown>> = [];
  for (let trial = 1; trial <= RUNS; trial += 1) {
    runs.push(await measureHls(browser, requireBaseURL(baseURL), HLS_VOD_SOURCE, 'audit-hls-vod', false, trial));
    await record(testInfo, 'audit-video-remote-hls-vod', { schema: 1, runs, complete: runs.length === RUNS });
  }
  await record(testInfo, 'audit-video-remote-hls-vod', { schema: 1, runs, statistics: 'Raw samples only; N=3 is not p95/p99.', qualification: 'Frontend HLS adaptation only; no backend RemoteStreamingService relay or network path is included.' });
});

test('remote rolling HLS live current-edge frontend-only: three cold contexts', async ({ browser, baseURL }, testInfo) => {
  const runs: Array<Record<string, unknown>> = [];
  for (let trial = 1; trial <= RUNS; trial += 1) {
    runs.push(await measureHls(browser, requireBaseURL(baseURL), HLS_LIVE_SOURCE, 'audit-hls-live', true, trial));
    await record(testInfo, 'audit-video-remote-hls-live', { schema: 1, runs, complete: runs.length === RUNS });
  }
  await record(testInfo, 'audit-video-remote-hls-live', { schema: 1, runs, statistics: 'Raw samples only; N=3 is not p95/p99.', qualification: 'A local intercepted rolling playlist exercises frontend current-edge playback. It has no DVR archive, no live seek, no backend relay, and no upstream/network qualification.' });
});

test('audit manifest records deliberate remote/live and track-selection gaps', async ({}, testInfo) => {
  await record(testInfo, 'audit-video-manifest', {
    schema: 1,
    generated_at: new Date().toISOString(),
    runs_per_fixture: RUNS,
    source_observation_ms: SOURCE_OBSERVATION_MS,
    observation_ms: OBSERVATION_MS,
    fixtures: FIXTURES,
    statistics: 'Raw samples only. N=3 is a startup smoke comparison, never a p95/p99 estimate.',
    cache: 'new Chrome context clears browser cache; the synthetic fixture server and macOS filesystem cache remain warm-or-unknown.',
    coverage: ['local-library direct/remux/transcode/HEVC/long-GOP/WebM', 'local multi-audio/text-subtitle seek continuity', 'frontend-only synthetic DASH quality selection', 'frontend-only synthetic HLS VOD seek', 'frontend-only synthetic rolling HLS live current edge'],
    unsupported: ['backend remote resolver/relay and real network paths', 'live DVR archive and seek', 'native decoder/GPU allocation'],
  });
});

test('multi-audio and text subtitles preserve a seek after selection', async ({ browser, baseURL }, testInfo) => {
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript(installProbe);
  const page = await context.newPage();
  const sessionBodies: Record<string, unknown>[] = [];
  const ownedSessions = new Set<string>();
  page.on('response', (response) => {
    if (response.request().method() !== 'POST' || !/\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(response.url()).pathname)) return;
    void sessionId(response).then((id) => { if (id) ownedSessions.add(id); });
  });
  page.on('request', (request) => {
    if (request.method() !== 'POST' || !/\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(request.url()).pathname)) return;
    try { sessionBodies.push(JSON.parse(request.postData() ?? '{}') as Record<string, unknown>); } catch { /* evidence remains best effort */ }
  });
  try {
    await page.goto('/library');
    const id = await libraryItemId(page, 'Perf Tracks');
    await resetLocalTrialState(page, requireBaseURL(baseURL), id);
    await openWatch(page, id);
    await expect.poll(() => page.evaluate(() => (window as AuditWindow).__videoAudit?.first_frame ?? null), { timeout: 30_000 }).not.toBeNull();
    await page.locator('video').hover();
    await page.getByRole('button', { name: 'Playback settings' }).click();
    const audio = page.locator('input[type="radio"][name$="-audio"]');
    const subtitles = page.locator('input[type="radio"][name$="-subtitles"]');
    expect(await audio.count(), 'two generated audio choices').toBeGreaterThanOrEqual(2);
    expect(await subtitles.count(), 'off plus generated text subtitle').toBeGreaterThanOrEqual(2);
    const awaitSuccessfulAudioSession = () => page.waitForResponse((response) => response.request().method() === 'POST'
      && /\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(response.url()).pathname)
      && response.ok(), { timeout: 5_000 }).catch(() => null);
    const audioAt = await page.evaluate(() => performance.now());
    const audioResponse = awaitSuccessfulAudioSession();
    await audio.last().check({ force: true });
    const audioSession = await audioResponse;
    const audioFrame = audioSession ? await waitFrameAfter(page, audioAt) : null;
    expect(audioSession, 'audio selection must acknowledge a playback session within five seconds').not.toBeNull();
    expect(audioFrame, 'audio selection must emit a new decoded frame').not.toBeNull();
    await subtitles.last().check({ force: true });
    // VTT selection is browser-local and may not change the playback session.
    // It qualifies only after the selected text track is showing with a cue.
    await expect.poll(() => page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      return Array.from(video.textTracks).some((track) => track.mode === 'showing' && (track.cues?.length ?? 0) > 0);
    }), { timeout: 5_000 }).toBe(true);
    const subtitle = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      const showing = Array.from(video.textTracks).find((track) => track.mode === 'showing');
      return { showing: Boolean(showing), loaded_cues: showing?.cues?.length ?? 0 };
    });
    await page.keyboard.press('Escape');
    const seekAt = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      video.currentTime = 12;
      return performance.now();
    });
    const landed = await waitFrameAfter(page, seekAt, 30_000, null, null, 12);
    expect(landed, 'decoded frame after selecting tracks and seeking').not.toBeNull();
    const selected = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      return { time: video.currentTime, text_tracks_showing: Array.from(video.textTracks).filter((track) => track.mode === 'showing').length };
    });
    await record(testInfo, 'audit-video-multi-audio-subtitle', {
      schema: 1, fixture: 'Perf Tracks', selected_session_requests: sessionBodies,
      audio_selection: { successful_session_and_next_decoded_frame: audioFrame },
      subtitle_selection: subtitle,
      seek_after_selection: { at_ms: seekAt, landed, current_time: selected.time }, text_tracks_showing: selected.text_tracks_showing,
    });
  } finally {
    await stopOwnedSessions(page, ownedSessions);
    await context.close();
  }
});

for (const fixture of FIXTURES) {
  test(`${fixture.name}: three cold-context decoded playback trials`, async ({ browser, baseURL }, testInfo) => {
    test.setTimeout(240_000);
    const results: Result[] = [];
    for (let trial = 1; trial <= RUNS; trial += 1) {
      results.push(await measure(browser, baseURL!, fixture, trial));
      await record(testInfo, `audit-video-${fixture.name}`, { schema: 1, fixture, runs: results, complete: results.length === RUNS });
    }
    await record(testInfo, `audit-video-${fixture.name}`, {
      schema: 1,
      generated_at: new Date().toISOString(),
      fixture,
      runs: results,
      statistics: 'Raw samples only. Do not infer p95/p99 from three trials.',
    });
    const unavailable = results.filter((result) => result.outcomes.resume !== 'passed' || result.outcomes.quality !== 'passed');
    expect(unavailable, `${fixture.name} retained raw samples with failed or unsupported resume/quality outcomes`).toEqual([]);
  });
}

test('MPEG4 seek diagnostic records the absolute timeline after an early converted-HLS seek', async ({ browser, baseURL }, testInfo) => {
  test.skip(!SEEK_DIAGNOSTIC, 'Set AUDIT_VIDEO_SEEK_DIAGNOSTIC=1 to collect one bounded MPEG4 seek diagnostic.');
  test.setTimeout(75_000);
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript(installProbe);
  const page = await context.newPage();
  const ownedSessions = new Set<string>();
  const sessionEvents: Array<{ requested_start: string | null; status: number; returned_start: number | null; mode: string | null; kind: string | null }> = [];
  const captureSession = (response: import('@playwright/test').Response) => {
    if (response.request().method() !== 'POST' || !/\/api\/library\/[^/]+\/playback-sessions$/.test(new URL(response.url()).pathname)) return;
    const requestedStart = new URL(response.url()).searchParams.get('start');
    void response.json().then((body: unknown) => {
      const value = body as { session_id?: unknown; start?: unknown; mode?: unknown; kind?: unknown } | null;
      if (typeof value?.session_id === 'string') ownedSessions.add(value.session_id);
      sessionEvents.push({
        requested_start: requestedStart,
        status: response.status(),
        returned_start: typeof value?.start === 'number' ? value.start : null,
        mode: typeof value?.mode === 'string' ? value.mode : null,
        kind: typeof value?.kind === 'string' ? value.kind : null,
      });
    }).catch(() => sessionEvents.push({ requested_start: requestedStart, status: response.status(), returned_start: null, mode: null, kind: null }));
  };
  page.on('response', captureSession);
  try {
    await page.goto('/library');
    const id = await libraryItemId(page, 'Perf Encode');
    await resetLocalTrialState(page, baseURL!, id);
    const clickAt = await openWatch(page, id);
    await expect.poll(() => page.evaluate(() => (window as AuditWindow).__videoAudit?.first_frame ?? null), { timeout: 30_000 }).not.toBeNull();
    const first = await page.evaluate(() => (window as AuditWindow).__videoAudit!.first_frame!);
    const before = await videoState(page);
    const seekAt = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      video.currentTime = 60;
      return performance.now();
    });
    const landed = await waitFrameAfter(page, seekAt, 30_000, null, null, 60);
    const after = await videoState(page);
    const media = await page.evaluate(() => {
      const video = document.querySelector('video') as HTMLVideoElement;
      return {
        current_time: video.currentTime,
        duration: video.duration,
        seeking: video.seeking,
        paused: video.paused,
        ready_state: video.readyState,
        seekable: Array.from({ length: video.seekable.length }, (_unused, index) => ({ start: video.seekable.start(index), end: video.seekable.end(index) })),
      };
    });
    await page.waitForTimeout(50); // permit the response listener to consume the final POST body
    await record(testInfo, 'audit-video-transcode-mpeg4-seek-diagnostic', {
      schema: 1,
      fixture: 'transcode-mpeg4',
      qualification: 'One cold context, bounded 30-second post-seek decoded-frame wait. This diagnoses a failed matrix outcome; it is not a replacement benchmark sample.',
      click_to_first_decoded_frame_ms: Math.round(first.at_ms - clickAt),
      before_seek: { current_time: before.time, decoded_frame: before.probe.last_frame, decoded_size: { width: before.width, height: before.height } },
      seek: { target_seconds: 60, at_ms: seekAt, landed_frame: landed, decoded_at_target: Boolean(landed), media, last_frame: after.probe.last_frame, events_since_seek: after.probe.events.filter((event) => event.at_ms >= seekAt) },
      playback_session_events: sessionEvents,
    });
    expect(first).not.toBeNull();
  } finally {
    page.off('response', captureSession);
    await stopOwnedSessions(page, ownedSessions);
    await context.close();
  }
});
