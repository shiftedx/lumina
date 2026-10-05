import { expect, type Page, type Route } from '@playwright/test';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_LENGTH, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';

/** Shared mocked Lumina API (synthetic media) for shell/theme/watch browser specs. */

export const user = { id: 'member-1', username: 'alexandria', display_name: 'Alexandria', role: 'admin', is_active: true, bio: '', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', onboarding_status: 'completed' };
export const item = {
  id: 'library-1', user_id: user.id, visibility: 'private', remote_id: 'synthetic-1', source_url: 'https://example.test/watch/1', webpage_url: 'https://example.test/watch/1',
  title: 'A deliberately long synthetic fixture title that should wrap cleanly across two lines in both themes', uploader: 'Lumina fixture', duration: 90, extractor: 'youtube', status: 'available',
  file_path: '/tmp/lumina-test/synthetic-1.mp4', file_size: SYNTHETIC_MEDIA_LENGTH, metadata_json: { description: 'theme fixture', view_count: 1 }, chapters: [],
  downloaded_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};
/** GET /api/me/access for an unrestricted member (2.8.0). */
export const OPEN_ACCESS = { sections: null, blocked_streaming: [], followed_only: false, allowed_now: true, until: null, remaining_minutes: null, can_download: true };
/** GET /api/admin/members/{id}/access for a member with no limits (2.8.0). */
const NO_LIMITS_ACCESS = { sections: null, movie_rating_max: null, tv_rating_max: null, unrated: 'allow', streaming: { youtube: true, twitch: true, kick: true, live: true, open_search: true, followed_only: false }, schedule: null, daily_limit_minutes: null, can_download: true };
const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
const rules = { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null };
const downloadDefaults = { format_selection: formatSelection, output_profile: outputProfile };
const automationDefaults = { cron_expression: '0 */6 * * *', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 };

export async function mockApi(page: Page, uiPrefs: Record<string, unknown>) {
  const saved: Record<string, unknown>[] = [];
  // Server-owned search history: a per-member in-memory list, most-recent-first, deduped case-insensitively.
  const searchHistory: { id: string; query: string; searched_at: string }[] = [];
  let searchHistorySeq = 0;
  const settings = () => ({
    id: 'settings-1', user_id: user.id, download_defaults: downloadDefaults, automation_defaults: automationDefaults, ui_prefs: uiPrefs, notification_prefs: {},
    remote_playback_cache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 }, resolved_download_defaults: downloadDefaults, resolved_automation_defaults: automationDefaults,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  });
  await page.addInitScript(() => {
    class QuietEventSource {
      onopen: ((event: Event) => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(new Event('open')), 0); }
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: QuietEventSource });
  });
  let loggedIn = false;
  let sessionUser = { ...user };
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON() as { display_name?: string };
        if (body.display_name) sessionUser = { ...sessionUser, display_name: body.display_name };
        return json({ user: sessionUser });
      }
      return loggedIn ? json({ user: sessionUser }) : json({ detail: 'not authenticated' }, 401);
    }
    if (path === '/api/session/login') { loggedIn = true; return json({ user: sessionUser }); }
    // Device ring: no member is remembered unless a spec says so.
    if (path === '/api/session/device-members') return json([]);
    if (path === '/api/public/showcase') return json({ slides: [] }); // the calm sign-in page unless a spec mocks releases
    if (path === '/api/session/switch') return json({ detail: 'not_in_ring' }, 404);
    if (path === '/api/session/forget') return route.fulfill({ status: 204, body: '' });
    if (path === '/api/me/jellyfin-import') return json({ server: null });
    if (path === '/api/me/export') {
      return json({
        exported_at: new Date().toISOString(), user: sessionUser, settings: settings(), follows: [], queue: [],
        notes: [], search_history: searchHistory, playback_progress: [], remote_playback_progress: [],
      });
    }
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', desktop_dir: null, data_dir: '/tmp/lumina-test' });
    if (path === '/api/library/sections') return json({ movies: 1, shows: 1, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: 1, recordings: 0, deleted: 0 });
    if (path === '/api/library') return json({ items: [item], next_cursor: null });
    if (path === '/api/titles') return json({ items: [], next_cursor: null });
    const empty = { items: [], categories: [], state: 'empty', refreshing: false, stale: false, last_success_at: null };
    if (path === '/api/discovery/popular') return json(empty);
    if (path === '/api/discovery/live') return json({ ...empty, twitch_available: true, hero: [] });
    if (path === '/api/automations' || path === '/api/admin/users' || path === '/api/playback/continue') return json([]);
    if (path === '/api/me/access') return json(OPEN_ACCESS);
    if (path === '/api/admin/sections' || path === '/api/admin/invites') return json([]);
    if (path === '/api/admin/public-address') return json({ public_address: null });
    if (path === '/api/admin/local-address') return json({ local_address: null, lan_http: false });
    if (/^\/api\/admin\/members\/[^/]+\/access$/.test(path)) return json({ ...NO_LIMITS_ACCESS, screen_time_today_seconds: 0, bonus_minutes_today: 0 });
    if (/^\/api\/admin\/members\/[^/]+\/activity$/.test(path)) return json({ screen_time_today_seconds: 0, recent: [] });
    if (path === '/api/admin/settings') return json({ library_root: '/tmp/l', temp_root: '/tmp/t', archive_path: '/tmp/a.txt', concurrency: 2, yt_dlp_defaults: {}, ui_prefs: {}, webhook_url: null, webhook_enabled: false, webhook_notify_new_videos: false, webhook_notify_failures: false });
    if (path === '/api/discovery/interests') return json({ categories: [], selected_keys: [] });
    if (path === '/api/discovery/suppressions') return json({ items: [], channels: [] });
    if (path === `/api/library/${item.id}`) return json(item);
    if (path === `/api/library/${item.id}/tags` || path === `/api/library/${item.id}/notes`) return json([]);
    if (path === `/api/library/${item.id}/playback`) return json({ item_id: item.id, position_seconds: 0, duration_seconds: 90, completed: false });
    if (path === `/api/library/${item.id}/media`) return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') });
    if (path === '/api/settings/me') {
      if (request.method() === 'PUT' || request.method() === 'PATCH') {
        const body = request.postDataJSON() as { ui_prefs?: Record<string, unknown> };
        saved.push(body);
        if (body.ui_prefs) Object.assign(uiPrefs, body.ui_prefs);
      }
      return json(settings());
    }
    if (path === '/api/collections') return json([]); // Home's Collections shelf: none, not a 404
    if (path === '/api/jobs') return json({ items: [], next_cursor: null });
    if (path === '/api/me/watch-queue') return json({ revision: 0, limit: 500, entries: [] });
    if (path === '/api/search/history') {
      if (request.method() === 'GET') return json(searchHistory);
      if (request.method() === 'POST') {
        const text = ((request.postDataJSON() as { query?: string }).query || '').trim();
        if (!text) return json(null);
        const existing = searchHistory.findIndex((entry) => entry.query.toLowerCase() === text.toLowerCase());
        if (existing >= 0) searchHistory.splice(existing, 1);
        const entry = { id: `history-${searchHistorySeq++}`, query: text, searched_at: new Date().toISOString() };
        searchHistory.unshift(entry);
        return json(entry);
      }
      if (request.method() === 'DELETE') {
        const deleted = searchHistory.length;
        searchHistory.length = 0;
        return json({ deleted });
      }
    }
    if (path.startsWith('/api/search/history/') && request.method() === 'DELETE') {
      const id = path.slice('/api/search/history/'.length);
      const index = searchHistory.findIndex((entry) => entry.id === id);
      if (index >= 0) searchHistory.splice(index, 1);
      return route.fulfill({ status: 204 });
    }
    if (path.startsWith('/api/discovery/')) return json({ items: [], categories: [] });
    // Media-vault endpoints answer empty unless a spec adds title fixtures, so every other spec keeps today's pages.
    if (path === '/api/titles/next-up' || path === '/api/connected-apps') return json([]);
    if (path === '/api/home/title-rows') return json({ rows: [] });
    return json({ detail: `mock: unhandled ${request.method()} ${path}` }, 404);
  });
  return saved;
}

