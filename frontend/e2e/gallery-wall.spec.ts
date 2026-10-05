import { expect, test, type Page } from '@playwright/test';

import type { LocalSearchResponse } from '../src/types';
import { type GalleryWallMock, mockGalleryWall, wallTitles } from './gallery-mock';
import { item, mockApi, signIn } from './lumina-mock';

/** The Movies and Shows walls over the mocked API. */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
type Viewport = typeof DESKTOP;

async function openWall(page: Page, path = '/library/movies', options: GalleryWallMock = {}, viewport: Viewport = DESKTOP, scheme: 'light' | 'dark' = 'light') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  const requests = await mockGalleryWall(page, options);
  await page.goto(path);
  await signIn(page);
  await expect(page.locator('.g-wall h1')).toBeVisible();
  return requests;
}
const focusedName = (page: Page) => page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? '');
const loader = (page: Page) => page.evaluate(() => (window as unknown as { __luminaImageLoader: { stats: () => { cap: number; maxInFlight: number; dropped: number } } }).__luminaImageLoader.stats());

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of [DESKTOP, PHONE]) {
    test(`walls in ${scheme} at ${viewport.width}px: masthead, markers, few DOM rows, screenshots`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await openWall(page, '/library/movies', {}, viewport, scheme);
      await expect(page.locator('.g-kicker')).toHaveText('300 titles · Sorted by recently added');
      await expect(page.locator('.g-poster').first()).toBeVisible();
      await expect(page.locator('.g-marker-triangle').first()).toBeAttached();
      const rows = await page.locator('.g-row').count();
      const inView = await page.evaluate(() => [...document.querySelectorAll('.g-row')].filter((row) => { const box = row.getBoundingClientRect(); return box.bottom > 0 && box.top < innerHeight; }).length);
      expect(rows).toBeLessThanOrEqual(inView + 4); // ± 3 rows, plus one whose top touches the viewport's bottom edge
      if (viewport === PHONE) {
        const columns = await page.locator('.g-row[data-row="1"] .g-poster').evaluateAll((cells) => new Set(cells.map((cell) => Math.round(cell.getBoundingClientRect().left))).size);
        expect(columns).toBe(3);
        await expect(page.locator('.g-feature')).toHaveCount(0);
        await expect(page.locator('.g-caption').first()).toBeHidden();
      }
      await page.screenshot({ path: `../output/playwright/gallery/movies-${scheme}-${viewport.width}.png` });
      await page.goto('/library/shows');
      await expect(page.getByRole('heading', { level: 1, name: 'Shows' })).toBeVisible();
      await expect(page.locator('.g-marker-count').first()).toBeAttached();
      await page.screenshot({ path: `../output/playwright/gallery/shows-${scheme}-${viewport.width}.png` });
      expect(errors).toEqual([]);
    });
  }
}

test('chips and filters live in the address, keep focus and announce the count', async ({ page }) => {
  const requests = await openWall(page);
  const chip = page.getByRole('button', { name: 'Unwatched', exact: true });
  await chip.click();
  await expect(page).toHaveURL(/\/library\/movies\?unwatched=1$/);
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(chip).toBeFocused();
  await expect(page.locator('.g-wall > [role="status"]')).toHaveText('150 titles');
  expect(requests.some((url) => url.pathname === '/api/titles' && url.searchParams.get('unwatched') === 'true')).toBe(true);
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: 'Filters' });
  await drawer.getByRole('checkbox', { name: /^Drama/ }).check();
  await drawer.getByRole('button', { name: /^Show \d/ }).click();
  await expect(page).toHaveURL(/genre=Drama/);
  const filters = page.getByRole('button', { name: 'Filters (1)' });
  await expect(filters).toBeFocused();
  await filters.click();
  await expect(drawer).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(filters).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/movies\?unwatched=1$/);
});

