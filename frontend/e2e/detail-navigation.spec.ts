import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockGalleryWall } from './gallery-mock';
import { mockGalleryTitle } from './gallery-title-mock';
import { movieSummary, seriesSummary, titleDetail } from '../src/test/galleryFixtures';

const movie = movieSummary('movie-0000', { category: 'movies', name: 'A Title 0' });
const show = seriesSummary('series-0000', { category: 'shows', name: 'Harbor Lights' });
const anime = movieSummary('anime-0000', { category: 'anime', name: 'Animated Lantern' });

for (const scheme of ['dark', 'light'] as const) for (const width of [1440, 390, 320]) {
  test(`detail Back provides context below the header in ${scheme} at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await mockApi(page, {});
    await mockGalleryTitle(page, { detail: titleDetail(movie), others: [titleDetail(show), titleDetail(anime)] });
    await mockGalleryWall(page, { total: 30, artwork: true });
    await page.goto(`/video/title/${movie.id}`); await signIn(page);

    for (const [title, label] of [[movie, 'Movie'], [show, 'Show'], [anime, 'Anime']] as const) {
      if (title.id !== movie.id) await page.goto(`/video/title/${title.id}`);
      await expect(page.locator('.t-hero')).toBeVisible();
      await expect(page.getByRole('heading', { name: title.name, exact: true })).toBeVisible();
      const header = page.locator('.g-topbar');
      const back = page.locator('.t-back');
      await expect(back).toBeVisible();
      await expect.poll(() => page.locator('.t-hero-art img.g-art-image').first().evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(1);
      await page.screenshot({ path: `../output/detail-navigation/${scheme}-${width}-${label.toLowerCase()}.png` });
      await expect(header.locator('.g-topbar-context')).toHaveCount(0);
      await expect(back).toHaveText(label === 'Movie' ? 'Movies' : label === 'Show' ? 'Shows' : 'Anime');
      const barBox = await header.boundingBox();
      const backBox = await back.boundingBox();
      expect.soft(backBox!.y, 'Back clears the global menu and mark').toBeGreaterThanOrEqual(barBox!.y + barBox!.height);
    }
  });
}

test('related titles omit duplicate header context and Back restores the exact Movies card', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await mockApi(page, {});
  await mockGalleryTitle(page, { detail: titleDetail(movie), others: [titleDetail(show)], similar: [show] });
  await mockGalleryWall(page, { total: 30, artwork: true });
  await page.goto('/video/movies'); await signIn(page);
  const card = page.locator('.g-poster').first();
  await expect(card).toBeVisible();
  await card.focus(); await card.press('Enter');
  await expect(page).toHaveURL(/\/video\/title\/movie-0000$/);
  await expect(page.locator('.g-topbar-context')).toHaveCount(0);
  await page.getByRole('button', { name: /^Harbor Lights/ }).click();
  await expect(page).toHaveURL(/\/video\/title\/series-0000$/);
  await expect(page.locator('.g-topbar-context')).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/\/video\/title\/movie-0000$/);
  await expect(page.locator('.g-topbar-context')).toHaveCount(0);
  await page.locator('.t-back').click({ timeout: 3000 });
  await expect(page).toHaveURL(/\/video\/movies$/);
  await expect(card).toBeFocused();
  await expect(page.locator('.g-topbar-context')).toHaveText('Movies');
});
