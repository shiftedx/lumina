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
  test(`matched episode owns the heading and filename stays in file details at ${width}px`, async ({ page }) => {
    await openEpisode(page, width);
    await expect(page.getByRole('button', { name: 'Harbor Lights', exact: true })).toBeVisible();
    await expect(page.getByText(/S2 · E1/, { exact: false }).first()).toBeVisible();
    const heading = await page.getByRole('heading', { level: 1 }).boundingBox();
    const frame = await page.locator('.player-frame').boundingBox();
    expect(heading!.y + heading!.height).toBeLessThanOrEqual(frame!.y);
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
    expect(await page.locator('.description-card p').first().evaluate(el => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(14);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test('an empty season reclaims the rail and explains where to find all episodes', async ({ page }) => {
  await openEpisode(page, 1440, 'empty');
  await expect(page.getByText('No next episode in your library.', { exact: false })).toBeVisible();
  const allEpisodes = page.getByRole('button', { name: 'See all episodes' });
  await expect(allEpisodes).toBeVisible();
  await page.getByRole('button', { name: 'Share', exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(allEpisodes).toBeFocused();
  await expect(page.locator('.watch-rail')).toBeHidden();
  const layout = await page.locator('.watch-layout').boundingBox();
  const player = await page.locator('.player-frame').boundingBox();
  expect(player!.width).toBeGreaterThan(layout!.width * .98);
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
  await page.keyboard.press('ArrowDown');
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
  await expect(page.locator('.watch-layout.theater')).toBeVisible();
  const frame = await page.locator('.player-frame').boundingBox();
  const heading = await page.locator('.watch-title').boundingBox();
  const actions = await page.locator('.watch-actions').boundingBox();
  const tools = await page.locator('.watch-tools').boundingBox();
  expect(heading!.y).toBeGreaterThanOrEqual(frame!.y + frame!.height);
  expect(actions!.y).toBeGreaterThanOrEqual(heading!.y + heading!.height);
  expect(tools!.y).toBeGreaterThanOrEqual(actions!.y + actions!.height);
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
