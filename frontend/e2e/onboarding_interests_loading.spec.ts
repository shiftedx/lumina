import { expect, test, type Page, type Route } from '@playwright/test';

import { mockApi, user } from './lumina-mock';

const categories = [
  { key: 'music', label: 'Music' },
  { key: 'film', label: 'Film' },
];

async function pendingOnboarding(page: Page) {
  await mockApi(page, {});
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
  await page.route(/\/api\/discovery\/channels(\?|$)/, (route) => route.fulfill({ json: { categories: [] } }));
}

test('interest loading keeps Continue guarded while Skip remains available', async ({ page }) => {
  await pendingOnboarding(page);
  let pending: Route | undefined;
  await page.route('**/api/discovery/interests', (route) => { pending = route; });

  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize my Home' }).click();
  await expect.poll(() => Boolean(pending)).toBe(true);

  await expect(page.getByText('Loading interests…')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Skip for now' })).toBeEnabled();

  await pending!.fulfill({ json: { categories, selected_keys: ['music'] } });
  await expect(page.getByRole('checkbox', { name: 'Music', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled();
});

test('a failed interest load can retry without turning unknown interests into an empty choice', async ({ page }) => {
  await pendingOnboarding(page);
  let attempts = 0;
  await page.route('**/api/discovery/interests', (route) => {
    attempts++;
    if (attempts === 1) return route.fulfill({ status: 503, json: { detail: 'Temporarily unavailable' } });
    return route.fulfill({ json: { categories, selected_keys: ['film'] } });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Personalize my Home' }).click();

  await expect(page.getByRole('alert')).toContainText('We couldn’t load your interests.');
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Skip for now' })).toBeEnabled();
  await page.getByRole('button', { name: 'Try again' }).click();

  await expect(page.getByRole('checkbox', { name: 'Film', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeEnabled();
  expect(attempts).toBe(2);
});

test('a late initial interest response cannot replace the canonical Skip result', async ({ page }) => {
  await pendingOnboarding(page);
  let pending: Route | undefined;
  await page.route('**/api/discovery/interests', (route) => { pending = route; });
  await page.route('**/api/onboarding/skip', (route) => route.fulfill({ json: { status: 'skipped', selected_keys: [], followed: [] } }));

  await page.goto('/');
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole('button', { name: 'Explore without setup' }).click();

  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Choose interests' })).toBeVisible();

  await pending!.fulfill({ json: { categories, selected_keys: ['music'] } });
  await page.waitForTimeout(100);
  await expect(page.getByRole('button', { name: 'Choose interests' })).toBeVisible();
});
