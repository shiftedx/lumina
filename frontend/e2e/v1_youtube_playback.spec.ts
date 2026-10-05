import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/**
 * A public YouTube source plays, seeks, resumes and switches quality in a real
 * browser; a restricted source is honestly unavailable with its original link and no
 * sign-in. The API is mocked; media is real VP9/Opus (headless Chromium has no H.264).
 */

const SOURCE = 'https://www.youtube.com/watch?v=lumina00001';
const TITLE = 'Public split A/V fixture with a long title that wraps across the watch header cleanly';
const media: Record<string, Buffer> = {};

test.beforeAll(() => {
  const work = mkdtempSync(join(tmpdir(), 'lumina-s27-'));
  for (const [name, size] of [['360', '640x360'], ['720', '1280x720']] as const) {
    const file = join(work, `${name}.webm`);
    execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `testsrc2=size=${size}:rate=24:duration=8`, '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
      '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-b:v', '300k', '-c:a', 'libopus', '-shortest', file]);
    media[name] = readFileSync(file);
  }
});

const rendition = (id: string, height: number) => ({
  rendition_id: id, width: height * 16 / 9, height, frame_rate: 24, bitrate_kbps: 300, video_codec: 'vp9', audio_codec: 'opus',
  container: 'webm', content_type: 'video/webm', display_label: `${height}p`,
});
const playback = (selected: 'r360' | 'r720') => ({
  status: 'ready', stream_id: 'yt-e2e', transport: 'progressive', media_kind: 'video',
  playback_url: `/api/remote-streams/yt-e2e/renditions/${selected}/content`, content_type: 'video/webm',
  has_video: true, has_audio: true, seekable: true, live: false,
  renditions: [rendition('r360', 360), rendition('r720', 720)], selected_rendition_id: selected,
});
const preview = {
  kind: 'video', title: TITLE, webpage_url: SOURCE, extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video', entries: [],
  capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } },
  playback: playback('r720'),
  raw: { id: 'lumina00001', webpage_url: SOURCE, uploader: 'Lumina fixture', duration: 8, extractor: 'youtube' },
};

/** Serve real bytes with honest HTTP range semantics, as the backend relay does. */
function serveRange(route: Route, body: Buffer) {
  const match = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range || '');
  if (!match) return route.fulfill({ status: 200, contentType: 'video/webm', body, headers: { 'Accept-Ranges': 'bytes' } });
  const start = Number(match[1]);
  const end = match[2] ? Math.min(Number(match[2]), body.length - 1) : body.length - 1;
  return route.fulfill({ status: 206, contentType: 'video/webm', body: body.subarray(start, end + 1), headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${body.length}` } });
}

/** Base mocks first: routes added later (per-test fixtures) take precedence. */
async function prepare(page: Page, scheme: 'light' | 'dark', viewport = { width: 1536, height: 960 }) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
}

async function openWatch(page: Page) {
  await page.goto(`/watch?url=${encodeURIComponent(SOURCE)}`);
  await signIn(page);
}

test('test_youtube_playback_seek_resume_quality', async ({ page }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const selections: string[] = [];
  await prepare(page, 'dark');
  await page.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(preview) }));
  await page.route('**/api/playback/remote/**', (route) => route.request().method() === 'GET'
    ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ source_identity: 'youtube:lumina00001', source_url: SOURCE, position_seconds: 3, duration_seconds: 8, completed: false, checkpoint_client_id: 'x', checkpoint_sequence: 1, checkpoint_revision: 1, cleared: false }) })
    : route.fulfill({ status: 200, contentType: 'application/json', body: route.request().postData() || '{}' }));
  await page.route('**/api/remote-streams/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    const select = /renditions\/(r\d+)\/select$/.exec(path);
    if (select) { selections.push(select[1]); return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(playback(select[1] as 'r360')) }); }
    const content = /renditions\/r(\d+)\/content$/.exec(path);
    if (content) return serveRange(route, media[content[1]]);
    return route.fulfill({ status: 404, body: '' });
  });
  await openWatch(page);

  const video = page.locator('video');
  await expect(video).toHaveCount(1);
  // Resume: the saved checkpoint (3s) is applied, and playback advances from it.
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.currentTime), { timeout: 20_000 }).toBeGreaterThanOrEqual(3);
  expect(await video.evaluate((m: HTMLVideoElement) => [Math.round(m.duration), m.videoHeight])).toEqual([8, 720]);

  // Seek to a later position through range requests.
  await video.evaluate((m: HTMLVideoElement) => { m.currentTime = 6; });
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.readyState >= 2 && m.currentTime >= 6)).toBe(true);

  // Quality switch: actual chosen quality is shown, position survives, one media element.
  await video.evaluate((m: HTMLVideoElement) => { m.pause(); m.currentTime = 5; });
  await page.locator('[data-lumina-player="true"]').hover();
  const quality = page.getByLabel('Remote playback quality');
  await expect(quality).toHaveValue('r720');
  await quality.selectOption('r360');
  expect(selections).toEqual(['r360']);
  await expect(quality).toHaveValue('r360');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.videoHeight)).toBe(360);
  expect(await video.evaluate((m: HTMLVideoElement) => Math.round(m.currentTime))).toBe(5);
  await expect(page.locator('video, audio')).toHaveCount(1);
  expect(errors).toEqual([]);
});

const restricted = { category: 'sign_in_required', message: 'This source is restricted to signed-in viewers (age, membership, subscription or private). Lumina only plays and saves public media, so open the original link to view it with the provider.' };

test('test_restricted_source_honest', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await prepare(page, 'dark', { width: 1536, height: 960 });
  await page.route('**/api/preview', (route) => route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: restricted }) }));
  await openWatch(page);
  const original = page.getByRole('link', { name: 'Open original' });
  await expect(original).toHaveAttribute('href', SOURCE, { timeout: 15_000 });
  await expect(page.locator('.player-frame')).toContainText('restricted to signed-in viewers');
  await expect(page.getByRole('button', { name: 'Save to library' })).toBeDisabled();
  await expect(page.getByText(/cookie|sign in with/i)).toHaveCount(0);
  await expect(page.getByLabel('Password')).toHaveCount(0);
  await original.focus();
  await expect(original).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
