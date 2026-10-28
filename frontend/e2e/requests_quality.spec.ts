import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test, type Locator, type Page } from '@playwright/test';

import {
  anime,
  animeDetail,
  animeFilm,
  catalogHome,
  movie,
  quotas,
  request,
  show,
  showDetail,
  unmapped,
} from '../src/features/requests/requestsFixtures';
import type { CatalogDetail, CatalogItem, MediaRequest } from '../src/features/requests/requestsApi';
import { mockApi, signIn } from './lumina-mock';

/**
 * Synthetic evidence for every Requests route plus the three decision dialogs.
 * Screenshots are review artifacts, not pixel baselines.
 */
const OUT = process.env.REQUESTS_QUALITY_OUT ?? join(process.cwd(), '..', 'output', 'app-quality', 'requests');
const VIEWPORTS = [{ name: 'desktop', width: 1440, height: 900 }, { name: 'phone', width: 390, height: 844 }] as const;
const SCHEMES = ['light', 'dark'] as const;
const STATES = [
  'discover', 'movies', 'shows', 'anime', 'search', 'title', 'mine', 'manage',
  'request-dialog', 'approve-dialog', 'decline-dialog', 'mine-empty', 'discover-failure',
] as const;

const art = (label: string, wide = false) => {
  const [width, height] = wide ? [1280, 720] : [600, 900];
  const safe = label.replace(/[<>&"']/g, '');
  return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#172554"/><stop offset=".52" stop-color="#7c3aed"/><stop offset="1" stop-color="#ec4899"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="${width * .78}" cy="${height * .2}" r="${Math.min(width, height) * .16}" fill="#fef3c7" opacity=".7"/><text x="8%" y="86%" fill="white" font-family="system-ui" font-size="${wide ? 66 : 42}" font-weight="700">${safe}</text></svg>`)}`;
};

const decorate = <T extends CatalogItem>(item: T): T => ({
  ...item,
  poster_url: art(item.title),
  backdrop_url: art(item.title, true),
  logo_url: undefined,
});

const MOVIE = decorate(movie);
const SHOW = decorate(show);
const ANIME = decorate({ ...anime, anime: { ...anime.anime!, popularity: 98_000, color: '#7c3aed' } });
const ANIME_FILM = decorate(animeFilm);
const UNMAPPED = decorate(unmapped);
const ITEMS = new Map([MOVIE, SHOW, ANIME, ANIME_FILM, UNMAPPED].map((item) => [item.key, item]));
const HOME = {
  hero: [MOVIE, SHOW],
  rails: catalogHome.rails.map((rail) => ({ ...rail, items: rail.items.map((item) => ITEMS.get(item.key) ?? decorate(item)) })),
};
const SHOW_DETAIL: CatalogDetail = {
  ...showDetail,
  poster_url: SHOW.poster_url,
  backdrop_url: SHOW.backdrop_url,
  logo_url: undefined,
  cast: showDetail.cast.map((person) => ({ ...person, profile_url: art(person.name) })),
  recommendations: [MOVIE],
};
const ANIME_DETAIL: CatalogDetail = {
  ...animeDetail,
  poster_url: ANIME.poster_url,
  backdrop_url: ANIME.backdrop_url,
  logo_url: undefined,
  anime: ANIME.anime,
  anime_relations: [ANIME_FILM],
};
const MOVIE_DETAIL: CatalogDetail = {
  ...MOVIE,
  runtime: 136,
  certification: 'R',
  tagline: 'Free your mind.',
  seasons: [],
  trailers: [{ youtube_id: 'synthetic01', name: 'Official trailer', type: 'Trailer', official: true }],
  cast: [{ name: 'Synthetic Performer', character: 'The One', profile_url: art('Synthetic Performer') }],
  crew: [{ name: 'Synthetic Director', job: 'Director' }],
  networks: [],
  studios: ['Synthetic Pictures'],
  recommendations: [ANIME_FILM],
  similar: [],
};

const member = { id: 'member-1', name: 'Alexandria' };
const minePending = request({ id: 'rq-mine-pending', requested_by: member, poster_url: SHOW.poster_url });
const mineProcessing = request({
  id: 'rq-mine-processing',
  key: 'movie:603',
  kind: 'movie',
  media_type: 'movie',
  tmdb_id: 603,
  title: 'The Matrix',
  year: 1999,
  poster_url: MOVIE.poster_url,
  seasons: null,
  status: 'processing',
  progress: .62,
  requested_by: member,
});
const managePending = request({ id: 'rq-manage-pending', poster_url: SHOW.poster_url });
const manageApproved = request({ id: 'rq-manage-approved', status: 'approved', poster_url: SHOW.poster_url });
const manageFailed = request({
  id: 'rq-manage-failed',
  key: 'movie:603',
  kind: 'movie',
  media_type: 'movie',
  tmdb_id: 603,
  title: 'The Matrix',
  year: 1999,
  poster_url: MOVIE.poster_url,
  seasons: null,
  status: 'failed',
  failure_reason: 'The download service could not reach the provider.',
});

type FixtureState = { discoverFailure: boolean; mineEmpty: boolean };

async function mockRequests(page: Page, state: FixtureState) {
  await page.route(/\/api\/requests(?:\/|\?|$)/, async (route) => {
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const pageNumber = Number(requestUrl.searchParams.get('page') ?? '1');

    if (method === 'GET' && path === '/api/requests/catalog/home') {
      return state.discoverFailure ? json({ detail: 'requests_disabled' }, 503) : json(HOME);
    }
    if (method === 'GET' && path.startsWith('/api/requests/catalog/genres/')) {
      return json({ genres: [{ id: '28', name: 'Action' }, { id: '18', name: 'Drama' }, { id: '878', name: 'Science fiction' }] });
    }
    if (method === 'GET' && path.startsWith('/api/requests/catalog/list/')) {
      const kind = path.split('/').at(-1);
      const items = kind === 'movie' ? [MOVIE, ANIME_FILM] : kind === 'show' ? [SHOW] : [ANIME, ANIME_FILM];
      return json({ items: pageNumber === 1 ? items : [], page: pageNumber, total_pages: 1 });
    }
    if (method === 'GET' && path === '/api/requests/catalog/anime/season') {
      return json({ season: 'FALL', year: 2026, items: pageNumber === 1 ? [ANIME, ANIME_FILM] : [] });
    }
    if (method === 'GET' && path === '/api/requests/catalog/anime/schedule') {
      return json({
        days: Array.from({ length: 7 }, (_, offset) => ({
          date: `2026-10-${String(11 + offset).padStart(2, '0')}`,
          entries: offset < 2 ? [{ item: ANIME, episode: 4 + offset, airing_at: `2026-10-${String(11 + offset).padStart(2, '0')}T19:00:00Z` }] : [],
        })),
      });
    }
    if (method === 'GET' && path === '/api/requests/catalog/search') {
      return json({ items: pageNumber === 1 ? [MOVIE, SHOW] : [], page: pageNumber, total_pages: 1 });
    }
    if (method === 'GET' && path.startsWith('/api/requests/catalog/title/')) {
      const [, kind, id] = path.match(/\/title\/(movie|show|anime)\/(\d+)$/) ?? [];
      const detail = kind === 'movie' && id === '603' ? MOVIE_DETAIL : kind === 'anime' ? ANIME_DETAIL : SHOW_DETAIL;
      return json(detail);
    }
    if (method === 'GET' && path === '/api/requests/quota') return json({ quotas });
    if (method === 'GET' && path === '/api/requests') {
      const scope = requestUrl.searchParams.get('scope');
      const status = requestUrl.searchParams.get('status');
      const items: MediaRequest[] = scope === 'mine'
        ? (state.mineEmpty ? [] : [minePending, mineProcessing])
        : status === 'pending' ? [managePending] : [manageApproved, manageFailed];
      return json({ items, page: 1, total_pages: 1, counts: { pending: 1, approved: 1, failed: 1 } });
    }
    if (method === 'POST' && path === '/api/requests') return json(minePending, 201);
    if (method === 'POST' && /\/api\/requests\/[^/]+\/approve$/.test(path)) return json({ ...managePending, status: 'approved' });
    if (method === 'POST' && /\/api\/requests\/[^/]+\/decline$/.test(path)) return json({ ...managePending, status: 'declined' });
    if (method === 'POST' && /\/api\/requests\/[^/]+\/retry$/.test(path)) return json({ ...manageFailed, status: 'processing' });
    if (method === 'DELETE' && /^\/api\/requests\/[^/]+$/.test(path)) return route.fulfill({ status: 204, body: '' });
    return json({ detail: `requests fixture: unhandled ${method} ${path}` }, 404);
  });
}

async function open(page: Page, path: string, ready: Locator) {
  await page.goto(path);
  await expect(ready).toBeVisible();
  await expect(page.locator('main[aria-label="Main content"]')).toBeVisible();
}

async function expectViewportFits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
}

async function expectPrimaryTarget(target: Locator, minimum = 36) {
  await expect(target).toBeVisible();
  const box = await target.boundingBox();
  expect(box, 'primary target has a layout box').not.toBeNull();
  expect(box!.width, `primary target is at least ${minimum} CSS pixels wide`).toBeGreaterThanOrEqual(minimum);
  expect(box!.height, `primary target is at least ${minimum} CSS pixels tall`).toBeGreaterThanOrEqual(minimum);
}

async function capture(page: Page, state: string, viewport: string, scheme: string) {
  await expectViewportFits(page);
  await page.waitForTimeout(150);
  await page.screenshot({ path: join(OUT, `${state}-${viewport}-${scheme}.png`) });
}

async function expectSelectedRequestTabVisible(page: Page) {
  const row = page.getByRole('navigation', { name: 'Requests pages' });
  const selected = row.locator('[aria-current="page"]');
  await expect(selected).toBeVisible();
  await expect.poll(async () => {
    const [rowBox, selectedBox] = await Promise.all([row.boundingBox(), selected.boundingBox()]);
    return Boolean(rowBox && selectedBox && selectedBox.x >= rowBox.x - 1 && selectedBox.x + selectedBox.width <= rowBox.x + rowBox.width + 1);
  }).toBe(true);
}

test.describe.configure({ mode: 'parallel' });

for (const scheme of SCHEMES) {
  for (const viewport of VIEWPORTS) {
    test(`Requests evidence: ${viewport.name} ${scheme}`, async ({ page }) => {
      test.setTimeout(240_000);
      page.setDefaultTimeout(15_000);
      mkdirSync(OUT, { recursive: true });
      await page.setViewportSize(viewport);
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: 'reduce' });
      const fixtureState: FixtureState = { discoverFailure: false, mineEmpty: false };
      await mockApi(page, { sidebar_collapsed: false });
      await mockRequests(page, fixtureState);

      await page.goto('/requests');
      await signIn(page);
      await expect(page.getByRole('region', { name: 'Featured' })).toBeVisible();
      await expectPrimaryTarget(page.getByRole('region', { name: 'Featured' }).getByRole('button', { name: 'Request', exact: true }));
      await capture(page, 'discover', viewport.name, scheme);

      await open(page, '/requests/movies', page.getByRole('button', { name: /The Matrix, 1999 .* Film/ }).first());
      await expect(page.getByRole('group', { name: 'Section' })).toBeVisible();
      await expectPrimaryTarget(page.getByRole('button', { name: /The Matrix, 1999 .* Film/ }).first());
      await capture(page, 'movies', viewport.name, scheme);

      await open(page, '/requests/shows', page.getByRole('button', { name: /Game of Thrones, 2011 .* Series/ }).first());
      await expect(page.getByRole('group', { name: 'Genre' })).toBeVisible();
      await expectPrimaryTarget(page.getByRole('button', { name: /Game of Thrones, 2011 .* Series/ }).first());
      await capture(page, 'shows', viewport.name, scheme);

      await open(page, '/requests/anime', page.getByRole('heading', { name: 'Airing this week' }));
      await expect(page.getByRole('group', { name: 'Anime section' })).toBeVisible();
      await expect(page.getByText('Attack on Titan', { exact: true }).first()).toBeVisible();
      await capture(page, 'anime', viewport.name, scheme);

      await open(page, '/requests/search?q=matrix', page.getByRole('heading', { level: 1, name: 'Results for “matrix”' }));
      await expectPrimaryTarget(page.getByRole('button', { name: /The Matrix, 1999 .* Film/ }).first());
      await capture(page, 'search', viewport.name, scheme);

      await open(page, '/requests/title/show/1399', page.getByRole('heading', { level: 1, name: 'Game of Thrones' }));
      await expectPrimaryTarget(page.getByRole('button', { name: 'Request', exact: true }));
      await expectPrimaryTarget(page.getByRole('button', { name: 'Trailer', exact: true }));
      await capture(page, 'title', viewport.name, scheme);

      await open(page, '/requests/mine', page.getByRole('button', { name: 'Cancel request' }));
      await expect(page.getByRole('list', { name: 'Progress' }).first()).toBeVisible();
      await expectPrimaryTarget(page.getByRole('button', { name: 'Cancel request' }));
      await capture(page, 'mine', viewport.name, scheme);

      await open(page, '/requests/manage', page.getByRole('heading', { name: /Waiting for you/ }));
      if (viewport.name === 'phone') {
        await expectSelectedRequestTabVisible(page);
        const tabs = page.getByRole('navigation', { name: 'Requests pages' });
        const left = page.getByRole('button', { name: 'Scroll Requests pages tabs left' });
        const right = page.getByRole('button', { name: 'Scroll Requests pages tabs right' });
        await expect(left).toBeEnabled();
        await left.click();
        await expect.poll(() => tabs.evaluate((element) => element.scrollLeft)).toBe(0);
        await expect(left).toBeDisabled();
        await expect(right).toBeEnabled();
        await right.click();
        await expect(right).toBeDisabled();
        await expectSelectedRequestTabVisible(page);
      }
      await expectPrimaryTarget(page.getByRole('button', { name: 'Approve with changes' }));
      await expectPrimaryTarget(page.getByRole('button', { name: 'Decline', exact: true }));
      if (viewport.name === 'phone') {
        const status = page.getByRole('combobox', { name: 'Status' });
        await expect(status).toBeVisible();
        await status.click();
        await expect(page.getByRole('listbox').getByRole('option')).toHaveText(['All requests', 'Approved · 1', 'Downloading', 'Partly in', 'In your vault', 'Declined', 'Needs attention · 1']);
        await page.keyboard.press('Escape');
      } else {
        await expect(page.getByRole('group', { name: 'Status' })).toBeVisible();
      }
      await capture(page, 'manage', viewport.name, scheme);

      await page.goto('/requests/title/show/1399');
      await page.getByRole('button', { name: 'Request', exact: true }).click();
      const requestDialog = page.getByRole('dialog', { name: 'Request Game of Thrones' });
      await expect(requestDialog.getByRole('group', { name: 'Seasons' })).toBeVisible();
      await expectPrimaryTarget(requestDialog.getByRole('button', { name: 'Request', exact: true }));
      await capture(page, 'request-dialog', viewport.name, scheme);

      await page.goto('/requests/manage');
      await page.getByRole('button', { name: 'Approve with changes' }).click();
      const approveDialog = page.getByRole('dialog', { name: 'Approve Game of Thrones' });
      await expect(approveDialog.getByRole('group', { name: 'Seasons' })).toBeVisible();
      await expectPrimaryTarget(approveDialog.getByRole('button', { name: 'Approve', exact: true }));
      await capture(page, 'approve-dialog', viewport.name, scheme);

      await page.goto('/requests/manage');
      await page.getByRole('button', { name: 'Decline', exact: true }).click();
      const declineDialog = page.getByRole('dialog', { name: 'Decline Game of Thrones?' });
      await expect(declineDialog.getByLabel('Reason (optional)')).toBeVisible();
      await expectPrimaryTarget(declineDialog.getByRole('button', { name: 'Decline', exact: true }));
      await capture(page, 'decline-dialog', viewport.name, scheme);

      fixtureState.mineEmpty = true;
      await open(page, '/requests/mine', page.getByRole('heading', { name: 'No requests yet' }));
      await capture(page, 'mine-empty', viewport.name, scheme);
      fixtureState.mineEmpty = false;

      fixtureState.discoverFailure = true;
      await open(page, '/requests', page.getByRole('heading', { name: 'Requests are resting' }));
      await expectPrimaryTarget(page.getByRole('button', { name: 'Open Settings → Requests' }));
      await capture(page, 'discover-failure', viewport.name, scheme);
    });
  }
}

test.afterAll(() => {
  mkdirSync(OUT, { recursive: true });
  const captures = SCHEMES.flatMap((scheme) => VIEWPORTS.flatMap((viewport) => STATES.map((state) => ({
    state,
    viewport: { name: viewport.name, width: viewport.width, height: viewport.height },
    scheme,
    file: `${state}-${viewport.name}-${scheme}.png`,
  }))));
  writeFileSync(join(OUT, 'manifest.json'), `${JSON.stringify({ synthetic: true, source: 'frontend/e2e/requests_quality.spec.ts', captures }, null, 2)}\n`);
});
