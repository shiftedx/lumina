import { readFileSync } from 'node:fs';
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

import { mockApi, signIn } from './lumina-mock';

/** A standalone Books route, through the shell, on synthetic local archive data. */

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
type Viewport = { width: number; height: number };
const PAGE_PNG = readFileSync(new URL('./fixtures/books-page.png', import.meta.url));

const ebook = {
  id: 'ebook-1', kind: 'ebook', format: 'epub', title: 'Northbound', author: 'Mira Vale', description: 'A quiet voyage north.', language: 'en', genres: ['Adventure'],
  series_id: null, series_name: null, volume_number: null, reading_direction: 'ltr', cover_url: null, chapter_count: 2, page_count: 0,
  created_at: '2026-10-01T00:00:00Z', visibility: 'private', can_edit: true, available: true,
  progress: { unit_index: 0, offset: 0, percent: 0, completed: false, updated_at: null }, favorite: false, reading_list: false, shelf_ids: [],
};
const manga = {
  ...ebook, id: 'manga-1', kind: 'manga', format: 'cbz', title: 'Skybound, Volume 1', author: 'Rin Kato', description: 'A skyfaring manga.',
  series_id: 'series-1', series_name: 'Skybound', volume_number: 1, reading_direction: 'rtl', chapter_count: 0, page_count: 3,
};
const shelf = { id: 'shelf-1', name: 'Weekend reads', description: 'For slower mornings.', visibility: 'private', can_edit: true, book_ids: [ebook.id], count: 1, covers: [] };
const series = { id: 'series-1', title: 'Skybound', author: 'Rin Kato', description: 'A skyfaring manga.', cover_url: null, volume_count: 1, read_volume_count: 0, next_book_id: manga.id };
const overview = { totals: { ebooks: 1, manga_series: 1, volumes: 1 }, continue_reading: [], recently_added: [ebook], shelves: [shelf], discover: [manga] };
const json = (route: Route, body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });

const richEbooks = [
  ['ebook-2', 'The Lantern Coast', 'Sofia Maren'], ['ebook-3', 'A Map of Rain', 'Elliot Vale'], ['ebook-4', 'The Last Orchard', 'Noor Patel'],
  ['ebook-5', 'Salt and Starlight', 'Harriet Moss'], ['ebook-6', 'Window Seat', 'June Yamada'], ['ebook-7', 'A Quiet Atlas', 'Jon Bell'],
].map(([id, title, author], index) => ({ ...ebook, id, title, author, cover_url: `/api/books/${id}/cover`, genres: [index % 2 ? 'Literary' : 'Adventure'], progress: index < 3 ? { unit_index: index, offset: 0.35, percent: 25 + index * 20, completed: false, updated_at: '2026-10-02T00:00:00Z' } : ebook.progress }));
const richManga = [
  ['manga-2', 'Cloud Runner', 'Cloud Runner', 'series-2', 1], ['manga-3', 'Cloud Runner, Volume 2', 'Cloud Runner', 'series-2', 2],
  ['manga-4', 'Night Market', 'Night Market', 'series-3', 1], ['manga-5', 'Night Market, Volume 2', 'Night Market', 'series-3', 2],
  ['manga-6', 'Wild Signal', 'Wild Signal', 'series-4', 1], ['manga-7', 'Wild Signal, Volume 2', 'Wild Signal', 'series-4', 2],
].map(([id, title, seriesName, seriesId, volume]) => ({ ...manga, id, title, cover_url: `/api/books/${id}/cover`, series_name: seriesName, series_id: seriesId, volume_number: volume, author: volume === 1 ? 'Rin Kato' : 'Tala Rowan' }));
const richShelves = [
  { ...shelf, id: 'shelf-2', name: 'Rainy afternoons', description: 'Short novels and long tea breaks.', book_ids: ['ebook-2', 'ebook-5'], count: 2, covers: ['/api/books/ebook-2/cover', '/api/books/ebook-5/cover'] },
  { ...shelf, id: 'shelf-3', name: 'Shared favorites', description: 'A household shelf.', visibility: 'shared', book_ids: ['ebook-3', 'manga-2'], count: 2, covers: ['/api/books/ebook-3/cover', '/api/books/manga-2/cover'] },
  { ...shelf, id: 'shelf-4', name: 'After midnight', description: 'For the next chapter.', book_ids: ['ebook-4', 'manga-4'], count: 2, covers: ['/api/books/ebook-4/cover', '/api/books/manga-4/cover'] },
];
const richSeries = [
  { id: 'series-2', title: 'Cloud Runner', author: 'Rin Kato', description: 'A courier above the weather.', cover_url: '/api/books/manga-2/cover', volume_count: 2, read_volume_count: 0, next_book_id: 'manga-2' },
  { id: 'series-3', title: 'Night Market', author: 'Tala Rowan', description: 'A city after closing.', cover_url: '/api/books/manga-4/cover', volume_count: 2, read_volume_count: 1, next_book_id: 'manga-5' },
  { id: 'series-4', title: 'Wild Signal', author: 'Emi Cho', description: 'Messages from the mountain.', cover_url: '/api/books/manga-6/cover', volume_count: 2, read_volume_count: 0, next_book_id: 'manga-6' },
];
const richOverview = {
  totals: { ebooks: richEbooks.length, manga_series: richSeries.length, volumes: richManga.length }, continue_reading: richEbooks.slice(0, 3),
  recently_added: [...richEbooks, ...richManga], shelves: richShelves, discover: [...richManga.slice(0, 3), ...richEbooks.slice(3)],
};

