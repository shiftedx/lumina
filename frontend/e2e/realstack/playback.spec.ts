import { expect, libraryItemId, test, videoTime } from './realstack';

const SESSION_MANIFEST = /\/api\/playback-sessions\/[\w-]+\/index\.m3u8$/;

test('direct WebM plays from the original file', async ({ page }) => {
  const id = await libraryItemId(page, 'Realstack Direct');
  await page.goto('/library/movies');
  await page.getByRole('button', { name: /^Realstack Direct,/ }).first().click();
  await page.getByRole('button', { name: /^(Play|Resume|Start)/ }).first().click();
  await expect(page.getByRole('heading', { name: 'Realstack Direct', level: 1 })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Realstack Direct player' })).toContainText('VP9 / OPUS · Original');
  await expect.poll(() => videoTime(page)).toBeGreaterThan(0.5);
  expect(await page.locator('video').evaluate((video: HTMLVideoElement) => video.currentSrc)).toContain(`/api/library/${id}/media`);
});

// Bundled Chromium has no H.264/HEVC decoder, so these assert the server-side session, not frames.
for (const { title, via, codecs } of [
  { title: 'Realstack Remux', via: 'Remuxed', codecs: 'H.264 / AAC' },
  { title: 'Realstack Transcode', via: 'Transcoded', codecs: 'HEVC / AAC' },
]) {
  test(`${title} starts an HLS playback session (${via})`, async ({ page }) => {
    await page.goto('/library/movies');
    const manifest = page.waitForResponse((response) => SESSION_MANIFEST.test(new URL(response.url()).pathname));
    await page.getByRole('button', { name: new RegExp(`^${title},`) }).first().click();
    await page.getByRole('button', { name: /^(Play|Resume|Start)/ }).first().click();
    const response = await manifest;
    expect(response.status()).toBe(200);
    const body = await response.text();
    expect(body).toContain('#EXTM3U');
    expect(body).toMatch(/#EXT-X-MAP:URI="init\.mp4"/);
    const segment = await page.request.get(new URL(body.match(/^seg\d+\.m4s$/m)![0], response.url()).href);
    expect(segment.status()).toBe(200);
    expect((await segment.body()).byteLength).toBeGreaterThan(1000);
    await expect(page.getByRole('region', { name: `${title} player` })).toContainText(`${codecs} · ${via}`);
  });
}

// No download journey: PublicSourcePolicy refuses loopback hosts (correctly); backend suites cover acquisition.
