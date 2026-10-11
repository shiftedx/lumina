import { expect, test } from '@playwright/test';
import { SETTINGS_PAGES } from '../src/features/settings/navigation';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

for (const width of [390, 975, 1536]) {
  test(`settings panels contain their controls and every row remains reachable at ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await mockApi(page, { settings_advanced: true });
    await mockAdminFixtures(page);
    await page.goto('/'); await signIn(page);
    const problems: string[] = [];
    for (const destination of SETTINGS_PAGES) {
      await page.goto(`/settings/${destination.section}?page=${destination.id}`);
      await expect(page.locator('[data-setting-id]').first()).toBeVisible();
      for (const summary of await page.locator('.g-settings-local-advanced > summary').all()) await summary.click();
      const report = await page.locator('.g-settings-pane').evaluate((pane) => ({
        rows: [...pane.querySelectorAll<HTMLElement>('[data-setting-id]')].filter((row) => row.getBoundingClientRect().height > 0).map((row) => row.dataset.settingId),
        overflow: [...pane.querySelectorAll<HTMLElement>('.g-settings-rows, .g-settings-local-advanced')].some((panel) => panel.scrollWidth > panel.clientWidth + 1),
        documentOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      }));
      expect(report.rows.sort(), destination.id).toEqual([...destination.rows].sort());
      if (report.overflow || report.documentOverflow) problems.push(destination.id);
    }
    expect(problems).toEqual([]);
  });
}

test('assistant model dropdown discovers draft endpoints without saving configuration', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  const lookups: unknown[] = [];
  let saves = 0;
  await page.route('**/api/admin/ai/models', async (route) => {
    lookups.push(route.request().postDataJSON());
    await route.fulfill({ json: { models: ['local-model', 'available-model'], error: null } });
  });
  await page.route('**/api/admin/ai/config', async (route) => {
    if (route.request().method() === 'PUT') saves++;
    await route.fallback();
  });
  await page.goto('/settings/ai?page=assistant'); await signIn(page);
  const model = page.getByRole('combobox', { name: 'Model', exact: true });
  await expect(model.getByRole('option', { name: 'available-model', exact: true })).toHaveCount(1);
  await expect(model).toHaveValue('local-model');
  await page.getByLabel('Server address', { exact: true }).fill('http://assistant.home.arpa/v1');
  await expect.poll(() => lookups).toContainEqual({ base_url: 'http://assistant.home.arpa/v1' });
  await model.selectOption('available-model');
  expect(saves).toBe(0);
  await expect(page.getByRole('button', { name: 'Save AI settings' })).toBeEnabled();
});


test('invitation libraries fit a compact grid with every choice and the limits selector accessible', async ({ page }) => {
  await page.setViewportSize({ width: 975, height: 900 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/members?page=invitations'); await signIn(page);
  await page.getByRole('button', { name: 'Invite someone', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Invite someone' });
  await expect(dialog.getByRole('checkbox', { name: 'All libraries', exact: true })).toBeVisible();
  const choices = dialog.locator('.g-setting-options .g-choice');
  await expect(choices).toHaveCount(4);
  const rects = await choices.evaluateAll((labels) => labels.map((label) => ({ x: label.getBoundingClientRect().x, y: label.getBoundingClientRect().y })));
  expect(rects[0].y).toBe(rects[1].y);
  expect(rects[0].x).not.toBe(rects[1].x);
  await expect(dialog.getByRole('combobox', { name: 'Limits', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
});


test('member library choices use the full pane and Activity has no unrelated Save', async ({ page }) => {
  await page.setViewportSize({ width: 975, height: 900 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/members/m2'); await signIn(page);
  await page.getByRole('tab', { name: 'Libraries', exact: true }).click();
  const choices = page.locator('[data-setting-id="members.libraries"] .g-setting-options');
  await expect(choices).toBeVisible();
  expect(await choices.evaluate((element) => element.clientWidth)).toBeGreaterThan(500);
  await expect(choices.getByRole('checkbox')).toHaveCount(4);
  await page.getByRole('tab', { name: 'Activity', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toHaveCount(0);
});


test('model explanations stay inside the viewport on desktop and phones', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/ai?page=models'); await signIn(page);
  for (const width of [975, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.getByRole('button', { name: 'About Granite Embedding 97M multilingual', exact: true }).click();
    const note = page.locator('.g-info-note:not([hidden])');
    await expect(note).toBeVisible();
    const bounds = await note.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width - 15);
    await page.keyboard.press('Escape');
  }
});
