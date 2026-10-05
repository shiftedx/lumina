import { expect, test, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { strFromU8, strToU8, unzipSync, zipSync } from 'fflate';
import { OPEN_ACCESS } from './lumina-mock';
import { assertFocusIndicator, auditA11y } from './release-evidence';
import { redactTraceArchive } from './redacting-artifact-reporter';

const LONG_TITLE = 'Eine außergewöhnlich ausführliche Dokumentation über Donaudampfschifffahrtsgesellschaftskapitäne, gemeinschaftliche Erinnerungen und sorgfältig bewahrte Familiengeschichten';
const LONG_DESCRIPTION = 'Diese ausführliche Beschreibung verwendet absichtlich längere lokalisierte Wörter und zusammenhängende Sätze, damit Metadaten auch bei vergrößerter Darstellung als lesbare Zeilen innerhalb des verfügbaren Inhaltsbereichs umbrechen.';

const user = {
  id: 'member-1',
  username: 'alexandria',
  display_name: 'Alexandria With A Deliberately Long Localized Profile Name',
  role: 'viewer',
  is_active: true,
  bio: '',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const outputProfile = {
  base_path: null,
  subdir: '',
  template: '%(title)s.%(ext)s',
  organize_by: 'downloads',
};

const formatSelection = {
  preset: 'best_1080p',
  custom_format: null,
  extract_audio: false,
  audio_format: null,
  embed_thumbnail: true,
  embed_metadata: true,
  subtitles: 'none',
  output_container: 'mp4',
};

const settings = {
  id: 'settings-1',
  user_id: user.id,
  download_defaults: { format_selection: formatSelection, output_profile: outputProfile },
  automation_defaults: {
    cron_expression: '0 */6 * * *',
    auto_download: false,
    format_selection: formatSelection,
    output_profile: outputProfile,
    rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null },
    duplicate_policy: 'skip_same_source',
    max_items_per_run: 20,
    max_items_per_day: null,
    backfill_limit: 10,
  },
  ui_prefs: {},
  notification_prefs: {},
  resolved_download_defaults: { format_selection: formatSelection, output_profile: outputProfile },
  resolved_automation_defaults: {
    cron_expression: '0 */6 * * *',
    auto_download: false,
    format_selection: formatSelection,
    output_profile: outputProfile,
    rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null },
    duplicate_policy: 'skip_same_source',
    max_items_per_run: 20,
    max_items_per_day: null,
    backfill_limit: 10,
  },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const library = Array.from({ length: 8 }, (_, index) => ({
  id: `library-${index + 1}`,
  user_id: user.id,
  visibility: 'private',
  remote_id: `remote-${index + 1}`,
  source_url: `https://example.test/watch/${index + 1}`,
  webpage_url: `https://example.test/watch/${index + 1}`,
  title: index === 0 ? LONG_TITLE : `Lokalisierter Sammlungseintrag Nummer ${index + 1}`,
  uploader: 'Ein sehr ausführlicher Name des Familienkanals',
  duration: 3720,
  extractor: 'youtube',
  status: 'available',
  metadata_json: {
    description: LONG_DESCRIPTION,
    channel: 'Ein sehr ausführlicher Name des Familienkanals',
    uploader: 'Ein sehr ausführlicher Name des Familienkanals',
    channel_url: 'https://example.test/channel/family',
    view_count: 1200,
    height: index === 0 ? 1080 : 720,
  },
  chapters: index === 0 ? [
    { start_time: 0, end_time: 120, title: 'Opening' },
    { start_time: 120, end_time: 3720, title: 'Main story' },
  ] : [],
  downloaded_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}));

const jobs = [{
  id: 'job-1',
  user_id: user.id,
  source_url: 'https://example.test/watch/download',
  status: 'running',
  progress: 42,
  artwork_url: '/api/artwork/remote/job-artwork',
  preview_snapshot: { title: LONG_TITLE },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}];

const automation = {
  id: 'automation-1',
  user_id: user.id,
  label: 'Ein ungewöhnlich ausführlicher abonnierter Familienkanal',
  source_url: 'https://example.test/channel/family',
  source_type: 'channel',
  artwork_url: '/api/artwork/remote/channel-avatar',
  cron_expression: '0 */6 * * *',
  active: true,
  auto_download: false,
  format_selection: formatSelection,
  output_profile: outputProfile,
  rules: settings.automation_defaults.rules,
  duplicate_policy: 'skip_same_source',
  max_items_per_run: 20,
  max_items_per_day: null,
  backfill_limit: 10,
  last_checked_at: '2026-01-01T00:00:00',
  next_check_at: null,
  last_error: null,
  last_run_summary: {},
  // The server-side follow refresh keeps the newest entries; the feed renders them directly.
  feed_entries: library.slice(0, 4).map((item) => ({ id: item.remote_id, title: item.title, uploader: item.uploader, duration: item.duration, artwork_url: `/api/artwork/remote/video-${item.remote_id}`, webpage_url: item.webpage_url })),
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

async function installApi(page: Page, options: { onboarding?: 'pending'; discoveryHome?: 'failing'; channelSuggestions?: 'degraded' } = {}) {
  const interestCategories = [{ key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }];
  const interestLabel = (key: string) => interestCategories.find((category) => category.key === key)?.label || key;
  const channelCandidate = (displayName: string, sourceUrl: string, following = false) => ({
    channel_key: sourceUrl, source_url: sourceUrl, display_name: displayName, source: 'youtube',
    source_label: 'YouTube', artwork_url: null, category_keys: [], following,
  });
  let selectedInterestKeys: string[] = [];
  const createdFollows: Array<typeof automation> = [];
  type SuppressionRecord = { id: string; scope: 'item' | 'channel'; source_id: string | null; source_url: string | null; target_key: string; title: string | null; channel_name: string | null; source: string };
  let suppressions: SuppressionRecord[] = [];
  let suppressionSeq = 0;
  const homeMusicItems = [
    { id: 'home-music-1', title: 'A personalized music pick', uploader: 'Lumina fixture', artwork_url: '/api/artwork/remote/home-music-1', webpage_url: 'https://example.test/watch/home-music-1', category_keys: ['music'] },
    { id: 'home-music-2', title: 'Another music pick', uploader: 'Second Fixture', artwork_url: '/api/artwork/remote/home-music-2', webpage_url: 'https://example.test/watch/home-music-2', category_keys: ['music'] },
  ];
  const isSuppressed = (item: { id: string; webpage_url: string; uploader: string }) => suppressions.some((entry) => (
    (entry.scope === 'item' && (entry.source_id === item.id || entry.source_url === item.webpage_url))
    || (entry.scope === 'channel' && entry.target_key === item.uploader.trim().toLowerCase())
  ));
  let onboardingStatus: 'pending' | 'completed' | 'skipped' = options.onboarding === 'pending' ? 'pending' : 'completed';
  await page.addInitScript(() => {
    class QuietEventSource {
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      constructor(_url: string) { setTimeout(() => this.onopen?.(new Event('open')), 0); }
      addEventListener() {}
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { configurable: true, value: QuietEventSource });
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/session/me') return json({ user: { ...user, onboarding_status: onboardingStatus } });
    if (path === '/api/onboarding/complete') {
      const body = request.postDataJSON() as { keys?: unknown; follows?: Array<{ source_url?: unknown; display_name?: unknown }> };
      selectedInterestKeys = Array.isArray(body.keys) ? body.keys.filter((key): key is string => typeof key === 'string') : [];
      const followed = (Array.isArray(body.follows) ? body.follows : []).map((follow) => {
        const sourceUrl = typeof follow.source_url === 'string' ? follow.source_url : '';
        const displayName = typeof follow.display_name === 'string' ? follow.display_name : '';
        const valid = sourceUrl.includes('youtube.com') && Boolean(displayName);
        if (!valid) return { channel_key: sourceUrl, display_name: displayName, status: 'invalid', automation_id: null };
        if (createdFollows.some((entry) => entry.source_url === sourceUrl)) {
          return { channel_key: sourceUrl, display_name: displayName, status: 'existing', automation_id: null };
        }
        const id = `follow-${createdFollows.length + 1}`;
        createdFollows.push({ ...automation, id, label: displayName, source_url: sourceUrl, source_type: 'channel', auto_download: false });
        return { channel_key: sourceUrl, display_name: displayName, status: 'created', automation_id: id };
      });
      onboardingStatus = 'completed';
      return json({ status: onboardingStatus, selected_keys: selectedInterestKeys, followed });
    }
    if (path === '/api/discovery/channels') {
      const categories = url.searchParams.getAll('keys').map((key) => (
        options.channelSuggestions === 'degraded'
          ? { key, label: interestLabel(key), state: 'curated', channels: [channelCandidate(`Curated ${interestLabel(key)} Channel`, `https://www.youtube.com/@curated-${key}`)] }
          : { key, label: interestLabel(key), state: 'ranked', channels: [channelCandidate(`Popular ${interestLabel(key)} Channel`, `https://www.youtube.com/@popular-${key}`)] }
      ));
      return json({ categories });
    }
    if (path === '/api/discovery/channels/search') {
      return json({ query: (request.postDataJSON() as { query?: string }).query || '', channels: [channelCandidate('Searched Creator', 'https://www.youtube.com/@searchedcreator')] });
    }
    if (path === '/api/onboarding/skip') {
      if (onboardingStatus === 'pending') onboardingStatus = 'skipped';
      return json({ status: onboardingStatus, selected_keys: selectedInterestKeys });
    }
    if (path === '/api/health' || path === '/api/runtime-health') return json({ status: 'ok', desktop_dir: null, data_dir: '/tmp/lumina-test' });
    if (path === '/api/bootstrap/status') return json({ needs_setup: false });
    if (path === '/api/jobs') return json({ items: jobs, next_cursor: null });
    if (path === '/api/library/refresh') return json({ items: library, next_cursor: null });
    if (path === '/api/library/sections') return json({ movies: 0, shows: 0, anime: 0, albums: 0, artists: 0, saved_audio: 0, youtube: library.length, recordings: 0, deleted: 0 });
    if (path === '/api/library') return json({ items: library, next_cursor: null });
    if (path === '/api/playback/continue') return json([]);
    if (path === '/api/discovery/popular') return json({
      items: [{ id: 'popular-1', title: 'A gradually discovered favorite', uploader: 'Lumina fixture', artwork_url: '/api/artwork/remote/popular-1', webpage_url: 'https://example.test/watch/popular-1', category_keys: ['documentaries'] }],
      categories: [{ key: 'documentaries', label: 'Documentaries', state: 'ready' }], state: 'partial', refreshing: false, stale: false, error: null,
    });
    if (path === '/api/discovery/interests') {
      if (request.method() === 'PUT') selectedInterestKeys = (request.postDataJSON() as { keys?: unknown[] }).keys?.filter((key): key is string => typeof key === 'string') || [];
      return json({ categories: interestCategories, selected_keys: selectedInterestKeys });
    }
    if (path === '/api/discovery/home') {
      if (options.discoveryHome === 'failing') return json({ items: [], categories: [], state: 'failed', refreshing: false, stale: false, error: 'temporarily unavailable' });
      const visible = selectedInterestKeys.includes('music') ? homeMusicItems.filter((item) => !isSuppressed(item)) : [];
      return json({
        items: visible,
        categories: interestCategories.filter((category) => selectedInterestKeys.includes(category.key)),
        state: selectedInterestKeys.length ? (visible.length ? 'ready' : 'empty') : 'empty', refreshing: false, stale: false, error: null,
      });
    }
    if (path === '/api/discovery/suppressions') {
      if (request.method() === 'POST') {
        const body = request.postDataJSON() as { scope: 'item' | 'channel'; source_id?: string | null; source_url?: string | null; title?: string | null; uploader?: string | null; source?: string };
        const targetKey = body.scope === 'channel' ? String(body.uploader || '').trim().toLowerCase() : String(body.source_id || body.source_url || '');
        const existing = suppressions.find((entry) => entry.scope === body.scope && entry.target_key === targetKey);
        if (existing) return json(existing);
        const record: SuppressionRecord = {
          id: `sup-${++suppressionSeq}`, scope: body.scope, source_id: body.source_id ?? null, source_url: body.source_url ?? null,
          target_key: targetKey, title: body.title ?? null, channel_name: body.uploader ?? null, source: body.source ?? 'youtube',
        };
        suppressions.push(record);
        return json(record);
      }
      return json({ items: suppressions.filter((entry) => entry.scope === 'item'), channels: suppressions.filter((entry) => entry.scope === 'channel') });
    }
    if (/^\/api\/discovery\/suppressions\/[^/]+$/.test(path) && request.method() === 'DELETE') {
      const id = decodeURIComponent(path.split('/').pop() || '');
      suppressions = suppressions.filter((entry) => entry.id !== id);
      return route.fulfill({ status: 204 });
    }
    if (path === '/api/auth-profiles') return json([{ id: 'auth-1', user_id: user.id, label: 'Privater Familienzugang', kind: 'cookie_file', has_password: false, has_token: false }]);
    if (path === '/api/settings/me') return json(settings);
    if (path === '/api/automations') return json([automation, ...createdFollows]);
    if (path === '/api/discovery/up-next') return json({ items: [], categories: [], state: 'ready', refreshing: false, stale: false, error: null });
    if (path === '/api/discovery/live') return json({
      items: [{ id: 'live-1', title: 'A gaming stream happening now', uploader: 'Lumina fixture', webpage_url: 'https://example.test/watch/live-1', category_keys: ['gaming'], capabilities: { provider: 'youtube', lifecycle: 'live', can_play: true, can_acquire: false, chat: { live: 'unavailable', replay: 'unavailable' } } }],
      categories: [{ key: 'gaming', label: 'Gaming', state: 'ready' }],
      state: 'ready', refreshing: false, stale: false, twitch_available: true, hero: [],
    });
    if (path === '/api/preview') {
      const sourceUrl = typeof (request.postDataJSON() as { source_url?: unknown } | null)?.source_url === 'string'
        ? (request.postDataJSON() as { source_url: string }).source_url : '';
      if (sourceUrl.includes('/watch/home-music-1')) {
        return json({ kind: 'video', title: 'A personalized music pick', webpage_url: sourceUrl, uploader: 'Lumina fixture', availability: 'public', capabilities: { provider: 'youtube', lifecycle: 'vod', can_play: true, can_acquire: true, chat: { live: 'unavailable', replay: 'unavailable' } }, playback: null, artwork_url: '/api/artwork/remote/home-music-1', chapters: [], description_timestamps: [], entries: [], raw: { id: 'home-music-1', title: 'A personalized music pick', uploader: 'Lumina fixture', duration: 200 } });
      }
      return json({ kind: 'playlist', title: automation.label, artwork_url: '/api/artwork/remote/channel-avatar', webpage_url: automation.source_url, entries: library.slice(0, 4).map((item) => ({ id: item.remote_id, title: item.title, uploader: item.uploader, duration: item.duration, artwork_url: `/api/artwork/remote/video-${item.remote_id}`, webpage_url: item.webpage_url })), raw: {} });
    }
    if (path.startsWith('/api/artwork/remote/')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900"><rect width="900" height="900" fill="#356258"/></svg>' });
    if (/^\/api\/library\/[^/]+\/notes$/.test(path)) return json([]);
    if (/^\/api\/library\/[^/]+\/provenance$/.test(path)) return route.fulfill({ status: 404 });
    // Media-vault endpoints this fixture predates: absent, as on a server without them.
    if (/^\/api\/library\/[^/]+\/(subtitle-tracks|segments)$/.test(path)) return route.fulfill({ status: 404 });
    if (path === '/api/acquisition-batches') return json([]);
    if (/^\/api\/library\/[^/]+\/tags$/.test(path)) return json([]);
    if (/^\/api\/library\/[^/]+\/playback$/.test(path)) return json({ item_id: path.split('/')[3], position_seconds: 0, duration_seconds: 3720, completed: false });
    if (/^\/api\/library\/[^/]+$/.test(path)) return json(library.find((item) => item.id === path.split('/')[3]) || library[0]);
    if (/^\/api\/library\/[^/]+\/media$/.test(path)) return route.fulfill({ status: 204 });
    if (path === '/api/collections') return json([]);
    if (path === '/api/me/access') return json(OPEN_ACCESS);
    return json({});
  });
}

async function openApp(page: Page, width: number, height = 900, options: { onboarding?: 'pending'; discoveryHome?: 'failing'; channelSuggestions?: 'degraded' } = {}) {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await installApi(page, options);
  await page.goto('/');
  if (options.onboarding === 'pending') return;
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page) {
  const geometry = await page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
  expect(geometry.scroll).toBeLessThanOrEqual(geometry.client);
}

/** The --g-control floor: 36px for a mouse or trackpad, 44px for touch and remotes (tokens.css). */
const targetFloor = (page: Page) => page.evaluate(() => (matchMedia('(pointer: fine) and (min-width: 600px)').matches ? 36 : 44));

async function expectEffectiveTargets(page: Page) {
  const floor = (await targetFloor(page)) - .5;
  const undersized = await page.locator('button:visible, a:visible, summary:visible, select:visible').evaluateAll((elements, min) => elements
    .map((element) => ({ name: element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName, ...element.getBoundingClientRect().toJSON() }))
    .filter((rect) => rect.width < min || rect.height < min), floor);
  // Radio/checkbox controls are small by nature, so their effective pointer
  // target is the enclosing <label>, whose whole box toggles the control.
  // Measure that box (not the ~18px input) so CI verifies every toggle meets
  // the 44px norm. This is a blanket sweep (issue #113): the previously
  // exempted sub-44px toggles (subscriptions auto-download, chat-rail
  // Hide removed / Follow playback) now size their labels to >=44px.
  const undersizedChoices = await page.locator('input[type=radio]:visible, input[type=checkbox]:visible').evaluateAll((inputs, min) => inputs
    .map((input) => {
      const label = input.closest('label') ?? input;
      const rect = label.getBoundingClientRect();
      return { name: input.getAttribute('aria-label') || label.textContent?.trim() || input.getAttribute('name') || 'choice', width: rect.width, height: rect.height };
    })
    .filter((rect) => rect.width < min || rect.height < min), floor);
  expect([...undersized, ...undersizedChoices]).toEqual([]);
}

async function navigateWithMobileTabs(page: Page, label: string, heading: RegExp) {
  await page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: label }).click();
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
}

// Subscriptions has no tab on a phone: it is reached through the More drawer.
async function navigateWithMore(page: Page, label: string, heading: RegExp) {
  await page.getByRole('button', { name: /^More(,|$)/ }).click();
  await page.getByRole('dialog', { name: 'Mobile navigation' }).getByRole('button', { name: label }).click();
  await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
}

async function coverEverySurface(page: Page) {
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
  await navigateWithMobileTabs(page, 'Streaming', /^Streaming$/);
  // Your channels is a view of the Streaming page now (segmented control), not its own tab or More entry.
  await page.getByRole('radio', { name: 'Your channels' }).check({ force: true });
  await expect(page.getByRole('heading', { level: 2, name: 'Latest from these channels' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
  await expect(page.locator('.g-channel-tile .g-avatar img').first()).toBeVisible();
  await expect(page.locator('.g-channels-wall .g-still img').first()).toBeVisible();
  const avatarGeometry = await page.locator('.g-channel-tile .g-avatar').first().evaluate((avatar) => {
    const style = getComputedStyle(avatar);
    const bounds = avatar.getBoundingClientRect();
    return { width: bounds.width, height: bounds.height, borderRadius: style.borderRadius };
  });
  // 96px avatars, 88px in the phone's three columns (under 600px).
  const avatarSize = (page.viewportSize()?.width ?? 0) < 600 ? 88 : 96;
  expect(avatarGeometry).toEqual({ width: avatarSize, height: avatarSize, borderRadius: '50%' });
  await navigateWithMobileTabs(page, 'Library', /^Library$/);
  await page.getByRole('navigation', { name: 'Library' }).getByRole('link', { name: 'YouTube' }).click();
  await page.getByRole('button', { name: new RegExp(`^${LONG_TITLE}, `) }).click();
  await expect(page.getByRole('heading', { level: 1, name: LONG_TITLE })).toBeVisible();
  await page.getByRole('tab', { name: 'Notes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Add current time' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole('tab', { name: 'Sharing & tags' }).click();
  await expect(page.getByRole('heading', { name: 'About this Library item' })).toBeVisible();
  await expect(page.getByRole('combobox', { name: `Who can see ${LONG_TITLE}` })).toBeEnabled();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('dialog', { name: 'Mobile navigation' }).getByRole('button', { name: 'Downloads' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /^Downloads$/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('dialog', { name: 'Mobile navigation' }).getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: /^Settings$/ })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectEffectiveTargets(page);
}

test('all primary surfaces reflow at the 320 CSS-pixel narrow standard', async ({ page }) => {
  await openApp(page, 320, 760);
  await coverEverySurface(page);
});

test('all primary surfaces reflow at a standards-realistic 200% equivalent viewport', async ({ page }) => {
  // A 1280px-wide browser at 200% exposes roughly 640 CSS pixels. Reducing the
  // layout viewport exercises actual reflow; CSS zoom and DPR changes do not.
  await openApp(page, 640, 900);
  await coverEverySurface(page);
});

test('closed drawer clips every descendant and the open drawer remains scrollable', async ({ page }) => {
  await openApp(page, 320, 480);
  const drawer = page.locator('.g-sidebar');
  await expect(drawer).toHaveCSS('visibility', 'hidden');
  const closed = await drawer.evaluate((element) => ({
    right: element.getBoundingClientRect().right,
    overflowX: getComputedStyle(element).overflowX,
  }));
  expect(closed.right).toBeLessThanOrEqual(0);
  expect(closed.overflowX).toMatch(/clip|hidden/);

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(drawer).toHaveCSS('visibility', 'visible');
  const open = await drawer.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(open.overflowY).toBe('auto');
  expect(open.scrollHeight).toBeGreaterThan(open.clientHeight);
  await drawer.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect.poll(() => drawer.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  // The More drawer ends with the member's name and its actions; the role and bio live in the top bar's account menu.
  await drawer.getByRole('button', { name: 'Sign out' }).scrollIntoViewIfNeeded();
  await expect(drawer.getByText(/Alexandria With A Deliberately Long/)).toBeAttached();
  await expect(drawer.getByText('Vault owner')).toHaveCount(0);
});

test('Watch keeps long localized title and metadata readable at 200% equivalent', async ({ page }) => {
  await openApp(page, 640, 900);
  await navigateWithMobileTabs(page, 'Library', /^Library$/);
  await page.getByRole('navigation', { name: 'Library' }).getByRole('link', { name: 'YouTube' }).click();
  await page.getByRole('button', { name: new RegExp(`^${LONG_TITLE}, `) }).click();
  const title = page.getByRole('heading', { level: 1, name: LONG_TITLE });
  const geometry = await title.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const lineHeight = Number.parseFloat(style.lineHeight);
    return {
      width: rect.width,
      lines: rect.height / lineHeight,
      pageHeight: document.documentElement.scrollHeight,
      pageWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    };
  });
  expect(geometry.width).toBeGreaterThanOrEqual(300);
  expect(geometry.lines).toBeLessThan(9);
  expect(geometry.pageHeight).toBeLessThan(6000);
  expect(geometry.pageWidth).toBeLessThanOrEqual(geometry.viewportWidth);
  // The description now leads the Overview tool instead of hiding behind the chapter list.
  await expect(page.getByText(LONG_DESCRIPTION)).toBeVisible();
});

test('rendered controls retain 44px targets and shelves work by pointer and keyboard', async ({ page }) => {
  await openApp(page, 640, 900);
  await expectEffectiveTargets(page);

  await page.setViewportSize({ width: 1280, height: 900 });
  const shelf = page.getByRole('region', { name: 'Recently saved' });
  const row = shelf.locator('.h-row');
  await expect(shelf).toBeVisible();
  await shelf.getByRole('button', { name: 'Scroll Recently saved right' }).click();
  await expect.poll(() => row.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  await row.evaluate((element) => { element.scrollLeft = 0; });
  const cards = row.locator('[data-focus-item]');
  await cards.first().focus();
  for (let step = 0; step < 5; step += 1) await page.keyboard.press('ArrowRight');
  await expect(cards.nth(5)).toBeFocused();
  await expect.poll(() => row.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  const transition = await shelf.locator('.g-art').first().evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(transition).toBe('0s');
});

test('a member can choose interests in Settings and receive a personalized Home shelf', async ({ page }) => {
  await openApp(page, 1280, 900);
  await page.getByRole('button', { name: 'Choose interests' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();

  const music = page.getByRole('checkbox', { name: 'Music' });
  await music.check();
  const interestLabel = page.locator('.g-fieldset .g-choice', { hasText: 'Music' });
  await expect.poll(() => interestLabel.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(await targetFloor(page));
  await page.getByRole('button', { name: 'Save interests' }).click();
  await expect(page.getByText('Your Home interests were saved.')).toBeVisible();

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Home' }).click();
  await expect(page.getByRole('region', { name: 'Picked for you' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^A personalized music pick/ })).toBeVisible();
});

test('a member suppresses a recommended item and channel, then restores them from Settings', async ({ page }) => {
  await openApp(page, 1280, 900);
  await page.getByRole('button', { name: 'Choose interests' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Save interests' }).click();
  await expect(page.getByText('Your Home interests were saved.')).toBeVisible();

  // The collapsed navigation must be opened before reaching Home; the desktop
  // sidebar then stays expanded for the rest of the journey.
  await page.getByRole('button', { name: 'Open navigation' }).click();
  const primaryNav = page.getByRole('navigation', { name: 'Primary' });
  await primaryNav.getByRole('button', { name: 'Home' }).click();
  const pick = page.getByRole('button', { name: /^A personalized music pick/ });
  const replacement = page.getByRole('button', { name: /^Another music pick/ });
  await expect(pick).toBeVisible();
  await expect(replacement).toBeVisible();

  // Hide the item: it leaves the visible surface and the next ranked pick stays.
  await page.getByRole('button', { name: 'More options for A personalized music pick' }).click();
  await page.getByRole('menuitem', { name: 'Not interested' }).click();
  await expect(pick).toHaveCount(0);
  await expect(replacement).toBeVisible();

  // Hide the remaining pick's channel: the whole channel leaves the surface.
  await page.getByRole('button', { name: 'More options for Another music pick' }).click();
  await page.getByRole('menuitem', { name: /^Don't recommend Second Fixture/ }).click();
  await expect(replacement).toHaveCount(0);

  // Settings lists both suppressions with safe metadata and restores each.
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { level: 3, name: 'Hidden recommendations' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restore recommendations of A personalized music pick' })).toBeVisible();
  await page.getByRole('button', { name: 'Restore recommendations of Second Fixture' }).click();
  await page.getByRole('button', { name: 'Restore recommendations of A personalized music pick' }).click();

  // Restoring makes both eligible again: Home shows them on its next fetch.
  await primaryNav.getByRole('button', { name: 'Home' }).click();
  await expect(page.getByRole('button', { name: /^A personalized music pick/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Another music pick/ })).toBeVisible();
});

test('a new member is guided through first-run onboarding into a prepared, personalized Home', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending' });

  // Gated into a focused full-screen surface: no primary navigation is offered.
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary' })).toHaveCount(0);
  // Reduced motion: the entry choreography does not run.
  const animation = await page.locator('.g-onboarding-panel').evaluate((element) => getComputedStyle(element).animationName);
  expect(animation).toBe('none');

  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();

  // Interest setup advances into channel discovery before Home.
  await expect(page.getByRole('heading', { level: 1, name: 'Follow a few channels' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Popular in Music' })).toBeVisible();
  await page.getByRole('button', { name: /^Continue/ }).click();

  // Preparation resolves into Home, which immediately reflects the interest.
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Picked for you' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^A personalized music pick/ })).toBeVisible();

  // Resume: reloading never re-gates a member who has completed setup.
  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toHaveCount(0);
});

test('a pending member who reloads without acting stays gated in first-run setup', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending' });
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();

  await page.reload();

  // The durable pending status resumes setup rather than dropping into Home.
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Primary' })).toHaveCount(0);
});

test('a new member can skip first-run onboarding and still reach a non-blocking Home', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending' });
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();

  await page.getByRole('button', { name: 'Skip for now' }).click();

  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  // Skipping chose no interests, so Home keeps the optional personalization invite.
  await expect(page.getByRole('heading', { name: 'Make Home yours' })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toHaveCount(0);
});

test('onboarding still reaches Home with an honest degraded shelf when discovery warming fails', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending', discoveryHome: 'failing' });
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();

  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();
  // Pass through channel discovery without following anything.
  await expect(page.getByRole('heading', { level: 1, name: 'Follow a few channels' })).toBeVisible();
  await page.getByRole('button', { name: /^Continue/ }).click();

  // Durable writes landed, so Home is entered even though warming did not, and
  // the personalized shelf keeps an honest degraded state instead of stalling.
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await expect(page.getByText('Personalized discovery is taking a pause')).toBeVisible();
});

test('a failed Up Next recommendation never breaks the Watch surface or blocks navigation', async ({ page }) => {
  await openApp(page, 1280, 900);
  // Recommendation loading fails while the member is watching. Registered after
  // installApi so this more specific route wins for the Up Next path.
  await page.route('**/api/discovery/up-next', (route) => route.fulfill({
    status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Recommendations are temporarily unavailable.' }),
  }));

  // Choose an interest so Home offers a remote pick, then open it. The
  // collapsed navigation must be opened before reaching Home.
  await page.getByRole('button', { name: 'Choose interests' }).click();
  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Save interests' }).click();
  await expect(page.getByText('Your Home interests were saved.')).toBeVisible();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Home' }).click();
  await page.getByRole('button', { name: /^A personalized music pick/ }).click();

  // The Watch surface renders and stays usable despite the failed recommendation.
  await expect(page.getByRole('heading', { level: 1, name: 'A personalized music pick' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Up next' })).toBeVisible();
  // Nothing auto-advances and the surface did not crash into an error boundary.
  await expect(page.getByText('Plays next')).toHaveCount(0);
  await expectNoHorizontalOverflow(page);

  // Manual navigation still works — the failed recommendation never blocks it.
  await page.getByRole('button', { name: 'Back' }).click();
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
});

test('a new member discovers, searches, and follows channels during onboarding with a curated fallback', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending', channelSuggestions: 'degraded' });
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();
  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();

  // The discovery step is keyboard-reachable and lands focus on its heading.
  const discoverHeading = page.getByRole('heading', { level: 1, name: 'Follow a few channels' });
  await expect(discoverHeading).toBeVisible();
  await expect(discoverHeading).toBeFocused();

  // A cold provider yields a curated "Popular in Music" fallback, described as a
  // starting set and never as an authoritative trending ranking.
  await expect(page.getByRole('heading', { name: 'Popular in Music' })).toBeVisible();
  await expect(page.getByText(/starting set/i)).toBeVisible();
  await expect(page.getByText(/trending/i)).toHaveCount(0);

  // Manual search finds a creator; follow it, then complete the step.
  await page.getByRole('searchbox').fill('creator');
  await page.getByRole('button', { name: 'Find channels' }).click();
  await page.getByRole('button', { name: 'Follow Searched Creator' }).click();
  await page.getByRole('button', { name: /^Continue with/ }).click();

  // Home opens, then the durable follow appears in Subscriptions and warms
  // through the existing bounded, partial-outcome subscription feed.
  await expect(page.getByRole('heading', { level: 1, name: /Alexandria/ })).toBeVisible();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.evaluate(() => { window.history.pushState(null, '', '/streaming/channels'); window.dispatchEvent(new PopStateEvent('popstate')); });
  await expect(page.getByRole('link', { name: /^Searched Creator/ })).toBeVisible();
});

test('new Lumina discovery, hidden navigation, chapter, and theater controls hold their browser contracts', async ({ page }) => {
  await openApp(page, 1280, 900);
  const sidebar = page.locator('.g-sidebar');
  const navigationToggle = page.getByRole('button', { name: 'Open navigation' });
  await expect(navigationToggle).toBeVisible();
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBe(0);
  await expect.poll(() => page.locator('.lumina-main').evaluate((element) => getComputedStyle(element).marginLeft)).toBe('0px');
  await navigationToggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();
  await expect(sidebar).not.toHaveAttribute('aria-hidden', 'true');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Home' })).toBeFocused();
  await page.getByRole('button', { name: 'Close navigation' }).focus();
  const downloads = page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Downloads' });
  await expect(downloads.locator('b')).toHaveText('1');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused();
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  await expect(sidebar.locator('nav[aria-label="Primary"]')).toBeHidden();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();

  await page.setViewportSize({ width: 920, height: 420 });
  await expect.poll(() => sidebar.evaluate((element) => element.getBoundingClientRect().width)).toBe(320); // 920px is below the 960px drawer breakpoint: a 320px drawer
  const shortMenu = await sidebar.evaluate((element) => ({
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
    scrollHeight: element.scrollHeight,
  }));
  expect(shortMenu.overflowY).toBe('auto');
  expect(shortMenu.scrollHeight).toBeGreaterThan(shortMenu.clientHeight);
  await sidebar.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(page.getByRole('button', { name: /, account menu$/ })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await expect(page.getByRole('navigation', { name: 'Browse by category' }).getByRole('button')).toHaveCount(25);
  await expect(page.getByText('Showing a partial feed while more categories warm up.')).toBeVisible();
  await expect(page.getByRole('button', { name: /^A gradually discovered favorite/ })).toBeVisible();

  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Library' }).click();
  await page.getByRole('navigation', { name: 'Library' }).getByRole('link', { name: 'YouTube' }).click();
  await page.getByRole('button', { name: new RegExp(`^${LONG_TITLE}, `) }).click();
  const theater = page.getByRole('button', { name: 'Enter theater mode' });
  await expect(theater).toHaveAttribute('data-tooltip', 'Enter theater mode');
  await page.locator('[data-lumina-player="true"]').focus();
  for (let index = 0; index < 16 && !(await theater.evaluate((element) => element === document.activeElement)); index += 1) await page.keyboard.press('Tab');
  await expect(theater).toBeFocused();
  await expect.poll(() => theater.evaluate((element) => getComputedStyle(element, '::after').opacity)).toBe('1');
  await theater.click();
  await expect(page.getByRole('button', { name: 'Exit theater mode' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.player-progress-stack [data-chapter-marker="true"]').first()).toBeVisible();
  await expect(page.locator('.player-progress-stack [data-chapter-markers="true"]')).toHaveAttribute('aria-hidden', 'true');
  await expect.poll(() => page.locator('.player-progress-stack [data-chapter-marker="true"]').first().evaluate((element) => getComputedStyle(element).pointerEvents)).toBe('none');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-lumina-player="true"]').hover();
  await expectEffectiveTargets(page);
});

test('replay chat loads as a deliberate synchronized rail and never blocks playback', async ({ page }) => {
  await openApp(page, 1280, 900);

  const favoriteUrl = 'https://example.test/watch/popular-1';
  const chatIdentity = 'url:https://example.test/watch/popular-1';
  // A completed-live remote source that advertises replay chat but is not
  // progressively playable, so no remote-stream registration is involved.
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: favoriteUrl,
      extractor: 'generic', extractor_key: 'Generic', media_kind: 'video',
      capabilities: {
        provider: 'youtube', lifecycle: 'completed_live',
        can_play: false, play_reason: 'segmented_transport_not_supported',
        can_acquire: false, acquire_reason: 'segmented_transport_not_supported',
        chat: { live: 'unavailable', replay: 'available' },
      },
      entries: [], raw: { id: 'popular-1', webpage_url: favoriteUrl, uploader: 'Lumina fixture', duration: 600, extractor: 'generic' },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));
  await page.route('**/api/chat-replay/**', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        source_identity: chatIdentity, status: 'ready', event_count: 2, truncated: false, dropped_malformed: 0,
        events: [
          { id: 'e1', offset_ms: 0, kind: 'message', text: 'welcome back to the replay', moderation: 'visible', author: { name: 'Ada', badges: [] } },
          { id: 'e2', offset_ms: 5000, kind: 'paid_message', amount: '$5.00', text: 'loved this moment', moderation: 'visible', author: { name: 'Grace', badges: ['moderator'] } },
        ],
      }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: 'null' });
  });

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  // The chat rail appears with a deliberate Load action; chat is not auto-loaded.
  const rail = page.getByRole('complementary', { name: 'Replay chat' });
  await expect(rail).toBeVisible();
  const loadButton = rail.getByRole('button', { name: 'Load replay chat' });
  await expect(loadButton).toBeVisible();
  await expect(rail.getByText('welcome back to the replay')).toBeHidden();
  // The (unavailable) playback surface renders alongside the rail.
  await expect(page.locator('.player-frame')).toBeVisible();

  await loadButton.click();
  // Follow mode reveals the message at the current (0s) offset, incl. its
  // seek control, which must meet the 44px target norm like the rest of Watch.
  await expect(rail.getByText('welcome back to the replay')).toBeVisible();
  await expect(rail.getByRole('button', { name: /Jump to 0:00/i })).toBeVisible();
  await expectEffectiveTargets(page);

  // Bounded search finds the later paid message across the whole asset.
  await rail.getByRole('searchbox', { name: 'Search replay chat' }).fill('loved');
  await expect(rail.getByText('loved this moment')).toBeVisible();
  await expect(rail.getByText('welcome back to the replay')).toBeHidden();

  await expectNoHorizontalOverflow(page);
});

// YouTube still advertises current chat (a stale preview); Twitch advertises none
// because provider-native chat needs a third-party identity unavailable in 1.0.
// Either way no current-chat rail may mount.
for (const source of [
  { provider: 'youtube', extractorKey: 'Youtube', label: 'YouTube', liveChat: 'available' },
  { provider: 'twitch', extractorKey: 'Twitch', label: 'Twitch', liveChat: 'unavailable' },
]) test(`live ${source.label} opens with a Live state and relays media without a current chat rail`, async ({ page }) => {
  await openApp(page, 1280, 900);

  const liveUrl = 'https://example.test/watch/popular-1';
  // A currently-live source, playable at the current edge through the guarded live relay.
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: liveUrl,
      extractor: source.provider, extractor_key: source.extractorKey, media_kind: 'video',
      capabilities: {
        provider: source.provider, lifecycle: 'live',
        can_play: true, play_reason: null,
        can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
        chat: { live: source.liveChat, replay: 'unavailable' },
      },
      playback: {
        status: 'ready', stream_id: 'live-e2e', transport: 'hls', media_kind: 'video',
        playback_url: '/api/remote-streams/live-e2e/relay/1/master.m3u8',
        content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true,
        seekable: false, live: true, renditions: [],
      },
      entries: [], raw: { id: 'popular-1', webpage_url: liveUrl, uploader: 'Lumina fixture', is_live: true, extractor: source.provider },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));
  // Minimal guarded-relay responses so the player attaches without real media:
  // a live master and an (edge-empty) live media playlist that never ENDLISTs.
  await page.route('**/api/remote-streams/**', (route) => {
    const url = route.request().url();
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    if (url.includes('/refresh')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        status: 'ready', stream_id: 'live-e2e', transport: 'hls', media_kind: 'video',
        playback_url: '/api/remote-streams/live-e2e/relay/1/master.m3u8',
        content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true, seekable: false, live: true, renditions: [],
      }) });
    }
    if (url.endsWith('master.m3u8')) {
      return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl',
        body: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n/api/remote-streams/live-e2e/relay/1/r/media\n' });
    }
    if (url.includes('/r/media')) {
      return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl',
        body: '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n' });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  // The generic public-edge live-chat surface is removed; the frontend no
  // longer calls /api/live-chat. These mocks of the removed endpoint are inert
  // (recorded in the request ledger) and are kept to prove the rail stays absent
  // even when a stale preview still advertises current chat.
  await page.route('**/api/live-chat/**', (route) => {
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'chat is briefly unavailable' }) });
  });
  await page.route('**/api/live-chat', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'live-chat-e2e', status: 'active' }),
  }));

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  // The player renders a clear Live state at the current edge (reveal controls
  // first, since they auto-hide during playback).
  const player = page.locator('[data-lumina-player="true"]');
  await expect(player).toBeVisible();
  await player.hover();
  await expect(player.getByRole('status', { name: 'Watching live' })).toBeVisible();
  // VOD seeking is gone for a live edge.
  await expect(player.getByLabel('Seek')).toHaveCount(0);

  // The coordinated current-chat rail is removed. Even when the preview
  // still advertises current chat, the rail must not render; the media plays on
  // its own and no chat surface appears beside it.
  await expect(page.getByRole('complementary', { name: 'Live chat' })).toHaveCount(0);
  await expect(page.getByRole('complementary', { name: 'Replay chat' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Load replay chat' })).toHaveCount(0);
  await player.hover();
  await expect(player.getByRole('status', { name: 'Watching live' })).toBeVisible();

  await expectNoHorizontalOverflow(page);
});

// YouTube advertises chat, so a deliberate stop with failed chat capture is an
// honest partial. Twitch (#100) records media through the guarded live-HLS
// transport with no chat sibling (provider-native chat is unavailable in 1.0),
// so the same stop is an honest media-only completion.
for (const source of [
  {
    provider: 'youtube', extractorKey: 'Youtube', liveChat: 'available', mediaOnly: false,
    chatWhileLive: 'capturing', chatAfterStop: 'failed', terminal: 'partial',
    liveChatText: /Capturing chat/i, terminalText: 'Recorded with a partial outcome', chatOutcomeText: /Chat could not be captured/i,
  },
  {
    provider: 'twitch', extractorKey: 'Twitch', liveChat: 'unavailable', mediaOnly: true,
    chatWhileLive: 'unavailable', chatAfterStop: 'unavailable', terminal: 'completed',
    liveChatText: /no chat to capture/i, terminalText: 'Recorded', chatOutcomeText: /no chat to capture/i,
  },
]) test(`record from now captures a live ${source.provider} source and renders an honest ${source.terminal} outcome`, async ({ page }) => {
  await openApp(page, 1280, 900);

  const liveUrl = 'https://example.test/watch/popular-1';
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: liveUrl,
      extractor: source.provider, extractor_key: source.extractorKey, media_kind: 'video',
      capabilities: {
        provider: source.provider, lifecycle: 'live',
        can_play: true, play_reason: null,
        can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
        can_record: true, record_reason: null,
        chat: { live: source.liveChat, replay: 'unavailable' },
      },
      playback: {
        status: 'ready', stream_id: 'live-e2e', transport: 'hls', media_kind: 'video',
        playback_url: '/api/remote-streams/live-e2e/relay/1/master.m3u8',
        content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true,
        seekable: false, live: true, renditions: [],
      },
      entries: [], raw: { id: 'popular-1', webpage_url: liveUrl, uploader: 'Lumina fixture', is_live: true, extractor: source.provider },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));
  await page.route('**/api/remote-streams/**', (route) => {
    const url = route.request().url();
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    if (url.includes('/refresh')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        status: 'ready', stream_id: 'live-e2e', transport: 'hls', media_kind: 'video',
        playback_url: '/api/remote-streams/live-e2e/relay/1/master.m3u8',
        content_type: 'application/vnd.apple.mpegurl', has_video: true, has_audio: true, seekable: false, live: true, renditions: [],
      }) });
    }
    if (url.endsWith('master.m3u8')) {
      return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl',
        body: '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n/api/remote-streams/live-e2e/relay/1/r/media\n' });
    }
    if (url.includes('/r/media')) {
      return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl',
        body: '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n' });
    }
    return route.fulfill({ status: 404, body: '' });
  });
  await page.route('**/api/live-chat/**', (route) => {
    if (route.request().method() === 'DELETE') return route.fulfill({ status: 204, body: '' });
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'chat is briefly unavailable' }) });
  });
  await page.route('**/api/live-chat', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ session_id: 'live-chat-e2e', status: 'active' }),
  }));

  // The durable recording: a deliberate stop finalizes a usable video plus the
  // provider's honest chat outcome — never collapsed to a single done/failed line.
  const record = (status: string, media: string, chat: string) => ({
    id: 'rec-e2e', source_url: liveUrl, title: 'A gradually discovered favorite', extractor: source.provider,
    status, stop_requested: status === source.terminal, cancel_requested: false,
    media: { status: media, library_item_id: media === 'completed' || media === 'partial' ? 'item-e2e' : null, failure_category: null, error: null },
    chat: { status: chat, chat_asset_id: chat === 'completed' ? 'chat-e2e' : null, failure_category: chat === 'failed' ? 'chat_failed' : null, error: null },
    created_at: '2026-07-19T00:00:00',
  });
  let terminal = false;
  // A regex (not a glob) so sub-resources with slashes — /{id}, /{id}/stop — are
  // all intercepted; a "**"-suffixed glob does not cross a path separator.
  await page.route(/\/api\/live-recordings(\/|$|\?)/, (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path.endsWith('/stop') && method === 'POST') { terminal = true; return json(record(source.terminal, 'completed', source.chatAfterStop)); }
    if (path.endsWith('/cancel') && method === 'POST') return json(record('cancelled', 'failed', source.chatAfterStop));
    if (path === '/api/live-recordings' && method === 'POST') return json(record('live', 'recording', source.chatWhileLive), 201);
    if (path === '/api/live-recordings' && method === 'GET') return json({ items: [], next_cursor: null });
    if (method === 'GET') return json(terminal ? record(source.terminal, 'completed', source.chatAfterStop) : record('live', 'recording', source.chatWhileLive));
    return route.fulfill({ status: 404, body: '' });
  });

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  // "Record from now" is offered for the live source, distinct from live viewing.
  const recording = page.getByRole('region', { name: 'Live recording' });
  await expect(recording).toBeVisible();
  if (source.mediaOnly) {
    // The media-only forward-only boundary — no live chat capture and no
    // historical import — is shown BEFORE recording starts.
    const boundary = recording.getByRole('note');
    await expect(boundary).toContainText('Live chat is not captured');
    await expect(boundary).toContainText('nothing from earlier is imported');
  }
  await recording.getByRole('button', { name: 'Record from now' }).click();

  // While live, both sibling outputs are shown; controls meet the 44px norm.
  await expect(recording.getByText(/Recording the video/i)).toBeVisible();
  await expect(recording.getByText(source.liveChatText)).toBeVisible();
  await expectEffectiveTargets(page);

  // A deliberate stop saves the video AND shows the chat outcome.
  await recording.getByRole('button', { name: 'Stop & save' }).click();
  await expect(recording.getByText(source.terminalText, { exact: true })).toBeVisible();
  await expect(recording.getByText(/saved to your library/i)).toBeVisible();
  await expect(recording.getByText(source.chatOutcomeText)).toBeVisible();
  // A terminal recording exposes no further stop/cancel controls.
  await expect(recording.getByRole('button', { name: 'Stop & save' })).toHaveCount(0);

  await expectNoHorizontalOverflow(page);
});

