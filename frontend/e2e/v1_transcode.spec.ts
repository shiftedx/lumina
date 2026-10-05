import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { HOME_PATH, mockHome } from './home-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/**
 * An MPEG-4 Part 2 / AC-3 AVI (not browser-native) plays in a real browser
 * through the backend's own ffmpeg HLS command. The API is mocked; the segments are real.
 */

const backend = resolve('../backend');
const user = { id: 'member-1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true, bio: '', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', onboarding_status: 'completed' };
const item = {
  id: 'library-1', user_id: user.id, visibility: 'private', remote_id: 'synthetic-1', source_url: 'https://example.test/watch/1', webpage_url: 'https://example.test/watch/1',
  title: 'A converted synthetic fixture whose original codecs no browser plays natively', uploader: 'Lumina fixture', duration: 8, extractor: 'youtube', status: 'available',
  file_path: null, file_size: 1, metadata_json: { description: 'fixture' }, chapters: [],
  downloaded_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};
const facts = { container: 'avi', video_codec: 'mpeg4', audio_codec: 'ac3', width: 320, height: 180, duration: 8, audio_tracks: 1, subtitles: [] };
let hlsDir = '';

test.beforeAll(() => {
  const work = mkdtempSync(join(tmpdir(), 'lumina-s24-'));
  const source = join(work, 'source.avi');
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:v', 'mpeg4', '-c:a', 'ac3', '-shortest', source]);
  hlsDir = join(work, 'hls');
  execFileSync(join(backend, '.venv/bin/python'), ['-c', [
    'import subprocess, sys; from pathlib import Path',
    'from app.services.local_playback_sessions import ffmpeg_command',
    'from app.services.playback_decision import Decision',
    'out = Path(sys.argv[2]); out.mkdir()',
    "decision = Decision('transcode', video='encode', audio='encode', video_index=0, audio_index=1, height=180, audio_channels=2)",
    "subprocess.run(ffmpeg_command('ffmpeg', Path(sys.argv[1]), {}, out, decision), check=True)",
  ].join('\n'), source, hlsDir], { cwd: backend });
});

