import { mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page, type Route } from '@playwright/test';
import { episodeSegments, fulfillRangeMedia, item, mockAdminFixtures, mockApi, mockTitles, mockTranscripts, pauseAt, signIn, user } from './lumina-mock';
import { mockPaletteSearch } from './palette-mock';
import { CHANNEL_ID, caps, liveSnapshotBody, mockChannelPage, mockLibraryChannels, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { mockPopular, mockReco, popularWithForYou } from './reco-mock';
import { stillItem } from '../src/test/galleryFixtures';
import { recoDiagnostics, recoSurfaceStats } from '../src/test/recoFixtures';

/**
 * Every screen of the 13.1 table plus the A-C screens, at desktop and phone, light and dark, with forced
 * colours for a few. Mocked, synthetic data only. Not a pixel-diff gate: the images and a contact sheet go to
 * VISUAL_OUT (default output/visual/, gitignored) for the owner's review.
 */
const OUT = process.env.VISUAL_OUT ?? join(process.cwd(), '..', 'output', 'visual');
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 },
  ...(process.env.VISUAL_WIDE ? [{ name: 'wide', width: 1920, height: 1080 }] : []), // dev loop: density checks on a big monitor
] as const;
type Prefs = Record<string, unknown>;
type Shot = { area: string; state: string; only?: 'desktop' | 'phone'; open: (page: Page, prefs: Prefs) => Promise<void> };
/** Route overrides a state registers (they win over the shared mocks); the loop removes them before the next screen. */
const overrides = new WeakMap<Page, Array<[string | RegExp, (route: Route) => unknown]>>();
const over = async (page: Page, url: string | RegExp, body: unknown, status = 200) => {
  const handler = typeof body === 'function' ? (body as (route: Route) => unknown) : (route: Route) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route(url, handler);
  overrides.set(page, [...(overrides.get(page) ?? []), [url, handler]]);
};
const clearOverrides = async (page: Page) => { for (const [url, handler] of overrides.get(page) ?? []) await page.unroute(url, handler); overrides.delete(page); };
const follow = (id: string, label: string, url: string, extra: Record<string, unknown> = {}) => ({
  id, user_id: 'member-1', label, source_url: url, source_type: 'channel', artwork_url: null, cron_expression: '*/30 * * * *', active: true, auto_download: false,
  format_selection: { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' },
  output_profile: { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' },
  rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null },
  duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10, last_checked_at: '2026-09-29T20:40:00', next_check_at: '2026-09-29T21:10:00',
  last_error: null, last_run_summary: { discovered: 3 }, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  feed_entries: [1, 2, 3].map((n) => ({ id: `${id}-${n}`, title: `${label} latest ${n}`, uploader: label, duration: 600, artwork_url: `/api/artwork/remote/${id}-${n}`, webpage_url: `https://www.youtube.com/watch?v=${id}${n}`, capabilities: caps('youtube', 'vod') })), ...extra,
});
const FOLLOWS = [follow('yt-id', 'Harbor Films', `https://www.youtube.com/channel/${CHANNEL_ID}`), follow('yt-handle', 'Handle Films', 'https://www.youtube.com/@handlefilms')];
const collectionEntry = (id: string, position: number) => ({ id, position, availability: 'available', ref: { kind: 'library', library_item_id: item.id, provider: null, remote_id: null, url: null }, title: item.title, uploader: null, artwork_url: null, duration: item.duration });
const COLLECTION = { id: 'c1', owner_user_id: 'member-1', name: 'Weekend watchlist', description: null, visibility: 'private', revision: 1, entries: [collectionEntry('e1', 0)], items: [], item_count: 1, rules: null };
const INTERESTS = { categories: [{ key: 'nature', label: 'Nature' }, { key: 'film', label: 'Film' }, { key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }], selected_keys: ['nature'] };
const onboard = async (page: Page) => {
  await over(page, '**/api/session/me', { user: { ...user, onboarding_status: 'pending' } });
  await over(page, '**/api/discovery/interests', INTERESTS);
  await over(page, /\/api\/discovery\/channels(\?|$)/, { categories: [{ key: 'film', label: 'Film', state: 'curated', channels: ['Harbor Films', 'Cinema Notes', 'Short Reels'].map((name, n) => ({ channel_key: `c${n}`, source_url: `https://www.youtube.com/@c${n}`, display_name: name, source: 'youtube', source_label: 'YouTube', artwork_url: null, category_keys: ['film'], following: n === 2 })) }] });
  await page.goto('/');
};
const finishInterests = async (page: Page) => { await page.getByLabel('Film').check(); await press(page, 'Continue'); };
const EPISODE = 'item-ep-2-2';
/** The 1.5 s synthetic episode with an intro and credits segment, paused at `seconds` (a seek on range media, so a loaded host cannot play past the segment). */
const episode = async (page: Page, seconds: number) => {
  await over(page, `**/api/library/${EPISODE}/segments`, episodeSegments(EPISODE));
  await over(page, `**/api/library/${EPISODE}/media`, fulfillRangeMedia);
  const segments = page.waitForResponse(`**/api/library/${EPISODE}/segments`); // the player reads them on a time update, so they must be in before the seek
  await at(`/watch/library/${EPISODE}`)(page);
  await segments;
  await expect(page.locator('video')).toHaveAttribute('src', `/api/library/${EPISODE}/media`);
  await expect.poll(() => page.locator('video').evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThanOrEqual(1);
  await pauseAt(page, seconds);
};
const AT = '2026-07-20T10:00:00Z';
const signedOut = (page: Page) => over(page, '**/api/session/me', { detail: 'not authenticated' }, 401);
const RING = [
  { user_id: 'm2', display_name: 'Sam Rivers', username: 'sam', role: 'viewer', switch: 'instant', active: false },
  { user_id: 'member-1', display_name: 'Alexandria', username: 'alexandria', role: 'admin', switch: 'password', active: false },
];
const signInAs = async (page: Page, status: number, detail: string, shown: string | RegExp = detail) => {
  await signedOut(page); await over(page, '**/api/session/login', { detail }, status);
  await page.goto('/'); await page.getByLabel('Username').fill('alexandria'); await page.getByLabel('Password', { exact: true }).fill('synthetic-pw');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click(); await expect(page.getByText(shown)).toBeVisible();
};
const PLAYLIST = { kind: 'playlist', title: 'Weekend cooking playlist', webpage_url: 'https://www.youtube.com/playlist?list=PLsynthetic', extractor: 'youtube:tab', extractor_key: 'YoutubeTab', media_kind: 'video',
  entries: ['Sourdough basics', 'Knife skills', 'Weeknight pasta', 'Quick pickles'].map((title, n) => ({ id: `pl${n}`, title, uploader: 'Harbor Films', duration: 300 + n * 60, webpage_url: `https://www.youtube.com/watch?v=pl${n}`, capabilities: caps('youtube', 'vod') })), raw: {} };
const BATCH = {
  id: 'batch-1', source_url: 'https://example.test/list', source_title: 'Weekend cooking playlist', source_provenance: {}, status: 'partial', selected_count: 3, queued_count: 0, duplicate_count: 0, completed_count: 2, failed_count: 1, progress: 67,
  format_selection: {}, output_profile: {}, created_at: AT, updated_at: AT, finished_at: AT,
  entries: [
    { id: 'e1', batch_id: 'batch-1', selection_index: 0, title: 'Sourdough basics', status: 'completed', progress: 100 },
    { id: 'e2', batch_id: 'batch-1', selection_index: 1, title: 'Knife skills', status: 'failed', progress: 0, error: 'This video is unavailable.' },
  ],
};
const press = (page: Page, name: string | RegExp, exact = false) => page.getByRole('button', { name, exact }).first().click();

const DIAGNOSTICS = {
  generated_at: '2026-09-24T10:00:00Z', status: 'degraded',
  versions: { lumina: '1.0.0', python: '3.11.9', yt_dlp: '2026.09.01', ffmpeg: '7.1', node: 'v22.9.0' },
  runtime: { ffmpeg_available: true, js_runtime_available: true, yt_dlp_ejs_available: true },
  storage_roots: [{ label: 'Main library', mode: 'managed', enabled: true, state: 'available', checked_at: '2026-09-24T09:40:00Z' }],
  queue: { jobs_by_status: { running: 1, queued: 4, completed: 180 }, concurrency: 2, event_streams: 3 },
  maintenance_sweeps: { consecutive_failures: 0, last_error: null, last_success_at: '2026-09-24T09:59:00Z', last_failure_at: null },
  persistence: {}, recent_errors: [], ai: { enabled: true, ok: true, model_available: true, error: null, asr_configured: false },
  recommendations: recoDiagnostics({ surfaces: [recoSurfaceStats()] }),
};

/** The two auth screens run first; mockApi keeps its logged-in flag per page, so later screens sign in once and then load straight in. */
const at = (path: string, ready?: (page: Page) => Promise<void>) => async (page: Page, _prefs?: Prefs) => {
  await page.goto(path);
  // The auth screen's loading stage is also .g-auth and gives way to the app on its own; only a sign-in field means signed out.
  await page.locator('.g-auth input, main[aria-label="Main content"]').first().waitFor();
  if (await page.locator('.g-auth input').count()) await signIn(page);
  await ready?.(page);
};

/** A list state opens its route with `ready` asserting a row is on screen: a populated screenshot, never an empty one under a list name. */
const populated = (path: string, setup: (page: Page) => Promise<unknown>, ready: (page: Page) => Promise<unknown>) => async (page: Page, prefs: Prefs) => { await setup(page); await at(path, async () => { await ready(page); })(page, prefs); };
const JOBS = [
  { id: 'job-run', source_url: 'https://example.test/run', status: 'running', title: 'A long documentary about river deltas and the people who map them every single season', progress: 42, downloaded_bytes: 420 * 1024 ** 2, speed: 6 * 1024 ** 2, eta: 95, created_at: AT },
  { id: 'job-wait', source_url: 'https://example.test/wait', status: 'queued', title: 'Waiting for a free slot', created_at: AT },
  { id: 'job-fail', source_url: 'https://example.test/fail', status: 'failed', title: 'A video that went private', error: 'This video is private. Lumina only saves public media.', created_at: AT },
  { id: 'job-done', source_url: 'https://example.test/done', status: 'completed', title: 'Evening concert recording', outputs: [{ library_item_id: item.id, root_label: 'Archive disk', folder: 'Video UHD' }], created_at: AT, finished_at: AT },
];
const COLLECTIONS = [{ ...COLLECTION, entries: [], item_count: 4 }, { ...COLLECTION, id: 'c2', name: 'Short films', item_count: 7, entries: [] }];
const DELETED = [1, 2, 3].map((n) => stillItem(`deleted-${n}`, { kind: 'video', title: `Removed clip ${n}`, extractor: 'youtube', user_id: 'member-1', status: 'missing', media_state: 'quarantined', downloaded_at: AT }));


const ACT_USER = { id: 'member-1', name: 'Maya' };
const ACTIVITY = {
  generated_at: '2026-10-02T12:00:00Z',
  sessions: [
    { id: 'a1', source: 'library', user: ACT_USER, title: 'Dune: Part Two', subtitle: null, item_id: 'm1', artwork_url: null, client: { kind: 'app', name: 'Infuse', device: 'Apple TV' }, method: 'transcode', video: { from: 'HEVC', to: 'H.264', height: 1080, tonemap: true }, audio: { from: 'EAC3', to: 'AAC' }, hardware: 'qsv', speed: 3.2, throttled: true, position_seconds: 2710, duration_seconds: 9960, started_at: new Date(Date.now() - 12 * 60_000).toISOString(), last_seen_at: '2026-10-02T12:00:00Z', stoppable: true },
    { id: 'a2', source: 'library', user: { id: 'member-2', name: 'Sam' }, title: 'Severance', subtitle: 'S2 · E4 · Woe\'s Hollow', item_id: 'e1', artwork_url: null, client: { kind: 'web', name: 'Lumina web', device: null }, method: 'direct', video: null, audio: null, hardware: null, speed: null, throttled: false, position_seconds: 1200, duration_seconds: 3300, started_at: new Date(Date.now() - 40 * 60_000).toISOString(), last_seen_at: '2026-10-02T12:00:00Z', stoppable: true },
    { id: 'a3', source: 'remote', user: ACT_USER, title: 'How a bridge is built, start to finish, in one very long uncut documentary title', subtitle: 'Engineering Channel', item_id: null, artwork_url: null, client: { kind: 'web', name: 'Lumina web', device: null }, method: 'relay', video: null, audio: null, hardware: null, speed: null, throttled: false, position_seconds: null, duration_seconds: null, started_at: new Date(Date.now() - 3 * 60_000).toISOString(), last_seen_at: '2026-10-02T12:00:00Z', stoppable: true },
  ],
  downloads: [{ id: 'd1', title: 'Lecture 4', user: ACT_USER, progress: 62, speed_bytes: 3_400_000, started_at: null }],
  recordings: [{ id: 'r1', title: 'Launch stream', user: { id: 'member-2', name: 'Sam' }, status: 'live', started_at: null }],
  server: { cpu_percent: 47, memory: { used_bytes: 6.2 * 1024 ** 3, total_bytes: 16 * 1024 ** 3 }, load_average: [1.8, 1.4, 1.1], uptime_seconds: 9 * 86400 + 3600 * 4, transcoder_cpu_percent: 31, ffmpeg_processes: 2, hardware: { mode: 'auto', active: 'qsv', disabled: false, failures: 0, fallbacks: 1 } },
};
const ACTIVITY_HISTORY = { next_before: '2026-10-01T20:00:00Z', items: [
  { id: 'h1', source: 'library', user: ACT_USER, title: 'Arrival', subtitle: null, item_id: 'm2', client: { kind: 'app', name: 'Infuse', device: 'Apple TV' }, method: 'transcode', hardware: 'qsv', video: null, started_at: '2026-10-02T09:00:00Z', ended_at: '2026-10-02T10:45:00Z', watched_seconds: 6300, stopped_by_admin: false },
  { id: 'h2', source: 'library', user: { id: 'member-2', name: 'Sam' }, title: 'Severance', subtitle: 'S2 · E3 · Who Is Alive?', item_id: 'e0', client: { kind: 'web', name: 'Lumina web', device: null }, method: 'direct', hardware: null, video: null, started_at: '2026-10-01T19:00:00Z', ended_at: '2026-10-01T19:20:00Z', watched_seconds: 1200, stopped_by_admin: true },
] };
const SCREENS: Shot[] = [
  { area: 'auth', state: 'login', open: async (page) => { await page.goto('/'); await expect(page.getByRole('heading', { name: 'Welcome home' })).toBeVisible(); } },
  { area: 'auth', state: 'picker', open: async (page) => {
    const ring = '**/api/session/device-members'; // only this screen has a ring; later sign-ins need the plain form
    await page.route(ring, (route) => route.fulfill({ json: [{ user_id: 'm2', display_name: 'Sam Rivers', username: 'sam', role: 'viewer', switch: 'instant', active: false }] }));
    await page.goto('/'); await expect(page.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
    await page.unroute(ring);
  } },
  // Signed-out sub-states: they run before the first sign-in and each forces its own 401.
  { area: 'auth', state: 'login-error', open: (page) => signInAs(page, 401, 'Incorrect username or password.') },
  { area: 'auth', state: 'rate-limited', open: (page) => signInAs(page, 429, 'Too many sign-in attempts. Try again in a few minutes.', 'Too many attempts. Try again in a minute.') },
  { area: 'auth', state: 'setup', open: async (page) => { await signedOut(page); await over(page, '**/api/bootstrap/status', { needs_setup: true }); await page.goto('/'); await expect(page.getByRole('button', { name: 'Create household vault' })).toBeVisible(); } },
  { area: 'auth', state: 'invite', open: async (page) => { await signedOut(page); await page.goto('about:blank'); await page.goto('/#invite=synthetictoken'); await expect(page.getByRole('button', { name: 'Join household' })).toBeVisible(); } },
  { area: 'auth', state: 'reset', open: async (page) => { await signedOut(page); await page.goto('about:blank'); await page.goto('/#reset=synthetictoken'); await expect(page.getByRole('button', { name: 'Set new password' })).toBeVisible(); } },
  { area: 'auth', state: 'picker-tiles', open: async (page) => {
    await signedOut(page); await over(page, '**/api/session/device-members', RING);
    await page.goto('/'); await expect(page.getByRole('button', { name: 'Alexandria, vault owner, needs password' })).toBeVisible(); } },
  { area: 'auth', state: 'picker-password', open: async (page) => {
    await signedOut(page); await over(page, '**/api/session/device-members', RING);
    await page.goto('/'); await page.getByRole('button', { name: 'Alexandria, vault owner, needs password' }).click(); await expect(page.getByLabel('Password for Alexandria')).toBeFocused(); } },
  { area: 'home', state: 'default', open: at('/') },
  { area: 'palette', state: 'results', open: async (page) => { await at('/')(page); await page.keyboard.press('ControlOrMeta+k'); await page.getByRole('combobox').fill('alp'); await expect(page.getByRole('group', { name: 'Movies' })).toBeVisible(); } },
  { area: 'library', state: 'all', open: at('/library') },
  { area: 'library', state: 'movies', open: at('/library/movies') },
  { area: 'library', state: 'music', open: at('/library/music') },
  { area: 'title', state: 'series', open: at('/title/series-1?season=1') },
  { area: 'live', state: 'default', open: at('/streaming/live') },
  { area: 'subscriptions', state: 'list', open: populated('/streaming/channels', (page) => over(page, '**/api/automations', FOLLOWS), (page) => expect(page.getByText('Harbor Films').first()).toBeVisible()) },
  { area: 'channel', state: 'videos', open: at('/channel/youtube/UCabcdefghijklmnopqrstuv') },
  { area: 'library', state: 'channels', open: at('/library/youtube?view=channels') },
  { area: 'explore', state: 'for-you', open: at('/streaming') },
  { area: 'streaming', state: 'home', open: at('/streaming') },
  { area: 'streaming', state: 'twitch', open: at('/streaming?provider=twitch') },
  { area: 'streaming', state: 'live', open: at('/streaming/live') },
  { area: 'streaming', state: 'live-wall', open: async (page) => { await at('/streaming/live?rail=gaming', async () => { await expect(page.locator('.g-rail-wall')).toBeVisible(); })(page); await page.locator('.g-rail-more').scrollIntoViewIfNeeded(); await page.waitForTimeout(600); } },
  { area: 'streaming', state: 'channels', open: populated('/streaming/channels', (page) => over(page, '**/api/automations', FOLLOWS), (page) => expect(page.getByText('Harbor Films').first()).toBeVisible()) },
  { area: 'home', state: 'picked-menu', open: async (page) => { await at('/')(page); await page.locator('[data-shelf-section="picked_for_you"]').getByRole('button', { name: /^More options for / }).first().click(); await expect(page.getByRole('menu')).toBeVisible(); } },
  { area: 'home', state: 'art-hover', open: async (page) => { await at('/')(page); await page.locator('.g-poster').first().scrollIntoViewIfNeeded(); await page.locator('.g-art-host').first().hover(); await expect(page.locator('.g-art-host').first().getByRole('button', { name: /^More options for / })).toHaveCSS('opacity', '1'); } },
  { area: 'home', state: 'art-menu', open: async (page) => { await at('/')(page); await page.locator('.g-poster').first().scrollIntoViewIfNeeded(); const host = page.locator('.g-art-host').first(); await host.hover(); await host.getByRole('button', { name: /^More options for / }).click(); await expect(page.getByRole('menu')).toBeVisible(); } },
  { area: 'library', state: 'poster-hover', open: async (page) => { await at('/library/movies')(page); const host = page.locator('.g-art-host').first(); await host.hover(); await expect(host.getByRole('button', { name: /^More options for / })).toHaveCSS('opacity', '1'); } },
  { area: 'library', state: 'poster-menu', open: async (page) => { await at('/library/movies')(page); const host = page.locator('.g-art-host').first(); await host.hover(); await host.getByRole('button', { name: /^More options for / }).click(); await expect(page.getByRole('menu')).toBeVisible(); } },
  { area: 'diagnostics', state: 'recommendations', open: at('/admin/diagnostics', async (page) => { await expect(page.getByRole('region', { name: 'Recommendations', exact: true })).toBeVisible(); }) },
  { area: 'watch', state: 'web-video', open: at(`/watch/library/${item.id}`) },
  { area: 'explore', state: 'query', open: at('/streaming/search?q=alpine') },
  // The card-grid landing and one section of each group.
  { area: 'settings', state: 'home', open: at('/settings') },
  { area: 'settings', state: 'activity', open: async (page) => {
    await over(page, '**/api/admin/activity/history**', ACTIVITY_HISTORY);
    await over(page, '**/api/admin/activity', ACTIVITY);
    await at('/settings/activity', async (shown) => { await expect(shown.getByText('Arrival')).toBeVisible(); })(page);
  } },
  { area: 'settings', state: 'discovery', open: at('/settings/discovery') },
  { area: 'settings', state: 'playback', open: at('/settings/playback') },
  { area: 'settings', state: 'members', open: at('/settings/members') },
  { area: 'settings', state: 'overview', open: at('/settings/overview') },
  { area: 'settings', state: 'ai', open: at('/settings/ai') },
  { area: 'settings', state: 'library', open: at('/settings/library') },
  { area: 'downloads', state: 'active', open: populated('/downloads', (page) => over(page, /\/api\/jobs(\?|$)/, { items: JOBS, next_cursor: null }), async (page) => { await expect(page.locator('.g-job').filter({ hasText: 'A video that went private' })).toBeVisible(); await expect(page.locator('.g-job').filter({ hasText: 'river deltas' })).toBeVisible(); await expect(page.locator('.g-job').filter({ hasText: 'Evening concert recording' })).toBeVisible(); }) },
  { area: 'collections', state: 'list', open: populated('/library/collections', (page) => over(page, '**/api/collections', COLLECTIONS), (page) => expect(page.locator('.g-collection-tile')).toHaveCount(2)) },
  { area: 'deleted', state: 'list', open: populated('/library/deleted', async (page) => { await over(page, '**/api/library/sections', { movies: 1, shows: 1, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 1, recordings: 0, deleted: 3 }); await over(page, /\/api\/library(\?|$)/, (route: Route) => route.fulfill({ json: { items: new URL(route.request().url()).searchParams.get('status') === 'missing' ? DELETED : [item], next_cursor: null } })); }, (page) => expect(page.locator('.g-deleted-rows li')).toHaveCount(3)) },
  { area: 'player', state: 'controls', open: async (page) => { await at(`/watch/library/${item.id}`)(page); await page.getByRole('slider').first().focus(); } },
  { area: 'player', state: 'mini', open: async (page) => { await at(`/watch/library/${item.id}`)(page); const drawer = page.getByRole('button', { name: 'Open navigation' }); // the sidebar is a drawer on phones
      if (await drawer.isVisible()) await drawer.click();
      await page.getByRole('button', { name: 'Home', exact: true }).and(page.locator('#primary-navigation button')).click(); } },
  // Spec 13.1 states (release-review fix): shell, palette, dialogs, settings, empty and error states, collections, player.
  { area: 'shell', state: 'sidebar-collapsed', open: async (page, prefs) => { prefs.sidebar_collapsed = true; await at('/')(page); } },
  { area: 'shell', state: 'profile-menu', open: async (page) => { await at('/')(page); await page.getByRole('button', { name: /account menu$/ }).click(); await expect(page.getByRole('menu')).toBeVisible(); } },
  { area: 'shell', state: 'activity-popover', open: async (page) => { await at('/')(page); await page.locator('.g-activity').first().hover(); await page.waitForTimeout(400); } },
  { area: 'shell', state: 'phone-drawer', only: 'phone', open: async (page) => { await at('/')(page); await page.getByRole('button', { name: 'Open navigation' }).click(); await expect(page.getByRole('dialog', { name: 'Mobile navigation' })).toBeVisible(); } },
  { area: 'shell', state: 'toast-error-action', open: async (page) => { await over(page, /\/api\/jobs(\?|$)/, (route: Route) => route.abort()); await at('/')(page); await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible(); } },
  { area: 'shell', state: 'toast', open: async (page) => { await at('/')(page); await press(page, 'Edit home'); await press(page, /reset/i); await expect(page.getByRole('button', { name: 'Undo' }).first()).toBeVisible(); } },
  { area: 'palette', state: 'empty', open: async (page) => { await at('/')(page); await page.keyboard.press('ControlOrMeta+k'); await expect(page.getByRole('combobox')).toBeFocused(); } },
  { area: 'palette', state: 'no-results', open: async (page) => { await at('/')(page); await page.keyboard.press('ControlOrMeta+k'); await page.getByRole('combobox').fill('zzzz'); await page.waitForTimeout(500); } },
  { area: 'palette', state: 'link-invalid', open: async (page) => { await at('/')(page); await press(page, 'Add a link'); await page.getByRole('combobox').fill('not a link'); await page.keyboard.press('Enter'); await expect(page.getByRole('alert')).toBeVisible(); } },
  { area: 'palette', state: 'error', open: async (page) => {
    await over(page, '**/api/search?*', { detail: 'down' }, 500); await over(page, '**/api/youtube-search', { detail: 'down' }, 500); // the palette shows its error only when the library and YouTube searches both fail
    await at('/')(page); await page.keyboard.press('ControlOrMeta+k'); await page.getByRole('combobox').fill('alp'); await expect(page.getByText('Search is unavailable right now.')).toBeVisible(); } },
  { area: 'palette', state: 'phone-sheet', only: 'phone', open: async (page) => { await at('/')(page); await page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: 'Search' }).click(); await expect(page.locator('dialog.g-palette')).toBeVisible(); await page.waitForTimeout(500); } },
  { area: 'member-picker', state: 'dialog', open: async (page) => {
    await over(page, '**/api/session/device-members', [{ user_id: 'm2', display_name: 'Sam Rivers', username: 'sam', role: 'viewer', switch: 'instant', active: false }]);
    await at('/')(page); await page.getByRole('button', { name: /account menu$/ }).click(); await page.getByRole('menuitem', { name: /^Switch member/ }).click(); await expect(page.getByRole('dialog')).toBeVisible(); } },
  { area: 'onboarding', state: 'interests', open: async (page) => {
    await onboard(page); await expect(page.getByLabel('Nature')).toBeVisible(); } },
  { area: 'onboarding', state: 'channels', open: async (page) => { await onboard(page); await finishInterests(page); await expect(page.getByRole('button', { name: /^Follow Harbor Films/ })).toBeVisible(); } },
  { area: 'onboarding', state: 'preparing', open: async (page) => {
    await onboard(page); await over(page, '**/api/onboarding/complete', () => undefined); // never answers: the preparing screen stays up
    await finishInterests(page); await press(page, 'Continue'); await expect(page.getByRole('progressbar').first()).toBeVisible(); await page.waitForTimeout(400); } },
  { area: 'onboarding', state: 'error', open: async (page) => {
    await onboard(page); await over(page, '**/api/onboarding/skip', { detail: 'down' }, 500); // the Skip failure is the one onboarding error the app renders; a failed finish on step 2 shows none (see the d-int report)
    await press(page, 'Skip for now'); await expect(page.getByText(/could not update your setup/)).toBeVisible(); } },
  { area: 'settings', state: 'save-bar', open: async (page) => { await at('/settings/overview')(page); await page.getByLabel('Simultaneous downloads').fill('3'); await expect(page.getByText('Unsaved changes').first()).toBeVisible(); } },
  { area: 'settings', state: 'search', open: async (page) => { await at('/settings')(page); await page.getByRole('searchbox', { name: 'Search settings' }).fill('play'); await page.waitForTimeout(300); } },
  { area: 'settings', state: 'jellyfin-import', open: async (page) => {
    await over(page, /\/api\/me\/jellyfin-import/, (route: Route) => route.request().method() === 'GET' ? route.fulfill({ json: { server: 'http://192.168.1.20:8096' } }) : route.fulfill({ json: { watched: 12, in_progress: 2, favorites: 3, up_to_date: 4, unmatched: 1, unmatched_names: ['Heat (1995)'] } }));
    await at('/settings/privacy')(page);
    const row = page.locator('[data-setting-id="privacy.jellyfin-history"]'); await row.getByLabel('Jellyfin username').fill('alice'); await row.getByLabel('Jellyfin password').fill('synthetic-pw');
    await row.getByRole('button', { name: 'Preview', exact: true }).click(); await expect(page.getByRole('dialog', { name: 'Import from Jellyfin?' })).toBeVisible(); } },
  { area: 'settings', state: 'confirm', open: async (page) => { await at('/settings/discovery')(page); await press(page, 'Clear recommendation history'); await expect(page.getByRole('dialog', { name: 'Clear recommendation history?' })).toBeVisible(); } },
  { area: 'settings', state: 'identify', open: async (page) => {
    await over(page, '**/api/admin/metadata/unmatched', [{ id: 'movie-1', name: 'Alpine Crossing', type: 'movie', category: 'movies', year: 2024, images: {} }]);
    await at('/settings/media')(page); await page.getByRole('button', { name: /^Identify / }).first().click(); await expect(page.getByRole('dialog')).toBeVisible(); } },
  { area: 'subscriptions', state: 'empty', open: at('/streaming/channels') },
  { area: 'subscriptions', state: 'error', open: async (page) => { await over(page, '**/api/automations', { detail: 'down' }, 500); await at('/streaming/channels')(page); await expect(page.getByRole('alert').first()).toBeVisible(); } },
  { area: 'subscriptions', state: 'follow-settings', open: async (page) => {
    await over(page, '**/api/automations', FOLLOWS); await over(page, '**/api/follows/refresh', FOLLOWS);
    await at('/subscriptions/yt-id')(page); await press(page, 'Follow settings'); await expect(page.getByRole('dialog')).toBeVisible(); } },
  { area: 'live', state: 'empty', open: async (page) => { await over(page, '**/api/discovery/live', liveSnapshotBody({ hero: [], items: [], state: 'empty' })); await at('/streaming/live')(page); } },
  { area: 'live', state: 'error', open: async (page) => { await over(page, '**/api/discovery/live', { detail: 'down' }, 500); await at('/streaming/live')(page); await expect(page.getByRole('alert').first()).toBeVisible(); } },
  { area: 'live', state: 'record', open: async (page) => { await at('/streaming/live')(page); await page.getByRole('button', { name: /^Record/ }).first().hover(); } },
  { area: 'downloads', state: 'empty', open: async (page) => { await over(page, /\/api\/jobs(\?|$)/, { items: [], next_cursor: null }); await at('/downloads')(page); } },
  // The boot gate waits on the first jobs answer, so the Downloads skeleton (loadState 'loading') never shows in a booted app; this is the loading screen a member sees.
  { area: 'auth', state: 'loading', open: async (page) => { await signedOut(page); await over(page, '**/api/session/me', () => undefined); await page.goto('/'); await expect(page.getByRole('heading', { name: 'Opening your vault' })).toBeVisible(); } },
  { area: 'downloads', state: 'playlist-batch', open: populated('/downloads', (page) => over(page, /\/api\/acquisition-batches(\?|$)/, [BATCH]), (page) => expect(page.getByText('Knife skills')).toBeVisible()) },
  { area: 'downloads', state: 'error', open: async (page) => { await over(page, /\/api\/jobs(\?|$)/, { detail: 'down' }, 500); await at('/downloads')(page); await expect(page.getByRole('alert').first()).toBeVisible(); } },
  { area: 'collections', state: 'empty', open: at('/library/collections') },
  { area: 'collections', state: 'detail', open: async (page) => {
    await over(page, /\/api\/collections(\/c1)?$/, COLLECTION); await over(page, '**/api/collections', [{ ...COLLECTION, entries: [] }]);
    await at('/library/collections/c1')(page); await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible(); await expect(page.getByText(item.title).first()).toBeVisible(); } },
  { area: 'collections', state: 'detail-empty', open: async (page) => {
    const empty = { ...COLLECTION, entries: [], item_count: 0 }; await over(page, /\/api\/collections(\/c1)?$/, empty); await over(page, '**/api/collections', [empty]); await at('/library/collections/c1')(page); await expect(page.getByRole('heading', { level: 1, name: 'Weekend watchlist' })).toBeVisible(); } },
  { area: 'collections', state: 'builder', open: async (page) => { await at('/library/collections?new=smart')(page); await expect(page.getByRole('dialog', { name: 'New smart collection' })).toBeVisible(); } },
  { area: 'deleted', state: 'empty', open: at('/library/deleted') },
  { area: 'library', state: 'channels-empty', open: async (page) => { await over(page, /\/api\/library\/channels(\?|$)/, []); await at('/library/youtube?view=channels')(page); } },
  // every playlist entry starts selected, so two are cleared
  { area: 'playlist', state: 'selection', open: async (page) => {
    await over(page, '**/api/preview', PLAYLIST);
    await at('/')(page); await press(page, 'Add a link'); await page.getByRole('combobox').fill(PLAYLIST.webpage_url); await page.keyboard.press('Enter');
    await expect(page.getByRole('checkbox', { name: /weeknight pasta/i })).toBeVisible(); // the preview answers after the palette closes
    await page.getByRole('checkbox', { name: /weeknight pasta/i }).uncheck(); await page.getByRole('checkbox', { name: /quick pickles/i }).uncheck(); await expect(page.getByRole('button', { name: 'Keep 2 videos' })).toBeVisible(); } },
  { area: 'player', state: 'panel', open: async (page) => { await at(`/watch/library/${item.id}`)(page); await press(page, 'Playback settings'); await expect(page.getByRole('group', { name: 'Playback settings' })).toBeVisible(); } },
  { area: 'player', state: 'captions', open: async (page, prefs) => { prefs.captions = { size: 'large', background: 'box' }; await at(`/watch/library/${item.id}`)(page); } },
  { area: 'player', state: 'theater', open: async (page) => { await at(`/watch/library/${item.id}`)(page); await press(page, /theater/i); await page.waitForTimeout(300); } },
  { area: 'player', state: 'skip', open: async (page) => { await episode(page, 0.3); await page.getByRole('button', { name: 'Skip intro' }).waitFor(); } },
  { area: 'player', state: 'up-next', open: async (page) => { await episode(page, 1.05); await expect(page.getByRole('region', { name: 'Next episode' })).toBeVisible(); } },
  { area: 'player', state: 'live-remote', open: async (page) => {
    const url = 'https://www.youtube.com/watch?v=live-watch1';
    await over(page, '**/api/preview', { kind: 'video', title: 'Harbor at night, live', webpage_url: url, entries: [], artwork_url: '/api/artwork/remote/p1', playback: null,
      capabilities: caps('youtube', 'live', { can_record: true, chat: { live: 'available', replay: 'unavailable' } }), chapters: [], description_timestamps: [],
      raw: { extractor_key: 'Youtube', uploader: 'Harbor Films', channel_id: CHANNEL_ID, channel_follower_count: 2_100_000, description: 'Live from the harbor.', concurrent_view_count: 12_412 } });
    await over(page, /\/api\/playback\/remote\//, null);
    await page.goto(`/watch?url=${encodeURIComponent(url)}`);
    await expect(page.getByRole('heading', { level: 1, name: 'Harbor at night, live' })).toBeVisible(); } },
  { area: 'player', state: 'error', open: async (page) => { await over(page, `**/api/library/${item.id}/media`, { detail: 'gone' }, 500); await at(`/watch/library/${item.id}`)(page); await page.waitForTimeout(1500); } },
  { area: 'home', state: 'undo-row', open: async (page) => { await at('/')(page); await page.locator('[data-shelf-section="picked_for_you"]').getByRole('button', { name: /^More options for / }).first().click(); await page.getByRole('menuitem', { name: 'Not interested' }).click(); await expect(page.getByRole('button', { name: 'Undo' }).first()).toBeVisible(); } },
];
/** Spec 13.1 states beyond the minimum: the release review must see them, so a missing key fails this spec. */
const REQUIRED = [
  'shell-sidebar-collapsed', 'shell-profile-menu', 'shell-activity-popover', 'shell-phone-drawer', 'shell-toast',
  'palette-empty', 'palette-no-results', 'palette-link-invalid', 'palette-phone-sheet', 'member-picker-dialog',
  'onboarding-interests', 'onboarding-channels', 'onboarding-preparing', 'onboarding-error', 'settings-save-bar', 'settings-search', 'settings-jellyfin-import', 'settings-confirm', 'settings-identify',
  'subscriptions-empty', 'subscriptions-error', 'subscriptions-follow-settings', 'live-empty', 'live-error', 'live-record',
  'downloads-empty', 'downloads-error', 'collections-empty', 'collections-detail', 'collections-builder', 'deleted-empty', 'library-channels-empty',
  'auth-login-error', 'auth-rate-limited', 'auth-setup', 'auth-invite', 'auth-reset', 'auth-picker-tiles', 'auth-picker-password',
  'palette-error', 'shell-toast-error-action', 'auth-loading', 'downloads-playlist-batch', 'collections-detail-empty', 'playlist-selection',
  'subscriptions-list', 'downloads-active', 'collections-list', 'deleted-list', 'collections-detail',
  'player-panel', 'player-captions', 'player-theater', 'player-skip', 'player-up-next', 'player-live-remote', 'player-error', 'home-undo-row',
];
test('the catalogue lists every 13.1 state', () => {
  const have = new Set(SCREENS.map((screen) => `${screen.area}-${screen.state}`));
  expect(REQUIRED.filter((key) => !have.has(key))).toEqual([]);
});

const ONLY = process.env.VISUAL_ONLY ? new Set(process.env.VISUAL_ONLY.split(',')) : null; // dev loop: comma list of area-state keys
const FORCED = new Set(['home-default', 'palette-results', 'settings-confirm', 'shell-profile-menu', 'settings-playback', 'settings-home', 'player-controls', 'live-default', 'home-picked-menu']);

test.describe.configure({ mode: 'parallel' });

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of VIEWPORTS) {
    test(`catalogue ${scheme} ${viewport.name}`, async ({ page }) => {
      test.setTimeout(480_000);
      page.setDefaultTimeout(15_000); // a broken state fails fast instead of eating the whole budget
      mkdirSync(OUT, { recursive: true });
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      const prefs: Prefs = { sidebar_collapsed: false, settings_advanced: true };
      await mockApi(page, prefs); await mockAdminFixtures(page); await mockTitles(page); await mockTranscripts(page); await mockPaletteSearch(page);
      await mockRemoteArtwork(page); await mockLive(page); await mockChannelPage(page); await mockLibraryChannels(page, 12); await mockReco(page); await mockPopular(page, popularWithForYou());
      await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(DIAGNOSTICS) }));
      if (ONLY) await at('/')(page); // a filtered dev run still needs the signed-in session the full run has by now
      for (const screen of SCREENS) {
        if (screen.only && screen.only !== viewport.name) continue;
        if (ONLY && !ONLY.has(`${screen.area}-${screen.state}`)) continue;
        await clearOverrides(page);
        for (const key of Object.keys(prefs)) delete prefs[key];
        prefs.sidebar_collapsed = false;
        prefs.settings_advanced = true; // advanced rows on, so Settings shots cover every row
        await screen.open(page, prefs);
        await page.waitForTimeout(300); // artwork fade and fonts settle
        await page.screenshot({ path: join(OUT, `${screen.area}-${screen.state}-${viewport.name}-${scheme}.png`) });
        if (scheme === 'light' && viewport.name === 'desktop' && FORCED.has(`${screen.area}-${screen.state}`)) {
          await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce', forcedColors: 'active' });
          await page.screenshot({ path: join(OUT, `${screen.area}-${screen.state}-${viewport.name}-forced.png`) });
          await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce', forcedColors: 'none' });
        }
      }
    });
  }
}

test.afterAll(() => {
  mkdirSync(OUT, { recursive: true });
  // The contact sheet: every image as one static page.
  const images = readdirSync(OUT).filter((name) => name.endsWith('.png')).sort();
  const cells = images.map((name) => `<figure><img src="${name}" loading="lazy" alt=""><figcaption>${name.replace('.png', '')}</figcaption></figure>`).join('\n');
  writeFileSync(join(OUT, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Lumina visual catalogue</title>
<style>body{margin:24px;font:14px system-ui}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}img{width:100%;border:1px solid}figure{margin:0}</style>
<main>${cells}</main>`);
});
