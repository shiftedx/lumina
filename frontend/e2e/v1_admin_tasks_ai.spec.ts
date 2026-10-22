import { expect, test } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** AI failure must not prevent real browser media initialization. */

test('test_ai_failure_no_playback_impact', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true });
  await page.route('**/api/enrichment', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' }));
  await page.route(`**/api/library/${item.id}/transcripts`, (route) => route.fulfill({ status: 500, body: 'boom' }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  const video = page.locator('video').first();
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThan(0);
  await page.getByRole('tab', { name: 'Summary' }).click();
  await expect(page.getByText('No transcript for this video.')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Transcript' })).toHaveCount(0);
});
