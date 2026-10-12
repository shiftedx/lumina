import { readFile } from 'node:fs/promises';
import { expect, OWNER, test } from './realstack';

test('theme choice persists on the server across reload', async ({ page }) => {
  await page.goto('/settings/appearance');
  await page.getByRole('radio', { name: 'Dark' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(async () => (await (await page.request.get('/api/settings/me')).json()).ui_prefs.theme).toBe('dark');
  // Drop the local boot hint: only the server copy can bring dark back under a light OS scheme.
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByRole('radio', { name: 'Dark' })).toBeChecked();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('radio', { name: 'System' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('settings export downloads the member’s JSON profile', async ({ page }) => {
  await page.goto('/settings/privacy');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export my data' }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe('lumina-export.json');
  const data = JSON.parse(await readFile((await file.path())!, 'utf8'));
  expect(data).toEqual(expect.objectContaining({ settings: expect.anything() }));
  expect(data.user.username).toBe('hearth');
  expect(JSON.stringify(data)).not.toMatch(/"password(_hash)?"|\$argon2|session_token|csrf/i);
});

test('admin creates and verifies a database backup', async ({ page }) => {
  await page.goto('/admin/backups');
  const backups = page.getByRole('list', { name: 'Backups' });
  const manual = backups.getByRole('listitem').filter({ hasText: 'Manual' });
  await expect(manual).toHaveCount(0);
  await page.getByRole('button', { name: 'Back up now' }).click();
  await expect(manual).toHaveCount(1);
  await expect(manual).toContainText(/[1-9]\d* library items/);
  await manual.getByRole('button', { name: /^Verify backup/ }).click();
  await expect(manual).toContainText('Verified: intact and restorable');
  // Download re-proves the admin password and yields the SQLite copy.
  await manual.getByRole('button', { name: /^Download backup from/ }).click();
  await page.getByRole('textbox', { name: 'Your password' }).fill(OWNER.password);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download backup', exact: true }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^lumina-.*-manual\.db$/);
  expect((await readFile((await file.path())!)).subarray(0, 15).toString()).toBe('SQLite format 3');
});