/** One on-device model row as GET /api/admin/models returns it. */
export const localModel = (patch: Record<string, unknown> = {}) => ({
  id: 'granite-embedding-97m-multilingual-r2-q8', role: 'search', name: 'Granite Embedding 97M multilingual', description: 'Fast search model that understands many languages.',
  licence: 'Apache-2.0', size_bytes: 115_061_088, ram_bytes: 912_680_550, default: true, active: true, state: 'absent', bytes_done: null as number | null, bytes_total: null as number | null,
  reason: null as string | null, running: false, features: ['semantic_search'], ...patch,
});
/** The four catalog models, none installed. */
export const localModels = [
  localModel(),
  localModel({ id: 'nomic-embed-text-v1.5-q8', name: 'Nomic Embed Text v1.5', description: 'English-only search model.', size_bytes: 146_146_432, ram_bytes: 590_558_003, default: false, active: false }),
  localModel({ id: 'faster-whisper-small-int8', role: 'speech', name: 'Whisper small', description: 'Speech-to-text in 99 languages with word timings.', licence: 'MIT', size_bytes: 486_212_372, ram_bytes: 858_993_459, features: ['subtitles_from_speech', 'sync'] }),
  localModel({ id: 'faster-whisper-large-v3-turbo-int8', role: 'speech', name: 'Whisper large-v3-turbo (accurate)', description: 'More accurate speech-to-text.', licence: 'MIT', size_bytes: 1_621_665_983, ram_bytes: 1_717_986_918, default: false, active: false, features: ['subtitles_from_speech', 'sync'] }),
];
const readyFor = (requires: string | null) => ({ requires, ready: true, reason: null as string | null });
/** AiConfig.features with no model installed and an assistant server set. */
export const aiFeatureReadiness = {
  semantic_search: { requires: 'search_model', ready: false, reason: 'The search model is not installed' as string | null },
  subtitles_from_speech: { requires: 'speech_model', ready: false, reason: 'The speech model is not installed' as string | null },
  sync: readyFor(null), translate: readyFor('assistant'), recap: readyFor('assistant'), smart_collection_builder: readyFor('assistant'),
  match_tie_breaker: readyFor('assistant'), mute_strong_language: readyFor(null),
};

