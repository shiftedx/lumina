import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { mockGalleryWall, mockLibrarySections } from './gallery-mock';
import { type LibraryItemsMock, mockLibraryItems, mockTitleDetails } from './library-mock';
import { navigateApp, mockApi, signIn } from './lumina-mock';
import { librarySections } from '../src/test/galleryFixtures';
import { chooseSelect } from './select-helper';

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
const tabs = (page: Page) => page.getByRole('navigation', { name: 'Library tools' });

for (const viewport of [DESKTOP, PHONE]) {
  test(`Library Refresh is visible and starts a rescan at ${viewport.width}px`, async ({ page }) => {
    const { titles, sections } = await openLibrary(page, '/library/shows?sort=name', viewport);
    let scans = 0;
    let refreshes = 0;
    await page.route('**/api/admin/library/automation/scan', async (route) => {
      scans += 1;
      expect(route.request().method()).toBe('POST');
      expect(route.request().postDataJSON()).toEqual({});
      await route.fulfill({ status: 202, json: { started: [{ root_id: 'r1', run_id: 'run-1' }], queued: [], skipped: [] } });
    });
    await page.route('**/api/library/refresh', async (route) => {
      refreshes += 1;
      await route.fulfill({ json: { items: [], next_cursor: null } });
    });
    const refresh = page.getByRole('button', { name: 'Refresh', exact: true });
    await expect(refresh).toBeVisible();
    const buttonBox = (await refresh.boundingBox())!;
    const tabsBox = (await tabs(page).boundingBox())!;
    expect(buttonBox.x + buttonBox.width).toBeLessThanOrEqual(viewport.width);
    expect(buttonBox.height).toBeGreaterThanOrEqual(44);
    expect(buttonBox.x).toBeGreaterThanOrEqual(tabsBox.x + tabsBox.width);
    const before = titles.length;
    const countsBefore = sections.length;
    await refresh.click();
    await expect(page.getByText('Library refreshed. Scanning 1 library', { exact: true })).toBeVisible();
    expect(scans).toBe(1);
    expect(refreshes).toBe(1);
    await expect.poll(() => titles.length).toBeGreaterThan(before);
    await expect.poll(() => sections.length).toBeGreaterThan(countsBefore);
    await expect(page).toHaveURL(/\/video\/shows\?sort=name$/);
    await expect(refresh).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `../output/playwright/library/refresh-${viewport.width}.png` });
  });
}

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE, TEN_FOOT]) {
    test(`Legacy Library opens Movies in ${scheme} at ${viewport.width}px without sideways scroll`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      const { titles } = await openLibrary(page, '/library', viewport, scheme);
      await expect(page).toHaveURL(/\/video\/movies$/);
      await expect(page.getByRole('heading', { name: 'Movies', level: 1 })).toBeVisible();
      await expect(page.locator('.g-browse-hero')).toBeVisible();
      await expect(tabs(page).getByRole('link')).toHaveText(['Collections', 'Deleted']);
      const catalog = titles.filter((url) => url.pathname === '/api/titles' && url.searchParams.has('category'));
      expect(catalog.length).toBeGreaterThan(0);
      expect(catalog.every((url) => url.searchParams.get('category') === 'movies')).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      expect(errors).toEqual([]);
    });
  }
}

test('saved videos are fetched when their library opens and reduced motion disables artwork transitions', async ({ page }) => {
  const { library } = await openLibrary(page);
  expect(library.requests.filter((url) => url.searchParams.get('kind') === 'video')).toHaveLength(0);
  await page.goto('/video/youtube');
  await expect(page.getByRole('button', { name: /^Video 0, / })).toBeVisible();
  await expect(page.locator('.g-still-card .g-art-image').first()).toHaveCSS('transition-duration', '0s');
});

