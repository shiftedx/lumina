/**
 * The saved sign-in surface is absent and the credential / generic
 * live-chat API family is never requested, end to end.
 *
 * After local login, the Settings surface has no "Saved sign-ins" card
 * (asserted by label AND class absence) while the surviving local-session
 * "Sign out" control stays. A live YouTube source then plays real media
 * with no current-chat rail — even though the (deliberately stale) preview
 * still advertises current chat, which proves the rail family is gone from
 * the client rather than merely unrequested. From the real network layer,
 * the request ledger proves the client made ZERO /api/auth-profiles* and
 * /api/live-chat* requests and that no pageerror fired. The server-side
 * absence semantics (route table, 422 rejection, static-mount answers) and
 * the UI/DTO absence are proven in-process by
 * backend/tests/test_v1_public_options.py and
 * frontend/src/v1_public_options.test.tsx; this spec proves the client
 * neither renders the surface nor attempts the removed endpoints.
 *
 * Media: the live playback fixture is the shared self-contained WebM
 * (VP9/Opus) clip — headless Chromium is an open-codec build without H.264,
 * so MP4 fixtures fail with DEMUXER_ERROR_NO_SUPPORTED_STREAMS.
 *
 * Theme: the app is dark-only by design (F03) — the frontend has no theme
 * toggle or light-theme surface (no `data-theme`, no `prefers-color-scheme`
 * in frontend/src), so light-theme equivalents are N/A.
 */
import { expect, test, type Page } from '@playwright/test';
import { assertMediaPlaybackAdvances } from './release-evidence';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

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

const LIVE_URL = 'https://example.test/watch/live-1';
const LIVE_TITLE = 'A live favorite';
const MEDIA_PATH = '/api/remote-streams/live-e2e/relay/1/live.webm';
const WEBM_BODY = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');

const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const downloadDefaults = { format_selection: formatSelection, output_profile: outputProfile };
const settings = {
  id: 'settings-1',
  user_id: user.id,
  download_defaults: downloadDefaults,
  automation_defaults: { cron_expression: '0 */6 * * 1', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  // The sidebar defaults to collapsed; the spec asserts surface absence with
  // the account control reachable, so mock it open.
  ui_prefs: { sidebar_collapsed: false },
  notification_prefs: {},
  resolved_download_defaults: downloadDefaults,
  resolved_automation_defaults: { cron_expression: '0 */6 * * 1', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

// A currently-live YouTube source with ready progressive playback at the
// current edge. The preview deliberately STALE-ADVERTISES current chat
// (chat.live: 'available') so the test proves the current-chat rail family
// is absent from the client even when a preview still claims the provider
// offers it — nothing mounts a rail and no /api/live-chat* request fires.
const LIVE_PLAYBACK = {
  status: 'ready', stream_id: 'live-e2e', transport: 'progressive', media_kind: 'video',
  playback_url: MEDIA_PATH,
  content_type: SYNTHETIC_MEDIA_TYPE, has_video: true, has_audio: true,
  seekable: false, live: true, renditions: [],
};

const LIVE_PREVIEW = {
  kind: 'video', title: LIVE_TITLE, webpage_url: LIVE_URL,
  extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video',
  capabilities: {
    provider: 'youtube', lifecycle: 'live',
    can_play: true, play_reason: null,
    can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
    chat: { live: 'available', replay: 'unavailable' },
  },
  playback: LIVE_PLAYBACK,
  entries: [], raw: { id: 'live-e2e', webpage_url: LIVE_URL, uploader: 'Lumina fixture', is_live: true, extractor: 'youtube' },
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
    if (path === '/api/automations') return json([]);
    if (path === '/api/admin/users') return json([]);
    if (path === '/api/admin/settings') return json({ library_root: '/tmp/lumina-test-data/library', temp_root: '/tmp/lumina-test-data/temp', archive_path: '/tmp/lumina-test-data/archive.txt', concurrency: 2, yt_dlp_defaults: {}, ui_prefs: {}, webhook_url: null, webhook_enabled: false, webhook_notify_new_videos: false, webhook_notify_failures: false });
    if (path === '/api/discovery/interests') return json({ categories: [], selected_keys: [] });
    // Well-formed suppression list: the Settings card crashes on malformed
    // payloads, so the fixture must be the exact valid shape.
    if (path === '/api/discovery/suppressions') return json({ items: [], channels: [] });
    const emptySnapshot = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/popular') return json({
      items: [{ id: 'live-1', title: LIVE_TITLE, uploader: 'Lumina fixture', artwork_url: '/api/artwork/remote/live-1', webpage_url: LIVE_URL, category_keys: ['fixtures'] }],
      categories: [{ key: 'fixtures', label: 'Fixtures', state: 'ready' }], state: 'ready', refreshing: false, stale: false, error: null,
    });
    if (path === '/api/discovery/home') return json(emptySnapshot);
    if (path === '/api/discovery/live') return json({ ...emptySnapshot, twitch_available: false, hero: [] });
    if (path === '/api/discovery/up-next') return json({ items: [], categories: [], state: 'ready', refreshing: false, stale: false, error: null });
    if (path === '/api/playback/continue') return json([]);
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    if (path === '/api/settings/me') return json(settings);
    if (path === '/api/artwork/remote/live-1') return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900"><rect width="900" height="900" fill="#356258"/></svg>' });
    if (path === MEDIA_PATH) return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: WEBM_BODY });
    if (path === '/api/preview') {
      const sourceUrl = (request.postDataJSON() as { source_url?: unknown } | null)?.source_url;
      if (sourceUrl === LIVE_URL) return json(LIVE_PREVIEW);
      return json({ detail: `public-options mock: unhandled preview ${String(sourceUrl)}` }, 404);
    }
    if (path.startsWith('/api/remote-streams/')) {
      if (request.method() === 'DELETE') return route.fulfill({ status: 204 });
      if (path.endsWith('/refresh')) return json(LIVE_PLAYBACK);
    }
    if (path.startsWith('/api/discovery/')) return json({ items: [], categories: [] });
    // Anything else must surface loudly rather than hang — including any
    // removed credential or live-chat endpoint, which the request ledger
    // asserts on below.
    return json({ detail: `public-options mock: unhandled ${request.method()} ${path}` }, 404);
  });
}