/** Admin GET fixtures for the a11y sweep, the TV spec and the registry coverage spec; admin writes fall through to mockApi. */
export async function mockAdminFixtures(page: Page) {
  const at = '2026-09-24T09:40:00Z';
  const root = { id: 'fast', label: 'Family NAS — movies and home videos archive', path: '/media/nas', mode: 'managed', enabled: true, minimum_free_bytes: 0, artifact_count: 1284, observation: { state: 'available', checked_at: at, free_bytes: 812e9, total_bytes: 2e12 } };
  const ADMIN: Record<string, unknown> = {
    overview: {
      generated_at: at, jobs_by_status: { completed: 184, running: 2, failed: 6 }, failure_window_days: 7, concurrency: 2, max_active_jobs_per_user: 25, min_free_disk_mb: 2048,
      recent_failures: [{ reason: 'ERROR: [youtube] Sign in to confirm your age. This video may be inappropriate for some users.', count: 4, last_at: at }],
      library_free_bytes: 812e9, library_items_by_status: { available: 1268, missing: 4 }, event_streams: 3, persistence: {},
      roots: [{ ...root, state: 'available', checked_at: at, free_bytes: 812e9, total_bytes: 2e12, artifact_bytes: 1.9e12 }],
    },
    diagnostics: {
      generated_at: at, status: 'degraded', versions: { lumina: '1.0.0', python: '3.11.9', yt_dlp: '2026.09.01', ffmpeg: '7.1', node: 'v22.9.0' },
      runtime: { ffmpeg_available: true, js_runtime_available: true, yt_dlp_ejs_available: true },
      storage_roots: [{ label: root.label, mode: 'managed', enabled: true, state: 'offline', checked_at: at }],
      queue: { jobs_by_status: { running: 1, queued: 4 }, concurrency: 2, event_streams: 3 },
      maintenance_sweeps: { consecutive_failures: 0, last_error: null, last_success_at: at, last_failure_at: null }, persistence: {},
      recent_errors: [{ source: 'summary', at, message: 'Local AI endpoint timed out' }],
      ai: { enabled: true, ok: true, model_available: true, error: null, asr_configured: false },
    },
    'ai/config': { base_url: 'http://127.0.0.1:8080/v1', model: 'local-model', has_api_key: false, max_concurrency: 3, context_tokens: 145000, asr_base_url: '', asr_model: '', enabled: true, asr_available: false, ai_features_disabled: [], features: aiFeatureReadiness, model_threads: null, model_threads_auto: 3 },
    models: { models: localModels },
    tasks: { items: [{ kind: 'download', id: 'd1', status: 'failed', title: 'A very long conference recording title that keeps going to prove the row wraps on phones', detail: 'youtube', owner: 'Alexandria', error: 'HTTP Error 403: Forbidden', attempts: 2, library_item_id: null, finished_at: null, can_cancel: false, can_retry: true, created_at: at }], next_cursor: null, counts: { download: { failed: 1 }, asr: {}, summary: {} } },
    users: [user, { ...user, id: 'm2', username: 'eleanor', display_name: 'Eleanor Whitcombe-Harrington (living-room TV)', role: 'viewer', is_active: false, screen_time_today_seconds: 3900, access: { sections: ['movies', 'anime'], movie_rating_max: 'G', tv_rating_max: 'TV-Y7', unrated: 'hide', streaming: { youtube: false, twitch: false, kick: false, live: false, open_search: false, followed_only: false }, schedule: { mon: [['07:00', '20:00']] }, daily_limit_minutes: 120, can_download: false } }],
    invitations: [{ id: 'i1', role: 'viewer', status: 'pending', created_at: at, expires_at: at }],
    'storage/roots': [root],
    'storage/rules': { revision: 1, default_root_id: 'fast', rules: [] },
    backups: { backups: [{ name: 'lumina-20260924T031500Z-scheduled', kind: 'scheduled', created_at: at, app_version: '1.0.0', schema_version: 1, size: 48e6, sha256: 'a', counts: { users: 2, library_items: 1272, library_notes: 38 } }], schedule: { daily: true, keep: 7 } },
    imports: [],
    'library/automation': { server_timezone: 'UTC', night_hour: 3, poller: { heartbeat_at: null, stalled: false }, roots: [] },
    'media-server': { jellyfin_enabled: true, jellyfin_url: 'https://vault.example', has_tmdb_key: true, metadata_language: 'en-US', introdb_enabled: false, hwaccel: 'auto', max_playback_sessions: 3, transcode_cache_gb: 10, jellyfin_import_url: null, anime_folders: ['Anime'], recategorising: false },
    'metadata/unmatched': [],
    artwork: { total: 41200, prepared: 38112, failed: 3, unsupported: 1, cache_bytes: 1.2e9, running: true, paused_for_playback: false, failure_reasons: { timeout: 3 }, serving: { hits_memory: 0, hits_disk: 0, misses: 0, generated: 0, fallback_original: 0, fallback_unavailable: 0, failures: 0 }, updated_at: at },
    settings: { concurrency: 2, max_active_jobs_per_user: 25, min_free_disk_mb: 2048 },
    'recording-retention': { keep_days: 30, max_gb: 0 },
    sections: [{ id: 'movies', label: 'Movies', count: 1204 }, { id: 'shows', label: 'TV shows', count: 310 }, { id: 'anime', label: 'Anime', count: 42 }, { id: 'vault', label: 'Videos and downloads', count: 96 }],
    invites: [{ id: 'iv1', email: 'grandad.at.the.cottage@example.com', status: 'pending', created_at: at, expires_at: '2026-10-01T09:40:00Z', sent_at: at, libraries: ['Movies', 'TV shows'], access: { display_name: 'Grandad' } }],
    'public-address': { public_address: null },
    'local-address': { local_address: null, lan_http: false },
    'members/m2/access': {
      sections: ['movies', 'anime'], movie_rating_max: 'G', tv_rating_max: 'TV-Y7', unrated: 'hide', streaming: { youtube: false, twitch: false, kick: false, live: false, open_search: false, followed_only: false },
      schedule: Object.fromEntries(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((day) => [day, [['07:00', '20:00']]])), daily_limit_minutes: 120, can_download: false, screen_time_today_seconds: 3900, bonus_minutes_today: 0,
    },
    'members/m2/follows': [{ id: 'f1', label: 'Bluey Official', source_url: 'https://www.youtube.com/@BlueyOfficial' }],
    'members/m2/activity': { screen_time_today_seconds: 3900, recent: [{ id: 'h1', source: 'library', user: { id: 'm2', name: 'Eleanor' }, title: 'Bluey', subtitle: 'Season 1 · Episode 3', item_id: null, client: { name: 'Lumina web', device: 'Living-room TV' }, method: 'direct', hardware: null, video: null, started_at: at, ended_at: at, watched_seconds: 420, stopped_by_admin: false }] },
  };
  await page.route(/\/api\/admin\/(overview|diagnostics|ai\/config|models|tasks|users|invitations|storage\/roots|storage\/rules|library\/automation|backups|imports|media-server|metadata\/unmatched|artwork|settings|recording-retention|sections|invites|public-address|local-address|members\/m2\/access|members\/m2\/follows|members\/m2\/activity)(\?|$)/, (route) => {
    const key = new URL(route.request().url()).pathname.replace('/api/admin/', '');
    return route.request().method() === 'GET' ? route.fulfill({ contentType: 'application/json', body: JSON.stringify(ADMIN[key]) }) : route.fallback();
  });
}

