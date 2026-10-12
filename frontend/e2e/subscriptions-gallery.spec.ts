/** Browser: Subscriptions in light and dark, desktop and phone. */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { caps, CHANNEL_ID, liveItems, liveSnapshotBody, mockChannelPage, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockApi, signIn, user } from './lumina-mock';

const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const rules = { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null };

function follow(id: string, label: string, sourceUrl: string, provider: string, extra: Record<string, unknown> = {}) {
  return {
    id, user_id: user.id, label, source_url: sourceUrl, source_type: 'channel', artwork_url: null, cron_expression: '*/30 * * * *', active: true,
    auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules, duplicate_policy: 'skip_same_source',
    max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10, last_checked_at: '2026-09-29T20:40:00', next_check_at: '2026-09-29T21:10:00',
    last_error: null, last_run_summary: { discovered: 3 }, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    feed_entries: [1, 2, 3].map((n) => ({
      id: `${id}-${n}`, title: `${label} latest ${n}`, uploader: label, duration: 600, artwork_url: `/api/artwork/remote/${id}-${n}`,
      webpage_url: provider === 'twitch' ? `https://www.twitch.tv/videos/${n}` : `https://www.youtube.com/watch?v=${id}${n}`, capabilities: caps(provider, 'vod'),
    })), ...extra,
  };
}

const follows = [
  follow('yt-id', 'Harbor Films', `https://www.youtube.com/channel/${CHANNEL_ID}`, 'youtube'),
  follow('yt-handle', 'Handle Films', 'https://www.youtube.com/@handlefilms', 'youtube'),
  follow('tw', 'Streamer', 'https://www.twitch.tv/streamer', 'twitch'),
];

async function open(page: Page, { width = 1440, height = 900, theme = 'dark' as 'light' | 'dark', path = '/streaming/channels' } = {}) {
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: theme });
  await page.setViewportSize({ width, height });
  await mockApi(page, { sidebar_collapsed: false, theme });
  await mockRemoteArtwork(page);
  await mockLive(page, liveSnapshotBody({ hero: liveItems(1, { uploader: 'Harbor Films', uploader_id: CHANNEL_ID }, 'follow'), items: [] }));
  const requests = await mockChannelPage(page);
  await page.route('**/api/automations', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(follows) }));
  await page.route('**/api/follows/refresh', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(follows) }));
  await page.route(/\/api\/library\/channels(\?|$)/, (route) => route.fulfill({ contentType: 'application/json', body: '[]' }));
  await page.goto(path);
  await signIn(page);
  return requests;
}

for (const theme of ['light', 'dark'] as const) {
  for (const [label, width, height] of [['desktop', 1440, 900], ['phone', 390, 844]] as const) {
    test(`Subscriptions reads cleanly in ${theme} at ${label} width`, async ({ page }) => {
      await open(page, { width, height, theme });
      await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Live from your follows' })).toBeVisible();
      const results = await new AxeBuilder({ page }).include('main').analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`)).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/visual/subscriptions-${theme}-${label}.png` });
    });
  }
}

test('the masthead, the live band, the gold ring and the YouTube tile', async ({ page }) => {
  await open(page);
  await expect(page.getByText(/^3 channels · 1 live now · checked /)).toBeVisible();
  await expect(page.getByText('Following here never subscribes you on the platform.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Live from your follows' })).toBeVisible();
  const tile = page.getByRole('link', { name: /^Harbor Films, live/ });
  await expect(tile.locator('.g-avatar')).toHaveClass(/is-live/);
  await tile.click();
  await expect(page).toHaveURL(new RegExp(`/channel/youtube/${CHANNEL_ID}$`));
});

test('a handle follow redirects to its channel page without adding a history entry', async ({ page }) => {
  await open(page);
  await page.getByRole('link', { name: /^Handle Films/ }).click();
  await expect(page).toHaveURL(new RegExp(`/channel/youtube/${CHANNEL_ID}$`));
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Films' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/streaming\/channels$/);
});

test('a Twitch follow keeps its detail, and Follow settings returns focus on Escape', async ({ page }) => {
  await open(page);
  await page.getByRole('link', { name: /^Streamer/ }).click();
  await expect(page).toHaveURL(/\/subscriptions\/tw$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Streamer' })).toBeVisible();
  const opener = page.getByRole('button', { name: 'Follow settings' });
  await opener.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(opener).toBeFocused();
});
