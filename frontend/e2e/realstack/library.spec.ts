/**
 * Library gallery on the real stack: the music root imports into four albums and
 * three album artists, an album shows its cover square and plays on to its next track, and the anime folders sort a
 * show out of Shows and back. Runs after household.setup (owner signed in, Fixture media imported privately).
 */
import path from 'node:path';

import { expect, ROOT, test } from './realstack';

const MUSIC_ROOT = path.join(ROOT, 'Music');

test('music imports into albums and artists, an album plays on, and the anime folders re-sort the library', async ({ page }) => {
  test.setTimeout(240_000);

  await test.step('register the music folder as an external root and import it, shared', async () => {
    await page.goto('/admin/storage');
    await page.getByLabel('Container path').fill(MUSIC_ROOT);
    await page.getByLabel('Label').fill('Music');
    await page.getByLabel('Mode').selectOption({ label: 'External — read-only, for imports' });
    await page.getByRole('button', { name: 'Add root' }).click();
    const root = page.getByRole('group', { name: /Import Music now/ });  // roots are table rows; the import step opens under the new row
    await expect(page.getByRole('row', { name: /^Music External/ })).toContainText('Available');
    await expect(root.getByRole('radio', { name: /Household/ })).toBeChecked();
    await root.getByRole('button', { name: 'Import now' }).click();
    await expect(page.getByRole('region', { name: 'Music Complete' })).toBeVisible({ timeout: 90_000 });
  });

  await test.step('sections count four albums and three album artists', async () => {
    // Sections are cached 30 s per member (routers/library_sections.py), so a count read before the import may linger.
    await expect.poll(async () => {
      const { albums, artists } = await (await page.request.get('/api/library/sections')).json();
      return { albums, artists };
    }, { timeout: 45_000, intervals: [2_000] }).toEqual({ albums: 4, artists: 3 });
  });

  let tracks: { item_id: string }[] = [];
  await test.step('the album page shows its cover at exactly 1:1', async () => {
    await page.goto('/library/music');
    await page.getByRole('button', { name: /^Album One by Artist A/ }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Album One' })).toBeVisible();
    const albumId = decodeURIComponent(new URL(page.url()).pathname.split('/').pop()!);
    tracks = (await (await page.request.get(`/api/titles/${albumId}`)).json()).tracks;
    expect(tracks).toHaveLength(3);
    // The served rendition itself (a 300×200 cover.jpg, cropped by the server), not the element's box.
    const cover = page.locator('main .g-art-square img.g-art-image').first();
    await expect.poll(() => cover.evaluate(async (image: HTMLImageElement) => {
      if (!image.complete || !image.currentSrc) return null;
      const served = new Image();
      served.src = image.currentSrc;
      await served.decode();
      return served.naturalWidth > 0 && served.naturalWidth === served.naturalHeight;
    }), { timeout: 30_000 }).toBe(true);
  });

  await test.step('track 1 plays to its end and track 2 starts', async () => {
    await page.getByRole('button', { name: /^Play 1\. Song One/ }).click();
    await expect(page).toHaveURL(new RegExp(`/watch/library/${tracks[0].item_id}$`));
    await expect(page).toHaveURL(new RegExp(`/watch/library/${tracks[1].item_id}$`), { timeout: 30_000 });
  });

  await test.step('Anime holds Show A and Film; Shows does not hold Show A', async () => {
    await page.goto('/library/anime');
    await expect(page.getByRole('button', { name: /^Show A,/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /^Film, 2020/ }).first()).toBeVisible();
    await page.goto('/library/shows');
    await expect(page.getByRole('button', { name: /^Realstack Show,/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /^Show A,/ })).toHaveCount(0);
  });

  await test.step('with no anime folders, Show A is back in Shows; then the default returns', async () => {
    await page.goto('/settings/library');
    // Anime folders are an advanced setting (2.7.0): reveal them first.
    await page.locator('.g-settings-advanced').getByLabel('Show advanced settings').check();
    const row = page.locator('[data-setting-id="library.anime"]');
    await row.getByRole('button', { name: 'Remove Anime' }).click();
    await row.getByRole('button', { name: 'Save' }).click();
    await expect(row.getByText('Titles re-sorted.')).toBeVisible({ timeout: 30_000 });
    await expect.poll(async () => {
      await page.goto('/library/shows');
      await expect(page.getByRole('button', { name: /^Realstack Show,/ }).first()).toBeVisible(); // the wall has drawn
      return page.getByRole('button', { name: /^Show A,/ }).count();
    }, { timeout: 60_000 }).toBeGreaterThan(0);
    await page.goto('/settings/library');
    await row.getByLabel('Folder name').fill('Anime');
    await row.getByRole('button', { name: 'Add' }).click();
    await row.getByRole('button', { name: 'Save' }).click();
    await expect(row.getByText('Titles re-sorted.')).toBeVisible({ timeout: 30_000 });
  });
});
