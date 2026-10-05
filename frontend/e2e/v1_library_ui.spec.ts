import { expect, test, type Page } from '@playwright/test';
import { item as baseItem, mockApi, mockTitles, movieTitle, signIn, user } from './lumina-mock';
import { answerCategoriesByType } from './library-mock';

/** Library views over saved and imported media (mocked API, synthetic fixtures). */

type Fixture = Omit<typeof baseItem, 'metadata_json' | 'file_path'> & { metadata_json: Record<string, unknown>; file_path: string | null; kind: string; media_state: string | null; playlist_name?: string | null };

let counter = 0;
function fixture(title: string, overrides: Partial<Fixture> & { metadata_json?: Record<string, unknown> } = {}): Fixture {
  counter += 1;
  return { ...baseItem, id: `lib-${counter}`, title, kind: 'video', media_state: 'available', uploader: 'Lumina fixture', duration: 600 + counter * 37, downloaded_at: `2026-02-${String(28 - counter).padStart(2, '0')}T00:00:00Z`, ...overrides } as Fixture;
}
const imported = (title: string, kind: string, metadata: Record<string, unknown>, extra: Partial<Fixture> = {}) => fixture(title, { extractor: 'external_library', kind, metadata_json: { lumina_import_kind: kind, ...metadata }, file_path: null, ...extra });
const episode = (series: string, season: number, number: number, title: string) => imported(title, 'episode', { series, season_number: season, episode_number: number }, { uploader: series, playlist_name: `${series} · Season ${season}`, duration: 2600 });
const track = (number: number, title: string) => imported(title, 'track', { track_number: number, release_year: 2021 }, { uploader: 'Nova Lights', playlist_name: 'Tides of Glass', duration: 180 + number * 11 });

function fixtures(): Fixture[] {
  counter = 0;
  return [
    fixture('Building a cedar canoe by hand, start to finish', { user_id: user.id }),
    imported('Arrival Light', 'movie', { release_year: 2016 }),
    imported('The Long Northern Summer of the Lighthouse Keeper and Her Remarkably Patient Dog', 'movie', { release_year: 1999 }, { media_state: 'offline' }),
    fixture('Live: late-night synth jam', { kind: 'recording', extractor: 'twitch', uploader: 'synthcellar' }),
    episode('Blue Harbor', 1, 1, 'Low Tide'), episode('Blue Harbor', 1, 2, 'The Ferry'), episode('Blue Harbor', 1, 3, 'Fog Line'), episode('Blue Harbor', 2, 1, 'Return'),
    episode('Arcadia', 1, 1, 'Pilot'),
    track(1, 'Glasswater'), track(2, 'Northbound'), track(3, 'A Quiet Engine'), track(4, 'Tidal Lock'),
    fixture('Rainy café piano session', { kind: 'audio', uploader: 'Nova Lights', playlist_name: null }),
    imported('clip_0042', 'video', { lumina_import_kind: 'unclassified' }),
  ];
}

const VIEW_KINDS: Record<string, string[]> = { video: ['video'], movie: ['movie'], episode: ['episode'], music: ['audio', 'track'], recording: ['recording'] };

function poster(id: string): string {
  const hue = [...id].reduce((sum, char) => sum + char.charCodeAt(0) * 37, 0) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="480"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 38% 34%)"/><stop offset="1" stop-color="hsl(${(hue + 50) % 360} 42% 16%)"/></linearGradient></defs><rect width="320" height="480" fill="url(#g)"/><circle cx="230" cy="130" r="60" fill="hsl(${hue} 60% 70% / .25)"/></svg>`;
}

