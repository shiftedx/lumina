import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall } from './gallery-mock';
import { mockApi, signIn } from './lumina-mock';
import { mockMusic } from './music-mock';

/**
 * The album and artist pages and the persistent music player over the mocked API.
 * Legacy /title/{id} and /watch/library/{id} bookmarks resolve to canonical Audio pages.
 */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 320, height: 844 };
type Viewport = typeof DESKTOP;

async function open(page: Page, path: string, viewport: Viewport = DESKTOP, scheme: 'light' | 'dark' = 'light') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  await mockGalleryWall(page);
  const calls = await mockMusic(page);
  await page.goto(path);
  await signIn(page);
  return calls;
}

async function noSeriousAxe(page: Page, where: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).exclude('.player-frame video').analyze();
  expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => `${where}: ${violation.id}`)).toEqual([]);
}

test('album playback stays on its Audio page and Next updates the player and Media Session', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, '/title/album-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await expect(page.getByText('Album · 2019 · 3 tracks · 5 min')).toBeVisible();
  const cover = await page.locator('.g-album-cover .g-art-square').boundingBox();
  expect(Math.round(cover!.width)).toBe(Math.round(cover!.height));
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  const player = page.getByRole('region', { name: 'Music player' });
  await expect(player).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 1');
  await player.getByRole('button', { name: 'Open listening room for Song 1' }).click();
  const room = page.getByRole('dialog', { name: 'Listening room' });
  await expect(room.getByRole('heading', { level: 3, name: 'Song 1' })).toBeVisible();
  await expect(room.getByRole('button', { name: 'Next track' })).toBeEnabled();
  await room.getByRole('button', { name: 'Next track' }).click();
  await expect(room.getByRole('heading', { level: 3, name: 'Song 2' })).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 2' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 2');
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  expect(errors).toEqual([]);
});

test('paused and dismissed music leaves the page clear and explicit playback restores the bar', async ({ page }) => {
  await open(page, '/audio/title/album-1');
  const playTrack = page.getByRole('button', { name: 'Play 1. Song 1, 1:30' });
  const player = page.getByRole('region', { name: 'Music player' });
  await playTrack.click();
  await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
  await player.getByRole('button', { name: 'Pause music' }).click();
  await expect(player).toBeHidden();
  await expect(page.getByRole('main')).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('--music-dock-height'))).toBe('');

  await playTrack.click();
  await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
  await player.getByRole('button', { name: 'Dismiss music player' }).click();
  await expect(player).toBeHidden();
  await expect(page.getByRole('main')).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue('--music-dock-height'))).toBe('');
  await playTrack.click();
  await expect(player.getByRole('button', { name: 'Pause music' })).toBeVisible();
});

test('artist → album, and Shuffle plays from a shuffled order', async ({ page }) => {
  await open(page, '/title/artist-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
  await expect(page.getByText('Artist · 1 album · 3 tracks')).toBeVisible();
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await page.getByRole('button', { name: 'Shuffle' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('region', { name: 'Music player' }).getByRole('button', { name: /Open listening room for Song [123]/ })).toBeVisible();
});

test('an album opened on its artist names the artist on Back, and goes there even after a track played', async ({ page }) => {
  await open(page, '/title/artist-1');
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  const player = page.getByRole('region', { name: 'Music player' });
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
  await page.getByRole('button', { name: 'Back to Artist A' }).click();
  await expect(page).toHaveURL(/\/audio\/title\/artist-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
  await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
});

test('keyboard only: the tracklist is one column and Left reaches Play', async ({ page }) => {
  await open(page, '/title/album-1');
  const first = page.getByRole('button', { name: 'Play 1. Song 1, 1:30' });
  await first.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: 'Play 2. Song 2, 1:30' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
  await expect(page.getByRole('region', { name: 'Music player' }).getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`music pages and the listening room in ${scheme} at ${viewport.width}px: no serious axe findings or sideways scroll`, async ({ page }, testInfo) => {
      await open(page, '/title/album-1', viewport, scheme);
      await expect(page.getByRole('button', { name: 'Play 1. Song 1, 1:30' })).toBeVisible();
      const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await noSeriousAxe(page, `album ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`album-${scheme}-${viewport.width}.png`), fullPage: true });
      await page.goto('/title/artist-1');
      await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
      await noSeriousAxe(page, `artist ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`artist-${scheme}-${viewport.width}.png`), fullPage: true });
      await page.goto('/watch/library/track-1');
      await expect(page).toHaveURL(/\/audio\/title\/album-1$/);
      const player = page.getByRole('region', { name: 'Music player' });
      await expect(player.getByRole('button', { name: 'Open listening room for Song 1' })).toBeVisible();
      await player.getByRole('button', { name: 'Open listening room for Song 1' }).click();
      await expect(page.getByRole('dialog', { name: 'Listening room' })).toBeVisible();
      await noSeriousAxe(page, `listening room ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: testInfo.outputPath(`listening-room-${scheme}-${viewport.width}.png`), fullPage: true });
    });
  }
}
