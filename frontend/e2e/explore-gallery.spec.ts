/** Browser: Explore in light and dark, desktop and phone, by keyboard alone. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { caps, CHANNEL_ID, liveItems, liveSnapshotBody, mockChannelPage, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockApi, signIn } from './lumina-mock';

const popular = {
  items: ['news', 'music', 'gaming'].flatMap((key) => Array.from({ length: 30 }, (_, index) => ({
    id: `${key}-${index}`, title: `Popular ${key} ${index + 1}`, uploader: 'Harbor Films', source: 'youtube', source_label: 'YouTube', kind: 'video', duration: 300 + index,
    webpage_url: `https://www.youtube.com/watch?v=${key}-${index}`, artwork_url: `/api/artwork/remote/${key}-${index}`, category_keys: [key], capabilities: caps('youtube', 'vod'),
  }))),
  categories: [{ key: 'news', label: 'News', state: 'ready' }, { key: 'music', label: 'Music', state: 'ready' }, { key: 'gaming', label: 'Gaming', state: 'ready' }],
  state: 'ready', refreshing: false, stale: false, error: null, last_success_at: '2026-09-29T20:30:00Z', refreshed_at: '2026-09-29T20:30:00Z',
};
const result = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
  id, title, uploader: 'Harbor Films', source: 'youtube', source_label: 'YouTube', kind: 'video', duration: 400, view_count: 12_000,
  webpage_url: `https://www.youtube.com/watch?v=${id}`, artwork_url: `/api/artwork/remote/${id}`, capabilities: caps('youtube', 'vod'), ...extra,
});
const results = [
  result(CHANNEL_ID, 'Harbor Films', { kind: 'channel', webpage_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, capabilities: caps('youtube', 'vod', { can_play: false }) }),
  { ...liveItems(1, {}, 'found')[0] },
  result('h1', 'A slow morning in the harbor'),
  result('h2', 'Harbor in sixty seconds', { kind: 'short', duration: 58 }),
  result('h4', 'Harbor walks, the full series', { kind: 'playlist', duration: null }),
];

async function open(page: Page, { width = 1440, height = 900, theme = 'dark' as 'light' | 'dark', path = '/streaming' } = {}) {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: theme });
  await page.setViewportSize({ width, height });
  await mockApi(page, { sidebar_collapsed: false, theme });
  await mockRemoteArtwork(page);
  await mockLive(page, liveSnapshotBody({ items: liveItems(10) }));
  await mockChannelPage(page);
  await page.route('**/api/discovery/popular', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(popular) }));
  await page.route((url) => url.pathname === '/api/source-search', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ query: 'harbor', items: results, errors: [{ source: 'soundcloud', message: 'SoundCloud is rate limiting searches right now.', retryable: true }] }),
  }));
  await page.route(/\/api\/library\/channels(\?|$)/, (route) => route.fulfill({ contentType: 'application/json', body: '[]' }));
  await page.goto(path);
  await signIn(page);
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`Explore reads cleanly in ${theme} at ${label} width`, async ({ page }) => {
      await open(page, { width, height, theme });
      await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'News' })).toBeVisible();
      const results = await new AxeBuilder({ page }).include('main').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/visual/explore-${theme}-${label}.png` });
    });
  }
}

test('the masthead, live teaser, 25 categories and popular rails', async ({ page }) => {
  await open(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Live now' })).toBeVisible();
  await expect(page.getByRole('link', { name: /See all/ }).first()).toHaveAttribute('href', '/streaming/live');
  await expect(page.getByRole('navigation', { name: 'Browse by category' }).getByRole('button')).toHaveCount(25);
  for (const name of ['News', 'Music', 'Gaming']) await expect(page.getByRole('heading', { name })).toBeVisible();
});

test('a search groups results in order, offers jump links and a source retry, and a channel result opens its page', async ({ page }) => {
  await open(page);
  const field = page.getByPlaceholder('Search YouTube');
  await field.fill('harbor');
  await field.press('Enter');
  await expect(page.getByRole('heading', { level: 2, name: 'Results for “harbor”' })).toBeVisible();
  const headings = await page.locator('.g-explore h2, .g-explore .g-rail-head h2, .g-explore [id^="rail-results-"]').allInnerTexts();
  const order = ['Channels', 'Live now', 'Videos', 'Shorts', 'Playlists'].map((name) => headings.findIndex((text) => text.trim() === name));
  expect(order.every((index) => index >= 0), headings.join(' | ')).toBe(true);
  expect([...order].sort((a, b) => a - b)).toEqual(order);
  await expect(page.getByRole('navigation', { name: 'Jump to' })).toBeVisible();
  await expect(page.getByRole('alert').filter({ hasText: 'SoundCloud results are unavailable' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry SoundCloud' })).toBeVisible();
  await page.getByRole('link', { name: /Harbor Films/ }).first().click();
  await expect(page).toHaveURL(new RegExp(`/channel/youtube/${CHANNEL_ID}$`));
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/streaming\/search\?q=harbor$/);
});

test('the search field is reached by keyboard and Enter searches', async ({ page }) => {
  await open(page);
  const field = page.getByPlaceholder('Search YouTube');
  await field.focus();
  await page.keyboard.type('harbor');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 2, name: 'Results for “harbor”' })).toBeVisible();
  await page.getByRole('link', { name: 'Clear' }).click();
  await expect(page).toHaveURL(/\/streaming$/);
});

test('Escape leaves a See all wall and returns to the rails', async ({ page }) => {
  await open(page);
  const seeAll = page.locator('section.g-rail:not([data-rail-key="live-now"]) .g-rail-all').first();
  await seeAll.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/streaming\?rail=/);
  await expect(page.locator('.g-rail-wall')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/streaming$/);
  await expect(page.locator('.g-rail-wall')).toHaveCount(0);
  await expect(page.locator('section.g-rail:not([data-rail-key="live-now"]) .g-rail-all').first()).toBeFocused();
});
