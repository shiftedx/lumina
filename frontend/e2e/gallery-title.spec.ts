import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import { episodeSummary, FIXTURE_AT, keyScenes, movieSummary, seriesSummary, titleDetail, userData } from '../src/test/galleryFixtures';
import type { TitleSummary } from '../src/types';
import { type GalleryTitleOptions, mockGalleryTitle } from './gallery-title-mock';
import { mockApi, mockTitles, signIn } from './lumina-mock';

/** Gallery title page over the mocked API. */

const season = (n: number): TitleSummary => movieSummary(`season-${n}`, { type: 'season', name: n ? `Season ${n}` : 'Specials', index_number: n, parent_id: 'series-1' });
const episode = (s: number, e: number, patch: Partial<TitleSummary> = {}) => episodeSummary(s, e, { play_item_id: `item-ep-${s}-${e}`, ...patch });
const seasonTwo = [
  episode(2, 1, { user_data: userData({ played: true }) }), episode(2, 2, { user_data: userData({ position_seconds: 960, duration_seconds: 2640 }) }), ...[3, 4, 5, 6].map((e) => episode(2, e)),
];
const showOptions: GalleryTitleOptions = {
  detail: titleDetail(seriesSummary('series-1', { overview: 'Three keepers, one lighthouse, and a town that depends on both.', user_data: userData({ last_watched_at: FIXTURE_AT, unplayed_count: 5 }) }), {
    children: [season(1), season(2), season(0)], play_next: seasonTwo[1],
    people: [{ name: 'Ines Varga', type: 'Creator' }, { name: 'Mara Quill', role: 'Keeper', type: 'Actor' }],
  }),
  episodes: { 1: [1, 2, 3].map((e) => episode(1, e, { user_data: userData({ played: true }) })), 2: seasonTwo, 0: [episode(0, 1)] },
  summaries: { available: true, items: [{ episode_id: 'ep-2-1', overview: 'Mara finds the logbook under the stairs.' }, { episode_id: 'ep-2-2', overview: 'Spoiler: the ferry never left.' }] },
  recap: { episode_title_id: 'ep-2-2', state: 'fallback', points: [], fallback: [{ episode_id: 'ep-2-1', name: 'Episode 1', season_number: 2, index_number: 1, overview: 'The keepers argue about the lamp.' }], suggest_preroll: false },
};
const movieOptions: GalleryTitleOptions = {
  detail: titleDetail(movieSummary('movie-1', { overview: 'A girl and her grandfather carry a lamp across the ice.' }), {
    people: [{ name: 'Ines Varga', type: 'Director' }, { name: 'Mara Quill', role: 'Keeper', type: 'Actor' }],
  }),
  // lumina-mock knows the movie's versions (item-movie-4k), not the fixture's item-movie-1, so the player stays open.
  keyScenes: { ...keyScenes, item_id: 'item-movie-4k' },
  similar: [movieSummary('movie-2', { name: 'Southern Lantern' })],
};

async function open(page: Page, path: string, options: GalleryTitleOptions, viewport = { width: 1440, height: 900 }) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await mockTitles(page);
  const calls = await mockGalleryTitle(page, options);
  await page.goto(path);
  await signIn(page);
  return calls;
}

const box = async (page: Page, selector: string) => {
  const found = await page.locator(selector).first().boundingBox();
  if (!found) throw new Error(`${selector} is not visible`);
  return found;
};

test('show: meta line, the story so far, the row starting at the next episode, a watched summary but never a spoiler, and resume', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const calls = await open(page, '/title/series-1', showOptions);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeFocused();
  await expect(page.locator('.t-meta')).toHaveText('2021 · 2 seasons · Drama · Continue S2 · E2, 28 min left');
  await expect(page.getByRole('region', { name: 'The story so far' })).toContainText('From the episode guide');
  await expect(page.getByRole('tab', { name: 'Season 2' })).toHaveAttribute('aria-selected', 'true');
  const row = page.locator('.t-episodes');
  await expect(row.getByRole('button', { name: 'Play 1. Episode 1, S2 · E1, watched', exact: true })).toContainText('Mara finds the logbook under the stairs.');
  await expect(row.getByRole('button', { name: 'Play 2. Episode 2, S2 · E2, in progress, 28 minutes left', exact: true })).toContainText('28 min left');
  await expect(row).not.toContainText('Spoiler');
  expect(await row.evaluate((list) => list.scrollLeft)).toBeGreaterThan(0);
  expect(calls).toContain('GET /api/titles/series-1/episode-summaries?season=2');
  await page.getByRole('button', { name: 'Resume S2 · E2', exact: true }).click();
  await expect(page).toHaveURL(/\/watch\/library\/item-ep-2-2$/);
  expect(errors).toEqual([]);
});

