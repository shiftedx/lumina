import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall } from './gallery-mock';
import { mockApi, signIn } from './lumina-mock';
import { mockMusic } from './music-mock';

/**
 * The album and artist pages and the audio stage over the mocked API. The Music
 * tab itself sits behind the /library/music route, which has its own journey; these pages are reached by /title/{id}.
 */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
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

const end = (page: Page) => page.locator('.player-frame audio').evaluate((media: HTMLAudioElement) => media.dispatchEvent(new Event('ended')));

test('album → play → the next track starts on the audio stage, with Media Session metadata', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, '/title/album-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await expect(page.getByText('Album · 2019 · 3 tracks · 5 min')).toBeVisible();
  const cover = await page.locator('.g-album-cover .g-art-square').boundingBox();
  expect(Math.round(cover!.width)).toBe(Math.round(cover!.height));
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  await expect(page).toHaveURL(/\/watch\/library\/track-1$/);
  const stage = page.locator('.g-audio-stage');
  await expect(stage.getByText('Song 1', { exact: true })).toBeVisible();
  await expect(stage.getByText('Artist A · Album One')).toBeVisible();
  await expect(stage.getByText('Next · 2. Song 2')).toBeVisible();
  await expect(page.locator('.player-frame audio')).toHaveAttribute('src', '/api/library/track-1/media');
  expect(await page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 1');
  await end(page);
  await expect(page).toHaveURL(/\/watch\/library\/track-2$/);
  await expect(stage.getByText('Song 2', { exact: true })).toBeVisible();
  await expect(stage.getByText('Next · 3. Song 3')).toBeVisible();
  expect(await page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('Song 2');
  await expect(page.locator('.player-up-next')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('artist → album, and Shuffle plays from a shuffled order', async ({ page }) => {
  await open(page, '/title/artist-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
  await expect(page.getByText('Artist · 1 album · 3 tracks')).toBeVisible();
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/title\/album-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
  await page.getByRole('button', { name: 'Shuffle' }).click();
  await expect(page).toHaveURL(/\/watch\/library\/track-[123]$/);
  await expect(page.locator('.g-audio-stage')).toBeVisible();
});

test('an album opened on its artist names the artist on Back, and goes there even after a track played', async ({ page }) => {
  await open(page, '/title/artist-1');
  await page.getByRole('button', { name: 'Album One by Artist A, 2019, 3 tracks' }).click();
  await expect(page).toHaveURL(/\/title\/album-1$/);
  await page.getByRole('button', { name: 'Play 1. Song 1, 1:30' }).click();
  await expect(page).toHaveURL(/\/watch\/library\/track-1$/);
  await page.locator('.watch-back').click();
  await expect(page).toHaveURL(/\/title\/album-1$/);
  await page.getByRole('button', { name: 'Back to Artist A' }).click();
  await expect(page).toHaveURL(/\/title\/artist-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
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
  await expect(page).toHaveURL(/\/watch\/library\/track-1$/);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`music pages and the stage in ${scheme} at ${viewport.width}px: no serious axe findings, no sideways scroll, screenshots`, async ({ page }) => {
      await open(page, '/title/album-1', viewport, scheme);
      await expect(page.getByRole('button', { name: 'Play 1. Song 1, 1:30' })).toBeVisible();
      const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      await noSeriousAxe(page, `album ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `../output/playwright/music/album-${scheme}-${viewport.width}.png`, fullPage: true });
      await page.goto('/title/artist-1');
      await expect(page.getByRole('heading', { level: 1, name: 'Artist A' })).toBeVisible();
      await noSeriousAxe(page, `artist ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `../output/playwright/music/artist-${scheme}-${viewport.width}.png`, fullPage: true });
      await page.goto('/watch/library/track-1');
      await expect(page.locator('.g-audio-stage')).toBeVisible();
      await noSeriousAxe(page, `audio stage ${scheme} ${viewport.width}`);
      expect(await sideways()).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `../output/playwright/music/stage-${scheme}-${viewport.width}.png` });
    });
  }
}
