import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { recoDiagnostics, recoSurfaceStats, suppression, suppressionList } from '../src/test/recoFixtures';
import { caps, CHANNEL_ID, liveItems, liveSnapshotBody, mockChannelPage, mockLibraryChannels, mockLive, mockRemoteArtwork } from './live-youtube-mock';
import { episodeSegments, fulfillRangeMedia, item, mockAdminFixtures, mockApi, mockDeviceRing, mockTitles, mockTranscripts, pauseAt, signIn, user, type Ring } from './lumina-mock';
import { mockHome } from './home-mock';
import { SYNTHETIC_MEDIA_BASE64, SYNTHETIC_MEDIA_TYPE } from './synthetic-media';
import { mockPaletteSearch } from './palette-mock';
import { mockPopular, mockReco, popularWithForYou } from './reco-mock';
import { redOrPinkInMain } from './red-check';

/**
 * Every release route in both themes at desktop, phone and 320px (the 200%-zoom reflow width):
 * zero serious/critical axe violations and no horizontal page scroll. Mocked API; admin sections without
 * their own fixtures render their honest empty/error states, which are release states too.
 */

const ROUTES: Array<[string, string]> = [
  ['home', '/'], ['explore', '/streaming/search?q=alpine'], ['live', '/streaming/live'], ['subscriptions', '/streaming/channels'],
  ['channel', '/subscriptions/chan-1'], ['library', '/library'],
  ['library-shows', '/library/shows'], ['title-series', '/title/series-1?season=1'], ['title-movie', '/title/movie-1'],
  ['music', '/music'], ['downloads', '/downloads'],
  ['channel-page', `/channel/youtube/${CHANNEL_ID}`], ['library-channels', '/library/youtube?view=channels'], ['library-youtube', '/library/youtube'],
  ['admin-diagnostics', '/admin/diagnostics'], ['explore-for-you', '/streaming'],
  ['settings', '/settings'],
  ...['account', 'playback', 'appearance', 'discovery', 'downloads', 'apps', 'privacy', 'about', 'overview', 'library', 'media', 'transcoding', 'ai', 'members', 'tasks', 'backups', 'diagnostics']
    .map((section): [string, string] => [`settings-${section}`, `/settings/${section}`]),
  ['watch', `/watch/library/${item.id}`],
];
const AT = '2026-09-24T10:00:00Z';
const DIAGNOSTICS = {
  generated_at: AT, status: 'degraded', versions: { lumina: '1.0.0', python: '3.11.9', yt_dlp: '2026.09.01', ffmpeg: '7.1', node: 'v22.9.0' },
  runtime: { ffmpeg_available: true, js_runtime_available: true, yt_dlp_ejs_available: true },
  storage_roots: [{ label: 'Main library', mode: 'managed', enabled: true, state: 'available', checked_at: AT }],
  queue: { jobs_by_status: { running: 1, queued: 4 }, concurrency: 2, event_streams: 3 },
  maintenance_sweeps: { consecutive_failures: 0, last_error: null, last_success_at: AT, last_failure_at: null }, persistence: {},
  recent_errors: [{ source: 'summary', at: AT, message: 'Local AI endpoint timed out' }],
  ai: { enabled: true, ok: true, model_available: true, error: null, asr_configured: false },
};
const VIEWPORTS = [{ width: 1536, height: 960 }, { width: 390, height: 844 }, { width: 320, height: 720 }];

async function audit(page: Page, where: string, found: string[]) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    // Video frames and artwork are content-dependent backdrops, not UI text surfaces.
    .exclude('.player-frame video').analyze();
  for (const violation of results.violations.filter((entry) => entry.impact === 'serious' || entry.impact === 'critical')) {
    found.push(`${where}: ${violation.id} → ${violation.nodes.slice(0, 3).map((node) => node.target.join(' ')).join(', ')}`);
  }
  if (page.viewportSize()!.width <= 390) {
    // Touch targets: 44px for controls; toggles are measured by their clickable <label>; inline text links are exempt.
    const small = await page.locator('button:visible, a:visible, summary:visible, select:visible, input[type=radio]:visible, input[type=checkbox]:visible').evaluateAll((elements) => elements
      .filter((element) => !(element.tagName === 'A' && element.closest('p, li > span, dd, td')))
      .map((element) => {
        const box = (element.matches('input') ? element.closest('label') ?? element : element).getBoundingClientRect();
        // A control may reach 44px through an invisible absolutely-positioned ::after (the reco menu button's inset hit area).
        const reach = getComputedStyle(element, '::after');
        const grown = reach.position === 'absolute' && reach.content !== 'none';
        return {
          name: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName,
          width: Math.max(box.width, grown ? parseFloat(reach.width) || 0 : 0), height: Math.max(box.height, grown ? parseFloat(reach.height) || 0 : 0),
        };
      })
      .filter((box) => box.width < 43.5 || box.height < 43.5)
      .map((box) => `${box.name} (${Math.round(box.width)}×${Math.round(box.height)})`));
    if (small.length) found.push(`${where}: small targets ${[...new Set(small)].slice(0, 8).join('; ')}`);
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 0) found.push(`${where}: page scrolls sideways by ${overflow}px`);
}

/** The surface has its data: no loading placeholder left in main (Home's below-the-fold shelves load only when scrolled near, so they are not waited on). */
async function dataReady(page: Page) {
  await expect(page.locator('main [aria-busy="true"]:not([data-shelf-section])')).toHaveCount(0, { timeout: 60_000 });
}

async function settle(page: Page) {
  await expect(page.locator('main h1').first()).toBeVisible({ timeout: 60_000 }); // a cold dev-server compile under a loaded gate outlasts the 30 s default
  await dataReady(page);
  await page.waitForTimeout(250);
}

for (const scheme of ['light', 'dark'] as const) {
  for (const viewport of VIEWPORTS) {
    test(`test_a11y_all_routes_themes ${scheme} ${viewport.width}`, async ({ page }) => {
      // ~1 min of routes on an idle machine; the budget is a hang guard and must absorb a saturated one (3x load stretched it past 180s).
      test.setTimeout(600_000);
      const found: string[] = [];
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      const shots = (scheme === 'dark' && viewport.width === 390) || (scheme === 'light' && viewport.width === 1536);
      const shot = (name: string) => shots ? page.screenshot({ path: `../output/playwright/s70/${name}-${scheme}-${viewport.width}.png` }) : undefined;
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize(viewport);
      await mockApi(page, { sidebar_collapsed: false });
      await mockTranscripts(page);
      await mockAdminFixtures(page);
      await mockTitles(page);
      await mockRemoteArtwork(page);
      await mockLive(page);
      await mockChannelPage(page);
      await mockLibraryChannels(page);
      await mockReco(page);
      await mockPopular(page, popularWithForYou());
      await mockPaletteSearch(page);
      await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ json: { ...DIAGNOSTICS, recommendations: recoDiagnostics({ surfaces: [recoSurfaceStats()] }) } }));

      for (const hash of ['', '#invite=sweep-token', '#reset=sweep-token']) {
        await page.goto(`/${hash}`);
        await expect(page.locator('.g-auth')).toBeVisible();
        await audit(page, `auth${hash}`, found);
        await shot(`auth${hash.replace(/=.*/, '').replace('#', '-')}`);
      }
      await page.goto('/');
      await signIn(page);
      // Sign-in hands focus to the page, never leaving the skip link showing over the heading.
      await expect(page.locator('.g-skip-link')).not.toBeInViewport();

      for (const [name, path] of ROUTES) {
        await page.goto(path);
        await settle(page);
        await audit(page, name, found);
        await shot(name);
        if (name !== 'watch') continue;
        const tabs = page.getByRole('tablist', { name: 'Video tools' }).getByRole('tab');
        const labels = await tabs.allTextContents();
        expect(labels.length).toBeGreaterThan(3);
        for (const label of labels) {
          await tabs.filter({ hasText: label }).click();
          await page.waitForTimeout(150);
          await audit(page, `watch/${label}`, found);
          await page.getByRole('tablist', { name: 'Video tools' }).scrollIntoViewIfNeeded();
          await shot(`watch-${label.toLowerCase().replace(/\W+/g, '-')}`);
        }
      }
      expect(found).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
}

