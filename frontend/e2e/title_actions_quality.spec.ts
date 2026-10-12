import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import { movieSummary, titleDetail, userData } from '../src/test/galleryFixtures';
import { mockGalleryTitle } from './gallery-title-mock';
import { mockApi, mockTitles, signIn, user } from './lumina-mock';

const OUT = process.env.TITLE_ACTIONS_OUT || join(process.cwd(), '..', 'output', 'app-quality', 'title-actions-after');
const movie = titleDetail(movieSummary('movie-1', {
  name: 'Northern Lantern',
  overview: 'A family carries a lamp across the ice before the winter harbor closes.',
  play_item_id: 'item-movie-4k',
  user_data: userData({ position_seconds: 2530, resume_item_id: 'item-movie-4k' }),
}), {
  versions: [{ item_id: 'item-movie-4k', height: 2160, hdr: true, file_size: 58 * 1024 ** 3 }],
  extras: [{ item_id: 'item-trailer', extra_type: 'trailer', name: 'Trailer', duration_seconds: 90 }],
});

async function openTitle(page: Page, viewport: { width: number; height: number }, scheme: 'light' | 'dark') {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  await mockGalleryTitle(page, { detail: movie });
  await page.goto('/title/movie-1');
  await signIn(page);
  // Exercise the complete owner action set. The shared API fixture intentionally omits optional capabilities.
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, can_edit_details: true } } }));
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
}

for (const variant of [
  { name: 'desktop-light', viewport: { width: 1440, height: 900 }, scheme: 'light' },
  { name: 'desktop-dark', viewport: { width: 1440, height: 900 }, scheme: 'dark' },
  { name: 'phone-light', viewport: { width: 390, height: 844 }, scheme: 'light' },
  { name: 'phone-dark', viewport: { width: 390, height: 844 }, scheme: 'dark' },
] as const) {
  test(`title actions ${variant.name} keep one viewing action prominent and every owner action usable`, async ({ page }) => {
    mkdirSync(OUT, { recursive: true });
    await openTitle(page, variant.viewport, variant.scheme);
    const actions = page.locator('.t-actions button');
    await page.screenshot({ fullPage: true, path: join(OUT, `title-actions-${variant.name}.png`) });
    expect(await actions.allTextContents().then((labels) => labels.map((label) => label.trim()))).toEqual([
      'Resume at 42:10',
      'Add to watchlist',
      'Trailer',
      'Mark watched',
      'Favorite',
      'Edit details',
      'Fix match',
    ]);

    await expect(actions.filter({ has: page.locator('.g-button-text', { hasText: 'Resume at 42:10' }) })).toHaveClass(/is-primary/);
    await expect(page.locator('.t-actions .is-primary')).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Add to watchlist' })).toHaveClass(/is-secondary/);
    await expect(page.getByRole('button', { name: 'Trailer' })).toHaveClass(/is-secondary/);
    for (const name of ['Mark watched', 'Favorite', 'Edit details', 'Fix match']) {
      await expect(page.getByRole('button', { name })).toHaveClass(/is-quiet/);
    }
    if (variant.viewport.width === 390) {
      const boxes = await actions.evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect()).map(({ x, width, height }) => ({ x, width, height })));
      expect(boxes.every(({ x, width, height }) => x >= 0 && x + width <= 390 && height >= 44)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    }
  });
}
