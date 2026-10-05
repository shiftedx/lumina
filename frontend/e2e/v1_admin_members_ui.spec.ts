import { expect, test } from '@playwright/test';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

/** Members list, member page and invitations against mocked admin fixtures. */

test('members list opens a member page whose edits save in one PUT', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  let saved: unknown = null;
  await page.route('**/api/admin/members/m2/access', (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    saved = route.request().postDataJSON();
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.goto('/admin/members');
  await signIn(page);
  const list = page.getByRole('list', { name: 'Household members' });
  const eleanor = list.getByRole('link', { name: /Eleanor/ });
  await expect(eleanor).toContainText('Movies, Anime · G · TV-Y7');
  await expect(eleanor).toContainText('1 h 05 min today');
  await expect(list.getByRole('link', { name: /Alexandria/ })).toContainText('Owner — no limits');
  await eleanor.click();
  await expect(page).toHaveURL(/\/settings\/members\/m2$/);
  await expect(page.getByRole('heading', { level: 2, name: /Eleanor/ })).toBeFocused();
  await page.getByRole('tab', { name: 'Ratings' }).click();
  await page.getByRole('radio', { name: 'PG', exact: true }).check();
  await page.getByRole('tab', { name: 'Streaming' }).click();
  await expect(page.getByRole('list', { name: 'Followed channels' })).toContainText('Bluey Official');
  await page.getByRole('tab', { name: 'Schedule' }).click();
  await page.getByRole('combobox', { name: 'Daily limit' }).selectOption('90');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  expect(saved).toMatchObject({ movie_rating_max: 'PG', daily_limit_minutes: 90, sections: ['movies', 'anime'] });
  await page.getByRole('button', { name: 'Members', exact: true }).first().click();
  await expect(page).toHaveURL(/\/settings\/members$/);
  expect(errors).toEqual([]);
});

test('invite dialog sends, and shows the link when email is not set up', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  await page.route('**/api/admin/invites', (route) => (route.request().method() === 'POST'
    ? route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'iv2', email: 'ana@example.com', status: 'pending', created_at: '2026-10-04T09:00:00Z', expires_at: '2026-10-11T09:00:00Z', sent_at: null, invitation_url: 'http://127.0.0.1:4174/#invite=one-time', email_sent: false }) })
    : route.fallback()));
  await page.goto('/settings/members');
  await signIn(page);
  await expect(page.getByRole('list', { name: 'Waiting invitations' })).toContainText('Grandad');
  await page.getByRole('button', { name: 'Invite someone' }).click();
  const dialog = page.getByRole('dialog', { name: 'Invite someone' });
  await dialog.getByLabel('Email').fill('ana@example.com');
  await dialog.getByRole('checkbox', { name: 'Movies', exact: true }).check();
  await expect(dialog.getByRole('button', { name: 'Set the public address' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Send email' }).click();
  const ready = page.getByRole('dialog', { name: 'Invitation ready' });
  await expect(ready).toContainText('Email isn’t set up');
  await expect(ready.getByLabel('Invite link')).toHaveValue(/#invite=one-time$/);
});
