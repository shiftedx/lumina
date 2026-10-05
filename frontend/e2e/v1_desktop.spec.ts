import { expect, test } from '@playwright/test';

// The desktop product and host-launch surface are absent from the web UI.
// Real-browser evidence: local login boots the shell, the Library surface renders
// its items with NO open/reveal OS-launch affordances, and no page errors occur.

const user = {
  id: 'member-1',
  username: 'alexandria',
  display_name: 'Alexandria',
  role: 'admin',
  is_active: true,
  bio: '',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  onboarding_status: 'completed',
};

const fixtureItem = {
  id: 'library-1',
  user_id: user.id,
  visibility: 'private',
  remote_id: 'synthetic-1',
  source_url: 'https://example.test/watch/1',
  webpage_url: 'https://example.test/watch/1',
  title: 'Synthetic baseline fixture',
  uploader: 'Lumina fixture',
  duration: 90,
  extractor: 'youtube',
  status: 'available',
  file_path: '/tmp/lumina-test/synthetic-1.mp4',
  file_size: 14496,
  metadata_json: { description: 'baseline fixture', view_count: 1 },
  chapters: [],
  downloaded_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const downloadDefaults = { format_selection: formatSelection, output_profile: outputProfile };
const settings = {
  id: 'settings-1',
  user_id: user.id,
  download_defaults: downloadDefaults,
  automation_defaults: { cron_expression: '0 */6 * * *', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  ui_prefs: { sidebar_collapsed: false },
  notification_prefs: {},
  remote_playback_cache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 },
  resolved_download_defaults: downloadDefaults,
  resolved_automation_defaults: { cron_expression: '0 */6 * * *', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

test('web UI has no host-launch affordances after desktop removal', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (err) => pageErrors.push(String(err?.stack ?? err)));
  // Hermetic SSE stub (same pattern as the baseline spec).
  await page.addInitScript(() => {
    class QuietEventSource {
      onopen: ((event: Event) => void) | null = null;
      constructor(_url: string) { setTimeout(() => this.onopen?.(new Event('open')), 0); }
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: QuietEventSource });
  });

  let loggedIn = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') return loggedIn ? json({ user }) : json({ detail: 'not authenticated' }, 401);
    if (path === '/api/session/login') {
      const body = request.postDataJSON() as { username?: string; password?: string };
      if (body?.username && body?.password) loggedIn = true;
      return loggedIn ? json({ user }) : json({ detail: 'invalid credentials' }, 401);
    }
    if (path === '/api/session/logout') { loggedIn = false; return json({ ok: true }); }
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    // Health has no desktop_dir once the desktop product is removed; the mock mirrors that.
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', data_dir: '/tmp/lumina-test' });
    if (path === '/api/library/sections') return json({ movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 1, recordings: 0, deleted: 0 });
    if (path === '/api/library') return json({ items: [fixtureItem], next_cursor: null });
    const emptySnapshot = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/popular') return json(emptySnapshot);
    if (path === '/api/discovery/live') return json({ ...emptySnapshot, twitch_available: true, hero: [] });
    if (path === '/api/automations') return json([]);
    if (path === '/api/admin/users') return json([]);
    if (path === '/api/admin/settings') return json({ library_root: '/tmp/lumina-test-data/library', temp_root: '/tmp/lumina-test-data/temp', archive_path: '/tmp/lumina-test-data/archive.txt', concurrency: 2, yt_dlp_defaults: {}, ui_prefs: {}, oauth_auto_create_users: true, webhook_url: null, webhook_enabled: false, webhook_notify_new_videos: false, webhook_notify_failures: false, oauth_google_enabled: false, oauth_github_enabled: false, oauth_providers: [] });
    if (path === '/api/discovery/interests') return json({ categories: [], selected_keys: [] });
    if (path === '/api/discovery/suppressions') return json({ items: [], channels: [] });
    if (path === '/api/playback/continue') return json([]);
    if (path === '/api/settings/me') return json(settings);
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    if (path === '/api/auth-profiles') return json([]);
    if (path.startsWith('/api/discovery/')) return json({ items: [], categories: [] });
    // The removed host-launch endpoints must not be called by the UI; if they
    // ever are, fail loudly instead of serving a fake 204.
    if (/\/api\/library\/[^/]+\/(open|reveal)$/.test(path)) {
      return json({ detail: `mock: UI called removed host-launch route ${path}` }, 500);
    }
    return json({ detail: `mock: unhandled ${request.method()} ${path}` }, 404);
  });

  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto('/');

  // Local login on the real auth screen.
  const authCard = page.locator('.g-auth');
  await expect(authCard).toBeVisible();
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(authCard).toBeHidden();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();

  // Library surface: the item renders without any open/reveal OS-launch affordance.
  await page.getByRole('button', { name: 'Library', exact: true }).first().click();
  await page.getByRole('navigation', { name: 'Library' }).getByRole('link', { name: 'YouTube' }).click();
  await expect(page.getByRole('button', { name: new RegExp(`^${fixtureItem.title},`) })).toBeVisible();
  const main = page.locator('main[aria-label="Main content"]');
  const launchButtons = main.getByRole('button', { name: /^(open|reveal)/i });
  await expect(launchButtons).toHaveCount(0);
  const launchLinks = main.getByRole('link', { name: /^(open|reveal)/i });
  await expect(launchLinks).toHaveCount(0);

  expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toHaveLength(0);
});
