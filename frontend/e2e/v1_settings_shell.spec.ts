import { expect, test } from '@playwright/test';
import { mockAdminFixtures, mockApi, signIn, user } from './lumina-mock';

/** One Settings with a sidebar, deep links, and old /admin links. */

test('the Settings entry opens Settings, and each section has its own address', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/');
  await signIn(page);
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('region', { name: 'All settings' })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await nav.getByRole('link', { name: 'Members' }).click();
  await expect(page).toHaveURL(/\/settings\/members$/);
  await page.reload();
  await expect(nav.getByRole('link', { name: 'Members' })).toHaveAttribute('aria-current', 'page');
  await page.goBack();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('region', { name: 'All settings' })).toBeVisible();
});

const REDIRECTS: Array<[string, string, string]> = [
  ['/admin', 'overview', 'Dashboard'], ['/admin/members', 'members', 'Members'], ['/admin/tasks', 'tasks', 'Tasks'],
  ['/admin/storage', 'library', 'Library & storage'], ['/admin/imports', 'library', 'Library & storage'], ['/admin/media', 'media', 'Media server'],
  ['/admin/ai', 'ai', 'AI & models'], ['/admin/backups', 'backups', 'Backups'], ['/admin/diagnostics', 'diagnostics', 'Diagnostics'], ['/admin/nope', 'overview', 'Dashboard'],
];

test('every old /admin link lands on its Settings section (success criterion 9)', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/');
  await signIn(page);
  for (const [from, section, heading] of REDIRECTS) {
    await page.goto(from);
    await expect(page, from).toHaveURL(new RegExp(`/settings/${section}$`));
    await expect(page.getByRole('heading', { level: 2, name: heading, exact: true }), from).toBeVisible();
  }
});

test('on a phone, Settings is a list first; a section opens on its own and Back returns', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/settings');
  await signIn(page);
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(nav.getByRole('link', { name: 'Playback', exact: true })).toBeVisible();
  await expect(page.locator('.g-settings-pane')).toBeHidden();
  await nav.getByRole('link', { name: 'Playback', exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/playback$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Playback', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Playback', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: 'All settings' })).toBeVisible(); // the sidebar (and its search) is the list view on a phone
  await page.goBack();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(nav.getByRole('link', { name: 'Display' })).toBeVisible();
  await nav.getByRole('link', { name: 'Display' }).click();
  await page.getByRole('button', { name: 'All settings' }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(nav.getByRole('link', { name: 'Display' })).toBeVisible();
});

test('search finds a setting by what it does and changes it in place (success criterion 7)', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  const saved = await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/settings');
  await signIn(page);
  const box = page.getByRole('searchbox', { name: 'Search settings' });
  await box.fill('dark');
  const results = page.getByRole('region', { name: 'Search results' });
  await expect(results.getByRole('heading', { level: 2, name: 'Display' })).toBeVisible();
  await results.getByRole('radio', { name: 'Dark' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'dark')).toBe(true);
  await box.fill('zzzz');
  await expect(page.getByText('No settings match')).toBeVisible();
});

test('unsaved Transcoding edits are confirmed before leaving by sidebar, primary navigation or Back', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, settings_advanced: true });
  await mockAdminFixtures(page);
  await page.goto('/');
  await signIn(page);
  await page.goto('/settings/members');
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await nav.getByRole('link', { name: 'Transcoding' }).click();
  const cache = page.getByRole('spinbutton', { name: 'Conversion cache (GB)' });
  await cache.fill('25');

  page.once('dialog', (dialog) => dialog.dismiss());
  await nav.getByRole('link', { name: 'Backups' }).click();
  await expect(page).toHaveURL(/\/settings\/transcoding$/);
  await expect(cache).toHaveValue('25');

  page.once('dialog', (dialog) => dialog.dismiss());
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Home' }).click();
  await expect(page).toHaveURL(/\/settings\/transcoding$/);

  page.once('dialog', (dialog) => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(/\/settings\/transcoding$/);
  await expect(cache).toHaveValue('25');

  page.once('dialog', (dialog) => dialog.accept());
  await page.goBack();
  await expect(page).toHaveURL(/\/settings\/members$/);
});

test('a keyboard alone deactivates a member: Enter opens their page, Deactivate opens a dialog with Cancel focused, Esc returns', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  await page.route('**/api/admin/users', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify([user, { ...user, id: 'm2', username: 'sam', display_name: 'Sam', role: 'viewer' }]) }));
  await page.goto('/');
  await signIn(page);
  await page.goto('/settings/members');
  await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /Sam/ }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 2, name: 'Sam' })).toBeFocused();
  const deactivate = page.getByRole('button', { name: 'Deactivate' });
  await deactivate.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Deactivate Sam?' });
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(deactivate).toBeFocused();
});

test('/settings/discovery shows Home & discovery with its three rows and the nav link current', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/settings/discovery');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 2, name: 'Home & discovery', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Home & discovery' })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.g-settings-pane [data-setting-id]')).toHaveCount(3);
});

test('Settings uses the full width: a card home and one label | control column at 1536 and 1920', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  await page.goto('/');
  await signIn(page);
  for (const width of [1536, 1920]) {
    await page.setViewportSize({ width, height: 960 });
    await page.goto('/settings');
    const home = page.getByRole('region', { name: 'All settings' });
    await expect(home.getByRole('heading', { level: 2 })).toHaveText(['You', 'Server', 'Advanced']);
    const pane = (await page.locator('.g-settings-pane').boundingBox())!;
    expect(width - (pane.x + pane.width), 'pane reaches the gutter').toBeLessThanOrEqual(49);
    const tops = await home.locator('.g-settings-card').evaluateAll((cards) => cards.slice(0, 8).map((card) => Math.round(card.getBoundingClientRect().top)));
    expect(tops.filter((top) => top === tops[0]).length, 'cards per row').toBeGreaterThanOrEqual(width >= 1920 ? 4 : 3);
    await page.screenshot({ path: `../output/settings-ia/home-${width}.png`, fullPage: true });
    await page.goto('/settings/playback');
    const quality = (await page.getByRole('combobox', { name: 'Maximum quality' }).boundingBox())!;
    const box = (await page.locator('.g-settings-pane').boundingBox())!;
    // One control column: fields fill it, and every switch ends at the same right edge.
    expect(Math.abs(quality.x + quality.width - (box.x + box.width)), 'the field ends at the column edge').toBeLessThanOrEqual(1);
    expect(quality.width, 'a field is the column width, not the full row').toBeLessThanOrEqual(321);
    const ends = await page.locator('.g-settings-pane .g-switch-track').evaluateAll((tracks) => tracks.map((track) => Math.round(track.getBoundingClientRect().right)));
    expect(new Set(ends).size, 'every switch ends at the same x').toBe(1);
    await page.screenshot({ path: `../output/settings-ia/playback-${width}.png`, fullPage: true });
  }
});

test('on a phone the Settings home is the grouped list, then a section, with no card grid', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto('/settings');
  await signIn(page);
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(nav.getByRole('link', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'All settings' })).toBeHidden();
  await page.screenshot({ path: '../output/settings-ia/home-390.png', fullPage: true });
  await nav.getByRole('link', { name: 'Playback', exact: true }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Playback', exact: true })).toBeVisible();
  await page.screenshot({ path: '../output/settings-ia/playback-390.png', fullPage: true });
});