export async function signIn(page: Page) {
  const card = page.locator('.g-auth');
  await expect(card).toBeVisible();
  await page.getByLabel('Username').fill(user.username);
  await page.getByLabel('Password', { exact: true }).fill('correct-horse');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
}

const lines = [
  'Morning light reaches the ridge long before it reaches the valley floor.',
  'The villagers here have farmed these terraces for eleven generations.',
  'Every spring the old stone bridge floods, and every summer they rebuild it.',
  'Conservation here is less a policy than a habit passed down at the table.',
  'We followed the shepherds up to the high pastures above the tree line.',
  'Snowmelt feeds the lake, and the lake feeds almost everything else.',
];
export const transcriptCues = Array.from({ length: 240 }, (_, ordinal) => ({ ordinal, start_ms: ordinal * 1500, end_ms: ordinal * 1500 + 1400, text: lines[ordinal % lines.length] }));
export const transcripts = [
  { id: 'tr-en', library_item_id: item.id, language: 'en', source_kind: 'source_caption', revision: 2, cue_count: transcriptCues.length, model_label: null, created_at: '2026-01-02T00:00:00Z' },
  { id: 'tr-de', library_item_id: item.id, language: 'de', source_kind: 'source_caption', revision: 1, cue_count: transcriptCues.length, model_label: null, created_at: '2026-01-01T00:00:00Z' },
];

