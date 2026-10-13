import { MEDIA_ROOT, OWNER, OWNER_STATE, expect, test } from '../realstack/realstack';

test('owner + fixture import', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Username').fill(OWNER.username);
  await page.getByLabel('Display name').fill(OWNER.displayName);
  await page.getByLabel('Password', { exact: true }).fill(OWNER.password);
  await page.getByRole('button', { name: 'Create household vault' }).click();
  await page.getByLabel('Mounted path').fill(MEDIA_ROOT);
  await page.getByLabel('Folder name').fill('Fixture media');
  await page.getByRole('button', { name: 'Add and scan' }).click();
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('button', { name: 'Explore without setup' }).click();
  await page.goto('/admin/imports');
  await expect(page.getByRole('region', { name: 'Fixture media Complete' })).toBeVisible({ timeout: 60_000 });
  await page.context().storageState({ path: OWNER_STATE });
});