test('Library navigation and browser Back preserve the previous category', async ({ page }) => {
  await openLibrary(page);
  await mockTitleDetails(page);
  await navigateApp(page, 'Shows');
  await expect(page).toHaveURL(/\/video\/shows$/);
  const spotlight = page.locator('.g-browse-hero');
  await expect(spotlight).toBeVisible();
  await spotlight.getByRole('button', { name: 'Details' }).press('Enter');
  await expect(page).toHaveURL(/\/video\/title\//);
  await page.goBack();
  await expect(page).toHaveURL(/\/video\/shows$/);
  await expect(spotlight.getByRole('button', { name: 'Details' })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/\/video\/movies$/);
  await page.goto('/music');
  await expect(page).toHaveURL(/\/audio$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Music' })).toBeVisible();
});

test('YouTube: sort and source live in the address, pages load ahead, and a delete undoes', async ({ page }) => {
  const { library } = await openLibrary(page, '/library/youtube');
  await expect(page.locator('.g-still-wall .g-kicker')).toHaveText('318 videos · Sorted by recently added');
  expect(library.requests.some((url) => url.searchParams.get('kind') === 'video' && url.searchParams.get('source') === 'saved' && url.searchParams.get('status') === 'available')).toBe(true);
  await chooseSelect(page, page.getByRole('combobox', { name: 'Source' }), 'Other web sources');
  await expect(page).toHaveURL(/source=other_web$/);
  await expect.poll(() => library.requests.some((url) => url.searchParams.get('source') === 'other_web')).toBe(true);
  await chooseSelect(page, page.getByRole('combobox', { name: 'Source' }), 'All web sources');
  await chooseSelect(page, page.getByRole('combobox', { name: 'Sort' }), 'Title A–Z');
  await expect(page).toHaveURL(/\/video\/youtube\?sort=title$/);
  await chooseSelect(page, page.getByRole('combobox', { name: 'Source' }), 'Twitch');
  await expect(page).toHaveURL(/\/video\/youtube\?sort=title&source=twitch$/);
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Source' })).toHaveText('Twitch');
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

async function openNavigation(page: Page) {
  await page.getByRole('button', { name: 'Open navigation' }).click();
  const menu = page.getByRole('dialog', { name: 'Navigation' });
  await expect(menu).toBeVisible();
  return menu;
}

test('Enter on a Library destination focuses the new category', async ({ page }) => {
  await openLibrary(page);
  const menu = await openNavigation(page);
  await menu.getByRole('button', { name: 'Shows', exact: true }).press('Enter');
  await expect(page).toHaveURL(/\/video\/shows$/);
  await expect(page.getByRole('heading', { name: 'Shows', level: 1 })).toBeFocused();
  await expect((await openNavigation(page)).getByRole('button', { name: 'Shows', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('phone: the active Library destination is visible without scrolling the page', async ({ page }) => {
  await openLibrary(page, '/library/recordings', PHONE);
  const menu = await openNavigation(page);
  const active = menu.getByRole('button', { name: 'Recordings', exact: true });
  await expect(active).toHaveAttribute('aria-current', 'page');
  await expect(active).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('the active Library destination remains visible when desktop shrinks to phone', async ({ page }) => {
  await openLibrary(page, '/library/recordings', DESKTOP);
  const menu = await openNavigation(page);
  const active = menu.getByRole('button', { name: 'Recordings', exact: true });
  await expect(active).toHaveAttribute('aria-current', 'page');
  await page.setViewportSize(PHONE);
  await expect(active).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('server navigation remains authoritative over cached section counts', async ({ page }) => {
  const cached = librarySections({ movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 0, recordings: 12 });
  await page.addInitScript((sections) => localStorage.setItem('lumina.sections.member-1', JSON.stringify(sections)), cached);
  await openLibrary(page, '/library/recordings', PHONE);
  const menu = await openNavigation(page);
  await expect(menu.getByRole('button', { name: 'Recordings', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(menu.getByRole('button', { name: 'Movies', exact: true })).toBeVisible();
});

test('phone: the Library menu scrolls through its full set of destinations', async ({ page }) => {
  await openLibrary(page, '/library', PHONE);
  const menu = await openNavigation(page);
  const movies = menu.getByRole('button', { name: 'Movies', exact: true });
  const downloads = menu.getByRole('button', { name: 'Downloads', exact: true });
  await expect(movies).toBeInViewport({ ratio: 1 });
  await downloads.scrollIntoViewIfNeeded();
  await expect(downloads).toBeInViewport({ ratio: 1 });
  expect((await downloads.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await movies.scrollIntoViewIfNeeded();
  await expect(movies).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test('arrow keys walk the YouTube wall', async ({ page }) => {
  await openLibrary(page, '/library/youtube');
  const first = page.getByRole('button', { name: /^Video 0, / });
  await first.focus();
  await page.keyboard.press('ArrowDown');
  await expect(first).not.toBeFocused();
  expect(await page.evaluate(() => document.activeElement?.closest('.g-still-wall') !== null && document.activeElement?.getAttribute('aria-label')?.startsWith('Video '))).toBe(true);
});

test('keyboard and D-pad walk the Library destinations', async ({ page }) => {
  await openLibrary(page);
  const trigger = page.getByRole('button', { name: 'Open navigation' });
  await trigger.press('Enter');
  const menu = page.getByRole('dialog', { name: 'Navigation' });
  await expect(menu.getByRole('button', { name: 'Home', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('button', { name: 'Movies', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('button', { name: 'Shows', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/video\/shows$/);
  await expect(page.getByRole('heading', { name: 'Shows', level: 1 })).toBeFocused();
});

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`axe: Movies, YouTube, Recordings and Deleted in ${scheme} at ${viewport.width}px`, async ({ page }) => {
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
  await navigateApp(page, 'Anime');
  await expect(page.getByRole('heading', { level: 1, name: 'Anime' })).toBeVisible();
  await expect(page.locator('.g-kicker')).toHaveText('24 titles · Sorted by recently added');
  await page.getByRole('button', { name: 'Filters' }).click();
  await page.getByRole('checkbox', { name: /^Drama/ }).check();
  await page.getByRole('dialog').getByRole('button', { name: /^Show/ }).click();
  await expect(page).toHaveURL(/\/video\/anime\?genre=Drama$/);
  await page.locator('.g-poster').first().click();
  await expect(page).toHaveURL(/\/video\/title\//);
  await page.goBack();
  await expect(page).toHaveURL(/\/video\/anime\?genre=Drama$/);
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeVisible();
});

test('Music: See all, the Artists view kept across a page, and an album opened on its artist goes back there', async ({ page }) => {
  await openLibrary(page, '/library/music');
  await mockTitleDetails(page);
  await expect(page).toHaveURL(/\/audio$/);
  const views = page.getByRole('group', { name: 'Show' });
  await views.getByRole('button', { name: 'Artists' }).click();
  await expect(page).toHaveURL(/\/audio\?view=artists/);
  await page.locator('.g-album').first().click();
  await expect(page).toHaveURL(/\/audio\/title\/artist-0000$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/audio\?view=artists/);
  await expect(views.getByRole('button', { name: 'Artists' })).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.g-album').first().click();
  await expect(page).toHaveURL(/\/audio\/title\/artist-0000$/);
  await page.locator('main .g-album').first().click();
  await expect(page).toHaveURL(/\/audio\/title\/album-of-artist-0000$/);
  await page.getByRole('button', { name: /^Back/ }).first().click(); // Back to the artist, not the Music tab
  await expect(page).toHaveURL(/\/audio\/title\/artist-0000$/);
  await views.waitFor({ state: 'detached' });
  await page.goBack();
  await expect(page).toHaveURL(/\/audio\?view=artists/);
});

test('Music: saved audio in its own view, square', async ({ page }) => {
  await openLibrary(page, '/library/music');
  await page.getByRole('button', { name: 'Saved audio', exact: true }).click();
  await expect(page).toHaveURL(/\/audio\?view=saved/);
  await expect(page.getByRole('button', { name: /^Track 0, / }).first()).toBeVisible();
  await expect(page.locator('.g-still-card.is-square .g-art-square').first()).toBeVisible();
});
