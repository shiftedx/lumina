import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

/** Settings › Library & storage › Anime folders over the mocked API. */

test('adds, validates and saves anime folders, then follows the re-sort', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true }); // anime folders are an advanced row
  await mockAdminFixtures(page);
  let current = {
    jellyfin_enabled: true, jellyfin_url: 'https://vault.example', has_tmdb_key: true, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto',
    max_playback_sessions: 3, transcode_cache_gb: 10, jellyfin_import_url: null, anime_folders: ['Anime'], recategorising: false,
  };
  let polls = 0;
  const puts: unknown[] = [];
  // Registered after mockAdminFixtures, so this answers media-server: PUT starts a re-sort, the second poll ends it.
  await page.route((url) => url.pathname === '/api/admin/media-server', async (route) => {
    const request = route.request();
    if (request.method() === 'PUT') {
      const body = request.postDataJSON() as { anime_folders: string[] };
      puts.push(body);
      current = { ...current, anime_folders: body.anime_folders, recategorising: true };
    } else if (current.recategorising && ++polls >= 2) {
      current = { ...current, recategorising: false };
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(current) });
  });
  await page.goto('/settings/library');
  await signIn(page);
  const row = page.locator('[data-setting-id="library.anime"]');
  const field = row.getByRole('textbox', { name: 'Folder name' });
  await expect(field).toBeVisible();
  await field.fill('TV/Anime');
  await row.getByRole('button', { name: 'Add' }).click();
  await expect(row.getByText('Use one folder name, not a path.')).toBeVisible();
  await field.fill('Cartoons');
  await field.press('Enter');
  await expect(row.getByRole('button', { name: 'Remove Cartoons' })).toBeVisible();
  const results = await new AxeBuilder({ page }).include('[data-setting-id="library.anime"]').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id)).toEqual([]);
  await row.getByRole('button', { name: 'Save' }).click();
  await expect(row.getByText('Re-sorting titles into Anime…')).toBeVisible();
  await expect(row.getByText('Titles re-sorted.')).toBeVisible({ timeout: 10_000 });
  expect(puts).toEqual([{ anime_folders: ['Anime', 'Cartoons'] }]);
  await page.screenshot({ path: '../output/playwright/music/anime-folders-1440.png' });
});
