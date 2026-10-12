import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/**
 * A public Twitch VOD plays and seeks through the relay's HLS shape in a real
 * browser (hls.js + real fMP4 VP9/Opus segments), and a live Twitch stream shows Live,
 * then an honest "ended" state after one refresh. The API is mocked.
 */

const VOD_URL = 'https://www.twitch.tv/videos/2000000001';
const LIVE_URL = 'https://www.twitch.tv/luminafixture';
let hlsDir = '';

test.beforeAll(() => {
  hlsDir = mkdtempSync(join(tmpdir(), 'lumina-s29-'));
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
    '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '300k', '-g', '48', '-c:a', 'libopus', '-shortest', '-f', 'hls',
    '-hls_segment_type', 'fmp4', '-hls_time', '2', '-hls_playlist_type', 'vod', '-hls_fmp4_init_filename', 'init.mp4',
    '-hls_segment_filename', join(hlsDir, 'seg%d.m4s'), join(hlsDir, 'index.m3u8')]);
});

const caps = (lifecycle: string, live: boolean) => ({
  provider: 'twitch', lifecycle, can_play: true, can_acquire: !live, acquire_reason: live ? 'live_acquisition_not_supported' : null,
  can_record: live, from_start_available: false, can_schedule: false, chat: { live: 'unavailable', replay: 'unavailable' },
});
const descriptor = (streamId: string, live: boolean) => ({
  status: 'ready', stream_id: streamId, transport: 'hls', media_kind: 'video', playback_url: `/api/remote-streams/${streamId}/relay/1/master.m3u8`,
  content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true, seekable: !live, live, renditions: [],
});
const previewFor = (url: string, title: string, lifecycle: string, live: boolean, streamId: string) => ({
  kind: 'video', title, webpage_url: url, extractor: 'twitch', extractor_key: live ? 'TwitchStream' : 'TwitchVod', media_kind: 'video', entries: [],
  capabilities: caps(lifecycle, live), playback: descriptor(streamId, live),
  raw: { id: streamId, webpage_url: url, uploader: 'luminafixture', extractor: 'twitch', ...(live ? { is_live: true } : { was_live: true, duration: 8 }) },
});

async function open(page: Page, url: string, scheme: 'light' | 'dark', viewport: { width: number; height: number }, routes: (page: Page) => Promise<void>) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await routes(page);
  await page.goto(`/watch?url=${encodeURIComponent(url)}`);
  await signIn(page);
}

test('test_twitch_vod_seek_fixture', async ({ page }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, VOD_URL, 'dark', { width: 1536, height: 960 }, async (p) => {
    await p.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(previewFor(VOD_URL, 'A finished Twitch broadcast', 'completed_live', false, 'twitch-vod')) }));
    await p.route('**/api/playback/remote/**', (route) => route.request().method() === 'GET'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ position_seconds: 3, duration_seconds: 8, completed: false, checkpoint_client_id: 'x', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false }) })
      : route.fulfill({ status: 200, contentType: 'application/json', body: route.request().postData() || '{}' }));
    await p.route('**/api/remote-streams/twitch-vod/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
      if (path.endsWith('/master.m3u8')) {
        return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=640x360,CODECS="vp09.00.21.08,opus"\n/api/remote-streams/twitch-vod/relay/1/r/media\n' });
      }
      const name = path.split('/r/')[1];
      if (name === 'media') {
        // The relay's rewritten shape: every upstream address becomes an opaque /r/ id.
        const playlist = readFileSync(join(hlsDir, 'index.m3u8'), 'utf8').replace(/init\.mp4|seg\d+\.m4s/g, (file) => `/api/remote-streams/twitch-vod/relay/1/r/${file}`);
        return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: playlist });
      }
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: readFileSync(join(hlsDir, name)) });
    });
  });
  await expect(page.locator('.g-watch-kicker .g-live')).toHaveText('ENDED');
  const video = page.locator('video');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.currentTime), { timeout: 20_000 }).toBeGreaterThanOrEqual(3);
  expect(await video.evaluate((m: HTMLVideoElement) => Math.round(m.duration))).toBe(8);
  await video.evaluate((m: HTMLVideoElement) => { m.currentTime = 6.5; });
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.readyState >= 2 && m.currentTime >= 6.5)).toBe(true);
  await page.locator('[data-lumina-player="true"]').hover();
  await expect(page.getByLabel('Seek')).toBeVisible();
  expect(errors).toEqual([]);
});

test('test_twitch_live_edge_then_ended', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const refreshes: string[] = [];
  await open(page, LIVE_URL, 'dark', { width: 1536, height: 960 }, async (p) => {
    await p.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(previewFor(LIVE_URL, 'luminafixture is live with a long stream title that wraps', 'live', true, 'twitch-live')) }));
    await p.route('**/api/remote-streams/twitch-live/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
      if (path.endsWith('/refresh')) {
        refreshes.push(path);
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...descriptor('twitch-live', true), status: 'unsupported', transport: null, playback_url: null, has_video: false, has_audio: false, fallback_code: 'live_stream_ended', fallback_message: 'This live stream has ended.' }) });
      }
      if (path.endsWith('/master.m3u8')) return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n/api/remote-streams/twitch-live/relay/1/r/media\n' });
      // The broadcast is gone: the edge playlist no longer resolves upstream.
      return route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"The relay could not fetch an upstream HLS resource."}' });
    });
  });
  await expect(page.locator('.g-watch-kicker .g-live')).toHaveText('LIVE');
  await expect(page.locator('.player-frame')).toContainText('This live stream has ended.', { timeout: 20_000 });
  expect(refreshes).toHaveLength(1);
  // The page stops claiming Live and offers nothing that can no longer work.
  await expect(page.locator('.g-watch-kicker .g-live')).toHaveText('ENDED');
  await expect(page.getByRole('button', { name: 'Refresh stream' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Record from now' })).toHaveCount(0);
  await expect(page.getByLabel('Seek')).toHaveCount(0);
  await expect(page.getByText(/cookie|sign in with|log in/i)).toHaveCount(0);
  await page.waitForTimeout(1_000);
  expect(refreshes).toHaveLength(1);
  expect(errors).toEqual([]);
});