test('phone: the chips and filters live in a bottom sheet', async ({ page }) => {
  await openWall(page, '/library/movies', {}, PHONE);
  await expect(page.getByRole('group', { name: 'Quick filters' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  const sheet = page.getByRole('dialog', { name: 'Filters' });
  await expect(sheet).toBeVisible();
  const box = await sheet.boundingBox();
  expect(Math.round(box!.y + box!.height)).toBe(PHONE.height);
  const chips = sheet.locator('fieldset').first();
  await chips.getByRole('button', { name: 'Unwatched' }).click();
  await sheet.getByRole('button', { name: /^Show/ }).click();
  await expect(page).toHaveURL(/\?unwatched=1$/);
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeFocused();
});

test('the A–Z rail appears for Title A–Z and focuses the first title of a letter', async ({ page }) => {
  await openWall(page, '/library/movies?sort=name');
  const rail = page.getByRole('navigation', { name: 'Jump to letter' });
  await expect(rail.getByRole('button')).toHaveCount(27);
  await expect(rail.getByRole('button', { name: '#', exact: true })).toHaveAttribute('aria-disabled', 'true');
  await rail.getByRole('button', { name: 'M', exact: true }).click();
  await expect.poll(() => focusedName(page)).toMatch(/^M Title \d+,/);
  await page.keyboard.press('t');
  await expect.poll(() => focusedName(page)).toMatch(/^T Title \d+,/);
  await page.getByRole('combobox', { name: 'Sort' }).selectOption('year');
  await expect(rail).toHaveCount(0);
});

test('keyboard only: Down from the heading into the grid, arrows move, Up reaches Filters, Enter opens', async ({ page }) => {
  await openWall(page, '/library/movies?sort=name');
  await page.locator('.g-wall h1').evaluate((heading: HTMLElement) => { heading.tabIndex = -1; heading.focus(); });
  await page.keyboard.press('ArrowDown');
  await expect.poll(() => focusedName(page)).toMatch(/^A Title 0,/);
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => focusedName(page)).toMatch(/^A Title 1,/);
  await page.keyboard.press('ArrowUp');
  await expect(page.getByRole('button', { name: /^Filters/ })).toBeFocused(); // never the Sort select, which would eat Up and Down
  await page.keyboard.press('ArrowLeft'); // Left and Right walk the toolbar
  await expect(page.getByRole('searchbox', { name: 'Find the one where…' })).toBeFocused();
  await page.locator('.g-grid button[tabindex="0"]').focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/title\/movie-0001$/);
});

test('"Find the one where…" searches on Enter and opens the player at the moment', async ({ page }) => {
  const movie = wallTitles('movie', 300)[42];
  const hit = { kind: 'title', id: movie.id, title: movie.name, subtitle: String(movie.year), score: 1, lexical_score: 1, semantic_score: 0, match_mode: 'lexical', title_id: movie.id, media_title: movie };
  const moment = { ...hit, kind: 'moment', id: `${item.id}@761000`, subtitle: 'At 12:41', item, start_ms: 761_000 };
  const search = { query: 'lamp', mode: 'lexical', matches: [hit, moment], items: [item], index_generation: 1 } as unknown as LocalSearchResponse;
  const requests = await openWall(page, '/library/movies', { search });
  const field = page.getByRole('searchbox', { name: 'Find the one where…' });
  await field.fill('lamp');
  await page.waitForTimeout(400);
  expect(requests.some((url) => url.pathname === '/api/search')).toBe(false);
  await field.press('Enter');
  await expect(page).toHaveURL(/\/library\/movies\?q=lamp$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Results for “lamp”' })).toBeVisible();
  expect(requests.find((url) => url.pathname === '/api/search')?.searchParams.get('scope')).toBe('movies');
  await expect(page.getByRole('button', { name: new RegExp(`^${movie.name},`) })).toBeVisible();
  await page.getByRole('searchbox', { name: 'Find the one where…' }).press('Escape');
  await expect(page).toHaveURL(/\/library\/movies$/);
  await field.fill('lamp');
  await field.press('Enter');
  // lumina-mock's clip is 1.5 s long, and a start that close to the end restarts at 0 (as in gallery-title.spec).
  await page.route(`**/api/library/${item.id}/playback-options*`, (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ mode: 'direct', reason: null, facts: { container: 'mp4', video_codec: 'h264', audio_codec: 'aac', width: 160, height: 90, duration: 6720 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 2 }),
  }));
  await page.getByRole('button', { name: `Play ${movie.name} from 12:41` }).click();
  // The player starts at the moment (as in gallery-title.spec); the time then leaves the address.
  await expect.poll(() => page.locator('video').first().getAttribute('src')).toMatch(new RegExp(`/api/library/${item.id}/media#t=761$`));
});

// Back from a title page (filters, sort, scroll, focused poster) and the member switch are covered in v1_library_ui.spec.ts.

test('loads at most four images at a time and drops loads for rows flung past', async ({ page }) => {
  await openWall(page, '/library/movies?sort=name', { total: 1200, artDelayMs: 300 });
  await expect.poll(async () => (await loader(page)).maxInFlight).toBe(4);
  for (let step = 0; step < 20; step += 1) {
    await page.mouse.wheel(0, DESKTOP.height);
    await page.waitForTimeout(200);
  }
  const stats = await loader(page);
  expect(stats.cap).toBe(4);
  expect(stats.maxInFlight).toBeLessThanOrEqual(4);
  expect(stats.dropped).toBeGreaterThan(0);
});

test('reduced motion turns every gallery transition off', async ({ page }) => {
  await openWall(page);
  await expect(page.locator('.g-art-image').first()).toHaveCSS('transition-duration', '0s');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(page.locator('.g-art-image').first()).toHaveCSS('transition-duration', '0.4s');
});

