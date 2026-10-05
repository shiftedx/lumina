/** Member access (2.8.0): blocked streaming is hidden and a direct address is a calm state; no administrator wording. */
import { expect, test, type Page } from '@playwright/test';

import { mockApi, OPEN_ACCESS, signIn } from './lumina-mock';

const open = OPEN_ACCESS;

async function visit(page: Page, path: string, access: Record<string, unknown>, width = 1440) {
  await page.setViewportSize({ width, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route('**/api/me/access', (route) => route.fulfill({ json: { ...open, ...access } }));
  await page.goto(path);
  await signIn(page);
}

test('all streaming blocked: no Streaming nav, and /streaming is a calm explanation', async ({ page }) => {
  await visit(page, '/streaming', { blocked_streaming: ['youtube', 'twitch', 'kick'] });
  await expect(page.getByRole('status').filter({ hasText: "Streaming isn't available right now" })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' })).toHaveCount(0);
  await expect(page.getByText(/admin|parent/i)).toHaveCount(0);
});

test('phone: the tab bar drops Streaming when everything is blocked', async ({ page }) => {
  await visit(page, '/', { blocked_streaming: ['youtube', 'twitch', 'kick', 'open_search'] }, 390);
  const tabs = page.getByRole('navigation', { name: 'Mobile primary navigation' });
  await expect(tabs.getByRole('button', { name: 'Library' })).toBeVisible();
  await expect(tabs.getByRole('button', { name: 'Streaming' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Add a link' })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});
