/**
 * Opt-in raw evidence for music and book readers. These are correctness-backed
 * measurements, not CI budgets: capabilities that a browser/product lacks are
 * recorded explicitly instead of being represented as zero milliseconds.
 */
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Browser, type BrowserContext, type Locator, type Page, type TestInfo } from '@playwright/test';

const ROOT = process.env.AUDIT_READERS_ROOT!;
const OWNER_STATE = path.join(ROOT, 'owner.json');
const RUNS = 3;
const LONG_FORM = process.env.AUDIT_READERS_LONG_FORM === '1';
const UNPROBED = process.env.AUDIT_READERS_UNPROBED === '1';
type Sample = Record<string, unknown>;

function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * p) - 1)];
}

function summarize(values: number[]): Record<string, number | string | null> {
  return {
    samples: values.length,
    p50: percentile(values, .5),
    p95: values.length >= 20 ? percentile(values, .95) : null,
    p95_status: values.length >= 20 ? 'reported' : 'suppressed_below_20_samples',
  };
}

function readerLabel(format: 'epub' | 'pdf' | 'cbz' | 'cbr'): string {
  return format === 'epub' ? 'Book chapter' : format === 'pdf' ? 'Book pages' : 'Manga pages';
}

async function waitForReaderContent(page: Page, format: 'epub' | 'pdf' | 'cbz' | 'cbr'): Promise<Locator> {
  const reader = page.getByLabel(readerLabel(format));
  await expect(reader).toBeVisible();
  if (format === 'epub') {
    const prose = reader.locator('.books-prose');
    await expect(prose).toBeVisible();
    await expect.poll(async () => (await prose.innerText()).trim().length > 0, { timeout: 30_000 }).toBe(true);
  } else {
    const image = reader.locator('img').first();
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0 && node.naturalHeight > 0), { timeout: 30_000 }).toBe(true);
  }
  return reader;
}

function writeEvidence(info: TestInfo, name: string, evidence: Record<string, unknown>) {
  const body = JSON.stringify(evidence, null, 2) + '\n';
  const target = info.outputPath(`${name}.json`);
  writeFileSync(target, body);
  return info.attach(`${name}.json`, { body: Buffer.from(body), contentType: 'application/json' });
}

async function heapSupplement(page: Page): Promise<Record<string, unknown>> {
  return page.evaluate(async () => {
    const performanceWithMemory = performance as Performance & { memory?: { usedJSHeapSize?: number; totalJSHeapSize?: number }; measureUserAgentSpecificMemory?: () => Promise<{ bytes: number }> };
    if (typeof performanceWithMemory.measureUserAgentSpecificMemory === 'function') {
      try { return { capability: 'measureUserAgentSpecificMemory', bytes: (await performanceWithMemory.measureUserAgentSpecificMemory()).bytes }; }
      catch (error) { return { capability: 'measureUserAgentSpecificMemory', unavailable: String(error) }; }
    }
    if (performanceWithMemory.memory?.usedJSHeapSize !== undefined) return { capability: 'performance.memory_nonstandard', used_js_heap_bytes: performanceWithMemory.memory.usedJSHeapSize, total_js_heap_bytes: performanceWithMemory.memory.totalJSHeapSize };
    return { capability: 'browser_js_heap_api_unavailable' };
  });
}

async function requestEvidence(page: Page, origin: string, work: () => Promise<void>): Promise<{ declared_content_length_bytes: number; same_origin_responses: number; missing_content_length_responses: number }> {
  let declaredContentLengthBytes = 0;
  let sameOriginResponses = 0;
  let missingContentLengthResponses = 0;
  const onResponse = (response: import('@playwright/test').Response) => {
    if (new URL(response.url()).origin !== origin) return;
    sameOriginResponses += 1;
    const length = Number(response.headers()['content-length']);
    if (Number.isFinite(length) && length > 0) declaredContentLengthBytes += length;
    else missingContentLengthResponses += 1;
  };
  page.on('response', onResponse);
  try { await work(); } finally { page.off('response', onResponse); }
  return { declared_content_length_bytes: declaredContentLengthBytes, same_origin_responses: sameOriginResponses, missing_content_length_responses: missingContentLengthResponses };
}

