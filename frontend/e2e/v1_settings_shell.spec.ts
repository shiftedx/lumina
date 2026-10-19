import { chooseSelect } from './select-helper';
import { auditA11y } from './release-evidence';
import { navigateApp } from './lumina-mock';
import { expect, test } from '@playwright/test';
import { categoryForPage, resolveSettingsPage } from '../src/features/settings/navigation';
import { type SettingsSectionId } from '../src/app/routes';
import { mockAdminFixtures, mockApi, signIn, user } from './lumina-mock';

test('Settings and Manage Server open separate spaces, with bookmarkable subpages and Back', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/'); await signIn(page);
  await navigateApp(page, 'My Settings');
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('heading', { level: 1, name: 'My Settings' })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(nav.getByRole('link')).toHaveCount(7);
  await expect(nav.getByRole('link', { name: 'Household' })).toHaveCount(0);
  await page.getByRole('navigation', { name: 'Settings spaces' }).getByRole('link', { name: 'Manage Server' }).click();
  await expect(page).toHaveURL(/\/manage$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeVisible();
  await nav.getByRole('link', { name: 'Household' }).click();
  await expect(page).toHaveURL(/\/manage\/members\?page=members$/);
  await page.getByRole('link', { name: 'Invitations', exact: true }).click();
  await expect(page).toHaveURL(/page=invitations$/);
  await page.reload(); await expect(page.getByRole('link', { name: 'Invitations', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.goBack(); await expect(page.getByRole('link', { name: 'Members', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('every old admin link still opens its category in Manage Server', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/'); await signIn(page);
  for (const [from, section] of [['/admin', 'overview'], ['/admin/members', 'members'], ['/admin/tasks', 'tasks'], ['/admin/storage', 'library'], ['/admin/imports', 'library'], ['/admin/media', 'media'], ['/admin/ai', 'ai'], ['/admin/backups', 'backups'], ['/admin/diagnostics', 'diagnostics']] as const) {
    await page.goto(from);
    await expect(page).toHaveURL(new RegExp(`/settings/${section}${from === '/admin/imports' ? '\\?page=imports' : ''}$`));
    await expect(page.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: categoryForPage(resolveSettingsPage(section as SettingsSectionId)).label, exact: true })).toBeVisible();
  }
});

test('phone Settings is list then detail, with persistent search and a labelled subpage picker', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false }); await page.goto('/settings'); await signIn(page);
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(page.locator('.g-settings-pane')).toBeHidden();
  await nav.getByRole('link', { name: 'Playback', exact: true }).click();
  await expect(nav).toBeHidden();
  await expect(page.getByRole('searchbox', { name: 'Search settings' })).toBeVisible();
  await chooseSelect(page, page.getByRole('combobox', { name: 'Settings page' }), 'Subtitles');
  await expect(page.getByRole('heading', { name: 'Captions', exact: true })).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Autoplay next video' })).toHaveCount(0);
  await page.getByRole('button', { name: 'All settings' }).click(); await expect(page).toHaveURL(/\/settings$/);
  await expect(nav).toBeVisible();
});

test('search finds a destination and saves the real setting on its own page', async ({ page }) => {
  const saved = await mockApi(page, { sidebar_collapsed: false }); await page.goto('/settings'); await signIn(page);
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('dark');
  const results = page.getByRole('region', { name: 'Search results' });
  await expect(results.getByRole('radio')).toHaveCount(0);
  await page.getByRole('searchbox', { name: 'Search settings' }).press('ArrowDown');
  await expect(results.getByRole('link', { name: /Theme/ })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/settings\/appearance\?page=display$/);
  await page.getByRole('radio', { name: 'Dark' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'dark')).toBe(true);
});

for (const width of [390, 1440]) {
  test(`quick changes go straight to a field and save at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const saved = await mockApi(page, {}); await mockAdminFixtures(page);
    await page.goto('/settings'); await signIn(page);
    await expect(page.getByRole('heading', { name: 'Browse settings' })).toBeVisible();
    expect((await auditA11y(page)).violations).toEqual([]);
    await page.getByRole('link', { name: 'Set streaming quality' }).click();
    const quality = page.getByRole('combobox', { name: 'Maximum quality' });
    await expect(quality).toBeFocused();
    await chooseSelect(page, quality, 'Up to 720p');
    await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.playback_max_height === 720)).toBe(true);
    await page.getByRole('button', { name: 'All settings' }).click();
    await expect(page.getByRole('heading', { name: /Quick changes/ })).toBeFocused();
    await page.getByRole('link', { name: 'Change appearance' }).click();
    await page.getByRole('radio', { name: 'Dark' }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'dark')).toBe(true);
    expect((await auditA11y(page)).violations).toEqual([]);
    await page.getByRole('button', { name: 'All settings' }).click();
    expect((await auditA11y(page)).violations).toEqual([]);
    await page.getByRole('navigation', { name: 'Settings spaces' }).getByRole('link', { name: 'Manage Server' }).click();
    await page.getByRole('link', { name: 'Manage media folders' }).click();
    await expect(page).toHaveURL(/\/manage\/library\?page=folders$/);
    await expect(page.getByRole('button', { name: 'Add root', exact: true })).toBeVisible();
  });
}

test('drafts survive declined subpage, space, navigation and browser Back changes', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/settings/members'); await signIn(page);
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Maintenance' }).click();
  await page.getByRole('link', { name: 'Video conversion', exact: true }).click();
  const cache = page.getByRole('spinbutton', { name: 'Conversion cache (GB)' }); await cache.fill('25');
  for (const action of [() => page.getByRole('link', { name: 'Backups', exact: true }).click(), () => page.getByRole('navigation', { name: 'Settings spaces' }).getByRole('link', { name: 'My Settings' }).click(), () => navigateApp(page, 'Home'), () => page.goBack()]) {
    page.once('dialog', (dialog) => dialog.dismiss()); await action();
    await expect(page).toHaveURL(/\/manage\/transcoding\?page=transcoding$/); await expect(cache).toHaveValue('25');
  }
  page.once('dialog', (dialog) => dialog.accept()); await page.goBack(); await expect(page).toHaveURL(/\/manage\/backups\?page=backups$/);
});

test('keyboard member deactivation focuses Cancel and Escape restores the trigger', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.route('**/api/admin/users', (route) => route.fulfill({ json: [user, { ...user, id: 'm2', username: 'sam', display_name: 'Sam', role: 'viewer' }] }));
  await page.goto('/settings/members'); await signIn(page);
  await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /Sam/ }).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 2, name: 'Sam' })).toBeFocused();
  const deactivate = page.getByRole('button', { name: 'Deactivate' }); await deactivate.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Deactivate Sam?' }).getByRole('button', { name: 'Cancel' })).toBeFocused();
  await page.keyboard.press('Escape'); await expect(deactivate).toBeFocused();
});

test('phone confirmation actions follow their visual order when tabbing', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.route('**/api/admin/users', (route) => route.fulfill({ json: [user, { ...user, id: 'm2', username: 'sam', display_name: 'Sam', role: 'viewer' }] }));
  await page.goto('/settings/members'); await signIn(page);
  await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /Sam/ }).click();
  await page.getByRole('button', { name: 'Deactivate', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Deactivate Sam?' });
  const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
  const confirm = dialog.getByRole('button', { name: 'Deactivate Sam', exact: true });
  await expect(cancel).toBeFocused();
  const [first, second] = await Promise.all([cancel.boundingBox(), confirm.boundingBox()]);
  expect(first!.y + first!.height).toBeLessThanOrEqual(second!.y);
  await page.keyboard.press('Tab');
  await expect(confirm).toBeFocused();
  await page.keyboard.press('Escape');
});


test('invitations open the public address on its new Connections page', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/manage/members?page=invitations'); await signIn(page);
  await page.getByRole('button', { name: 'Invite someone', exact: true }).click();
  await page.getByRole('button', { name: 'Set the public address', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Invite someone' })).toHaveCount(0);
  await expect(page).toHaveURL(/\/manage\/members\?page=addresses$/);
  await expect(page.getByRole('textbox', { name: 'Public address', exact: true })).toBeFocused();
});

test('navigation overlays the full-width page, stays beside its trigger, dismisses outside, and opens both settings spaces', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/settings/playback'); await signIn(page);
  const main = page.getByRole('main');
  const before = await main.boundingBox();
  const trigger = page.getByRole('button', { name: 'Open navigation' });
  const modal = page.getByRole('dialog', { name: 'Navigation' });
  await trigger.click();
  expect((await auditA11y(page)).violations).toEqual([]);
  const bounds = await modal.boundingBox();
  const anchor = await trigger.boundingBox();
  expect(bounds!.x).toBeCloseTo(anchor!.x, 0);
  expect(bounds!.y).toBeCloseTo(anchor!.y + anchor!.height + 4, 0);
  expect(bounds!.width).toBeLessThanOrEqual(280);
  await expect(modal).not.toHaveAttribute('aria-modal', 'true');
  // The anchored panel preserves the full content width.
  expect(await page.locator('#main-content').boundingBox()).toEqual(before);
  await page.mouse.click(12, 120);
  await expect(modal).toHaveCount(0);
  await trigger.click();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await navigateApp(page, 'Manage Server');
  await expect(page.getByRole('heading', { level: 1, name: 'Manage Server' })).toBeFocused();
  await navigateApp(page, 'My Settings');
  await expect(page.getByRole('heading', { level: 1, name: 'My Settings' })).toBeFocused();
  await expect(main).toHaveCSS('margin-left', '0px');
});


test('settings search keeps placeholder and entered text comfortably inside its field', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings'); await signIn(page);
  for (const width of [1536, 975, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const search = page.getByRole('searchbox', { name: 'Search settings' });
    await expect(search).toBeVisible();
    await expect(search).toHaveCSS('padding-left', '12px');
    await expect(search).toHaveCSS('padding-right', '12px');
    const bounds = await search.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await search.fill('quality');
    await expect(page.getByRole('region', { name: 'Search results' })).toBeVisible();
    await search.fill('');
  }
});
