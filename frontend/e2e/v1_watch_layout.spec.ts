import { expect, test, type Page } from '@playwright/test';
import { item, mockApi, signIn } from './lumina-mock';

/** Watch composition for a web video: 16:9 player with Up next beside it (under it in theater), the editorial column under the player, tools in the column. */

async function openWatch(page: Page, viewport: { width: number; height: number }) {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
  await page.setViewportSize(viewport);
  await mockApi(page, { sidebar_collapsed: false });
  await page.goto(`/watch/library/${item.id}`);
  await signIn(page);
  await expect(page.locator('video').first()).toBeVisible();
  await page.waitForTimeout(300);
}

const box = async (page: Page, selector: string) => {
  const rect = await page.locator(selector).first().boundingBox();
  if (!rect) throw new Error(`${selector} is not rendered`);
  return rect;
};

test('test_watch_geometry', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  // Standard: Up next shares the row with the player; the column sits under the player.
  let player = await box(page, '.player-frame');
  expect(Math.abs(player.height - player.width * 9 / 16)).toBeLessThanOrEqual(2);
  let side = await box(page, '.g-watch-side');
  expect(side.x).toBeGreaterThanOrEqual(player.x + player.width);
  expect(side.y).toBeLessThan(player.y + player.height);
  expect((await box(page, '.g-watch-column h1')).y).toBeGreaterThanOrEqual(player.y + player.height);
  expect((await box(page, '.watch-tools')).y).toBeGreaterThan(player.y + player.height);
  await page.screenshot({ path: 'test-results/visual/watch-web-standard.png' });

  // Theater: the player takes the width; the column and side column sit under it.
  await page.locator('body').press('t');
  await expect(page.locator('.watch-layout.theater')).toBeVisible();
  await page.waitForTimeout(300);
  player = await box(page, '.player-frame');
  const column = await box(page, '.g-watch-column');
  side = await box(page, '.g-watch-side');
  expect(column.y).toBeGreaterThanOrEqual(player.y + player.height);
  expect(side.y).toBeGreaterThanOrEqual(player.y + player.height);
  expect(side.x).toBeGreaterThan(column.x + column.width);
  await page.screenshot({ path: 'test-results/visual/watch-web-theater.png' });
});

test('test_watch_small_layout', async ({ page }) => {
  await openWatch(page, { width: 390, height: 844 });
  const player = await box(page, '.player-frame');
  expect(player.x).toBe(0);
  expect(player.width).toBe(390);
  const column = await box(page, '.g-watch-column');
  const side = await box(page, '.g-watch-side');
  expect(side.y).toBeGreaterThanOrEqual(column.y + column.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await expect(page.getByRole('tab', { name: 'Notes', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Share' })).toBeVisible();
});

test('test_watch_preserves_current_play', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  const video = page.locator('video').first();
  await video.evaluate((media: HTMLVideoElement) => { media.dataset.identity = 'original'; });
  // Let the synthetic clip play briefly, then hold its position.
  await expect.poll(() => video.evaluate((media: HTMLVideoElement) => { if (media.paused) void media.play().catch(() => undefined); return media.currentTime; })).toBeGreaterThan(0.3);
  const position = await video.evaluate((media: HTMLVideoElement) => { media.pause(); return media.currentTime; });

  const notes = page.getByRole('tab', { name: 'Notes', exact: true });
  await notes.click();
  await expect(page.getByRole('region', { name: 'Notes', exact: true })).toBeVisible();
  await notes.press('ArrowLeft');
  // Moments sits right before Notes; Home jumps back to the first tool.
  await expect(page.getByRole('tab', { name: 'Moments' })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(page.getByRole('tab', { name: 'Summary' })).toBeFocused();
  await expect(page.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true');

  expect(await video.evaluate((media: HTMLVideoElement) => [media.dataset.identity, media.currentTime])).toEqual(['original', position]);
});

test('test_watch_tools_tablist_not_gallery_tabs_spacing', async ({ page }) => {
  await openWatch(page, { width: 1536, height: 960 });
  const tablist = page.getByRole('tablist', { name: 'Video tools' });
  await expect(tablist).toBeVisible();
  // `.gallery .g-tabs` (24px margin-bottom, 45px min-height) must not leak into the watch tools tablist.
  // The 24px gap is the web-video column's own rule (watch.css), unchanged by this task.
  const style = await tablist.evaluate((el) => {
    const s = getComputedStyle(el);
    return { gap: s.columnGap, margin: s.marginBottom, minHeight: s.minHeight };
  });
  expect(style).toEqual({ gap: '24px', margin: '0px', minHeight: '0px' });
});