type AudioStart = { at_ms: number; context_time_at_call_seconds: number; when_seconds: number | undefined; offset_seconds: number | undefined; duration_argument_seconds: number | undefined; buffer_duration_seconds: number | null };
type AudioAudit = { starts: AudioStart[]; decode_calls: number; media_playing_at_ms: number | null; media_seeked_current_time_seconds: number | null };

async function musicContext(browser: Browser, baseURL: string, rejectDecodeAttempts = 0): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript((remainingDecodeFailures) => {
    type AuditWindow = Window & { __auditAudio?: AudioAudit };
    const audit: AudioAudit = { starts: [], decode_calls: 0, media_playing_at_ms: null, media_seeked_current_time_seconds: null };
    (window as AuditWindow).__auditAudio = audit;
    const originalStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when?: number, offset?: number, duration?: number) {
      audit.starts.push({ at_ms: performance.now(), context_time_at_call_seconds: this.context.currentTime, when_seconds: when, offset_seconds: offset, duration_argument_seconds: duration, buffer_duration_seconds: this.buffer?.duration ?? null });
      return originalStart.call(this, when, offset, duration);
    };
    // The streaming engine keeps its audio element detached, so document-level
    // media events cannot observe it. Attach before play() starts to retain the
    // browser's genuine playing event timing.
    const originalMediaPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      this.addEventListener('playing', () => { audit.media_playing_at_ms ??= performance.now(); }, { once: true });
      this.addEventListener('seeked', () => { audit.media_seeked_current_time_seconds = this.currentTime; });
      return originalMediaPlay.call(this);
    };
    const originalDecode = AudioContext.prototype.decodeAudioData;
    let failuresLeft = remainingDecodeFailures;
    AudioContext.prototype.decodeAudioData = function (audioData: ArrayBuffer, successCallback?: DecodeSuccessCallback | null, errorCallback?: DecodeErrorCallback | null) {
      audit.decode_calls += 1;
      if (failuresLeft > 0) {
        failuresLeft -= 1;
        const error = new DOMException('Audit forces decode fallback', 'NotSupportedError');
        errorCallback?.(error);
        return Promise.reject(error);
      }
      return originalDecode.call(this, audioData, successCallback, errorCallback);
    };
  }, rejectDecodeAttempts);
  return { context, page: await context.newPage() };
}

async function audioAudit(page: Page): Promise<AudioAudit> {
  return page.evaluate(() => (window as Window & { __auditAudio?: AudioAudit }).__auditAudio!);
}

async function startAlbum(page: Page): Promise<number> {
  await page.goto('/audio');
  await expect(page.getByRole('button', { name: 'Play artist' })).toBeVisible({ timeout: 30_000 });
  const at = await page.evaluate(() => performance.now());
  await page.getByRole('button', { name: 'Play artist' }).click();
  await expect.poll(() => audioAudit(page).then((audit) => audit.starts.length), { timeout: 30_000 }).toBeGreaterThan(0);
  return at;
}

