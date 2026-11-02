import { expect, test, type Page } from '@playwright/test';

import type { ServerNavigation } from '../src/types';
import { mockGalleryWall } from './gallery-mock';
import { mockApi, signIn } from './lumina-mock';

const MOVIES_AND_ANIME: ServerNavigation = {
  categories: ['movies', 'anime'],
  features: { streaming: false, requests: true },
};
const ANIME_ONLY: ServerNavigation = {
  categories: ['anime'],
  features: { streaming: false, requests: true },
};

async function openCategory(page: Page, path: string) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: true }, MOVIES_AND_ANIME);
  await mockGalleryWall(page, { total: 16, anime: 12, artwork: true });
  await page.goto(path);
  await signIn(page);
}

test('server-visible category navigation exposes only enabled bespoke pages and retains library tools', async ({ page }) => {
  await openCategory(page, '/video/movies');
  await expect(page.getByRole('heading', { level: 1, name: 'Movies' })).toBeVisible();

  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  const navigation = page.getByRole('dialog', { name: 'Navigation', exact: true });
  await expect(navigation.getByRole('button', { name: 'Movies', exact: true })).toBeVisible();
  await expect(navigation.getByRole('button', { name: 'Anime', exact: true })).toBeVisible();
  for (const name of ['Shows', 'YouTube', 'Recordings', 'All video', 'Streaming']) {
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveCount(0);
  }

  await navigation.getByRole('button', { name: 'Anime', exact: true }).click();
  await expect(page).toHaveURL(/\/video\/anime$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Anime' })).toBeVisible();

  const tools = page.getByRole('navigation', { name: 'Library tools', exact: true });
  await expect(tools.getByRole('link', { name: 'Collections', exact: true })).toHaveAttribute('href', '/library/collections');
  await expect(tools.getByRole('link', { name: 'Deleted', exact: true })).toHaveAttribute('href', '/library/deleted');
  for (const name of ['All', 'Movies', 'Shows', 'Anime', 'Music', 'YouTube', 'Recordings']) {
    await expect(tools.getByRole('link', { name, exact: true })).toHaveCount(0);
  }
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeVisible();
});

test('legacy video addresses canonicalize to the first enabled category', async ({ page }) => {
  await openCategory(page, '/video');
  await expect(page).toHaveURL(/\/video\/movies$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Movies' })).toBeVisible();
});

test('legacy video and mobile navigation fall back to Anime when it is the only enabled category', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: true }, ANIME_ONLY);
  await mockGalleryWall(page, { total: 16, anime: 12, artwork: true });
  await page.goto('/video');
  await signIn(page);

  await expect(page).toHaveURL(/\/video\/anime$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Anime' })).toBeVisible();
  const tabs = page.getByRole('navigation', { name: 'Mobile primary navigation', exact: true });
  await expect(tabs.getByRole('button', { name: 'Anime', exact: true })).toHaveAttribute('aria-current', 'page');

  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  const navigation = page.getByRole('dialog', { name: 'Navigation', exact: true });
  await expect(navigation.getByRole('button', { name: 'Anime', exact: true })).toBeVisible();
  for (const name of ['Movies', 'Shows', 'YouTube', 'Recordings', 'All video']) {
    await expect(navigation.getByRole('button', { name, exact: true })).toHaveCount(0);
  }
});