/** Transcript API over the shared fixture item (register after mockApi so these routes win). */
export async function mockTranscripts(page: Page) {
  await page.route(/\/api\/(library\/[^/]+\/transcripts|transcripts\/)/, async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === `/api/library/${item.id}/transcripts`) return json(transcripts);
    if (url.pathname.endsWith('/cues')) {
      const after = Number(url.searchParams.get('cursor') ?? -1);
      const limit = Number(url.searchParams.get('limit') || 50);
      const items = transcriptCues.filter((cue) => cue.ordinal > after).slice(0, limit);
      const last = items.at(-1);
      return json({ items, next_cursor: last && last.ordinal < transcriptCues.length - 1 ? String(last.ordinal) : null });
    }
    if (url.pathname.endsWith('/search')) {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return json(transcriptCues.filter((cue) => cue.text.toLowerCase().includes(q)).slice(0, 100));
    }
    return route.fallback();
  });
}

/** Title fixtures: one series with Specials, one movie with two versions, and the Library items behind them. */
const at = '2026-09-20T00:00:00Z';
const titleImage = (id: string, type = 'Primary') => `/api/titles/${id}/images/${type}`;
export const titleUserData = (patch: Record<string, unknown> = {}) => ({ played: false, is_favorite: false, position_seconds: 0, duration_seconds: null, last_watched_at: null, resume_item_id: null, unplayed_count: null, ...patch });
const titleSummary = (patch: Record<string, unknown>) => ({
  sort_name: null, year: null, index_number: null, index_number_end: null, parent_id: null, series_id: null, series_name: null, season_number: null, overview: null,
  genres: [] as string[], official_rating: null, community_rating: null, runtime_seconds: null, poster_url: null, backdrop_url: null, play_item_id: null as string | null,
  added_at: at, user_data: titleUserData(), ...patch,
}) as unknown as Record<string, unknown> & { id: string; name: string; play_item_id: string | null };
const detailOf = (summary: Record<string, unknown>, patch: Record<string, unknown> = {}) => ({
  ...summary, tagline: null, studios: [], premiered: null, end_date: null, status: null, logo_url: null, provider_ids: {}, people: [], versions: [], extras: [], children: [],
  boxset: null, aired_episode_count: null, play_next: null, match: null, has_recap: false, ...patch,
});
const EPISODE_NAMES = ['Low Tide', 'The Ferry', 'Fog Line'];
const episodeTitle = (season: number, index: number, patch: Record<string, unknown> = {}) => titleSummary({
  id: `ep-${season}-${index}`, type: 'episode', name: season ? EPISODE_NAMES[index - 1] : 'Behind the Lamp', index_number: index, season_number: season, parent_id: `season-${season}`,
  series_id: 'series-1', series_name: 'Harbor Lights', runtime_seconds: 90, play_item_id: `item-ep-${season}-${index}`, poster_url: titleImage(`ep-${season}-${index}`),
  overview: 'The keepers argue about the lamp while a storm rolls in from the north and the ferry is late again.', ...patch,
});
export const titleEpisodes: Record<number, ReturnType<typeof episodeTitle>[]> = {
  1: [1, 2, 3].map((index) => episodeTitle(1, index, { user_data: titleUserData({ played: true }) })),
  2: [episodeTitle(2, 1, { user_data: titleUserData({ played: true }) }), episodeTitle(2, 2, { user_data: titleUserData({ position_seconds: 40, duration_seconds: 90 }) }), episodeTitle(2, 3)],
  0: [episodeTitle(0, 1)],
};
const seasonTitle = (index: number) => titleSummary({ id: `season-${index}`, type: 'season', name: index ? `Season ${index}` : 'Specials', index_number: index, parent_id: 'series-1', series_id: 'series-1', series_name: 'Harbor Lights' });
export const seriesTitle = titleSummary({
  id: 'series-1', type: 'series', name: 'Harbor Lights', year: 2021, genres: ['Drama'], official_rating: 'TV-14', overview: 'Three keepers, one lighthouse, and a town that depends on both.',
  poster_url: titleImage('series-1'), backdrop_url: titleImage('series-1', 'Backdrop'), user_data: titleUserData({ last_watched_at: at, unplayed_count: 2 }),
});
export const movieTitle = titleSummary({
  id: 'movie-1', type: 'movie', name: 'Northern Lantern', year: 2019, genres: ['Adventure', 'Family'], official_rating: 'PG', runtime_seconds: 6720,
  overview: 'A girl and her grandfather carry a lamp across the ice.', poster_url: titleImage('movie-1'), backdrop_url: titleImage('movie-1', 'Backdrop'), play_item_id: 'item-movie-4k',
});
const allEpisodes = Object.values(titleEpisodes).flat();
const titleDetails: Record<string, unknown> = {
  'series-1': detailOf(seriesTitle, {
    children: [seasonTitle(1), seasonTitle(2), seasonTitle(0)], play_next: titleEpisodes[2][1], match: { method: 'id', score: null, at },
    people: [{ id: 'p1', name: 'Mara Quill', role: 'Keeper', type: 'Actor', image_url: null }],
  }),
  'movie-1': detailOf(movieTitle, {
    versions: [
      { item_id: 'item-movie-4k', label: null, container: 'mkv', video_codec: 'hevc', audio_codec: 'truehd', width: 3840, height: 2160, hdr: true, file_size: 58 * 1024 ** 3, duration_seconds: 6720, media_state: 'available' },
      { item_id: 'item-movie-1080', label: null, container: 'mp4', video_codec: 'h264', audio_codec: 'aac', width: 1920, height: 1080, hdr: false, file_size: 8 * 1024 ** 3, duration_seconds: 6720, media_state: 'available' },
    ],
    extras: [{ item_id: 'item-trailer', extra_type: 'trailer', name: 'Trailer', duration_seconds: 90, artwork_url: null }],
    match: { method: 'search', score: 0.71, at },
  }),
  ...Object.fromEntries(allEpisodes.map((episode) => [episode.id, detailOf(episode, { versions: [{ item_id: episode.play_item_id, height: 90, hdr: false, container: 'webm', media_state: 'available' }] })])),
};

