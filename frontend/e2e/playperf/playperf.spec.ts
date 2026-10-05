/**
 * `make playperf`: click -> first frame, quality switches while playing, and resumes that land between keyframes.
 * A switch counts as resumed once the new source's clock then advances without a stall for 1.5 s.
 * It must also resume where it was asked (within 2 s), not ahead of it.
 */
import { firstFrameProbe, openWatch } from '../realstack/perfProbe';
import { expect, libraryItemId, test } from '../realstack/realstack';

const TTFF_BUDGET = Number(process.env.TTFF_BUDGET ?? 3000); // the remux p50 budget (client_metrics.BUDGETS)
const SWITCH_BUDGET = Number(process.env.SWITCH_BUDGET ?? 4000);
const TITLES = (process.env.PLAYPERF_TITLES ?? 'Perf Direct,Perf Remux,Perf Hevc,Perf Gop,Perf Encode').split(',');
type W = Window & { __perfFirstFrame?: number; __perfEmptied?: number };
// When the <video> last dropped its source: a switch's new playback starts after it.
const emptiedProbe = () => document.addEventListener('emptied', () => { (window as W).__perfEmptied = Date.now(); }, true);
const bufferState = (v: HTMLVideoElement) => JSON.stringify({ t: v.currentTime, readyState: v.readyState, paused: v.paused, seeking: v.seeking, error: v.error?.code, buffered: Array.from({ length: v.buffered.length }, (_, i) => [v.buffered.start(i), v.buffered.end(i)]) });

for (const title of TITLES) {
  test(`${title}: start and quality switches`, async ({ page }) => {
    await page.addInitScript(firstFrameProbe);
    await page.addInitScript(emptiedProbe);
    await page.goto('/library');
    const id = await libraryItemId(page, title);
    const results: Record<string, number | string> = {};
    const playAt = await openWatch(page, id);
    await expect.poll(() => page.evaluate(() => (window as W).__perfFirstFrame ?? 0), { timeout: 30_000 }).toBeGreaterThan(0);
    results.ttff = Math.round(await page.evaluate(() => (window as W).__perfFirstFrame!) - playAt);
    const video = page.locator('video');
    const time = () => video.evaluate((v: HTMLVideoElement) => v.currentTime);
    await expect.poll(time, { timeout: 30_000 }).toBeGreaterThan(3);
    console.log(`PLAYPERF ${title} ttff ${results.ttff}`);
    results.mode = (await page.locator('[data-playback-mode]').getAttribute('data-playback-mode')) ?? '?';
    for (const choice of ['720p', '480p', /^(Original|Auto)$/]) {
      await video.hover();
      await page.getByRole('button', { name: 'Playback settings' }).click();
      const radio = page.getByRole('radio', { name: choice });
      const from = await time();
      const clicked = Date.now();
      await radio.dispatchEvent('click');
      await page.keyboard.press('Escape');
      // Sampled every 100 ms from the switch's new source (the old one plays on until the answer, then rewinds to the
      // asked second): resumed at the first sample from which the clock never stalls or jumps and advances > 1 s over 1.5 s.
      let resumed = -1;
      let landed = -1;
      const samples: Array<[number, number]> = [];
      const deadline = clicked + 20_000;
      while (resumed < 0 && Date.now() < deadline) {
        const [t, emptied] = await video.evaluate((v: HTMLVideoElement) => [v.currentTime, (window as W).__perfEmptied ?? 0]);
        samples.push([Date.now(), t]);
        const fresh = samples.filter(([at]) => at > emptied && emptied >= clicked);
        const k = fresh.findIndex(([at, start], index) => {
          const window_ = fresh.slice(index).filter(([later]) => later <= at + 1500);
          return fresh.at(-1)![0] >= at + 1500 && window_.every(([, value], j) => j === 0 || (value > window_[j - 1][1] && value - window_[j - 1][1] < 0.5)) && window_.at(-1)![1] - start > 1;
        });
        if (k >= 0) { resumed = fresh[k][0] - clicked; landed = fresh[k][1]; }
        await page.waitForTimeout(100);
      }
      if (resumed < 0) console.log('PLAYPERF hung', await video.evaluate(bufferState));
      results[String(choice)] = resumed;
      console.log(`PLAYPERF ${title} switch ${String(choice)} from ${from.toFixed(1)} -> ${resumed} ms, landed at ${landed.toFixed(1)}`);
      if (resumed >= 0) expect(Math.abs(landed - from), `switch to ${String(choice)} landed at ${landed} for ${from}`).toBeLessThan(2);
      if (resumed < 0) break;
      await page.waitForTimeout(2000);
    }
    console.log(`PLAYPERF ${title} ${JSON.stringify(results)}`);
    expect(results.ttff as number, 'click -> first frame ms').toBeLessThan(TTFF_BUDGET);
    for (const [k, v] of Object.entries(results)) if (k !== 'ttff' && k !== 'mode') expect(v as number, `switch to ${k} resumed ms (-1 = hung)`).toBeGreaterThanOrEqual(0);
    for (const [k, v] of Object.entries(results)) if (k !== 'ttff' && k !== 'mode') expect(v as number, `switch to ${k} ms`).toBeLessThan(SWITCH_BUDGET);
  });
}

