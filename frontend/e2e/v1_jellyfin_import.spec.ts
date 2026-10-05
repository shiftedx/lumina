import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Jellyfin history import: a member previews, backs out with Esc, previews again and imports. */

const SUMMARY = { watched: 12, in_progress: 2, favorites: 3, up_to_date: 4, unmatched: 1, unmatched_names: ['Heat (1995)'] };

test('a member previews and imports their Jellyfin history from Settings', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, { sidebar_collapsed: false });
  const bodies: Array<{ path: string; body: unknown }> = [];
  await page.route((url) => url.pathname.startsWith('/api/me/jellyfin-import'), (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (request.method() === 'GET') return json({ server: 'http://192.168.1.20:8096' });
    bodies.push({ path, body: request.postDataJSON() });
    return json(SUMMARY);
  });
  await page.goto('/settings/privacy');
  await signIn(page);
  const row = page.locator('[data-setting-id="privacy.jellyfin-history"]');
  await expect(row.getByText('Jellyfin server: http://192.168.1.20:8096')).toBeVisible();
  await row.getByLabel('Jellyfin username').fill('alice');
  await row.getByLabel('Jellyfin password').fill('jf-secret-pw');
  await row.getByRole('button', { name: 'Preview', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Import from Jellyfin?' });
  await expect(dialog.getByRole('list', { name: 'What would change' })).toContainText('Watched12');
  const alpha = await dialog.evaluate((element) => {
    const parts = (/rgba?\(([^)]+)\)/.exec(getComputedStyle(element).backgroundColor)?.[1] ?? '').split(/[ ,/]+/).filter(Boolean);
    return parts.length > 3 ? Number(parts[3]) : 1;
  });
  expect(alpha).toBe(1);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  // Cancel clears the password (it never outlives the attempt that used it), so a real member retypes it.
  await row.getByLabel('Jellyfin password').fill('jf-secret-pw');
  await row.getByRole('button', { name: 'Preview', exact: true }).click();
  await dialog.getByText('Not in Lumina (1)').click();
  await expect(dialog.getByText('Heat (1995)')).toBeVisible();
  await dialog.getByRole('button', { name: 'Import history', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(row.getByRole('status')).toHaveText('Imported from Jellyfin: 12 watched, 2 in progress, 3 new favorites.');
  await expect(row.getByLabel('Jellyfin password')).toHaveValue('');
  const credentials = { username: 'alice', password: 'jf-secret-pw' };
  expect(bodies).toEqual([
    { path: '/api/me/jellyfin-import/preview', body: credentials },
    { path: '/api/me/jellyfin-import/preview', body: credentials },
    { path: '/api/me/jellyfin-import', body: credentials },
  ]);
});
