import { expect, test, type Page } from '@playwright/test';

/** Mixed-source follows (YouTube, Twitch, Kick) from the server's single refresh path. */

const user = { id: 'member-1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true, bio: '', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', onboarding_status: 'completed' };
const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const rules = { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null };
const defaults = { cron_expression: '*/30 * * * *', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules, duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10 };
const caps = (provider: string, lifecycle = 'vod') => ({ provider, lifecycle, can_play: provider !== 'kick', can_acquire: provider === 'youtube', chat: { live: 'unavailable', replay: 'unavailable' } });
const art = '/api/artwork/remote/fixture';

function follow(id: string, label: string, sourceUrl: string, extra: Record<string, unknown>) {
  return {
    id, user_id: user.id, label, source_url: sourceUrl, source_type: 'channel', artwork_url: null, cron_expression: '*/30 * * * *', active: true,
    auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules, duplicate_policy: 'skip_same_source',
    max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10, last_checked_at: '2026-01-01T12:00:00', next_check_at: '2026-01-01T12:30:00',
    last_error: null, last_run_summary: { discovered: 3 }, feed_entries: [], created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', ...extra,
  };
}

const follows = [
  follow('yt', 'Veritasium', 'https://www.youtube.com/@veritasium', {
    feed_entries: [
      { id: 'yt-1', title: 'A deliberately long title about why the same equation keeps showing up in places nobody expected it to be', uploader: 'Veritasium', duration: 1260, artwork_url: art, webpage_url: 'https://www.youtube.com/watch?v=yt-1', capabilities: caps('youtube') },
      { id: 'yt-2', title: 'The shortest possible path', uploader: 'Veritasium', duration: 840, artwork_url: art, webpage_url: 'https://www.youtube.com/watch?v=yt-2', capabilities: caps('youtube') },
    ],
  }),
  follow('kick', 'xQc', 'https://kick.com/xqc', {
    feed_entries: [{ id: 'kick-live', title: 'Live on Kick right now', uploader: 'xQc', artwork_url: art, webpage_url: 'https://kick.com/xqc', capabilities: caps('kick', 'live') }],
  }),
  follow('tw', 'Streamer', 'https://www.twitch.tv/streamer', {
    last_error: 'HTTP Error 503: Service Unavailable',
    feed_entries: [{ id: 'tw-1', title: 'Last-known Twitch broadcast', uploader: 'Streamer', artwork_url: art, webpage_url: 'https://www.twitch.tv/videos/1', capabilities: caps('twitch') }],
  }),
  follow('new', 'Freshly Followed', 'https://www.youtube.com/@fresh', { last_checked_at: null, next_check_at: null }),
];

async function mockApi(page: Page) {
  const refreshes: string[] = [];
  const previews: string[] = [];
  await page.addInitScript(() => {
    class QuietEventSource {
      onopen: ((event: Event) => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(new Event('open')), 0); }
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: QuietEventSource });
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') return json({ user });
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', desktop_dir: null, data_dir: '/tmp/lumina-test' });
    if (path === '/api/automations') return json(follows);
    if (path === '/api/follows/refresh') { refreshes.push(request.method()); return json(follows.filter((row) => row.active)); }
    if (path === '/api/preview') { previews.push(path); return json({ detail: 'no client provider work expected' }, 500); }
    const empty = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/live') return json({
      ...empty, state: 'ready', twitch_available: true, hero: [],
      items: [{ id: 'live-1', title: 'A public gaming stream', uploader: 'Someone', webpage_url: 'https://www.twitch.tv/someone', thumbnail: null, artwork_url: art, view_count: 1200, source: 'twitch', source_label: 'Twitch', category_keys: ['gaming'], capabilities: caps('twitch', 'live') }],
      categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }],
      followed_unavailable: [{ source: 'kick', checked_at: '2026-01-01T12:04:00Z' }],
    });
    if (path.startsWith('/api/artwork/remote/')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#356258"/></svg>' });
    if (path === '/api/library') return json({ items: [], next_cursor: null });
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    if (path === '/api/settings/me') return json({
      id: 's', user_id: user.id, download_defaults: { format_selection: formatSelection, output_profile: outputProfile }, automation_defaults: defaults,
      ui_prefs: { sidebar_collapsed: false }, notification_prefs: {}, remote_playback_cache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 },
      resolved_download_defaults: { format_selection: formatSelection, output_profile: outputProfile }, resolved_automation_defaults: defaults,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    });
    if (path === '/api/admin/users' || path === '/api/playback/continue') return json([]);
    if (path === '/api/discovery/interests') return json({ categories: [], selected_keys: [] });
    if (path === '/api/discovery/suppressions') return json({ items: [], channels: [] });
    if (path.startsWith('/api/discovery/')) return json(empty);
    return json({ detail: `mock: unhandled ${request.method()} ${path}` }, 404);
  });
  return { refreshes, previews };
}

