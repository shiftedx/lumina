import { expect, test } from '@playwright/test';
import { mockApi, signIn } from './lumina-mock';
import { mockPaletteSearch } from './palette-mock';

test.beforeEach(async ({ page }) => {
  await mockApi(page, {});
  await mockPaletteSearch(page);
  await page.goto('/');
  await signIn(page);
});

test('⌘K → type → ↓ → ↵ opens a title', async ({ page }) => {
  await page.keyboard.press('ControlOrMeta+k');
  const input = page.getByRole('combobox');
  await expect(input).toBeFocused();
  await input.fill('lib'); // the mock answers every query with the Alpine fixtures; 'lib' also matches the Library place for Go to
  await expect(page.getByRole('group', { name: 'Movies' })).toContainText('Alpine Crossing');
  for (const group of ['Shows', 'Anime', 'Music', 'Moments', 'Your videos', 'YouTube', 'Go to']) await expect(page.getByRole('group', { name: group })).toBeVisible();
  await expect(page.getByText(/^\d+ results$/)).toBeAttached();
  await input.press('ArrowDown');
  await expect(page.locator('[role=option][aria-selected=true]')).toHaveCount(1);
  while (!(await page.locator('[role=option][aria-selected=true]').textContent())?.includes('Alpine Crossing')) await input.press('ArrowDown');
  await input.press('Enter');
  await expect(page).toHaveURL(/\/title\/movie-1$/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('Esc clears, then closes and returns focus to the trigger', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /^Search Lumina, / });
  await trigger.click();
  await page.getByRole('combobox').fill('alp');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('combobox')).toHaveValue('');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('combobox')).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('⌘↵ searches everything in Explore', async ({ page }) => {
  await page.keyboard.press('ControlOrMeta+k');
  await page.getByRole('combobox').fill('alp');
  await page.keyboard.press('ControlOrMeta+Enter');
  await expect(page).toHaveURL(/\/streaming\/search\?q=alp$/);
});

test('link mode rejects text and opens a pasted link', async ({ page }) => {
  await page.getByRole('button', { name: 'Add a link' }).click();
  await expect(page.getByRole('dialog', { name: 'Add a link' })).toBeVisible();
  await page.getByRole('combobox').fill('not a link');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('alert')).toHaveText("That doesn't look like a link.");
  await page.getByRole('combobox').fill('https://www.youtube.com/watch?v=yt-1');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/watch\?url=/);
});

test('phone: the Search tab opens a full-screen palette', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: 'Search' }).click();
  const dialog = page.locator('dialog.g-palette');
  await expect(dialog).toBeVisible();
  await expect.poll(async () => Math.round((await dialog.boundingBox())?.width ?? 0)).toBe(390); // the open animation settles first
});

test('the palette reaches Streaming, Live now, Your channels, saved channels and Home & discovery through one Actions row each', async ({ page }) => {
  // Live, YouTube and 1.9.0 surfaces. Mocks: mockLive / mockLibraryChannels from live-youtube-mock.ts if the surface needs them.
  const cases: Array<[string, string, RegExp]> = [['streaming', 'Streaming', /\/streaming$/], ['live now', 'Live now', /\/streaming\/live$/], ['your channels', 'Your channels', /\/streaming\/channels$/], ['saved channels', 'Open saved channels', /\/library\/youtube\?view=channels$/], ['discovery', 'Home & discovery', /\/settings\/discovery$/]];
  for (const [query, label, url] of cases) {
    await page.keyboard.press('ControlOrMeta+k');
    await page.locator('dialog.g-palette').getByRole('combobox').fill(query);
    const group = page.getByRole('group', { name: 'Actions' });
    await expect(group.getByRole('option', { name: new RegExp(`^${label}`) })).toBeVisible();
    await expect(page.getByRole('group', { name: 'Go to' }).getByRole('option', { name: new RegExp(`^${label.replace('Open ', '')}`) }).filter({ hasNotText: 'Settings' })).toHaveCount(0); // one row per destination (the Streaming settings section is its own destination)
    await group.getByRole('option', { name: new RegExp(`^${label}`) }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.locator('main h1').first()).toBeVisible();
  }
});

test('profile menu opens from the keyboard and focuses its first action', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /, account menu$/ });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', { name: /^(Switch member…|Sign in as someone else…)$/ })).toBeFocused();
});

test('profile menu → Switch member opens the picker (keyboard only)', async ({ page }) => {
  const trigger = page.getByRole('button', { name: /, account menu$/ });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: "Who's watching?" })).toBeVisible();
});