async function mockApi(page: Page, requests: string[], mode: 'transcode' | 'direct' = 'transcode') {
  // Registered first so the catch-all below (added after) wins for the paths it knows about, and
  // falls back here for the new Home's own shelves (home-mock.ts) rather than duplicating them.
  await mockHome(page);
  await page.addInitScript(() => {
    class QuietEventSource {
      onopen: ((event: Event) => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(new Event('open')), 0); }
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: QuietEventSource });
  });
  let loggedIn = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requests.push(`${request.method()} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') return loggedIn ? json({ user }) : json({ detail: 'not authenticated' }, 401);
    if (path === '/api/session/login') { loggedIn = true; return json({ user }); }
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', desktop_dir: null, data_dir: '/tmp/lumina-test' });
    if (path === '/api/library') return json({ items: [item], next_cursor: null });
    if (path === `/api/library/${item.id}/playback-options`) {
      return json(mode === 'direct'
        ? { mode, reason: null, facts: { ...facts, container: 'matroska,webm', video_codec: 'vp9', audio_codec: 'opus', height: 90 } }
        : { mode, reason: 'VideoCodecNotSupported,AudioCodecNotSupported', facts });
    }
    if (path === `/api/library/${item.id}/media`) return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') });
    if (path === `/api/library/${item.id}/playback-sessions`) return json({ session_id: 'sess-1', mode: 'transcode', playback_url: '/api/playback-sessions/sess-1/index.m3u8' }, 201);
    if (path.startsWith('/api/playback-sessions/sess-1/')) {
      const name = path.split('/').pop() as string;
      return route.fulfill({ status: 200, contentType: name.endsWith('.m3u8') ? 'application/vnd.apple.mpegurl' : 'video/mp4', body: readFileSync(join(hlsDir, name)) });
    }
    if (path === '/api/playback-sessions/sess-1') return route.fulfill({ status: 204 });
    if (path === `/api/library/${item.id}/playback`) {
      // PUT answers with the saved progress like the real API; a null here crashed the app's
      // continue-watching reducer on the seek checkpoint and raced the Back click (flake).
      if (request.method() !== 'PUT') return json(null);
      const at = '2026-01-01T00:00:00Z';
      return json({ id: 'progress-1', user_id: user.id, item_id: item.id, duration_seconds: null, completed: false, ...request.postDataJSON(), last_watched_at: at, created_at: at, updated_at: at, item });
    }
    if (path === `/api/library/${item.id}`) return json(item);
    if (path === `/api/library/${item.id}/comments` || path === `/api/library/${item.id}/tags`) return json([]);
    const empty = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/popular') return json(empty);
    if (path === '/api/discovery/live') return json({ ...empty, twitch_available: true, hero: [] });
    if (path === '/api/automations' || path === '/api/admin/users' || path === '/api/playback/continue') return json([]);
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    // Without personal settings the sign-in toast stays up and covers the mini-player's Close button.
    if (path === '/api/settings/me') {
      const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
      const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
      const defaults = { download_defaults: { format_selection: formatSelection, output_profile: outputProfile } };
      return json({ id: 's', user_id: user.id, ...defaults, automation_defaults: {}, ui_prefs: {}, notification_prefs: {}, remote_playback_cache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }, resolved_download_defaults: defaults.download_defaults, resolved_automation_defaults: {}, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' });
    }
    if (path === '/api/admin/settings') return json({ library_root: '/tmp/l', temp_root: '/tmp/t', archive_path: '/tmp/a.txt', concurrency: 2, max_playback_sessions: 2, yt_dlp_defaults: {}, ui_prefs: {}, webhook_url: null, webhook_enabled: false, webhook_notify_new_videos: false, webhook_notify_failures: false });
    if (path.startsWith('/api/discovery/')) return json({ items: [], categories: [], selected_keys: [], channels: [] });
    if (HOME_PATH(path)) return route.fallback();
    return json({ detail: `mock: unhandled ${request.method()} ${path}` }, 404);
  });
}

async function openWatch(page: Page, label = '180p · MPEG4 / AC3 · Transcoded') {
  await page.goto('/');
  const card = page.locator('.g-auth');
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
  // The new Home's Recently saved card names itself "{title}, {uploader}, {duration}" (StillCard),
  // not "Play {title}".
  // A click that lands while Home is still settling can be swallowed, so retry it until the watch page is open.
  await expect(async () => {
    if (!page.url().includes('/watch/')) await page.getByRole('button', { name: new RegExp(`^${item.title}, `) }).first().click({ timeout: 3000 });
    await expect(page.getByText(label).first()).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 20_000 });
}

type Engine = 'chromium' | 'firefox' | 'webkit';

// Headless Playwright Chromium is an open-codec build (no H.264), so AV playback runs in Firefox and WebKit.
async function withPage(playwright: typeof import('@playwright/test'), engine: Engine, baseURL: string, run: (page: Page) => Promise<void>) {
  const browser = await playwright[engine].launch().catch((error: Error) => {
    test.skip(/Executable doesn't exist|Library not loaded/.test(error.message), `BLOCKED: Playwright ${engine} build is not installed`);
    throw error;
  });
  try {
    await run(await browser.newPage({ baseURL }));
  } finally {
    await browser.close();
  }
}

for (const engine of ['firefox', 'webkit'] as const) {
  test(`test_transcode_real_browser_av ${engine}`, async ({ playwright, baseURL }) => {
    test.slow(); // launches a second real browser and runs a real transcode; the stacked 15 s waits below exceed the 30 s default on a loaded host
    await withPage(playwright as never, engine, baseURL as string, async (page) => {
      const requests: string[] = [];
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await mockApi(page, requests);
      await page.setViewportSize({ width: 1536, height: 960 });
      await openWatch(page);
      const video = page.locator('.lumina-player video');
      // play() may stay pending until the transcode has data (never awaited, or a loaded host hangs the evaluate); re-issue it while polling
      await expect.poll(() => video.evaluate((media: HTMLVideoElement) => { media.muted = true; void media.play().catch(() => undefined); return media.currentTime; }), { timeout: 15000 }).toBeGreaterThan(1);
      expect(await video.evaluate((media: HTMLVideoElement) => [media.videoWidth, media.videoHeight])).toEqual([320, 180]);
      await video.evaluate((media: HTMLVideoElement) => { media.currentTime = 6; });
      await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.currentTime), { timeout: 15000 }).toBeGreaterThanOrEqual(6);
      expect(requests).toContain(`POST /api/library/${item.id}/playback-sessions`);
      // Back docks the same player as the mini-player, so the session keeps running; closing it
      // unmounts LocalLibraryPlayer and runs its cleanup effect. A hard page.goto (tab close)
      // discards the JS realm before any unmount can run, in every engine.
      await page.getByRole('button', { name: 'Back', exact: true }).click();
      await page.getByRole('button', { name: 'Close player' }).click();
      await expect.poll(() => requests.includes('DELETE /api/playback-sessions/sess-1')).toBe(true);
      expect(errors).toEqual([]);
    });
  });
}

// Open-codec Chromium: a probed direct file shows its facts; an H.264 derivative
// Chromium cannot decode shows the honest failure state rather than an endless spinner.
for (const state of ['direct', 'error'] as const) {
  test(`s24 watch ${state}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1536, height: 960 });
    await mockApi(page, [], state === 'direct' ? 'direct' : 'transcode');
    await openWatch(page, state === 'direct' ? '90p · VP9 / OPUS · Original' : undefined);
    if (state === 'error') await expect(page.getByText('Playback of the converted file failed.').first()).toBeVisible({ timeout: 15000 });
    else await expect.poll(() => page.locator('.lumina-player video').evaluate((media: HTMLVideoElement) => media.readyState), { timeout: 15000 }).toBeGreaterThan(1);
    await page.waitForTimeout(400);
    expect(errors).toEqual([]);
  });
}
