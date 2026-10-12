import { MEDIA_ROOT, OWNER, OWNER_STATE, expect, test } from '../realstack/realstack';

test('owner + fixture import', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username').fill(OWNER.username);
  await page.getByLabel('Display name').fill(OWNER.displayName);
  await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
  await page.getByRole('button', { name: 'Create household vault' }).click();
  await page.getByRole('button', { name: 'Skip for now' }).click();
  await page.goto('/admin/storage');
  await page.getByLabel('Container path').fill(MEDIA_ROOT);
  await page.getByLabel('Label').fill('Fixture media');
  await page.getByLabel('Mode').selectOption({ label: 'External — read-only, for imports' });
  await page.getByRole('button', { name: 'Add root' }).click();
  const root = page.getByRole('group', { name: /Import Fixture media now/ });
  await root.getByRole('button', { name: 'Import now' }).click();
  await expect(page.getByRole('region', { name: 'Fixture media Complete' })).toBeVisible({ timeout: 60_000 });
  await page.context().storageState({ path: OWNER_STATE });
});
