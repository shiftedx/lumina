/**
 * Spec 9.6 measurement helpers for the `perf` project (and the lead's reference-host runs): Chromium CDP CPU and
 * network throttling, a first-frame probe that runs inside the page, and a Play that goes through the app's
 * own route handling.
 */
import type { Browser, Page } from '@playwright/test';

import { expect, OWNER_STATE } from './realstack';

export type Profile = 'lan' | 'slow';
export const RUNS = 3;
export const NETWORK: Record<Profile, { latency: number; downloadThroughput: number; uploadThroughput: number }> = {
  lan: { latency: 2, downloadThroughput: -1, uploadThroughput: -1 },
  slow: { latency: 40, downloadThroughput: 20_000_000 / 8, uploadThroughput: 5_000_000 / 8 },
};

type PerfWindow = Window & { __perfFirstFrame?: number; __perfPlayAt?: number };

/** Installed before any page script: the page-clock time the first video frame is presented. */
export function firstFrameProbe() {
  document.addEventListener('loadeddata', (event) => {
    const video = event.target;
    if (video instanceof HTMLVideoElement) video.requestVideoFrameCallback((now) => { (window as PerfWindow).__perfFirstFrame ??= now; });
  }, true);
}

/** A new context (cold browser cache) at `cpu`× CPU and the given network. */
export async function throttledPage(browser: Browser, baseURL: string, profile: Profile, { cpu = 4, storageState = OWNER_STATE }: { cpu?: number; storageState?: string } = {}): Promise<Page> {
  const context = await browser.newContext({ baseURL, storageState });
  await context.addInitScript(firstFrameProbe);
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpu });
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, ...NETWORK[profile] });
  return page;
}

/**
 * Loads the app, then opens /watch/library/{id} from a real click (a user gesture, so autoplay is allowed) that
 * goes through the app's popstate handler and openRoute, as any Play does. Returns the page-clock time of the click.
 */
