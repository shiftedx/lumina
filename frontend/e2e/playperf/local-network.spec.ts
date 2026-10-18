/**
 * Opt-in evidence collection for the local H.264/AAC remux path. The scenarios
 * deliberately have no pass/fail latency budget: the result is a decision input,
 * not a CI performance gate. See docs/research/local-conversion-network-2026-10-17.md.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

import type { Browser, Page, TestInfo } from '@playwright/test';

import { openWatch } from '../realstack/perfProbe';
import { expect, OWNER_STATE, test } from '../realstack/realstack';

const RUNS = 3;
const OBSERVATION_MS = 20_000;
const REMUX_TITLE = 'Perf Remux';
const MEASUREMENT_ENABLED = process.env.LUMINA_RUN_LOCAL_NETWORK_MEASUREMENTS === '1';

type Ceiling = null | 480;
type NetworkCondition = {
  name: 'unshaped' | 'cdp-2.5mbps-50ms';
  latency_ms: number;
  download_bytes_per_second: number;
  upload_bytes_per_second: number;
  packet_loss_percent: number;
};
type Scenario = { name: string; ceiling: Ceiling; network: NetworkCondition; downlink_mbps: number | null; expected_height: number };
type PlaybackOptions = {
  mode: string;
  facts?: { height?: number | null } | null;
  audio_tracks?: Array<{ index: number; default: boolean }>;
};
type SessionResponse = { session_id: string; mode: string; start: number };
type EventSample = { type: 'waiting' | 'stalled' | 'playing' | 'timeupdate'; at: number };
type DecodedFrame = { at: number; height: number; mediaTime: number };
type Observation = { firstDecodedFrame: DecodedFrame | null; events: EventSample[] };
type Measurement = {
  scenario: string;
  trial: number;
  fixture: string;
  network: NetworkCondition;
  playback_max_height: Ceiling;
  browser_downlink_mbps: number | null;
  transient_bandwidth_budget_bps: number | null;
  cold_context: true;
  click_to_first_decoded_frame_ms: number;
  observation_wall_clock_ms: number;
  clock_advance_seconds: number;
  stalls: { count: number; total_duration_ms: number };
  decoded_height: { first_frame: number; at_observation_end: number };
  requested: { position_seconds: number; audio_index: number | null; resolved_default_audio_index: number | null };
  playback: { mode: string; source_height: number | null; session_mode: string; session_start_seconds: number };
};

type ObservedWindow = Window & { __localConversionObservation?: Observation };

const UNSHAPED: NetworkCondition = {
  name: 'unshaped', latency_ms: 0, download_bytes_per_second: -1, upload_bytes_per_second: -1, packet_loss_percent: 0,
};
const SHAPED: NetworkCondition = {
  // CDP throughput is bytes/second. 2.5 Mbit/s / 8 = 312,500 bytes/second.
  name: 'cdp-2.5mbps-50ms', latency_ms: 50, download_bytes_per_second: 2_500_000 / 8, upload_bytes_per_second: -1, packet_loss_percent: 0,
};
const SCENARIOS: readonly Scenario[] = [
  // Chromium reports 10 Mbps for a saturated/faster Network Information value; it must remain uncapped.
  { name: 'unshaped-saturated-reference', ceiling: null, network: UNSHAPED, downlink_mbps: 10, expected_height: 1080 },
  // 2.5 Mbps downlink becomes a conservative 2 Mbps transient cap. It must choose 480p without persisting a member setting.
  { name: 'shaped-automatic-480p', ceiling: null, network: SHAPED, downlink_mbps: 2.5, expected_height: 480 },
  // A saved member choice is still authoritative when the same low hint is present.
  { name: 'shaped-manual-480p', ceiling: 480, network: SHAPED, downlink_mbps: 2.5, expected_height: 480 },
];
const RAW_OUTPUT = process.env.LOCAL_NETWORK_RAW_OUTPUT ?? path.resolve(process.cwd(), '../output/performance-fixes/playback');
const RUN_ID = new Date().toISOString().replaceAll(':', '-');
const SATURATED_NETWORK_DOWNLINK_MBPS = 10;

function transientBudget(downlink: number | null): number | null {
  return downlink === null || downlink >= SATURATED_NETWORK_DOWNLINK_MBPS ? null : Math.floor(downlink * 1_000_000 * 0.8);
}

test.skip(!MEASUREMENT_ENABLED, 'Set LUMINA_RUN_LOCAL_NETWORK_MEASUREMENTS=1 during an isolated measurement window.');
test.describe.configure({ mode: 'serial' });

/** Capture decoded-frame evidence and playback events before the app loads. */
function installObservationProbe(): void {
  const observed: Observation = { firstDecodedFrame: null, events: [] };
  Object.defineProperty(window, '__localConversionObservation', { configurable: true, value: observed });
  const captureEvent = (event: Event) => {
    if (event.target instanceof HTMLVideoElement && ['waiting', 'stalled', 'playing', 'timeupdate'].includes(event.type)) {
      observed.events.push({ type: event.type as EventSample['type'], at: performance.now() });
    }
  };
  for (const type of ['waiting', 'stalled', 'playing', 'timeupdate']) document.addEventListener(type, captureEvent, true);
  document.addEventListener('loadeddata', (event) => {
    const video = event.target;
    if (!(video instanceof HTMLVideoElement) || observed.firstDecodedFrame !== null) return;
    video.requestVideoFrameCallback((at, metadata) => {
      observed.firstDecodedFrame ??= { at, height: video.videoHeight, mediaTime: metadata.mediaTime };
    });
  }, true);
}

