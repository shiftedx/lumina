/** Real photo import, exact original bytes, derivatives, favorite persistence, and recoverable removal. */
import { createHash } from 'node:crypto';

import { landscapePng } from '../photo-fixtures';
import { expect, test } from './realstack';

test('Photos imports real pixels, preserves originals, and persists favorites and restoration', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const original = landscapePng(8, 1440, 960);
  await page.goto('/photos');
  await expect(page.getByRole('heading', { name: 'Photos', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Import', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Add photos' });
  await dialog.locator('input[type=file]').first().setInputFiles({ name: 'Realstack landscape.png', mimeType: 'image/png', buffer: original });
  const imported = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/photos/import' && response.request().method() === 'PUT');
  await dialog.getByRole('button', { name: 'Import', exact: true }).click();
  const importResponse = await imported;
  expect(importResponse.status(), await importResponse.text()).toBe(201);
  await expect(dialog.getByRole('button', { name: 'Done', exact: true })).toBeEnabled({ timeout: 90_000 });
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  const listing = await page.request.get('/api/photos?q=Realstack');
  expect(listing.ok(), await listing.text()).toBe(true);
  const body = await listing.json() as { items: Array<{ id: string; title: string | null; filename: string; width: number; height: number; original_url: string; thumbnail_url: string }> };
  const photo = body.items.find((item) => item.filename === 'Realstack landscape.png');
  expect(photo).toBeTruthy();
  expect([photo!.width, photo!.height]).toEqual([1440, 960]);
  const label = photo!.title || photo!.filename;
  const tile = page.locator(`[data-photo-id="${photo!.id}"]`);
  await expect(tile).toBeVisible();
  await expect.poll(() => tile.locator('img').first().evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  const download = await page.request.get(photo!.original_url);
  expect(download.ok()).toBe(true);
  expect(createHash('sha256').update(await download.body()).digest('hex')).toBe(createHash('sha256').update(original).digest('hex'));
  const preview = await page.request.get(photo!.thumbnail_url);
  expect(preview.ok()).toBe(true);
  expect(preview.headers()['content-type']).toMatch(/^image\//);
  expect((await preview.body()).length).toBeLessThan(original.length);
  await page.screenshot({ path: testInfo.outputPath('real-photos-desktop.png') });

  await page.getByRole('button', { name: `View ${label}`, exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Photo viewer' })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: `Add ${label} to favorites`, exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: `Remove ${label} from favorites`, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Move ${label} to trash`, exact: true }).click();
  await page.getByRole('button', { name: 'Trash', exact: true }).click();
  await expect(page.getByRole('button', { name: `Restore ${label}`, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Restore ${label}`, exact: true }).click();
  await page.getByRole('button', { name: 'Timeline', exact: true }).click();
  await expect(page.getByRole('button', { name: `View ${label}`, exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: testInfo.outputPath('real-photos-phone.png') });

  await page.getByRole('button', { name: 'Import', exact: true }).click();
  const sources = page.getByRole('dialog', { name: 'Add photos' });
  await sources.getByRole('button', { name: 'Synced folders', exact: true }).click();
  await sources.getByRole('button', { name: 'Add source', exact: true }).click();
  const rootSelect = sources.getByRole('combobox', { name: 'Photo storage root' });
  await rootSelect.click();
  await page.getByRole('option', { name: 'Fixture media', exact: true }).click();
  await sources.getByLabel(/Folder inside that root/).fill('Photos');
  await sources.getByRole('button', { name: 'Add and scan', exact: true }).click();
  await expect.poll(async () => {
    const response = await page.request.get('/api/admin/photo-sources');
    const status = await response.json() as { items: Array<{ relative_folder: string; indexed_count: number }> };
    return status.items.find((item) => item.relative_folder === 'Photos')?.indexed_count ?? 0;
  }, { timeout: 90_000 }).toBe(1);
  await sources.getByRole('button', { name: 'Done', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'View Realstack source.png', exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('button', { name: 'View Realstack source.png', exact: true }).locator('img').evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
});