test('schedule an upcoming broadcast from the beginning and render an honest partial-history outcome', async ({ page }) => {
  await openApp(page, 1280, 900);

  const upcomingUrl = 'https://example.test/watch/popular-1';
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: upcomingUrl,
      extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video',
      capabilities: {
        provider: 'youtube', lifecycle: 'upcoming',
        can_play: false, play_reason: 'upcoming_not_started',
        can_acquire: false, acquire_reason: 'upcoming_not_started',
        can_record: false, can_schedule: true, schedule_reason: null,
        scheduled_start: '2026-07-20T18:00:00', from_start_available: true,
        chat: { live: 'available', replay: 'unavailable' },
      },
      entries: [], raw: { id: 'popular-1', webpage_url: upcomingUrl, uploader: 'Lumina fixture', live_status: 'is_upcoming', extractor: 'youtube' },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));

  // A durable scheduled recording: it starts in the waiting phase, connects, and
  // records from the beginning but with incomplete earlier history — an explicit
  // partial-history condition, never a false "complete recording".
  const record = (status: string, media: string, chat: string, extra: Record<string, unknown> = {}) => ({
    id: 'rec-sched-e2e', source_url: upcomingUrl, title: 'A gradually discovered favorite', extractor: 'youtube',
    status, stop_requested: false, cancel_requested: false,
    start_intent: 'from_start', fallback_policy: 'allow_live_edge', scheduled_start_at: '2026-07-20T18:00:00',
    media: { status: media, library_item_id: media === 'completed' || media === 'partial' ? 'item-e2e' : null, failure_category: null, error: null },
    chat: { status: chat, chat_asset_id: chat === 'completed' ? 'chat-e2e' : null, failure_category: null, error: null },
    capture_origin: 'pending', history: 'pending', awaiting_fallback_choice: false, waiting_reason: null,
    created_at: '2026-07-20T00:00:00', ...extra,
  });
  await page.route(/\/api\/live-recordings(\/|$|\?)/, (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    // The POST returns the durable waiting phase (shown immediately); the first
    // status poll then shows the finished honest partial — capture from the
    // beginning with explicitly incomplete earlier history.
    if (path === '/api/live-recordings' && method === 'POST') return json(record('waiting', 'pending', 'pending'), 201);
    if (path === '/api/live-recordings' && method === 'GET') return json({ items: [], next_cursor: null });
    if (method === 'GET') return json(record('partial', 'completed', 'completed', { capture_origin: 'source_beginning', history: 'partial' }));
    return route.fulfill({ status: 404, body: '' });
  });

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  // The upcoming source offers scheduling + a from-start intent choice (product
  // intent, never a raw yt-dlp flag).
  const recording = page.getByRole('region', { name: 'Live recording' });
  await expect(recording).toBeVisible();
  // Axe-scan the schedule-variant live-recording START surface (its intent chooser
  // is on screen) before scheduling — a deep surface auditA11y never covered.
  expect((await auditA11y(page)).violations).toEqual([]);
  await recording.getByRole('radio', { name: /from the beginning if available/i }).check();
  await expectEffectiveTargets(page);
  await recording.getByRole('button', { name: 'Schedule recording' }).click();

  // The normalized waiting state is shown with the scheduled time.
  await expect(recording.getByText(/Waiting for this broadcast to begin/i)).toBeVisible();

  // Once it finishes, the honest partial-history disclosure appears: capture began
  // at the beginning, but earlier history is explicitly incomplete.
  await expect(recording.getByText(/began at the beginning of the broadcast/i)).toBeVisible();
  await expect(recording.getByText(/Earlier history is incomplete/i)).toBeVisible();
  // Axe-scan a terminal partial-history live-recording surface (its honesty
  // disclosures now announced as role="status" live regions).
  expect((await auditA11y(page)).violations).toEqual([]);

  await expectNoHorizontalOverflow(page);
});

