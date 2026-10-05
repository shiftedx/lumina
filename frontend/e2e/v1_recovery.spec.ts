import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** Admin backups: list, verify, delete confirmation, schedule */

const backups = [
  { name: 'lumina-20260924T031500Z-scheduled', kind: 'scheduled', created_at: '2026-09-24T03:15:00+00:00', app_version: '1.0.0', schema_version: 1, size: 48 * 1024 ** 2, sha256: 'a', counts: { users: 4, library_items: 1272, library_notes: 38 } },
  { name: 'lumina-20260920T184000Z-manual', kind: 'manual', created_at: '2026-09-20T18:40:00+00:00', app_version: '1.0.0', schema_version: 1, size: 46 * 1024 ** 2, sha256: 'b', counts: { users: 4, library_items: 1240, library_notes: 35 } },
];

test('test_admin_backups', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/admin/backups', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ backups, schedule: { daily: true, keep: 7 } }) }));
  await page.route('**/api/admin/backups/*/verify', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, problems: [] }) }));
  await page.goto('/admin/backups');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Backups', exact: true })).toBeVisible();
  const row = page.getByRole('listitem', { name: /Automatic backup/ });
  await row.getByRole('button', { name: /Verify/ }).click();
  await expect(row.getByText('Verified: intact and restorable')).toBeVisible();
  await page.getByRole('listitem', { name: /Manual backup/ }).getByRole('button', { name: /Delete backup from/ }).click();
  await expect(page.getByRole('dialog').getByRole('button', { name: 'Cancel' })).toBeFocused(); // danger confirms start on Cancel
  await expect(page.getByRole('button', { name: 'Delete backup', exact: true })).toBeVisible();
  await expect(page.getByRole('spinbutton', { name: /to keep/ })).toHaveValue('7');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
