import { expect, test, type Page } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';

/** A display-name edit propagates through the real app shell (mocked API). */

async function openSettings(page: Page) {
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/settings/account');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
}

test('test_profile_display_name_edit: renaming updates the profile everywhere it is shown', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await openSettings(page);

  const nameInput = page.getByLabel('Display name');
  await nameInput.fill('Alexandria the Second');
  await page.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByText('Display name updated.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Alexandria the Second, account menu' })).toBeVisible();
});