async function mockBooks(page: Page) {
  await page.route('**/api/books/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;
    if (/\/(pages\/\d+|cover)$/.test(pathname)) return route.fulfill({ contentType: 'image/png', body: PAGE_PNG });
    if (pathname === '/api/books/overview') return json(route, overview);
    if (pathname === '/api/books') return json(route, { items: url.searchParams.get('kind') === 'manga' ? [manga] : [ebook], total: 1, next_cursor: null });
    if (pathname === '/api/books/series') return json(route, { items: [series], total: 1, next_cursor: null });
    if (pathname === '/api/books/shelves') return json(route, [shelf]);
    if (pathname === '/api/books/series/series-1') return json(route, { series, books: [manga] });
    if (pathname === '/api/books/manga-1/reader') return json(route, { book: manga, chapters: [], page_count: 3, reading_direction: 'rtl' });
    if (pathname === '/api/books/ebook-1/reader') return json(route, { book: ebook, chapters: [{ label: 'Departure' }, { label: 'The pass' }], page_count: 0, reading_direction: 'ltr' });
    if (pathname === '/api/books/ebook-1/chapters/0') return json(route, { nodes: [
      { tag: 'h2', text: 'Departure' },
      { tag: 'p', text: 'Morning light reached the ridge before it reached the valley floor, and the old road held the quiet of a world not yet awake.' },
      { tag: 'p', text: 'Northbound, Mira counted the familiar bends and kept the map folded in her pocket until the river opened into view.' },
      { tag: 'blockquote', text: 'Every journey begins twice: once in the mind, and once at the gate.' },
      { tag: 'p', text: 'By noon the train had crossed the last bridge. Beyond the glass, the coast was all pale sky, dark water, and possibilities.' },
    ] });
    if (pathname === '/api/books/ebook-1/chapters/1') return json(route, { nodes: [{ tag: 'h2', text: 'The pass' }, { tag: 'p', text: 'Wind traced the high path between the stones.' }] });
    if (pathname === '/api/books/manga-1/state' && request.method() === 'PATCH') return json(route, { ...manga, ...request.postDataJSON() });
    if (pathname === '/api/books/ebook-1/state' && request.method() === 'PATCH') return json(route, { ...ebook, ...request.postDataJSON() });
    if (pathname === '/api/books/ebook-1') return json(route, ebook);
    if (pathname === '/api/books/manga-1') return json(route, manga);
    return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: `books mock: ${request.method()} ${pathname}` }) });
  });
}

async function openBooks(page: Page, path: string, viewport = DESKTOP) {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  await mockBooks(page);
  await page.goto(path);
  await signIn(page);
}

async function openRichBooks(page: Page, path: string, viewport: Viewport) {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: true });
  await mockBooks(page);
  // These routes sit above the compact default fixture and keep the density review local to this spec.
  await page.route('**/api/books/overview', (route) => json(route, richOverview));
  await page.route(/\/api\/books(?:\?|$)/, (route) => json(route, { items: richEbooks, total: richEbooks.length, next_cursor: null }));
  await page.route(/\/api\/books\/series(?:\?|$)/, (route) => json(route, { items: richSeries, total: richSeries.length, next_cursor: null }));
  await page.goto(path);
  await signIn(page);
}

async function assertAccessibleAndNoOverflow(page: Page) {
  const results = await new AxeBuilder({ page }).include('main').analyze();
  expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical').map((violation) => violation.id)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
}

async function impeccableReview(page: Page, label: string) {
  const detector = process.env.IMPECCABLE_DETECT_URL;
  if (!detector) return;
  const messages: string[] = [];
  const onConsole = (message: { text: () => string }) => { if (message.text().includes('[impeccable]')) messages.push(message.text()); };
  page.on('console', onConsole);
  await page.evaluate(() => {
    document.title = '[Human] Books QA';
    document.head.appendChild(document.createElement('script'));
  });
  await page.addScriptTag({ url: detector });
  await page.waitForTimeout(2500);
  const findings = await page.evaluate(() => (window as typeof window & { impeccableDetect?: () => unknown }).impeccableDetect?.() ?? []);
  page.off('console', onConsole);
  console.log(`[impeccable][books:${label}] ${JSON.stringify({ console: messages, findings })}`);
}