test('onboarding setup and channel discovery surfaces pass the tagged Axe gate', async ({ page }) => {
  await openApp(page, 1280, 900, { onboarding: 'pending' });

  // (a) The first-run onboarding interest-setup surface — deep-a11y-scanned here
  // for the first time (auditA11y previously only covered Home + bootstrap).
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();
  expect((await auditA11y(page)).violations).toEqual([]);

  // (b) The channel discovery panel.
  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Follow a few channels' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Popular in Music' })).toBeVisible();
  expect((await auditA11y(page)).violations).toEqual([]);
});

test('the record-from-now live-recording start surface passes the tagged Axe gate', async ({ page }) => {
  await openApp(page, 1280, 900);

  const liveUrl = 'https://example.test/watch/popular-1';
  // A currently-live, recordable, from-start-capable source: the
  // record-from-now START renders the intent chooser. can_play:false keeps the
  // Watch surface to the recording controls (no live player chrome), scanning this
  // deep, previously-unaudited start surface in isolation.
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: liveUrl,
      extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video',
      capabilities: {
        provider: 'youtube', lifecycle: 'live',
        can_play: false, play_reason: 'live_playback_not_supported',
        can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
        can_record: true, record_reason: null, from_start_available: true,
        chat: { live: 'available', replay: 'unavailable' },
      },
      entries: [], raw: { id: 'popular-1', webpage_url: liveUrl, uploader: 'Lumina fixture', is_live: true, extractor: 'youtube' },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));
  await page.route(/\/api\/live-recordings(\/|$|\?)/, (route) => {
    const method = route.request().method();
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/live-recordings' && method === 'GET') return json({ items: [], next_cursor: null });
    return route.fulfill({ status: 404, body: '' });
  });

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  const recording = page.getByRole('region', { name: 'Live recording' });
  await expect(recording).toBeVisible();
  // The intent chooser is on screen; its radio targets meet 44px.
  await expect(recording.getByRole('radio', { name: /from the beginning if available/i })).toBeVisible();
  await expectEffectiveTargets(page);
  // Axe-scan the record-from-now START surface (record variant).
  expect((await auditA11y(page)).violations).toEqual([]);
});