// Copied video restarts at the keyframe before the asked position; 10 s and 20 s sit just before the long-GOP
// fixtures' keyframes (10.4 s, 20.8 s), where a restart used to stall for 10-20 s or freeze the picture.
for (const title of TITLES) for (const at of [10, 20]) {
  test(`${title}: resume at ${at} s plays from there`, async ({ page }) => {
    await page.addInitScript(firstFrameProbe);
    await page.goto('/library');
    const id = await libraryItemId(page, title);
    const playAt = await openWatch(page, `${id}?t=${at}`);
    const video = page.locator('video');
    const time = () => video.evaluate((v: HTMLVideoElement) => v.currentTime);
    try { await expect.poll(time, { timeout: 25_000 }).toBeGreaterThan(at + 12); } catch (error) {
      console.log('PLAYPERF stalled', await video.evaluate(bufferState));
      throw error;
    }
    const ttff = Math.round(await page.evaluate(() => (window as W).__perfFirstFrame ?? 0) - playAt);
    console.log(`PLAYPERF ${title} resume@${at} ttff ${ttff}`);
    expect(await time(), 'plays from the asked position').toBeLessThan(at + 20);
    expect(ttff, 'click -> first frame ms').toBeLessThan(TTFF_BUDGET);
  });
}

// hls.js 1.6 took a growing (EVENT) playlist as live and, with timelineOffset, aimed its first fragment at twice the
// asked position whenever the playlist already listed that far (a fast encode, a slow answer): ~1 in 10 switches
// then resumed seconds ahead. Holding the answer 1.5 s lets the encode list that far every time.
test('an encode switch loads the asked position first even when its playlist is already long', async ({ page }) => {
  await page.addInitScript(firstFrameProbe);
  await page.goto('/library');
  const id = await libraryItemId(page, 'Perf Hevc');
  await openWatch(page, `${id}?t=1`); // an early switch: twice its position is soon listed
  const video = page.locator('video');
  const time = () => video.evaluate((v: HTMLVideoElement) => v.currentTime);
  await expect.poll(time, { timeout: 30_000 }).toBeGreaterThan(4);
  await page.route(/\/playback-sessions\?/, async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({ response });
  });
  let session = '';
  const segments: string[] = [];
  page.on('request', (request) => {
    const match = /playback-sessions\/([^/]+)\/(seg\d+)\.m4s/.exec(request.url());
    if (match && match[1] === session) segments.push(match[2]);
  });
  await video.hover();
  await page.getByRole('button', { name: 'Playback settings' }).click();
  const from = await time();
  const started = page.waitForResponse((response) => response.request().method() === 'POST' && /\/playback-sessions\?/.test(response.url()));
  await page.getByRole('radio', { name: '480p' }).dispatchEvent('click');
  await page.keyboard.press('Escape');
  session = ((await (await started).json()) as { session_id: string }).session_id;
  let landed = 0;
  await expect.poll(async () => { const a = await time(); await page.waitForTimeout(300); landed = await time(); return a > 0 && landed > a; }, { timeout: 20_000 }).toBe(true);
  console.log(`PLAYPERF long-playlist switch from ${from.toFixed(1)} landed at ${landed.toFixed(1)}, segments ${segments.join(' ')}`);
  expect(segments[0], 'the first fragment loaded after the switch').toBe('seg0');
  expect(Math.abs(landed - from), 'position after the switch').toBeLessThan(2.5);
});