test('Books deep link has its own navigation and reaches a manga reader without Library lenses', async ({ page }) => {
  await openBooks(page, '/books');
  await expect(page.getByRole('heading', { level: 1, name: 'Books' })).toBeVisible();
  const tabs = page.getByRole('navigation', { name: 'Books' });
  await expect(tabs.getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/books');
  await expect(tabs.getByRole('link', { name: 'Ebooks' })).toHaveAttribute('href', '/books/ebooks');
  await expect(tabs.getByRole('link', { name: 'Manga' })).toHaveAttribute('href', '/books/manga');
  await expect(tabs.getByRole('link', { name: 'Shelves' })).toHaveAttribute('href', '/books/shelves');
  await expect(page.locator('.books-surface').getByRole('navigation', { name: 'Library' })).toHaveCount(0);
  await expect(page.getByText(/sample catalog/i)).toHaveCount(0);
  await assertAccessibleAndNoOverflow(page);
  await page.screenshot({ path: '../output/playwright/books/overview-1440.png', fullPage: true });
  const additions = await page.locator('.books-overview-additions').boundingBox();
  const shelves = await page.locator('.books-overview-shelves').boundingBox();
  const discover = await page.locator('.books-overview-discover').boundingBox();
  expect(Math.abs(additions!.y - shelves!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(shelves!.x - discover!.x)).toBeLessThanOrEqual(1);
  expect(discover!.y).toBeGreaterThan(shelves!.y);
  const fallbackTitle = await page.locator('.books-overview-additions .books-cover-fallback > strong').first().evaluate((title) => {
    const styles = getComputedStyle(title);
    return { height: title.getBoundingClientRect().height, lineHeight: Number.parseFloat(styles.lineHeight) };
  });
  expect(fallbackTitle.height).toBeLessThanOrEqual(fallbackTitle.lineHeight + 1);

  await tabs.getByRole('link', { name: 'Manga' }).click();
  await expect(page).toHaveURL(/\/books\/manga$/);
  await page.getByRole('button', { name: /Open Skybound, 1 volume/ }).click();
  await expect(page).toHaveURL(/\/books\/series\/series-1$/);
  await page.getByRole('button', { name: 'Read volume 1' }).click();
  await expect(page).toHaveURL(/\/books\/read\/manga-1$/);
  await expect(page.getByRole('heading', { level: 1, name: /Books \/ Skybound, Volume 1/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0);
  await assertAccessibleAndNoOverflow(page);
  await page.screenshot({ path: '../output/playwright/books/reader-1440.png' });
  await page.getByRole('button', { name: 'Back to Books' }).click();
  await expect(page).toHaveURL(/\/books\/series\/series-1$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Skybound' })).toBeVisible();
});

for (const width of [320, 390]) {
  test(`Books overview fits a ${width}px phone viewport`, async ({ page }) => {
    await openBooks(page, '/books', { ...PHONE, width });
    await expect(page.getByRole('heading', { level: 1, name: 'Books' })).toBeVisible();
    await assertAccessibleAndNoOverflow(page);
    await page.screenshot({ path: `../output/playwright/books/overview-${width}.png`, fullPage: true });
    await impeccableReview(page, `sparse-overview-phone-${width}`);
  });
}

test('ebook reader keeps its phone controls usable and closes both sidebars', async ({ page }) => {
  await openBooks(page, '/books/read/ebook-1', PHONE);
  await expect(page.getByRole('heading', { level: 1, name: 'Northbound' })).toBeVisible();
  await expect(page.getByText('Morning light reached the ridge')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Chapters' }).click();
  await expect(page.getByRole('complementary', { name: 'Table of contents' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('complementary', { name: 'Table of contents' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await page.getByRole('button', { name: 'Sepia' }).click();
  await page.getByRole('button', { name: 'Larger text' }).click();
  await page.getByRole('button', { name: 'Two columns' }).click();
  await expect(page.locator('.books-reader')).toHaveAttribute('data-appearance', 'sepia');
  await expect(page.locator('.books-reader')).toHaveAttribute('style', /--book-font-size: 20px/);
  await expect(page.locator('.books-prose')).toHaveClass(/is-spread/);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('complementary', { name: 'Reading appearance' })).toHaveCount(0);
  expect(await page.locator('.books-reader-header button, .books-reader-footer button').evaluateAll((controls) => controls.flatMap((control) => {
    const height = control.getBoundingClientRect().height;
    return height === 0 || height >= 44 ? [] : [`${control.getAttribute('aria-label') ?? control.textContent?.trim()}: ${height}px`];
  }))).toEqual([]);
  await assertAccessibleAndNoOverflow(page);
  await page.screenshot({ path: '../output/playwright/books/ebook-reader-390.png', fullPage: true });
});

test('ebook reader offers a legible sepia desktop page', async ({ page }) => {
  await openBooks(page, '/books/read/ebook-1');
  await expect(page.getByText('Every journey begins twice: once in the mind')).toBeVisible();
  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await page.getByRole('button', { name: 'Sepia' }).click();
  await page.getByRole('button', { name: 'Larger text' }).click();
  await page.getByRole('button', { name: 'Two columns' }).click();
  await page.getByRole('button', { name: 'Close reading appearance' }).click();
  const spreadProse = await page.locator('.books-prose').boundingBox();
  const spreadControls = await page.locator('.books-reader-footer-controls').boundingBox();
  expect(spreadProse).not.toBeNull();
  expect(spreadControls).not.toBeNull();
  expect(Math.abs((spreadProse!.x + spreadProse!.width / 2) - (spreadControls!.x + spreadControls!.width / 2))).toBeLessThanOrEqual(24);
  expect(spreadControls!.width).toBeLessThanOrEqual(840);
  expect(spreadControls!.x).toBeGreaterThanOrEqual(spreadProse!.x);
  expect(spreadControls!.x + spreadControls!.width).toBeLessThanOrEqual(spreadProse!.x + spreadProse!.width);
  await page.getByRole('button', { name: 'Reading appearance' }).click();
  await page.getByRole('button', { name: 'Single' }).click();
  await page.getByRole('button', { name: 'Close reading appearance' }).click();
  const singleProse = await page.locator('.books-prose').boundingBox();
  const singleControls = await page.locator('.books-reader-footer-controls').boundingBox();
  expect(singleProse).not.toBeNull();
  expect(singleControls).not.toBeNull();
  expect(Math.abs((singleProse!.x + singleProse!.width / 2) - (singleControls!.x + singleControls!.width / 2))).toBeLessThanOrEqual(24);
  await assertAccessibleAndNoOverflow(page);
  await page.screenshot({ path: '../output/playwright/books/ebook-reader-sepia-1440.png', fullPage: true });
  await impeccableReview(page, 'reader-sepia-desktop');
});

for (const [label, viewport] of [['1440', DESKTOP], ['390', PHONE]] as const) {
  test(`rich Books review at ${label}px shows practical collection density`, async ({ page }) => {
    for (const [path, heading, capture] of [
      ['/books', 'Books', 'rich-overview'], ['/books/manga', 'Manga', 'rich-manga'], ['/books/shelves', 'Your shelves', 'rich-shelves'],
    ] as const) {
      await openRichBooks(page, path, viewport);
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
      await assertAccessibleAndNoOverflow(page);
      await page.screenshot({ path: `../output/playwright/books/${capture}-${label}.png`, fullPage: true });
      if (capture === 'rich-overview' && label === '1440') await impeccableReview(page, 'rich-overview-desktop');
    }
  });
}

for (const [name, viewport] of [['desktop', DESKTOP], ['phone', PHONE]] as const) {
  test(`book detail presents bundled files and readable synopsis on ${name}`, async ({ page }, testInfo) => {
    await openBooks(page, '/books', viewport);
    const bundled = { ...ebook, cover_url: '/api/books/ebook-1/cover', description: 'The old road held the quiet of a world not yet awake. Northbound, Mira kept her map folded until the river opened into view.\n\nBeyond the last bridge, the coast was pale sky, dark water, and possibilities.', filename: 'Northbound - Mira Vale.epub', canonical_book_id: 'ebook-1', reading_file_id: 'ebook-1', files: [
      { id: 'ebook-1', filename: 'Northbound - Mira Vale.epub', format: 'epub', chapter_count: 2, page_count: 0, available: true },
      { id: 'pdf-1', filename: 'Northbound - Mira Vale.pdf', format: 'pdf', chapter_count: 0, page_count: 30, available: true },
    ] };
    await page.route('**/api/books/ebook-1', (route) => json(route, bundled));
    await page.route('**/api/books/ebook-1/cover', (route) => route.fulfill({ contentType: 'image/png', body: PAGE_PNG }));
    await page.route(/\/api\/books(?:\?|$)/, (route) => json(route, { items: [{ ...ebook, id: 'ebook-2', title: 'After the Storm', cover_url: '/api/books/ebook-1/cover' }], total: 1, next_cursor: null }));
    await page.goto('/books/title/ebook-1');
    await expect(page.getByRole('heading', { level: 1, name: 'Northbound' })).toBeVisible();
    await expect(page.getByText('Northbound - Mira Vale.epub', { exact: true })).toBeVisible();
    await expect(page.getByText('Northbound - Mira Vale.pdf', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Read PDF', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'More by Mira Vale' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`books-detail-${name}.png`), fullPage: true });
  });
}