test('imported media credits its series instead of offering a channel follow', async ({ page }) => {
  await mockApi(page, { sidebar_collapsed: false });
  const imported = { ...item, id: 'imported-1', extractor: 'external_library', uploader: null, playlist_name: 'Valleys of the Alps', metadata_json: { lumina_import_kind: 'episode' } };
  await page.route(/\/api\/library\/imported-1(\/.*)?$/, (route) => {
    const url = route.request().url();
    return url.endsWith('/imported-1') ? route.fulfill({ contentType: 'application/json', body: JSON.stringify(imported) }) : route.fallback({ url: url.replace('imported-1', item.id) });
  });
  await page.goto('/watch/library/imported-1');
  await signIn(page);
  const byline = page.locator('.g-watch-byline');
  await expect(byline).toContainText('Valleys of the Alps');
  await expect(byline).toContainText('Imported');
  await expect(byline.getByRole('button', { name: /Follow/ })).toHaveCount(0);
  await expect(page.getByText('Unknown channel')).toHaveCount(0);
});

test('test_focus_return_and_seek: menus close on Escape and hand focus back to their opener', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  // Sign-in moves focus into the page (heading or main), never onto the skip link.
  await expect.poll(() => page.evaluate(() => Boolean(document.activeElement?.closest('#main-content')))).toBe(true);
  await page.reload();
  await expect(page.locator('video').first()).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.locator('.g-skip-link')).toBeFocused();
  await expect(page.locator('.g-skip-link')).toBeInViewport();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main-content')).toBeFocused();
  // Mobile navigation drawer: Escape closes it and focus returns to the menu button.
  const opener = page.getByRole('button', { name: 'Open navigation' });
  await opener.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Mobile navigation' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Mobile navigation' })).toBeHidden();
  await expect(opener).toBeFocused();
  // The seek slider is keyboard operable with a visible focus ring.
  const seek = page.getByRole('slider', { name: 'Seek' });
  await seek.focus();
  await expect.poll(() => seek.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none');
  await page.keyboard.press('End');
  await expect.poll(() => seek.evaluate((element) => Number((element as HTMLInputElement).value))).toBeGreaterThan(1);
});

test('gallery walls and the filter sheet: no serious axe findings, sideways scroll or small targets, both themes', async ({ page }) => {
  test.setTimeout(180_000);
  const { mockGalleryWall } = await import('./gallery-mock');
  const found: string[] = [];
  await mockApi(page, { sidebar_collapsed: false });
  await mockGalleryWall(page);
  await page.goto('/library/movies');
  await signIn(page);
  for (const scheme of ['light', 'dark'] as const) {
    for (const viewport of VIEWPORTS) {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize(viewport);
      // Rail letters are exempt from the 44 px rule on phones, so Title A–Z is audited on desktop only.
      for (const path of ['/library/movies', '/library/shows', ...(viewport.width > 600 ? ['/library/movies?sort=name'] : [])]) {
        await page.goto(path);
        await settle(page);
        await audit(page, `gallery ${path} ${scheme} ${viewport.width}`, found);
      }
      await page.goto('/library/movies');
      await settle(page);
      await page.getByRole('button', { name: /^Filters/ }).click();
      await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
      await audit(page, `gallery filters ${scheme} ${viewport.width}`, found);
      await page.keyboard.press('Escape');
    }
  }
  expect(found).toEqual([]);
});

test('Home and Home in edit mode: no serious axe findings, sideways scroll or small targets; keyboard-only walk', async ({ page }) => {
  test.setTimeout(180_000);
  const { continueEntry, homeMovie, mockHome } = await import('./home-mock');
  const { episodeSummary } = await import('../src/test/galleryFixtures');
  const found: string[] = [];
  await mockApi(page, { sidebar_collapsed: false });
  await mockHome(page, { continueWatching: [continueEntry(episodeSummary(2, 4))], nextUp: [episodeSummary(2, 5)], newest: [homeMovie(1), homeMovie(2)] });
  await page.goto('/');
  await signIn(page);
  for (const scheme of ['light', 'dark'] as const) {
    for (const viewport of VIEWPORTS) {
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      await page.setViewportSize(viewport);
      await page.goto('/');
      await settle(page);
      await audit(page, `home ${scheme} ${viewport.width}`, found);
      await page.getByRole('button', { name: 'Edit home' }).click();
      await expect(page.locator('.h-editor')).toBeVisible();
      await audit(page, `home edit ${scheme} ${viewport.width}`, found);
      await page.locator('.h-editor').getByRole('button', { name: 'Done' }).click();
    }
  }
  // Keyboard only: Tab reaches Edit home, the editor's controls in row order, Reset and Done; nothing traps focus.
  await page.setViewportSize(VIEWPORTS[0]);
  await page.goto('/');
  await settle(page);
  await page.getByRole('button', { name: 'Edit home' }).focus();
  await page.keyboard.press('Enter');
  const stops: string[] = [];
  for (let step = 0; step < 4; step += 1) {
    stops.push(await page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.textContent ?? ''));
    await page.keyboard.press('Tab');
  }
  expect(stops).toEqual(['Reorder Continue watching', 'Show Continue watching', 'Move Continue watching up', 'Move Continue watching down']);
  expect(found).toEqual([]);
});

// ---- integration: every state of spec 13.1, one test per area, both themes, desktop and phone ------------------

/** A visible focus ring on the first three tab stops. */
async function focusRingCheck(page: Page, where: string, found: string[]) {
  // Start from the top of the document: a bare body.focus() keeps the last click or focus as the starting point.
  await page.evaluate(() => { document.body.setAttribute('tabindex', '-1'); document.body.focus(); document.body.removeAttribute('tabindex'); window.scrollTo(0, 0); });
  for (let stop = 1; stop <= 3; stop += 1) {
    await page.keyboard.press('Tab');
    const ring = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      if (!element || element === document.body) return 'no focus';
      const style = getComputedStyle(element);
      return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2 ? '' : `${element.tagName}.${element.className} has no ring`;
    });
    if (ring) found.push(`${where}: tab stop ${stop}: ${ring}`);
  }
}

