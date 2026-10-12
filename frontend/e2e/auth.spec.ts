import { expect, test } from '@playwright/test';
import { mockApi } from './lumina-mock';

test('login renders its title, fields and primary button', async ({ page }) => {
  await mockApi(page, {});
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
  await expect(page.getByText('Sign in with your Lumina account.')).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
});

test('setup renders when the vault has no owner yet', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/bootstrap/status', (route) => route.fulfill({ json: { needs_setup: true } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Make Lumina yours' })).toBeVisible();
  await expect(page.getByLabel('Display name')).toBeVisible();
  await expect(page.getByText('At least 12 characters.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create household vault' })).toBeVisible();
});

test('an invite link renders the join form', async ({ page }) => {
  await mockApi(page, {});
  await page.goto('/#invite=synthetic-token');
  await expect(page.getByRole('heading', { name: 'Join this household' })).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Display name')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Join household' })).toBeVisible();
});

test('a reset link renders the new-password form', async ({ page }) => {
  await mockApi(page, {});
  await page.goto('/#reset=synthetic-token');
  await expect(page.getByRole('heading', { name: 'Choose a new password' })).toBeVisible();
  await expect(page.getByLabel('New password', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Set new password' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Back to sign in' })).toBeVisible();
});

test('a rate-limited sign-in says to wait a minute', async ({ page }) => {
  await mockApi(page, {});
  await page.route('**/api/session/login', (route) => route.fulfill({ status: 429, json: { detail: 'rate_limited' } }));
  await page.goto('/');
  await page.getByLabel('Username').fill('sam');
  await page.getByLabel('Password', { exact: true }).fill('synthetic-passphrase');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByText('Too many attempts. Try again in a minute.')).toBeVisible();
});

test('at phone width the left column is hidden and the bar brand shows', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {});
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
  await expect(page.locator('.g-auth-side')).toBeHidden();
  await expect(page.locator('.g-auth-bar')).toBeVisible();
});

// A 1x1 PNG: the showcase art route only has to answer with a decodable image.
const PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkqOcHAAGmAQs+B90oAAAAAElFTkSuQmCC', 'base64');
async function mockShowcase(page: import('@playwright/test').Page) {
  await page.route('**/api/public/showcase', (route) => route.fulfill({ json: { slides: [
    { backdrop_url: '/api/public/showcase/art/a', title: 'Dune: Part Three', caption: 'In cinemas Friday', kind: 'movie' },
    { backdrop_url: '/api/public/showcase/art/b', title: 'Frieren', caption: 'New this season', kind: 'anime' },
  ] } }));
  await page.route('**/api/public/showcase/art/*', (route) => route.fulfill({ contentType: 'image/png', body: PIXEL }));
}

test('release art fills the page behind the sign-in panel and never blocks signing in', async ({ page }) => {
  await mockApi(page, {});
  await mockShowcase(page);
  await page.goto('/');
  await expect(page.getByText('In cinemas Friday')).toBeVisible();
  await expect(page.locator('.g-auth.has-art .g-show-art img')).toHaveCount(1);
  const pips = page.getByRole('group', { name: 'Featured releases' }).getByRole('button');
  await expect(pips).toHaveCount(2);
  await pips.nth(1).click();
  await expect(page.getByText('New this season')).toBeVisible();
  await page.getByRole('button', { name: 'Pause the showcase' }).click();
  await expect(page.getByRole('button', { name: 'Play the showcase' })).toBeVisible();
  await page.getByLabel('Username').fill('sam');
  await page.getByLabel('Password', { exact: true }).fill('synthetic-passphrase');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('.g-auth')).toHaveCount(0);
});

test('at phone width the release art sits behind a full-width form with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {});
  await mockShowcase(page);
  await page.goto('/');
  await expect(page.getByText('In cinemas Friday')).toBeVisible();
  await expect(page.locator('.g-auth-bar')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const form = await page.locator('.g-auth-main').boundingBox();
  expect(form?.width).toBe(390);
});
