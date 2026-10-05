import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Multi-source Discover results (mocked API). */

const caps = { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } };
const result = (id: string, title: string, extra: Record<string, unknown> = {}) => ({ id, title, uploader: 'Harbour Films', duration: 400, view_count: 12000, source: 'youtube', source_label: 'YouTube', kind: 'video', webpage_url: `https://www.youtube.com/watch?v=${id}`, capabilities: caps, ...extra });
const harbour = [
  result('h1', 'A slow morning in the old harbour, filmed from the lighthouse over one long patient take'),
  result('h2', 'Harbour in sixty seconds', { kind: 'short', duration: 58 }),
  result('h3', 'Harbour sounds, a field recording', { source: 'soundcloud', source_label: 'SoundCloud', duration: 1800, webpage_url: 'https://soundcloud.com/demo/harbour', capabilities: { ...caps, provider: 'soundcloud' } }),
  result('h4', 'Harbour walks — the full series', { kind: 'playlist', duration: null }),
];

async function openDiscover(page: Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.route((url) => url.pathname === '/api/source-search', async (route) => {
    const { query } = route.request().postDataJSON() as { query: string };
    if (query === 'slow') {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ query, items: [result('old', 'A stale result that must never land')], errors: [] }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ query, items: harbour, errors: [{ source: 'soundcloud', message: 'SoundCloud is rate limiting searches right now (HTTP 429). Try again shortly.', retryable: true }] }) });
  });
  await page.route((url) => url.pathname === '/api/search', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ query: 'harbour', mode: 'lexical', matches: [], items: [item], index_generation: 1 }) }));
  await page.goto('/');
  await signIn(page);
}

async function search(page: Page, query: string) {
  await page.getByRole('button', { name: /^Search Lumina, / }).click();
  await page.getByRole('combobox').fill(query);
  await page.keyboard.press('ControlOrMeta+Enter'); // search everything in Explore
}

test('test_discover_stale_response_discarded: a slow earlier query never replaces the newer one', async ({ page }) => {
  await openDiscover(page);
  await search(page, 'slow');
  await search(page, 'harbour');
  await expect(page.getByRole('heading', { name: 'Results for “harbour”' })).toBeVisible();
  await page.waitForTimeout(1800);
  await expect(page.getByText('A stale result that must never land')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Harbour in sixty seconds/ })).toBeVisible();
});

test('test_discover_visual_focus', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openDiscover(page);
  await search(page, 'harbour');
  await expect(page.getByRole('heading', { name: 'In your library' })).toBeVisible();
  expect(errors).toEqual([]);
});