test('a terminal failed live-recording surface passes the tagged Axe gate', async ({ page }) => {
  await openApp(page, 1280, 900);

  const liveUrl = 'https://example.test/watch/popular-1';
  // A currently-live source whose durable recording reached a terminal FAILED
  // outcome. It stays recordable in principle (can_record) but is not playable at
  // the edge, so the Watch surface renders the failed recording panel cleanly.
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gradually discovered favorite', webpage_url: liveUrl,
      extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video',
      capabilities: {
        provider: 'youtube', lifecycle: 'live',
        can_play: false, play_reason: 'live_playback_not_supported',
        can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
        can_record: true, record_reason: null, from_start_available: true,
        chat: { live: 'available', replay: 'unavailable' },
      },
      entries: [], raw: { id: 'popular-1', webpage_url: liveUrl, uploader: 'Lumina fixture', is_live: true, extractor: 'youtube' },
    }),
  }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ query: '', items: [] }),
  }));
  // An existing durable recording adopted on load: both sibling outputs failed —
  // an honest terminal FAILED outcome, never collapsed to a single line.
  const failed = {
    id: 'rec-failed-e2e', source_url: liveUrl, title: 'A gradually discovered favorite', extractor: 'youtube',
    status: 'failed', stop_requested: false, cancel_requested: false,
    media: { status: 'failed', library_item_id: null, failure_category: 'capture_failed', error: null },
    chat: { status: 'failed', chat_asset_id: null, failure_category: 'chat_failed', error: null },
    capture_origin: 'live_edge', history: 'from_edge', awaiting_fallback_choice: false, waiting_reason: null,
    created_at: '2026-07-19T00:00:00',
  };
  await page.route(/\/api\/live-recordings(\/|$|\?)/, (route) => {
    const method = route.request().method();
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/live-recordings' && method === 'GET') return json({ items: [failed], next_cursor: null });
    if (method === 'GET') return json(failed);
    return route.fulfill({ status: 404, body: '' });
  });

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Streaming' }).click();
  await page.getByRole('button', { name: /^A gradually discovered favorite/ }).click();

  const recording = page.getByRole('region', { name: 'Live recording' });
  await expect(recording).toBeVisible();
  await expect(recording.getByText('Recording failed')).toBeVisible();
  await expect(recording.getByText(/The video could not be recorded/i)).toBeVisible();
  await expect(recording.getByText(/Chat could not be captured/i)).toBeVisible();
  // Axe-scan the terminal FAILED live-recording surface.
  expect((await auditA11y(page)).violations).toEqual([]);
});

