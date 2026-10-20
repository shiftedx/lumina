import { navigateApp } from './lumina-mock';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn, user } from './lumina-mock';

/** Light/dark/system themes on the real app shell (mocked API, synthetic media). */

async function expectReadable(page: Page) {
  // Rendered WCAG AA text contrast; video frames and artwork overlays are excluded (theme-invariant, content-dependent).
  const results = await new AxeBuilder({ page }).withRules(['color-contrast']).exclude('.player-frame').analyze();
  expect(results.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(' ')}: ${n.failureSummary}`))).toEqual([]);
}

// Theme-dependent: rendered contrast differs per scheme; layout width does not change it.
for (const scheme of ['light', 'dark'] as const) {
  test(`test_theme_visual_existing_screens ${scheme}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1536, height: 960 });
    await mockApi(page, { sidebar_collapsed: false });
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
    await expectReadable(page);
    await signIn(page);
    await page.waitForTimeout(300);
    await expectReadable(page);
    await page.getByRole('button', { name: /, account menu$/ }).click();
    await page.getByRole('menuitem', { name: 'My Settings' }).click();
    await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Display' }).click();
    await expect(page.getByRole('radio', { name: 'System' })).toBeChecked();
    await page.waitForTimeout(300);
    await expectReadable(page);
    await navigateApp(page, 'Video');
    await page.getByRole('button', { name: new RegExp(`^${item.title}, `) }).first().click();
    await expect(page.locator('video').first()).toBeVisible();
    await page.waitForTimeout(500);
    await expectReadable(page);
    expect(errors).toEqual([]);
  });
}

test('test_theme_system_changes: explicit choice ignores the OS, system follows it, and the choice persists', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 1536, height: 960 });
  const saved = await mockApi(page, { sidebar_collapsed: false, theme: 'dark' });
  await page.goto('/');
  await signIn(page);
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'My Settings' }).click();
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Display' }).click();
  await page.getByRole('radio', { name: 'System' }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'system')).toBe(true);

  // test_theme_no_flash_and_persists: the boot hint applies before any app script runs.
  await page.getByRole('radio', { name: 'Light' }).check();
  await page.route('**/src/main.tsx', (route) => route.abort());
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

for (const width of [1536, 390]) test(`OLED stays pure black, readable and persistent at ${width}px`, async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height: 960 });
  const saved = await mockApi(page, { sidebar_collapsed: false, theme: 'light' });
  await page.goto('/settings/appearance?page=display');
  await signIn(page);
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Display', exact: true }).click();
  await page.getByRole('radio', { name: 'OLED', exact: true }).check();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled');
  await expect.poll(() => saved.some((body) => (body.ui_prefs as Record<string, unknown> | undefined)?.theme === 'oled')).toBe(true);
  for (const surface of [page.locator('body'), page.locator('.g-settings-rows').first()]) {
    await expect(surface).toHaveCSS('background-color', 'rgb(0, 0, 0)');
  }
  await expect(page.locator('meta[name="theme-color"]').first()).toHaveAttribute('content', '#000');
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', await page.locator('html').evaluate((el) => el.clientWidth));
  await expectReadable(page);
  await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: `../output/oled-theme-${width}.png`, fullPage: true });
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled');
  await page.route('**/src/main.tsx', (route) => route.abort());
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled');
  await expect(page.locator('meta[name="theme-color"]').first()).toHaveAttribute('content', '#000');
});
