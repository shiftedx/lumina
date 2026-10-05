import { expect, test, type Page } from '@playwright/test';
import { mockApi, mockTitles, signIn } from './lumina-mock';

/** Title lenses and pages over the mocked API (synthetic media). */

async function open(page: Page, path: string, uiPrefs: Record<string, unknown> = {}) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false, ...uiPrefs });
  const calls = await mockTitles(page);
  await page.goto(path);
  await signIn(page);
  return calls;
}

test('Next up → series → Season 2 → play', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await open(page, '/');
  // The hero carousel pushes the last shelves below the lazy-load margin; bring them near so the empty ones resolve and drop out.
  await page.locator('.h-shelf').last().scrollIntoViewIfNeeded();
  await expect(page.locator('.h-shelf h2')).toHaveText(['Next up', 'Because you watched Harbor Lights', 'Recently saved']);
  await page.getByRole('button', { name: 'Harbor Lights, S2 · E2' }).click();
  await expect(page).toHaveURL(/\/title\/series-1\?season=2$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeVisible();
  await expect(page.getByRole('tab')).toHaveText(['Season 1', 'Season 2', 'Specials']);
  await expect(page.getByRole('tab', { name: 'Season 2' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Season 1' }).click();
  await expect(page).toHaveURL(/season=1$/);
  await expect(page.getByRole('button', { name: 'Play 1. Low Tide, S1 · E1, watched' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/season=2$/);
  await page.getByRole('button', { name: 'Resume S2 · E2', exact: true }).click();
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-2-2$/);
  await expect(page.locator('.watch-episode-byline')).toContainText('S2 · E2 · The Ferry');
  await page.getByRole('button', { name: 'Harbor Lights', exact: true }).click();
  await expect(page).toHaveURL(/\/title\/series-1\?season=2$/);
  expect(errors).toEqual([]);
});

test('Back from Watch returns to the title page and season it was played from', async ({ page }) => {
  // The synthetic clip is a second long; autoplay would move on to E3 before Back is pressed.
  await open(page, '/title/series-1?season=2', { autoplay_up_next: false });
  await page.getByRole('button', { name: 'Resume S2 · E2', exact: true }).click();
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-2-2$/);
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page).toHaveURL(/\/title\/series-1\?season=2$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeVisible();
});

test('a movie plays the chosen version', async ({ page }) => {
  await open(page, '/title/movie-1');
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
  await expect(page.getByRole('radio', { name: '4K HDR · 58 GB' })).toBeChecked();
  await page.getByRole('radio', { name: '1080p · 8.0 GB' }).check();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page).toHaveURL(/\/watch\/library\/item-movie-1080$/);
});

test('an admin fixes a match from the title page', async ({ page }) => {
  const calls = await open(page, '/title/movie-1');
  await page.getByRole('button', { name: 'Fix match' }).click();
  const dialog = page.getByRole('dialog', { name: 'Fix match' });
  await dialog.getByRole('button', { name: 'Choose Northern Lantern (2019)' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Matched to Northern Lantern' })).toBeVisible();
  expect(calls).toContain('POST /api/admin/titles/movie-1/identify');
});

test('keyboard only: arrows move between Home rows, Enter opens, Escape goes back', async ({ page }) => {
  await open(page, '/');
  await page.getByRole('button', { name: 'Harbor Lights, S2 · E2' }).first().focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: /^Northern Lantern,/ }).first()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/title\/movie-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/library\/movies$/);
  await expect(page.getByRole('button', { name: /^Northern Lantern,/ })).toBeVisible();
});

// The 10-foot step is for touch screens and TV remotes; a mouse gets the 36px desktop control (tokens.css).
test.describe('on a touch screen or TV', () => {
  test.use({ hasTouch: true });
  test('Try again, New smart collection and Visit TMDB are 48px targets at the 1600px step', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize({ width: 1600, height: 1000 });
    await mockApi(page, { sidebar_collapsed: false });
    // Registered after mockApi, so these win: an empty collections panel and a failing Movies lens.
    await page.route((url) => url.pathname === '/api/collections', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route((url) => url.pathname === '/api/titles', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' }));
    const height = async (name: string) => (await page.getByRole(name === 'Visit TMDB' ? 'link' : 'button', { name, exact: true }).first().boundingBox())?.height ?? 0;
    await page.goto('/library/collections');
    await signIn(page);
    expect(await height('New smart collection')).toBeGreaterThanOrEqual(48);
    await page.goto('/library/movies');
    await expect(page.locator('.g-inline-error').getByRole('button', { name: 'Try again' })).toBeVisible();
    expect(await page.locator('.g-inline-error').getByRole('button', { name: 'Try again' }).boundingBox().then((box) => box?.height ?? 0)).toBeGreaterThanOrEqual(48);
    await page.goto('/settings/about');
    expect(await height('Visit TMDB')).toBeGreaterThanOrEqual(48);
  });
});