test('Tab pressed the instant the sidebar opens lands in it, every time', async ({ page }) => {
  await openApp(page, 1280, 900);
  const sidebar = page.locator('#primary-navigation');
  await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  for (let round = 0; round < 5; round += 1) {
    await page.getByRole('button', { name: 'Open navigation' }).focus();
    await page.keyboard.press('Enter'); // opens
    await page.keyboard.press('Tab');   // no wait between the two
    await expect(page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'Home' })).toBeFocused();
    await page.getByRole('button', { name: 'Close navigation', expanded: true }).focus();
    await page.keyboard.press('Enter'); // closes
    await expect(sidebar).toHaveAttribute('aria-hidden', 'true');
  }
});

test('global Search has a visible indicator when reached by keyboard', async ({ page }) => {
  await openApp(page, 1280, 900);
  const search = page.getByRole('button', { name: /^Search Lumina, / });
  for (let index = 0; index < 30; index += 1) {
    await page.keyboard.press('Tab');
    if (await search.evaluate((element) => element === document.activeElement)) break;
  }
  await expect(search).toBeFocused();
  await expect(assertFocusIndicator(search)).resolves.toBeUndefined();
});

test('representative desktop and native 320px journeys pass the tagged moderate Axe gate', async ({ browser, page }) => {
  await openApp(page, 1280, 900);
  expect((await auditA11y(page)).violations).toEqual([]);

  const mobileContext = await browser.newContext({ viewport: { width: 320, height: 760 }, screen: { width: 320, height: 760 } });
  const mobile = await mobileContext.newPage();
  await openApp(mobile, 320, 760);
  expect((await auditA11y(mobile)).violations).toEqual([]);
  await mobileContext.close();
});

