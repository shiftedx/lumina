import { expect, inviteMember, libraryItemId, OWNER, test } from './realstack';

test('invited member is isolated from private items, keeps own search history, and is signed out on deactivation', async ({ page, browser }) => {
  const { context, member, problems } = await inviteMember(page, browser, 'rowan', 'Quiet-meadow-2026');
  const privateId = await libraryItemId(page, 'Realstack Direct');

  await test.step('member cannot see the owner’s private import', async () => {
    expect((await member.request.get(`/api/library/${privateId}`)).status()).toBe(404);
    expect((await member.request.get(`/api/library/${privateId}/media`)).status()).toBe(404);
    await member.goto(`/watch/library/${privateId}`);
    await expect(member.getByText('That Library item is unavailable.')).toBeVisible();
    await member.goto('/library');
    await expect(member.getByText('Your library is empty')).toBeVisible();
    await expect(member.getByRole('button', { name: /^Realstack Direct,/ })).toHaveCount(0);
  });

  await test.step('search history is per member', async () => {
    await page.getByRole('button', { name: /^Search Lumina, / }).click();
    await page.getByRole('combobox', { name: 'Search Lumina' }).fill('realstack lantern');
    await page.keyboard.press('ControlOrMeta+Enter');
    await expect.poll(async () => (await (await page.request.get('/api/search/history')).json()).map((entry: { query: string }) => entry.query)).toEqual(['realstack lantern']);
    await page.goto('/settings/privacy');
    await expect(page.locator('[data-setting-id="privacy.search-history"]').getByRole('button', { name: 'Clear search history' })).toBeVisible();
    await page.getByRole('button', { name: /^Search Lumina, / }).click();
    await expect(page.getByRole('option', { name: /realstack lantern/ })).toBeVisible();
    await member.goto('/settings/privacy');
    await expect(member.locator('[data-setting-id="privacy.search-history"]')).toContainText('No search history yet.');
    expect(await (await member.request.get('/api/search/history')).json()).toEqual([]);
  });

  await test.step('deactivating the member revokes their session', async () => {
    await page.goto('/admin/members');
    await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /Rowan/ }).click();  // 2.8: account actions live on the member page
    await page.getByRole('button', { name: 'Deactivate' }).click();
    await page.getByRole('dialog', { name: /Deactivate Rowan\?/ }).getByRole('button', { name: 'Deactivate Rowan' }).click();
    await expect(page.getByRole('button', { name: 'Reactivate' })).toBeVisible();
    expect((await member.request.get('/api/session/me')).status()).toBe(401);
    await member.reload();
    await expect(member.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
  });

  expect(problems, 'member page errors or server 5xx').toEqual([]);
  await context.close();
});

test('Home layout: the owner reorders and hides shelves, the layout holds in a new browser, another member keeps the default', async ({ page, browser }) => {
  await page.goto('/');
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/settings/me' && response.request().method() === 'PUT'
    && JSON.stringify(response.request().postDataJSON()).includes('"home_shelves":[{'));
  await page.getByRole('button', { name: 'Edit home' }).click();
  await page.getByRole('button', { name: 'Move Recently added music up' }).click();
  await page.getByRole('switch', { name: 'Show Next up' }).click();
  await page.locator('.h-editor').getByRole('button', { name: 'Done' }).click();
  expect((await saved).ok()).toBe(true);

  const fresh = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const again = await fresh.newPage();
  await again.goto('/');
  await again.getByLabel('Username').fill(OWNER.username);
  await again.getByLabel('Password', { exact: true }).fill(OWNER.password);
  await again.getByRole('button', { name: 'Sign in', exact: true }).click();
  await again.getByRole('button', { name: 'Edit home' }).click();
  const rows = again.locator('.h-edit-row');
  await expect(rows.nth(11)).toHaveAttribute('data-shelf-row', 'recent_music');
  await expect(again.getByRole('switch', { name: 'Show Next up' })).toHaveAttribute('aria-checked', 'false');
  await fresh.close();

  const { context, member } = await inviteMember(page, browser, 'juniper', 'Quiet-harbour-2026');
  await member.goto('/');
  await member.getByRole('button', { name: 'Edit home' }).click();
  await expect(member.locator('.h-edit-row').last()).toHaveAttribute('data-shelf-row', 'recent_music');
  await expect(member.getByRole('switch', { name: 'Show Next up' })).toHaveAttribute('aria-checked', 'true');
  await context.close();

  // Leave the shared realstack owner on the default layout for the journeys after this one.
  await page.goto('/');
  await page.getByRole('button', { name: 'Edit home' }).click();
  await page.getByRole('button', { name: 'Reset to default' }).click();
  await page.locator('.h-editor').getByRole('button', { name: 'Done' }).click();
});