function titleLibraryItem(id: string) {
  const episode = allEpisodes.find((entry) => entry.play_item_id === id);
  const base = { ...item, id, extractor: 'external_library', extra_type: null as string | null };
  if (episode) return { ...base, title: `Harbor Lights ${String(episode.id).replace('ep-', 'S').replace('-', 'E')}`, kind: 'episode', title_id: episode.id };
  if (id === 'item-movie-4k' || id === 'item-movie-1080') return { ...base, title: 'Northern Lantern', kind: 'movie', title_id: 'movie-1' };
  if (id === 'item-trailer') return { ...base, title: 'Northern Lantern trailer', kind: 'video', title_id: 'movie-1', extra_type: 'trailer' };
  return null;
}

function titlePoster(id: string): string {
  const hue = [...id].reduce((sum, char) => sum + char.charCodeAt(0) * 29, 0) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480"><rect width="320" height="480" fill="hsl(${hue} 36% 28%)"/><circle cx="220" cy="140" r="64" fill="hsl(${hue} 60% 72% / .3)"/></svg>`;
}

/** Title, identify and title-backed Library endpoints (register after mockApi so these routes win). */
export async function mockTitles(page: Page) {
  const calls: string[] = [];
  await page.route((url) => url.pathname.startsWith('/api/titles') || url.pathname === '/api/home/title-rows' || url.pathname.startsWith('/api/me/favorites/')
    || url.pathname.startsWith('/api/admin/titles/') || url.pathname === '/api/admin/metadata/unmatched' || /^\/api\/library\/item-/.test(url.pathname), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    calls.push(`${method} ${path}${url.search}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.includes('/images/')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: titlePoster(path) });
    if (path === '/api/titles') {
      const category = url.searchParams.get('category');
      const type = category === 'movies' ? 'movie' : category === 'shows' ? 'series' : url.searchParams.get('type');
      return json({ items: type === 'movie' ? [movieTitle] : type === 'series' ? [seriesTitle] : type === 'episode' ? [titleEpisodes[2][1]] : [], next_cursor: null });
    }
    if (path === '/api/titles/next-up') return json([titleEpisodes[2][1]]);
    if (path === '/api/home/title-rows') return json({ rows: [{ id: 'byw-series-1', kind: 'because_you_watched', title: 'Because you watched Harbor Lights', anchor_title_id: 'series-1', items: [movieTitle] }] });
    if (path === '/api/titles/series-1/episodes') return json(titleEpisodes[Number(url.searchParams.get('season'))] ?? []);
    if (path.endsWith('/similar')) return json([]);
    if (path.endsWith('/recap')) return json({ detail: 'Not found' }, 404);
    if (path.endsWith('/watched')) return json(titleUserData({ played: (request.postDataJSON() as { watched: boolean }).watched }));
    if (path.startsWith('/api/me/favorites/')) return route.fulfill({ status: 204 });
    if (path === '/api/admin/metadata/unmatched') return json([]);
    if (path.endsWith('/identify')) {
      return method === 'GET'
        ? json([{ tmdb_id: 11, name: 'Northern Lantern', original_name: null, year: 2019, overview: 'A girl and her grandfather carry a lamp across the ice.', poster_url: null, score: 0.94 }])
        : json({ title_id: 'movie-1', metadata_due_at: at }, 202);
    }
    if (/\/(unmatch|refresh)$/.test(path)) return json({ title_id: 'movie-1', metadata_due_at: at });
    const detail = /^\/api\/titles\/([^/]+)$/.exec(path);
    if (detail) return titleDetails[detail[1]] ? json(titleDetails[detail[1]]) : json({ detail: 'Not found' }, 404);
    const library = /^\/api\/library\/(item-[^/]+)(?:\/(.+))?$/.exec(path);
    const libraryItem = library ? titleLibraryItem(library[1]) : null;
    if (library && !libraryItem) return json({ detail: 'Not found' }, 404);
    if (library && libraryItem) {
      const part = library[2];
      if (!part) return json(libraryItem);
      if (part === 'media') return route.fulfill({ status: 200, contentType: SYNTHETIC_MEDIA_TYPE, body: Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64') });
      if (part === 'playback') {
        return method === 'PUT'
          ? json({ id: `pb-${libraryItem.id}`, user_id: user.id, item_id: libraryItem.id, ...(request.postDataJSON() as object), last_watched_at: at, created_at: at, updated_at: at, item: libraryItem })
          : json(null);
      }
      if (part === 'playback-options') return json({ mode: 'direct', reason: null, facts: { container: 'webm', video_codec: 'vp9', audio_codec: 'opus', width: 160, height: 90, duration: 1.5 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null });
      if (part === 'subtitle-tracks' || part === 'mute-ranges' || part === 'tags' || part === 'notes' || part === 'transcripts') return json([]);
      if (part === 'segments') return json({ item_id: libraryItem.id, segments: [] });
      if (part === 'up-next') {
        // The rest of the show in order, Specials left out; a movie outside a collection has none.
        const regular = [...titleEpisodes[1], ...titleEpisodes[2]];
        const at = regular.findIndex((episode) => episode.id === libraryItem.title_id);
        return at < 0 ? json({ kind: 'none', title: null, title_id: null, current_id: null, items: [] })
          : json({ kind: 'episodes', title: 'Harbor Lights', title_id: 'series-1', current_id: libraryItem.title_id, items: regular.slice(at + 1) });
      }
    }
    return route.fallback();
  });
  return calls;
}

export type Ring = Array<{ user_id: string; display_name: string; username: string; role: string; switch: 'instant' | 'password'; active: boolean }>;
/** Answers the three ring routes; `switched` flips the session to the chosen member. */
export async function mockDeviceRing(page: Page, members: Ring) {
  const calls: string[] = [];
  await page.route('**/api/session/device-members', (route) => route.fulfill({ json: members }));
  await page.route('**/api/session/switch', async (route) => {
    const { user_id: id } = route.request().postDataJSON() as { user_id: string };
    calls.push(`switch:${id}`);
    const target = members.find((member) => member.user_id === id);
    if (!target) return route.fulfill({ status: 404, json: { detail: 'not_in_ring' } });
    if (target.switch === 'password') return route.fulfill({ status: 401, json: { detail: 'password_required' } });
    return route.fulfill({ json: { user: { ...user, id: target.user_id, username: target.username, display_name: target.display_name, role: target.role }, csrf_token: 'mock' } });
  });
  await page.route('**/api/session/forget', (route) => { calls.push('forget'); return route.fulfill({ status: 204, body: '' }); });
  return calls;
}

/** Range-capable synthetic media, so seeking a paused clip sticks (a plain 200 restarts it, which a loaded host races). */
export async function fulfillRangeMedia(route: Route) {
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
  const from = Number(start) || 0;
  const to = end ? Number(end) : media.length - 1;
  await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
}

/** The 1.5 s episode's intro (0.1-0.6 s) and credits (1.0-1.5 s) segments: skip button and up-next card. */
export const episodeSegments = (id: string) => ({ item_id: id, segments: [
  { type: 'intro', start_seconds: 0.1, end_seconds: 0.6, source: 'fingerprint', confidence: 0.9 },
  { type: 'credits', start_seconds: 1.0, end_seconds: 1.5, source: 'heuristic', confidence: 0.7 }] });

/** Pause the player at `seconds` and wait until the seek landed. */
export const pauseAt = (page: Page, seconds: number) => page.locator('video').evaluate(async (media: HTMLVideoElement, at: number) => {
  media.muted = true; media.pause();
  if (Math.abs(media.currentTime - at) > 0.02) await new Promise<void>((resolve) => { media.addEventListener('seeked', () => resolve(), { once: true }); media.currentTime = at; });
}, seconds);