export async function openWatch(page: Page, libraryId: string, settleMs = 0): Promise<number> {
  await page.goto('/library');
  await expect(page.getByRole('main', { name: 'Main content' })).toBeVisible();
  if (settleMs) await page.waitForTimeout(settleMs); // a person looks at the page first; idle preloads (1.5 s) have run
  await page.evaluate((id) => {
    const button = document.createElement('button');
    button.id = 'perf-play';
    button.textContent = 'Play (perf)';
    button.style.cssText = 'position:fixed;right:0;bottom:0;z-index:2147483647';
    button.addEventListener('click', () => {
      delete (window as PerfWindow).__perfFirstFrame; // only a frame after this Play counts
      (window as PerfWindow).__perfPlayAt = performance.now();
      window.history.pushState(null, '', `/watch/library/${id}`);
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    document.body.append(button);
  }, libraryId);
  await page.click('#perf-play');
  return page.evaluate(() => (window as PerfWindow).__perfPlayAt!);
}

export async function firstFrameAt(page: Page): Promise<number> {
  await page.waitForFunction(() => (window as PerfWindow).__perfFirstFrame !== undefined, undefined, { timeout: 30_000 });
  return page.evaluate(() => (window as PerfWindow).__perfFirstFrame!);
}

/** Runs `run` RUNS times; returns the median and a line with every sample (printed, and used as the failure message). */
export async function medianOf(name: string, run: () => Promise<number>): Promise<{ median: number; samples: string }> {
  const values: number[] = [];
  try {
    for (let index = 0; index < RUNS; index += 1) values.push(Math.round(await run()));
  } finally {
    console.log(`${name}: ${values.join(', ')}${values.length < RUNS ? ' (a run failed)' : ''}`); // the samples so far, even on a throw
  }
  const samples = `${name}: ${values.join(', ')}`;
  return { median: [...values].sort((a, b) => a - b)[Math.floor(RUNS / 2)], samples };
}

/** Stops a conversion the closed context can no longer stop, so the next cold run starts its own. */
export async function stopSession(page: Page, sessionId: string): Promise<void> {
  const origin = new URL(page.url()).origin;
  const { csrf_token: csrf } = (await (await page.request.get('/api/session/me')).json()) as { csrf_token: string };
  const response = await page.request.delete(`/api/playback-sessions/${sessionId}`, { headers: { Origin: origin, 'X-CSRF-Token': csrf } });
  expect(response.status()).toBe(204);
}

/** Closes a run's context from a `finally`: logs a failure instead of throwing over the run's own error. */
export async function closeQuietly(page: Page): Promise<void> {
  await page.context().close().catch((error: unknown) => console.log(`could not close the context: ${String(error)}`));
}

type Sample = { metric: string; label: string; value: number };
type MetricsWindow = Window & { __perfSamples?: Sample[]; __perfPending?: number };
export const PERF_MEMBER = { username: 'perfadmin', password: 'perf-household-password' };

/**
 * Installed before any page script: a copy of every sample the app sends (its own perfMetrics values, unbucketed),
 * from its pagehide beacons and its 30 s posts. Beacons are kept, not sent: appSamples forces one per poll, and
 * posting each would only hit the server's rate limit (429s). `__perfPending` counts beacon bodies still being read.
 */
export function captureMetrics() {
  const w = window as MetricsWindow;
  w.__perfSamples = [];
  w.__perfPending = 0;
  const isMetrics = (url: unknown) => String(url).endsWith('/api/metrics/client');
  const send = navigator.sendBeacon.bind(navigator);
  navigator.sendBeacon = (url, data) => {
    if (!isMetrics(url) || !(data instanceof Blob)) return send(url, data);
    w.__perfPending! += 1;
    void data.text()
      .then((text) => { w.__perfSamples!.push(...(JSON.parse(text) as { samples: Sample[] }).samples); })
      .finally(() => { w.__perfPending! -= 1; });
    return true;
  };
  const post = window.fetch.bind(window);
  window.fetch = (input, init) => {
    if (isMetrics(input) && typeof init?.body === 'string') w.__perfSamples!.push(...(JSON.parse(init.body) as { samples: Sample[] }).samples);
    return post(input, init);
  };
}

/** A signed-in, throttled context on the perf titles server. */
export async function perfTitlesPage(browser: Browser, profile: Profile, { cpu = 4 }: { cpu?: number } = {}): Promise<Page> {
  const baseURL = process.env.PERF_TITLES_URL!;
  const context = await browser.newContext({ baseURL });
  await context.addInitScript(captureMetrics);
  const login = await context.request.post('/api/session/login', { data: PERF_MEMBER, headers: { Origin: baseURL } });
  expect(login.ok(), await login.text()).toBe(true);
  const { csrf_token: csrf } = (await login.json()) as { csrf_token: string };
  await context.request.post('/api/onboarding/skip', { headers: { Origin: baseURL, 'X-CSRF-Token': csrf } });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpu });
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, ...NETWORK[profile] });
  return page;
}

/** Everything the app has recorded so far (its pagehide flush, triggered here, once every beacon body is read). */
export async function appSamples(page: Page): Promise<Sample[]> {
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await page.waitForFunction(() => (window as MetricsWindow).__perfPending === 0);
  return page.evaluate(() => (window as MetricsWindow).__perfSamples ?? []);
}

/** Flushes and drops every sample so far, so the next appMetric can only return a later one (appMetric reads the newest). */
export async function resetAppSamples(page: Page): Promise<void> {
  await appSamples(page);
  await page.evaluate(() => { (window as MetricsWindow).__perfSamples = []; });
}

/** The app's latest value of one metric and label, waiting up to 30 s for it. */
export async function appMetric(page: Page, metric: string, label: string): Promise<number> {
  let value: number | undefined;
  await expect.poll(async () => {
    value = (await appSamples(page)).filter((sample) => sample.metric === metric && sample.label === label).at(-1)?.value;
    return value;
  }, { timeout: 30_000 }).toBeDefined();
  return value!;
}

/** Wheel down the page until it stops moving, letting each screen's images load. */
export async function scrollToEnd(page: Page): Promise<void> {
  await page.mouse.move(640, 400);
  let still = 0;
  let last = -1;
  while (still < 3) {
    await page.mouse.wheel(0, 700);
    await page.waitForTimeout(150);
    const y = await page.evaluate(() => window.scrollY);
    still = y === last ? still + 1 : 0;
    last = y;
  }
  await page.waitForTimeout(2_000); // the last screen's retries settle before failures are counted
}
