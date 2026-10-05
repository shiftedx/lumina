import { expect, test, type Page } from '@playwright/test';

import { item, mockApi, signIn } from './lumina-mock';

/** A watch link's ?t= and a saved position both reach the first range request. */
async function directOptions(page: Page) {
  await page.route(`**/api/library/${item.id}/playback-options*`, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ mode: 'direct', reason: null, facts: { container: 'mp4', video_codec: 'h264', audio_codec: 'aac', width: 160, height: 90, duration: 90 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 2 }),
  }));
}

test('a watch link with ?t= starts there and then drops the time from the address', async ({ page }) => {
  await mockApi(page, {});
  await directOptions(page);
  await page.goto(`/watch/library/${item.id}?t=42`);
  await signIn(page);
  await expect.poll(() => page.locator('video').first().getAttribute('src')).toMatch(new RegExp(`/api/library/${item.id}/media#t=42$`));
  await expect.poll(() => new URL(page.url()).search).toBe('');
});

test('an unfinished item resumes from its saved position', async ({ page }) => {
  await mockApi(page, {});
  await directOptions(page);
  await page.route(`**/api/library/${item.id}/playback`, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ item_id: item.id, position_seconds: 30, duration_seconds: 90, completed: false }),
  }));
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect.poll(() => page.locator('video').first().getAttribute('src')).toMatch(new RegExp(`/api/library/${item.id}/media#t=30$`));
  expect(new URL(page.url()).search).toBe('');
});
