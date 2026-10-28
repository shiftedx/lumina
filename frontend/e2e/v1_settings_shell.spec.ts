import { chooseSelect } from './select-helper';
import { auditA11y } from './release-evidence';
import { navigateApp } from './lumina-mock';
import { expect, test } from '@playwright/test';
import { categoryForPage, resolveSettingsPage } from '../src/features/settings/navigation';
import { type SettingsSectionId } from '../src/app/routes';
import { mockAdminFixtures, mockApi, openSettingsArea, signIn, user } from './lumina-mock';

test('Settings opens controls immediately with every personal and household area directly available', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/'); await signIn(page); await navigateApp(page, 'My Settings');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings', exact: true })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(nav.getByRole('link')).toHaveCount(13);
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeVisible();
  await openSettingsArea(page, 'Household');
  await expect(page.getByRole('list', { name: 'Household members' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Invite someone', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Invitations', exact: true }).click();
  await expect(page.locator('#settings-group-invitations')).toBeFocused();
  await expect(page.getByRole('list', { name: 'Household members' })).toBeVisible();
  await page.reload();
  await expect(nav.getByRole('link', { name: 'Household', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('every old admin link still opens its category in Manage Server', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/'); await signIn(page);
  for (const [from, section] of [['/admin', 'overview'], ['/admin/members', 'members'], ['/admin/tasks', 'tasks'], ['/admin/storage', 'library'], ['/admin/imports', 'library'], ['/admin/media', 'media'], ['/admin/ai', 'ai'], ['/admin/backups', 'backups'], ['/admin/diagnostics', 'diagnostics']] as const) {
    await page.goto(from);
    await expect(page).toHaveURL(new RegExp(`/settings/${section}${from === '/admin/imports' ? '\\?page=imports' : ''}$`));
    await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: categoryForPage(resolveSettingsPage(section as SettingsSectionId)).label, exact: true })).toBeVisible();
  }
});

test('phone settings keeps direct navigation and every playback control on one page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings'); await signIn(page);
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(page.getByRole('navigation', { name: 'Settings sections' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeVisible();
  await page.getByRole('link', { name: 'Subtitles', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Captions', exact: true })).toBeVisible();
  await expect(page.getByRole('switch', { name: 'Autoplay next video' })).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: 'Settings page' })).toHaveCount(0);
  await openSettingsArea(page, 'Library & storage');
  await expect(page.getByRole('button', { name: 'Add root', exact: true })).toBeVisible();
});

test('resizing settings keeps the active category visible without losing the current form', async ({ page }) => {
  await page.setViewportSize({ width: 951, height: 844 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/library?page=folders#settings-group-folders'); await signIn(page);
  const rail = page.getByRole('navigation', { name: 'Settings sections' });
  const active = rail.getByRole('link', { name: 'Library & storage', exact: true });
  await expect(active).toHaveAttribute('aria-current', 'page');
  const path = page.getByRole('textbox', { name: 'Container path', exact: true });
  await path.fill('/media/resize-proof'); await path.focus();
  await page.evaluate(() => window.scrollTo(0, 400));
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
  await page.evaluate(() => {
    const testWindow = window as Window & { settingsRailScrollIntoViewCalls?: number };
    testWindow.settingsRailScrollIntoViewCalls = 0;
    const native = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = function (arg) {
      testWindow.settingsRailScrollIntoViewCalls = (testWindow.settingsRailScrollIntoViewCalls ?? 0) + 1;
      return native.call(this, arg);
    };
  });
  for (const width of [720, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(async () => {
      const [railBox, activeBox] = await Promise.all([rail.boundingBox(), active.boundingBox()]);
      return Boolean(railBox && activeBox && activeBox.x >= railBox.x + 16 && activeBox.x + activeBox.width <= railBox.x + railBox.width - 16);
    }).toBe(true);
    await expect(path).toHaveValue('/media/resize-proof');
    await expect(path).toBeFocused();
    expect(await page.evaluate(() => (window as Window & { settingsRailScrollIntoViewCalls?: number }).settingsRailScrollIntoViewCalls ?? 0)).toBe(0);
    expect(await page.evaluate(() => scrollY)).toBeGreaterThan(0);
    await expect(page).toHaveURL(/\/settings\/library\?page=folders#settings-group-folders$/);
  }
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
  test(`direct settings controls save and update their visuals at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const saved = await mockApi(page, {}); await mockAdminFixtures(page);
    await page.goto('/settings'); await signIn(page);
    expect((await auditA11y(page)).violations).toEqual([]);
    const quality = page.getByRole('combobox', { name: 'Maximum quality' });
    await chooseSelect(page, quality, 'Up to 720p');
    await expect(page.locator('.settings-preview-quality')).toHaveText('720p maximum');
    await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.playback_max_height === 720)).toBe(true);
    const nav = page.getByRole('navigation', { name: 'Settings sections' });
    await openSettingsArea(page, 'Display');
    await page.getByRole('radio', { name: 'Dark' }).check();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'dark')).toBe(true);
    expect((await auditA11y(page)).violations).toEqual([]);
    await openSettingsArea(page, 'Library & storage');
    await expect(page.getByRole('button', { name: 'Add root', exact: true })).toBeVisible();
    await expect(page.getByRole('progressbar', { name: /Family NAS.*space used/ })).toBeVisible();
  });
}

test('drafts survive in-page jumps and declined area, navigation and browser Back changes', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false }); await mockAdminFixtures(page);
  await page.goto('/settings/members'); await signIn(page);
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Maintenance' }).click();
  await page.getByRole('link', { name: 'Video conversion', exact: true }).click();
  const cache = page.getByRole('spinbutton', { name: 'Conversion cache (GB)' }); await cache.fill('25');
  await page.getByRole('link', { name: 'Backups', exact: true }).click();
  await expect(cache).toHaveValue('25');
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  for (const action of [() => nav.getByRole('link', { name: 'Playback', exact: true }).click(), () => navigateApp(page, 'Home'), () => page.goBack()]) {
    page.once('dialog', (dialog) => dialog.dismiss()); await action();
    await expect(page).toHaveURL(/\/manage\/backups\?page=backups$/); await expect(cache).toHaveValue('25');
  }
  page.once('dialog', (dialog) => dialog.accept()); await openSettingsArea(page, 'Playback');
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeVisible();
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
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeVisible();
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
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeFocused();
  await navigateApp(page, 'My Settings');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeFocused();
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
