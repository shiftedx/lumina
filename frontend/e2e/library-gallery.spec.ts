import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { type LibraryItemsMock, mockLibraryItems, mockTitleDetails } from './library-mock';
import { mockApi, signIn } from './lumina-mock';

/** The Library shell over the mocked API. */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const TEN_FOOT = { width: 1920, height: 1080 };
type Viewport = typeof DESKTOP;

async function openLibrary(page: Page, path = '/library', viewport: Viewport = DESKTOP, scheme: 'light' | 'dark' = 'light', items: LibraryItemsMock = {}) {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  const titles = await mockGalleryWall(page);
  const sections = await mockLibrarySections(page, items.deleted ? { deleted: items.deleted } : {});
  const library = await mockLibraryItems(page, items);
  await page.goto(path);
  await signIn(page);
  await expect(page.locator('.surface.gallery h1').first()).toBeVisible();
  return { titles, sections, library };
}
const tabs = (page: Page) => page.getByRole('navigation', { name: 'Library' });
const focusedIn = (page: Page, selector: string) => page.evaluate((within) => Boolean(document.activeElement?.closest(within)), selector);

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE, TEN_FOOT]) {
    test(`All landing in ${scheme} at ${viewport.width}px: masthead, spotlight, chapters, four requests, no sideways scroll`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      const aborted: string[] = []; // React StrictMode (dev server) mounts twice and aborts the first round: not requests the landing made
      page.on('requestfailed', (request) => aborted.push(request.url()));
      const { titles, sections, library } = await openLibrary(page, '/library', viewport, scheme);
      await expect(page.locator('.g-all .g-kicker')).toHaveText('1,735 movies · 812 shows · 29 anime · 110 albums · 318 videos · 12 recordings');
      await expect(page.locator('.g-spotlight .g-feature')).toHaveCount(3);
      for (const name of ['Movies', 'Shows', 'Anime', 'Music', 'YouTube', 'Recordings', 'Collections']) await expect(page.getByRole('heading', { level: 2, name, exact: true })).toBeAttached();
      await expect(page.locator('#g-chapter-movies .g-poster').first()).toBeVisible();
      await expect(page.locator('#g-chapter-shows .g-poster').first()).toBeVisible();
      await expect(page.locator('#g-chapter-movies .g-chapter-row')).toHaveCount(2);
      if (viewport === PHONE) {
        await expect(page.locator('#g-chapter-movies .g-chapter-row').first().locator('.g-poster')).toHaveCount(3);
        await expect(page.locator('#g-chapter-movies .g-caption')).toHaveCount(0);
      }
      if (viewport === TEN_FOOT) await expect(page.locator('.g-all')).toHaveCSS('padding-left', '72px');
      await page.waitForTimeout(500); // anything else the landing would ask for before a scroll
      const landing = [...titles.filter((url) => url.pathname === '/api/titles'), ...sections, ...library.requests.filter((url) => url.searchParams.has('kind'))]
        .filter((url) => { const index = aborted.indexOf(url.href); if (index >= 0) aborted.splice(index, 1); return index < 0; });
      // On a phone the third chapter already sits within the one-viewport margin, so it loads too.
      expect(landing.map((url) => `${url.pathname}${url.search}`), 'sections, spotlight and two chapter slices (criterion 2)').toHaveLength(viewport === PHONE ? 5 : 4);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `../output/playwright/library/all-${scheme}-${viewport.width}.png` });
      expect(errors).toEqual([]);
    });
  }
}

test('lazy chapters load as they near the viewport; with reduced motion nothing animates', async ({ page }) => {
  const { library } = await openLibrary(page);
  expect(library.requests.filter((url) => url.searchParams.get('kind') === 'video')).toHaveLength(0);
  await page.locator('#g-chapter-youtube').scrollIntoViewIfNeeded();
  await expect(page.locator('#g-chapter-youtube').getByRole('button', { name: /^Video 0, / })).toBeVisible();
  await expect(page.locator('#g-chapter-youtube .g-still-caption').first()).toContainText('Video 0');
  await expect(page.locator('.g-spotlight .g-art-image').first()).toHaveCSS('transition-duration', '0s');
});