async function openStreamControl(page: Page): Promise<void> {
  await page.goto('/audio');
  await expect(page.getByRole('button', { name: 'Play artist' })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Play artist' }).click();
  await page.getByRole('button', { name: 'Open listening room', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Stream track' })).toBeVisible({ timeout: 30_000 });
}

async function captureBoundarySchedule(page: Page, crossfadeSeconds: number): Promise<Sample> {
  const firstStart = await startAlbum(page);
  await page.getByRole('button', { name: 'Open listening room', exact: true }).click();
  const room = page.getByRole('dialog', { name: 'Listening room' });
  let reconfiguredAt: number | null = null;
  if (crossfadeSeconds) {
    reconfiguredAt = await page.evaluate(() => performance.now());
    await room.getByLabel('Crossfade').selectOption(String(crossfadeSeconds));
  }
  await expect.poll(async () => (await audioAudit(page)).starts.some((entry) => entry.when_seconds !== undefined && entry.when_seconds > entry.context_time_at_call_seconds + .1 && entry.offset_seconds === 0 && entry.buffer_duration_seconds !== null && entry.buffer_duration_seconds > 0 && (reconfiguredAt === null || entry.at_ms >= reconfiguredAt)), { timeout: 30_000 }).toBe(true);
  const starts = (await audioAudit(page)).starts;
  const scheduled = starts.filter((entry) => entry.when_seconds !== undefined && entry.when_seconds > entry.context_time_at_call_seconds + .1 && entry.offset_seconds === 0 && entry.buffer_duration_seconds !== null && entry.buffer_duration_seconds > 0 && (reconfiguredAt === null || entry.at_ms >= reconfiguredAt));
  return { first_source_start_proxy_ms: Math.round(starts[0].at_ms - firstStart), crossfade_seconds: crossfadeSeconds, reconfigured_at_ms: reconfiguredAt, source_start_calls: starts, boundary_schedule: scheduled, schedule_clock_proof: 'No seek or manual track step occurs in this case. A prefetched successor starts more than 100 ms in the future of the AudioContext clock, at offset zero, with a decoded buffer duration. This proves scheduling, not physical audible continuity.' };
}

async function close(context: BrowserContext) { await context.close().catch(() => undefined); }

test('music decoded, native-streaming fallback, offline, and scheduling evidence', async ({ browser, baseURL }, info) => {
  // This case measures the known-short buffered path. Unknown facts deliberately
  // stream now; qualify real probe facts before timing rather than relying on a
  // background warmer winning a race on a busy host. The separate long case
  // explicitly exercises the unprobed primary action without this setup.
  const ready = await browser.newContext({ baseURL, storageState: OWNER_STATE });
  let metadata: Sample | null = null;
  try {
    const albums = await (await ready.request.get('/api/titles?type=album&limit=100')).json() as { items: Array<{ id: string; name: string }> };
    const album = albums.items.find((entry) => entry.name === 'Audit Album');
    expect(album).toBeTruthy();
    const url = `/api/music/titles/${album!.id}/tracks`;
    type TrackFacts = { id: string; duration: number | null; sample_rate: number | null; channels: number | null };
    const before = await (await ready.request.get(url)).json() as TrackFacts[];
    expect(before).toHaveLength(4);
    for (const track of before) expect((await ready.request.get(`/api/library/${track.id}/playback-options`)).ok()).toBe(true);
    const after = await (await ready.request.get(url)).json() as TrackFacts[];
    expect(after.every((track) => [track.duration, track.sample_rate, track.channels].every((value) => value !== null && value > 0))).toBe(true);
    metadata = { setup: 'Ordinary synchronous playback-options probe for all four short tracks, excluded from playback timings; not cold-import readiness.', before, after };
    await writeEvidence(info, 'music-readiness', metadata);
  } finally { await ready.close(); }
  const decoded: Sample[] = [];
  let codecCoverage: Sample[] = [];
  for (let trial = 1; trial <= RUNS; trial += 1) {
    const { context, page } = await musicContext(browser, baseURL!);
    try {
      const beforeHeap = await heapSupplement(page);
      let clickAt = 0;
      const network = await requestEvidence(page, baseURL!, async () => { clickAt = await startAlbum(page); });
      const state = await page.evaluate(() => (window as Window & { __auditAudio?: { starts: Array<{ at_ms: number }> } }).__auditAudio!);
      expect(state.starts.length).toBeGreaterThan(0);
      decoded.push({ trial, audio_clock_schedule_proxy_ms: Math.round(state.starts[0].at_ms - clickAt), ...network, js_heap_before: beforeHeap, js_heap_after: await heapSupplement(page) });
      await writeEvidence(info, 'music-progress', { stage: 'decoded_cold_contexts', completed_trials: decoded });
      if (trial === 1) {
        await page.getByRole('button', { name: 'Open listening room', exact: true }).click();
        const room = page.getByRole('dialog', { name: 'Listening room' });
        codecCoverage = [{ track: await room.locator('.music-room-record h3').innerText(), quality: await room.locator('.music-quality').innerText() }];
        for (let index = 0; index < 3; index += 1) {
          const before = await page.evaluate(() => (window as Window & { __auditAudio?: { starts: unknown[] } }).__auditAudio?.starts.length ?? 0);
          await room.getByRole('button', { name: 'Next track' }).click();
          await expect.poll(() => page.evaluate(() => (window as Window & { __auditAudio?: { starts: unknown[] } }).__auditAudio?.starts.length ?? 0), { timeout: 30_000 }).toBeGreaterThan(before);
          codecCoverage.push({ track: await room.locator('.music-room-record h3').innerText(), quality: await room.locator('.music-quality').innerText() });
        }
      }
    } finally { await close(context); }
  }

  const fixtureCodecByTitle = { 'Audit Track 1': 'flac', 'Audit Track 2': 'mp3', 'Audit Track 3': 'aac_in_m4a', 'Audit Track 4': 'opus' } as const;
  const titles = codecCoverage.map((entry) => entry.track);
  expect(titles).toEqual(Object.keys(fixtureCodecByTitle));
  const codecEvidence = codecCoverage.map((entry) => ({ ...entry, fixture_codec: fixtureCodecByTitle[entry.track as keyof typeof fixtureCodecByTitle] }));
  expect(codecEvidence.every((entry) => entry.fixture_codec)).toBe(true);
  await writeEvidence(info, 'music-progress', { stage: 'codec_coverage', decoded_cold_contexts: decoded, codec_coverage: codecEvidence, player_quality_capability: 'unqualified_source_audio_label' });

  const { context: wavContext, page: wavPage } = await musicContext(browser, baseURL!, 1);
  let wavFallback: Sample = {};
  try {
    const wavResponse = wavPage.waitForResponse((response) => response.url().includes('/audio?format=wav'));
    const clickAt = await startAlbum(wavPage);
    const response = await wavResponse;
    const responseUrl = new URL(response.url());
    expect(responseUrl.searchParams.get('format')).toBe('wav');
    await wavPage.getByRole('button', { name: 'Open listening room', exact: true }).click();
    const quality = await wavPage.getByRole('dialog', { name: 'Listening room' }).locator('.music-quality').innerText();
    expect(quality).toContain('Automatic PCM fallback');
    wavFallback = { audio_clock_schedule_proxy_ms: Math.round((await audioAudit(wavPage)).starts[0].at_ms - clickAt), response_path: responseUrl.pathname, response_format: responseUrl.searchParams.get('format'), response_content_type: response.headers()['content-type'] ?? null, quality_label: quality, proof: 'The native decode was deliberately rejected once; the observed fallback request selected the server WAV conversion.' };
  } finally { await close(wavContext); }
  await writeEvidence(info, 'music-progress', { stage: 'wav_server_fallback', decoded_cold_contexts: decoded, codec_coverage: codecEvidence, wav_server_fallback: wavFallback });

  const { context: scheduleContext, page: schedulePage } = await musicContext(browser, baseURL!);
  let schedule: Sample = {};
  try { schedule = await captureBoundarySchedule(schedulePage, 3); }
  finally { await close(scheduleContext); }
  await writeEvidence(info, 'music-progress', { stage: 'crossfade_schedule', decoded_cold_contexts: decoded, codec_coverage: codecEvidence, wav_server_fallback: wavFallback, crossfade_schedule: schedule });

  const { context: gaplessContext, page: gaplessPage } = await musicContext(browser, baseURL!);
  let gapless: Sample = {};
  try { gapless = await captureBoundarySchedule(gaplessPage, 0); }
  finally { await close(gaplessContext); }
  await writeEvidence(info, 'music-progress', { stage: 'gapless_schedule', decoded_cold_contexts: decoded, codec_coverage: codecEvidence, wav_server_fallback: wavFallback, crossfade_schedule: schedule, gapless_schedule: gapless });

  const { context: streamContext, page: streamPage } = await musicContext(browser, baseURL!, 2);
  let streaming: Sample = {};
  try {
    await openStreamControl(streamPage);
    const clickAt = await streamPage.evaluate(() => performance.now());
    await streamPage.getByRole('button', { name: 'Stream track' }).click();
    await expect.poll(() => audioAudit(streamPage).then((audit) => audit.media_playing_at_ms), { timeout: 30_000 }).not.toBeNull();
    const onset = (await audioAudit(streamPage)).media_playing_at_ms!;
    streaming = { native_media_playing_event_ms: Math.round(onset - clickAt), decode_failures_forced: 2, proxy: 'Detached HTMLMediaElement playing event after explicit Stream track; not acoustic output onset.' };
  } finally { await close(streamContext); }
  await writeEvidence(info, 'music-progress', { stage: 'native_streaming', decoded_cold_contexts: decoded, codec_coverage: codecEvidence, wav_server_fallback: wavFallback, crossfade_schedule: schedule, gapless_schedule: gapless, native_streaming: streaming });

  const { context: offlineContext, page: offlinePage } = await musicContext(browser, baseURL!);
  let offline: Sample = {};
  try {
    await startAlbum(offlinePage);
    await offlinePage.getByRole('button', { name: 'Open listening room', exact: true }).click();
    const savedAt = await offlinePage.evaluate(() => performance.now());
    await offlinePage.getByRole('button', { name: 'Save track' }).click();
    await expect(offlinePage.getByRole('button', { name: 'Track available offline' })).toBeVisible({ timeout: 60_000 });
    const savedAtDone = await offlinePage.evaluate(() => performance.now());
    await offlinePage.goto('/music-offline.html');
    await expect(offlinePage.getByRole('button', { name: 'Play music' })).toBeEnabled({ timeout: 30_000 });
    await offlineContext.setOffline(true);
    const clickAt = await offlinePage.evaluate(() => performance.now());
    await offlinePage.getByRole('button', { name: 'Play music' }).click();
    await expect.poll(() => offlinePage.evaluate(() => (window as Window & { __auditAudio?: { starts: unknown[] } }).__auditAudio?.starts.length ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);
    const starts = (await audioAudit(offlinePage)).starts;
    const startedAt = starts[starts.length - 1].at_ms;
    offline = { save_track_ms: Math.round(savedAtDone - savedAt), offline_audio_clock_schedule_proxy_ms: Math.round(startedAt - clickAt), offline_network: true, proxy: 'AudioBufferSourceNode.start; not acoustic output onset.' };
  } finally { await close(offlineContext); }

  const values = decoded.map((entry) => Number(entry.audio_clock_schedule_proxy_ms));
  await writeEvidence(info, 'music', {
    generated_at: new Date().toISOString(),
    measurement: 'Audio clock scheduling and HTMLMediaElement playing are browser event proxies, not microphone-measured audible onset.',
    metadata_readiness: metadata,
    decoded_cold_contexts: decoded,
    decoded_summary_ms: summarize(values),
    codec_coverage: codecEvidence,
    codec_matrix: {
      flac: 'real synthetic queue member', mp3: 'real synthetic queue member', aac_m4a: 'real AAC-in-M4A synthetic queue member', opus: 'real synthetic queue member',
      wav_server_fallback: wavFallback,
    },
    gapless_schedule: gapless,
    crossfade_schedule: schedule,
    native_streaming: streaming,
    offline,
    capabilities: { audiobook: { status: 'product_capability_absent' }, physical_output: { status: 'not_benchmarked_requires_device_lab' } },
  });
});

test('long-form FLAC automatically uses the native streaming path instead of decoded memory', async ({ browser, baseURL }, info) => {
  test.skip(!LONG_FORM, 'The long-form fixture is generated only for the isolated long-form audit.');
  const { context, page } = await musicContext(browser, baseURL!);
  let evidence: Sample = {};
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  try {
    const albums = await (await page.request.get('/api/titles?type=album&limit=100')).json() as { items: Array<{ id: string; name: string }> };
    const album = albums.items.find((entry) => entry.name === 'Audit Long Album');
    expect(album, 'long-form album fixture').toBeTruthy();
    type LongTrack = { id: string; title: string; duration: number | null; sample_rate: number | null; channels: number | null };
    const listedTracks = await (await page.request.get(`/api/music/titles/${album!.id}/tracks`)).json() as LongTrack[];
    let longTrack = listedTracks.find((entry) => entry.title === 'Audit Long Track');
    expect(longTrack, 'long-form track fixture').toBeTruthy();
    // The normal playback-options endpoint synchronously uses MediaProbeService
    // for a selected item. This is the production readiness path when its
    // background cache worker has not reached a newly scanned long file yet.
    if (!UNPROBED) {
      const probe = await page.request.get(`/api/library/${longTrack!.id}/playback-options`);
      expect(probe.ok()).toBe(true);
      await expect.poll(async () => {
        const tracks = await (await page.request.get(`/api/music/titles/${album!.id}/tracks`)).json() as LongTrack[];
        longTrack = tracks.find((entry) => entry.title === 'Audit Long Track');
        return [longTrack?.duration, longTrack?.sample_rate, longTrack?.channels].join(':');
      }, { timeout: 90_000 }).toBe('768:48000:2');
      expect(longTrack, 'long-form track metadata').toBeTruthy();
      expect(longTrack!.duration).toBeGreaterThanOrEqual(767);
      expect(longTrack!.sample_rate).toBe(48_000);
      expect(longTrack!.channels).toBe(2);
    } else {
      expect([longTrack!.duration, longTrack!.sample_rate, longTrack!.channels]).toEqual([null, null, null]);
    }
    await page.goto(`/audio/title/${album!.id}`);
    await expect(page.getByRole('heading', { level: 1, name: 'Audit Long Album' })).toBeVisible({ timeout: 30_000 });
    // An oversized, fully-probed item is intentionally selected for native
    // streaming by musicSession.startMusicQueue. Clear prior media markers so
    // this records the primary Play interaction, not route/bootstrap activity.
    await page.evaluate(() => {
      const audit = (window as Window & { __auditAudio?: AudioAudit }).__auditAudio!;
      audit.media_playing_at_ms = null;
      audit.media_seeked_current_time_seconds = null;
    });
    const clickAt = await page.evaluate(() => performance.now());
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await expect.poll(() => audioAudit(page).then((audit) => audit.media_playing_at_ms), { timeout: 30_000 }).not.toBeNull();
    await page.locator('button.music-dock-track').click();
    const room = page.getByRole('dialog', { name: 'Listening room' });
    await expect(room.locator('.music-room-record h3')).toHaveText('Audit Long Track');
    await expect(room.locator('.music-quality')).toContainText('Browser streaming');
    const afterPrimaryPlay = await audioAudit(page);
    expect(afterPrimaryPlay.media_playing_at_ms).toBeGreaterThanOrEqual(clickAt);
    expect(afterPrimaryPlay.starts).toHaveLength(0);
    expect(afterPrimaryPlay.decode_calls).toBe(0);

    const position = page.getByLabel('Track position');
    await page.evaluate(() => { (window as Window & { __auditAudio?: AudioAudit }).__auditAudio!.media_seeked_current_time_seconds = null; });
    await position.fill('60');
    await expect.poll(() => audioAudit(page).then((audit) => {
      const value = audit.media_seeked_current_time_seconds;
      return value !== null && value >= 59 && value <= 61;
    }), { timeout: 30_000 }).toBe(true);
    const afterSeek = await audioAudit(page);
    expect(afterSeek.starts).toHaveLength(0);
    expect(afterSeek.decode_calls).toBe(0);
    evidence = {
      track_metadata: { id: longTrack!.id, duration_seconds: longTrack!.duration, sample_rate_hz: longTrack!.sample_rate, channels: longTrack!.channels },
      metadata_readiness: UNPROBED ? 'Background probe warming disabled in isolated fixture; real track API returned null duration/sample-rate/channels. No synchronous probe performed.' : 'GET /api/library/{item_id}/playback-options invoked the normal MediaProbeService path before measurement.',
      expected_decoded_pcm_bytes: UNPROBED ? null : longTrack!.duration! * longTrack!.sample_rate! * longTrack!.channels! * Float32Array.BYTES_PER_ELEMENT,
      automatic_native_streaming: 'The oversized or unprobed track entered Browser streaming from the primary Play action; no Stream track recovery control was required.',
      primary_play_to_native_media_playing_event_ms: Math.round(afterPrimaryPlay.media_playing_at_ms! - clickAt),
      active_track_heading: await room.locator('.music-room-record h3').innerText(),
      quality_label: await room.locator('.music-quality').innerText(),
      audio_audit_after_primary_play: afterPrimaryPlay,
      seek_target_seconds: 60,
      media_seeked_current_time_seconds: afterSeek.media_seeked_current_time_seconds,
      audio_audit_after_seek: afterSeek,
      page_errors: pageErrors,
      proxy: 'Detached HTMLMediaElement playing event after primary Play; not acoustic output onset.',
    };
  } finally { await close(context); }
  await writeEvidence(info, 'long-form-music', {
    generated_at: new Date().toISOString(),
    browser: { name: browser.browserType().name(), version: browser.version() },
    long_form: evidence,
    limitations: ['The browser instrumentation records decodeAudioData calls and AudioBufferSourceNode starts, not a native allocation peak, full-transfer byte count or microphone-measured output.'],
  });
});

test('EPUB, PDF, CBZ, and CBR real reader journeys retain raw turn, request, and JS-heap evidence', async ({ browser, baseURL }, info) => {
  const kinds = [
    { title: 'Audit EPUB', format: 'epub' },
    { title: 'Audit PDF', format: 'pdf' },
    { title: 'Audit CBZ', format: 'cbz' },
    { title: 'Audit CBR', format: 'cbr' },
  ] as const;
  const evidence: Sample[] = [];
  for (const fixture of kinds) {
    const firstUsable: number[] = [];
    const turns: number[] = [];
    const soak: number[] = [];
    const runs: Sample[] = [];
    for (let trial = 1; trial <= RUNS; trial += 1) {
      const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
      const page = await context.newPage();
      try {
        const books = await (await page.request.get('/api/books?limit=100')).json() as { items: Array<{ id: string; title: string; format: string }> };
        const book = books.items.find((entry) => entry.format === fixture.format && (fixture.format === 'epub' ? entry.title === fixture.title : entry.title.includes(fixture.title)));
        expect(book, `${fixture.format} fixture`).toBeTruthy();
        // This clock remains valid across navigation; page performance.now() resets on goto.
        const started = Date.now();
        let reader: Locator;
        const network = await requestEvidence(page, baseURL!, async () => {
          await page.goto(`/books/read/${book!.id}`);
          await expect(page.getByRole('heading', { level: 1, name: new RegExp(fixture.title, 'i') })).toBeVisible();
          reader = await waitForReaderContent(page, fixture.format);
        });
        const usable = Date.now() - started;
        firstUsable.push(usable);
        const warmTurns: number[] = [];
        // Assigned only after the reader has rendered actual prose or decoded page pixels.
        const activeReader = reader!;
        for (let turn = 0; turn < 8; turn += 1) {
          const before = await activeReader.innerHTML();
          const at = await page.evaluate(() => performance.now());
          await page.getByRole('button', { name: 'Next', exact: true }).click();
          await expect.poll(() => activeReader.innerHTML(), { timeout: 30_000 }).not.toBe(before);
          warmTurns.push(Math.round((await page.evaluate(() => performance.now())) - at));
        }
        turns.push(...warmTurns);
        const soakTurns: number[] = [];
        for (let navigation = 1; navigation <= 20; navigation += 1) {
          const before = await activeReader.innerHTML();
          const at = await page.evaluate(() => performance.now());
          await page.getByLabel('Reading position').fill(String((navigation % 10) + 1));
          await expect.poll(() => activeReader.innerHTML(), { timeout: 30_000 }).not.toBe(before);
          soakTurns.push(Math.round((await page.evaluate(() => performance.now())) - at));
        }
        soak.push(...soakTurns);
        runs.push({ trial, first_usable_content_ms: Math.round(usable), warm_turns_ms: warmTurns, bounded_navigation_soak_ms: soakTurns, ...network, js_heap_after_navigation: await heapSupplement(page) });
      } finally { await close(context); }
    }
    evidence.push({ format: fixture.format, cold_contexts: runs, first_usable_ms: summarize(firstUsable), warm_turn_ms: summarize(turns), bounded_navigation_soak_ms: { samples_per_cold_context: 20, ...summarize(soak) } });
    await writeEvidence(info, 'readers-progress', { stage: `completed_${fixture.format}`, reader_runs: evidence });
  }
  await writeEvidence(info, 'readers', {
    generated_at: new Date().toISOString(),
    reader_runs: evidence,
    fixture_scale: { kind: 'one synthetic fixture size per format', formats: { epub: { chapters: 36 }, pdf: { pages: 72 }, cbz: { pages: 28 }, cbr: { pages: 16 } }, comparison_claim: 'No small-versus-large comparison is made.' },
    capabilities: { cbr: { status: 'measured_synthetic_rar4_stored_members' }, audiobook: { status: 'product_capability_absent' } },
    limitations: ['First usable content uses a navigation-safe Node clock. EPUB requires visible non-empty rendered prose; PDF, CBZ, and CBR require a visible, decoded page image with nonzero intrinsic dimensions. It is not paint-to-photon.', 'JS heap is supplemental only; process RSS/host-load evidence belongs to scripts/perf/audit_run.py.'],
  });
});
