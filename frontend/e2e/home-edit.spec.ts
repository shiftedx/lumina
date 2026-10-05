import { expect, test, type Page } from '@playwright/test';

import { episodeSummary } from '../src/test/galleryFixtures';
import { continueEntry, homeMovie, mockHome } from './home-mock';
import { mockApi, signIn } from './lumina-mock';

/** Editing Home by keyboard, pointer, touch and TV remote; persistence through PUT /api/settings/me. */

type Saved = Array<{ ui_prefs?: { home_shelves?: Array<{ id: string; visible: boolean }> | null } }>;

async function openHome(page: Page, uiPrefs: Record<string, unknown> = {}, viewport = { width: 1440, height: 900 }) {
  await page.setViewportSize(viewport);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const saved = (await mockApi(page, { sidebar_collapsed: false, ...uiPrefs })) as Saved;
  const requests = await mockHome(page, {
    continueWatching: [continueEntry(episodeSummary(2, 4))],
    nextUp: [episodeSummary(2, 5)],
    newest: [homeMovie(1), homeMovie(2), homeMovie(3)],
  });
  await page.goto('/');
  await signIn(page);
  await expect(page.getByRole('button', { name: 'Edit home' })).toBeVisible();
  return { saved, requests };
}
const handle = (page: Page, name: string) => page.getByRole('button', { name: `Reorder ${name}` });
const said = (page: Page) => page.locator('.h-editor [role="status"]');
const rows = (page: Page) => page.locator('.h-edit-row');
const lastLayout = (saved: Saved) => saved.map((body) => body.ui_prefs?.home_shelves).filter((value) => value !== undefined).at(-1);

test('keyboard reorder: Space on Next up, two ArrowDowns, Space; saved and kept across a reload', async ({ page }) => {
  const { saved } = await openHome(page);
  await page.getByRole('button', { name: 'Edit home' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.h-masthead').getByRole('button', { name: 'Done' })).toHaveAttribute('aria-pressed', 'true');
  await expect(said(page)).toHaveText('Editing Home. 13 shelves.');
  await expect(handle(page, 'Continue watching')).toBeFocused();
  await handle(page, 'Next up').focus();
  await page.keyboard.press('Space');
  await expect(said(page)).toHaveText('Next up picked up, position 3 of 13. Use Up and Down to move, Space to drop, Escape to cancel.');
  await page.keyboard.press('ArrowDown');
  await expect(said(page)).toHaveText('Next up, position 4 of 13.');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Space');
  await expect(said(page)).toHaveText('Next up dropped at position 5 of 13.');
  await expect(handle(page, 'Next up')).toBeFocused();
  await expect.poll(() => lastLayout(saved)?.slice(0, 5).map((entry) => entry.id)).toEqual(['continue', 'live', 'watchlist', 'new_in_library', 'next_up']);
  await page.reload();
  await page.getByRole('button', { name: 'Edit home' }).click();
  await expect(rows(page).nth(4)).toHaveAttribute('data-shelf-row', 'next_up');
});

test('pointer drag two rows down drops there and saves; Escape mid-drag puts it back and stays in edit mode', async ({ page }) => {
  const { saved } = await openHome(page);
  await page.getByRole('button', { name: 'Edit home' }).click();
  const pitch = (await rows(page).nth(1).boundingBox())!.y - (await rows(page).nth(0).boundingBox())!.y;
  const grip = (await handle(page, 'Next up').boundingBox())!;
  const x = grip.x + grip.width / 2;
  const y = grip.y + grip.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 8, { steps: 2 });
  await page.mouse.move(x, y + 2 * pitch, { steps: 8 });
  await expect(page.locator('[data-shelf-row="next_up"]')).toHaveAttribute('data-dragging', '');
  await page.mouse.up();
  await expect(said(page)).toHaveText('Next up dropped at position 5 of 13.');
  await expect.poll(() => lastLayout(saved)?.findIndex((entry) => entry.id === 'next_up')).toBe(4);

  const again = (await handle(page, 'Next up').boundingBox())!;
  await page.mouse.move(again.x + again.width / 2, again.y + again.height / 2);
  await page.mouse.down();
  await page.mouse.move(again.x + again.width / 2, again.y + again.height / 2 - pitch, { steps: 6 });
  await page.keyboard.press('Escape');
  await expect(said(page)).toHaveText('Reorder cancelled. Next up is back at position 5.');
  await page.mouse.up();
  await expect(rows(page).nth(4)).toHaveAttribute('data-shelf-row', 'next_up');
  await expect(page.locator('.h-editor')).toBeVisible();
});

test.describe('touch', () => {
  test.use({ hasTouch: true });

  test('a touch drag on a phone moves a shelf', async ({ page }) => {
    const { saved } = await openHome(page, {}, { width: 390, height: 844 });
    await page.getByRole('button', { name: 'Edit home' }).click();
    const pitch = (await rows(page).nth(1).boundingBox())!.y - (await rows(page).nth(0).boundingBox())!.y;
    const grip = (await handle(page, 'Next up').boundingBox())!;
    const x = Math.round(grip.x + grip.width / 2);
    const y = Math.round(grip.y + grip.height / 2);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let step = 1; step <= 8; step += 1) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: Math.round(y + (step * pitch) / 8) }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect(said(page)).toHaveText('Next up dropped at position 4 of 13.');
    await expect.poll(() => lastLayout(saved)?.findIndex((entry) => entry.id === 'next_up')).toBe(3);
  });
});

