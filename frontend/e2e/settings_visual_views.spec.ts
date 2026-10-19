import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { SETTINGS_CATEGORIES, SETTINGS_PAGES } from '../src/features/settings/navigation';
import { mockAdminFixtures, mockApi, openSettingsArea, signIn } from './lumina-mock';

for (const width of [390, 1440]) {
  test(`every setting area is within two navigation clicks at ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 960 });
    await mockApi(page, {}); await mockAdminFixtures(page);
    await page.goto('/settings'); await signIn(page);
    for (const category of SETTINGS_CATEGORIES) {
      await openSettingsArea(page, category.label);
      const expected = SETTINGS_PAGES.filter((entry) => entry.category === category.id && entry.id !== 'about').flatMap((entry) => entry.rows).sort();
      await expect.poll(() => page.locator('[data-setting-id]').evaluateAll((rows) => rows.filter((row) => row.getClientRects().length > 0).map((row) => row.getAttribute('data-setting-id')).sort())).toEqual(expected);
      // Keep the long workflows compact without hiding controls behind another action.
      if (width === 1440) {
        const compactGroups: Record<string, { target: string; height: number }> = { ai: { target: '#settings-group-features', height: 760 }, playback: { target: '#settings-group-captions', height: 600 }, household: { target: '#settings-group-resources', height: 850 }, overview: { target: '[data-setting-id="activity.background"]', height: 150 } };
        const group = compactGroups[category.id];
        if (group) expect((await page.locator(group.target).boundingBox())!.height, `${category.label} compact group height`).toBeLessThan(group.height);
      }
      await expect(page.locator('.g-settings-local-advanced')).toHaveCount(0);
      await expect(page.getByRole('combobox', { name: 'Settings page' })).toHaveCount(0);
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
  await expect(page.getByRole('heading', { name: 'Captions', exact: true })).toBeVisible();
});

for (const scheme of ['light', 'dark'] as const) {
  test(`playback history scrolls with the keyboard on a ${scheme} phone`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
    await mockApi(page, {}); await mockAdminFixtures(page);
    await page.goto('/settings/overview'); await signIn(page);
    const history = page.getByRole('region', { name: 'Playback history table' });
    await expect(history.getByRole('table')).toBeVisible();
    await history.focus();
    await page.keyboard.press('ArrowRight');
    await expect(history).toBeFocused();
    await expect.poll(() => history.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
    const audit = await new AxeBuilder({ page }).include('[data-setting-id="activity.history"]').analyze();
    expect(audit.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')).toEqual([]);
  });
}

if (process.env.SETTINGS_REWORK_OUT) {
  for (const [width, scheme] of [[1440, 'dark'], [390, 'light']] as const) {
    test(`capture bespoke settings views at ${width}px`, async ({ page }) => {
      const out = process.env.SETTINGS_REWORK_OUT!;
      mkdirSync(out, { recursive: true });
      await page.setViewportSize({ width, height: 960 });
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await mockApi(page, {}); await mockAdminFixtures(page);
      await page.goto('/settings'); await signIn(page);
      await expect(page.getByRole('combobox', { name: 'Maximum quality' })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: join(out, `playback-${width}.png`) });
      writeFileSync(join(out, `layout-${width}.json`), JSON.stringify(await page.locator('.g-settings-pane').evaluate((pane) => [...pane.querySelectorAll<HTMLElement>('*')].filter((element) => element.getBoundingClientRect().right > innerWidth + 1).map((element) => ({ tag: element.tagName, css: element.className, width: element.getBoundingClientRect().width }))), null, 2));
        await openSettingsArea(page, 'Library & storage');
      await expect(page.getByRole('row', { name: /Family NAS/ })).toBeVisible();
      await page.screenshot({ path: join(out, `storage-${width}.png`) });
      await openSettingsArea(page, 'Overview & activity');
      await page.getByRole('link', { name: 'Now playing', exact: true }).click();
      await expect(page.getByText('The Lighthouse Keepers', { exact: true })).toBeVisible();
      await page.screenshot({ path: join(out, `activity-${width}.png`) });
      await page.getByRole('link', { name: 'Playback history', exact: true }).click();
      await expect(page.locator('.activity-viewing-chart')).toBeVisible();
      await page.screenshot({ path: join(out, `history-${width}.png`) });
      await openSettingsArea(page, 'Display');
      await page.screenshot({ path: join(out, `display-${width}.png`) });
    });
  }
}
