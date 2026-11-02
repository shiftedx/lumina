import { chooseSelect } from './select-helper';
import { auditA11y } from './release-evidence';
import { navigateApp } from './lumina-mock';
import { expect, test } from '@playwright/test';
import { categoryForPage, resolveSettingsPage } from '../src/features/settings/navigation';
import { type SettingsSectionId } from '../src/app/routes';
import { mockAdminFixtures, mockApi, openSettingsArea, signIn, user } from './lumina-mock';

test('My settings opens Profile and keeps personal and server navigation distinct', async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/'); await signIn(page); await navigateApp(page, 'My Settings');
  await expect(page.getByRole('heading', { level: 1, name: 'My settings', exact: true })).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  await expect(nav.getByRole('link')).toHaveCount(7);
  await expect(page.getByRole('textbox', { name: 'Display name' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Household', exact: true })).toHaveCount(0);
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
    await expect(page.getByRole('heading', { level: 1, name: 'Manage server' })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: categoryForPage(resolveSettingsPage(section as SettingsSectionId)).label, exact: true })).toBeVisible();
  }
});

test('phone settings keeps direct navigation and every playback control on one page', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/playback'); await signIn(page);
  await expect(page.getByRole('combobox', { name: 'Choose settings section' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Mobile primary navigation' })).toHaveCount(0);
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
    await expect(page.getByRole('combobox', { name: 'Choose settings section' })).toHaveValue('library');
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
    await page.goto('/settings/playback'); await signIn(page);
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
  await openSettingsArea(page, 'Library & storage');
  await page.getByRole('link', { name: 'Video conversion', exact: true }).click();
  await page.locator('#settings-group-transcoding > details.g-settings-advanced > summary').click();
  const cache = page.getByRole('spinbutton', { name: 'Conversion cache (GB)' }); await cache.fill('25');
  await page.getByRole('link', { name: 'Folders', exact: true }).click();
  await expect(cache).toHaveValue('25');
  const nav = page.getByRole('navigation', { name: 'Settings sections' });
  for (const action of [() => openSettingsArea(page, 'Playback'), () => navigateApp(page, 'Home'), () => page.goBack()]) {
    page.once('dialog', (dialog) => dialog.dismiss()); await action();
    await expect(page).toHaveURL(/\/manage\/library\?page=folders$/); await expect(cache).toHaveValue('25');
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
  await expect(page.getByRole('textbox', { name: 'Public address', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Public address', exact: true })).toBeFocused();
});

test('navigation overlays the full-width page, dismisses outside, and opens both settings spaces', async ({ page }) => {
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
  expect(bounds!.x).toBe(0);
  expect(bounds!.y).toBe(0);
  expect(bounds!.width).toBeLessThanOrEqual(280);
  await expect(modal).not.toHaveAttribute('aria-modal', 'true');
  // The anchored panel preserves the full content width.
  expect(await page.locator('#main-content').boundingBox()).toEqual(before);
  await page.mouse.click(500, 120);
  await expect(modal).toHaveCount(0);
  await trigger.click();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await navigateApp(page, 'Manage Server');
  await expect(page.getByRole('heading', { level: 2, name: 'Overview & activity', exact: true })).toBeFocused();
  await navigateApp(page, 'My Settings');
  await expect(page.getByRole('heading', { level: 2, name: 'Profile & security', exact: true })).toBeFocused();
  await expect(main).toHaveCSS('margin-left', '0px');
});


test('settings search keeps placeholder and entered text comfortably inside its field', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings'); await signIn(page);
  for (const width of [1536, 975, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const search = page.getByRole('searchbox', { name: 'Search settings' });
    await expect(search).toBeVisible();
    await expect(search).toHaveCount(1);
    const bounds = await search.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    await search.fill('quality');
    await expect(page.getByRole('region', { name: 'Search results' })).toBeVisible();
    await search.fill('');
  }
});

test('profile errors retain the draft and declined search, category, account and media navigation preserve it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { theme: 'dark' }); await mockAdminFixtures(page);
  let profileWrites = 0; let logouts = 0;
  await page.route('**/api/session/me', async (route) => {
    if (route.request().method() !== 'PUT') return route.fallback();
    profileWrites++;
    return route.fulfill({ status: 422, json: { detail: 'Choose another display name.' } });
  });
  await page.route('**/api/session/logout', async (route) => {
    logouts++; return route.fulfill({ status: 204, body: '' });
  });
  await page.goto('/settings/account?page=profile'); await signIn(page);
  const name = page.getByRole('textbox', { name: 'Display name', exact: true });
  await expect(page.getByRole('button', { name: 'Save profile', exact: true })).toHaveCount(0);
  await name.fill('Unsaved member');
  await page.getByRole('button', { name: 'Save profile', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Choose another display name.' })).toBeVisible();
  await expect(name).toHaveValue('Unsaved member');
  expect(profileWrites).toBe(1);
  if (process.env.SETTINGS_VISUAL_DIR) {
    await page.screenshot({ fullPage: true, path: `${process.env.SETTINGS_VISUAL_DIR}/personal-profile-error-390.png` });
  }
  const url = page.url();
  for (const action of [
    () => page.getByRole('searchbox', { name: 'Search settings' }).fill('quality'),
    () => openSettingsArea(page, 'Playback'),
    () => navigateApp(page, 'Home'),
    async () => {
      await page.getByRole('button', { name: /account menu/ }).click();
      await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    },
  ]) {
    page.once('dialog', (dialog) => dialog.dismiss());
    await action();
    await expect(page).toHaveURL(url);
    await expect(name).toHaveValue('Unsaved member');
    expect(logouts).toBe(0);
  }
  await page.getByRole('button', { name: 'Discard', exact: true }).click();
  await expect(name).toHaveValue(user.display_name);
  await name.fill('Leave this draft');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: /account menu/ }).click();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect.poll(() => logouts).toBe(1);
});

test('member direct server URLs and full settings search never mount owner controls', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  const member = { ...user, role: 'viewer' };
  await page.route('**/api/session/login', (route) => route.fulfill({ json: { user: member } }));
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: member } }));
  const ownerReads: string[] = [];
  page.on('request', (request) => { if (new URL(request.url()).pathname.startsWith('/api/admin/')) ownerReads.push(new URL(request.url()).pathname); });
  await page.goto('/manage/ai?page=assistant');
  await expect(page.getByText(/This part of Settings is for vault owners/)).toBeVisible();
  await expect(page.locator('[data-setting-id]')).toHaveCount(0);
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('assistant');
  await expect(page.getByRole('region', { name: 'Search results' }).locator('a[href^="/manage"]')).toHaveCount(0);
  for (const link of await page.getByRole('region', { name: 'Search results' }).getByRole('link').all()) await expect(link).toHaveAttribute('href', /^\/settings\//);
  expect(ownerReads).toEqual([]);
  await page.goto('/settings/account?page=profile');
  await expect(page.getByRole('textbox', { name: 'Display name' })).toBeVisible();
});

test('settings shortcut and keyboard remain usable with enlarged text, forced colors and reduced motion', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 600 });
  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/playback'); await signIn(page);
  const skip = page.getByRole('link', { name: 'Skip to main content' });
  await skip.focus();
  await expect(skip).toBeFocused();
  expect((await skip.boundingBox())!.y).toBeGreaterThanOrEqual(0);
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main', { name: 'Main content' })).toBeFocused();
  await page.addStyleTag({ content: 'html { font-size: 200%; }' });
  await expect(page.locator('dialog[open]')).toHaveCount(0);
  await page.keyboard.press('Control+k');
  const search = page.getByRole('searchbox', { name: 'Search settings' });
  await expect(search).toBeFocused();
  await search.fill('caption');
  await search.press('ArrowDown');
  await expect(page.getByRole('region', { name: 'Search results' }).getByRole('link', { name: /Captions/ }).first()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-setting-id="playback.captions"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
