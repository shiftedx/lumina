/** Live and YouTube spec 6.1–6.2 and Testing (browser): a remote live watch page in light and dark, desktop and phone. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { caps, CHANNEL_ID, liveSnapshotBody, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockApi, signIn } from './lumina-mock';
import { redOrPinkInMain } from './red-check';

const url = 'https://www.youtube.com/watch?v=live-watch1';
const preview = (lifecycle: string) => ({
  kind: 'video', title: 'Harbor at night, live', webpage_url: url, entries: [], artwork_url: '/api/artwork/remote/p1', playback: null,
  capabilities: caps('youtube', lifecycle, { can_record: lifecycle === 'live', chat: { live: 'available', replay: 'unavailable' } }),
  chapters: [], description_timestamps: [{ start: 8, end: 13, seconds: 754, label: '12:34' }],
  raw: { extractor_key: 'Youtube', uploader: 'Harbor Films', channel_id: CHANNEL_ID, channel_follower_count: 2_100_000, description: 'Jump to 12:34 for the storm. https://example.test/shop', concurrent_view_count: 12_412 },
});

async function open(page: Page, lifecycle = 'live', viewport = { width: 1440, height: 900 }, theme: 'light' | 'dark' = 'dark') {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: theme });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false, theme });
  await mockRemoteArtwork(page);
  await mockLive(page, liveSnapshotBody({ items: [{ id: 'live-watch1', webpage_url: url, view_count: 13_000, capabilities: caps('youtube', 'live'), category_keys: ['gaming'] }] }));
  await page.route('**/api/preview', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(preview(lifecycle)) }));
  await page.route(/\/api\/playback\/remote\//, (route) => route.fulfill({ contentType: 'application/json', body: 'null' }));
  await page.goto(`/watch?url=${encodeURIComponent(url)}`);
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor at night, live' })).toBeVisible();
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`a live watch page reads cleanly in ${theme} at ${label} width`, async ({ page }) => {
      await open(page, 'live', { width, height }, theme);
      await expect(page.locator('.g-watch-kicker .g-live.is-live')).toBeVisible();
      await expect(page.getByText(/watching/)).toBeVisible();
      const results = await new AxeBuilder({ page }).include('.g-watch-body').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/visual/watch-live-${theme}-${label}.png` });
    });
  }
}

test('the byline opens the channel page and the description seeks without links', async ({ page }) => {
  await open(page, 'vod');
  await expect(page.locator('.g-watch-description a')).toHaveCount(0);
  await page.getByRole('button', { name: 'Seek to 12:34' }).click();
  await expect(page.getByRole('link', { name: 'Harbor Films' })).toHaveAttribute('href', `/channel/youtube/${CHANNEL_ID}`);
});

test('no red on the watch page, and the chat seam shows the live chat', async ({ page }) => {
  await open(page);
  // Registered after open(): the shared mocks register later routes that would win.
  await page.route(/\/api\/live-chat\?/, (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'active', cursor: 1, poll_after_ms: 2000,
    events: [{ id: 'm1', offset_ms: null, kind: 'message', text: 'The storm is coming in', author: { name: '@harborfan', channel_id: null, badges: [] }, moderation: 'visible', amount: null, source_channel: null }] }) }));
  await expect(page.getByRole('list', { name: 'Live chat messages' }).getByText('The storm is coming in')).toBeVisible({ timeout: 10_000 });
  const reds = await redOrPinkInMain(page);
  expect(reds).toEqual([]);
});

test('Up next updates automatically after a cold subject lookup without replacing the watched video', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  await mockRemoteArtwork(page);
  await page.route('**/api/preview', route => route.fulfill({ json: preview('vod') }));
  let calls = 0;
  await page.route('**/api/discovery/up-next', route => {
    calls += 1;
    return route.fulfill({ json: {
      items: calls === 1 ? [] : [{ id: 'harbor-next', title: 'Harbor storms explained', uploader: 'Harbor Films', source: 'youtube',
        webpage_url: 'https://www.youtube.com/watch?v=harbor-next', duration: 600, capabilities: caps('youtube', 'vod') }],
      categories: [], state: calls === 1 ? 'loading' : 'ready', refreshing: calls === 1, stale: false, error: null,
    } });
  });
  await page.goto(`/watch?url=${encodeURIComponent(url)}`); await signIn(page);
  await expect(page.getByRole('heading', { name: 'Harbor at night, live', level: 1 })).toBeVisible();
  const rail = page.getByRole('region', { name: 'Up next', exact: true });
  await expect(rail.getByText('Finding related videos…')).toBeVisible();
  await expect(rail.getByText('No related videos from this source yet.')).toHaveCount(0);
  await expect(rail.getByRole('button', { name: /^Harbor storms explained/ })).toBeVisible();
  expect(calls).toBe(2);
  await expect(page.getByRole('heading', { name: 'Harbor at night, live', level: 1 })).toBeVisible();
});
