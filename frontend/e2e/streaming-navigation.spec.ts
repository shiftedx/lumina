import { mkdirSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockLibraryItems } from './library-mock';
import { liveItems, liveSnapshotBody, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { chooseSelect } from './select-helper';

for (const width of [1440, 390]) {
  test(`Streaming opens discovery and groups saved provider videos at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await mockApi(page, { streaming_providers: ['twitch'] });
    const library = await mockLibraryItems(page, { videos: 20 });
    await mockRemoteArtwork(page);
    await mockLive(page, liveSnapshotBody({ items: liveItems(6) }));
    await page.goto('/'); await signIn(page);
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    const nav = page.getByRole('navigation', { name: 'Primary', exact: true });
    await expect(nav.getByRole('button', { name: 'YouTube', exact: true })).toHaveCount(0);
    await nav.getByRole('button', { name: 'Streaming', exact: true }).click();
    await expect(page).toHaveURL(/\/streaming$/);
    await expect(page.getByRole('heading', { name: 'Streaming', level: 1 })).toBeVisible();
    await expect(page.getByRole('radio', { name: 'Browse', exact: true })).toBeChecked();
    await page.getByRole('radio', { name: 'Live now', exact: true }).check();
    await expect(page).toHaveURL(/\/streaming\/live$/);
    await expect(page.locator('.g-live-hero')).toBeVisible();
    await page.getByRole('radio', { name: 'Library', exact: true }).check();
    await expect(page).toHaveURL(/\/streaming\/library$/);
    await expect(page.getByRole('button', { name: /^Video 0, / })).toBeVisible();
    expect(library.requests.some((url) => url.searchParams.get('source') === 'saved')).toBe(true);
    await chooseSelect(page, page.getByRole('combobox', { name: 'Source' }), 'Twitch');
    await expect(page).toHaveURL(/\/streaming\/library\?source=twitch$/);
    await expect.poll(() => library.requests.some((url) => url.searchParams.get('source') === 'twitch')).toBe(true);
    await page.reload();
    await expect(page.getByRole('combobox', { name: 'Source' })).toHaveText('Twitch');
    await expect(page.getByRole('button', { name: /^Video 4, / })).toBeVisible();
    const tabs = await page.getByRole('group', { name: 'Streaming view' }).boundingBox();
    const topbar = await page.locator('.g-topbar').boundingBox();
    expect(tabs!.y).toBeGreaterThanOrEqual(topbar!.y + topbar!.height);
    await expect(page.locator('.g-browse-hero')).toHaveCount(0);
    expect((await new AxeBuilder({ page }).include('main').analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    mkdirSync('../output/streaming-navigation', { recursive: true });
    await page.screenshot({ path: `../output/streaming-navigation/library-${width}.png` });
  });
}


test('saved streaming videos stay available without remote discovery', async ({ page }) => {
  await mockApi(page, {}, { categories: ['youtube'], features: { streaming: false, requests: false } });
  await mockLibraryItems(page, { videos: 5 });
  await page.goto('/streaming/library?source=twitch'); await signIn(page);
  await expect(page.getByRole('button', { name: /^Video 4, / })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Browse', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming', exact: true })).toBeVisible();
});
