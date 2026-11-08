import { expect, test, type Page, type Route } from '@playwright/test';

import { item, mockApi, navigateApp, signIn } from './lumina-mock';

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

test('leaving a pending watch link prevents its late lookup from starting playback', async ({ page }) => {
  await mockApi(page, {});
  await directOptions(page);
  let pending: Route | undefined;
  await page.route(`**/api/library/${item.id}`, (route) => { pending = route; });
  await page.goto(`/watch/library/${item.id}?t=42`);
  await signIn(page);
  await expect.poll(() => Boolean(pending)).toBe(true);

  await navigateApp(page, 'Home');
  await expect(page).toHaveURL(/\/$/);
  await pending!.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(item) });
  // Let the released response and any resulting player requests finish before checking the cancelled intent.
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('video')).toHaveCount(0);
});


test('settings edits clear the continuing video mini player on phone and desktop', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {}); await directOptions(page);
  await page.goto(`/watch/library/${item.id}`); await signIn(page);
  const video = page.locator('video').first();
  await expect(video).toHaveAttribute('src', new RegExp(`/api/library/${item.id}/media`));
  const original = await video.getAttribute('src');
  await navigateApp(page, 'My Settings');
  const mini = page.locator('.watch-mini');
  await expect(mini).toBeVisible();
  await expect(video).toHaveAttribute('src', original!);
  const name = page.getByRole('textbox', { name: 'Display name', exact: true });
  await name.fill('Video keeps playing');
  const save = page.getByRole('button', { name: 'Save profile', exact: true });
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await save.focus();
    await expect.poll(async () => {
      const [saveBox, playerBox] = await Promise.all([save.boundingBox(), mini.boundingBox()]);
      return Boolean(saveBox && playerBox && saveBox.y + saveBox.height <= playerBox.y + 1);
    }).toBe(true);
    await expect(video).toHaveAttribute('src', original!);
  }
  await page.getByRole('button', { name: 'Discard', exact: true }).click();
});
