import { expect, test, type Page } from '@playwright/test';
import { mockApi, user } from './lumina-mock';

/** Password sign-in answers a challenge; /api/session/two-factor then accepts only 123456 (or the recovery code). */
async function twoStepApi(page: Page, { expired = false } = {}) {
  await mockApi(page, {});
  let verified = false;
  const calls: unknown[] = [];
  await page.route('**/api/session/login', (route) => route.fulfill({ json: { two_factor_required: true, challenge: 'chal-1' } }));
  await page.route('**/api/session/me', (route) => (verified ? route.fulfill({ json: { user, csrf_token: 'csrf' } }) : route.fulfill({ status: 401, json: { detail: 'not authenticated' } })));
  await page.route('**/api/session/two-factor', (route) => {
    const body = route.request().postDataJSON() as { code?: string; recovery_code?: string };
    calls.push(body);
    if (expired) return route.fulfill({ status: 410, json: { detail: 'two_factor_challenge_expired' } });
    if (body.code === '123456' || body.recovery_code === 'abcd-efgh-jkmn-pqrs') { verified = true; return route.fulfill({ json: { user, csrf_token: 'csrf' } }); }
    return route.fulfill({ status: 401, json: { detail: 'That code did not match.' } });
  });
  return calls;
}

async function password(page: Page) {
  await page.goto('/');
  await page.getByLabel('Username').fill('alexandria');
  await page.getByLabel('Password', { exact: true }).fill('synthetic-passphrase');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Two-step verification' })).toBeVisible();
}

test('a wrong code is announced, then the right one signs in', async ({ page }) => {
  const calls = await twoStepApi(page);
  await password(page);
  await page.getByLabel('Code').fill('000000');
  await expect(page.getByRole('alert')).toContainText('That code did not match.');
  await expect(page.getByLabel('Code')).toBeFocused();
  await page.getByLabel('Code').fill('123456');
  await expect(page.getByRole('heading', { name: 'Two-step verification' })).toBeHidden();
  expect(calls.at(-1)).toEqual({ challenge: 'chal-1', code: '123456', trust_device: false });
});

test('a recovery code signs in', async ({ page }) => {
  const calls = await twoStepApi(page);
  await password(page);
  await page.getByRole('button', { name: 'Use a recovery code instead' }).click();
  await page.getByLabel('Recovery code').fill('abcd-efgh-jkmn-pqrs');
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page.getByRole('heading', { name: 'Two-step verification' })).toBeHidden();
  expect(calls.at(-1)).toEqual({ challenge: 'chal-1', recovery_code: 'abcd-efgh-jkmn-pqrs', trust_device: false });
});

test('an expired sign-in returns to the password form with a message', async ({ page }) => {
  await twoStepApi(page, { expired: true });
  await password(page);
  await page.getByLabel('Code').fill('123456');
  await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
  await expect(page.getByText('Your sign-in timed out. Enter your password again.')).toBeVisible();
});