async function mockLibrary(page: Page) {
  const items = fixtures();
  const calls: string[] = [];
  await page.route((url) => url.pathname.startsWith('/api/library'), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    const q = url.searchParams;
    if (path.endsWith('/artwork')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: poster(path) });
    const visible = (entry: Fixture) => (!q.get('kind') || VIEW_KINDS[q.get('kind') as string].includes(entry.kind))
      && (!q.get('source') || (q.get('source') === 'imported' ? entry.extractor === 'external_library' : q.get('source') === 'saved' ? entry.extractor !== 'external_library' : entry.extractor === q.get('source')));
    const action = /^\/api\/library\/([^/]+)\/(delete-file|restore-file)$/.exec(path);
    if (action) {
      const entry = items.find((candidate) => candidate.id === action[1]) as Fixture;
      Object.assign(entry, action[2] === 'delete-file' ? { status: 'missing', media_state: 'quarantined' } : { status: 'available', media_state: 'available' });
      calls.push(`${action[2]}:${entry.id}`);
      return json(entry);
    }
    if (path === '/api/library') {
      calls.push(url.search);
      let page = items.filter((entry) => visible(entry) && (q.get('status') ? entry.status === q.get('status') : true) && (q.get('group') === null || entry.uploader === q.get('group')));
      if (q.get('sort') === 'title') page = [...page].sort((a, b) => a.title.localeCompare(b.title));
      return json({ items: page, next_cursor: null });
    }
    return route.fallback();
  });
  return calls;
}

const tabs = (page: Page) => page.getByRole('navigation', { name: 'Library' });

async function openLibrary(page: Page, path: string, viewport: { width: number; height: number }, scheme: 'light' | 'dark') {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  const calls = await mockLibrary(page);
  await mockTitles(page);
  await answerCategoriesByType(page);
  await page.goto(path);
  await signIn(page);
  await expect(page.locator('.surface.gallery h1').first()).toBeVisible();
  return calls;
}

test('test_library_views_delete_and_undo', async ({ page }) => {
  const calls = await openLibrary(page, '/library/youtube', { width: 1536, height: 960 }, 'dark');
  const canoe = page.getByRole('button', { name: /^Building a cedar canoe by hand, start to finish, / });
  await expect(canoe).toBeVisible();
  // Imported: read-only, but every every StillCard a menu for Add to queue.
  await page.getByRole('button', { name: /^More options for clip_0042/ }).click();
  await expect(page.getByRole('menuitem', { name: 'Add to queue' })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'Delete from Lumina' })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'More options for Building a cedar canoe by hand, start to finish' }).click();
  await page.getByRole('menuitem', { name: 'Delete from Lumina' }).click();
  await expect(page.locator('.g-toast').filter({ hasText: 'Deleted' })).toBeVisible();
  await expect(canoe).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(canoe).toBeVisible();
  expect(calls).toContain('delete-file:lib-1');
  expect(calls).toContain('restore-file:lib-1');
});