const AREA_VIEWPORTS = [{ width: 1536, height: 960 }, { width: 390, height: 844 }];
type Area = { page: Page; scheme: 'light' | 'dark'; width: number; uiPrefs: Record<string, unknown>; found: string[]; visit: (state: string, ring?: boolean) => Promise<void> };

/** One test per theme × viewport; `visit` audits the state on screen, labelled `{area}-{state}-{theme}-{width}`. */
function areaTest(name: string, run: (area: Area) => Promise<void>) {
  for (const scheme of ['light', 'dark'] as const) {
    for (const viewport of AREA_VIEWPORTS) {
      test(`a11y area ${name} ${scheme} ${viewport.width}`, async ({ page }) => {
        test.setTimeout(180_000);
        const found: string[] = [];
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(error.message));
        await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
        await page.setViewportSize(viewport);
        const uiPrefs: Record<string, unknown> = { sidebar_collapsed: false };
        await mockApi(page, uiPrefs);
        await mockRemoteArtwork(page);
        const visit = async (state: string, ring = false) => {
          await page.waitForTimeout(250);
          const where = `${name}-${state}-${scheme}-${viewport.width}`;
          await audit(page, where, found);
          if (ring) await focusRingCheck(page, where, found);
        };
        await run({ page, scheme, width: viewport.width, uiPrefs, found, visit });
        expect(found).toEqual([]);
        expect(errors).toEqual([]);
      });
    }
  }
}

const RING: Ring = [
  { user_id: 'member-2', display_name: 'Sam Rivers', username: 'sam', role: 'viewer', switch: 'instant', active: false },
  { user_id: 'member-1', display_name: 'Alexandria', username: 'alexandria', role: 'admin', switch: 'password', active: false },
];
const AUTH_ROUTE = '**/api/session/login';

areaTest('auth', async ({ page, visit }) => {
  await page.goto('/');
  await expect(page.locator('.g-auth')).toBeVisible();
  await visit('login', true);
  for (const hash of ['invite', 'reset']) {
    await page.goto(`/#${hash}=sweep-token`);
    await expect(page.locator('.g-auth')).toBeVisible();
    await visit(hash);
  }
  await page.goto('/');
  await page.route(AUTH_ROUTE, (route) => route.fulfill({ status: 401, json: { detail: 'Invalid username or password.' } }));
  await page.getByLabel('Username').fill('alexandria');
  await page.getByLabel('Password', { exact: true }).fill('wrong');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  await visit('login-error');
  await page.route(AUTH_ROUTE, (route) => route.fulfill({ status: 429, headers: { 'retry-after': '30' }, json: { detail: 'Too many attempts. Try again later.' } }));
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  await visit('rate-limited');
  await page.route('**/api/bootstrap/status', (route) => route.fulfill({ json: { needs_setup: true } }));
  await page.goto('/');
  await expect(page.locator('.g-auth')).toBeVisible();
  await visit('setup');
  await page.route('**/api/bootstrap/status', (route) => route.fulfill({ json: { needs_setup: false } }));
  await mockDeviceRing(page, RING);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
  await visit('picker', true);
  await page.getByRole('button', { name: 'Alexandria, vault owner, needs password' }).click();
  await expect(page.getByLabel('Password for Alexandria')).toBeVisible();
  await visit('picker-password');
});