test('the authenticated app loading state passes the tagged moderate Axe gate', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', { configurable: true, value: class { close() {} addEventListener() {} } });
  });
  await page.route('**/api/**', async (route) => {
    const pathName = new URL(route.request().url()).pathname;
    if (pathName === '/api/bootstrap/status') {
      await new Promise((resolve) => setTimeout(resolve, 2_000));
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ needs_setup: false }) });
    }
    const body = { status: 'ok' };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Opening your vault' })).toBeVisible();
  expect((await auditA11y(page)).violations).toEqual([]);
});

test('live tab renders rails and cards click through to Watch', async ({ page }) => {
  await openApp(page, 1280, 900);
  // installApi's /api/discovery/live snapshot ranks one YouTube stream into Gaming.
  // Opening the card posts /api/preview; answer with a non-attached live preview
  // (no playback object) so Watch takes over without registering a guarded relay.
  await page.route('**/api/preview', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      kind: 'video', title: 'A gaming stream happening now', webpage_url: 'https://example.test/watch/live-1',
      uploader: 'Lumina fixture', extractor: 'youtube', extractor_key: 'Youtube', media_kind: 'video', availability: 'public',
      capabilities: {
        provider: 'youtube', lifecycle: 'live',
        can_play: true, play_reason: null,
        can_acquire: false, acquire_reason: 'live_acquisition_not_supported',
        chat: { live: 'unavailable', replay: 'unavailable' },
      },
      playback: null, artwork_url: null, chapters: [], description_timestamps: [], entries: [],
      raw: { id: 'live-1', title: 'A gaming stream happening now', uploader: 'Lumina fixture', webpage_url: 'https://example.test/watch/live-1', is_live: true, extractor: 'youtube' },
    }),
  }));

  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.evaluate(() => { window.history.pushState(null, '', '/streaming/live'); window.dispatchEvent(new PopStateEvent('popstate')); });

  await expect(page.getByRole('heading', { level: 1, name: 'Streaming' })).toBeVisible();
  // The only stream leads as the hero (a rail needs company), so Watch is the way in.
  await expect(page.locator('.g-live-hero')).toBeVisible();
  const card = page.getByRole('button', { name: /^Watch A gaming stream happening now/ });
  await expect(card).toBeVisible();
  await card.click();
  await expect(page.getByRole('heading', { level: 1, name: 'A gaming stream happening now' })).toBeVisible();
});