async function go(page: Page, mobile: boolean, label: string, heading: RegExp) {
  // Streaming's own views are in-page; open them by address, as the Streaming page's links do.
  const path = { Subscriptions: '/streaming/channels', Live: '/streaming/live' }[label];
  if (path) {
    await page.evaluate((to) => { window.history.pushState(null, '', to); window.dispatchEvent(new PopStateEvent('popstate')); }, path);
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    return;
  }
  if (mobile && !['Home', 'Library', 'Live', 'Search'].includes(label)) await page.getByRole('button', { name: 'More', exact: true }).click(); // the tab bar holds four places; the rest sit in its More sheet
  const nav = mobile && ['Home', 'Library', 'Live', 'Search'].includes(label) ? page.getByRole('navigation', { name: 'Mobile primary navigation' }) : mobile ? page.getByRole('dialog', { name: 'Mobile navigation' }) : page.getByRole('navigation', { name: 'Primary' });
  await nav.getByRole('button', { name: label }).click();
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
}

// Navigation differs by width (the phone uses the mobile nav), so both widths run.
for (const viewport of [{ width: 1536, height: 960 }, { width: 390, height: 844 }]) {
  test(`follow feed and Kick live status ${viewport.width}`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.setViewportSize(viewport);
    const calls = await mockApi(page);
    await page.goto('/');
    await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
    const mobile = viewport.width <= 680;

    await go(page, mobile, 'Subscriptions', /^Streaming$/);
    // Mixed sources in one feed, a last-known failure kept visible, a pending first check.
    await expect(page.getByRole('button', { name: /^A deliberately long title/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Live on Kick right now/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Last-known Twitch broadcast/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Channels needing attention' })).toBeVisible();
    await expect(page.getByRole('link', { name: /^xQc, Kick/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /^Streamer, Twitch · Last check failed/ })).toBeVisible();
    await expect(page.getByRole('list').filter({ hasText: 'HTTP Error 503' })).toBeVisible();

    await page.getByRole('button', { name: /Retry channels/ }).click();
    await expect.poll(() => calls.refreshes).toEqual(['POST']);
    await expect(page.getByRole('button', { name: /Restart retry/ })).toBeVisible();

    // Source filter narrows the list and the feed without touching follows.
    await page.getByRole('group', { name: 'Filter by source' }).getByRole('button', { name: 'Kick' }).click();
    await expect(page.getByRole('link', { name: /^Veritasium/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Live on Kick right now/ })).toBeVisible();
    await page.getByRole('group', { name: 'Filter by source' }).getByRole('button', { name: 'All sources' }).click();

    // Follow detail (Twitch keeps it; YouTube follows open their channel page): deep-linkable, settings in a drawer.
    await page.getByRole('link', { name: /^Streamer, Twitch/ }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Streamer' })).toBeFocused();
    await expect(page).toHaveURL(/\/subscriptions\/tw$/);
    await expect(page.getByRole('link', { name: /Open on Twitch/ })).toHaveAttribute('href', 'https://www.twitch.tv/streamer');
    await page.getByRole('button', { name: 'Follow settings' }).click();
    await expect(page.getByText('When on, saves Video · 1080p, up to 5 per check, 20 per day.', { exact: false })).toBeVisible();
    await page.getByRole('button', { name: 'Unfollow Streamer' }).click();
    await expect(page.getByRole('alertdialog')).toContainText('Videos already in your library stay');
    await page.getByRole('button', { name: 'Keep following' }).click();
    await page.getByRole('button', { name: 'Done' }).click();

    // Deep link keeps the last-known videos on the detail.
    await page.goto('/subscriptions/tw');
    await expect(page.getByRole('heading', { level: 1, name: 'Streamer' })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Last-known Twitch broadcast/ })).toBeVisible();

    await go(page, mobile, 'Live', /^Streaming$/);
    await expect(page.getByText(/Live status for followed Kick channels is unavailable · last tried .+/)).toBeVisible();

    expect(calls.previews).toEqual([]);
    expect(errors).toEqual([]);
  });
}
