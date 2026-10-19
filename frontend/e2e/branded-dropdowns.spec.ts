import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { docFixture } from '../src/features/titles/editor/editorFixtures';
import { mockAdminFixtures, mockApi, mockTitles, signIn } from './lumina-mock';
import { mockMetadataEditor } from './metadata-editor-mock';

async function withinViewport(page: Page) {
  const popup = page.getByRole('listbox');
  await expect(popup).toBeVisible();
  const box = (await popup.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.x).toBeGreaterThanOrEqual(7);
  expect(box.y).toBeGreaterThanOrEqual(7);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width - 7);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height - 7);
  const issues = (await new AxeBuilder({ page }).include('[role="listbox"]').analyze()).violations.filter((issue) => issue.impact === 'serious' || issue.impact === 'critical');
  expect(issues).toEqual([]);
}

for (const colorScheme of ['light', 'dark'] as const) for (const width of [1440, 320]) {
  test(`Lumina dropdowns work across settings, dialogs and suggestions in ${colorScheme} at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme, reducedMotion: 'reduce' });
    await mockApi(page, { settings_advanced: true });
    await mockAdminFixtures(page);
    await mockTitles(page);
    await mockMetadataEditor(page, { doc: docFixture('movie', { title_id: 'movie-1', name: 'Harbor Lights' }) });
    await page.route((url) => url.pathname === '/api/metadata/vocabulary', (route) => route.fulfill({ json: [{ value: 'Comedy', count: 1 }, { value: 'Drama', count: 1 }] }));
    await page.goto('/settings/playback?page=playback');
    await signIn(page);
    const quality = page.getByRole('combobox', { name: 'Maximum quality' });
    await quality.click();
    await withinViewport(page);
    await page.screenshot({ path: testInfo.outputPath('settings-choice.png') });
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    await expect(quality).toContainText('Data saver');
    await expect(quality).toBeFocused();
    await expect(page.locator('select:visible, datalist')).toHaveCount(0);

    await page.goto('/manage/members?page=invitations');
    await page.getByRole('button', { name: 'Invite someone', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Invite someone', exact: true });
    const limits = dialog.getByRole('combobox', { name: 'Limits', exact: true });
    await limits.click();
    await withinViewport(page);
    await page.screenshot({ path: testInfo.outputPath('dialog-choice.png') });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
    await expect(limits).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    await page.goto('/title/movie-1/edit');
    const genres = page.getByRole('combobox', { name: 'Genres', exact: true });
    await genres.fill('Com');
    await expect(page.getByRole('option', { name: 'Comedy', exact: true })).toBeVisible();
    await withinViewport(page);
    await page.screenshot({ path: testInfo.outputPath('suggestion-choice.png') });
    await page.getByRole('option', { name: 'Comedy', exact: true }).click();
    await genres.press('Enter');
    await expect(page.getByRole('button', { name: 'Remove Comedy', exact: true })).toBeVisible();
    await expect(page.locator('select:visible, datalist')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