test('tabs, See all, the /music address and Back to the All landing where it was left', async ({ page }) => {
  await openLibrary(page);
  await mockTitleDetails(page);
  await expect(tabs(page).getByRole('link')).toHaveText(['All', 'Movies', 'Shows', 'Anime', 'Music', 'YouTube', 'Recordings', 'Collections', 'Deleted']);
  await expect(tabs(page).getByRole('link', { name: 'All' })).toHaveAttribute('aria-current', 'page');
  await tabs(page).getByRole('link', { name: 'Shows' }).click();
  await expect(page).toHaveURL(/\/library\/shows$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Shows' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/library$/);
  await page.getByRole('link', { name: 'See all movies' }).click();
  await expect(page).toHaveURL(/\/library\/movies$/);
  await page.goBack();
  await page.locator('#g-chapter-anime').scrollIntoViewIfNeeded();
  const poster = page.locator('#g-chapter-anime .g-poster').first();
  await expect(poster).toBeVisible();
  await poster.focus();
  const scrolled = await page.evaluate(() => window.scrollY);
  expect(scrolled).toBeGreaterThan(300);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/title\//);
  await expect(page.getByRole('button', { name: 'Back to Library' })).toBeVisible(); // Back names where it goes (G9)
  await page.goBack();
  await expect(page).toHaveURL(/\/library$/);
  await expect(poster).toBeFocused();
  expect(Math.abs(await page.evaluate(() => window.scrollY) - scrolled)).toBeLessThanOrEqual(2);
  await page.goto('/music');
  await expect(page).toHaveURL(/\/library\/music$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Music' })).toBeVisible();
  await expect(tabs(page).getByRole('link', { name: 'Music' })).toHaveAttribute('aria-current', 'page');
});

test('YouTube: sort and source live in the address, pages load ahead, and a delete undoes', async ({ page }) => {
  const { library } = await openLibrary(page, '/library/youtube');
  await expect(page.locator('.g-still-wall .g-kicker')).toHaveText('318 videos · Sorted by recently added');
  await page.getByRole('combobox', { name: 'Sort' }).selectOption('title');
  await expect(page).toHaveURL(/\/library\/youtube\?sort=title$/);
  await page.getByRole('combobox', { name: 'Source' }).selectOption('twitch');
  await expect(page).toHaveURL(/\/library\/youtube\?sort=title&source=twitch$/);
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Source' })).toHaveValue('twitch');
  await page.goto('/library/youtube');
  await expect(page.getByRole('button', { name: /^Video 0, / })).toBeVisible(); // a wheel before the wall renders scrolls nothing
  await page.mouse.wheel(0, 20_000);
  await expect.poll(() => library.requests.some((url) => url.searchParams.get('cursor') === 'c60')).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0));
  const card = page.getByRole('button', { name: /^Video 0, / });
  await page.getByRole('button', { name: 'More options for Video 0' }).click();
  await page.getByRole('menuitem', { name: 'Delete from Lumina' }).click();
  await expect(page.locator('.g-toast').filter({ hasText: 'Deleted “Video 0”. It stays restorable for a while.' })).toBeVisible();
  await expect(card).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(card).toBeVisible();
  expect(library.calls).toEqual(['delete-file:video-0000', 'restore-file:video-0000']);
});

test('Enter on a tab or See all keeps keyboard focus on the new active tab', async ({ page }) => {
  await openLibrary(page);
  await tabs(page).getByRole('link', { name: 'Shows' }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/library\/shows$/);
  await expect(tabs(page).getByRole('link', { name: 'Shows' })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(tabs(page).getByRole('link', { name: 'Anime' })).toBeFocused();
  await page.goto('/library');
  await page.getByRole('link', { name: 'See all movies' }).focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/library\/movies$/);
  await expect(tabs(page).getByRole('link', { name: 'Movies' })).toBeFocused();
});

