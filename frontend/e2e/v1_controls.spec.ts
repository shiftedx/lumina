import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';
import { redOrPinkInMain } from './red-check';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** Player controls: keyboard-only flow, slider semantics, focus rings, live edge. */

const LIVE_URL = 'https://www.twitch.tv/luminafixture';

async function openLibraryWatch(page: Page, scheme: 'light' | 'dark', viewport = { width: 1536, height: 960 }) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  const saved = await mockApi(page, { sidebar_collapsed: false, player_volume: 0.4 });
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, async (route) => {
    const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
    const from = Number(start) || 0;
    const to = end ? Number(end) : media.length - 1;
    await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
  });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  const video = page.locator('video');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState), { timeout: 15_000 }).toBeGreaterThan(0);
  await video.evaluate((element: HTMLVideoElement) => element.pause());
  return { video, saved };
}

async function tabTo(page: Page, name: string) {
  for (let step = 0; step < 60; step += 1) {
    await page.keyboard.press('Tab');
    if (await page.evaluate((label) => document.activeElement?.getAttribute('aria-label') === label, name)) return;
  }
  throw new Error(`Tab never reached ${name}`);
}

test('test_keyboard_only_player_flow', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const { video, saved } = await openLibraryWatch(page, 'dark');
  // The member's saved volume is applied from server ui_prefs.
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.volume)).toBe(0.4);

  await tabTo(page, 'Seek');
  const seek = page.getByRole('slider', { name: 'Seek' });
  await expect(seek).toHaveAttribute('aria-valuetext', /^0:0\d of 0:01$/);
  await page.keyboard.press('End');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.currentTime)).toBeGreaterThan(1.4);
  await page.keyboard.press('Home');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.currentTime)).toBe(0);

  // Focus ring on the seek slider is a real, visible outline.
  const ring = await seek.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(ring).toBe('solid');

  // Shortcuts from inside the player region.
  await page.keyboard.press('m');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.muted)).toBe(true);
  await page.keyboard.press('m');
  await tabTo(page, 'Playback speed');
  // A native select opens its popup on arrows in Chromium/macOS; choose the option directly.
  await page.getByLabel('Playback speed').selectOption('1.25');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.playbackRate)).toBe(1.25);

  // Typing in search never drives the player.
  await page.getByRole('combobox').first().focus();
  await page.keyboard.type('k j l');
  expect(await video.evaluate((m: HTMLVideoElement) => m.paused)).toBe(true);

  // With nothing focused, K plays and K pauses.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.keyboard.press('k');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.paused || m.ended)).toBe(false);
  await page.keyboard.press('k');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.paused)).toBe(true);

  // Volume from the keyboard persists to server ui_prefs.
  await page.locator('[data-lumina-player="true"]').focus();
  await page.keyboard.press('ArrowUp');
  await expect.poll(() => saved.some((body) => (body.ui_prefs as { player_volume?: number } | undefined)?.player_volume === 0.45)).toBe(true);
  expect(errors).toEqual([]);
});

// Phone width: the control row must keep 44px targets.
test('controls focused and hover 390', async ({ page }) => {
  await openLibraryWatch(page, 'dark', { width: 390, height: 844 });
  await expect(page.locator('[data-player-state="ready"]')).toHaveCount(1);
  await tabTo(page, 'Seek');
  await expect(page.locator('[data-controls-visible="true"]')).toHaveCount(1);
  await page.getByRole('button', { name: 'Mute' }).hover();
  for (const control of await page.locator('.player-controls button, .player-controls select').all()) {
    const box = await control.boundingBox();
    expect(box && box.height).toBeGreaterThanOrEqual(44);
  }
});

let hlsDir = '';
test.describe('live', () => {
  test.beforeAll(() => {
    hlsDir = mkdtempSync(join(tmpdir(), 'lumina-s49-'));
    execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
      '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '300k', '-g', '48', '-c:a', 'libopus', '-shortest', '-f', 'hls',
      '-hls_segment_type', 'fmp4', '-hls_time', '2', '-hls_fmp4_init_filename', 'init.mp4',
      '-hls_segment_filename', join(hlsDir, 'seg%d.m4s'), join(hlsDir, 'index.m3u8')]);
  });

  test('live controls', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1536, height: 960 });
    await mockApi(page, { sidebar_collapsed: false });
    const descriptor = {
      stream_id: 'live-s49', status: 'ready', transport: 'hls', playback_url: '/api/remote-streams/live-s49/master.m3u8', media_kind: 'video',
      content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true, seekable: false, live: true, renditions: [],
    };
    await page.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'luminafixture is live', webpage_url: LIVE_URL, extractor: 'twitch', extractor_key: 'TwitchStream', media_kind: 'video', entries: [],
      capabilities: { provider: 'twitch', lifecycle: 'live', can_play: true, can_acquire: false, acquire_reason: 'live_acquisition_not_supported', can_record: false, from_start_available: false, can_schedule: false, chat: { live: 'unavailable', replay: 'unavailable' } },
      playback: descriptor, raw: { id: 'live-s49', webpage_url: LIVE_URL, uploader: 'luminafixture', extractor: 'twitch', is_live: true },
    }) }));
    await page.route('**/api/remote-streams/live-s49/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
      if (path.endsWith('/refresh')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(descriptor) });
      if (path.endsWith('/master.m3u8')) return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360,CODECS="vp09.00.21.08,opus"\n/api/remote-streams/live-s49/relay/1/r/media\n' });
      const name = path.split('/r/')[1];
      if (name === 'media') {
        // No ENDLIST: hls.js treats this as a live window.
        const playlist = readFileSync(join(hlsDir, 'index.m3u8'), 'utf8').replace(/#EXT-X-ENDLIST\n?/, '').replace(/init\.mp4|seg\d+\.m4s/g, (file) => `/api/remote-streams/live-s49/relay/1/r/${file}`);
        return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: playlist });
      }
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: readFileSync(join(hlsDir, name)) });
    });
    await page.goto(`/watch?url=${encodeURIComponent(LIVE_URL)}`);
    await signIn(page);
    await expect(page.locator('[data-player-state="ready"]')).toHaveCount(1, { timeout: 20_000 });
    await expect(page.getByLabel('Seek')).toHaveCount(0);
    await expect(page.getByLabel('Playback speed')).toHaveCount(0);
    await page.locator('video').evaluate((m: HTMLVideoElement) => m.pause());
    const goLive = page.getByRole('button', { name: 'Go live' });
    await expect(goLive).toBeVisible();
    await goLive.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('status', { name: 'Watching live' })).toBeVisible();
    expect(await redOrPinkInMain(page)).toEqual([]);
    // One shared badge, still under reduced motion, and no second coloured time code.
    await expect(page.locator('.player-live-line .g-live.is-live.on-bar')).toHaveCount(1);
    await expect(page.locator('.player-timecode')).toHaveCount(0);
    expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
  });
});
