/**
 * Local-only auth and provider-auth route absence, end to end.
 *
 * The auth screen is a local-account surface only: no provider OAuth buttons,
 * and local login still boots the app. The Settings surface has no Twitch
 * connection card (asserted by label absence). While the app runs, the client
 * never touches a removed provider endpoint — /api/oauth/*,
 * /api/twitch/connection*, /api/twitch/live-chat* — tracked from the real
 * network layer. The server-side absence semantics (SPA static mount answering
 * the removed routes with 404 on GET and 405 on POST/DELETE) are proven
 * in-process by backend/tests/test_v1_public_auth.py; this spec proves the
 * client never attempts those routes in the first place.
 *
 * Theme: the app is dark-only by design (VISUAL_SPEC) — the frontend has no
 * theme toggle or light-theme surface (no `data-theme`, no
 * `prefers-color-scheme` in frontend/src), so light-theme equivalents are N/A.
 */
import { expect, test, type Page } from '@playwright/test';

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

const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const downloadDefaults = { format_selection: formatSelection, output_profile: outputProfile };
const settings = {
  id: 'settings-1',
  user_id: user.id,
  download_defaults: downloadDefaults,
  automation_defaults: { cron_expression: '0 */6 * * 1', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  ui_prefs: { sidebar_collapsed: false },
  notification_prefs: {},
  resolved_download_defaults: downloadDefaults,
  resolved_automation_defaults: { cron_expression: '0 */6 * * 1', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function installApi(page: Page, requested: string[], state: { loggedIn: boolean }) {
  return page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    requested.push(`${request.method()} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') return state.loggedIn ? json({ user }) : json({ detail: 'not authenticated' }, 401);
    if (path === '/api/session/login') {
      const body = request.postDataJSON() as { username?: string; password?: string };
      if (body?.username && body?.password) state.loggedIn = true;
      return state.loggedIn ? json({ user }) : json({ detail: 'invalid credentials' }, 401);
    }
    if (path === '/api/session/logout') { state.loggedIn = false; return json({ ok: true }); }
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', data_dir: '/tmp/lumina-test' });
    if (path === '/api/library') return json({ items: [], next_cursor: null });
    const emptySnapshot = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/popular') return json(emptySnapshot);
    if (path === '/api/discovery/live') return json({ ...emptySnapshot, twitch_available: true, hero: [] });
    if (path === '/api/automations') return json([]);
    if (path === '/api/admin/users') return json([]);
    if (path === '/api/admin/settings') return json({ library_root: '/tmp/lumina-test-data/library', temp_root: '/tmp/lumina-test-data/temp', archive_path: '/tmp/lumina-test-data/archive.txt', concurrency: 2, yt_dlp_defaults: {}, ui_prefs: {}, webhook_url: null, webhook_enabled: false, webhook_notify_new_videos: false, webhook_notify_failures: false });
    if (path === '/api/discovery/interests') return json({ categories: [], selected_keys: [] });
    if (path === '/api/discovery/suppressions') return json({ items: [], channels: [] });
    if (path === '/api/playback/continue') return json([]);
    if (path === '/api/settings/me') return json(settings);
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    if (path === '/api/auth-profiles') return json([]);
    if (path.startsWith('/api/discovery/')) return json({ items: [], categories: [] });
    // Anything else must surface loudly rather than hang — including any
    // removed provider endpoint, which the request ledger asserts on below.
    return json({ detail: `public-auth mock: unhandled ${request.method()} ${path}` }, 404);
  });
}

function assertNoProviderEndpoints(requested: string[]) {
  const removed = requested.filter((entry) =>
    entry.startsWith('GET /api/oauth') || entry.startsWith('POST /api/oauth') ||
    entry.includes('/api/twitch/connection') || entry.includes('/api/twitch/live-chat'),
  );
  expect(removed, `removed provider endpoints were requested: ${removed.join(', ')}`).toEqual([]);
}

test('local-only auth: no provider buttons, local login boots, Settings has no Twitch card, no provider endpoint touched', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  const requested: string[] = [];
  const state = { loggedIn: false };
  await installApi(page, requested, state);

  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto('/');

  // The auth screen is a local-account surface only (401 -> login).
  const authCard = page.locator('.g-auth');
  await expect(authCard).toBeVisible();
  await expect(page.getByLabel('Username')).toBeVisible();
  await expect(page.getByLabel('Password', { exact: true })).toBeVisible();
  await expect(authCard.getByText(/Sign in with your Lumina account/i)).toBeVisible();
  // Label absence: no provider OAuth buttons of any kind.
  await expect(page.getByRole('button', { name: /^Continue with /i })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Sign in with (Google|GitHub|Twitch)/i })).toHaveCount(0);

  // Local login still boots the app.
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(authCard).toBeHidden();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();

  // Settings surface has NO Twitch connection card (label absence).
  // At the 1536x960 desktop viewport the sidebar is open by default (the
  // account control is visible), so click it directly.
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /^Settings$/ })).toBeVisible();
  await expect(page.getByText('Twitch chat identity')).toHaveCount(0);
  await expect(page.locator('.twitch-connection-card')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: /Twitch/i })).toHaveCount(0);

  // Route absence from the client's side: no removed provider endpoint was
  // ever requested — not on boot, after login, or in Settings.
  assertNoProviderEndpoints(requested);
  expect(pageErrors).toEqual([]);
});