test.describe('trace redaction', () => {
  test('removes credentials, cookie bodies, URLs, and resource blobs from retained traces', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'lumina-trace-redaction-'));
    const archivePath = path.join(directory, 'trace.zip');
    const marker = 'owner-password-trace-marker';
    try {
      await writeFile(archivePath, zipSync({
        'trace.trace': strToU8(`${JSON.stringify({ type: 'before', apiName: 'locator.fill', params: { value: marker, selector: 'input[type=password]', files: [{ name: `private-cookie-${marker}` }] }, url: `https://private.example/${marker}` })}\n`),
        'trace.network': strToU8(`Cookie: fixture_access=${marker}`),
        'trace.stacks': strToU8(JSON.stringify({ files: [`/private/${marker}`] })),
        'resources/private-body': strToU8(`multipart cookie ${marker}`),
      }));

      redactTraceArchive(archivePath);
      const archive = unzipSync(new Uint8Array(await readFile(archivePath)));
      const serialized = Object.entries(archive).map(([name, bytes]) => `${name}\n${strFromU8(bytes)}`).join('\n');
      expect(serialized).not.toContain(marker);
      expect(serialized).not.toContain('private.example');
      expect(Object.keys(archive).some((name) => name.includes('resources') || name.endsWith('.network'))).toBe(false);
      expect(serialized).toContain('[redacted]');
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  test('deletes an unreadable retained trace instead of leaving raw evidence behind', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'lumina-trace-fail-closed-'));
    const archivePath = path.join(directory, 'trace.zip');
    try {
      await writeFile(archivePath, 'not-a-zip owner-secret-marker', { mode: 0o600 });
      expect(() => redactTraceArchive(archivePath)).toThrow();
      await expect(readFile(archivePath)).rejects.toMatchObject({ code: 'ENOENT' });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
});
