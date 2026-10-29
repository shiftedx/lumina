import { expect, test, videoTime } from './realstack';

test('a legacy source negotiates a proven modern output and WebKit decodes its real frames', async ({ page }) => {
  // The read-only endpoint reports the completed startup encoder probes.
  const report = await page.request.get('/api/admin/diagnostics');
  expect(report.ok()).toBe(true);
  const codecs = ((await report.json()).playback.encode_codecs ?? ['h264']) as string[];
  await page.goto('/library/movies');
  await page.getByRole('button', { name: /^Realstack Legacy,/ }).first().click();
  const created = page.waitForResponse((response) => response.request().method() === 'POST' && /\/playback-sessions$/.test(new URL(response.url()).pathname));
  await page.getByRole('button', { name: /^(Play|Resume(?: at .+)?|Start(?: over)?)$/ }).first().click();
  const response = await created;
  expect(response.ok(), await response.text()).toBe(true);
  const session = await response.json() as { video_codec: string };
  const profiles = new URL(response.url()).searchParams.get('profiles')?.split(',') ?? [];
  const expected = codecs.includes('av1') && profiles.includes('hls-av1-aac') ? 'av1'
    : codecs.includes('hevc') && profiles.includes('hls-hevc-aac') ? 'hevc' : 'h264';
  expect(session.video_codec).toBe(expected);
  await expect(page.getByRole('region', { name: 'Realstack Legacy player' })).toContainText('Transcoded');
  await expect.poll(() => videoTime(page), { timeout: 15_000 }).toBeGreaterThan(0.5);
});

/**
 * Real WebKit decode of the backend's actual H.264/AAC HLS output (issue #136). Bundled
 * Chromium in `playback.spec.ts` only proves the server-side session (manifest + segment
 * bytes); this asserts real frames advance, and that a far seek restarts the conversion
 * and lands near the target (#137's seek-anywhere restart). Runs only under the
 * `webkit-playback` project — see `make e2e-browsers` — and the full `make check` gate.
 */

for (const { title, via, codecs } of [
  { title: 'Realstack Remux', via: 'Remuxed', codecs: 'H.264 / AAC' },
  { title: 'Realstack Transcode', via: 'Transcoded', codecs: 'HEVC / AAC' },
]) {
  test(`${title} plays real frames in WebKit (${via})`, async ({ page }) => {
    if (via === 'Transcoded') {
      // Recent WebKit builds decode HEVC directly. Model a client where HEVC failed so this
      // case continues to exercise the server's real H.264 transcode and WebKit decode path.
      await page.addInitScript(() => localStorage.setItem('lumina.hevcFailed', '1'));
    }
    await page.goto('/library/movies');
    await page.getByRole('button', { name: new RegExp(`^${title},`) }).first().click();
    await page.getByRole('button', { name: /^(Play|Resume(?: at .+)?|Start(?: over)?)$/ }).first().click();
    await expect(page.getByRole('region', { name: `${title} player` })).toContainText(`${codecs} · ${via}`);
    await expect.poll(() => videoTime(page), { timeout: 15_000 }).toBeGreaterThan(0.5);
  });
}

test('seeking beyond the converted range restarts the transcode and lands near the target', async ({ page }) => {
  // Keep this a deterministic transcode scenario on WebKit builds that can decode HEVC.
  await page.addInitScript(() => localStorage.setItem('lumina.hevcFailed', '1'));
  await page.goto('/library/movies');
  await page.getByRole('button', { name: /^Realstack Seek,/ }).first().click();
  await page.getByRole('button', { name: /^(Play|Resume(?: at .+)?|Start(?: over)?)$/ }).first().click();
  await expect(page.getByRole('region', { name: 'Realstack Seek player' })).toContainText('HEVC / AAC · Transcoded');
  // Playing inside the first converted segment proves real WebKit decode before the far seek.
  await expect.poll(() => videoTime(page), { timeout: 15_000 }).toBeGreaterThan(0.5);

  const restart = page.waitForResponse(
    (response) => response.request().method() === 'POST' && /\/playback-sessions\?start=/.test(response.url()),
  );
  // The 90 s fixture's runtime conversion takes several real seconds, so at this point the
  // converted range is only the first few segments — well short of 70 s.
  await page.locator('video').evaluate((video: HTMLVideoElement) => { video.currentTime = 70; });
  const response = await restart;
  expect(response.ok(), await response.text()).toBe(true);

  // Without the #137 restart, hls.js would stall near the old generation's edge (well under
  // 70 s) waiting for segments that were never going to arrive. The fixed generation lands
  // within a second of the 70 s target; pause as soon as it crosses 69 s so the upper bound
  // checks a still frame instead of racing playback that keeps advancing while this polls.
  await expect.poll(() => videoTime(page), { timeout: 20_000 }).toBeGreaterThan(69);
  await page.locator('video').evaluate((video: HTMLVideoElement) => video.pause());
  expect(await videoTime(page)).toBeLessThan(75);
});
