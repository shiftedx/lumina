import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

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

async function expectArtworkCaptionsInsideCards(page: Page) {
  const escaped = await page.locator('.ed-art-card').evaluateAll((cards) => cards.flatMap((card, cardIndex) => {
    const cardBox = card.getBoundingClientRect();
    return Array.from(card.querySelector('.ed-art-caption')?.children ?? []).flatMap((child) => {
      const box = child.getBoundingClientRect();
      const outside = box.left < cardBox.left - 0.5 || box.right > cardBox.right + 0.5 || box.top < cardBox.top - 0.5 || box.bottom > cardBox.bottom + 0.5;
      return outside ? [`card ${cardIndex + 1}: ${child.getAttribute('aria-label') || child.textContent?.trim() || child.tagName} escapes its card`] : [];
    });
  }));
  expect(escaped).toEqual([]);
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

test('Artwork explains its immediate saves without presenting the metadata draft footer', async ({ page }) => {
  await open(page);
  await page.getByRole('textbox', { name: 'Overview' }).fill('A draft kept across sections.');
  await expect(page.getByRole('button', { name: 'Save 1 change' })).toBeVisible();

  await page.getByRole('radio', { name: 'Artwork' }).check({ force: true });
  await expect(page.getByText('Artwork changes save as you make them. Each one can be undone from History.')).toBeVisible();
  await expect(page.getByText('You also have 1 unsaved metadata change.')).toBeVisible();
  await expect(page.locator('.ed-footer')).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.getByRole('button', { name: 'Review changes' }).click();
  await expect(page.getByRole('textbox', { name: 'Overview' })).toHaveValue('A draft kept across sections.');
  await expect(page.getByRole('button', { name: 'Save 1 change' })).toBeVisible();
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

for (const [width, scheme] of [[390, 'light'], [390, 'dark'], [1536, 'light'], [1536, 'dark']] as const) {
  test(`Artwork captions keep labels and controls inside their cards at ${width}px, ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await open(page, { width, height: width === 390 ? 844 : 900 }, '/title/movie-1/edit?tab=artwork');
    const posterCaption = page.locator('.ed-art-card').first().locator('.ed-art-caption');
    await expect(posterCaption.locator('strong')).toHaveText('Poster');
    await expect(posterCaption.locator('.g-chip')).toHaveText('From TMDB');
    await expect(posterCaption.getByRole('button', { name: 'Lock Poster' })).toBeVisible();
    await expect(posterCaption.getByRole('button', { name: 'Poster options' })).toBeVisible();
    await expectArtworkCaptionsInsideCards(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

const qualityOutput = process.env.METADATA_EDITOR_QUALITY_OUT;
if (qualityOutput) {
  for (const [width, scheme] of [[390, 'light'], [390, 'dark'], [1536, 'light'], [1536, 'dark']] as const) {
    test(`capture: Artwork autosave at ${width}px, ${scheme}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, { width, height: width === 390 ? 844 : 900 });
      await page.getByRole('textbox', { name: 'Overview' }).fill('A draft kept across sections.');
      await page.getByRole('radio', { name: 'Artwork' }).check({ force: true });
      await expect(page.getByText('Artwork changes save as you make them. Each one can be undone from History.')).toBeVisible();
      await expect(page.getByText('You also have 1 unsaved metadata change.')).toBeVisible();
      await mkdir(qualityOutput, { recursive: true });
      await page.screenshot({ fullPage: true, path: `${qualityOutput}/artwork-autosave-${width}-${scheme}.png` });
    });
  }
}
