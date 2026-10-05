import { expect, test, type Page } from '@playwright/test';

import { mockApi, mockTitles, signIn } from './lumina-mock';

/** Up next on a library episode: the show's following episodes, no empty queue, no web-video copy; autoplay continues it. */
async function openEpisode(page: Page, itemId: string, viewport = { width: 1440, height: 900 }) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, {});
  await mockTitles(page);
  await page.goto(`/watch/library/${itemId}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
}

test('an episode lists the rest of its show in order and plays the next one when it ends', async ({ page }) => {
  await openEpisode(page, 'item-ep-1-2');
  const rail = page.getByRole('region', { name: 'Up next' });
  await expect(rail.locator('.watch-queue-item strong')).toHaveText(['S1 · E3 · Fog Line', 'S2 · E1 · Low Tide', 'S2 · E2 · The Ferry', 'S2 · E3 · Fog Line']);
  await expect(rail.getByRole('button', { name: 'See all episodes' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Your queue/ })).toHaveCount(0);
  await expect(page.getByText(/Related videos appear here/)).toHaveCount(0);
  await page.locator('video').first().evaluate((media: HTMLVideoElement) => media.dispatchEvent(new Event('ended')));
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-1-3$/);
});

test('the rail fits a phone without sideways scroll', async ({ page }) => {
  await openEpisode(page, 'item-ep-2-1', { width: 390, height: 844 });
  await expect(page.getByRole('region', { name: 'Up next' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('a movie outside a collection has no Up next', async ({ page }) => {
  await openEpisode(page, 'item-movie-4k');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Up next' })).toHaveCount(0);
  await expect(page.getByText(/Related videos/)).toHaveCount(0);
});

test('fullscreen stays on while autoplay moves to the next episode', async ({ page }) => {
  await openEpisode(page, 'item-ep-1-2');
  await page.locator('.lumina-player').first().hover();
  await page.getByRole('button', { name: 'Enter fullscreen' }).click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.className)).toBe('watch-host');
  await page.locator('video').first().evaluate((media: HTMLVideoElement) => media.dispatchEvent(new Event('ended')));
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-1-3$/);
  await expect(page.locator('.lumina-player').first()).toBeVisible();
  expect(await page.evaluate(() => document.fullscreenElement?.className)).toBe('watch-host');
  const frame = await page.locator('.player-frame').first().boundingBox();
  const viewport = page.viewportSize();
  expect(frame && viewport && Math.round(frame.width) === viewport.width && Math.round(frame.height) === viewport.height).toBe(true);
});

test('picture-in-picture stays open while autoplay moves to the next episode', async ({ page }) => {
  await openEpisode(page, 'item-ep-1-2');
  const video = page.locator('.lumina-player video').first();
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThanOrEqual(1);
  await page.locator('.lumina-player').first().hover();
  await page.getByRole('button', { name: 'Picture in picture' }).click();
  await expect.poll(() => page.evaluate(() => Boolean(document.pictureInPictureElement))).toBe(true);
  await page.evaluate(() => { (window as unknown as { pipVideo: Element | null }).pipVideo = document.pictureInPictureElement; });
  await video.evaluate((media: HTMLVideoElement) => media.dispatchEvent(new Event('ended')));
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-1-3$/);
  await expect.poll(() => page.locator('.lumina-player video').first().getAttribute('src')).toContain('item-ep-1-3');
  // The same element, still in the floating window, now in the next episode's player.
  expect(await page.evaluate(() => {
    const pip = document.pictureInPictureElement;
    return pip !== null && pip === (window as unknown as { pipVideo: Element | null }).pipVideo && pip === document.querySelector('.lumina-player video');
  })).toBe(true);
  expect(await page.evaluate(() => navigator.mediaSession?.metadata?.title)).toBeTruthy();
});