test('test_library_visual', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await openLibrary(page, '/library', { width: 1536, height: 960 }, 'dark');
  await expect(page.getByRole('heading', { level: 1, name: 'Library' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Low Tide/ })).toHaveCount(0); // All lists titles by chapter, never single episodes
  await tabs(page).getByRole('link', { name: 'Movies' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Movies' })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Northern Lantern,/ })).toBeVisible();
  await expect(page).toHaveURL(/\/library\/movies$/);
  await tabs(page).getByRole('link', { name: 'Shows' }).click();
  await page.getByRole('button', { name: /^Harbor Lights,/ }).click();
  await expect(page.getByRole('tab', { name: 'Season 2' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/shows$/);
  await tabs(page).getByRole('link', { name: 'YouTube' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'YouTube' })).toBeVisible();
  expect(errors).toEqual([]);
});

/** A 200-title Movies wall over the mocked title endpoints; each poster opens Northern Lantern's page. */
async function openGallery(page: Page, path: string, viewport = { width: 1536, height: 960 }) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockLibrary(page);
  await mockTitles(page);
  const movies = Array.from({ length: 200 }, (_, index) => ({ ...movieTitle, id: `movie-g${index}`, name: `Gallery ${String(index).padStart(3, '0')}`, poster_url: null, poster: null, backdrop: null }));
  await page.route((url) => url.pathname === '/api/titles' || url.pathname === '/api/titles/facets' || /^\/api\/titles\/movie-g\d+$/.test(url.pathname), async (route) => {
    const url = new URL(route.request().url());
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/titles/facets') return json({ genres: [{ name: 'Drama', count: 200 }], years: { min: 2019, max: 2019 }, resolutions: [] });
    if (url.pathname === '/api/titles') {
      if (url.searchParams.get('type') !== 'movie' && url.searchParams.get('category') !== 'movies') return route.fallback();
      return json({ items: url.searchParams.get('limit') === '1' ? movies.slice(0, 1) : movies, next_cursor: null, total: 200, start_index: 0, letters: [{ letter: 'G', index: 0 }] });
    }
    return route.fallback({ url: `${url.origin}/api/titles/movie-1` });
  });
  await page.goto(path);
  await signIn(page);
}

test('the wall keeps its sort and filters in the address across a reload', async ({ page }) => {
  await openGallery(page, '/library/movies');
  await expect(page.getByRole('button', { name: /^Gallery 000,/ })).toBeVisible();
  await page.getByRole('combobox', { name: 'Sort' }).focus();
  await page.getByRole('combobox', { name: 'Sort' }).selectOption('name');
  await expect(page.getByRole('combobox', { name: 'Sort' })).toBeFocused();
  await page.getByRole('button', { name: 'Unwatched', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unwatched', exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Filters' }).click();
  await page.getByRole('checkbox', { name: /^Drama/ }).check();
  await page.getByRole('dialog').getByRole('button', { name: /^Show/ }).click();
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeFocused();
  await expect(page).toHaveURL(/\/library\/movies\?sort=name&unwatched=1&genre=Drama$/);
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Sort' })).toHaveValue('name');
  await expect(page.getByRole('button', { name: 'Unwatched', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Filters (1)' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/library\/movies\?sort=name&unwatched=1$/);
  await expect(page.getByRole('button', { name: 'Filters' })).toBeVisible();
});

test('Back from a title page puts the wall back: filters, sort, scroll and the focused poster', async ({ page }) => {
  await openGallery(page, '/library/movies?sort=name&unwatched=1');
  const poster = page.getByRole('button', { name: /^Gallery 060,/ });
  await page.getByRole('button', { name: /^Gallery 000,/ }).focus();
  for (let presses = 0; presses < 30 && !await poster.isVisible(); presses += 1) await page.keyboard.press('ArrowDown');
  await poster.focus();
  const scrolled = await page.evaluate(() => window.scrollY);
  expect(scrolled).toBeGreaterThan(500);
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
  for (const back of ['browser', 'page'] as const) {
    if (back === 'browser') await page.goBack();
    else await page.locator('.t-back').click();
    await expect(page).toHaveURL(/\/library\/movies\?sort=name&unwatched=1$/);
    await expect(page.getByRole('button', { name: /^Gallery 060,/ })).toBeFocused();
    expect(Math.abs(await page.evaluate(() => window.scrollY) - scrolled)).toBeLessThanOrEqual(2);
    if (back === 'browser') {
      await page.keyboard.press('Enter');
      await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
    }
  }
});

for (const viewport of [{ width: 375, height: 667 }, { width: 390, height: 844 }]) {
  test(`on a ${viewport.width}×${viewport.height} phone the A–Z rail sits beside the posters, above the tab bar, and draws the letters its drag maps`, async ({ page }) => {
    await openGallery(page, '/library/movies?sort=name', viewport);
    const rail = page.getByRole('navigation', { name: 'Jump to letter' });
    await expect(rail).toBeVisible();
    const railBox = await rail.boundingBox();
    const rightmost = await page.locator('.g-cell').evaluateAll((cells) => Math.max(...cells.map((cell) => cell.getBoundingClientRect().right)));
    expect(railBox?.width).toBeGreaterThanOrEqual(44);
    expect(rightmost).toBeLessThanOrEqual(railBox?.x ?? 0);
    expect((await rail.getByRole('button', { name: 'G' }).boundingBox())?.width).toBeGreaterThanOrEqual(44);
    const tabsTop = await page.locator('.g-mobile-tabs').evaluate((tabs) => tabs.getBoundingClientRect().top);
    const last = await rail.getByRole('button', { name: 'Z' }).boundingBox();
    expect((last?.y ?? 0) + (last?.height ?? 0)).toBeLessThanOrEqual(tabsTop);
    // GalleryWall's drag maps a point to slot floor((y - top) / height * 27): the letter drawn there must be that slot's.
    const mismatches = await rail.evaluate((nav) => {
      const letters = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'];
      const box = nav.getBoundingClientRect();
      const wrong: string[] = [];
      for (let y = box.top + 1; y < box.bottom - 1; y += 3) {
        const position = ((y - box.top) / box.height) * letters.length;
        const slot = Math.min(letters.length - 1, Math.floor(position));
        if ((position - slot) * (box.height / letters.length) < 1.5 || (slot + 1 - position) * (box.height / letters.length) < 1.5) continue; // sub-pixel snapping at a boundary (hit-testing snaps a fractional button edge by up to a pixel)
        const drawn = document.elementFromPoint(box.right - 10, y)?.closest('button')?.textContent;
        if (drawn !== letters[slot]) wrong.push(`${Math.round(y)}: ${drawn} vs ${letters[slot]}`);
      }
      return wrong;
    });
    expect(mismatches).toEqual([]);
  });
}

test('a phone too short for 27 letters hides the rail (scrolling and Find reach the same titles)', async ({ page }) => {
  await openGallery(page, '/library/movies?sort=name', { width: 375, height: 560 });
  await expect(page.getByRole('button', { name: /^Gallery 000,/ })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Jump to letter' })).toBeHidden();
});

test('a member who signs in after another sees none of their wall: no filters and fresh pages', async ({ page }) => {
  let logins = 0;
  await page.route((url) => url.pathname === '/api/session/login' || url.pathname === '/api/session/me', async (route) => {
    const request = route.request();
    const login = new URL(request.url()).pathname === '/api/session/login';
    if (login && request.method() === 'POST') logins += 1;
    else if (request.method() !== 'GET' || logins === 0) return route.fallback();
    const member = logins > 1 ? { ...user, id: 'member-2', username: 'bea', display_name: 'Bea', role: 'member' } : user;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ user: member }) });
  });
  const lists: string[] = [];
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/titles' && new URL(request.url()).searchParams.get('limit') === '60') lists.push(request.url()); });
  await openGallery(page, '/library/movies');
  await expect(page.getByRole('button', { name: /^Gallery 000,/ })).toBeVisible();
  await page.getByRole('button', { name: 'Unwatched', exact: true }).click();
  await expect(page).toHaveURL(/\/library\/movies\?unwatched=1$/);
  await page.getByRole('button', { name: /^Gallery 000,/ }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
  await page.getByRole('button', { name: /, account menu$/ }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click(); // Sign out lives in the account menu
  await expect(page.locator('.g-auth')).toBeVisible();
  const before = lists.length;
  await signIn(page);
  // From Home straight to a title page: its Back must not return to the other member's filtered wall.
  // The new Home's poster card names itself "{title}, {year}, {status}" (PosterCard), not "Open {title}".
  await page.getByRole('button', { name: /^Northern Lantern, / }).first().click();
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeVisible();
  await page.locator('.t-back').click();
  await expect(page).toHaveURL(/\/library\/movies$/);
  await expect(page.getByRole('button', { name: /^Gallery 000,/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Unwatched', exact: true })).toHaveAttribute('aria-pressed', 'false');
  expect(lists.length).toBeGreaterThan(before);
});
