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

test('profile name stays a draft until saved, persists through reload, and can be discarded', async ({ page }) => {
  await page.goto('/settings/account?page=profile');
  const profile = page.getByRole('form', { name: 'Profile' });
  const displayName = profile.getByLabel('Display name');
  await expect(displayName).toHaveValue(OWNER.displayName);

  let writes = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/session/me' && request.method() === 'PUT') writes += 1;
  });

  await displayName.fill(`${OWNER.displayName} draft`);
  await expect(profile.getByRole('button', { name: 'Save profile' })).toBeEnabled();
  expect(writes).toBe(0);
  await profile.getByRole('button', { name: 'Discard' }).click();
  await expect(displayName).toHaveValue(OWNER.displayName);
  expect(writes).toBe(0);

  const updatedName = `${OWNER.displayName} realstack`;
  const save = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/session/me' && response.request().method() === 'PUT');
  await displayName.fill(updatedName);
  await profile.getByRole('button', { name: 'Save profile' }).click();
  const saved = await save;
  expect(saved.ok()).toBe(true);
  expect(saved.request().postDataJSON()).toEqual({ display_name: updatedName });
  expect((await saved.json()).user.display_name).toBe(updatedName);
  await expect.poll(async () => (await (await page.request.get('/api/session/me')).json()).user.display_name).toBe(updatedName);
  await page.reload();
  await expect(page.getByRole('form', { name: 'Profile' }).getByLabel('Display name')).toHaveValue(updatedName);

  const restore = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/session/me' && response.request().method() === 'PUT');
  await page.getByRole('form', { name: 'Profile' }).getByLabel('Display name').fill(OWNER.displayName);
  await page.getByRole('form', { name: 'Profile' }).getByRole('button', { name: 'Save profile' }).click();
  expect((await restore).ok()).toBe(true);
  await expect.poll(async () => (await (await page.request.get('/api/session/me')).json()).user.display_name).toBe(OWNER.displayName);
});

test('download limits stay a draft until saved, persist through reload, and can be discarded', async ({ page }) => {
  const initial = await page.request.get('/api/admin/settings');
  expect(initial.ok()).toBe(true);
  const original = await initial.json() as { concurrency: number };
  const updatedConcurrency = original.concurrency === 8 ? 7 : original.concurrency + 1;

  await page.goto('/manage/overview?page=download-limits');
  const advanced = page.locator('#settings-group-download-limits > details.g-settings-advanced');
  if (!(await advanced.evaluate((element) => (element as HTMLDetailsElement).open))) await advanced.locator('summary').click();
  const limits = page.getByRole('form', { name: 'Overview' });
  const concurrency = limits.getByLabel('Simultaneous downloads');
  await expect(concurrency).toHaveValue(String(original.concurrency));

  let writes = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/admin/settings' && request.method() === 'PUT') writes += 1;
  });

  await concurrency.fill(String(updatedConcurrency));
  await expect(limits.getByRole('button', { name: 'Save download limits' })).toBeEnabled();
  expect(writes).toBe(0);
  await limits.getByRole('button', { name: 'Discard' }).click();
  await expect(concurrency).toHaveValue(String(original.concurrency));
  expect(writes).toBe(0);

  const save = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/admin/settings' && response.request().method() === 'PUT');
  await concurrency.fill(String(updatedConcurrency));
  await limits.getByRole('button', { name: 'Save download limits' }).click();
  const saved = await save;
  expect(saved.ok()).toBe(true);
  expect(saved.request().postDataJSON()).toMatchObject({ concurrency: updatedConcurrency });
  await expect.poll(async () => (await (await page.request.get('/api/admin/settings')).json()).concurrency).toBe(updatedConcurrency);
  await page.reload();
  if (!(await advanced.evaluate((element) => (element as HTMLDetailsElement).open))) await advanced.locator('summary').click();
  await expect(page.getByRole('form', { name: 'Overview' }).getByLabel('Simultaneous downloads')).toHaveValue(String(updatedConcurrency));

  const restore = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/admin/settings' && response.request().method() === 'PUT');
  await page.getByRole('form', { name: 'Overview' }).getByLabel('Simultaneous downloads').fill(String(original.concurrency));
  await page.getByRole('form', { name: 'Overview' }).getByRole('button', { name: 'Save download limits' }).click();
  expect((await restore).ok()).toBe(true);
  await expect.poll(async () => (await (await page.request.get('/api/admin/settings')).json()).concurrency).toBe(original.concurrency);
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
