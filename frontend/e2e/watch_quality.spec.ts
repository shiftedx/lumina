import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, mockTitles, signIn } from './lumina-mock';

const filename = 'Harbor.Lights.S02E01.Premiere.2160p.WEB-DL.H265.Multi.Audio.Release.Group.mkv';
const itemId = 'item-ep-2-1';
const OUT = process.env.WATCH_QUALITY_OUT;

async function openEpisode(page: Page, width: number, next: 'populated' | 'empty' | 'failure' = 'populated') {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, {});
  await mockTitles(page);
  await page.route(`**/api/library/${itemId}`, route => route.fulfill({ json: {
    ...item, id: itemId, title: filename, title_id: 'ep-2-1', kind: 'episode', extractor: 'external_library',
  } }));
  await page.route(`**/api/library/${itemId}/up-next`, async route => {
    if (next === 'failure') return route.fulfill({ status: 500, json: { detail: 'Unavailable' } });
    if (next === 'empty') return route.fulfill({ json: { kind: 'episodes', title: 'Harbor Lights', title_id: 'series-1', current_id: 'ep-2-1', items: [] } });
    return route.fallback();
  });
  await page.goto(`/watch/library/${itemId}`);
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Low Tide' })).toBeVisible();
}

for (const width of [390, 1440]) {
  test(`matched episode follows the player and filename stays in file details at ${width}px`, async ({ page }) => {
    await openEpisode(page, width);
    await expect(page.getByRole('button', { name: 'Harbor Lights', exact: true })).toBeVisible();
    await expect(page.getByText(/S2 · E1/, { exact: false }).first()).toBeVisible();
    const layout = page.locator('.watch-layout.is-library-title');
    await expect(layout).toBeVisible();
    await expect(page.locator('.watch-main > .player-frame, .watch-main > .watch-local-body, .watch-main > .watch-local-tools')).toHaveCount(3);
    expect(await page.locator('.watch-main > .player-frame, .watch-main > .watch-local-body, .watch-main > .watch-local-tools').evaluateAll((nodes) => nodes.map((node) => node.className))).toEqual([
      expect.stringContaining('player-frame'),
      expect.stringContaining('watch-local-body'),
      expect.stringContaining('watch-local-tools'),
    ]);
    const heading = await page.locator('.watch-local-title').boundingBox();
    const frame = await page.locator('.player-frame').boundingBox();
    const story = await page.locator('.watch-local-story').boundingBox();
    const continuation = await page.locator('.watch-local-continuation').boundingBox();
    const tools = await page.locator('.watch-local-tools').boundingBox();
    expect(heading!.y).toBeGreaterThanOrEqual(frame!.y + frame!.height);
    expect(story!.y).toBeGreaterThanOrEqual(frame!.y + frame!.height);
    expect(continuation!.y).toBeGreaterThanOrEqual(frame!.y + frame!.height);
    expect(tools!.y).toBeGreaterThanOrEqual(Math.max(story!.y + story!.height, continuation!.y + continuation!.height));
    if (width === 1440) {
      expect(continuation!.x).toBeGreaterThanOrEqual(story!.x + story!.width);
      expect(continuation!.y).toBeLessThan(story!.y + story!.height);
    } else {
      expect(continuation!.y).toBeGreaterThanOrEqual(story!.y + story!.height);
    }
    await expect(page.locator('.watch-local-meta')).toContainText('S2 · E1');
    await expect(page.locator('.watch-local-synopsis')).toContainText('The keepers argue about the lamp');
    await expect(page.locator('.watch-local-story .watch-byline')).toHaveCount(0);
    await expect(page.getByText(filename, { exact: true })).toBeHidden();
    await page.getByText('File details', { exact: true }).click();
    await expect(page.getByText(filename, { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Up next', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Notes', exact: true })).toBeVisible();
    expect(await page.locator('.watch-tools-tabs').evaluate(el => getComputedStyle(el).columnGap)).toBe('24px');
    if (width === 390) {
      const tablist = page.locator('.watch-tools-tabs');
      const tablistBox = await tablist.boundingBox();
      const tabBoxes = await tablist.getByRole('tab').evaluateAll(tabs => tabs.map(tab => {
        const box = tab.getBoundingClientRect();
        return { left: box.left, right: box.right };
      }));
      expect(await tablist.evaluate(el => el.scrollWidth)).toBeLessThanOrEqual(await tablist.evaluate(el => el.clientWidth));
      for (const box of tabBoxes) {
        expect(box.left).toBeGreaterThanOrEqual(tablistBox!.x - .5);
        expect(box.right).toBeLessThanOrEqual(tablistBox!.x + tablistBox!.width + .5);
      }
      await expect(page.getByRole('tab', { name: 'Sharing & tags', exact: true })).toBeInViewport();
    }
    expect(await page.locator('.watch-local-synopsis').evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(14);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test('an empty season keeps the full-width stage and explains where to find all episodes beneath it', async ({ page }) => {
  await openEpisode(page, 1440, 'empty');
  await expect(page.getByText('No next episode in your library.', { exact: false })).toBeVisible();
  const allEpisodes = page.getByRole('button', { name: 'See all episodes' });
  await expect(allEpisodes).toBeVisible();
  await page.getByRole('button', { name: 'Share', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(allEpisodes).toBeFocused();
  await expect(page.locator('.watch-rail')).toHaveCount(0);
  await expect(page.locator('.watch-local-continuation')).toContainText('No next episode in your library.');
  const layout = await page.locator('.watch-layout.is-library-title').boundingBox();
  const player = await page.locator('.player-frame').boundingBox();
  const continuation = await page.locator('.watch-local-continuation').boundingBox();
  expect(player!.width).toBeGreaterThan(layout!.width * .98);
  expect(continuation!.y).toBeGreaterThanOrEqual(player!.y + player!.height);
});

test('up next failure offers retry while preserving the playing media element', async ({ page }) => {
  await openEpisode(page, 1440, 'failure');
  await expect(page.getByText("Up next couldn't load.", { exact: true })).toBeVisible();
  const video = page.locator('video').first();
  await video.evaluate(media => { media.dataset.qualityIdentity = 'playing'; });
  await page.unroute(`**/api/library/${itemId}/up-next`);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route(`**/api/library/${itemId}/up-next`, async route => {
    requests++;
    await pending;
    await route.fallback();
  });
  const retry = page.getByRole('button', { name: 'Try again', exact: true });
  await page.getByRole('button', { name: 'Share', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(retry).toBeFocused();
  await retry.click();
  try {
    await expect(retry).toHaveAttribute('aria-busy', 'true');
    await expect(retry).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByLabel('Loading up next…')).toHaveCount(0);
    await retry.click({ force: true });
    expect(requests).toBe(1);
  } finally { release(); }
  await expect(page.getByRole('region', { name: 'Up next', exact: true })).toBeVisible();
  await expect(video).toHaveAttribute('data-quality-identity', 'playing');
});

test('theater keeps episode heading and actions below the stage without overlap', async ({ page }) => {
  await openEpisode(page, 1440);
  await page.locator('body').press('t');
  await expect(page.locator('.watch-layout.theater.is-library-title')).toBeVisible();
  const frame = await page.locator('.player-frame').boundingBox();
  const body = await page.locator('.watch-local-body').boundingBox();
  const heading = await page.locator('.watch-local-title').boundingBox();
  const actions = await page.locator('.watch-local-actions').boundingBox();
  const tools = await page.locator('.watch-local-tools').boundingBox();
  expect(heading!.y).toBeGreaterThanOrEqual(frame!.y + frame!.height);
  expect(actions!.y).toBeGreaterThanOrEqual(heading!.y + heading!.height);
  expect(tools!.y).toBeGreaterThanOrEqual(body!.y + body!.height);
  if (OUT) {
    mkdirSync(OUT, { recursive: true });
    await page.screenshot({ fullPage: true, path: join(OUT, 'episode-theater-1440-dark.png') });
  }
});

if (OUT) {
  for (const scheme of ['light', 'dark'] as const) {
    for (const width of [390, 1440]) {
      for (const next of ['populated', 'empty', 'failure'] as const) {
        test(`capture episode ${next} ${scheme} ${width}`, async ({ page }) => {
          await page.emulateMedia({ colorScheme: scheme });
          await openEpisode(page, width, next);
          mkdirSync(OUT, { recursive: true });
          await page.screenshot({ fullPage: true, path: join(OUT, `episode-${next}-${width}-${scheme}.png`) });
        });
      }
    }
  }
}