test('headlines use the real Newsreader italic, not a synthesised slant', async ({ page }) => {
  await openWall(page);
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => [...document.fonts].some((face) => face.family.replace(/['"]/g, '') === 'Newsreader' && face.style === 'italic' && face.status === 'loaded'))).toBe(true);
});

test('a page that fails to load says so and offers Try again', async ({ page }) => {
  const requests = await openWall(page, '/library/movies', { failTitles: true });
  const error = page.locator('.g-inline-error');
  await expect(error).toContainText('Lumina could not load these titles.');
  const asked = requests.filter((url) => url.pathname === '/api/titles').length;
  await error.getByRole('button', { name: 'Try again' }).click();
  await expect.poll(() => requests.filter((url) => url.pathname === '/api/titles').length).toBeGreaterThan(asked);
  await expect(error).toBeVisible();
});

test('a search that fails says so and offers Try again', async ({ page }) => {
  const requests = await openWall(page, '/library/movies?q=lamp', { search: 'fail' });
  await expect(page.getByText('Search is unavailable right now.')).toBeVisible();
  const asked = requests.filter((url) => url.pathname === '/api/search').length;
  await page.locator('.g-results').getByRole('button', { name: 'Try again' }).click();
  await expect.poll(() => requests.filter((url) => url.pathname === '/api/search').length).toBeGreaterThan(asked);
});

for (const width of [390, 320]) {
  test(`phone at ${width}px: the tabs and the toolbar each keep to one line`, async ({ page }) => {
    await openWall(page, '/library/movies?genre=Drama', {}, { width, height: 844 });
    const lenses = page.getByRole('navigation', { name: 'Library' });
    const lensBox = await lenses.boundingBox();
    const lensTops = await lenses.getByRole('link').evaluateAll((links) => links.map((link) => link.getBoundingClientRect().top));
    expect(new Set(lensTops.map(Math.round)).size).toBe(1);
    for (const name of ['Movies', 'Shows']) {
      const tab = await lenses.getByRole('link', { name, exact: true }).boundingBox();
      expect(tab!.x + tab!.width).toBeLessThanOrEqual(lensBox!.x + lensBox!.width);
    }
    const toolbar = page.locator('.g-toolbar');
    const tops = await toolbar.locator(':scope > *').evaluateAll((children) => children.map((child) => { const box = child.getBoundingClientRect(); return Math.round(box.top + box.height / 2); }));
    expect(new Set(tops).size).toBe(1);
    const filters = page.getByRole('button', { name: 'Filters (1)' });
    expect(await filters.boundingBox()).toMatchObject({ width: 44, height: 44 });
    await expect(filters.locator('.g-filters-count')).toHaveText('1');
    await expect(page.locator('.g-sort .g-label')).toHaveCSS('width', '1px');
    await expect(page.getByRole('combobox', { name: 'Sort' })).toBeVisible();
  });
}

for (const without of [['backdrop'], ['backdrop', 'poster']] as const) {
  test(`a feature tile without ${without.join(' or ')} names its title once, over ${without.length === 1 ? 'the poster' : 'a bare colour field'}`, async ({ page }) => {
    await openWall(page, '/library/movies', { without: [...without] });
    const tile = page.locator('.g-feature').first();
    await expect(tile).toBeVisible();
    const name = (await tile.locator('.g-feature-name').textContent()) ?? '';
    expect(name).toMatch(/^A Title 0$/);
    await expect(tile.getByText(name, { exact: true })).toHaveCount(1);
    await expect(tile.locator('.g-card')).toHaveCount(0);
    await expect(tile.locator('.g-feature-copy')).toHaveCSS('background-image', /linear-gradient/);
    if (without.length === 1) await expect(tile.locator('.g-art-poster img.g-art-image.is-shown')).toHaveCount(1);
    else await expect(tile.locator('img.g-art-image')).toHaveCount(0);
  });
}

test('dark: the feature tile keeps a hairline bottom edge; the current tab is underlined in gold', async ({ page }) => {
  await openWall(page, '/library/movies', {}, DESKTOP, 'dark');
  await expect(page.locator('.g-feature').first()).toBeVisible();
  expect(await page.locator('.g-feature').first().evaluate((tile) => getComputedStyle(tile, '::after').boxShadow)).toContain('0px -1px');
  const gold = await page.locator('.gallery').evaluate((wall) => getComputedStyle(wall).getPropertyValue('--g-gold').trim());
  const underline = await page.getByRole('navigation', { name: 'Library' }).getByRole('link', { name: 'Movies', exact: true }).evaluate((tab) => getComputedStyle(tab).textDecorationColor);
  expect(underline).toBe(await page.evaluate((hex) => { const probe = document.createElement('i'); probe.style.color = hex; document.body.append(probe); const colour = getComputedStyle(probe).color; probe.remove(); return colour; }, gold));
  await expect(page.getByRole('combobox', { name: 'Sort' })).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
});
