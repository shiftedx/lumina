import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import AxeBuilder from '@axe-core/playwright';
import { SETTINGS_CATEGORIES, SETTINGS_PAGES } from '../src/features/settings/navigation';
import { mockAdminFixtures, mockApi, openSettingsArea, signIn } from './lumina-mock';

for (const width of [320, 390, 768, 1440]) {
  test(`every setting area is within two navigation clicks at ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 960 });
    await mockApi(page, { theme: 'dark' }); await mockAdminFixtures(page);
    await page.goto('/settings'); await signIn(page);
    for (const category of SETTINGS_CATEGORIES) {
      await openSettingsArea(page, category.label);
      await expect(page.locator('.g-settings-section-head h2')).toHaveText(category.label);
      await expect(page.locator('[data-setting-id]').first()).toBeVisible();
      await expect(page.locator('.g-settings-pane [aria-busy="true"]')).toHaveCount(0);
      const expected = SETTINGS_PAGES.filter((entry) => entry.category === category.id && entry.id !== 'about').flatMap((entry) => entry.rows).sort();

      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      await expect(page.getByRole('searchbox', { name: 'Search settings' })).toHaveCount(1);
      await expect(page.getByRole('navigation', { name: 'Mobile primary navigation' })).toHaveCount(0);
      if (width === 1440) {
        const [searchBox, firstLink] = await Promise.all([page.getByRole('searchbox', { name: 'Search settings' }).boundingBox(), page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link').first().boundingBox()]);
        expect(firstLink!.y - searchBox!.y - searchBox!.height).toBeLessThanOrEqual(80);
      }
      if (width <= 768) await expect(page.getByRole('combobox', { name: 'Choose settings section' })).toBeVisible();
      for (const summary of await page.locator('.g-settings-advanced > summary').all()) {
        if (await summary.evaluate((element) => (element.parentElement as HTMLDetailsElement).open)) await summary.click();
      }
      if (process.env.SETTINGS_VISUAL_DIR) {
        mkdirSync(process.env.SETTINGS_VISUAL_DIR, { recursive: true });
        await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
        await page.screenshot({ fullPage: true, path: join(process.env.SETTINGS_VISUAL_DIR, `${category.space}-${category.id}-${width}.png`) });
      }
      for (const summary of await page.locator('.g-settings-advanced > summary').all()) {
        if (!(await summary.evaluate((element) => (element.parentElement as HTMLDetailsElement).open))) await summary.click();
      }
      await expect.poll(() => page.locator('[data-setting-id]').evaluateAll((rows) => rows.filter((row) => row.getClientRects().length > 0).map((row) => row.getAttribute('data-setting-id')).sort())).toEqual(expected);
    }
    await openSettingsArea(page, 'Household');
    await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /Eleanor/ }).click();
    await expect(page.locator('.g-settings-jumps.g-member-nav a')).toHaveCount(8);
    await expect(page.locator('[data-setting-id="members.daily"]')).toBeVisible();
    await expect(page.locator('[data-setting-id="members.resources"]')).toBeVisible();
    await expect(page.getByRole('tablist')).toHaveCount(0);
  });
}

test('bookmarked controls and group anchors focus their destination after loading', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/playback?page=watching#setting-playback-max-quality'); await signIn(page);
  await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeFocused();
  await page.goto('/settings/playback?page=watching#settings-group-captions');
  await expect(page.locator('#settings-group-captions')).toBeFocused();
  const [group, header] = await Promise.all([page.locator('#settings-group-captions').boundingBox(), page.locator('.g-topbar').boundingBox()]);
  expect(group!.y).toBeGreaterThanOrEqual(header!.y + header!.height);
  await expect(page.getByRole('heading', { name: 'Captions', exact: true })).toBeVisible();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`playback history scrolls with the keyboard on a ${scheme} phone`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await mockApi(page, {}); await mockAdminFixtures(page);
    await page.goto('/manage/activity?page=playback-history'); await signIn(page);
    const history = page.getByRole('region', { name: 'Playback history table' });
    await expect(history.getByRole('table')).toBeVisible();
    await expect(page.getByText('Scroll horizontally for more columns.', { exact: false })).toBeVisible();
    await history.focus();
    await page.keyboard.press('ArrowRight');
    await expect(history).toBeFocused();
    await expect.poll(() => history.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    const audit = await new AxeBuilder({ page }).include('[data-setting-id="activity.history"]').analyze();
    expect(audit.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')).toEqual([]);
  });
}