test('a hidden shelf is gone after Done, and its source is never requested after a reload', async ({ page }) => {
  const { saved, requests } = await openHome(page);
  await page.getByRole('button', { name: 'Edit home' }).click();
  await page.getByRole('switch', { name: 'Show Next up' }).click();
  await expect(page.getByRole('switch', { name: 'Show Next up' })).toHaveAttribute('aria-checked', 'false');
  await expect(said(page)).toHaveText('Next up hidden.');
  await page.locator('.h-editor').getByRole('button', { name: 'Done' }).click();
  await expect(page.getByRole('button', { name: 'Edit home' })).toBeFocused();
  await expect(page.getByText('Home layout saved.')).toBeAttached();
  await expect(page.getByRole('region', { name: 'Next up' })).toHaveCount(0);
  await expect.poll(() => lastLayout(saved)?.find((entry) => entry.id === 'next_up')).toEqual({ id: 'next_up', visible: false });
  requests.length = 0;
  await page.reload();
  await expect(page.getByRole('region', { name: 'Continue watching' })).toBeVisible();
  await expect.poll(() => requests.some((request) => request.startsWith('GET /api/titles?'))).toBe(true); // New in your library loaded
  expect(requests.filter((request) => request.includes('/api/titles/next-up'))).toEqual([]);
});

test('Reset writes null, then Undo restores the previous layout', async ({ page }) => {
  const { saved } = await openHome(page, { home_shelves: [{ id: 'recent_music', visible: true }] });
  await page.getByRole('button', { name: 'Edit home' }).click();
  await expect(rows(page).first()).toHaveAttribute('data-shelf-row', 'recent_music');
  await page.getByRole('button', { name: 'Reset to default' }).click();
  await expect(rows(page).first()).toHaveAttribute('data-shelf-row', 'continue');
  await expect(handle(page, 'Continue watching')).toBeFocused();
  await expect(page.getByText('Home reset to the default layout.')).toBeVisible();
  await expect.poll(() => lastLayout(saved)).toBeNull();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(rows(page).first()).toHaveAttribute('data-shelf-row', 'recent_music');
  await expect.poll(() => lastLayout(saved)?.[0]).toEqual({ id: 'recent_music', visible: true });
});

test('TV remote at 1920×1080: arrows reach Resume and the shelves; edit with arrows and Enter; Back cancels, then leaves', async ({ page }) => {
  await openHome(page, {}, { width: 1920, height: 1080 });
  await page.locator('.home h1').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: 'Resume' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('region', { name: 'Continue watching' }).locator('[data-focus-item]').first()).toBeFocused();
  await page.locator('.home h1').focus();
  await page.keyboard.press('ArrowUp');
  await expect(page.getByRole('button', { name: 'Edit home' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(handle(page, 'Continue watching')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(handle(page, 'Live now')).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('switch', { name: 'Show Live now' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Enter');
  await expect(said(page)).toHaveText('Live now picked up, position 2 of 13. Use Up and Down to move, Space to drop, Escape to cancel.');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  await expect(said(page)).toHaveText('Reorder cancelled. Live now is back at position 2.');
  await page.keyboard.press('Escape');
  await expect(page.locator('.h-editor')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Edit home' })).toBeFocused();
});

test('TV remote reaches Reset to default and Done below the last row (E-I3)', async ({ page }) => {
  await openHome(page, {}, { width: 1920, height: 1080 });
  await page.getByRole('button', { name: 'Edit home' }).click();
  await handle(page, 'Recently added music').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: 'Reset to default' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.h-editor').getByRole('button', { name: 'Done' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('Enter');
  await expect(handle(page, 'Continue watching')).toBeFocused();
});

test('another browser signed in to a member without a saved layout sees the default', async ({ page, browser }) => {
  const { saved } = await openHome(page);
  await page.getByRole('button', { name: 'Edit home' }).click();
  await page.getByRole('button', { name: 'Move Recently added music up' }).click();
  await expect.poll(() => lastLayout(saved)?.at(-2)?.id).toBe('recent_music');
  const other = await browser.newPage();
  await openHome(other);
  await other.getByRole('button', { name: 'Edit home' }).click();
  await expect(other.locator('.h-edit-row').last()).toHaveAttribute('data-shelf-row', 'recent_music');
  await other.close();
});
