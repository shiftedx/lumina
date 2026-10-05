import { expect, test, type Page } from '@playwright/test';
import { mockApi, mockDeviceRing, type Ring } from './lumina-mock';

const RING: Ring = [
  { user_id: 'member-2', display_name: 'Sam Rivers', username: 'sam', role: 'viewer', switch: 'instant', active: false },
  { user_id: 'member-1', display_name: 'Alexandria', username: 'alexandria', role: 'admin', switch: 'password', active: false },
];

test('signed out with a ring: the picker is the first screen; instant tile signs in', async ({ page }) => {
  await mockApi(page, {});
  const calls = await mockDeviceRing(page, RING);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Alexandria, vault owner, needs password' })).toContainText('Vault owner · password');
  await page.getByRole('button', { name: 'Continue as Sam Rivers' }).click();
  await expect.poll(() => calls).toEqual(['switch:member-2']);
});

test('a password tile expands in place, keyboard only', async ({ page }) => {
  await mockApi(page, {});
  await mockDeviceRing(page, RING);
  await page.goto('/');
  const owner = page.getByRole('button', { name: 'Alexandria, vault owner, needs password' });
  await owner.focus();
  await page.keyboard.press('Enter');
  const password = page.getByLabel('Password for Alexandria');
  await expect(password).toBeFocused();
  await page.keyboard.type('synthetic-passphrase');
  const login = page.waitForRequest('**/api/session/login');
  await page.keyboard.press('Enter');
  expect((await login).postDataJSON()).toEqual({ username: 'alexandria', password: 'synthetic-passphrase', remember_on_device: true });
});

test('Someone else opens the sign-in form with remember off', async ({ page }) => {
  await mockApi(page, {});
  await mockDeviceRing(page, RING);
  await page.goto('/');
  await page.getByRole('button', { name: 'Someone else' }).click();
  await expect(page.getByRole('checkbox', { name: "Show me in Who's watching on this device" })).not.toBeChecked();
  await expect(page.getByRole('button', { name: "Choose who's watching" })).toBeVisible();
});

test('no ring: the plain sign-in form, and no ring names anywhere', async ({ page }) => {
  await mockApi(page, {});
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
  await expect(page.getByRole('button', { name: "Choose who's watching" })).toHaveCount(0);
});