areaTest('onboarding', async ({ page, visit }) => {
  const categories = [{ key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }];
  const candidate = (name: string, slug: string) => ({ channel_key: slug, source_url: `https://www.youtube.com/@${slug}`, display_name: name, source: 'youtube', source_label: 'YouTube', artwork_url: null, category_keys: [], following: false });
  await page.route('**/api/session/me', (route) => route.fulfill({ json: { user: { ...user, onboarding_status: 'pending' } } }));
  await page.route('**/api/discovery/interests', (route) => route.fulfill({ json: { categories, selected_keys: [] } }));
  await page.route('**/api/discovery/channels', (route) => route.fulfill({ json: { categories: [{ key: 'music', label: 'Music', state: 'ranked', channels: [candidate('Popular Music Channel', 'popular')] }] } }));
  await page.route('**/api/discovery/channels/search', (route) => route.fulfill({ json: { query: 'creator', channels: [candidate('Searched Creator', 'searched')] } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeVisible();
  await visit('interests', true);
  await page.getByRole('checkbox', { name: 'Music' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Follow a few channels' })).toBeVisible();
  await page.getByRole('searchbox').or(page.getByRole('textbox').first()).fill('creator');
  await visit('channels');
  await page.route('**/api/onboarding/complete', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  await page.getByRole('button', { name: /^Continue/ }).click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  await visit('error');
  await page.route('**/api/onboarding/complete', () => new Promise<void>(() => undefined)); // never answers: the preparing phase stays up
  await page.getByRole('button', { name: /^Continue/ }).click();
  await expect(page.getByRole('status').first()).toBeVisible();
  await visit('preparing');
});

/** Signed in at `path` (mocks are registered by the caller first). */
async function openSigned(page: Page, path: string) {
  await page.goto(path);
  await page.locator('.g-auth input, main[aria-label="Main content"]').first().waitFor(); // the auth loading stage has no field
  if (await page.locator('.g-auth input').first().isVisible()) await signIn(page); // the mocked session survives later navigations
  await settle(page);
}

areaTest('shell', async ({ page, uiPrefs, width, visit }) => {
  await mockHome(page);
  await openSigned(page, '/');
  await visit('sidebar-expanded', true);
  uiPrefs.sidebar_collapsed = true;
  await page.reload();
  await settle(page);
  await visit('sidebar-collapsed');
  if (width <= 390) {
    await page.getByRole('button', { name: /^More(,|$)/ }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await visit('more-drawer');
    await page.keyboard.press('Escape');
  }
  await page.locator('.g-profile-trigger').click();
  await expect(page.getByRole('menu')).toBeVisible();
  await visit('profile-menu');
  await page.keyboard.press('Escape');
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill({ json: { items: [{ id: 'job-run', source_url: 'https://example.test/run', status: 'running', title: 'A documentary', progress: 42, created_at: AT }], next_cursor: null } }));
  await page.reload();
  await settle(page);
  const activity = page.locator('.g-activity');
  if (await activity.isVisible()) {
    await activity.click();
    await visit('activity-popover');
  }
});

areaTest('live-explore', async ({ page, visit }) => {
  const live = await mockLive(page, liveSnapshotBody({ hero: liveItems(1, {}, 'hero'), upcoming: liveItems(2, { kind: 'upcoming', capabilities: caps('youtube', 'upcoming') }, 'soon') }));
  await mockPaletteSearch(page);
  await mockReco(page);
  await mockPopular(page, popularWithForYou());
  await openSigned(page, '/streaming/live');
  await expect(page.getByRole('button', { name: /^Record/ }).first()).toBeVisible(); // the snapshot has arrived, not just the masthead
  await visit('live', true);
  const record = page.getByRole('button', { name: /^Record/ }).first();
  if (await record.isVisible().catch(() => false)) { await record.click(); if (await page.getByRole('dialog').isVisible()) { await visit('record-dialog'); await page.keyboard.press('Escape'); } }
  live.set(liveSnapshotBody({ items: [], hero: [], state: 'empty' }));
  await page.reload(); await settle(page); await visit('live-empty');
  await page.route('**/api/discovery/live', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  await page.reload(); await settle(page); await visit('live-error');
  await openSigned(page, '/streaming/search?q=alpine'); await visit('explore-results');
  await openSigned(page, '/streaming'); await visit('explore-for-you');
});

areaTest('subscriptions', async ({ page, visit }) => {
  const formatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: 'none', output_container: 'mp4' };
  const outputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
  const rules = { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null };
  const follow = (id: string, label: string, sourceUrl: string) => ({
    id, user_id: user.id, label, source_url: sourceUrl, source_type: 'channel', artwork_url: null, cron_expression: '*/30 * * * *', active: true, auto_download: false,
    format_selection: formatSelection, output_profile: outputProfile, rules, duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10,
    last_checked_at: '2026-09-29T20:40:00', next_check_at: '2026-09-29T21:10:00', last_error: null, last_run_summary: { discovered: 3 }, created_at: AT, updated_at: AT,
    feed_entries: [1, 2, 3].map((n) => ({ id: `${id}-${n}`, title: `${label} latest ${n}`, uploader: label, duration: 600, artwork_url: `/api/artwork/remote/${id}-${n}`, webpage_url: `https://www.youtube.com/watch?v=${id}${n}`, capabilities: caps('youtube', 'vod') })),
  });
  const follows = [follow('yt-id', 'Harbor Films', `https://www.youtube.com/channel/${CHANNEL_ID}`), follow('yt-two', 'Second Films', 'https://www.youtube.com/@secondfilms')];
  await mockLive(page, liveSnapshotBody({ hero: liveItems(1, { uploader: 'Harbor Films', uploader_id: CHANNEL_ID }, 'follow'), items: [] }));
  await mockChannelPage(page);
  await mockLibraryChannels(page, 12);
  await page.route('**/api/automations', (route) => route.fulfill({ json: follows }));
  await page.route('**/api/follows/refresh', (route) => route.fulfill({ json: follows }));
  await openSigned(page, '/streaming/channels'); await visit('list', true);
  await page.getByRole('link', { name: /^Second Films/ }).click();
  await expect(page.getByRole('button', { name: 'Follow settings' })).toBeVisible();
  await page.getByRole('button', { name: 'Follow settings' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await visit('follow-settings');
  await page.keyboard.press('Escape');
  for (const tab of ['videos', 'live', 'playlists']) { await openSigned(page, `/channel/youtube/${CHANNEL_ID}?tab=${tab}`); await expect(page.locator('.g-channel-wall, .g-channel-empty, main').first()).toBeVisible(); await visit(`channel-${tab}`, tab === 'videos'); }
  await openSigned(page, '/library/youtube?view=channels'); await visit('library-channels');
  await page.route(/\/api\/library\/channels(\?|$)/, (route) => route.fulfill({ json: [] }));
  await page.reload(); await settle(page); await visit('library-channels-empty');
  await page.route('**/api/automations', (route) => route.fulfill({ json: [] }));
  await openSigned(page, '/streaming/channels'); await visit('empty');
  await page.route('**/api/automations', (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  await page.reload(); await settle(page); await visit('error');
});

areaTest('recommendations', async ({ page, visit }) => {
  await mockHome(page);
  await mockPopular(page, popularWithForYou());
  await mockReco(page, { suppressions: suppressionList({ items: [suppression('item')], channels: [suppression('channel')], fewer: [suppression('fewer')], titles: [suppression('title')] }) });
  const picked = page.locator('[data-shelf-section="picked_for_you"]');
  await openSigned(page, '/');
  await expect(picked.getByRole('heading', { name: 'Picked for you' })).toBeVisible();
  await visit('home-picked', true);
  await picked.getByRole('button', { name: /^More options for / }).first().click();
  await expect(page.getByRole('menu')).toBeVisible();
  await visit('home-menu');
  await page.getByRole('menuitem', { name: 'Not interested' }).click();
  await expect(page.getByRole('button', { name: 'Undo' }).first()).toBeVisible();
  await visit('undo-row');
  await openSigned(page, '/streaming');
  await page.getByRole('button', { name: /^More options for / }).first().click();
  await expect(page.getByRole('menu')).toBeVisible();
  await visit('explore-menu');
  await page.keyboard.press('Escape');
  await openSigned(page, '/settings/discovery'); await visit('discovery');
  await page.getByRole('button', { name: /Clear/ }).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await visit('clear-history-dialog');
  await page.keyboard.press('Escape');
  const quiet = recoSurfaceStats({ surface: 'up_next', impressions: 12, opens: 3, ctr: null, plays: 1, play_through_median: null, completion_rate: null, negative_rate: null, explore_play_rate: null, exploit_play_rate: null });
  await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ json: { ...DIAGNOSTICS, recommendations: recoDiagnostics({ surfaces: [recoSurfaceStats(), quiet] }) } }));
  await openSigned(page, '/admin/diagnostics'); await visit('diagnostics-with-null-rates');
  await page.route('**/api/admin/diagnostics', (route) => route.fulfill({ json: { ...DIAGNOSTICS, recommendations: recoDiagnostics({ surfaces: [recoSurfaceStats()] }) } }));
  await page.reload(); await settle(page); await visit('diagnostics');
});

areaTest('palette', async ({ page, width, found, visit }) => {
  await mockPaletteSearch(page);
  await openSigned(page, '/');
  const open = async () => { await page.keyboard.press('ControlOrMeta+k'); await expect(page.locator('dialog.g-palette')).toBeVisible(); };
  const field = () => page.locator('dialog.g-palette').getByRole('combobox');
  const close = async () => { for (let step = 0; step < 3 && await page.locator('dialog.g-palette').isVisible(); step += 1) await page.keyboard.press('Escape'); };
  await focusRingCheck(page, `palette-page-${width}`, found); // a modal dialog traps focus; the ring rule is checked on the page behind it
  await open(); await visit('empty');
  await field().fill('alp'); await expect(page.getByRole('group', { name: 'Go to' }).or(page.locator('.g-palette-option').first()).first()).toBeVisible(); await visit('all-groups');
  await field().fill('zz'); await expect(page.locator('dialog.g-palette')).toContainText('Search everything for “zz”'); await expect(page.locator('dialog.g-palette')).not.toContainText('Alpine Crossing'); await visit('no-results');
  await close();
  await page.route('**/api/search?*', (route) => route.fulfill({ status: 503, json: { detail: 'down' } }));
  await page.route('**/api/youtube-search', (route) => route.fulfill({ status: 503, json: { detail: 'down' } }));
  await open(); await field().fill('alp'); await expect(page.getByRole('alert').first()).toBeVisible(); await visit('error');
  await close();
  await page.getByRole('button', { name: 'Add a link' }).first().click();
  await expect(page.getByRole('dialog', { name: 'Add a link' })).toBeVisible();
  await field().fill('not a link'); await page.keyboard.press('Enter'); await expect(page.getByRole('alert').first()).toBeVisible(); await visit('invalid-link');
  if (width <= 390) { await close(); await page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: 'Search' }).click(); await expect(page.locator('dialog.g-palette')).toBeVisible(); await visit('phone-sheet'); }
});

areaTest('member-picker-dialog', async ({ page, visit }) => {
  await openSigned(page, '/');
  await mockDeviceRing(page, RING);
  await page.reload(); await settle(page); // the ring is read when the app boots
  await page.locator('.g-profile-trigger').click();
  await page.getByRole('menuitem', { name: /Switch member|Sign in as someone else/ }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await visit('dialog', true);
});

areaTest('settings', async ({ page, visit }) => {
  await page.route((url) => url.pathname.startsWith('/api/me/jellyfin-import'), (route) => route.request().method() === 'GET'
    ? route.fulfill({ json: { server: 'http://192.168.1.20:8096' } })
    : route.fulfill({ json: { watched: 12, in_progress: 2, favorites: 3, up_to_date: 4, unmatched: 1, unmatched_names: ['Heat (1995)'] } }));
  await mockAdminFixtures(page);
  await mockMembers(page);
  await openSigned(page, '/settings'); await visit('landing', true);
  await openSigned(page, '/settings/media');
  await page.getByRole('textbox', { name: 'TMDB API key' }).fill('synthetic-key');
  await expect(page.locator('.g-save-bar button[type="submit"]')).toBeEnabled();
  await visit('save-bar');
  await openSigned(page, '/settings');
  const search = page.getByRole('searchbox', { name: 'Search settings' });
  if (await search.isVisible()) { await search.fill('theme'); await visit('search'); }
  await openSigned(page, '/settings/privacy');
  const row = page.locator('[data-setting-id="privacy.jellyfin-history"]');
  await row.getByLabel('Jellyfin username').fill('alice');
  await row.getByLabel('Jellyfin password').fill('synthetic');
  await row.getByRole('button', { name: 'Preview', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Import from Jellyfin?' })).toBeVisible();
  await visit('jellyfin-dialog');
  await page.keyboard.press('Escape');
  await openSigned(page, '/settings/members');
  await (await openSam(page)).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await visit('members-menu-or-confirm');
});

areaTest('downloads', async ({ page, visit }) => {
  const jobs = [
    { id: 'job-run', source_url: 'https://example.test/run', status: 'running', title: 'A long documentary about river deltas', progress: 42, downloaded_bytes: 420 * 1024 ** 2, speed: 6 * 1024 ** 2, eta: 95, created_at: AT },
    { id: 'job-fail', source_url: 'https://example.test/fail', status: 'failed', title: 'A video that went private', error: 'This video is private.', attempts: [], created_at: AT },
    { id: 'job-done', source_url: 'https://example.test/done', status: 'completed', title: 'Evening concert recording', outputs: [{ library_item_id: item.id, root_label: 'Archive disk', folder: 'Video UHD' }], created_at: AT, finished_at: AT },
  ];
  const batch = { id: 'batch-1', source_url: 'https://example.test/list', source_title: 'Weekend cooking playlist', source_provenance: {}, status: 'partial', selected_count: 3, queued_count: 0, duplicate_count: 0, completed_count: 2, failed_count: 1, progress: 67, format_selection: {}, output_profile: {}, created_at: AT, updated_at: AT, finished_at: AT, entries: [{ id: 'e1', batch_id: 'batch-1', selection_index: 0, title: 'Sourdough basics', status: 'completed', progress: 100 }, { id: 'e2', batch_id: 'batch-1', selection_index: 1, title: 'Knife skills', status: 'failed', progress: 0, error: 'This video is unavailable.' }] };
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route(/\/api\/jobs(\?|$)/, async (route) => { await gate; await route.fulfill({ json: { items: jobs, next_cursor: null } }); });
  await page.route(/\/api\/acquisition-batches(\?|$)/, (route) => route.fulfill({ json: [batch] }));
  await page.goto('/downloads');
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Downloads' })).toBeVisible();
  await visit('loading', true);
  release();
  await expect(page.getByText('Weekend cooking playlist')).toBeVisible();
  await visit('active-failed-finished');
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route(/\/api\/acquisition-batches(\?|$)/, (route) => route.fulfill({ json: [] }));
  await page.reload(); await settle(page); await visit('empty');
  await page.route(/\/api\/jobs(\?|$)/, (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  await page.reload(); await settle(page); await visit('error');
});

areaTest('playlist-acquisition', async ({ page, visit }) => {
  const playlist = {
    kind: 'playlist', title: 'Evening picks', extractor: 'youtube:tab', extractor_key: 'YoutubeTab', webpage_url: 'https://www.youtube.com/playlist?list=one', availability: 'public',
    entries: [{ id: 'one', title: 'First film', uploader: 'Archive', duration: 120 }, { id: 'two', title: 'Second film', uploader: 'Archive', duration: 180 }, { id: 'three', title: 'Third film', uploader: 'Archive', duration: 240 }],
    raw: { id: 'one', uploader: 'Archive' },
  };
  await page.route('**/api/preview', (route) => route.fulfill({ json: playlist }));
  await openSigned(page, `/watch?url=${encodeURIComponent(playlist.webpage_url)}`).catch(() => undefined);
  await expect(page.getByRole('checkbox', { name: /first film/i })).toBeVisible();
  await page.getByRole('checkbox', { name: /first film/i }).check();
  await page.getByRole('checkbox', { name: /second film/i }).check();
  await visit('two-selected', true);
});

areaTest('collections', async ({ page, visit }) => {
  const collection = (id: string, name: string, patch: Record<string, unknown> = {}) => ({ id, owner_user_id: user.id, name, description: null, visibility: 'shared', revision: 1, item_count: 0, items: [], entries: [], titles: [], created_at: AT, updated_at: AT, ...patch });
  let rows = [collection('c-1', 'Rainy Sundays', { item_count: 1, items: [item], entries: [{ id: 'e1', position: 0, availability: 'available', ref: { kind: 'library', library_item_id: item.id, provider: null, remote_id: null, url: null }, title: item.title, uploader: null, artwork_url: null, duration: 90 }] }), collection('c-2', 'Short films')];
  await page.route((url) => url.pathname.startsWith('/api/collections'), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/collections') return route.fulfill({ json: rows });
    const found = rows.find((row) => path === `/api/collections/${row.id}`);
    return found ? route.fulfill({ json: found }) : route.fulfill({ status: 404, json: { detail: 'missing' } });
  });
  await openSigned(page, '/library/collections'); await visit('list', true);
  await openSigned(page, '/library/collections/c-1'); await visit('with-items');
  await openSigned(page, '/library/collections/c-2'); await visit('empty-collection');
  await openSigned(page, '/library/collections?new=smart'); await expect(page.getByRole('dialog', { name: 'New smart collection' })).toBeVisible(); await visit('smart-builder');
  await page.keyboard.press('Escape');
  rows = [];
  await page.reload(); await settle(page); await visit('empty');
});

areaTest('deleted', async ({ page, visit }) => {
  const { mockGalleryWall, mockLibrarySections } = await import('./gallery-mock');
  const { mockLibraryItems } = await import('./library-mock');
  await mockGalleryWall(page);
  await mockLibrarySections(page, { deleted: 2 });
  await mockLibraryItems(page, { deleted: 2 });
  await openSigned(page, '/library/deleted'); await visit('rows', true);
  await mockLibrarySections(page, { deleted: 0 });
  await mockLibraryItems(page, { deleted: 0 });
  await page.reload(); await settle(page); await visit('empty');
});

areaTest('watch-web', async ({ page, visit }) => {
  await mockReco(page, { similar: [] });
  await openSigned(page, `/watch/library/${item.id}`);
  await expect(page.locator('video')).toHaveCount(1);
  await visit('editorial', true);
  const more = page.getByRole('button', { name: 'More', exact: true });
  if (await more.isVisible().catch(() => false)) { await more.click(); await visit('description-open'); }
});

async function pausedVideo(page: Page) {
  await expect(page.locator('video')).toHaveCount(1);
  await page.locator('video').evaluate((media: HTMLVideoElement) => media.pause());
}

/** Range-capable media, so seeking a paused synthetic video sticks. */
async function mockRangeMedia(page: Page) {
  const media = Buffer.from(SYNTHETIC_MEDIA_BASE64, 'base64');
  await page.route(`**/api/library/${item.id}/media`, async (route) => {
    const [, start = '0', end] = /bytes=(\d*)-(\d*)/.exec(route.request().headers().range || '') || [];
    const from = Number(start) || 0;
    const to = end ? Number(end) : media.length - 1;
    await route.fulfill({ status: 206, headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${from}-${to}/${media.length}`, 'Content-Type': SYNTHETIC_MEDIA_TYPE }, body: media.subarray(from, to + 1) });
  });
}

areaTest('player', async ({ page, uiPrefs, visit }) => {
  await mockRangeMedia(page);
  await page.route(`**/api/library/${item.id}/segments`, (route) => route.fulfill({ json: { item_id: item.id, segments: [{ type: 'intro', start_seconds: 0.1, end_seconds: 0.9, source: 'fingerprint', confidence: 0.9 }] } }));
  await openSigned(page, `/watch/library/${item.id}`);
  await pausedVideo(page);
  await page.keyboard.press('Shift');
  await page.getByRole('slider', { name: 'Seek' }).focus();
  await visit('controls', true);
  await page.getByRole('button', { name: 'Playback settings' }).first().click();
  await visit('playback-panel');
  await page.keyboard.press('Escape');
  await page.locator('video').evaluate((media: HTMLVideoElement) => { media.currentTime = 0.2; });
  await expect(page.getByRole('button', { name: /^Skip/ })).toBeVisible();
  await visit('skip-button');
  for (const size of ['small', 'large']) {
    uiPrefs.captions = { size, background: 'box' };
    await page.reload(); await pausedVideo(page); await visit(`captions-${size}`);
  }
  await page.getByRole('button', { name: 'Enter theater mode' }).click();
  await visit('theater');
  await page.route(`**/api/library/${item.id}/media`, (route) => route.fulfill({ status: 500, json: { detail: 'boom' } }));
  await page.reload();
  await expect(page.getByRole('alert').first().or(page.locator('.player-error, .g-state').first())).toBeVisible();
  await visit('recovery-error');
});

areaTest('player-up-next', async ({ page, visit }) => {
  const ep = 'item-ep-2-2';
  await mockTitles(page);
  await page.route(`**/api/library/${ep}/media`, fulfillRangeMedia);
  await page.route(`**/api/library/${ep}/segments`, (route) => route.fulfill({ json: episodeSegments(ep) }));
  const segments = page.waitForResponse(`**/api/library/${ep}/segments`); // the player reads them on a time update, so they must be in before the seek
  await openSigned(page, `/watch/library/${ep}`);
  await segments;
  await expect(page.locator('video')).toHaveCount(1);
  await expect.poll(() => page.locator('video').evaluate((media: HTMLVideoElement) => media.readyState)).toBeGreaterThanOrEqual(1);
  await pauseAt(page, 1.05);
  await expect(page.getByRole('region', { name: 'Next episode' })).toBeVisible();
  await visit('card', true);
});

areaTest('watch-live', async ({ page, visit }) => {
  const url = 'https://www.youtube.com/watch?v=live-watch1';
  await mockLive(page, liveSnapshotBody({ items: [{ id: 'live-watch1', webpage_url: url, view_count: 13_000, capabilities: caps('youtube', 'live'), category_keys: ['gaming'] }] }));
  await page.route('**/api/preview', (route) => route.fulfill({ json: { kind: 'video', title: 'Harbor at night, live', webpage_url: url, entries: [], artwork_url: '/api/artwork/remote/p1', playback: null,
    capabilities: caps('youtube', 'live', { can_record: true, chat: { live: 'available', replay: 'unavailable' } }), chapters: [], description_timestamps: [],
    raw: { extractor_key: 'Youtube', uploader: 'Harbor Films', channel_id: CHANNEL_ID, channel_follower_count: 2_100_000, description: 'Live from the harbor.', concurrent_view_count: 12_412 } } }));
  await page.route(/\/api\/playback\/remote\//, (route) => route.fulfill({ json: null }));
  await openSigned(page, `/watch?url=${encodeURIComponent(url)}`);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor at night, live' })).toBeVisible();
  await visit('live', true);
});

areaTest('mini-player', async ({ page, width, visit }) => {
  await mockHome(page);
  await openSigned(page, `/watch/library/${item.id}`);
  await pausedVideo(page);
  const home = width <= 390
    ? page.getByRole('navigation', { name: 'Mobile primary navigation' }).getByRole('button', { name: 'Home' })
    : page.getByRole('button', { name: 'Home', exact: true }).and(page.locator('#primary-navigation button'));
  await home.click();
  await expect(page.getByRole('region', { name: /^Mini player: / })).toBeVisible();
  await visit('docked', true);
});

areaTest('toasts', async ({ page, visit }) => {
  const { mockGalleryWall, mockLibrarySections } = await import('./gallery-mock');
  const { mockLibraryItems } = await import('./library-mock');
  await mockGalleryWall(page);
  await mockLibrarySections(page, { deleted: 3 });
  await mockLibraryItems(page, { deleted: 3 });
  await openSigned(page, '/library/deleted');
  const rows = page.locator('.g-deleted-rows li');
  await rows.first().getByRole('button', { name: 'Restore' }).click();
  await expect(page.locator('.g-toast.is-success').first()).toBeVisible();
  await visit('success');
  await page.route(/\/api\/library\/[^/]+\/restore-file$/, (route) => route.fulfill({ status: 409, json: { detail: 'The file is gone from disk.' } }));
  await rows.last().getByRole('button', { name: 'Restore' }).click();
  await expect(page.locator('.g-toast.is-error').first()).toBeVisible();
  await visit('error');
});

// ---- integration: the extra passes ---------------------------------------------------------------------------

/** Two household members besides the owner, so a confirm dialog (Deactivate) has an active target. */
async function mockMembers(page: Page) {
  const sam = { ...user, id: 'member-2', username: 'sam', display_name: 'Sam', role: 'viewer', has_local_password: true };
  const eleanor = { ...user, id: 'member-3', username: 'eleanor', display_name: 'Eleanor', role: 'viewer', is_active: false, has_local_password: true };
  await page.route(/\/api\/admin\/users(\?|$)/, (route) => route.fulfill({ json: [user, sam, eleanor] }));
}

async function sweepMocks(page: Page) {
  const { mockGalleryWall, mockLibrarySections } = await import('./gallery-mock');
  await mockApi(page, { sidebar_collapsed: false });
  await mockAdminFixtures(page);
  await mockMembers(page);
  await mockRemoteArtwork(page);
  await mockReco(page);
  await mockHome(page);
  await mockTitles(page);
  await mockGalleryWall(page); // after mockTitles and mockHome: the wall mock answers /api/titles
  await mockLibrarySections(page, {});
  await mockLive(page, liveSnapshotBody({ hero: liveItems(1, {}, 'hero') }));
  await mockPaletteSearch(page);
}

/** The "same set" of the forced-colours and more-contrast passes: shell, palette, settings, a wall, Live, and a dialog. */
async function passStates(page: Page, found: string[], inspectNow: (name: string) => Promise<void>) {
  const inspect = async (name: string) => { await page.waitForTimeout(250); await inspectNow(name); };
  await page.goto('/');
  await signIn(page);
  await page.keyboard.press('Tab');
  await inspect('shell');
  await page.keyboard.press('ControlOrMeta+k');
  await page.getByRole('combobox').fill('alp');
  await page.keyboard.press('ArrowDown');
  await inspect('palette');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await page.goto('/settings/playback'); await settle(page); await inspect('settings');
  await page.goto('/library/movies'); await settle(page); await inspect('wall');
  await page.goto('/streaming/live'); await settle(page); await inspect('live');
  await page.goto('/settings/members'); await settle(page);
  await (await openSam(page)).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await inspect('dialog');
  await page.keyboard.press('Escape');
  await page.goto(`/watch/library/${item.id}`); await settle(page); await inspect('player');
  expect(found).toEqual([]);
}

test('forced colours: focus, selection rules and poster markers stay visible', async ({ page }) => {
  test.setTimeout(180_000);
  await page.emulateMedia({ forcedColors: 'active', colorScheme: 'light' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await sweepMocks(page);
  const found: string[] = [];
  const visible = async (selector: string, property: 'outlineColor' | 'borderInlineStartColor' | 'borderTopColor') => {
    const value = await page.locator(selector).first().evaluate((element, name) => getComputedStyle(element)[name as 'outlineColor'], property);
    if (!value || value === 'transparent' || value === 'rgba(0, 0, 0, 0)') found.push(`${selector} ${property} invisible`);
  };
  await page.goto('/');
  await signIn(page);
  await settle(page);
  await page.keyboard.press('Tab'); await visible(':focus', 'outlineColor'); // shell focus
  await visible('.g-nav-item[aria-current="page"]', 'borderInlineStartColor'); // selected rule
  await page.keyboard.press('ControlOrMeta+k'); await page.getByRole('combobox').fill('alp');
  await page.keyboard.press('ArrowDown'); await visible('.g-palette-option[aria-selected="true"]', 'borderInlineStartColor'); // palette
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  await page.goto('/settings/playback'); await visible('.g-settings-link[aria-current="page"]', 'borderInlineStartColor'); // settings
  await page.goto('/library/movies'); await settle(page); await visible('.g-marker-triangle', 'borderTopColor'); // poster markers
  await page.goto('/streaming/live'); await settle(page); await visible('.g-live', 'borderTopColor'); // the LIVE badge keeps an edge
  expect(found).toEqual([]);
});

test('more contrast: axe on the shell, palette, settings, a dialog, the player and a wall', async ({ page }) => {
  test.setTimeout(240_000);
  await page.emulateMedia({ contrast: 'more', colorScheme: 'light', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await sweepMocks(page);
  const found: string[] = [];
  await passStates(page, found, (name) => audit(page, `more-contrast-${name}`, found));
});

test('reduced motion: nothing animates after an overlay opens', async ({ page }) => {
  test.setTimeout(240_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await sweepMocks(page);
  await page.route('**/api/discovery/home', (route) => route.fallback());
  const still = async (name: string) => { await page.waitForTimeout(200); expect(await page.evaluate(() => document.getAnimations().length), name).toBe(0); };
  await page.goto('/');
  await signIn(page);
  // Wait for each overlay to be open (the palette is a lazy chunk; under load an early Escape misses it).
  await page.keyboard.press('ControlOrMeta+k'); await expect(page.locator('dialog.g-palette')).toBeVisible(); await still('palette');
  await page.keyboard.press('Escape'); await expect(page.locator('dialog.g-palette')).toHaveCount(0);
  await page.locator('.g-profile-trigger').click(); await expect(page.getByRole('menu')).toBeVisible(); await still('profile menu'); await page.keyboard.press('Escape');
  await page.goto('/settings/members'); await settle(page);
  await (await openSam(page)).click();
  await expect(page.getByRole('dialog')).toBeVisible(); await still('dialog'); await page.keyboard.press('Escape');
  const { mockGalleryWall, mockLibrarySections } = await import('./gallery-mock');
  const { mockLibraryItems } = await import('./library-mock');
  await mockGalleryWall(page);
  await mockLibrarySections(page, { deleted: 2 });
  await mockLibraryItems(page, { deleted: 2 });
  await page.goto('/library/deleted'); await settle(page);
  await page.locator('.g-deleted-rows li').first().getByRole('button', { name: 'Restore' }).click();
  await expect(page.locator('.g-toast').first()).toBeVisible(); await still('toast');
  await mockRangeMedia(page);
  await page.goto(`/watch/library/${item.id}`); await expect(page.locator('video')).toHaveCount(1);
  await page.locator('video').evaluate((media: HTMLVideoElement) => media.pause());
  await page.getByRole('button', { name: 'Home', exact: true }).and(page.locator('#primary-navigation button')).click();
  await expect(page.getByRole('region', { name: /^Mini player: / })).toBeVisible(); await still('mini player');
  await page.locator('[data-shelf-section="picked_for_you"]').getByRole('button', { name: /^More options for / }).first().click();
  await still('reco menu');
  await page.getByRole('menuitem', { name: 'Not interested' }).click(); await still('undo row');
  await page.goto('/streaming/live'); await settle(page); await still('live');
});

// ---- integration: keyboard-only walks ------------------------------------------------------------------------

/** Settings → Members → Sam's member page (2.8.0 rows open a page); returns the Deactivate button that opens the confirm dialog. */
async function openSam(page: Page) {
  await page.getByRole('list', { name: 'Household members' }).getByRole('link', { name: /^Sam\b/ }).click();
  await expect(page.getByRole('heading', { name: 'Sam' })).toBeVisible();
  return page.getByRole('button', { name: 'Deactivate', exact: true });
}

async function walkSetup(page: Page) {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await sweepMocks(page);
  await mockPopular(page, popularWithForYou());
}

test('walk 1: sign in with the keyboard alone and land on the Home heading', async ({ page }) => {
  await walkSetup(page);
  await page.goto('/');
  await expect(page.locator('.g-auth')).toBeVisible();
  await page.getByLabel('Username').focus();
  await page.keyboard.type(user.username);
  await page.keyboard.press('Tab');
  await page.keyboard.type('correct-horse');
  await page.keyboard.press('Enter');
  await expect(page.locator('main h1').first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.activeElement?.closest('#main-content') !== null && document.activeElement?.tagName !== 'BODY')).toBe(true);
});

test('walk 2: the palette picks a title by keyboard and opens its page', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1536, height: 960 });
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  await mockPaletteSearch(page);
  await page.goto('/'); await signIn(page);
  await page.keyboard.press('ControlOrMeta+k');
  await page.getByRole('combobox').fill('alp');
  await expect(page.locator('.g-palette-option').filter({ hasText: 'Alpine Crossing' }).first()).toBeVisible();
  for (let step = 0; step < 12; step += 1) {
    const active = await page.locator('.g-palette-option[aria-selected="true"]').first().textContent().catch(() => '');
    if (/^Alpine Crossing\s*Film/.test(active ?? '')) break;
    await page.keyboard.press('ArrowDown');
  }
  await page.keyboard.press('Enter');
  // The mocked search row carries a partial title summary, so the page body is not asserted here (palette.spec.ts does the same).
  await expect(page).toHaveURL(/\/title\/movie-1/);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('walk 3: Switch member by keyboard signs in with remember_on_device', async ({ page }) => {
  await walkSetup(page);
  await page.goto('/'); await signIn(page);
  await mockDeviceRing(page, RING);
  await page.reload(); await settle(page);
  await page.locator('.g-profile-trigger').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('menuitem', { name: /Switch member|Sign in as someone else/ }).focus();
  await page.keyboard.press('Enter');
  const owner = page.getByRole('button', { name: 'Alexandria, vault owner, needs password' });
  await owner.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Password for Alexandria')).toBeFocused();
  await page.keyboard.type('synthetic-passphrase');
  const login = page.waitForRequest('**/api/session/login');
  await page.keyboard.press('Enter');
  expect((await login).postDataJSON()).toEqual({ username: 'alexandria', password: 'synthetic-passphrase', remember_on_device: true });
});

test('walk 4: the Settings D-pad moves between the sections and the pane', async ({ page }) => {
  await walkSetup(page);
  await page.goto('/settings/playback'); await signIn(page);
  await settle(page);
  const link = page.locator('.g-settings-link[aria-current="page"]');
  await link.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.g-settings-link:focus')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute('aria-current'))).not.toBe('page');
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => page.evaluate(() => document.activeElement?.closest('.g-settings-pane, .settings-pane, main') !== null && !document.activeElement?.classList.contains('g-settings-link'))).toBe(true);
});

test('walk 5: a confirm dialog closes on Escape and returns focus to its trigger', async ({ page }) => {
  await walkSetup(page);
  await page.goto('/settings/members'); await signIn(page); await settle(page);
  const actions = await openSam(page);
  await actions.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Deactivate Sam?' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(actions).toBeFocused();
});

test('walk 6: the player seek slider moves with arrows and the playback panel closes on Escape', async ({ page }) => {
  await walkSetup(page);
  await mockRangeMedia(page);
  await page.goto(`/watch/library/${item.id}`); await signIn(page);
  await pausedVideo(page);
  await page.keyboard.press('Shift');
  const seek = page.getByRole('slider', { name: 'Seek' });
  await seek.focus();
  const before = await seek.evaluate((element) => Number((element as HTMLInputElement).value));
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => seek.evaluate((element) => Number((element as HTMLInputElement).value))).toBeGreaterThan(before);
  const settings = page.getByRole('button', { name: 'Playback settings' }).first();
  await settings.focus();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Escape');
  await expect(settings).toBeFocused();
});

test('walk 7: Escape leaves an Explore See all wall and returns focus to its control', async ({ page }) => {
  await walkSetup(page);
  const popular = {
    items: ['news', 'music', 'gaming'].flatMap((key) => Array.from({ length: 30 }, (_, index) => ({ id: `${key}-${index}`, title: `Popular ${key} ${index + 1}`, uploader: 'Harbor Films', source: 'youtube', source_label: 'YouTube', kind: 'video', duration: 300 + index, webpage_url: `https://www.youtube.com/watch?v=${key}-${index}`, artwork_url: `/api/artwork/remote/${key}-${index}`, category_keys: [key], capabilities: caps('youtube', 'vod') }))),
    categories: [{ key: 'news', label: 'News', state: 'ready' }, { key: 'music', label: 'Music', state: 'ready' }, { key: 'gaming', label: 'Gaming', state: 'ready' }],
    state: 'ready', refreshing: false, stale: false, error: null, last_success_at: AT, refreshed_at: AT,
  };
  await page.route('**/api/discovery/popular', (route) => route.fulfill({ json: popular }));
  await page.goto('/streaming'); await signIn(page);
  const seeAll = page.locator('section.g-rail:not([data-rail-key="live-now"]) .g-rail-all').first();
  await seeAll.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.g-rail-wall')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.g-rail-wall')).toHaveCount(0);
  await expect(page.locator('section.g-rail:not([data-rail-key="live-now"]) .g-rail-all').first()).toBeFocused();
});

test('walk 8: a Subscriptions tile opens its channel with focus on the page, never the body', async ({ page }) => {
  await walkSetup(page);
  const follow = { id: 'yt-id', user_id: user.id, label: 'Harbor Films', source_url: `https://www.youtube.com/channel/${CHANNEL_ID}`, source_type: 'channel', artwork_url: null, cron_expression: '*/30 * * * *', active: true, auto_download: false, format_selection: {}, output_profile: {}, rules: {}, duplicate_policy: 'skip_same_source', max_items_per_run: 5, max_items_per_day: 20, backfill_limit: 10, last_checked_at: null, next_check_at: null, last_error: null, last_run_summary: {}, created_at: AT, updated_at: AT, feed_entries: [] };
  await mockChannelPage(page);
  await page.route('**/api/automations', (route) => route.fulfill({ json: [follow] }));
  await page.route('**/api/follows/refresh', (route) => route.fulfill({ json: [follow] }));
  await page.goto('/streaming/channels'); await signIn(page);
  const tile = page.getByRole('link', { name: /^Harbor Films/ });
  await tile.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('main h1').first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.activeElement && document.activeElement !== document.body && document.activeElement.closest('main') !== null)).toBe(true);
});

test('walk 9: the reco menu opens on Enter, arrows move, Escape returns, Tab continues from the trigger', async ({ page }) => {
  await walkSetup(page);
  await page.goto('/'); await signIn(page);
  const picked = page.locator('[data-shelf-section="picked_for_you"]');
  const button = picked.getByRole('button', { name: /^More options for / }).first();
  await button.focus();
  await page.keyboard.press('Enter');
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(menu.getByRole('menuitem').nth(1)).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await expect(button).toBeFocused();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await expect(menu).toHaveCount(0);
  const next = await page.evaluate(() => ({ inPicked: Boolean(document.activeElement?.closest('[data-shelf-section="picked_for_you"]')), label: document.activeElement?.getAttribute('aria-label') ?? '' }));
  expect(next.inPicked).toBe(true);
  expect(next.label).not.toBe(await button.getAttribute('aria-label'));
});