/** Install a deterministic Network Information hint before Lumina loads; CDP shaping supplies the actual transport. */
function installNetworkInformation(downlink: number | null): void {
  Object.defineProperty(navigator, 'connection', {
    configurable: true,
    value: downlink === null ? undefined : { downlink, saveData: false },
  });
}

function terminalStalls(events: EventSample[], startAt: number, endAt: number) {
  let began: number | null = null;
  let total = 0;
  let count = 0;
  for (const event of events) {
    if (event.at < startAt || event.at > endAt) continue;
    if ((event.type === 'waiting' || event.type === 'stalled') && began === null) began = event.at;
    if ((event.type === 'playing' || event.type === 'timeupdate') && began !== null) {
      count += 1;
      total += Math.max(0, event.at - began);
      began = null;
    }
  }
  if (began !== null) {
    count += 1;
    total += Math.max(0, endAt - began);
  }
  return { count, total_duration_ms: Math.round(total) };
}

async function configureNetwork(page: Page, network: NetworkCondition): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false,
    latency: network.latency_ms,
    downloadThroughput: network.download_bytes_per_second,
    uploadThroughput: network.upload_bytes_per_second,
    // This is the documented CDP WebRTC loss knob. It is explicitly zero;
    // this experiment injects no loss or reordering.
    packetLoss: network.packet_loss_percent,
    packetQueueLength: 0,
    packetReordering: false,
  });
}

async function sessionCsrf(page: Page): Promise<string> {
  const response = await page.request.get('/api/session/me');
  expect(response.ok(), await response.text()).toBe(true);
  const body = await response.json() as { csrf_token?: unknown };
  expect(typeof body.csrf_token, 'session CSRF token').toBe('string');
  return body.csrf_token as string;
}

async function setCeilingAndClearPosition(page: Page, baseURL: string, itemId: string, ceiling: Ceiling): Promise<void> {
  const csrf = await sessionCsrf(page);
  const headers = { Origin: baseURL, 'X-CSRF-Token': csrf };
  const cleared = await page.request.delete(`/api/library/${itemId}/playback`, { headers });
  expect(cleared.status(), 'clear residual playback position before a cold trial').toBe(204);
  const saved = await page.request.put('/api/settings/me', { headers, data: { ui_prefs: { playback_max_height: ceiling } } });
  expect(saved.ok(), await saved.text()).toBe(true);
  const prefs = await saved.json() as { ui_prefs?: { playback_max_height?: unknown } };
  expect(prefs.ui_prefs?.playback_max_height).toBe(ceiling);
}

async function remuxId(page: Page): Promise<string> {
  const response = await page.request.get('/api/library?limit=100');
  expect(response.ok(), await response.text()).toBe(true);
  const body = await response.json() as { items?: Array<{ id: string; title: string }> };
  const item = body.items?.find((candidate) => candidate.title === REMUX_TITLE);
  expect(item, `${REMUX_TITLE} fixture is indexed`).toBeTruthy();
  return item!.id;
}