test('movie: a key scene opens the player at its moment', async ({ page }) => {
  await open(page, '/title/movie-1', movieOptions);
  await expect(page.getByRole('region', { name: 'Key scenes' })).toContainText('“Carry it until the ice sings.”');
  // lumina-mock's clip is 1.5 s long, and a start that close to the end restarts at 0; the movie is 1h 52m.
  await page.route('**/api/library/item-movie-4k/playback-options*', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({ mode: 'direct', reason: null, facts: { container: 'mp4', video_codec: 'h264', audio_codec: 'aac', width: 160, height: 90, duration: 6720 }, audio_tracks: [], quality_heights: [], loudness_gain_db: null, free_video_slots: 2 }),
  }));
  await page.getByRole('button', { name: 'Play from 12:41' }).click();
  // The route opens with t=761 and the player starts there; once applied the time leaves the address, so a
  // reload resumes from the saved position instead of the quote.
  await expect.poll(() => page.locator('video').first().getAttribute('src')).toMatch(/\/api\/library\/item-movie-4k\/media#t=761$/);
  await expect(page).toHaveURL(/\/watch\/library\/item-movie-4k$/);
});

test('AI failing: no AI section, no error and no page error, and the teasers still show', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  // Every AI request has failed before absence is asserted, so a section that would have shown cannot be missed.
  const failed = ['/recap', '/episode-summaries', '/key-scenes'].map((part) => page.waitForResponse((response) => response.status() === 500 && new URL(response.url()).pathname.endsWith(part)));
  await open(page, '/title/series-1', { ...showOptions, recap: 500, summaries: 500, keyScenes: 500 });
  await expect(page.locator('.t-episodes').getByRole('button', { name: 'Play 3. Episode 3, S2 · E3, unwatched', exact: true })).toContainText('The keepers argue about the lamp.');
  await Promise.all(failed);
  await expect(page.getByRole('region', { name: 'The story so far' })).toHaveCount(0);
  await expect(page.getByRole('region', { name: /Key scenes/ })).toHaveCount(0);
  await expect(page.locator('.t-page [role=alert]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('deep link: a busy paper field with no text, then the page', async ({ page }) => {
  await open(page, '/title/movie-1', { ...movieOptions, detailDelayMs: 1500 });
  await expect(page.locator('.t-page[aria-busy="true"] .t-hero.is-empty')).toBeVisible();
  await expect(page.locator('.t-page h1')).toHaveCount(0);
  await expect(page.getByRole('heading', { level: 1, name: 'Northern Lantern' })).toBeFocused();
});

test('desktop: the hero, the body pulled over it, the side column to the right, and 320px episode cards', async ({ page }) => {
  await open(page, '/title/series-1', showOptions);
  await expect(page.locator('.t-episodes > li').first()).toBeVisible();
  const hero = await box(page, '.t-hero');
  const main = await box(page, '.t-main');
  const side = await box(page, '.t-side');
  expect(hero.height).toBeCloseTo(630, 0); // min(70vh, 680px) at 900px tall
  expect(main.y).toBeCloseTo(hero.y + hero.height - 120, 0);
  expect(side.x).toBeGreaterThanOrEqual(main.x + main.width + 47);
  expect(side.y).toBeCloseTo(main.y + 74, 0);
  expect((await box(page, '.t-episodes > li')).width).toBe(320);
});

test('desktop over the gallery styles: the art fills the hero, More like this posters are 168px, and Back reads on the art', async ({ page }) => {
  await open(page, '/title/movie-1', movieOptions);
  const poster = page.getByRole('region', { name: 'More like this' }).locator('.t-row-poster');
  await expect(poster).toBeVisible();
  const hero = await box(page, '.t-hero');
  const art = await box(page, '.t-hero-art');
  expect([art.x, art.y, art.width, art.height]).toEqual([hero.x, hero.y, hero.width, hero.height]);
  expect((await poster.boundingBox())?.width).toBe(168);
  const back = page.getByRole('button', { name: 'Back to Movies' });
  expect(await back.evaluate((element) => {
    const probe = document.createElement('span');
    probe.style.color = 'var(--g-on-art)';
    element.closest('.gallery')?.append(probe);
    const onArt = getComputedStyle(probe).color;
    probe.remove();
    return getComputedStyle(element).color === onArt;
  })).toBe(true);
});

for (const width of [390, 320]) {
  test(`phone ${width}: one column with the story after the actions and the side column after it, sideways rows, no sideways page, 44px targets`, async ({ page }) => {
    await open(page, '/title/series-1', showOptions, { width, height: 844 });
    await expect(page.locator('.t-episodes > li').first()).toBeVisible();
    await expect(page.getByRole('region', { name: 'The story so far' })).toBeVisible();
    const main = await box(page, '.t-main');
    const actions = await box(page, '.t-actions');
    const side = await box(page, '.t-side');
    const story = await box(page, '.t-story');
    expect((await box(page, '.t-hero')).height).toBeCloseTo(width * 0.75, 0);
    expect(side.x).toBeCloseTo(main.x, 0);
    // The story so far sits in the main column directly after the actions.
    expect(story.y).toBeGreaterThanOrEqual(actions.y + actions.height);
    expect(side.y).toBeGreaterThan(story.y);
    expect((await box(page, '.t-episodes > li')).width).toBeCloseTo(Math.min(width * 0.72, 300), 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    const small = await page.locator('.t-page').locator('button:visible, input[type=radio]:visible').evaluateAll((elements) => elements
      .map((element) => ({ name: element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName, box: (element.matches('input') ? element.closest('label') ?? element : element).getBoundingClientRect() }))
      .filter(({ box: rect }) => rect.width < 43.5 || rect.height < 43.5)
      .map(({ name, box: rect }) => `${name} (${Math.round(rect.width)}×${Math.round(rect.height)})`));
    expect(small).toEqual([]);
  });
}

test('keyboard only: the headline takes focus, arrows walk the actions, tabs and episodes, and Escape goes back', async ({ page }) => {
  await open(page, '/title/series-1', showOptions);
  await expect(page.getByRole('heading', { level: 1, name: 'Harbor Lights' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('button', { name: 'Resume S2 · E2', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('button', { name: 'Mark watched' })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  // The mocked member is an admin: Fix match can wrap the actions onto a second line, which Down visits first.
  if (await page.locator('.t-actions :focus').count()) await page.keyboard.press('ArrowDown');
  await expect(page.locator('[role=tab]:focus')).toHaveCount(1);
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('.t-episode:focus')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/\/library\/shows$/);
});

for (const scheme of ['light', 'dark'] as const) {
  for (const [name, path, options] of [['movie', '/title/movie-1', movieOptions], ['show', '/title/series-1', showOptions]] as const) {
    test(`${scheme} ${name}: no serious accessibility violations`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, path, options);
      await expect(page.locator(name === 'movie' ? '.t-scenes' : '.t-story')).toBeVisible();
      const results = await new AxeBuilder({ page }).include('.t-page').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
      expect(results.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
        .map((violation) => `${violation.id}: ${violation.nodes.slice(0, 3).map((node) => node.target.join(' ')).join(', ')}`)).toEqual([]);
    });
  }
}

test('motion: the page fades in, and nothing moves under reduced motion', async ({ page }) => {
  await open(page, '/title/movie-1', movieOptions);
  await expect(page.locator('.t-scenes')).toBeVisible();
  const moving = () => page.locator('.t-page, .t-page *').evaluateAll((elements) => elements.filter((element) => {
    const style = getComputedStyle(element);
    return style.animationName !== 'none' || style.transitionDuration.split(',').some((duration) => parseFloat(duration) > 0);
  }).length);
  expect(await moving()).toBe(0);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  expect(await page.locator('.t-page').evaluate((element) => getComputedStyle(element).animationName)).toBe('t-page-in');
});
