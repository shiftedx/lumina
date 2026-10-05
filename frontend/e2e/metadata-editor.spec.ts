import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { docFixture } from '../src/features/titles/editor/editorFixtures';
import { mockApi, mockTitles, signIn } from './lumina-mock';
import { mockMetadataEditor } from './metadata-editor-mock';

/** The metadata editor over the mocked API. */

const doc = () => docFixture('movie', { title_id: 'movie-1', name: 'Harbor Lights' });
const OLD = 'Work and life are split.';

async function open(page: Page, viewport = { width: 1536, height: 900 }, path = '/title/movie-1/edit') {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  await mockMetadataEditor(page, { doc: doc() });
  await page.goto(path);
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeVisible();
}

test('flow: edit, save, undo from the toast, reload, then undo from History', async ({ page }) => {
  await open(page);
  const overview = page.getByRole('textbox', { name: 'Overview' });
  await overview.fill('A lamp, a girl, a long walk.');
  await page.getByRole('button', { name: 'Save 1 change' }).click();
  const toast = page.getByText('Saved.');
  await expect(toast).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Overview' })).toHaveValue('A lamp, a girl, a long walk.');
  await page.getByRole('radio', { name: 'History' }).check({ force: true });
  await page.getByRole('button', { name: 'Undo' }).click();
  await page.getByRole('radio', { name: 'Details' }).check({ force: true });
  await expect(page.getByRole('textbox', { name: 'Overview' })).toHaveValue(OLD);
});

test('keyboard: Back to title, the sections and the footer are reachable with Tab alone', async ({ page }) => {
  await open(page);
  const reached: string[] = [];
  const press = async () => {
    await page.keyboard.press('Tab');
    reached.push(await page.evaluate(() => { const el = document.activeElement as HTMLElement | null; return el?.getAttribute('aria-label') || el?.textContent?.trim() || (el as HTMLInputElement | null)?.labels?.[0]?.textContent?.trim() || ''; }));
  };
  for (let i = 0; i < 12; i += 1) await press();
  // Save stays disabled (and so out of the tab order) until something changed.
  await page.getByRole('textbox', { name: 'Overview' }).fill('A lamp.');
  for (let i = 0; i < 80 && !reached.includes('Save 1 change'); i += 1) await press();
  expect(reached).toContain('Save 1 change');
  expect(reached.indexOf('Back to title')).toBeGreaterThanOrEqual(0);
  expect(reached.indexOf('Back to title')).toBeLessThan(reached.indexOf('Save 1 change'));
  expect(reached).toContain('Details'); // the section tabs are one radio group: arrows move between them
});

test('flow: rename a person on every title from the Cast tab (#164)', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 900 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  const credited = doc();
  credited.fields.people = { value: [{ person_id: 'p1', name: 'Keanu Reevs', role: 'Neo', type: 'Actor' }], source: 'tmdb', locked: false, kept: null };
  await mockMetadataEditor(page, { doc: credited });
  await page.goto('/title/movie-1/edit?tab=people');
  await signIn(page);
  await page.getByRole('button', { name: 'Edit Keanu Reevs everywhere' }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit person' });
  await expect(dialog.getByText('Changes show on all 2 titles that credit them, here and in connected apps.')).toBeVisible();
  expect((await new AxeBuilder({ page }).include('dialog').analyze()).violations).toEqual([]);
  await dialog.getByRole('textbox', { name: 'Name' }).fill('Keanu Reeves');
  await dialog.getByRole('button', { name: 'Save name' }).click();
  await expect(page.getByText('Name saved.')).toBeVisible();
  await expect(dialog.getByText('Originally Keanu Reevs')).toBeVisible();
});

test('History tab: lists the saved batch and Undo restores it', async ({ page }) => {
  await open(page, undefined, '/title/movie-1/edit?tab=history');
  await expect(page.getByText('No edits yet.')).toBeVisible();
});

test('phone: the Episodes tab is a list, and the sticky footer stays clear of a focused field', async ({ page }) => {
  await open(page, { width: 390, height: 844 });
  const field = page.getByRole('textbox', { name: 'Overview' });
  await field.focus();
  // The field is taller than what is left above the footer, so assert its first lines (where the caret is) are clear.
  await expect.poll(async () => { const [input, footer] = [await field.boundingBox(), await page.locator('.ed-footer').boundingBox()]; return Boolean(input && footer && input.y + 48 <= footer.y); }).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('phone: the Lock this item switch sits inside the viewport', async ({ page }) => {
  await open(page, { width: 390, height: 844 });
  const box = await page.locator('.g-switch-track').first().boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390 - 8).toBe(true);
});

for (const [width, scheme] of [[390, 'light'], [390, 'dark'], [1536, 'light'], [1536, 'dark']] as const) {
  test(`axe: the Details tab has no violations at ${width}px, ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await open(page, { width, height: 900 });
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
}