// 2.2.0 rule: live chat IS fetched for a live YouTube/Twitch source; the credential endpoints stay gone.
function assertNoRemovedCredentialEndpoints(requested: string[]) {
  const removed = requested.filter((entry) => entry.includes('/api/auth-profiles'));
  expect(removed, `removed credential endpoints were requested: ${removed.join(', ')}`).toEqual([]);
}

async function login(page: Page) {
  const authCard = page.locator('.g-auth');
  await expect(authCard).toBeVisible();
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(authCard).toBeHidden();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
}

async function openSettings(page: Page) {
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /^Settings$/ })).toBeVisible();
}

const SIGN_IN_LABEL_PATTERNS = [/saved sign-in/i, /sign in with/i, /auth profile/i, /credential/i, /cookie file/i, /browser sign-in/i, /netrc/i] as const;

async function assertSettingsSurfaceHasNoSignInControls(page: Page) {
  // The surface itself renders (positive control) and the surviving local
  // session control stays.
  await expect(page.getByRole('heading', { level: 1, name: /^Settings$/ })).toBeVisible();
  // Sign out lives on Profile now (the landing is a card grid; the top-bar menu has one too).
  await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name: 'Profile' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  // Label absence: no saved sign-in card or provider/credential control of
  // any kind. :visible excludes the Password row's ⓘ panel, whose verbatim
  // spec copy ("...the password you sign in with...") stays hidden until opened.
  for (const pattern of SIGN_IN_LABEL_PATTERNS) {
    await expect(page.getByText(pattern).and(page.locator(':visible')), `label "${pattern}" must be absent`).toHaveCount(0);
  }
  // Class absence: the removed Saved sign-ins card markup is gone.
  await expect(page.locator('.saved-sign-ins-card')).toHaveCount(0);
}

test('desktop 1536x960 dark: Settings has no Saved sign-ins card, a live source plays media and fetches live chat, and no credential endpoint is ever requested', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (err) => pageErrors.push(String(err)));
  const requested: string[] = [];
  const state = { loggedIn: false };
  await installApi(page, requested, state);

  await page.setViewportSize({ width: 1536, height: 960 });
  await page.goto('/');
  await login(page);

  // Settings surface: NO "Saved sign-ins" card (label + class absence).
  await openSettings(page);
  await assertSettingsSurfaceHasNoSignInControls(page);

  // A live YouTube source: real media plays at the current edge with no
  // current-chat rail, even though the stale preview advertises current chat.
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: new RegExp(`^${LIVE_TITLE}`) }).click();

  const player = page.locator('[data-lumina-player="true"]');
  await expect(player).toBeVisible();

  // Real media (WebM VP9/Opus fixture) actually plays in the browser.
  const media = await assertMediaPlaybackAdvances(player.locator('video'));
  expect(media.advancedSeconds, 'live media must advance').toBeGreaterThan(0);

  // The Live state holds and no current-chat rail mounts.
  await player.hover();
  await expect(player.getByRole('status', { name: 'Watching live' })).toBeVisible();
  await expect(player.getByLabel('Seek')).toHaveCount(0);
  await player.hover();
  await expect(player.getByRole('status', { name: 'Watching live' })).toBeVisible();

  // Request ledger: the client never touched the removed credential or
  // live-chat surface — not on boot, after login, in Settings, or while
  // playing the live source.
  assertNoRemovedCredentialEndpoints(requested);
  expect(requested.some((entry) => entry.includes('/api/live-chat')), 'live chat is fetched for a live YouTube source').toBe(true);
  expect(pageErrors, `page errors: ${pageErrors.join(' | ')}`).toEqual([]);
});
