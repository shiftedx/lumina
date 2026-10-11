import { expect, test } from '@playwright/test';
import { mockApi, user } from './lumina-mock';

for (const width of [390, 1440]) {
  test(`onboarding progress clears both headings and channel search fits at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await mockApi(page, {});
    await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
    await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories: [{ key: 'film', label: 'Film' }], selected_keys: [] } }));
    await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => route.fulfill({ json: { categories: [] } }));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Set up your Home' })).toBeVisible();
    const assertProgress = async () => {
      const progress = await page.locator('.g-onboarding-progress').boundingBox();
      const masthead = await page.locator('.g-onboarding-panel .g-masthead').boundingBox();
      expect(progress!.y + progress!.height).toBeLessThan(masthead!.y);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    };
    await assertProgress();
    await page.getByLabel('Film', { exact: true }).check();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Follow a few channels' })).toBeVisible();
    await assertProgress();
    const search = page.getByRole('searchbox');
    await expect(search).toBeVisible();
    if (width === 390) {
      expect((await search.boundingBox())!.width).toBeGreaterThan(300);
      const button = await page.getByRole('button', { name: 'Find channels' }).boundingBox();
      const field = await search.boundingBox();
      expect(button!.y).toBeGreaterThanOrEqual(field!.y + field!.height);
      const back = page.getByRole('button', { name: 'Back', exact: true });
      const next = page.getByRole('button', { name: 'Continue', exact: true });
      const backBox = await back.boundingBox();
      const nextBox = await next.boundingBox();
      expect(backBox!.y + backBox!.height).toBeLessThanOrEqual(nextBox!.y);
      await back.focus();
      await page.keyboard.press('Tab');
      await expect(next).toBeFocused();
    }
  });
}
