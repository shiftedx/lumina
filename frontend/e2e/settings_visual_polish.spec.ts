import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { SETTINGS_PAGES } from '../src/features/settings/navigation';
import { mockAdminFixtures, mockApi, signIn } from './lumina-mock';

const QUALITY_OUT = process.env.SETTINGS_QUALITY_OUT;

if (QUALITY_OUT) {
  for (const scheme of ['light', 'dark'] as const) {
    for (const viewport of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }] as const) {
      test(`capture every settings page in ${scheme} at ${viewport.width}px`, async ({ page }) => {
        test.setTimeout(480_000);
        mkdirSync(QUALITY_OUT, { recursive: true });
        await page.setViewportSize(viewport);
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
        await mockApi(page, { settings_advanced: true });
        await mockAdminFixtures(page);
        await page.goto('/'); await signIn(page);
        for (const destination of SETTINGS_PAGES) {
          await page.goto(`/settings/${destination.section}?page=${destination.id}`);
          await expect(page.locator('[data-setting-id]').first()).toBeVisible();
          await page.evaluate(() => document.fonts.ready);
          await page.screenshot({ fullPage: true, path: join(QUALITY_OUT, `${destination.id}-${viewport.name}-${scheme}.png`) });
        }
        await page.goto('/settings/members/m2');
        const memberTabs = page.locator('.g-member-tabs [role="tab"]');
        const memberPages = page.getByRole('combobox', { name: 'Member settings page' });
        await expect(viewport.name === 'phone' ? memberPages : memberTabs.first()).toBeVisible();
        const ids = viewport.name === 'phone'
          ? await memberPages.locator('option').evaluateAll((options) => options.map((option) => (option as HTMLOptionElement).value))
          : await memberTabs.evaluateAll((tabs) => tabs.map((tab) => tab.id.replace('member-tab-', '')));
        for (const id of ids) {
          if (viewport.name === 'phone') await memberPages.selectOption(id);
          else await page.locator(`#member-tab-${id}`).click();
          await page.screenshot({ fullPage: true, path: join(QUALITY_OUT, `member-${id}-${viewport.name}-${scheme}.png`) });
        }
      });
    }
  }
}

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
  const choices = page.locator('[data-setting-id="members.libraries"] .g-control-stack');
  await expect(choices).toBeVisible();
  expect(await choices.evaluate((element) => element.clientWidth)).toBeGreaterThan(500);
  await expect(choices.getByRole('checkbox')).toHaveCount(4);
  await expect(choices.getByRole('group', { name: 'Watch', exact: true })).toBeVisible();
  await expect(choices.getByRole('group', { name: 'Other files', exact: true })).toBeVisible();
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

test('setting descriptions start below their heading at wide desktop widths', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/apps?page=apps'); await signIn(page);
  const row = page.locator('[data-setting-id="apps.connected"]');
  const heading = row.locator(':scope > .g-setting-head > .g-setting-label');
  const description = row.locator(':scope > .g-setting-head > .g-setting-desc');
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(description).toBeVisible();
    const [headingBox, descriptionBox] = await Promise.all([heading.boundingBox(), description.boundingBox()]);
    expect(descriptionBox!.y, `${width}px description begins after the heading`).toBeGreaterThanOrEqual(headingBox!.y + headingBox!.height);
  }
});

test('phone switch rows keep their heading copy readable above auxiliary controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/ai?page=features'); await signIn(page);
  const row = page.locator('[data-setting-id="ai.semantic-search"]');
  const head = row.locator(':scope > .g-setting-head');
  const description = head.locator('.g-setting-desc');
  const control = row.locator(':scope > .g-setting-control');
  const [headBox, descriptionBox, controlBox] = await Promise.all([head.boundingBox(), description.boundingBox(), control.boundingBox()]);
  expect(descriptionBox!.width).toBeGreaterThan(220);
  expect(controlBox!.y).toBeGreaterThanOrEqual(headBox!.y + headBox!.height);
});

test('storage roots keep path and status readable at desktop and phone widths', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/library?page=folders'); await signIn(page);
  const row = page.getByRole('row', { name: /Family NAS/ });
  const pathCell = row.getByRole('cell').filter({ hasText: '/media/nas' });
  const path = row.getByText('/media/nas', { exact: true });
  const status = row.getByText('Available', { exact: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  expect((await path.boundingBox())!.height).toBeLessThan(24);
  await expect(pathCell.getByText('Path', { exact: true })).toBeHidden();
  await page.setViewportSize({ width: 390, height: 900 });
  const label = pathCell.getByText('Path', { exact: true });
  await expect(pathCell).toHaveAccessibleName('Path /media/nas');
  const [labelBox, pathBox, statusBox] = await Promise.all([label.boundingBox(), path.boundingBox(), status.boundingBox()]);
  expect(labelBox!.y + labelBox!.height).toBeLessThanOrEqual(pathBox!.y);
  expect(statusBox!.x).toBeGreaterThanOrEqual(16);
  expect(statusBox!.x + statusBox!.width).toBeLessThanOrEqual(374);
});

test('member settings expose every page in a labeled phone selector', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/members/m2'); await signIn(page);
  const pages = page.getByRole('combobox', { name: 'Member settings page' });
  await expect(pages).toBeVisible();
  await expect(page.getByRole('tablist', { name: /settings/ })).toBeHidden();
  await expect(pages.getByRole('option')).toHaveCount(7);
  await pages.selectOption('schedule');
  const monday = page.getByRole('group', { name: 'Monday' });
  const [dayBox, fromBox] = await Promise.all([monday.locator('.g-day-name').boundingBox(), monday.getByRole('textbox', { name: 'Monday from' }).boundingBox()]);
  expect(dayBox!.y + dayBox!.height).toBeLessThanOrEqual(fromBox!.y);
  await pages.selectOption('activity');
  await expect(page.getByText('Screen time today', { exact: true })).toBeVisible();
});

test('request rules render household defaults and member overrides from complete fixtures', async ({ page }) => {
  await mockApi(page, {}); await mockAdminFixtures(page);
  await page.goto('/settings/requests?page=request-rules'); await signIn(page);
  const defaults = page.getByRole('group', { name: 'Household defaults' });
  await expect(defaults).toBeVisible();
  await expect(defaults.locator('fieldset')).toHaveCount(3);
  await expect(defaults.getByRole('checkbox', { name: 'Shows: Auto-approve' })).toBeChecked();
  await expect(defaults.getByRole('checkbox', { name: 'Unlimited' }).nth(1)).toBeChecked();
  await expect(page.getByRole('button', { name: 'Edit Alexandria' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Edit Eleanor Whitcombe-Harrington' })).toBeEnabled();
  await expect(page.getByText('Custom', { exact: true })).toBeVisible();
});
