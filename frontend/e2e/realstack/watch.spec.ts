import type { Page } from '@playwright/test';
import { expect, libraryItemId, test, videoTime } from './realstack';

test('a timestamped note persists and seeks the player', async ({ page }) => {
  await page.goto('/library/movies');
  await page.getByRole('button', { name: /^Realstack Direct,/ }).first().click();
  await page.getByRole('button', { name: /^(Play|Resume|Start)/ }).first().click();
  await expect.poll(() => videoTime(page)).toBeGreaterThan(1.2);
  await page.getByRole('button', { name: 'Pause' }).click();
  const stamp = await videoTime(page);

  await page.getByRole('tab', { name: 'Notes' }).click();
  await page.getByRole('button', { name: 'Add current time' }).click();
  await page.getByRole('textbox', { name: 'New note' }).fill('Lantern appears on the left');
  await page.getByRole('button', { name: 'Save note' }).click();
  const seek = page.getByRole('button', { name: /^Seek to / });
  await expect(seek).toBeVisible();

  await page.reload();
  await page.getByRole('tab', { name: 'Notes' }).click();
  await expect(page.getByRole('list', { name: 'Notes on this item' })).toContainText('Lantern appears on the left');
  await page.locator('video').evaluate((video: HTMLVideoElement) => { video.pause(); video.currentTime = 3.5; });
  await seek.click();
  // Notes store whole milliseconds; the player lands back on the captured moment.
  await expect.poll(() => videoTime(page)).toBeLessThan(stamp + 0.5);
  expect(await videoTime(page)).toBeGreaterThanOrEqual(stamp - 0.5);
});

/** The Library is title-based, so episodes and movies join the queue through the API here. */
async function queueLibraryItem(page: Page, title: string) {
  const id = await libraryItemId(page, title);
  const session = await (await page.request.get('/api/session/me')).json();
  const response = await page.request.post('/api/me/watch-queue/entries', {
    data: { ref: { kind: 'library', library_item_id: id }, position: 'end' },
    headers: { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token as string },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

test('watch queue adds, reorders across reload, and autoplays the next entry', async ({ page }) => {
  await page.goto('/library');
  for (const title of ['Realstack Show S01E01', 'Realstack Show S01E02', 'Realstack Remux']) await queueLibraryItem(page, title);
  await page.goto('/library/movies');
  await page.getByRole('button', { name: /^Realstack Direct,/ }).first().click();
  await page.getByRole('button', { name: /^(Play|Resume|Start)/ }).first().click();
  const queue = page.getByRole('region', { name: 'Your queue' });
  const order = () => queue.getByRole('listitem').locator('strong').allTextContents();
  await expect.poll(order).toEqual(['Realstack Show S01E01', 'Realstack Show S01E02', 'Realstack Remux']);

  await queue.getByRole('button', { name: 'Move Realstack Show S01E02 up' }).click();
  await expect.poll(order).toEqual(['Realstack Show S01E02', 'Realstack Show S01E01', 'Realstack Remux']);
  await page.reload();
  await expect.poll(order).toEqual(['Realstack Show S01E02', 'Realstack Show S01E01', 'Realstack Remux']);
  await expect(queue.getByRole('button', { name: 'Play Realstack Show S01E02, plays next automatically' })).toBeVisible();

  // The 4 s Direct fixture ends on its own; the queue head then starts without a click.
  await page.locator('video').evaluate((video: HTMLVideoElement) => { video.currentTime = 3.6; return video.play(); });
  await expect(page.getByRole('heading', { name: 'Realstack Show S01E02', level: 1 })).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => videoTime(page)).toBeGreaterThan(0.3);
});

test('Resume on Home\'s hero starts playback at the saved position', async ({ page }) => {
  const id = await libraryItemId(page, 'Realstack Direct');
  await page.goto(`/watch/library/${id}`);
  await expect.poll(() => videoTime(page)).toBeGreaterThan(0.5);
  const saved = page.waitForResponse((response) => response.request().method() === 'PUT' && new URL(response.url()).pathname === `/api/library/${id}/playback`);
  await page.locator('video').evaluate((video: HTMLVideoElement) => { video.currentTime = 2.5; });
  await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(2.5);
  await page.locator('video').evaluate((video: HTMLVideoElement) => video.pause());
  await saved;
  await page.goto('/');
  await expect(page.locator('.h-hero-kicker')).toHaveText(/continue watching/i);
  await expect(page.locator('.h-hero-title')).toHaveText('Realstack Direct');
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect(page).toHaveURL(new RegExp(`/watch/library/${id}$`));
  await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(2.3);
});
