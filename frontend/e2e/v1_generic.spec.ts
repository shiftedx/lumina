import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** A pasted generic link plays when extraction proves media (metadata-only honesty: src/v1_generic.test.tsx). */

const PAGE = 'https://videos.example.org/watch/42';
const chat = { live: 'unavailable', replay: 'unavailable' };
const base = { kind: 'video', webpage_url: PAGE, extractor: 'generic', extractor_key: 'Generic', media_kind: 'video', entries: [], raw: { id: '42', webpage_url: PAGE, extractor: 'generic', duration: 2 } };
const playable = {
  ...base, title: 'A public clip from an independent video site',
  capabilities: { provider: 'generic', lifecycle: 'vod', can_play: true, can_acquire: true, chat },
  playback: { status: 'ready', stream_id: 'web-e2e', transport: 'progressive', media_kind: 'video', playback_url: '/api/remote-streams/web-e2e/content', content_type: SYNTHETIC_MEDIA_TYPE, has_video: true, has_audio: true, seekable: true, renditions: [] },
};

async function open(page: Page, body: unknown) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/preview', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }));
  await page.route('**/api/remote-streams/**', (route) => route.request().method() === 'DELETE'
    ? route.fulfill({ status: 204, body: '' })
    : route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') }));
  await page.goto(`/watch?url=${encodeURIComponent(PAGE)}`);
  await signIn(page);
}

test('test_generic_public_fixture_plays_and_saves', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, playable);
  const video = page.locator('video');
  await expect.poll(() => video.evaluate((m: HTMLVideoElement) => m.currentTime), { timeout: 20_000 }).toBeGreaterThan(0.2);
  await expect(page.getByRole('button', { name: 'Save to library' })).toBeEnabled();
  await expect(page.locator('.g-watch-byline')).toContainText('videos.example.org');
  await expect(page.getByRole('link', { name: 'Open original' })).toHaveCount(0);
  expect(errors).toEqual([]);
});
