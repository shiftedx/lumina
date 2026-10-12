/** Browser: the Library YouTube tab's Channels view and the channel filter. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { mockLibraryItems } from './library-mock';
import { mockChannelPage, mockLibraryChannels, mockRemoteArtwork } from './live-youtube-mock';
import { mockApi, signIn } from './lumina-mock';

async function open(page: Page, path = '/library/youtube', { width = 1440, height = 900, theme = 'light' as 'light' | 'dark' } = {}) {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height });
  await mockApi(page, { sidebar_collapsed: true, theme });
  await mockGalleryWall(page);
  await mockLibrarySections(page);
  await mockLibraryItems(page);
  await mockRemoteArtwork(page);
  await mockChannelPage(page);
  await mockLibraryChannels(page, 12);
  await page.goto(path);
  await signIn(page);
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`Library Channels reads cleanly in ${theme} at ${label} width`, async ({ page }) => {
      await open(page, '/library/youtube?view=channels', { width, height, theme });
      await expect(page.locator('.g-channel-card')).toHaveCount(12);
      const results = await new AxeBuilder({ page }).include('main').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/visual/library-channels-${theme}-${label}.png` });
    });
  }
}

test('Channels lists 12 tiles with unwatched badges, sorts by name, and a tile without a channel id opens the filtered Videos wall', async ({ page }) => {
  await open(page);
  await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Channels' }).click();
  await expect(page).toHaveURL(/\/library\/youtube\?view=channels$/);
  await expect(page.locator('.g-channel-card')).toHaveCount(12);
  await expect(page.getByRole('link', { name: /^Channel B1, .*1 unwatched/ })).toBeVisible();
  await expect(page.getByRole('link', { name: /^Channel A0, / })).not.toContainText('unwatched');
  await expect(page.getByRole('link', { name: /^Channel A0, / }).locator('.g-art-marker, .g-marker')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Sort' }).selectOption('name');
  await expect(page).toHaveURL(/\/library\/youtube\?view=channels&sort=name$/);
  await page.getByRole('link', { name: /^Channel A0, / }).click();
  await expect(page).toHaveURL(/\/library\/youtube\?channel=Channel(\+|%20)A0$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Channel A0' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Clear' }).or(page.getByRole('button', { name: 'Clear' })).first()).toBeVisible();
});

test('a tile with a channel id opens the channel page on In your library', async ({ page }) => {
  await open(page, '/library/youtube?view=channels');
  await page.getByRole('link', { name: /^Channel B1, / }).click();
  await expect(page).toHaveURL(/\/channel\/youtube\/UC[0-9A-Za-z_-]{22}\?tab=library$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
});
