import { mkdirSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockGalleryWall } from './gallery-mock';
import { mockLibraryItems } from './library-mock';
import { mockLibraryChannels, mockRemoteArtwork } from './live-youtube-mock';

for (const width of [1440, 390]) for (const category of ['movies', 'shows', 'anime', 'youtube']) {
  test(`scoped visual browse ${category} at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await mockApi(page, {});
    await mockGalleryWall(page, { total: 24, anime: 18, artwork: true });
    if (category === 'youtube') {
      await mockLibraryItems(page, { videos: 24 });
      await mockLibraryChannels(page, 8);
      await mockRemoteArtwork(page);
    }
    await page.goto(`/video/${category}`); await signIn(page);
    const hero = page.locator('.g-browse-hero');
    await expect(hero).toBeVisible();
    const tools = page.getByRole('navigation', { name: 'Library tools', exact: true });
    expect(await tools.getByRole('link').allTextContents()).toEqual(['Collections', 'Deleted']);
    expect((await hero.boundingBox())!.y).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    const header = page.locator('.g-topbar');
    const context = await page.locator('.g-browse-context').boundingBox();
    expect(context!.y).toBeGreaterThanOrEqual((await header.boundingBox())!.height);
    mkdirSync('../output/category-browse', { recursive: true });
    await page.screenshot({ path: `../output/category-browse/${category}-${width}.png` });
    if (category !== 'youtube') {
      const genres = page.getByRole('group', { name: `Browse ${category} by genre` });
      await genres.scrollIntoViewIfNeeded();
      await expect(genres).toBeVisible();
      await page.screenshot({ path: `../output/category-browse/${category}-browse-${width}.png` });
      await genres.getByRole('button', { name: /Drama/ }).click();
      await expect(page).toHaveURL(/genre=Drama/);
    } else {
      await page.keyboard.press('Control+k');
      const search = page.getByRole('searchbox', { name: 'Search saved videos' });
      await search.fill('Video 1'); await search.press('Enter');
      await expect(page).toHaveURL(/search=Video/);
      await search.press('Escape');
      await expect(page).not.toHaveURL(/search=/);
      await page.getByRole('button', { name: 'Channels', exact: true }).click();
      await expect(page.locator('.g-channels-tiles .g-channel-card')).toHaveCount(8);
      await page.screenshot({ path: `../output/category-browse/youtube-channels-${width}.png` });
      await page.keyboard.press('Control+k');
      const channelSearch = page.getByRole('searchbox', { name: 'Search saved channels' });
      await channelSearch.fill('No matching creator'); await channelSearch.press('Enter');
      await expect(page.getByText('0 matching saved channels', { exact: true })).toBeVisible();
      await expect(page.locator('.g-channels-tiles .g-channel-card')).toHaveCount(0);
      await channelSearch.press('Escape');
      await expect(page.locator('.g-channels-tiles .g-channel-card')).toHaveCount(8);
    }
  });
}