test('phone: the active tab starts in view without scrolling the page', async ({ page }) => {
  await openLibrary(page, '/library/recordings', PHONE);
  const tab = tabs(page).getByRole('link', { name: 'Recordings' });
  await expect.poll(async () => { const box = (await tab.boundingBox())!; return box.x >= 0 && box.x + box.width <= PHONE.width; }).toBe(true);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('arrow keys walk the YouTube wall', async ({ page }) => {
  await openLibrary(page, '/library/youtube');
  const first = page.getByRole('button', { name: /^Video 0, / });
  await first.focus();
  await page.keyboard.press('ArrowDown');
  await expect(first).not.toBeFocused();
  expect(await page.evaluate(() => document.activeElement?.closest('.g-still-wall') !== null && document.activeElement?.getAttribute('aria-label')?.startsWith('Video '))).toBe(true);
});

test('keyboard and D-pad walk through All', async ({ page }) => {
  await openLibrary(page);
  await expect(page.locator('#g-chapter-movies .g-poster').first()).toBeVisible();
  const all = tabs(page).getByRole('link', { name: 'All' });
  await all.focus();
  await page.keyboard.press('ArrowRight');
  await expect(tabs(page).getByRole('link', { name: 'Movies' })).toBeFocused();
  await page.keyboard.press('ArrowLeft');
  await expect(all).toBeFocused();
  await page.keyboard.press('ArrowDown');
  const tiles = page.locator('.g-spotlight .g-feature');
  await expect(tiles.nth(0)).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(tiles.nth(1)).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(tiles.nth(2)).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('link', { name: 'See all movies' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  expect(await focusedIn(page, '#g-chapter-movies .g-chapter-row')).toBe(true);
  await page.keyboard.press('Home');
  await expect(page.locator('#g-chapter-movies .g-chapter-row').first().locator('.g-poster').first()).toBeFocused();
  await page.keyboard.press('End');
  const last = page.locator('#g-chapter-movies .g-chapter-row').first().locator('.g-poster').last();
  await expect(last).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(last).toBeFocused();
  await page.keyboard.press('Control+Home');
  await expect(all).toBeFocused();
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`axe: All, YouTube, Recordings and Deleted in ${scheme} at ${viewport.width}px`, async ({ page }) => {
      await openLibrary(page, '/library', viewport, scheme, { deleted: 3 });
      const found: string[] = [];
      for (const path of ['/library', '/library/youtube', '/library/recordings', '/library/deleted']) {
        await page.goto(path);
        await expect(page.locator('.surface.gallery h1').first()).toBeVisible();
        await page.waitForTimeout(300);
        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
        for (const violation of results.violations.filter((entry) => entry.impact === 'serious' || entry.impact === 'critical')) found.push(`${path}: ${violation.id} → ${violation.nodes.slice(0, 3).map((node) => node.target.join(' ')).join(', ')}`);
        if (viewport === PHONE) {
          const small = await tabs(page).getByRole('link').evaluateAll((links) => links.map((link) => link.getBoundingClientRect()).filter((box) => box.height < 43.5).length);
          if (small) found.push(`${path}: ${small} tab links under 44px`);
        }
      }
      expect(found).toEqual([]);
    });
  }
}

test('Anime: a filtered wall survives a title and Back', async ({ page }) => {
  await openLibrary(page);
  await tabs(page).getByRole('link', { name: 'Anime' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Anime' })).toBeVisible();
  await expect(page.locator('.g-kicker')).toHaveText('24 titles · Sorted by recently added');
  await page.getByRole('button', { name: 'Filters' }).click();
  await page.getByRole('checkbox', { name: /^Drama/ }).check();
  await page.getByRole('dialog').getByRole('button', { name: /^Show/ }).click();
  await expect(page).toHaveURL(/\/library\/anime\?genre=Drama$/);
  await page.locator('.g-poster').first().click();
  await expect(page).toHaveURL(/\/title\//);
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/anime\?genre=Drama$/);
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeVisible();
});

test('Music: See all, the Artists view kept across a page, and an album opened on its artist goes back there', async ({ page }) => {
  await openLibrary(page);
  await mockTitleDetails(page);
  await page.locator('#g-chapter-music').scrollIntoViewIfNeeded();
  await expect(page.locator('#g-chapter-music .g-album').first()).toBeVisible();
  await page.getByRole('link', { name: 'See all music' }).click();
  await expect(page).toHaveURL(/\/library\/music$/);
  const views = page.getByRole('group', { name: 'Show' });
  await views.getByRole('button', { name: 'Artists' }).click();
  await expect(page).toHaveURL(/\/library\/music\?view=artists/);
  await page.locator('.g-album').first().click();
  await expect(page).toHaveURL(/\/title\/artist-0000$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/music\?view=artists/);
  await expect(views.getByRole('button', { name: 'Artists' })).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.g-album').first().click();
  await expect(page).toHaveURL(/\/title\/artist-0000$/);
  await page.locator('main .g-album').first().click();
  await expect(page).toHaveURL(/\/title\/album-of-artist-0000$/);
  await page.getByRole('button', { name: /^Back/ }).first().click(); // Back to the artist, not the Music tab
  await expect(page).toHaveURL(/\/title\/artist-0000$/);
  await views.waitFor({ state: 'detached' });
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/music\?view=artists/);
});

test('Music: saved audio in its own view, square', async ({ page }) => {
  await openLibrary(page, '/library/music');
  await page.getByRole('group', { name: 'Show' }).getByRole('button', { name: 'Saved audio' }).click();
  await expect(page).toHaveURL(/\/library\/music\?view=saved/);
  await expect(page.getByRole('button', { name: /^Track 0, / }).first()).toBeVisible();
  await expect(page.locator('.g-still-card.is-square .g-art-square').first()).toBeVisible();
});