function requestPayload(request: import('@playwright/test').Request): Record<string, unknown> {
  const raw = request.postData();
  if (!raw) return {};
  try {
    const body = JSON.parse(raw) as unknown;
    return body && typeof body === 'object' && !Array.isArray(body) ? body as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

async function measureTrial(browser: Browser, baseURL: string, scenario: Scenario, trial: number): Promise<Measurement> {
  const context = await browser.newContext({ baseURL, storageState: OWNER_STATE, viewport: { width: 1600, height: 1000 } });
  await context.addInitScript(installObservationProbe);
  await context.addInitScript(installNetworkInformation, scenario.downlink_mbps);
  const page = await context.newPage();
  let session: SessionResponse | null = null;
  try {
    await configureNetwork(page, scenario.network);
    const itemId = await remuxId(page);
    await setCeilingAndClearPosition(page, baseURL, itemId, scenario.ceiling);

    const optionsResponse = page.waitForResponse((response) => response.request().method() === 'GET'
      && new URL(response.url()).pathname === `/api/library/${itemId}/playback-options`);
    const sessionResponse = page.waitForResponse((response) => response.request().method() === 'POST'
      && new URL(response.url()).pathname === `/api/library/${itemId}/playback-sessions`);
    const sessionRequest = page.waitForRequest((request) => request.method() === 'POST'
      && new URL(request.url()).pathname === `/api/library/${itemId}/playback-sessions`);

    const clickAt = await openWatch(page, itemId);
    const optionsNetworkResponse = await optionsResponse;
    const options = await optionsNetworkResponse.json() as PlaybackOptions;
    session = await (await sessionResponse).json() as SessionResponse;
    const request = await sessionRequest;
    const payload = requestPayload(request);
    const first = await expect.poll(
      () => page.evaluate(() => (window as ObservedWindow).__localConversionObservation?.firstDecodedFrame ?? null),
      { timeout: 30_000 },
    ).not.toBeNull();
    void first;
    const start = await page.evaluate(() => {
      const observation = (window as ObservedWindow).__localConversionObservation;
      const video = document.querySelector('video') as HTMLVideoElement | null;
      if (!observation?.firstDecodedFrame || !video) throw new Error('Missing decoded-frame observation');
      return { frame: observation.firstDecodedFrame, clock: video.currentTime };
    });

    await page.waitForTimeout(OBSERVATION_MS);
    const end = await page.evaluate(() => {
      const observation = (window as ObservedWindow).__localConversionObservation;
      const video = document.querySelector('video') as HTMLVideoElement | null;
      if (!observation || !video) throw new Error('Missing video observation');
      return { at: performance.now(), clock: video.currentTime, height: video.videoHeight, events: observation.events };
    });
    const defaultAudio = options.audio_tracks?.find((track) => track.default)?.index ?? options.audio_tracks?.[0]?.index ?? null;
    const selectedAudio = typeof payload.audio_index === 'number' ? payload.audio_index : null;
    const requestedPosition = Number(new URL(request.url()).searchParams.get('start') ?? '0');
    const expectedBudget = transientBudget(scenario.downlink_mbps);
    expect(new URL(optionsNetworkResponse.url()).searchParams.get('bandwidth_bps')).toBe(expectedBudget === null ? null : String(expectedBudget));
    expect(new URL(request.url()).searchParams.get('bandwidth_bps')).toBe(expectedBudget === null ? null : String(expectedBudget));
    const expectedHeight = scenario.expected_height;
    expect(start.frame.height, `${scenario.name} initial decoded height`).toBe(expectedHeight);
    expect(end.height, `${scenario.name} decoded height after observation`).toBe(expectedHeight);
    expect(requestedPosition, 'cold trial starts at zero after clearing stored progress').toBe(0);
    expect(selectedAudio, 'fixture uses its default audio track').toBeNull();

    return {
      scenario: scenario.name,
      trial,
      fixture: REMUX_TITLE,
      network: scenario.network,
      playback_max_height: scenario.ceiling,
      browser_downlink_mbps: scenario.downlink_mbps,
      transient_bandwidth_budget_bps: expectedBudget,
      cold_context: true,
      click_to_first_decoded_frame_ms: Math.round(start.frame.at - clickAt),
      observation_wall_clock_ms: Math.round(end.at - start.frame.at),
      clock_advance_seconds: Number((end.clock - start.clock).toFixed(3)),
      stalls: terminalStalls(end.events, start.frame.at, end.at),
      decoded_height: { first_frame: start.frame.height, at_observation_end: end.height },
      requested: { position_seconds: requestedPosition, audio_index: selectedAudio, resolved_default_audio_index: defaultAudio },
      playback: { mode: options.mode, source_height: options.facts?.height ?? null, session_mode: session.mode, session_start_seconds: session.start },
    };
  } finally {
    if (session) {
      const csrf = await sessionCsrf(page).catch(() => null);
      if (csrf) await page.request.delete(`/api/playback-sessions/${session.session_id}`, { headers: { Origin: baseURL, 'X-CSRF-Token': csrf } }).catch(() => undefined);
    }
    await context.close();
  }
}

for (const scenario of SCENARIOS) {
  test(`${scenario.name}: three cold Perf Remux trials`, async ({ browser, baseURL }, testInfo) => {
    const measurements: Measurement[] = [];
    for (let trial = 1; trial <= RUNS; trial += 1) measurements.push(await measureTrial(browser, baseURL!, scenario, trial));
    const evidence = { generated_at: new Date().toISOString(), observation_window_ms: OBSERVATION_MS, measurements };
    console.log(`LOCAL-CONVERSION-NETWORK ${JSON.stringify(evidence)}`);
    await mkdir(RAW_OUTPUT, { recursive: true });
    await writeFile(path.join(RAW_OUTPUT, `local-network-${RUN_ID}-${scenario.name}.json`), `${JSON.stringify(evidence, null, 2)}\n`);
    await testInfo.attach(`${scenario.name}.json`, { contentType: 'application/json', body: Buffer.from(JSON.stringify(evidence, null, 2)) });
  });
}
